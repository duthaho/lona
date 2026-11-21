#!/usr/bin/env python3
"""Fail closed on obvious secrets/runtime files in the portable team bundle."""

import argparse
import re
from pathlib import Path

BAD_NAMES = {".env", "auth.json", "credentials.json", "state.db", "kanban.db", "id_rsa", "id_ed25519"}
BAD_SUFFIXES = {".pem", ".key", ".sqlite", ".db", ".log", ".bak"}
PATTERNS = {
    "github-token": re.compile(r"\bgh[opusr]_[A-Za-z0-9]{20,}\b"),
    "openai-key": re.compile(r"\bsk-[A-Za-z0-9_-]{25,}\b"),
    "telegram-token": re.compile(r"\b\d{8,12}:[A-Za-z0-9_-]{30,}\b"),
    "aws-key-id": re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
    "private-key": re.compile(r"-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----"),
}


def audit(root):
    errors = []
    files = 0
    for item in root.rglob("*"):
        relative = item.relative_to(root)
        if item.is_symlink():
            errors.append(f"{relative}: symlink")
            continue
        if not item.is_file():
            continue
        files += 1
        if item.name in BAD_NAMES or item.suffix in BAD_SUFFIXES:
            errors.append(f"{relative}: runtime/credential filename")
            continue
        try:
            content = item.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            errors.append(f"{relative}: unexpected binary asset")
            continue
        for label, pattern in PATTERNS.items():
            if pattern.search(content):
                errors.append(f"{relative}: possible {label}")
    for error in errors:
        print(error)
    if not errors:
        print(f"team template audit: {files} text files, no credential signatures or symlinks")
    return bool(errors)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--templates", type=Path, default=Path("config/hermes"))
    args = parser.parse_args()
    if not args.templates.is_dir():
        parser.error(f"template directory not found: {args.templates}")
    raise SystemExit(audit(args.templates))
