#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"

if [ ! -f env ] && [ -z "$GROQ_API_KEY" ]; then
  echo "Error: GROQ_API_KEY not set. Copy .env.example to .env and add your key."
  echo "Get a free key at: https://console.groq.com"
  exit 1
fi

python3.12 -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload
