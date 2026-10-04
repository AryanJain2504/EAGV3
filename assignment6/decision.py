"""Decision role: atomic worker. Cheap models OK (auto_route="decision").

Receives exactly ONE goal plus its context and returns EITHER a final
answer string OR exactly one tool call — never both, never more than one.
It is fire-and-forget: it never sees its own tool result. Verification
happens in Perception on the next iteration.
"""

import json

import gateway
from schemas import DecisionIn, DecisionOutput

DECISION_PROMPT = """You are Decision, an atomic worker in a multi-role agent loop.
You receive ONE goal and its context. You return EXACTLY ONE JSON object:
{"answer": str|null, "tool_call": {"name": str, "arguments": object}|null}.
Set answer OR tool_call, never both, never neither. Never emit more than one tool call.

Rules:
- If the goal is directly answerable from the given hits, attachment, or history,
  put the complete answer in `answer` and null the tool_call.
- For a direct memory lookup such as "When is Ada's graduation?", return the
  stored fact value in a natural-language answer. Do not describe the memory
  operation and do not say "fact saved" when the user asked for the fact.
- For factual extraction, use the attachment as the source of truth: copy dates,
  names, numbers, and quoted facts exactly from it. Do not substitute remembered
    facts, and ignore conflicting memory hits when an attachment is present. If the attachment does not contain the requested fact, use one tool call
  to obtain a better source instead of guessing.
- Otherwise emit ONE tool_call with the exact tool name and arguments from the
  provided tool list. Do not invent tools. Do not chain calls.
- When the goal asks you to create, set, save, schedule, send, or book
  something, perform the requested action with an available tool. Do not
  claim it was done based on an answer; an action goal needs a tool result.
- To open, read, or browse any URL use ONLY `fetch_url({"url": "..."})`. There is
  no tool named `open_url`, `browse`, `open`, or `visit`. Using those will fail.
- If an attachment (artifact bytes) is provided, read it and extract ONLY what
  the goal asks; return it as `answer` (small) — do not echo the whole attachment.
- If the attachment contains labeled SOURCE sections for a multi-source
  synthesis, use only those sources and do not fetch any unrelated URL. Include
  an advice item only when it is supported by at least two source sections. Do
  not introduce examples or terminology absent from the sources. Return the
  synthesis as a short numbered list, not as a paragraph.
- If the attachment or hits are insufficient, call the single most informative
  next tool (e.g. fetch_url for a page, web_search for candidates).
- Once history contains a successful `web_search` for the current query, never
  repeat that same search. Use its returned URLs and fetch one URL that has not
  already appeared in a successful `fetch_url` action.
- For a goal requesting the top N results or multiple URLs, do not answer until
  history contains successful fetches for all N sources. Fetch one unvisited
  result per decision step; the loop will verify completion.
- When a goal asks which option is most appropriate or best, always select from
  the options already identified in the history or hits. Never introduce or invent
  a new option that was not previously listed. Name the chosen option explicitly.
- Linguistic tasks — translation, summarization, reformatting, rewriting, bullet
  extraction — NEVER require a tool call. If the content to operate on is already
  in the history or prior decision answers, perform the task directly and return
  the result as `answer`. Do NOT web_search for translation help.
- If the exact same tool call (same name + same arguments) already appears in the
  history and did not produce an artifact or useful result, do NOT repeat it.
  Try a different tool or answer from the context you have, even if imperfect.
- Output ONLY the JSON object, no prose, no markdown fences.

FEW-SHOT 1 (tool call):
DecisionIn: goal="Fetch the contributor guide for Acme Widgets via fetch_url", hits=[], attachment=null
Output: {"answer": null, "tool_call": {"name": "fetch_url", "arguments": {"url": "https://example.com/acme-widgets/contributing"}}}

FEW-SHOT 2 (extract from attachment):
DecisionIn: goal="Extract birth date, death date and three key contributions from the fetched page", attachment="<41KB markdown, artifact #1>"
Output: {"answer": "Born 4 March 1901, died 22 November 1975. Key contributions: lattice resonance theory; the harmonic probe method; low-temperature alloy tables.", "tool_call": null}
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


def decide(d: DecisionIn, provider: str | None = None) -> DecisionOutput:
    """One Decision call. Raises RuntimeError on transport failure;
    ValueError on schema violation (loop feeds it back correctively).
    provider=None keeps gateway auto_route="decision"; set it to force a worker."""
    tools = [{"name": t.name, "description": t.description, "input_schema": t.input_schema}
             for t in d.tools]
    user_content = json.dumps({
        "goal": d.goal.model_dump(mode="json"),
        "hits": [h.model_dump(mode="json") for h in d.hits],
        "attachment": d.attachment,
        "history_slice": [h.model_dump(mode="json") for h in d.history_slice],
        "tools": tools,
    })
    route = {"provider": provider} if provider else {"auto_route": "decision"}
    resp = gateway.chat(
        messages=[{"role": "user", "content": user_content}],
        system=DECISION_PROMPT,
        max_tokens=1500,
        temperature=0.2,
        response_format={"type": "json_object"},
        **route,
    )
    text = _clean_json(resp.get("text") or "")
    try:
        out = DecisionOutput.model_validate(json.loads(text))
    except Exception as e:
        resp2 = gateway.chat(
            messages=[
                {"role": "user", "content": user_content},
                {"role": "assistant", "content": text},
                {"role": "user", "content": (
                    f"Your reply did not match the DecisionOutput schema: {e}. "
                    "Reply with ONLY the JSON object.")},
            ],
            system=DECISION_PROMPT,
            max_tokens=1500,
            temperature=0.2,
            response_format={"type": "json_object"},
            **route,
        )
        text2 = _clean_json(resp2.get("text") or "")
        out = DecisionOutput.model_validate(json.loads(text2))
    if (out.answer is None) == (out.tool_call is None):
        raise ValueError("Decision must set exactly one of answer/tool_call")
    return out
