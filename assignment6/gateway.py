"""Transport substrate: every LLM call goes through LLM Gateway V3.

No direct provider SDK imports anywhere in the agent. Perception passes an
explicit provider (strongest model); Decision and memory-write use
auto_route so the gateway's router pool picks a cheap worker.
"""

import os
from typing import Any, Dict, List, Optional

import httpx

GATEWAY_URL = os.getenv("LLM_GATEWAY_URL", "http://localhost:8101").rstrip("/")


def chat(
    messages: List[Dict[str, str]],
    system: str,
    *,
    max_tokens: int = 1500,
    temperature: float = 0.2,
    response_format: Optional[Dict[str, Any]] = None,
    provider: Optional[str] = None,
    auto_route: Optional[str] = None,
    timeout: float = 120.0,
) -> Dict[str, Any]:
    """POST /v1/chat and return the decoded response body.

    Raises RuntimeError (never returns half-parsed data) so the loop can
    retry the same role with a corrective message.
    """
    payload: Dict[str, Any] = {
        "messages": messages,
        "system": system,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    if response_format is not None:
        payload["response_format"] = response_format
    if provider is not None:
        payload["provider"] = provider  # explicit wins over auto_route
    elif auto_route is not None:
        payload["auto_route"] = auto_route

    errors: list[str] = []
    try:
        resp = httpx.post(f"{GATEWAY_URL}/v1/chat", json=payload, timeout=timeout)
        if resp.status_code == 200:
            return resp.json()
        errors.append(f"{GATEWAY_URL} -> HTTP {resp.status_code}: {resp.text[:300]}")
    except Exception as e:  # connection refused, timeout, ...
        errors.append(f"{GATEWAY_URL} -> {e}")
    raise RuntimeError(f"LLM Gateway call failed: {' | '.join(errors)}")
