#!/bin/sh
cd "$(dirname "$0")" || exit 1
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
if [ -x .venv/bin/python ]; then
    exec .venv/bin/python app.py
fi
exec python3 app.py
