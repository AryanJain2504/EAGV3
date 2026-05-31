#!/bin/bash
set -e

# Start frontend in the background
echo "Starting frontend web app on http://localhost:5173 ..."
cd frontend
npm run dev &
cd ..

# Start backend
echo "Starting backend..."
cd backend
source .venv/bin/activate

if [ -f .env ]; then
  echo "Loading environment variables from .env"
  export $(grep -v '^#' .env | xargs)
elif [ -z "$GEMINI_API_KEY" ]; then
  echo "WARNING: GEMINI_API_KEY is not set. The agent will fail when making API calls."
  echo "Please run: export GEMINI_API_KEY='your_api_key' before running this script."
fi

uvicorn main:app --host 0.0.0.0 --port 8000
