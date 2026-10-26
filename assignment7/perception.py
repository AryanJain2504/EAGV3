"""Perception: the agent's orchestrator.

Runs every loop iteration. Looks at the user's original query, the memory
hits, and the run history so far, and emits the current Observation —
which goals exist, which are done, and whether the next unfinished goal
needs raw bytes from a specific artifact.

Perception never reads artifact bytes. It sees handles + descriptors only.
When a goal needs bytes, Perception flips `send_artifact: true` and points
`artifact_index` at one of the artifacts listed in MEMORY HITS. The outer
loop resolves the index back to the artifact id and attaches the bytes.
"""

from __future__ import annotations

import json

from pydantic import BaseModel, Field, model_validator

from gateway import LLM, ensure_gateway
from schemas import Goal, MemoryItem, Observation, new_id


class _GoalDelta(BaseModel):
    """What the Perception LLM emits per goal. No `id` field — goals are
    identified by their position in the output list. The LLM cannot drift
    identity across iterations because there is no identity field to drift."""

    text: str = Field(min_length=1, max_length=240)
    done: bool = False
    # Routing fields are REQUIRED (no defaults). When they were optional,
    # the model emitted `send_artifact: true` but consistently omitted the
    # index fields, so no artifact ever reached Decision.
    send_artifact: bool
    artifact_index: int | None
    artifact_indices: list[int]

    @model_validator(mode="before")
    def _strip_text(cls, data):
        if isinstance(data, dict) and isinstance(data.get("text"), str):
            return {**data, "text": data["text"].strip()}
        return data


class _PerceptionOutput(BaseModel):
    goals: list[_GoalDelta] = Field(default_factory=list, max_length=10)


SYSTEM = (
    "You are the Perception layer of an agent.\n"
    "Each iteration you see the user's query, the prior goal list, the\n"
    "current memory hits (descriptors only — never raw bytes), and the run\n"
    "history. Return the CURRENT goal list as JSON matching the schema.\n\n"
    "Goals are identified by POSITION in the output array. Always return\n"
    "every PRIOR GOAL in the SAME ORDER and copy its text verbatim. Never\n"
    "drop, rewrite, or reorder a prior goal. Only after a completed\n"
    "directory listing reveals concrete work in an unknown set, append the\n"
    "new goals after all prior goals. Never insert them between prior goals.\n\n"
    "You speak at the level of INTENT, not tool selection. Write each goal\n"
    "as a short imperative describing WHAT must happen, not WHICH tool\n"
    "will do it. Decision is the layer that maps intent to a tool; leave\n"
    "that choice to Decision. Example intent verbs you may use: fetch,\n"
    "open, list, look up the time, convert currency, save a note, make\n"
    "this content searchable, query the existing knowledge base, extract,\n"
    "summarise, compare, synthesise. Do not name specific tools.\n\n"
    "Keep goals concise, but preserve every detail needed to carry them out\n"
    "or judge their result. In particular, retain requested quantities,\n"
    "names, locations, dates, times, and qualifying constraints from the\n"
    "user's query in the relevant goal. NEVER calculate or substitute a\n"
    "calendar date for relative phrases; copy the relative wording and the\n"
    "anchor date together into the goal text.\n\n"
    "DATE WORDING EXAMPLE\n"
    "User query: 'pay the invoice three days before 10 June 2026 and on the day'\n"
    "WRONG goals: ['Pay invoice on June 7, 2026', 'Pay invoice on June 10, 2026']\n"
    "RIGHT goals: ['Pay invoice three days before 10 June 2026',\n"
    "              'Pay invoice on 10 June 2026']\n"
    "The relative phrase AND the anchor date must both appear in the goal.\n"
    "Decision will resolve it with the date-computation capability when it acts.\n\n"
    "FETCH TARGET EXAMPLE\n"
    "User query: 'When is Ada's graduation?'\n"
    "WRONG goals: ['Fetch the page', 'Answer the question']\n"
    "RIGHT goals: ['Answer when Ada's graduation is']\n"
    "Every fetch/read goal must name its target: a URL, a ranked set\n"
    "('top 3 results'), or a quoted/file name. Never emit a bare\n"
    "'fetch the page' goal.\n\n"
    "Procedure:\n"
    "1. If PRIOR GOALS is empty, decompose the query into one or more short\n"
    "   imperative goals (one per distinct part). If the query asks to\n"
    "   read/fetch/process N items (\"top 3 results\", \"first 5 articles\"),\n"
    "   emit a SEPARATE fetch goal for each item plus the final\n"
    "   synthesis goal — NOT a single umbrella goal.\n"
    "   When the request asks for a number of examples or options from\n"
    "   one search (for example, 'find three things to do'), keep that as\n"
    "   ONE goal; do not create one goal per example. Split per item only\n"
    "   when the user names distinct items or separate named sources.\n"
    "   If the requested files/items are already named, emit one goal per\n"
    "   item and a final report goal. If the request covers an unknown set\n"
    "   that must first be discovered (for example, every matching file in\n"
    "   a directory), emit only the discovery goal initially. After the\n"
    "   discovery action, append one goal per discovered item, then append\n"
    "   any final report or answer goal.\n"
    "   If MEMORY HITS already contain `fact` items whose descriptors\n"
    "   start with `[sandbox:` or `[art:` (these mark previously-indexed\n"
    "   chunks of source documents), the next goal for any question\n"
    "   about that material is to QUERY THE EXISTING KNOWLEDGE BASE\n"
    "   rather than to re-fetch or re-open the original sources. Pair\n"
    "   that query goal with a final synthesis/answer goal — never emit\n"
    "   a knowledge-base query as the only goal, because the user still\n"
    "   needs an answer produced from the returned chunks.\n"
    "   If the query is an imperative that asks you to remember a fact or\n"
    "   create or update records, append a final goal to report what was\n"
    "   done, even though the query is not phrased as a question.\n"
    "   Whenever the user's query is a question (rather than a pure\n"
    "   action like 'save X' or 'fetch Y'), the LAST goal must be a\n"
    "   synthesis/answer goal that emits the final reply, except when an\n"
    "   unknown set must first be discovered; in that case defer this goal\n"
    "   until after the per-item goals are appended. Use verbs like answer,\n"
    "   tell, summarise, compare, list, extract, identify, describe.\n"
    "   If the query itself states a fact to remember or store, do NOT emit\n"
    "   a remember/store goal for it — the loop persists the query\n"
    "   automatically. Emit only goals for what must be done: actions,\n"
    "   lookups, and answers.\n"
    "   Combine analysis and the final response when they serve the same\n"
    "   requested conclusion; do not create separate goals to compare and\n"
    "   then repeat that same conclusion to the user.\n"
    "2. Otherwise copy each prior goal's `text` verbatim into the same slot.\n"
    "   Only after a completed directory listing reveals specific files or\n"
    "   items that still need processing, append one goal for each item,\n"
    "   then append any final report goal that was deferred until discovery.\n"
    "   Do not append duplicates of goals already present.\n"
    "   Mark `done: true` only when RUN HISTORY shows an action that\n"
    "   completes the goal's verb. A lookup or calculation does NOT satisfy\n"
    "   a create/write/update goal; only a successful write does. A fetch or\n"
    "   search does NOT satisfy an answer/extract/summarise/find/list/\n"
    "   identify/check/recommend goal; only the produced answer does.\n"
    "   A search result alone does not satisfy a goal that asks to check a\n"
    "   specific value such as weather, a price, or a time; if the snippet\n"
    "   does not contain the value, the goal still needs the result page.\n"
    "   A fetch that errored does NOT satisfy a fetch\n"
    "   goal: keep it not-done and reword that goal this reply to ask for an\n"
    "   alternative source from the results already listed in RUN HISTORY.\n"
    "   Once done, leave it done in every later iteration.\n"
    "   SATISFACTION EXAMPLES\n"
    "   Goal: Create a reminder two weeks before an event date.\n"
    "   History: a date calculation returned the target date. Keep done=false:\n"
    "   a computed date is not a created reminder.\n"
    "   Goal: Create a reminder two weeks before an event date.\n"
    "   History: a write attempt returned an error. Keep done=false: the\n"
    "   write did not succeed.\n"
    "   Goal: Fetch the second result.\n"
    "   History: the fetch returned HTTP 403. Keep done=false and reword to\n"
    "   fetch an alternative source from the results already listed.\n"
    "3. For the FIRST unfinished goal (lowest-index slot with done=false),\n"
    "   set `send_artifact: true` whenever ANY of these apply:\n"
    "     - the goal text contains extract / summarise / list / synthesise /\n"
    "       analyse / evaluate / select / compare / pick / choose / decide;\n"
    "     - the goal needs information that lives inside a fetched page or\n"
    "       file rather than just in the short descriptor.\n"
    "   Put the `i` values (0, 1, 2, ...) of ALL memory hits this goal needs\n"
    "   into `artifact_indices`. A goal that synthesises several fetched\n"
    "   pages needs every one of those page artifacts, not just the newest.\n"
    "   Entries whose `i` is null are not artifacts and cannot be picked.\n"
    "   When in doubt, include the artifacts whose descriptors match the\n"
    "   goal topic. `artifact_index`/`send_artifact` are the legacy\n"
    "   single-artifact fields; `artifact_indices` takes precedence.\n"
    "4. Only when the goal is purely fetch / search / compute / open / time\n"
    "   should you leave `send_artifact: false`, `artifact_index: null`, and\n"
    "   `artifact_indices: []`.\n"
    "5. DO NOT attach an artifact if the MEMORY HITS already contain indexed\n"
    "   chunks from the relevant document. If the hits show `fact` items with\n"
    "   a `chunk` field whose `source` matches the document the goal is about,\n"
    "   the document is already indexed — answer from those chunks instead.\n"
    "   Attaching the raw artifact would duplicate evidence the loop already\n"
    "   holds and would demand page-extraction from one arbitrary page when\n"
    "   the index already contains every passage.\n\n"
    "Example. Given\n"
    '  MEMORY HITS: [{"i":0,"artifact_id":"art:aaa","descriptor":'
    '"page fetch result -> art:aaa"}]\n'
    '  PRIOR GOALS: [{"text":"Fetch the page","done":false,'
    '"send_artifact":false,"artifact_index":null},\n'
    '                {"text":"Extract X","done":false,'
    '"send_artifact":false,"artifact_index":null}]\n'
    "return:\n"
    '  {"goals":[\n'
    '    {"text":"Fetch the page","done":true,'
    '"send_artifact":false,"artifact_index":null},\n'
    '    {"text":"Extract X","done":false,'
    '"send_artifact":true,"artifact_index":0,'
    '"artifact_indices":[0]}\n'
    "  ]}"
)


def _snapshot_history(history: list[dict]) -> list[dict]:
    out = []
    for h in history[-10:]:
        clipped = {}
        for k, v in h.items():
            if isinstance(v, str) and len(v) > 4000:
                clipped[k] = v[:4000] + "..."
            else:
                clipped[k] = v
        out.append(clipped)
    return out


def _snapshot_hits(hits: list[MemoryItem], run_id: str) -> list[dict]:
    """Render the memory hits the LLM sees. Artifacts are indexed (i) so
    Perception can point at them by integer; non-artifact hits show i=null.

    Only CURRENT-RUN artifacts get an index, matching
    `_current_run_artifact_handles`: stale handles from earlier runs stay
    visible for context but are not attachable, so numbering them would
    shift every valid index."""
    art_pos = 0
    out = []
    for h in hits[:12]:
        i = None
        if h.artifact_id and h.run_id == run_id:
            i = art_pos
            art_pos += 1
        out.append({
            "i": i,
            "kind": h.kind,
            "descriptor": h.descriptor,
            "keywords": h.keywords,
            "artifact_id": h.artifact_id,
        })
    return out


def _current_run_artifact_handles(
    hits: list[MemoryItem], run_id: str
) -> list[str]:
    """Artifact handles from the CURRENT run only.

    Memory hits are persistent and also carry previous
    runs' tool_outcome artifacts; pointing a goal at
    those would attach another run's bytes as evidence
    for this one."""
    return [
        h.artifact_id for h in hits[:12]
        if h.artifact_id and h.run_id == run_id
    ]


def observe(
    query: str,
    hits: list[MemoryItem],
    history: list[dict],
    prior_goals: list[Goal],
    run_id: str,
    provider: str = "g",
) -> Observation:
    ensure_gateway()

    art_ids_in_order = _current_run_artifact_handles(hits, run_id)

    prior_snapshot = [g.model_dump() for g in prior_goals] if prior_goals else []
    prompt = (
        f"USER QUERY:\n  {query}\n\n"
        f"PRIOR GOALS:\n{json.dumps(prior_snapshot, indent=2)}\n\n"
        f"MEMORY HITS (handles + descriptors only, no raw bytes; `i` is the\n"
        f"artifact_index to pass back when send_artifact is true):\n"
        f"{json.dumps(_snapshot_hits(hits, run_id), indent=2)}\n\n"
        f"RUN HISTORY (last 10 events):\n"
        f"{json.dumps(_snapshot_history(history), indent=2, default=str)}\n\n"
        f"Return the current goal list as JSON matching the schema."
    )

    schema = _PerceptionOutput.model_json_schema()

    def _call(prompt_text: str) -> dict:
        reply = LLM().chat(
            prompt=prompt_text,
            system=SYSTEM,
            auto_route="perception",
            provider=provider,
            response_format={
                "type": "json_schema",
                "schema": schema,
                "name": "PerceptionOutput",
                "strict": True,
            },
            temperature=0,
            max_tokens=8192,
        )
        print(f"[perception]      via {reply.get('provider', '?')}/{reply.get('model', '?')}")
        return reply

    reply = _call(prompt)

    parsed = reply.get("parsed")
    if not parsed or not parsed.get("goals"):
        # One corrective retry before collapsing to the query itself.
        parsed = _call(
            prompt + "\n\nYour previous reply contained no goals. "
            "Reply with ONLY the JSON object.",
        ).get("parsed")
    if not parsed or not parsed.get("goals"):
        print(f"[perception] proposed goals: {query}")
        return Observation(goals=[Goal(id=new_id("g"), text=query)])

    # Goal-count invariant: never contract, never reorder. Prior goals keep
    # their slot and id; Perception may APPEND new goals after the prior
    # list when a discovery action (e.g. list_dir) reveals work that wasn't
    # knowable on iter 1. NOTES_RUNS §6 (4): the previous hard-truncate to
    # `len(prior_goals)` blocked F-run-1 verbatim — list_dir revealed five
    # papers, but the goal list was locked to the three placeholders emitted
    # before the listing was known. We still drop appended goals whose text
    # duplicates a prior goal (the temp=1.0 dup-append failure mode that
    # motivated the original lock).
    raw_goals = parsed["goals"]
    print("[perception] proposed goals: "
          + " | ".join(str(g.get("text")) for g in raw_goals))
    if prior_goals:
        prior_texts = {g.text.strip().lower() for g in prior_goals}
        deduped = list(raw_goals[:len(prior_goals)])
        for extra in raw_goals[len(prior_goals):]:
            t = (extra.get("text") or "").strip().lower()
            if not t or t in prior_texts:
                if t:
                    print(f"[perception] dropped duplicate appended goal: {extra.get('text')}")
                continue
            prior_texts.add(t)
            deduped.append(extra)
        raw_goals = deduped

    try:
        deltas = [_GoalDelta.model_validate(d) for d in raw_goals]
    except Exception as exc:
        # One corrective retry; then keep whatever validates.
        retry_parsed = _call(
            prompt + f"\n\nYour previous reply was not valid ({exc}). "
            "Reply with ONLY the JSON object.",
        ).get("parsed") or {}
        deltas = []
        for d in retry_parsed.get("goals") or []:
            try:
                deltas.append(_GoalDelta.model_validate(d))
            except Exception:
                continue
        if not deltas:
            return Observation(goals=[Goal(id=new_id("g"), text=query)])

    print("[perception] routing: "
          + " | ".join(
              f"{'SEND' if (d.send_artifact or d.artifact_indices) else '----'}"
              f" send={d.send_artifact} idx={d.artifact_index} "
              f"idxs={d.artifact_indices}"
              for d in deltas
          ))
    out_goals: list[Goal] = []
    for i, delta in enumerate(deltas):
        attach_ids: list[str] = []
        raw_indices = delta.artifact_indices or []
        if not raw_indices and delta.send_artifact and delta.artifact_index is not None:
            raw_indices = [delta.artifact_index]
        for idx in raw_indices:
            if 0 <= idx < len(art_ids_in_order):
                art_id = art_ids_in_order[idx]
                if art_id not in attach_ids:
                    attach_ids.append(art_id)

        gid = prior_goals[i].id if i < len(prior_goals) else new_id("g")
        was_done = prior_goals[i].done if i < len(prior_goals) else False

        # Perception owns goal state, so its `done` stands. This layer is the
        # only one that knows what the user asked for and therefore the only
        # one entitled to say a goal is satisfied. `done` still only moves
        # forward, which keeps a completion from being reopened by a later
        # reword.
        out_goals.append(Goal(
            id=gid,
            text=delta.text,
            done=was_done or delta.done,
            attach_artifact_id=attach_ids[0] if attach_ids else None,
            attach_artifact_ids=attach_ids,
        ))

    return Observation(goals=out_goals)
