"""Action role: MCP stdio dispatch + artifact gate. Zero LLM calls.

Outputs > ARTIFACT_MIN_BYTES become artifacts (string id, bytes on disk);
everything smaller returns inline. The loop — never the LLM — moves bytes.
"""

import asyncio
import hashlib
import json
import shutil
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from schemas import ActionOut, Artifact, ToolCall, ToolDef

ARTIFACT_MIN_BYTES = 4096

# Static tool catalogue (mirrors mcp_server.py). Decision receives these as
# text; the server is the source of truth at dispatch time.
TOOLS: List[ToolDef] = [
    ToolDef(name="web_search", description="Web search (Tavily primary, DDG fallback). Max 5 results.",
            input_schema={"type": "object",
                          "properties": {"query": {"type": "string"},
                                         "max_results": {"type": "integer", "default": 5}},
                          "required": ["query"]}),
    ToolDef(name="fetch_url", description="Fetch clean markdown from a URL via crawl4ai.",
            input_schema={"type": "object",
                          "properties": {"url": {"type": "string"},
                                         "timeout": {"type": "integer", "default": 20}},
                          "required": ["url"]}),
    ToolDef(name="get_time", description="Current time in an IANA timezone.",
            input_schema={"type": "object",
                          "properties": {"timezone": {"type": "string", "default": "UTC"}},
                          "required": []}),
    ToolDef(name="currency_convert", description="Convert between ISO-3 currencies.",
            input_schema={"type": "object",
                          "properties": {"amount": {"type": "number"},
                                         "from_currency": {"type": "string"},
                                         "to_currency": {"type": "string"}},
                          "required": ["amount", "from_currency", "to_currency"]}),
    ToolDef(name="read_file", description="Read a UTF-8 file from the sandbox.",
            input_schema={"type": "object", "properties": {"path": {"type": "string"}},
                          "required": ["path"]}),
    ToolDef(name="list_dir", description="List a sandbox directory.",
            input_schema={"type": "object", "properties": {"path": {"type": "string", "default": "."}},
                          "required": []}),
    ToolDef(name="create_file", description="Create a new sandbox file; errors if it exists.",
            input_schema={"type": "object",
                          "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
                          "required": ["path", "content"]}),
    ToolDef(name="update_file", description="Overwrite an existing sandbox file.",
            input_schema={"type": "object",
                          "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
                          "required": ["path", "content"]}),
    ToolDef(name="edit_file", description="Find-and-replace inside a sandbox file.",
            input_schema={"type": "object",
                          "properties": {"path": {"type": "string"}, "find": {"type": "string"},
                                         "replace": {"type": "string"},
                                         "replace_all": {"type": "boolean", "default": False}},
                          "required": ["path", "find", "replace"]}),
]


class ArtifactStore:
    """Run-scoped artifact bytes. Only the string id ("art:<sha256-prefix>")
    travels in LLM packets; bytes live on disk under state/artifacts/."""

    def __init__(self, state_dir: str | Path, run_id: str):
        self.dir = Path(state_dir) / "artifacts"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.run_id = run_id

    @staticmethod
    def _handle(data: bytes) -> str:
        return "art:" + hashlib.sha256(data).hexdigest()[:8]

    def _fname(self, artifact_id: str) -> Path:
        return self.dir / f"{self.run_id}_{artifact_id.replace(':', '_')}.bin"

    def put(self, data: bytes, tool: str, content_type: str = "text/markdown") -> Artifact:
        handle = self._handle(data)
        self._fname(handle).write_bytes(data)
        return Artifact(id=handle, content_type=content_type, size_bytes=len(data),
                        source=f"tool:{tool}",
                        descriptor=f"{tool} output, {len(data)} bytes")

    def get(self, artifact_id: str) -> bytes:
        return self._fname(artifact_id).read_bytes()

    def exists(self, artifact_id: str) -> bool:
        return self._fname(artifact_id).exists()


class MCPClient:
    """Single persistent stdio session per agent run. One place owns the
    subprocess; the loop calls call_tool() and never touches transport."""

    def __init__(self, server_path: str | Path, timeout: float = 120.0):
        self.server_path = str(server_path)
        self.timeout = timeout
        self._session = None
        self._stack = None
        self._uv = shutil.which("uv") or "uv"

    async def _ensure(self):
        if self._session is not None:
            return self._session
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        import contextlib
        self._stack = contextlib.AsyncExitStack()
        await self._stack.__aenter__()
        params = StdioServerParameters(
            command=self._uv,
            args=["run", "--project", str(Path(self.server_path).parent),
                  "python", self.server_path],
        )
        read, write = await self._stack.enter_async_context(stdio_client(params))
        self._session = await self._stack.enter_async_context(ClientSession(read, write))
        await self._session.initialize()
        return self._session

    async def list_tools(self) -> List[str]:
        session = await self._ensure()
        result = await session.list_tools()
        return [t.name for t in result.tools]

    async def call_tool(self, name: str, arguments: Dict[str, Any]) -> Any:
        session = await self._ensure()
        result = await session.call_tool(name, arguments)
        # FastMCP returns content blocks; unwrap to plain text.
        parts = []
        for block in (result.content or []):
            text = getattr(block, "text", None)
            if text is not None:
                parts.append(text)
            else:
                parts.append(json.dumps(block.model_dump() if hasattr(block, "model_dump") else str(block)))
        return "\n".join(parts) if parts else ""

    async def close(self):
        if self._stack is not None:
            await self._stack.aclose()
            self._stack = None
            self._session = None


def execute(call: ToolCall, run_id: str, artifacts: ArtifactStore,
            raw_text: str) -> ActionOut:
    """Apply the >4KB artifact gate to one raw tool result.

    Kept synchronous and LLM-free on purpose: dispatch (async, in agent6)
    and gating (this pure function) are independently testable.
    """
    t0 = time.time()
    size = len(raw_text.encode("utf-8"))
    if size > ARTIFACT_MIN_BYTES:
        art = artifacts.put(raw_text.encode("utf-8"), call.name)
        return ActionOut(ok=True, text=None, artifact_id=art.id,
                         tool=call.name, latency_ms=int((time.time() - t0) * 1000))
    return ActionOut(ok=True, text=raw_text, artifact_id=None,
                     tool=call.name, latency_ms=int((time.time() - t0) * 1000))


def failed(call: ToolCall, error: str) -> ActionOut:
    return ActionOut(ok=False, text=None, artifact_id=None,
                     tool=call.name, latency_ms=0, error=error[:500])


_LOOP: asyncio.AbstractEventLoop | None = None


def run_async(coro):
    """Bridge for the sync agent loop. All coroutines share ONE event loop
    per process: the MCP stdio session is bound to the loop that created it,
    so per-call asyncio.run() breaks cleanup (generator didn't stop)."""
    global _LOOP
    if _LOOP is None or _LOOP.is_closed():
        _LOOP = asyncio.new_event_loop()
    return _LOOP.run_until_complete(coro)
