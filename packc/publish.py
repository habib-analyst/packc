"""packc publish: versioned pack tarball + sha256 + registry.json index."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import tarfile
import tempfile
from datetime import datetime, timezone

from . import emit_mcp, emit_skillmd, emit_tests
from .typecheck import check_file


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def publish(pack_dir: str, registry_dir: str) -> dict:
    """Publish the single .pack file in pack_dir into registry_dir.

    Runs `packc check` first (fails on errors), emits all three artifacts,
    then writes <name>-<version>.tar.gz + sha256 and upserts registry.json.
    Returns the registry entry.
    """
    packs = sorted(f for f in os.listdir(pack_dir) if f.endswith(".pack"))
    if not packs:
        raise SystemExit(f"publish: no .pack file found in {pack_dir}")
    if len(packs) > 1:
        raise SystemExit(f"publish: expected exactly one .pack file in {pack_dir}, "
                         f"found {packs}")
    pack_path = os.path.join(pack_dir, packs[0])

    pf, errors = check_file(pack_path)
    if errors:
        raise SystemExit("publish: packc check failed:\n" + "\n".join(str(e) for e in errors))

    name, version = pf.pack.name, pf.pack.version
    tarball_name = f"{name}-{version}.tar.gz"
    os.makedirs(registry_dir, exist_ok=True)
    tarball_path = os.path.join(registry_dir, tarball_name)
    if os.path.exists(tarball_path):
        raise SystemExit(f"publish: {tarball_name} already exists in the registry "
                         f"(name+version must be unique; bump the version)")

    staging = tempfile.mkdtemp(prefix="packc-publish-")
    try:
        root = os.path.join(staging, f"{name}-{version}")
        os.makedirs(root)
        shutil.copy(pack_path, os.path.join(root, os.path.basename(pack_path)))
        skillmd_name = f"{name}.SKILL.md"
        mcp_name = f"{name.replace('-', '_')}_mcp_server.py"
        tests_name = f"test_{name.replace('-', '_')}_conformance.py"
        with open(os.path.join(root, skillmd_name), "w", encoding="utf-8") as fh:
            fh.write(emit_skillmd.emit(pf))
        mcp_path = os.path.join(root, mcp_name)
        with open(mcp_path, "w", encoding="utf-8") as fh:
            fh.write(emit_mcp.emit(pf))
        with open(os.path.join(root, tests_name), "w", encoding="utf-8") as fh:
            fh.write(emit_tests.emit(pf, mcp_path))
        with tarfile.open(tarball_path, "w:gz") as tar:
            tar.add(root, arcname=f"{name}-{version}")
    finally:
        shutil.rmtree(staging, ignore_errors=True)

    digest = _sha256(tarball_path)
    entry = {
        "name": name,
        "version": version,
        "description": pf.pack.description,
        "author": pf.pack.author,
        "tarball": tarball_name,
        "sha256": digest,
        "skills": len(pf.skills),
        "published_at": datetime.now(timezone.utc).isoformat(),
    }
    reg_path = os.path.join(registry_dir, "registry.json")
    registry = []
    if os.path.exists(reg_path):
        with open(reg_path, encoding="utf-8") as fh:
            registry = json.load(fh)
    registry = [e for e in registry
                if not (e.get("name") == name and e.get("version") == version)]
    registry.append(entry)
    with open(reg_path, "w", encoding="utf-8") as fh:
        json.dump(registry, fh, indent=2)
        fh.write("\n")
    return entry
