#!/usr/bin/env bash
# 使用项目 .venv 启动 Web
set -e
cd "$(dirname "$0")"
if [[ ! -d .venv ]]; then
  python3 -m venv .venv
  .venv/bin/pip install -r requirements.txt
fi
exec .venv/bin/python web/app.py
