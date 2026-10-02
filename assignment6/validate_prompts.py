"""PoP (Proof-of-Prompt) validator for the Perception + Decision prompts.

Static checks mirroring the Assignment 5 validate_prompt.py pattern:
each role's prompt must contain its load-bearing obligations. Writes
pop_validation.json for the README + submission.
"""

import json
import sys
from pathlib import Path

from decision import DECISION_PROMPT
from perception import PERCEPTION_PROMPT


def check_perception(p: str) -> dict:
    pl = p.lower()
    return {
        "decompose_on_empty_prior": "prior_goals is empty" in pl and "decompose" in pl,
        "same_order_no_drop": "same" in pl and "never insert" in pl and "never drop" in pl,
        "done_only_from_history": "flip done=true only" in pl,
        "artifact_id_routing": "attach_artifact_id" in pl and "never" in pl and "artifact bytes" in pl,
        "strict_json": "exactly one json object" in pl,
        "few_shots": p.count("FEW-SHOT") >= 2,
    }


def check_decision(p: str) -> dict:
    pl = p.lower()
    return {
        "one_goal_only": "one goal" in pl,
        "answer_xor_toolcall": "never both" in pl,
        "single_tool_max": "never emit more than one tool call" in pl,
        "no_tool_invention": "do not invent tools" in pl,
        "attachment_extraction": "attachment" in pl,
        "strict_json": "only the json object" in pl,
        "few_shots": p.count("FEW-SHOT") >= 2,
    }


def main() -> int:
    perc = check_perception(PERCEPTION_PROMPT)
    dec = check_decision(DECISION_PROMPT)
    scorecard = {
        "perception": perc,
        "decision": dec,
        "perception_pass": all(perc.values()),
        "decision_pass": all(dec.values()),
    }
    out = Path(__file__).parent / "pop_validation.json"
    out.write_text(json.dumps(scorecard, indent=2), encoding="utf-8")
    print(json.dumps(scorecard, indent=2))
    if not (scorecard["perception_pass"] and scorecard["decision_pass"]):
        print("❌ PoP FAILED")
        return 1
    print(f"✅ PoP PASSED — saved to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
