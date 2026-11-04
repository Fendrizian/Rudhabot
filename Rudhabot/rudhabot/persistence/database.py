"""Local SQLite storage: one file (`rudhabot.db` in the data folder), Python's built-in `sqlite3`, no server.

Why: with JSON files every write rewrote a whole file (all accounts, all TCG profiles). Here a write touches only the
rows that changed, inside a transaction, and many small stores share one durable file. The JSON backend is kept
(`RUDHABOT_STORAGE=json`) and the repositories work with either.

Durability: WAL journal with `synchronous=FULL`, so a committed write survives a crash or power cut. Every write is
one transaction. Daily online backups (`backup_to`) copy a consistent snapshot without stopping the bot.

Three shapes cover every store:
- `Collection`: keyed JSON documents (accounts, bank profiles, TCG profiles, live matches). Used through
  `SqliteOwned`, which has the same `value()/persist()/replace()` interface as `OwnedJson`, but persists only the
  keys that changed.
- `ValueStore`: one whole JSON value with `load()/save()`, a drop-in for `JsonStore` (prices, lottery, birthdays...).
- `SqliteLedger`: an append-only table with the `Ledger` interface (`append/read/tail`), indexed by user.

The security tables (cases, incidents, snapshots, lockdowns, ...) are created by `rudhabot.persistence.security`.

Migration: the first time a store is opened on a new database, an existing JSON file at its old path is imported
(the original file is left untouched, and the import is recorded so it never runs twice).
`python -m rudhabot.persistence.database export <dir>` writes every store back out as JSON (for a rollback).
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import time
from contextlib import contextmanager
from typing import Any, Callable, Iterable, Iterator, Optional

from rudhabot.persistence.json_store import json_copy

log = logging.getLogger("rudhabot.db")

SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS docs (
    collection TEXT NOT NULL, key TEXT NOT NULL, value TEXT NOT NULL, updated REAL NOT NULL,
    PRIMARY KEY (collection, key)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS kv (name TEXT PRIMARY KEY, value TEXT NOT NULL, updated REAL NOT NULL);
CREATE TABLE IF NOT EXISTS ledger (
    id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, user TEXT, ts TEXT, entry TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS ledger_by_user ON ledger (name, user, id);
"""


def _dumps(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False)


class Database:
    """One connection, shared by the event loop and (rarely) worker threads, serialised by a re-entrant lock."""

    def __init__(self, path: str):
        self.path = path
        directory = os.path.dirname(os.path.abspath(path))
        os.makedirs(directory, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None, timeout=10.0)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=FULL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.execute("PRAGMA busy_timeout=10000")
        self._closed = False
        self._conn.executescript(_SCHEMA)
        stored = self.meta("schema_version")
        if stored is None:
            self.set_meta("schema_version", str(SCHEMA_VERSION))
        elif int(stored) > SCHEMA_VERSION:
            raise RuntimeError(f"{path} was written by a newer Rudhabot (schema {stored} > {SCHEMA_VERSION})")

    # ------------------------------------------------------------------ plumbing
    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Cursor]:
        """BEGIN IMMEDIATE ... COMMIT, rolled back if the block raises. Re-entrant: a nested call joins the outer one."""
        with self._lock:
            if self._conn.in_transaction:
                yield self._conn.cursor()
                return
            cur = self._conn.cursor()
            cur.execute("BEGIN IMMEDIATE")
            try:
                yield cur
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise
            else:
                self._conn.execute("COMMIT")

    def query(self, sql: str, params: Iterable = ()) -> list[tuple]:
        with self._lock:
            return self._conn.execute(sql, tuple(params)).fetchall()

    def execute(self, sql: str, params: Iterable = ()) -> int:
        """Run one statement in its own transaction; returns lastrowid."""
        with self.transaction() as cur:
            cur.execute(sql, tuple(params))
            return cur.lastrowid

    def data_version(self) -> int:
        """Changes when *another* connection (another process, a hand edit) commits; our own writes don't move it."""
        with self._lock:
            return self._conn.execute("PRAGMA data_version").fetchone()[0]

    def meta(self, key: str) -> Optional[str]:
        row = self.query("SELECT value FROM meta WHERE key=?", (key,))
        return row[0][0] if row else None

    def set_meta(self, key: str, value: str) -> None:
        self.execute("INSERT INTO meta(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                     (key, value))

    def integrity_ok(self) -> bool:
        return self.query("PRAGMA quick_check")[0][0] == "ok"

    def backup_to(self, destination: str) -> None:
        """A consistent online copy (SQLite's backup API, from a fresh connection so the bot keeps running)."""
        os.makedirs(os.path.dirname(os.path.abspath(destination)), exist_ok=True)
        tmp = destination + ".tmp"
        if os.path.exists(tmp):
            os.remove(tmp)
        src = sqlite3.connect(self.path, timeout=30.0)
        try:
            dst = sqlite3.connect(tmp)
            try:
                src.backup(dst)
            finally:
                dst.close()
        finally:
            src.close()
        os.replace(tmp, destination)

    def close(self) -> None:
        with self._lock:
            if not self._closed:
                self._conn.close()
                self._closed = True

    # ------------------------------------------------------------------ stores
    def collection(self, name: str) -> "Collection":
        return Collection(self, name)

    def value(self, name: str, default_factory: Callable[[], Any]) -> "ValueStore":
        return ValueStore(self, name, default_factory)

    def ledger(self, name: str) -> "SqliteLedger":
        return SqliteLedger(self, name)

    # ------------------------------------------------------------------ import
    def imported(self, name: str) -> bool:
        return self.meta(f"imported:{name}") is not None

    def mark_imported(self, name: str, source: str) -> None:
        self.set_meta(f"imported:{name}", _dumps({"source": source, "at": time.time()}))


# ====================================================================== documents
class Collection:
    """Keyed JSON documents in the `docs` table."""

    def __init__(self, db: Database, name: str):
        self.db, self.name = db, name
        self.path = f"{db.path}#{name}"          # for messages; there is no separate file

    def load_all(self) -> dict[str, Any]:
        rows = self.db.query("SELECT key, value FROM docs WHERE collection=?", (self.name,))
        return {key: json.loads(value) for key, value in rows}

    def load_raw(self) -> dict[str, str]:
        return dict(self.db.query("SELECT key, value FROM docs WHERE collection=?", (self.name,)))

    def get(self, key: str) -> Any:
        row = self.db.query("SELECT value FROM docs WHERE collection=? AND key=?", (self.name, str(key)))
        return json.loads(row[0][0]) if row else None

    def write(self, upserts: dict[str, str], deletes: Iterable[str] = ()) -> None:
        """Apply serialised upserts and deletes in one transaction."""
        now = time.time()
        with self.db.transaction() as cur:
            if upserts:
                cur.executemany(
                    "INSERT INTO docs(collection, key, value, updated) VALUES(?, ?, ?, ?) "
                    "ON CONFLICT(collection, key) DO UPDATE SET value=excluded.value, updated=excluded.updated",
                    [(self.name, key, text, now) for key, text in upserts.items()])
            deletes = list(deletes)
            if deletes:
                cur.executemany("DELETE FROM docs WHERE collection=? AND key=?", [(self.name, k) for k in deletes])

    def count(self) -> int:
        return self.db.query("SELECT COUNT(*) FROM docs WHERE collection=?", (self.name,))[0][0]

    def import_mapping(self, mapping: dict, source: str) -> int:
        self.write({str(k): _dumps(v) for k, v in mapping.items()})
        self.db.mark_imported(self.name, source)
        return len(mapping)


class SqliteOwned:
    """The SQLite counterpart of `OwnedJson`: the collection's documents kept in memory as one dict.

    Same ownership rule: `value()` is the live dict; mutate it only when you will `persist()`. `persist(*keys)` writes
    just those keys (a key no longer in the dict is deleted); `persist()` with no keys compares every document with
    what was last written and writes only the differences. A commit by another process is noticed (`data_version`)
    and the collection is reloaded before the next read.
    """

    def __init__(self, collection: Collection):
        self.store = collection
        self._value: Optional[dict] = None
        self._written: dict[str, str] = {}
        self._version: Optional[int] = None

    def value(self) -> dict:
        version = self.store.db.data_version()
        if self._value is None or version != self._version:
            raw = self.store.load_raw()
            self._written = raw
            self._value = {key: json.loads(text) for key, text in raw.items()}
            self._version = version
        return self._value

    def persist(self, *keys: str) -> None:
        value = self.value() if self._value is None else self._value
        candidates = [str(k) for k in keys] if keys else list(set(value) | set(self._written))
        upserts, deletes = {}, []
        for key in candidates:
            if key in value:
                text = _dumps(value[key])
                if self._written.get(key) != text:
                    upserts[key] = text
            elif key in self._written:
                deletes.append(key)
        if not upserts and not deletes:
            return
        self.store.write(upserts, deletes)
        self._written.update(upserts)
        for key in deletes:
            self._written.pop(key, None)

    def replace(self, value: dict) -> None:
        self.value()                              # makes sure `_written` reflects the table, so removals are deleted
        self._value = value
        self.persist()


def owned(store) -> Any:
    """The right in-memory owner for a repository's backing store (a JsonStore or a SQLite Collection)."""
    if isinstance(store, Collection):
        return SqliteOwned(store)
    from rudhabot.persistence.json_store import OwnedJson
    return OwnedJson(store)


# ====================================================================== whole values
class ValueStore:
    """A `JsonStore` drop-in: one JSON value stored as a row of the `kv` table."""

    def __init__(self, db: Database, name: str, default_factory: Callable[[], Any]):
        self.db, self.name, self._default_factory = db, name, default_factory
        self.path = f"{db.path}#{name}"

    def load(self) -> Any:
        row = self.db.query("SELECT value FROM kv WHERE name=?", (self.name,))
        if not row:
            value = self._default_factory()
            self.save(value)
            return json_copy(value)
        try:
            return json.loads(row[0][0])
        except json.JSONDecodeError:              # only possible after a hand edit; keep the bad text aside
            self.db.execute("INSERT OR REPLACE INTO kv(name, value, updated) VALUES(?, ?, ?)",
                            (f"{self.name}.corrupt-{int(time.time())}", row[0][0], time.time()))
            value = self._default_factory()
            self.save(value)
            return value

    def save(self, value: Any) -> None:
        self.db.execute("INSERT INTO kv(name, value, updated) VALUES(?, ?, ?) "
                        "ON CONFLICT(name) DO UPDATE SET value=excluded.value, updated=excluded.updated",
                        (self.name, _dumps(value), time.time()))

    def exists(self) -> bool:
        return bool(self.db.query("SELECT 1 FROM kv WHERE name=?", (self.name,)))

    def import_value(self, value: Any, source: str) -> None:
        self.save(value)
        self.db.mark_imported(self.name, source)


# ====================================================================== ledger
class SqliteLedger:
    """The `Ledger` interface over the `ledger` table: append-only, never rewritten, indexed by user."""

    def __init__(self, db: Database, name: str):
        self.db, self.name = db, name
        self.path = f"{db.path}#{name}"

    def append(self, entry: dict) -> None:
        user = entry.get("user")
        self.db.execute("INSERT INTO ledger(name, user, ts, entry) VALUES(?, ?, ?, ?)",
                        (self.name, None if user is None else str(user), entry.get("ts"), _dumps(entry)))

    def append_many(self, entries: Iterable[dict]) -> int:
        rows = [(self.name, None if e.get("user") is None else str(e.get("user")), e.get("ts"), _dumps(e))
                for e in entries]
        with self.db.transaction() as cur:
            cur.executemany("INSERT INTO ledger(name, user, ts, entry) VALUES(?, ?, ?, ?)", rows)
        return len(rows)

    def read(self) -> Iterator[dict]:
        last = 0
        while True:                               # paged, so a long history never sits in memory at once
            rows = self.db.query("SELECT id, entry FROM ledger WHERE name=? AND id>? ORDER BY id LIMIT 2000",
                                 (self.name, last))
            if not rows:
                return
            for row_id, text in rows:
                last = row_id
                try:
                    yield json.loads(text)
                except json.JSONDecodeError:
                    continue

    def tail(self, n: int = 20, user_id: Optional[str] = None) -> list[dict]:
        if n <= 0:
            return []
        if user_id is None:
            rows = self.db.query("SELECT entry FROM ledger WHERE name=? ORDER BY id DESC LIMIT ?", (self.name, n))
        else:
            rows = self.db.query("SELECT entry FROM ledger WHERE name=? AND user=? ORDER BY id DESC LIMIT ?",
                                 (self.name, str(user_id), n))
        return [json.loads(text) for (text,) in reversed(rows)]

    def count(self) -> int:
        return self.db.query("SELECT COUNT(*) FROM ledger WHERE name=?", (self.name,))[0][0]


# ====================================================================== storage factory
class Storage:
    """Hands every repository its backing store, for either backend.

    `docs(name, json_path)` -> a JsonStore (json) or a Collection (sqlite)
    `value(name, json_path, default, indent)` -> a JsonStore or a ValueStore
    `ledger(name, jsonl_path)` -> a Ledger or a SqliteLedger
    With SQLite, an existing JSON file at the old path is imported the first time (and left in place).
    """

    def __init__(self, backend: str, db: Optional[Database] = None):
        if backend not in ("sqlite", "json"):
            raise ValueError(f"RUDHABOT_STORAGE must be 'sqlite' or 'json', got {backend!r}")
        self.backend, self.db = backend, db
        self.imports: list[str] = []

    @classmethod
    def open(cls, backend: str, database_path: Optional[str]) -> "Storage":
        if backend == "json":
            return cls("json")
        if backend != "sqlite":
            return cls(backend)                   # raises the clear ValueError
        return cls("sqlite", Database(database_path))

    def docs(self, name: str, json_path: str):
        from rudhabot.persistence.json_store import JsonStore
        if self.db is None:
            return JsonStore(json_path, dict)
        collection = self.db.collection(name)
        if not self.db.imported(name):
            mapping = _read_json(json_path)
            if isinstance(mapping, dict) and mapping:
                count = collection.import_mapping(mapping, json_path)
                self.imports.append(f"{name}: {count} records from {os.path.basename(json_path)}")
            else:
                self.db.mark_imported(name, "(nothing to import)")
        return collection

    def value(self, name: str, json_path: str, default_factory: Callable[[], Any], *, indent: int | None = 4):
        from rudhabot.persistence.json_store import JsonStore
        if self.db is None:
            return JsonStore(json_path, default_factory, indent=indent)
        store = self.db.value(name, default_factory)
        if not self.db.imported(name):
            value = _read_json(json_path)
            if value is not None and not store.exists():
                store.import_value(value, json_path)
                self.imports.append(f"{name} from {os.path.basename(json_path)}")
            else:
                self.db.mark_imported(name, "(nothing to import)")
        return store

    def ledger(self, name: str, jsonl_path: str):
        from rudhabot.persistence.ledger import Ledger
        if self.db is None:
            return Ledger(jsonl_path)
        ledger = self.db.ledger(name)
        if not self.db.imported(name):
            count = 0
            for path in _ledger_files(jsonl_path):
                count += ledger.append_many(Ledger(path).read())
            self.db.mark_imported(name, jsonl_path if count else "(nothing to import)")
            if count:
                self.imports.append(f"{name}: {count} entries from {os.path.basename(jsonl_path)}")
        return ledger

    def backup(self, directory: str, keep: int = 14, stamp: Optional[str] = None) -> Optional[str]:
        """Write `rudhabot-<stamp>.db` into `directory` and keep the newest `keep` backups. None for JSON storage."""
        if self.db is None:
            return None
        stamp = stamp or time.strftime("%Y%m%d")
        target = os.path.join(directory, f"rudhabot-{stamp}.db")
        self.db.backup_to(target)
        backups = sorted(f for f in os.listdir(directory) if f.startswith("rudhabot-") and f.endswith(".db"))
        for old in backups[:-keep] if keep > 0 else []:
            try:
                os.remove(os.path.join(directory, old))
            except OSError:
                pass
        return target

    def close(self) -> None:
        if self.db is not None:
            self.db.close()


def _read_json(path: str) -> Any:
    if not path or not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as handle:
            content = handle.read().strip()
        return json.loads(content) if content else None
    except (OSError, json.JSONDecodeError):
        log.warning("not importing %s: unreadable or not JSON", path)
        return None


def _ledger_files(path: str) -> list[str]:
    """The rotated `ledger-<ts>.jsonl` files (oldest first), then the live file."""
    directory = os.path.dirname(path) or "."
    stem = os.path.splitext(os.path.basename(path))[0]
    rotated = []
    if os.path.isdir(directory):
        for name in os.listdir(directory):
            rest = name[len(stem) + 1:-len(".jsonl")] if name.startswith(stem + "-") and name.endswith(".jsonl") else ""
            if rest.isdigit():
                rotated.append((int(rest), os.path.join(directory, name)))
    files = [p for _, p in sorted(rotated)]
    if os.path.exists(path):
        files.append(path)
    return files


# ====================================================================== export (rollback to JSON)
def export_all(db: Database, directory: str) -> list[str]:
    """Write every collection and value back out as JSON files named after the store (for RUDHABOT_STORAGE=json)."""
    os.makedirs(directory, exist_ok=True)
    written = []
    for (name,) in db.query("SELECT DISTINCT collection FROM docs"):
        path = os.path.join(directory, f"{name}.json")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(db.collection(name).load_all(), handle, indent=2, ensure_ascii=False)
        written.append(path)
    for name, text in db.query("SELECT name, value FROM kv"):
        path = os.path.join(directory, f"{name}.json")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(json.loads(text), indent=2, ensure_ascii=False))
        written.append(path)
    for (name,) in db.query("SELECT DISTINCT name FROM ledger"):
        path = os.path.join(directory, f"{name}.jsonl")
        with open(path, "w", encoding="utf-8") as handle:
            for entry in db.ledger(name).read():
                handle.write(_dumps(entry) + "\n")
        written.append(path)
    return written


def main(argv: Optional[list[str]] = None) -> int:
    import argparse
    parser = argparse.ArgumentParser(prog="python -m rudhabot.persistence.database",
                                     description="Inspect, back up or export the bot's SQLite database.")
    parser.add_argument("command", choices=["info", "backup", "export", "check"])
    parser.add_argument("target", nargs="?", help="backup/export: the output folder")
    parser.add_argument("--db", default=None, help="the database file (default: <RUDHABOT_DATA_DIR>/rudhabot.db)")
    args = parser.parse_args(argv)
    if args.db is None:
        from rudhabot.settings import Paths
        args.db = Paths.from_env().database
    if not os.path.exists(args.db):
        print(f"no database at {args.db}")
        return 1
    db = Database(args.db)
    try:
        if args.command == "info":
            print(f"{args.db}: schema {db.meta('schema_version')}")
            for name, count in db.query("SELECT collection, COUNT(*) FROM docs GROUP BY collection ORDER BY 1"):
                print(f"  docs   {name:<24} {count:>8}")
            for (name,) in db.query("SELECT name FROM kv ORDER BY 1"):
                print(f"  value  {name}")
            for name, count in db.query("SELECT name, COUNT(*) FROM ledger GROUP BY name ORDER BY 1"):
                print(f"  ledger {name:<24} {count:>8}")
        elif args.command == "check":
            print("ok" if db.integrity_ok() else "INTEGRITY CHECK FAILED")
        elif args.command == "backup":
            target = os.path.join(args.target or ".", f"rudhabot-{time.strftime('%Y%m%d-%H%M%S')}.db")
            db.backup_to(target)
            print(target)
        elif args.command == "export":
            for path in export_all(db, args.target or "export"):
                print(path)
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
