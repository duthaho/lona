#!/usr/bin/env python3
"""Seed or reconcile Lona's portable Hermes enterprise team (stdlib for seed)."""

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

FILES = ("config.yaml", "SOUL.md", "profile.yaml")
MARKER = ".lona-team-managed.json"
NAME = re.compile(r"^[a-z][a-z0-9-]*$")


def digest(path):
    if path.is_file():
        return hashlib.sha256(path.read_bytes()).hexdigest()
    h = hashlib.sha256()
    for item in sorted(path.rglob("*")):
        if item.is_file():
            h.update(str(item.relative_to(path)).encode())
            h.update(item.read_bytes())
    return h.hexdigest()


def atomic_bytes(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink() or path.parent.is_symlink():
        raise ValueError(f"refusing symlinked target: {path}")
    fd, temporary = tempfile.mkstemp(prefix=".lona-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
        if path.exists():
            os.chmod(temporary, path.stat().st_mode & 0o777)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def marker(path):
    try:
        return json.loads((path / MARKER).read_text())
    except (FileNotFoundError, ValueError):
        return {"schema": 1, "files": {}}


def update_file(source, target, state, key, sync):
    if source.is_symlink():
        raise ValueError(f"symlink in template: {source}")
    old = state["files"].get(key)
    if target.exists():
        if target.is_symlink():
            raise ValueError(f"symlink in destination: {target}")
        if not (sync and old and old == digest(target)):
            return "preserved"
    if source.is_dir():
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(source, target, symlinks=False)
    else:
        atomic_bytes(target, source.read_bytes())
    state["files"][key] = digest(target)
    return "updated" if old else "seeded"


def save_marker(path, state):
    if state["files"]:
        atomic_bytes(path / MARKER, (json.dumps(state, indent=2, sort_keys=True) + "\n").encode())


def merge_profile(source, destination):
    """Reconcile team policy while retaining credentials, routes, models and extensions."""
    try:
        import yaml
    except ImportError as exc:
        raise RuntimeError("profile sync requires PyYAML (pip install pyyaml); seed/up do not") from exc
    template = yaml.safe_load(source.read_text()) or {}
    current = yaml.safe_load(destination.read_text()) or {}
    if not isinstance(current, dict):
        raise ValueError(f"profile config is not a mapping: {destination}")
    current.setdefault("agent", {})["reasoning_effort"] = template["agent"]["reasoning_effort"]
    current.setdefault("platform_toolsets", {})
    for platform, tools in template["platform_toolsets"].items():
        existing = current["platform_toolsets"].get(platform, [])
        current["platform_toolsets"][platform] = list(dict.fromkeys([*existing, *tools]))
    current.setdefault("skills", {})
    existing = current["skills"].get("auto_load", [])
    current["skills"]["auto_load"] = list(dict.fromkeys([*existing, *template["skills"]["auto_load"]]))
    current.setdefault("approvals", {}).setdefault("mode", "smart")
    current.setdefault("security", {}).setdefault("redact_secrets", True)
    content = yaml.safe_dump(current, sort_keys=False).encode()
    if content != destination.read_bytes():
        atomic_bytes(destination, content)


def apply(templates, home, sync=False):
    roles = json.loads((templates / "team" / "roles.json").read_text())
    if roles.get("schema") != 1:
        raise ValueError("unsupported team manifest version")
    skills_root = templates / "team" / "skills"
    # Fail early: a partial bundle must never be reported as a successful seed.
    for role, spec in roles["profiles"].items():
        if not NAME.fullmatch(role):
            raise ValueError(f"invalid profile name: {role}")
        for filename in FILES:
            if not (templates / "profiles" / role / filename).is_file():
                raise FileNotFoundError(f"missing template: {role}/{filename}")
        for skill in spec["skills"]:
            if not NAME.fullmatch(skill) or not (skills_root / skill / "SKILL.md").is_file():
                raise FileNotFoundError(f"missing skill: {skill}")
            for item in (skills_root / skill).rglob("*"):
                if item.is_symlink() or not (item.is_file() or item.is_dir()):
                    raise ValueError(f"unsafe skill asset: {item}")
    for role, spec in roles["profiles"].items():
        dest = home / "profiles" / role
        dest.mkdir(parents=True, exist_ok=True)
        state = marker(dest)
        for filename in FILES:
            source = templates / "profiles" / role / filename
            target = dest / filename
            if filename == "config.yaml" and sync and target.exists():
                merge_profile(source, target)
            else:
                update_file(source, target, state, filename, sync)
        for skill in spec["skills"]:
            update_file(skills_root / skill, dest / "skills" / skill,
                        state, f"skills/{skill}", sync)
        save_marker(dest, state)
    # The default profile owns the orchestrator's team protocol; no auth is copied.
    dest = home / "skills"
    state = marker(dest)
    update_file(skills_root / "enterprise-delivery-team",
                dest / "enterprise-delivery-team", state,
                "enterprise-delivery-team", sync)
    save_marker(dest, state)
    print(f"enterprise team {'reconciled' if sync else 'seeded'}: {len(roles['profiles'])} specialists + default")


def check(templates, home):
    roles = json.loads((templates / "team" / "roles.json").read_text())["profiles"]
    missing = []
    for role, spec in roles.items():
        dest = home / "profiles" / role
        missing.extend(str(dest / name) for name in FILES if not (dest / name).is_file())
        missing.extend(str(dest / "skills" / skill / "SKILL.md")
                       for skill in spec["skills"]
                       if not (dest / "skills" / skill / "SKILL.md").is_file())
    if not (home / "skills" / "enterprise-delivery-team" / "SKILL.md").is_file():
        missing.append("default enterprise-delivery-team skill")
    if missing:
        print("missing team assets:\n" + "\n".join(missing), file=sys.stderr)
        return 1
    print(f"enterprise team assets: {len(roles)} specialists + default OK")
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("seed", "sync", "check"))
    parser.add_argument("--templates", type=Path, default=Path("config/hermes"))
    parser.add_argument("--home", type=Path, default=Path("data/hermes"))
    args = parser.parse_args()
    try:
        if args.action == "check":
            return check(args.templates, args.home)
        apply(args.templates, args.home, sync=args.action == "sync")
        return 0
    except (OSError, ValueError, RuntimeError, KeyError, TypeError) as exc:
        print(f"enterprise team {args.action} failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
