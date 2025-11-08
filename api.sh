#!/usr/bin/env bash
PORT=8000
python3 -m http.server "$PORT"
echo "http://localhost:$PORT/api.html"
