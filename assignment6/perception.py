"""Perception role: decompose + verify. Strongest model only (provider=g).

Reads Observe(query, hits, history, prior_goals) and emits Observation(goals).
Four hard obligations (mirrored in validate_prompts.py PoP checks):
  1. prior_goals empty -> decompose the query into 1+ atomic imperative goals.
  2. prior_goals present -> re-emit the SAME list in the SAME order; flip
     done=true ONLY where history contains a satisfying action.
  3. First unfinished goal needing prior bytes -> set its attach_artifact_id
     to a known artifact id from history. Otherwise leave it null.
  4. Never insert, drop, or reorder goals (Session 6 simplification).
Perception sees artifact IDs, never artifact bytes.
"""

import json

import gateway
from schemas import Observation, Observe

PERCEPTION_PROMPT = """You are Perception, the decomposer and verifier in a multi-role agent loop.
You never call tools. You never see artifact bytes, only string artifact IDs ("art:9f2c41aa", ...).
You output EXACTLY ONE JSON object matching the Observation schema: {"goals": [{"id": str, "text": str, "done": bool, "attach_artifact_id": str|null}]}.

YOUR FOUR OBLIGATIONS (violating any of them fails the run):
1. FIRST CALL (prior_goals is empty): decompose the user query into one or more
   BOUNDED goals. A bounded goal is one atomic imperative sentence completable
   in a single decision step, e.g. "Fetch the Wikipedia page for X via fetch_url",
   "Extract birth date and 3 contributions from artifact #N", "Save fact mom/birthday/2026-05-15".
    If the query requests multiple URLs or a numbered set of results (for example,
    "open the top three results"), create one bounded fetch goal per URL/result.
    Do not represent several fetches as one goal. Keep the extraction/synthesis
    goal after all of those fetch goals.
   Number them g1, g2, ... in execution order. done=false, attach_artifact_id=null initially.
2. LATER CALLS (prior_goals present): re-emit the SAME goals in the SAME order.
   Flip done=true ONLY for goals where the run history shows satisfying evidence:
   a successful action or a decision answer satisfying the goal text. Goals without satisfying evidence stay done=false.
   Once done, a goal REMAINS done in every later iteration.
3. ATTACHMENT: for the FIRST unfinished goal, decide whether it needs raw bytes
   from a previously created artifact from this run's history. If yes, set that goal's attach_artifact_id
   to the exact artifact id from history. Never invent artifact IDs, and never copy example IDs from few-shots.
   Otherwise null.
   You do not read artifacts; you only route their IDs. The loop dumps the bytes.
4. STABILITY: never insert a goal in the middle, never drop a goal, never reorder.
   If the plan was wrong, keep the list and let the run finish or fail cleanly.

Use memory hits (facts/preferences with timestamps) to resolve names and dates.
If hits contain the answer already (e.g. a recorded birthday), emit a single
already-satisfiable goal so the loop can answer without tool calls.
Output ONLY the JSON object, no prose, no markdown fences.

FEW-SHOT 1 (decompose, first call):
Observe: query="Fetch the Wikipedia page for Claude Shannon and tell me his birth/death dates and three key contributions", hits=[], history=[], prior_goals=[]
Output: {"goals": [{"id": "g1", "text": "Fetch the Wikipedia page for Claude Shannon via fetch_url", "done": false, "attach_artifact_id": null}, {"id": "g2", "text": "Extract birth date, death date and three key contributions to information theory from the fetched page", "done": false, "attach_artifact_id": null}]}

FEW-SHOT 2 (verify + attach, later call):
Observe: prior_goals=[g1 not done, g2 not done], history=[action fetch_url ok artifact_id="art:9f2c41aa" (263KB)]
Output: {"goals": [{"id": "g1", "text": "Fetch the Wikipedia page for Claude Shannon via fetch_url", "done": true, "attach_artifact_id": null}, {"id": "g2", "text": "Extract birth date, death date and three key contributions to information theory from the fetched page", "done": false, "attach_artifact_id": "art:9f2c41aa"}]}
"""


def _clean_json(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    return text


def perceive(obs: Observe, provider: str = "o") -> Observation:
    """One Perception call. Raises RuntimeError on transport failure;
    ValueError on schema violation (loop feeds it back correctively)."""
    user_content = json.dumps({
        "query": obs.query,
        "hits": [h.model_dump(mode="json") for h in obs.hits],
        "history": [h.model_dump(mode="json") for h in obs.history],
        "prior_goals": [g.model_dump(mode="json") for g in obs.prior_goals],
        "run_id": obs.run_id,
    })
    resp = gateway.chat(
        messages=[{"role": "user", "content": user_content}],
        system=PERCEPTION_PROMPT,
        max_tokens=1200,
        temperature=0.2,
        response_format={"type": "json_object"},
        provider=provider,
    )
    text = _clean_json(resp.get("text") or "")
    try:
        return Observation.model_validate(json.loads(text))
    except Exception as e:
        # Single corrective retry with the raw text echoed back.
        resp2 = gateway.chat(
            messages=[
                {"role": "user", "content": user_content},
                {"role": "assistant", "content": text},
                {"role": "user", "content": (
                    f"Your reply did not match the Observation schema: {e}. "
                    "Reply with ONLY the JSON object.")},
            ],
            system=PERCEPTION_PROMPT,
            max_tokens=1200,
            temperature=0.2,
            response_format={"type": "json_object"},
            provider=provider,
        )
        text2 = _clean_json(resp2.get("text") or "")
        return Observation.model_validate(json.loads(text2))
