#!/bin/sh
# Mac / Linux: ./run.sh   (first time: pip3 install -r requirements.txt && python3 -m playwright install chromium)
cd "$(dirname "$0")" && exec python3 -m rokwatch "$@"
