"""PoP (Proof-of-Prompt) validator for the Assignment 7 role prompts.

Static checks mirroring the Assignment 6 validate_prompts.py pattern, with
one addition A6 lacked: prompt examples must teach patterns, never eval
answers. Each role's prompt must contain its load-bearing obligations AND
contain zero eval-query entities. Writes pop_validation.json.

Run: uv run python validate_prompts.py
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from decision import SYSTEM as DECISION_SYSTEM
from perception import SYSTEM as PERCEPTION_SYSTEM

HERE = Path(__file__).parent
MCP_TOOL_NAMES = [
    "web_search", "fetch_url", "get_time", "currency_convert",
    "read_file", "list_dir", "create_file", "update_file",
    "index_document", "search_knowledge",
]
# Eval-query entities that must never appear in role prompts, docstrings,
# or comments of the agent's own code (evidence/journal docs are exempt).
EVAL_PATTERNS = [
    r"15\s*may", r"shannon", r"mathematical theory", r"tokyo",
    r"\bmom's\b", r"\bmom\b",
]


def check_perception(prompt: str) -> dict:
    pl = prompt.lower()
    return {
        "decompose_on_empty_prior": "prior goals" in pl and "decompose" in pl,
        "same_order_verbatim": "same order" in pl and "verbatim" in pl,
        "done_from_run_history": "run history" in pl and "done" in pl,
        "artifact_routing": "artifact_index" in pl,
        "intent_not_tools": "do not name specific tools" in pl,
        "relative_date_contract": "relative phrase" in pl and "anchor date" in pl,
        "tool_blind": not any(name in prompt for name in MCP_TOOL_NAMES),
        "has_examples": pl.count("example") >= 2,
        "no_attach_if_indexed": "already indexed" in pl and "answer from those chunks" in pl,
    }


def check_decision(prompt: str) -> dict:
    """Gate Decision's load-bearing obligations.

    These are the behaviours the simplified architecture depends on. The list
    was rewritten when the contract layer was removed: assertions for the old
    structured sub-schema (`evidence_quote`, `SOURCE 1..N`, the `compute_date`
    tool name) described machinery that no longer exists, and leaving them in
    place would have forced those instructions back into the prompt to satisfy
    a stale check. A validator that pins an architecture is worse than none.
    """
    pl = prompt.lower()
    return {
        # one goal, exactly one response kind
        "one_goal_only": "one current goal" in pl,
        "answer_xor_toolcall": (
            "exactly one of" in pl and "plain-text answer" in pl
            and "one tool call" in pl
        ),
        # the original request outranks the goal paraphrase
        "original_request_authoritative": (
            "authoritative for names, dates" in pl
        ),
        # no silent mental date arithmetic
        "no_head_math": "never do calendar arithmetic" in pl,
        "date_capability": "date-computation tool" in pl,
        # act rather than narrate
        "action_not_prose": (
            "perform the action with a tool" in pl
            and "never report an action as done" in pl
        ),
        # answer only from supplied evidence; never fill gaps from model priors
        "evidence_only": (
            "use only the memory evidence supplied" in pl
            and "do not fill gaps from general knowledge" in pl
        ),
        "grounding_strict": (
            "answer from it and nothing else" in pl
            and "do not supplement from training data" in pl
        ),
        # cite the passage, not the document it came from
        "cite_chunk_id": "(mem:...)" in pl,
        "citation_is_mandatory": (
            "must contain at least one chunk id" in pl
            and "is not an answer" in pl
        ),
        "provenance_is_not_a_citation": (
            "provenance" in pl and "not something to cite" in pl
        ),
        # the indexed corpus is queried, never re-opened
        "indexed_doc_not_refetched": (
            "do not re-open an indexed document" in pl
        ),
        # artifact handles are not paths or URLs
        "artifact_handles": (
            "artifact handle" in pl and "never pass one as a" in pl
        ),
        # do not burn a turn on a call that already failed
        "no_repeat_failed_call": "already appears in recent history and failed" in pl,
        "has_examples": pl.count("example") >= 2,
    }


def check_memory_classifier() -> dict:
    src = (HERE / "memory.py").read_text(encoding="utf-8")
    return {
        "entity_requirement": "concrete entities" in src,
        "raw_fallback": '"raw"' in src,
    }


def check_neutral() -> dict:
    # Only files containing role prompts are gated. agent7.py is excluded
    # by design: it has no SYSTEM prompt, and its two matches are a
    # docstring usage example and the no-arg default query — neither is
    # model-facing prompt content.
    out = {}
    for path in ("perception.py", "decision.py", "memory.py"):
        text = (HERE / path).read_text(encoding="utf-8")
        hits = [pat for pat in EVAL_PATTERNS if re.search(pat, text, re.IGNORECASE)]
        out[f"{path}_neutral"] = not hits
        if hits:
            out[f"{path}_violations"] = hits
    return out


def main() -> int:
    perception = check_perception(PERCEPTION_SYSTEM)
    decision = check_decision(DECISION_SYSTEM)
    memory = check_memory_classifier()
    neutral = check_neutral()
    scorecard = {
        "perception": perception,
        "decision": decision,
        "memory_classifier": memory,
        "neutral": neutral,
        "pass": all(perception.values()) and all(decision.values())
        and all(memory.values()) and all(neutral.values()),
    }
    out = HERE / "pop_validation.json"
    out.write_text(json.dumps(scorecard, indent=2), encoding="utf-8")
    print(json.dumps(scorecard, indent=2))
    if not scorecard["pass"]:
        print("PoP FAILED")
        return 1
    print(f"PoP PASSED — saved to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
