"""SQLite storage: the same repository behaviour on both backends, row-level writes, imports, backups, export."""
import json
import os
import sqlite3
import tempfile
import unittest
from datetime import datetime

from rudhabot.clock import FixedClock
from rudhabot.persistence.database import Database, SqliteOwned, Storage, export_all
from rudhabot.persistence.json_store import JsonStore
from rudhabot.persistence.ledger import Ledger
from rudhabot.persistence.tcg import TcgRepository
from rudhabot.persistence.users import UserRepository
from rudhabot.services.audit import AuditService
from rudhabot.services.bank import BankService


class _Tmp(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = self._tmp.name

    def p(self, name):
        return os.path.join(self.dir, name)

    def db(self, name="t.db"):
        db = Database(self.p(name))
        self.addCleanup(db.close)
        return db


class BackendParityTests(_Tmp):
    """Every repository behaves identically over a JSON file and a SQLite collection."""

    def stores(self):
        db = self.db()
        return {"json": JsonStore(self.p("data.json"), dict), "sqlite": db.collection("accounts")}

    def test_accounts_round_trip_and_copies(self):
        for backend, store in self.stores().items():
            with self.subTest(backend=backend):
                users = UserRepository(store, ["SCAY"])
                self.assertEqual(users.get(5)["wallet"], 0)
                self.assertEqual(users.all(), {})                           # a lookup creates nothing
                users.update(5, wallet=10, inventory={"cap": 1})
                got = users.get(5)
                got["inventory"]["cap"] = 99                                  # a returned copy
                self.assertEqual(users.get(5)["inventory"], {"cap": 1})
                with users.transaction() as data:
                    data["5"]["wallet"] = 77
                    data["6"] = {"wallet": 3, "bank": 0}
                fresh = UserRepository(store, ["SCAY"])                      # a new process sees the same data
                self.assertEqual(fresh.get(5)["wallet"], 77)
                self.assertEqual(fresh.get(6)["wallet"], 3)
                self.assertTrue(fresh.consume_item(5, "cap"))
                self.assertFalse(fresh.consume_item(5, "cap"))

    def test_save_all_removes_missing_accounts(self):
        for backend, store in self.stores().items():
            with self.subTest(backend=backend):
                users = UserRepository(store, ["SCAY"])
                users.update(1, wallet=1)
                users.update(2, wallet=2)
                users.save_all({"2": users.all()["2"]})
                self.assertEqual(set(UserRepository(store, ["SCAY"]).all()), {"2"})

    def test_bank_and_tcg_profiles(self):
        db = self.db()
        clock = FixedClock(datetime(2026, 5, 1, 12))
        users = UserRepository(db.collection("accounts"), ["SCAY"])
        bank = BankService(db.collection("bank"), users, clock)
        users.update(1, wallet=1000)
        bank.transfer(1, 400, "deposit")
        self.assertEqual(users.get(1)["bank"], 400)
        self.assertIsNone(db.collection("bank").get("77"))
        bank.account(77)                                                     # looking up creates no row
        self.assertIsNone(db.collection("bank").get("77"))
        repo = TcgRepository(db.collection("tcg_profiles"), db.collection("tcg_matches"))
        profile = repo.profile(3)
        profile["wins"] = 4
        repo.save_profile(3, profile)
        repo.save_match("m1", {"seed": 1})
        again = TcgRepository(db.collection("tcg_profiles"), db.collection("tcg_matches"))
        self.assertEqual(again.profile(3)["wins"], 4)
        self.assertEqual(again.get_match("m1"), {"seed": 1})
        again.delete_match("m1")
        self.assertEqual(db.collection("tcg_matches").count(), 0)


class RowLevelWriteTests(_Tmp):
    def test_only_changed_documents_are_written(self):
        db = self.db()
        col = db.collection("c")
        owner = SqliteOwned(col)
        value = owner.value()
        for i in range(50):
            value[str(i)] = {"n": i}
        owner.persist()
        stamps = dict(db.query("SELECT key, updated FROM docs WHERE collection='c'"))
        value["7"]["n"] = 700
        owner.persist("7")
        after = dict(db.query("SELECT key, updated FROM docs WHERE collection='c'"))
        changed = [k for k in after if after[k] != stamps[k]]
        self.assertEqual(changed, ["7"])
        del value["8"]
        owner.persist()                                                      # a full diff deletes removed keys
        self.assertIsNone(col.get("8"))
        self.assertEqual(col.get("7"), {"n": 700})

    def test_a_commit_from_another_connection_is_noticed(self):
        db = self.db()
        owner = SqliteOwned(db.collection("c"))
        owner.value()["a"] = 1
        owner.persist("a")
        other = sqlite3.connect(db.path)
        other.execute("UPDATE docs SET value='2' WHERE collection='c' AND key='a'")
        other.commit()
        other.close()
        self.assertEqual(owner.value()["a"], 2)

    def test_a_failed_transaction_rolls_back(self):
        db = self.db()
        with self.assertRaises(RuntimeError):
            with db.transaction() as cur:
                cur.execute("INSERT INTO kv(name, value, updated) VALUES('x', '1', 0)")
                raise RuntimeError("boom")
        self.assertEqual(db.query("SELECT COUNT(*) FROM kv WHERE name='x'")[0][0], 0)


class ValueAndLedgerTests(_Tmp):
    def test_value_store_is_a_json_store_drop_in(self):
        db = self.db()
        store = db.value("prices", lambda: {"SCAY": 1.0})
        first = store.load()
        first["SCAY"] = 9.0                                                  # callers own what load() returns
        self.assertEqual(store.load(), {"SCAY": 1.0})
        store.save({"SCAY": 2.5})
        self.assertEqual(db.value("prices", dict).load(), {"SCAY": 2.5})

    def test_ledger_interface_and_audit_service(self):
        db = self.db()
        ledger = db.ledger("economy_ledger")
        clock = FixedClock(datetime(2026, 5, 1, 12))
        audit = AuditService(ledger, clock, channel_id=None)
        users = UserRepository(db.collection("accounts"), ["SCAY"], audit=audit)
        for i in range(30):
            users.update(i % 3, wallet=i)
        self.assertEqual(ledger.count(), 30 - 1)                            # user 0's first update is 0 -> 0
        self.assertEqual([e["after"] for e in ledger.tail(2, user_id="1")], [25, 28])
        self.assertEqual(len(list(ledger.read())), ledger.count())
        self.assertEqual(len(audit.recent(5, user_id=2)), 5)


class ImportExportBackupTests(_Tmp):
    def write(self, name, value):
        with open(self.p(name), "w", encoding="utf-8") as handle:
            json.dump(value, handle)

    def test_json_files_are_imported_once_and_left_in_place(self):
        self.write("data.json", {"1": {"wallet": 50, "bank": 5}})
        self.write("crypto.json", {"SCAY": 3.0})
        Ledger(self.p("ledger-100.jsonl")).append({"user": "1", "delta": 1})
        Ledger(self.p("ledger.jsonl")).append({"user": "1", "delta": 2})
        storage = Storage.open("sqlite", self.p("rudhabot.db"))
        self.addCleanup(storage.close)
        users = UserRepository(storage.docs("accounts", self.p("data.json")), ["SCAY"])
        prices = storage.value("crypto_prices", self.p("crypto.json"), dict)
        ledger = storage.ledger("economy_ledger", self.p("ledger.jsonl"))
        self.assertEqual(users.get(1)["wallet"], 50)
        self.assertEqual(prices.load(), {"SCAY": 3.0})
        self.assertEqual([e["delta"] for e in ledger.read()], [1, 2])      # rotated file first
        self.assertEqual(len(storage.imports), 3)
        self.assertTrue(os.path.exists(self.p("data.json")))                # originals untouched
        users.update(1, wallet=99)
        self.write("data.json", {"1": {"wallet": -1}})                      # later JSON edits are not re-imported
        again = Storage("sqlite", storage.db)
        self.assertEqual(UserRepository(again.docs("accounts", self.p("data.json")), ["SCAY"]).get(1)["wallet"], 99)
        self.assertEqual(again.imports, [])

    def test_backup_keeps_the_newest_and_export_round_trips(self):
        storage = Storage.open("sqlite", self.p("rudhabot.db"))
        self.addCleanup(storage.close)
        UserRepository(storage.docs("accounts", self.p("none.json")), ["SCAY"]).update(4, wallet=12)
        backups = self.p("backups")
        for day in ("20260101", "20260102", "20260103"):
            storage.backup(backups, keep=2, stamp=day)
        self.assertEqual(sorted(os.listdir(backups)), ["rudhabot-20260102.db", "rudhabot-20260103.db"])
        copy = Database(os.path.join(backups, "rudhabot-20260103.db"))
        self.addCleanup(copy.close)
        self.assertTrue(copy.integrity_ok())
        self.assertEqual(copy.collection("accounts").get("4")["wallet"], 12)
        written = export_all(storage.db, self.p("export"))
        with open(self.p("export/accounts.json"), encoding="utf-8") as handle:
            self.assertEqual(json.load(handle)["4"]["wallet"], 12)
        self.assertTrue(any(path.endswith("accounts.json") for path in written))

    def test_json_backend_is_still_available(self):
        storage = Storage.open("json", None)
        self.assertIsInstance(storage.docs("accounts", self.p("data.json")), JsonStore)
        self.assertIsInstance(storage.ledger("x", self.p("l.jsonl")), Ledger)
        with self.assertRaises(ValueError):
            Storage.open("postgres", None)


class MaintenanceCogTests(__import__("tests.cog_case", fromlist=["CogTestCase"]).CogTestCase):
    async def test_the_bot_runs_on_sqlite_and_backs_up_once_a_day(self):
        self.assertEqual(self.s.storage.backend, "sqlite")
        self.fund(self.alice, wallet=10)
        cog = self.cog("MaintenanceCog")
        await cog.backup_if_due()
        await cog.backup_if_due()
        backups = os.listdir(self.s.settings.paths.backups)
        self.assertEqual(len(backups), 1)
        copy = Database(os.path.join(self.s.settings.paths.backups, backups[0]))
        self.addCleanup(copy.close)
        self.assertEqual(copy.collection("accounts").get(str(self.alice.id))["wallet"], 10)


if __name__ == "__main__":
    unittest.main()
