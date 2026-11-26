#!/usr/bin/env bash
# Kahroba: capture the toolchain environment and create the Python venv
# (FastAPI backend + cocotb). Run as the kahroba user.
set -euo pipefail
K=$HOME/.kahroba
mkdir -p "$K" "$HOME/projects"
/opt/kahroba/devshell env -0 > "$K/ll.env"
python3 -m venv "$K/venv"
"$K/venv/bin/pip" install -q --retries 10 --timeout 60 --upgrade pip
"$K/venv/bin/pip" install -q --retries 10 --timeout 60 fastapi 'uvicorn[standard]' cocotb pytest pyyaml numpy
"$K/venv/bin/python" -c "import cocotb, fastapi, numpy; print('cocotb', cocotb.__version__)"
echo USER_OK
