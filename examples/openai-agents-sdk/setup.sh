#!/usr/bin/env bash
# Setup script for the OpenAI Agents SDK example.
# Clones the repo, installs deps, verifies the environment.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="${SCRIPT_DIR}/openai-agents-python"
REPO_URL="https://github.com/openai/openai-agents-python.git"

echo "=== OpenAI Agents SDK + Kensa eval setup ==="
echo ""

# 1. Clone repo (shallow, pinned tag)
if [ -d "${REPO_DIR}" ]; then
    echo "[ok] openai-agents-python already cloned at ${REPO_DIR}"
else
    echo "[..] Cloning ${REPO_URL} ..."
    git clone --depth 1 "${REPO_URL}" "${REPO_DIR}"
    echo "[ok] Cloned."
fi

# 2. Install the agents SDK + kensa with OpenAI instrumentation
echo ""
echo "[..] Installing dependencies ..."
if command -v uv &>/dev/null; then
    uv pip install "openai-agents>=0.13" "kensa[openai]"
else
    pip install "openai-agents>=0.13" "kensa[openai]"
fi
echo "[ok] Dependencies installed."

# 3. Verify OPENAI_API_KEY
echo ""
if [ -z "${OPENAI_API_KEY:-}" ]; then
    echo "[!!] OPENAI_API_KEY is not set."
    echo "     Export it or add it to .env in this directory."
else
    echo "[ok] OPENAI_API_KEY is set."
fi

# 4. Quick smoke test: can we import the agent?
echo ""
echo "[..] Verifying imports ..."
PYTHONPATH="${REPO_DIR}:${PYTHONPATH:-}" python -c "
import os
os.environ['OPENAI_AGENTS_DISABLE_TRACING'] = 'true'
from examples.customer_service.main import triage_agent
print(f'[ok] triage_agent loaded: {triage_agent.name}')
from kensa import instrument
print('[ok] kensa.instrument available')
"

echo ""
echo "=== Setup complete ==="
echo ""
echo "Run the eval:"
echo "  cd ${SCRIPT_DIR}"
echo "  kensa eval"
