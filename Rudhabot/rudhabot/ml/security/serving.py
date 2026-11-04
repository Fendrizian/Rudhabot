"""Loading and serving the security models, with every safety valve the reference asks for.

- Versioned artifacts in `models/security/` with a manifest; each model is refused if its feature-schema hash doesn't
  match the running code, and then the rules work alone.
- Each model has its own switch (`/security settings models.message off`, ...) and the environment can turn them all
  off (`RUDHABOT_SECURITY_MODELS=0`).
- Inference runs off the event loop (`asyncio.to_thread`) with a timeout; a timeout, error or missing model returns
  None and is counted, never raised.
- Below the confidence bar a reading is "uncertain" and produces no signal at all.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from collections import Counter
from typing import Optional

from rudhabot.ml import single_threaded_blas

single_threaded_blas()

import numpy as np                                    # noqa: E402

log = logging.getLogger("rudhabot.security.models")

DEFAULT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))),
                           "models", "security")
SIGNAL_AT = 0.8              # a reading below this is "uncertain": no signal
UNSURE_FROM = 0.4
TIMEOUT_S = 2.0
TO_INCIDENT = {"join_raid": "raid", "slow_drip": "raid", "spam_raid": "spam", "mention_bomb": "spam",
               "phishing": "phishing", "staff_nuke": "nuke", "malicious_bot": "integration",
               "webhook_spam": "integration"}


class SecurityModels:
    def __init__(self, directory: str = DEFAULT_DIR, enabled: bool = True):
        self.directory = directory
        self.enabled = enabled
        self.message = None
        self.incident = None
        self.ranker = None
        self.manifest: dict = {}
        self.errors: dict[str, str] = {}
        self.stats = Counter()
        self.latency_ms: dict[str, float] = {}
        self.loaded = False

    def load(self) -> "SecurityModels":
        """Read the artifacts (a few hundred ms for the 8.4M-parameter table): call it in a worker thread once the bot
        is up, never on the event loop. Until then (and if it fails) the rules work alone."""
        if self.enabled and not self.loaded:
            self._load()
        self.loaded = True
        return self

    # ------------------------------------------------------------------ loading
    def _load(self) -> None:
        path = os.path.join(self.directory, "manifest.json")
        if not os.path.exists(path):
            self.errors["all"] = "no models/security/manifest.json (rules only)"
            return
        try:
            with open(path, encoding="utf-8") as handle:
                self.manifest = json.load(handle)
        except (OSError, json.JSONDecodeError) as error:
            self.errors["all"] = f"manifest unreadable: {error}"
            return
        from rudhabot.ml.security.hashing import SCHEMA as HASH_SCHEMA
        from rudhabot.security.features import SCHEMA as FEATURE_SCHEMA
        loaders = {
            "message": (HASH_SCHEMA, self._load_message),
            "incident": (FEATURE_SCHEMA, self._load_incident),
            "ranker": (None, self._load_ranker),
        }
        for name, (schema, loader) in loaders.items():
            entry = self.manifest.get(name)
            if not entry:
                self.errors[name] = "not in the manifest"
                continue
            if schema is not None and entry.get("schema") != schema:
                self.errors[name] = f"trained for {entry.get('schema')}, running {schema}: refused"
                continue
            try:
                loader(os.path.join(self.directory, entry["file"]))
            except Exception as error:                 # a corrupt artifact must never take the bot down
                self.errors[name] = f"failed to load: {type(error).__name__}"
                log.warning("security model %s not loaded: %s", name, error)

    def _load_message(self, path):
        from rudhabot.ml.security.sparse import HashedMultiTaskNet
        self.message = HashedMultiTaskNet.load(path)

    def _load_incident(self, path):
        from rudhabot.ml.security.incident_model import IncidentNet
        self.incident = IncidentNet.load(path)

    def _load_ranker(self, path):
        from rudhabot.ml.security.incident_model import AlertRanker
        with open(path, encoding="utf-8") as handle:
            self.ranker = AlertRanker.from_dict(json.load(handle))

    @property
    def message_ready(self) -> bool:
        return self.message is not None

    @property
    def incident_ready(self) -> bool:
        return self.incident is not None

    def versions(self) -> dict:
        return {f"model_{k}": v.get("version") for k, v in self.manifest.items() if isinstance(v, dict) and v.get("version")}

    # ------------------------------------------------------------------ inference (sync, for threads and the simulator)
    def message_scores(self, text: str) -> dict:
        from rudhabot.ml.security.hashing import encode_messages, encode_urls
        from rudhabot.security.links import extract_urls
        idx, off = encode_messages([text])
        p = self.message.predict(idx, off)[0]
        url_p = 0.0
        urls = extract_urls(text)[:4]
        if urls:
            ui, uo = encode_urls(urls)
            url_p = float(self.message.predict(ui, uo)[:, 2].max())
        scam, spam = float(p[0]), float(p[1])
        top = max(scam, spam)
        return {"scam": scam, "spam": spam, "url": url_p, "uncertain": UNSURE_FROM <= top < SIGNAL_AT,
                "version": self.manifest.get("message", {}).get("version", "")}

    def incident_scores(self, vector) -> dict:
        p, fam = self.incident.predict(np.asarray(vector, np.float32)[None, :])
        families = self.incident.meta.get("families", list(TO_INCIDENT))
        k = int(np.argmax(fam[0]))
        family = TO_INCIDENT.get(families[k], "raid")
        return {"p": float(p[0]), "family": family, "family_p": float(fam[0][k]),
                "confident": float(p[0]) >= SIGNAL_AT and float(fam[0][k]) >= 0.4}

    # ------------------------------------------------------------------ async wrappers with timeout and fallback
    async def _off_loop(self, name: str, fn, *args) -> Optional[dict]:
        start = time.perf_counter()
        try:
            result = await asyncio.wait_for(asyncio.to_thread(fn, *args), TIMEOUT_S)
            self.stats[f"{name}_ok"] += 1
            self.latency_ms[name] = round((time.perf_counter() - start) * 1000, 2)
            return result
        except asyncio.TimeoutError:
            self.stats[f"{name}_timeout"] += 1
        except Exception as error:
            self.stats[f"{name}_error"] += 1
            log.warning("security model %s failed: %s", name, type(error).__name__)
        return None

    async def score_message(self, text: str) -> Optional[dict]:
        if self.message is None or not text:
            return None
        return await self._off_loop("message", self.message_scores, text[:2000])

    async def score_incident(self, vector) -> Optional[dict]:
        if self.incident is None:
            return None
        return await self._off_loop("incident", self.incident_scores, vector)

    def rank(self, rank_vector) -> Optional[tuple[float, list]]:
        if self.ranker is None:
            return None
        try:
            return self.ranker.score(rank_vector), self.ranker.why(rank_vector)
        except Exception:
            return None

    def scorers(self) -> tuple:
        """(message_model, incident_model) callables in the shape the wargame and drills use, or None for a model
        that isn't loaded. Uncertain message readings count as nothing, exactly as in the bot."""
        def message(text):
            s = self.message_scores(text)
            return (0.0, 0.0) if s["uncertain"] else (s["scam"], s["spam"])

        def incident(vec):
            s = self.incident_scores(vec)
            return s["p"], s["family"], s["confident"]
        return (message if self.message_ready else None), (incident if self.incident_ready else None)

    def status(self, config=None) -> dict:
        def state(name, ready, key):
            if config is not None and not config[key]:
                return "off (setting)"
            if not self.enabled:
                return "off (RUDHABOT_SECURITY_MODELS=0)"
            if not self.loaded:
                return "loading"
            return "ready" if ready else f"unavailable: {self.errors.get(name) or self.errors.get('all', '?')}"
        return {"available": True,
                "message": state("message", self.message_ready, "models.message"),
                "incident": state("incident", self.incident_ready, "models.incident"),
                "ranker": state("ranker", self.ranker is not None, "models.ranker"),
                "versions": self.versions(), "stats": dict(self.stats), "latency_ms": dict(self.latency_ms),
                "params": {"message": self.message.params if self.message else 0,
                           "incident": self.incident.params if self.incident else 0}}
