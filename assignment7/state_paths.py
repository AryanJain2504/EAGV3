"""One runtime state directory for every layer.

The agent process, the MCP subprocess, the vector index, and the artifact
store must agree on where state lives. `EAGV3_STATE_DIR` overrides the
default so evidence runs can use an isolated directory per query family
without mixing indexes or histories.
"""

from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
STATE_DIR = Path(os.environ.get("EAGV3_STATE_DIR") or (BASE_DIR / "state"))
STATE_DIR.mkdir(parents=True, exist_ok=True)
