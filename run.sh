#!/usr/bin/env bash
# Start the search server on http://localhost:8000
cd "$(dirname "$0")"
# shellcheck disable=SC1091
source venv/bin/activate
exec uvicorn app:app --host 0.0.0.0 --port 8000
