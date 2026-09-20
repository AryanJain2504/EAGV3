"""Assignment 4 chain test: INTERNET -> FILE -> FILE-verify. No LLM quota needed.

Usage:
    rm -f dossiers/*.txt
    venv/bin/python test_chain.py "Tata Sons"
"""

import asyncio
import json
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def main(query: str) -> None:
    params = StdioServerParameters(command="venv/bin/python", args=["server.py"])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            res = await session.call_tool("lookup_entity", {"query": query})
            data = json.loads(res.content[0].text)
            print(f"1. INTERNET ok | summary={len(data['summary'])} chars "
                  f"| news={len(data['news'])} | related={len(data['related'])}")

            slug = "".join(c.lower() if c.isalnum() else "-" for c in query).strip("-")
            body = (f"{query.upper()} DOSSIER\n" + data["summary"][:800]
                    + "\nRELATED: " + ", ".join(data["related"][:5]))
            res = await session.call_tool("save_dossier", {"name": slug, "content": body})
            print(f"2. FILE save ok | {res.content[0].text[:150]}")

            res = await session.call_tool("read_dossier", {"name": slug})
            back = json.loads(res.content[0].text)
            assert back["content"] == body, "read-back mismatch!"
            print(f"3. FILE read ok | {back['size_bytes']} bytes, content verified")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "Tata Sons"))
