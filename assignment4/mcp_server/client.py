import asyncio
import json
import os
import re
import sys
import uvicorn
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from sse_starlette.sse import EventSourceResponse

import httpx
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

# LLM via local Gateway V3 (Ollama worker: free, unlimited, no quota).
# Override with LLM_PROVIDER env (e.g. gemini when quota is back).
GATEWAY_URL = os.getenv("LLM_GATEWAY_URL", "http://localhost:8101").rstrip("/")
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "ollama")


def call_llm(prompt: str) -> str:
    body = {
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 1500,
        "temperature": 0.2,
        "provider": LLM_PROVIDER,
    }
    r = httpx.post(f"{GATEWAY_URL}/v1/chat", json=body, timeout=300.0)
    r.raise_for_status()
    return r.json().get("text", "")

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

SYSTEM_PROMPT = """You are an Autonomous Knowledge Graph Agent.
You have access to MCP tools on the server. Your goal: investigate a target using ALL THREE tool categories in order — INTERNET, then FILE, then UI. Skipping any category fails the run.

When the user asks for stock performance, you MUST complete these steps IN ORDER:
1. INTERNET — Call `analyze_stock_performance` with the user's complete request. It resolves the ticker and fetches the requested historical period.
2. FILE — Call `save_dossier` with name "market-performance" and content containing the ticker, period, start price, end price, and return percentage from the tool result.
3. UI — Call `show_dossier` with name "market-performance". This renders the Prefab Performance and Comparison tabs.

When the user asks to add or compare another stock, you MUST complete these steps IN ORDER:
1. INTERNET — Call `add_stock_comparison` with the user's complete request.
2. FILE — Call `update_dossier` with name "market-performance" and content updated with both stocks' returns.
3. UI — Call `show_dossier` with name "market-performance".

When the user asks to add a chart, pie chart, visualization, or improve the
comparison after market data already exists, call `show_dossier` directly with
name "market-performance". The Prefab dashboard already renders the
comparison cards, return split pie chart, and trend charts from saved state.

For general entities, use this sequence:
1. INTERNET — Call `lookup_entity` on the target to get its Wikipedia summary plus recent news headlines.
2. FILE — Call `save_dossier` with name "<target-slug>" and content = a short dossier.
3. UI — Call `show_dossier` with the same dossier name.

Rules:
- Exactly one tool call per turn. Never call two tools at once.
- Never skip a step and never reorder them. If a tool errors, retry it once with fixed arguments before moving on.
- Optional extras only AFTER the three mandatory steps: `read_dossier` or `list_dossiers`.

When calling a tool, reply ONLY in JSON:
{"tool_name": "<name>", "tool_arguments": {"arg1": "val1"}}

When all three mandatory steps are done and you have the dashboard schema, reply ONLY in JSON:
{"answer": "<summary quoting the saved dossier file path AND the /ui/dossier?name=<slug> page>", "prefab_ui": <the JSON schema returned by show_dossier>}
"""

def parse_llm(text):
    text = text.strip()
    if text.startswith("```json"): text = text[7:]
    if text.startswith("```"): text = text[3:]
    if text.endswith("```"): text = text[:-3]
    try:
        parsed = json.loads(text.strip())
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            raise
        parsed = json.loads(match.group(0))

    # Accept the shorter shape used by some local models.
    if "tool" in parsed and "tool_name" not in parsed:
        parsed["tool_name"] = parsed.pop("tool")
    if "args" in parsed and "tool_arguments" not in parsed:
        parsed["tool_arguments"] = parsed.pop("args")
    return parsed


def summarize_tool_result(tool_name: str, result_text: str) -> str:
    """Keep model context compact while retaining the facts needed for sequencing."""
    try:
        result = json.loads(result_text)
    except json.JSONDecodeError:
        return result_text[:2000]

    if tool_name in {"analyze_stock_performance", "add_stock_comparison"}:
        if tool_name == "analyze_stock_performance":
            finance = result.get("finance", {})
            return json.dumps({
                "ok": result.get("ok"),
                "ticker": result.get("ticker"),
                "days": result.get("days"),
                "start_price": finance.get("start_price"),
                "end_price": finance.get("end_price"),
                "change_percent": finance.get("change_percent"),
            })
        comparison = result.get("comparison", {})
        return json.dumps({
            "ok": result.get("ok"),
            "primary": result.get("primary"),
            "comparison_ticker": comparison.get("ticker"),
            "comparison_days": comparison.get("days"),
            "comparison_start_price": comparison.get("start_price"),
            "comparison_end_price": comparison.get("end_price"),
            "comparison_change_percent": comparison.get("change_percent"),
        })

    if tool_name in {"save_dossier", "update_dossier", "read_dossier"}:
        if tool_name == "read_dossier":
            result["content"] = result.get("content", "")[:1000]
        return json.dumps(result)

    return json.dumps(result)[:4000]

async def run_agent(query: str):
    yield json.dumps({"status": "info", "message": f"Starting autonomous investigation for: {query}"})
    
    server_params = StdioServerParameters(
        command=sys.executable,
        args=["server.py"],
        env=os.environ.copy()
    )
    
    messages = [{"role": "user", "parts": [SYSTEM_PROMPT + "\n\nUser target: " + query]}]
    
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            
            yield json.dumps({"status": "info", "message": "MCP Session initialized. Tools loaded."})
            
            # Allow up to 15 loops for deep recursive research
            last_error = ""
            for i in range(15):
                yield json.dumps({"status": "thinking", "message": f"Agent is thinking... (Loop {i+1})"})
                prompt = "\n".join([m["parts"][0] for m in messages])
                
                response_text = await asyncio.to_thread(call_llm, prompt)
                messages.append({"role": "model", "parts": [response_text]})
                
                try:
                    parsed = parse_llm(response_text)
                except Exception as error:
                    last_error = f"Invalid agent response: {error}"
                    yield json.dumps({"status": "warning", "message": last_error})
                    messages.append({"role": "user", "parts": ["Parse error. Return valid JSON."]})
                    continue
                    
                if "answer" in parsed:
                    yield json.dumps({"status": "final", "message": parsed["answer"], "prefab_ui": parsed.get("prefab_ui")})
                    return
                    
                if "tool_name" in parsed:
                    tool_name = parsed["tool_name"]
                    args = parsed.get("tool_arguments", {})
                    yield json.dumps({"status": "action", "message": f"Executing Tool: {tool_name}", "args": args})
                    
                    try:
                        result = await session.call_tool(tool_name, arguments=args)
                        tool_result_text = result.content[0].text if result.content else "Success"
                        compact_result = summarize_tool_result(tool_name, tool_result_text)
                        yield json.dumps({"status": "result", "message": f"Tool {tool_name} returned data."})
                        messages.append({"role": "user", "parts": [f"Tool Result: {compact_result}"]})
                    except Exception as e:
                        last_error = f"Tool {tool_name} failed: {e}"
                        yield json.dumps({"status": "warning", "message": last_error})
                        messages.append({"role": "user", "parts": [f"Tool Error: {str(e)}"]})

            yield json.dumps({
                "status": "error",
                "message": f"Agent stopped after 15 iterations. {last_error}".strip(),
            })

@app.get("/api/agent")
async def agent_endpoint(query: str):
    return EventSourceResponse(run_agent(query))


@app.get("/ui/dossier", response_class=HTMLResponse)
async def dossier_page(name: str):
    """Serve the dossier as a real Prefab page (PrefabApp.html()).

    The agent still calls show_dossier as its UI tool; this endpoint renders
    the identical Prefab app (same builder) for viewing in a browser."""
    from server import _build_dossier_app
    return _build_dossier_app(name).html()

if __name__ == "__main__":
    uvicorn.run("client:app", host="0.0.0.0", port=8000, reload=True)
