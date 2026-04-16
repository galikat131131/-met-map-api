#!/usr/bin/env bash
# Ping the deployed API every 10 min to keep Render's free tier from spinning down.
# Usage: ./keepalive.sh            (uses default URL)
#        ./keepalive.sh <url>      (override)
#
# Run in a spare terminal tab. Ctrl+C to stop.

set -u
URL="${1:-https://met-asian-art-api.onrender.com/}"
INTERVAL=600   # seconds

echo "Pinging $URL every ${INTERVAL}s. Ctrl+C to stop."
while true; do
  ts=$(date '+%Y-%m-%d %H:%M:%S')
  code=$(curl -sS -o /dev/null -w "%{http_code}" --max-time 60 "$URL" || echo "ERR")
  echo "[$ts] $code"
  sleep "$INTERVAL"
done
