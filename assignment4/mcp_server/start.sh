#!/bin/bash

echo "Starting the Autonomous Knowledge Graph MCP Server..."

# Set up a virtual environment to avoid PEP 668 "externally-managed-environment" errors
if [ ! -d "venv" ]; then
    echo "Creating Python virtual environment..."
    python3 -m venv venv
fi

echo "Activating virtual environment..."
source venv/bin/activate

echo "Installing dependencies..."
pip install -r requirements.txt

echo ""
echo "🚀 Launching FastMCP Inspector (Prefab Apps mode)..."
echo "Please click the link below to open the UI!"
fastmcp dev apps server.py
