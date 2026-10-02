"""Memory role: durable JSON store + pure helpers. No LLM on read.

Read/filter are deterministic keyword operations (Session 6 scope; RAG
arrives in later sessions). The only LLM touchpoint is
`memory_write_prompt()`, which builds the transcript digest prompt — the
agent loop sends it via gateway auto_route="memory" and validates each
returned item against MemoryItem before calling write().
"""

import json
import re
import uuid
from pathlib import Path
from typing import List

from schemas import HistoryItem, MemoryItem

STOPWORDS = {
    "the", "a", "an", "is", "are", "was", "were", "be", "to", "of", "and",
    "in", "on", "for", "with", "my", "me", "it", "this", "that", "what",
    "when", "where", "how", "do", "does", "did", "give", "tell", "please",
}


def _tokens(text: str) -> List[str]:
    words = re.findall(r"[a-z0-9]+", text.lower())
    return [w for w in words if w not in STOPWORDS]


class MemoryStore:
    def __init__(self, state_dir: str | Path):
        self.state_dir = Path(state_dir)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.state_dir / "memory.json"
        if not self.path.exists():
            self._save({"items": [], "artifact_seq": 0})

    # -- persistence ------------------------------------------------------
    def _load(self) -> dict:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {"items": [], "artifact_seq": 0}

    def _save(self, data: dict) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    def all_items(self) -> List[MemoryItem]:
        items = []
        for raw in self._load().get("items", []):
            try:
                items.append(MemoryItem.model_validate(raw))
            except Exception:
                continue  # skip legacy/malformed entries
        return items

    # -- read (no LLM) ----------------------------------------------------
    def read(self, query: str, history: List[HistoryItem] | None = None, top_k: int = 8) -> List[MemoryItem]:
        """Keyword hits over value entity/attribute/value + descriptor/keywords."""
        qtok = set(_tokens(query))
        if history:
            qtok |= set(_tokens(" ".join(h.text for h in history[-3:])))
        if not qtok:
            return []
        scored = []
        for item in self.all_items():
            if item.kind == "scratchpad":
                continue  # run-scoped notes never leak into hits
            val = item.value if isinstance(item.value, dict) else {}
            ent = str(val.get("entity", ""))
            attr = str(val.get("attribute", ""))
            hay = " ".join([ent, attr, str(val.get("value", "")),
                            item.descriptor, " ".join(item.keywords)])
            overlap = len(qtok & set(_tokens(hay)))
            if overlap:
                # entity/attribute exact matches weigh more than keyword noise
                exact = 2 * len(qtok & (set(_tokens(ent)) | set(_tokens(attr))))
                scored.append((overlap + exact, item))
        scored.sort(key=lambda s: s[0], reverse=True)
        return [item for _, item in scored[:top_k]]

    def filter(self, kind: str, goal_id: str | None = None) -> List[MemoryItem]:
        items = [i for i in self.all_items() if i.kind == kind]
        if goal_id is not None:
            items = [i for i in items if i.goal_id == goal_id]
        return items

    # -- write ------------------------------------------------------------
    def write(self, item: MemoryItem) -> None:
        if not item.id:
            item.id = f"mem_{uuid.uuid4().hex[:8]}"
        data = self._load()
        items = data.get("items", [])
        # Preferences overwrite on (entity, attribute); facts/outcomes append.
        if item.kind == "preference":
            val = item.value if isinstance(item.value, dict) else {}
            items = [i for i in items
                     if not (i.get("kind") == "preference"
                             and isinstance(i.get("value"), dict)
                             and i["value"].get("entity") == val.get("entity")
                             and i["value"].get("attribute") == val.get("attribute"))]
        items.append(item.model_dump(mode="json"))
        data["items"] = items
        self._save(data)

    def clear_scratchpad(self, run_id: str) -> int:
        data = self._load()
        items = data.get("items", [])
        kept = [i for i in items
                if not (i.get("kind") == "scratchpad" and i.get("run_id") == run_id)]
        data["items"] = kept
        self._save(data)
        return len(items) - len(kept)


def memory_write_prompt(transcript: str) -> str:
    """Build the digest prompt for the end-of-loop memory-write LLM call.

    Pure function (no LLM here): the agent loop sends the result via
    gateway auto_route="memory" and validates each item as MemoryItem.
    """
    return (
        "You digest one finished agent-loop transcript into durable memory items.\n"
        "Emit EXACTLY ONE JSON object: {\"items\": [MemoryItem, ...]} where each item has:\n"
        "kind (fact|preference|tool_outcome|scratchpad), keywords (list), "
        "descriptor (one line), value (OBJECT, e.g. {\"entity\": \"mom\", "
        "\"attribute\": \"birthday\", \"value\": \"2026-05-15\"}), "
        "source, confidence (0.0-1.0). Omit id, run_id, created_at (filled by the store).\n"
        "Rules: facts as value triples {entity, attribute, value}; "
        "preferences only for stated likes/dislikes; tool_outcome for dispatch records "
        "(tool name, ok/failed); scratchpad only for run-scoped notes; "
        "drop chit-chat, duplicates, and anything already obvious. "
        "Return [] items if nothing is worth keeping.\n\n"
        f"TRANSCRIPT:\n{transcript}"
    )
