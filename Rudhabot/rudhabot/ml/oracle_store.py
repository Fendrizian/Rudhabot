"""Versioned model artifacts: `models/manifest.json` + `models/<version>.npz`.

The manifest records, per version, the file, the feature-schema hash it was trained against, its training config and the
measured evaluation. Loading refuses an artifact whose schema hash does not match the running code (the service then
falls back to the heuristic). Rollback = point `current` (or the ORACLE_VERSION env var) at an older version.
"""
from __future__ import annotations

import json
import os
from typing import Optional

from rudhabot.ml.features import schema_hash
from rudhabot.ml.nn import OracleNet


class ArtifactError(RuntimeError):
    pass


class ModelStore:
    def __init__(self, directory: str):
        self.directory = directory
        self.manifest_path = os.path.join(directory, "manifest.json")

    def manifest(self) -> dict:
        if not os.path.exists(self.manifest_path):
            return {"current": None, "versions": {}}
        with open(self.manifest_path, encoding="utf-8") as handle:
            return json.load(handle)

    def _write(self, manifest: dict) -> None:
        os.makedirs(self.directory, exist_ok=True)
        tmp = self.manifest_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(manifest, handle, indent=2)
        os.replace(tmp, self.manifest_path)

    def current_version(self, override: Optional[str] = None) -> Optional[str]:
        return override or os.environ.get("ORACLE_VERSION") or self.manifest().get("current")

    def current_path(self, override: Optional[str] = None) -> str:
        version = self.current_version(override)
        entry = self.manifest()["versions"].get(version or "")
        if entry is None:
            raise ArtifactError(f"no model version {version!r} in {self.manifest_path}")
        return os.path.join(self.directory, entry["file"])

    def load_current(self, override: Optional[str] = None) -> OracleNet:
        version = self.current_version(override)
        entry = self.manifest()["versions"].get(version or "")
        if entry is None:
            raise ArtifactError(f"no model version {version!r}")
        if entry.get("schema") != schema_hash():
            raise ArtifactError(f"{version} was trained for feature schema {entry.get('schema')}, running {schema_hash()}")
        net = OracleNet.load(os.path.join(self.directory, entry["file"]))
        if net.meta.get("schema") != schema_hash():
            raise ArtifactError(f"{version}: artifact schema mismatch")
        return net

    def register(self, version: str, net: OracleNet, info: dict, make_current: bool = True) -> str:
        filename = f"{version}.npz"
        net.meta = {**net.meta, "version": version, "schema": schema_hash()}
        os.makedirs(self.directory, exist_ok=True)
        net.save(os.path.join(self.directory, filename))
        manifest = self.manifest()
        manifest["versions"][version] = {"file": filename, "schema": schema_hash(), **info}
        if make_current:
            manifest["current"] = version
        self._write(manifest)
        return os.path.join(self.directory, filename)

    def set_current(self, version: str) -> None:
        manifest = self.manifest()
        if version not in manifest["versions"]:
            raise ArtifactError(f"unknown version {version!r}")
        manifest["current"] = version
        self._write(manifest)
