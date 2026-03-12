#!/bin/bash

echo "=========="
echo "Running $0" 
date

# Navigate to the script's directory to ensure all paths are correct
cd "$(dirname "$0")"

# Load environment variables if the .env file exists
if [ -f .env ]; then
  set -a
  source .env
  set +a
fi

# Activate the virtual environment
source .venv/bin/activate

# Force UTF-8 encoding to prevent log corruption (blob data)
export PYTHONIOENCODING=utf-8
export LANG=C.UTF-8
# FIX: Force unbuffered output to prevent multi-byte characters (Chinese)
# from being split across buffer boundaries, which triggers "blob data" in journald.
export PYTHONUNBUFFERED=1

time python3 jira_translator.py \
    --projects PROJECT1,PROJECT2 \
    --no-confirm --full-ticket
EXIT_CODE=$?

if [ -n "$HEALTH_IO_UUID" ]; then
  echo "Pinging Healthchecks.io at $HEALTH_IO_UUID with ${EXIT_CODE}"
  curl -fsS -m 10 --retry 5 \
    "https://hc-ping.com/$HEALTH_IO_UUID/${EXIT_CODE}" \
    > /dev/null || true
fi

echo "=========="

exit $EXIT_CODE
