#!/usr/bin/env bash
set -eu
pos_task_dir="$(cd "$(dirname "$0")" && pwd)"
cd "$pos_task_dir"
if [ ! -x .venv/bin/python ]; then
  echo 'Create the backend .venv and install requirements first.'
  exit 1
fi
.venv/bin/python configure_local_ports.py
.venv/bin/python -m app.seed
exec .venv/bin/python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8011
