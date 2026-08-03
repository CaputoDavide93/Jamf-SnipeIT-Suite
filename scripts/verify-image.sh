#!/usr/bin/env bash
#
# Pre-deploy gate: validate the code on the interpreter production actually
# uses, not the one that happens to be in your local venv.
#
# Why this exists: on 2026-08-03 a missing `from typing import Optional` in
# infra/mutex.py passed a full local test run on Python 3.14 and then crashed
# every container on import. Python 3.14 defers annotation evaluation
# (PEP 649), so the bad annotation was never evaluated locally; the pinned
# 3.12 runtime evaluates it eagerly and raised NameError. The image reached
# ECR and a production run aborted before any module executed.
#
# Run this before `docker push`. It:
#   1. builds the image,
#   2. imports every module under the image's Python,
#   3. runs the test suite inside the image,
#   4. prints what actually landed in /app/config (secret-leak check).
#
# Usage: scripts/verify-image.sh [tag]

set -euo pipefail

TAG="${1:-jsi-verify:predeploy}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

echo "==> Building $TAG"
docker build --platform linux/amd64 -t "$TAG" . >/dev/null

echo "==> Runtime version"
docker run --rm --entrypoint python "$TAG" -V

echo "==> Importing every module on the runtime interpreter"
docker run --rm --entrypoint python "$TAG" -c "
import pkgutil, importlib, sys
sys.path.insert(0, '/app/src')
failed = []
count = 0
for mod in pkgutil.walk_packages(['/app/src'], ''):
    if mod.name.startswith('scripts'):
        continue
    count += 1
    try:
        importlib.import_module(mod.name)
    except Exception as exc:
        failed.append((mod.name, type(exc).__name__, exc))
for name, kind, exc in failed:
    print(f'  FAIL {name}: {kind}: {exc}')
if failed:
    sys.exit(1)
print(f'  {count} modules imported cleanly')
"

echo "==> Test suite on the runtime interpreter"
docker run --rm --user root -v "$REPO_ROOT/tests:/app/tests:ro" \
    --entrypoint sh "$TAG" -c \
    '/opt/venv/bin/pip install -q --disable-pip-version-check pytest && \
     cd /app && /opt/venv/bin/python -m pytest tests/ -q' | tail -3

echo "==> Files shipped in /app/config (must contain no credentials)"
docker run --rm --entrypoint sh "$TAG" -c 'ls -1 /app/config' | sed 's/^/  /'

echo
echo "OK — safe to push $TAG"
