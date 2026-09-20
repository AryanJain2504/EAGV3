"""Natural-language CLI for the Assignment 4 MCP agent.

Examples:
    venv/bin/python dossier_cli.py "show me SRM stock performance in the past 87 days"
    venv/bin/python dossier_cli.py "add Google stock as a comparison"

The FastAPI agent performs the Internet -> File -> Prefab UI sequence using
the local LLM gateway. Start the backend with start_dashboard.sh first.

Usage:
    venv/bin/python dossier_cli.py                  # interactive loop
    venv/bin/python dossier_cli.py "Tata Sons"       # single shot
"""

import asyncio
import sys
import webbrowser

import httpx

AGENT_URL = "http://127.0.0.1:8000/api/agent"


async def investigate(query: str, open_browser: bool = True) -> None:
    async with httpx.AsyncClient(timeout=300) as client:
        async with client.stream("GET", AGENT_URL, params={"query": query}) as response:
            response.raise_for_status()
            final_message = ""
            async for line in response.aiter_lines():
                if not line.startswith("data: "):
                    continue
                import json
                event = json.loads(line[6:])
                print(f"[{event.get('status', 'event')}] {event.get('message', '')}")
                if event.get("status") == "final":
                    final_message = event.get("message", "")
    if final_message:
        print(f"\n{final_message}")
        if open_browser:
            webbrowser.open("http://127.0.0.1:8000/ui/dossier?name=market-performance")


async def amain() -> None:
    if len(sys.argv) > 1:
        await investigate(" ".join(sys.argv[1:]))
        return
    print("Assignment 4 agent CLI — describe an investigation, blank line quits.")
    while True:
        try:
            query = input("agent> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not query:
            break
        try:
            await investigate(query)
        except Exception as e:
            print(f"[error] {e}")


if __name__ == "__main__":
    asyncio.run(amain())
