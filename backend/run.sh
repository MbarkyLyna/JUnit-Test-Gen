#!/usr/bin/env bash
cd "$(dirname "$0")"
source .venv/bin/activate
uvicorn app.main:app --reload --port 8000
