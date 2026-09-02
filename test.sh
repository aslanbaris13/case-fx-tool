#!/usr/bin/env bash
# Runs the test suite. Tests must pass with no network at all: they mock the
# upstream, and this is run with FX_UPSTREAM_BASE pointing at a closed port.
set -euo pipefail
exec pytest -q
