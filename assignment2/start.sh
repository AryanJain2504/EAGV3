#!/bin/bash

# Navigate to the assignment directory
cd "$(dirname "$0")"

echo "======================================"
echo "🚀 Starting Smart Tab Organizer..."
echo "======================================"

# 1. Setup and Build Frontend
echo ""
echo "📦 [1/2] Building Frontend (React + Vite)..."
cd frontend

# Try to use Node 22 via nvm if it exists
if [ -s "$HOME/.nvm/nvm.sh" ]; then
    source "$HOME/.nvm/nvm.sh"
    nvm install 22
    nvm use 22
fi

npm install
# Build the extension. (If you want it to auto-rebuild on file changes, you can change this to: npm run build -- --watch)
npm run build

if [ $? -ne 0 ]; then
    echo "❌ Frontend build failed. Please check the errors above."
    exit 1
fi
echo "✅ Frontend built successfully! The 'dist' folder is ready to load into Chrome."

# 2. Setup and Start Backend
echo ""
echo "🐍 [2/2] Starting Python Backend..."
cd ../backend

# Create virtual environment if it doesn't exist
if [ ! -d "venv" ]; then
    echo "Creating Python virtual environment..."
    python3 -m venv venv
fi

# Activate virtual environment
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt -q

echo "✅ Backend is starting on http://localhost:8000"
echo "Press Ctrl+C at any time to stop the server."
echo ""

# Run the server (this will block the terminal and keep running)
python main.py
