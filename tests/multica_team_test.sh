#!/usr/bin/env bash
# Exercise real provisioning code against labeled local HTTP test fixtures.
set -euo pipefail
cd "$(dirname "$0")/.."
python3 -W error::ResourceWarning -m unittest discover -s tests -p test_multica_team.py -v
