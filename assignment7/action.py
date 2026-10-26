"""Action: MCP dispatcher.

Pure dispatch — no LLM here. When a tool result is large, this layer pushes
the bytes to the artifact store and returns the handle alongside a short
descriptor. Memory will record both. Decision never sees the bytes unless
Perception decides a later goal needs them.
"""

from __future__ import annotations

import json
from typing import Any

from mcp import ClientSession

import artifacts
from schemas import ActionResult, ToolCall

# Tool outputs larger than this go to the artifact store rather than into
# memory/decision context. 4 KB is roughly one A4 page of text.
ARTIFACT_THRESHOLD_BYTES = 4096


def _result_to_text(result: Any) -> str:
    """Collapse an MCP CallToolResult into one text string."""
    if not getattr(result, "content", None):
        return ""
    parts: list[str] = []
    for c in result.content:
        text = getattr(c, "text", None)
        if text is not None:
            parts.append(text)
        else:
            parts.append(str(c))
    return "\n".join(parts)


def _is_error_text(text: str) -> bool:
    # Failure is a property of the transport envelope, not the page
    # content. Every real tool error in this system is prefixed at the
    # start ("Error executing tool ...", "ERROR: ..."); page prose about
    # exceptions and tracebacks must never count. Scanning content for
    # bare words once burned a full 20-iteration run on asyncio tutorials.
    normalized = str(text or "").strip().lower()
    return (
        normalized.startswith("error:")
        or normalized.startswith("error executing tool")
        or "browsertype.launch" in normalized
        or "executable doesn't exist" in normalized
    )


async def execute(
    session: ClientSession,
    tool_call: ToolCall,
    tool_schemas: dict[str, dict] | None = None,
) -> ActionResult:
    """Run one validated MCP call and return a typed result.

    When the result is larger than ARTIFACT_THRESHOLD_BYTES, the full bytes
    are written to the artifact store and the returned descriptor is a short
    preview plus the artifact id.
    """
    tool_call = ToolCall.model_validate(tool_call.model_dump())

    def _fail(text: str) -> ActionResult:
        return ActionResult(
            tool=tool_call.name, arguments=tool_call.arguments,
            text=text, success=False,
        )

    outcome: ActionResult | None = None
    if tool_schemas is not None:
        schema = tool_schemas.get(tool_call.name)
        if schema is None:
            outcome = _fail(f"ERROR: unknown MCP tool {tool_call.name!r}")
        else:
            try:
                import jsonschema
                jsonschema.validate(tool_call.arguments, schema)
            except Exception as exc:
                outcome = _fail(
                    f"ERROR: invalid arguments for {tool_call.name}: {exc}"
                )

    # Decision sometimes hallucinates that `art:...` handles are file paths
    # or URLs. Block that here so the bad call doesn't waste an iteration.
    if outcome is None:
        for arg_name in ("path", "url"):
            v = tool_call.arguments.get(arg_name)
            if isinstance(v, str) and v.startswith("art:"):
                outcome = _fail(
                    f"ERROR: {arg_name}={v!r} is an artifact handle, not a "
                    f"path/URL. Artifact bytes are attached by Perception when "
                    f"needed — answer from ATTACHED ARTIFACTS instead of calling "
                    f"{tool_call.name}."
                )
                break

    if outcome is None:
        result = await session.call_tool(tool_call.name, arguments=tool_call.arguments)
        text = _result_to_text(result)
        nbytes = len(text.encode("utf-8"))

        if _is_error_text(text):
            outcome = _fail(text)
        # Fetched pages are source material for later extraction goals even
        # when the response is short enough to fit in the normal preview.
        elif tool_call.name == "fetch_url" or nbytes > ARTIFACT_THRESHOLD_BYTES:
            art_id = artifacts.put(
                text.encode("utf-8"),
                content_type="text/plain",
                source=f"mcp:{tool_call.name}",
                descriptor=f"{tool_call.name}({json.dumps(tool_call.arguments)[:80]}) → {nbytes} bytes",
            )
            descriptor = (
                f"[artifact {art_id}, {nbytes} bytes] preview: "
                + text[:240].replace("\n", " ")
                + ("..." if nbytes > 240 else "")
            )
            outcome = ActionResult(
                tool=tool_call.name, arguments=tool_call.arguments,
                text=descriptor, success=True, artifact_id=art_id,
            )
        else:
            outcome = ActionResult(
                tool=tool_call.name, arguments=tool_call.arguments,
                text=text, success=True,
            )

    return outcome
