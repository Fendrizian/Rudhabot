"""Versioned model artifacts: `models/manifest.json` + `models/<version>.npz`.

The manifest records, per version, the file, the architecture and feature-schema hash it was trained against, its training config and the
measured evaluation. Loading refuses an artifact whose schema hash does not match the running code (the service then
falls back to the heuristic). Rollback = point `current` (or the CLAIRVOYANCE_VERSION env var) at an older version.
"""
from __future__ import annotations

import json
import os
from typing import Optional

from rudhabot.ml.features import encoder_for
from rudhabot.ml.nn import _Net, load_net


def _schema(arch: str) -> str:
    return encoder_for(arch)[2]()


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
        return (override or os.environ.get("CLAIRVOYANCE_VERSION") or os.environ.get("ORACLE_VERSION")   # legacy name
                or self.manifest().get("current"))

    def current_path(self, override: Optional[str] = None) -> str:
        version = self.current_version(override)
        entry = self.manifest()["versions"].get(version or "")
        if entry is None:
            raise ArtifactError(f"no model version {version!r} in {self.manifest_path}")
        return os.path.join(self.directory, entry["file"])

    def load_current(self, override: Optional[str] = None) -> _Net:
        version = self.current_version(override)
        entry = self.manifest()["versions"].get(version or "")
        if entry is None:
            raise ArtifactError(f"no model version {version!r}")
        arch = entry.get("arch", "v1")
        if arch not in ("v1", "v2"):
            raise ArtifactError(f"{version}: unknown architecture {arch!r}")
        expected = _schema(arch)
        if entry.get("schema") != expected:
            raise ArtifactError(f"{version} was trained for feature schema {entry.get('schema')}, running {expected}")
        net = load_net(os.path.join(self.directory, entry["file"]))
        if net.ARCH != arch or net.meta.get("schema") != expected:
            raise ArtifactError(f"{version}: artifact does not match its manifest entry")
        return net

    def register(self, version: str, net: _Net, info: dict, make_current: bool = True) -> str:
        filename = f"{version}.npz"
        net.meta = {**net.meta, "version": version, "schema": _schema(net.ARCH)}
        os.makedirs(self.directory, exist_ok=True)
        net.save(os.path.join(self.directory, filename))
        manifest = self.manifest()
        manifest["versions"][version] = {"file": filename, "arch": net.ARCH, "schema": _schema(net.ARCH), **info}
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
