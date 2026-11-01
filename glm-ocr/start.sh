#!/bin/sh
# Start the model server, wait until it answers, then the converter service.
# Both live in one container: the SDK's layout stage and the model are one engine.
set -e
python /app/model_server.py &
MODEL_PID=$!
i=0
until python -c "import urllib.request,sys; urllib.request.urlopen('http://127.0.0.1:${GLM_OCR_PORT:-8080}/health', timeout=2)" 2>/dev/null; do
  i=$((i+1))
  if ! kill -0 "$MODEL_PID" 2>/dev/null; then echo "model server exited" >&2; exit 1; fi
  if [ "$i" -gt 300 ]; then echo "model server did not come up" >&2; exit 1; fi
  sleep 1
done
exec python /app/service.py
