#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR/mcp_server"

# Navigate to the backend and start the FastAPI agent server
echo "Starting Backend Agent Server..."
if [ ! -d "venv" ]; then
    python3 -m venv venv
fi
source venv/bin/activate
pip install -r requirements.txt fastapi uvicorn sse-starlette google-generativeai
python -m uvicorn client:app --host 0.0.0.0 --port 8000 &
BACKEND_PID=$!

trap 'kill "$BACKEND_PID" 2>/dev/null || true' EXIT INT TERM

echo "Prefab UI: http://127.0.0.1:8000/ui/dossier?name=tata-sons"
echo "Agent API: http://127.0.0.1:8000/api/agent?query=Tata%20Sons"
wait "$BACKEND_PID"
