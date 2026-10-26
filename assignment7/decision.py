"""Decision: the only layer that chooses a tool, and the only layer that answers.

One LLM call per turn. It receives the current goal, the original request for
constraint context, permitted memory hits, recent history, any attached
artifact bytes, and the full MCP tool catalogue. It returns exactly one of two
things: a plain-text answer, or one tool call.

There is no contract layer here. An earlier version of this file classified
each goal into one of three output modes ("structured", "tools", "plain"),
forced a JSON sub-schema on some of them, and then validated the model's
citations against the ids it had been shown. That machinery cost roughly 300
lines and produced a worse failure mode than it removed: a goal the
classifier filed under the wrong mode got its tools withheld, so Decision
could not retrieve, and the run stalled with a self-describing non-answer
instead of a trace showing what actually went wrong.

The rules below therefore live in this prompt, where the model can read them
alongside the evidence, rather than in a Python branch that decides for it.
When a rule fails, the trace says which rule was ignored — not which branch
fired.
"""

from __future__ import annotations

import json
import re

from gateway import LLM, ensure_gateway
from schemas import DecisionOutput, Goal, MemoryItem, ToolCall

SYSTEM = (
    "You are the Decision layer of an agent.\n"
    "Inputs: ONE current goal, the original user request for constraint "
    "context, permitted memory snippets, recent history, optionally attached "
    "artifact bytes, and the full catalogue of available tools.\n"
    "Reply with exactly ONE of: a plain-text answer, or one tool call. Never "
    "both, and never narrate what you are about to do.\n\n"

    "FOLLOW THE ORIGINAL REQUEST\n"
    "The original user request is authoritative for names, dates, quantities, "
    "and relative timing. If the goal paraphrase conflicts with it, follow the "
    "original request. Never do calendar arithmetic in your head — resolve a "
    "relative phrase ('one week before 20 June') with the date-computation "
    "tool first, then act on the returned date.\n\n"

    "NEVER GUESS THE PRESENT DATE\n"
    "When the request or goal refers to today, tomorrow, this weekend, or a "
    "weekday, call the time tool for the present date first — and the "
    "date-computation tool when a weekday or offset must be resolved — then "
    "put that exact date into every search, fetch, or filename. Never search "
    "for or write a date that no tool returned.\n\n"

    "ANSWER FROM THE EVIDENCE YOU WERE GIVEN\n"
    "Use only the memory evidence supplied for the current goal. Do not fill "
    "gaps from general knowledge. If the supplied evidence does not answer the "
    "goal, either call the appropriate tool to obtain it, or say plainly that "
    "the available material does not contain the answer.\n"
    "If MEMORY EVIDENCE contains the answer, answer from it and nothing else. "
    "Do not add background, impact, reception, or legacy. Do not mention "
    "models, benchmarks, or applications that are not in the evidence. If the "
    "evidence is insufficient, say so — do not supplement from training data.\n"
    "A question may name an idea the sources never use verbatim. Do not "
    "refuse merely because the exact term is missing. Answer conceptually: "
    "for each relevant source, name the mechanism it describes and explain "
    "how that mechanism addresses the concern behind the term, citing the "
    "chunk. Refuse only when no retrieved passage bears on the question.\n"
    "When you answer from indexed chunks, your answer MUST contain at least "
    "one chunk id (mem:...) exactly as it appears in MEMORY EVIDENCE, naming "
    "the chunk that supports the claim. An answer about indexed material with "
    "no chunk id is not an answer — say plainly that the available material "
    "does not contain it instead. The `source` shown "
    "beside each chunk tells you which document it came from; it is provenance "
    "for the reader, not something to cite in place of the chunk that actually "
    "supports the claim. Answer only the fields or claims that were asked for "
    "— no biography, career context, awards, or other unsolicited background.\n\n"

    "RETRIEVE FROM THE KNOWLEDGE BASE; DO NOT RE-OPEN AN INDEXED DOCUMENT\n"
    "A document that has been indexed is already searchable, and its citable "
    "form is its chunks. To answer a question about indexed material, query "
    "the knowledge base rather than re-fetching or re-reading the original "
    "file: re-opening it would hand you one arbitrary page when the index "
    "already holds every passage. Index a document only when it has not been "
    "indexed yet.\n\n"

    "ACT, DO NOT NARRATE\n"
    "When the goal asks you to create, set, save, write, update, edit, "
    "schedule, send, or book something, perform the action with a tool. Never "
    "report an action as done based on prose; the loop requires a real "
    "successful tool result before a goal like that can close.\n"
    "Satisfy only the current goal with each call — do not fold another goal's "
    "work into the same call.\n\n"

    "ARTIFACT HANDLES ARE NOT PATHS OR URLs\n"
    "An artifact handle (art:...) names bytes that are already supplied under "
    "ATTACHED ARTIFACTS. Never pass one as a `path` or `url` argument. If you "
    "need a page that is not attached, fetch it by its real URL.\n\n"

    "DO NOT REPEAT A CALL THAT ALREADY FAILED\n"
    "If the same tool with the same arguments already appears in RECENT "
    "HISTORY and failed, do not issue it again. Change the arguments, use a "
    "different tool, or answer from the context you already have.\n\n"

    "KEEP A SEARCH RESULT IN RESERVE\n"
    "When the request asks for the top N web results, ask the search tool\n"
    "for N+2 (hard cap 5) so a blocked page can be replaced. If a fetch has\n"
    "failed and the goal asks for an alternative source, fetch the first\n"
    "result in the latest search output that no earlier action has used;\n"
    "never run another search while unused results remain.\n\n"

    "STATE-CHANGE EXAMPLE\n"
    "Goal: Schedule a checkup one week before 20 June 2026.\n"
    "Turn 1: call the date-computation tool with anchor='20 June 2026' and "
    "offset='1 week before' — it returns 13 June 2026.\n"
    "Turn 2: call the file-creation tool with path='checkup-2026-06-13.md' "
    "and content='Checkup one week before the 20 June 2026 appointment. "
    "Fire on 13 June 2026.'\n"
    "Wrong: 'The checkup has been scheduled.' Prose is not an action.\n\n"

    "EXTRACTED-FACT EXAMPLE\n"
    "Goal: Extract the birth date, death date, and three key contributions "
    "from the attached page.\n"
    "Answer: Born 4 March 1901; died 22 November 1975. Three contributions: "
    "lattice resonance theory; the harmonic probe method; low-temperature "
    "alloy tables."
)

ATTACH_HEAD = 18_000
ATTACH_TAIL = 6_000

# A memory id (`mem:` + token) as it appears inside a recorded tool result.
_MEM_ID_RE = re.compile(r"\bmem:[0-9A-Za-z]+\b")


HIT_VALUE_CHARS = 800
HIT_CHUNK_CHARS = 5_000


def _format_hits(hits: list[MemoryItem]) -> str:
    """Render memory hits for Decision, including the stored value.

    Each line carries the record id (the citation target), the kind, the
    one-line descriptor, and the source as provenance. Fact values are
    rendered too, bounded so ten hits cannot crowd out the rest of the
    prompt. The Session 7 diagnostic showed both failures this prevents:
    C lost the birthday because the date lived in `value` and only the
    descriptor was rendered; G looped on `search_knowledge` because the
    passage lived in `value.chunk` and was dropped here. Both were
    rendering bugs at this boundary, not prompt bugs.
    """
    evidence = [h for h in hits if h.kind != "tool_outcome"]
    if not evidence:
        return "  (none)"
    out = []
    for h in evidence[:10]:
        line = f"  - [{h.kind}] {h.id} — {h.descriptor}"
        if h.artifact_id:
            line += f" [artifact: {h.artifact_id}]"
        value = h.value or {}
        source = value.get("source") or h.source
        if source:
            line += f" (source: {source})"
        out.append(line)
        if h.kind not in {"fact", "preference"}:
            continue
        # Indexed chunks are the one payload whose informative part is often
        # past the first few hundred characters (the Abstract page starts
        # after its navigation). Keep the whole chunk, bounded at 5k chars.
        chunk = value.get("chunk")
        if chunk:
            out.append(f"    content: {str(chunk)[:HIT_CHUNK_CHARS]}")
            continue
        detail = value.get("raw")
        if detail:
            out.append(f"    content: {str(detail)[:HIT_VALUE_CHARS]}")
        elif value:
            rendered = json.dumps(value, ensure_ascii=False, default=str)
            out.append(f"    value: {rendered[:HIT_VALUE_CHARS]}")
    return "\n".join(out)


def _format_history(history: list[dict]) -> str:
    """Render run history for Decision.

    Action lines carry the ids recorded for them. Without those handles an
    answer could not name the evidence it used, because the model could read
    that a write happened but had no id to refer to. Rendering them is a
    rendering fix — the ids were always recorded, they were simply not shown.
    """
    if not history:
        return "  (empty)"
    lines = []
    for h in history[-6:]:
        if h.get("kind") == "answer":
            lines.append(f"  - answer: {(h.get('text') or '')[:300]}")
        elif h.get("kind") == "action":
            ids = []
            if h.get("artifact_id"):
                ids.append(str(h["artifact_id"]))
            ids.extend(_MEM_ID_RE.findall(str(h.get("result_descriptor") or "")))
            id_str = f" [{' '.join(dict.fromkeys(ids))}]" if ids else ""
            lines.append(
                f"  - {h.get('tool')}{id_str}: "
                f"{(h.get('result_descriptor') or '')[:4000]}"
            )
    return "\n".join(lines)


def _decode_artifact(data: bytes) -> str:
    text = data.decode("utf-8", errors="replace")
    try:
        envelope = json.loads(text)
    except json.JSONDecodeError:
        return text
    if isinstance(envelope, dict) and isinstance(envelope.get("text"), str):
        return envelope["text"]
    return text


_HIT_CHARS = 4_000


def _render_search_hits(text: str) -> str | None:
    """Render a search-result artifact as citable evidence blocks.

    `search_knowledge` returns concatenated hit objects, one per line group:
    each carries id, descriptor, source, and chunk. Raw JSON buries the chunk
    text under punctuation and truncated arXiv boilerplate, and the model
    then reports that the evidence has no relevant passage. Rendering the
    same bytes as `mem:`-labelled blocks fixes the boundary: the ids stay
    the citation handles and the prose becomes readable.
    """
    decoder = json.JSONDecoder()
    objs: list = []
    i = 0
    while i < len(text):
        while i < len(text) and text[i].isspace():
            i += 1
        if i >= len(text):
            break
        try:
            obj, end = decoder.raw_decode(text, i)
        except json.JSONDecodeError:
            return None
        objs.append(obj)
        i = end
    hits = [o for o in objs
            if isinstance(o, dict) and o.get("id") and "chunk" in o]
    if not hits:
        return None
    lines = ["SEARCH RESULTS:"]
    for h in hits:
        lines.append(
            f"- {h['id']} — {str(h.get('descriptor') or '')[:200]} "
            f"(source: {h.get('source') or '?'})"
        )
        lines.append(f"  content: {str(h['chunk'])[:_HIT_CHARS]}")
    return "\n".join(lines)


def _format_attached(attached: list[tuple[str, bytes]]) -> str:
    if not attached:
        return ""
    parts = ["\n\nATTACHED ARTIFACTS:"]
    for art_id, data in attached:
        text = _decode_artifact(data)
        rendered = _render_search_hits(text)
        if rendered is None:
            if len(text) > ATTACH_HEAD + ATTACH_TAIL:
                # Attachment size is the one input that silently decides
                # whether extraction can succeed, so make the truncation
                # visible.
                text = (
                    text[:ATTACH_HEAD]
                    + "\n\n[excerpt truncated; full artifact persisted on "
                    "disk]\n\n"
                    + text[-ATTACH_TAIL:]
                )
        else:
            text = rendered
        parts.append(f"--- {art_id} ---\n{text}")
    return "\n".join(parts)


def _parse_tool_call_text(text: str, mcp_tools: list[dict]) -> ToolCall | None:
    """Honour an evident tool intent that arrived as prose.

    When a model writes an invocation as text (`fetch_url(url="...")`) rather
    than emitting a structured tool call, the intent is unambiguous. This
    converts it — strictly. It requires a full-string match, a tool that is
    actually in the catalogue, literal-only arguments, and arguments that
    validate against that tool's schema. Anything else returns None and the
    text stays an answer. It never guesses an argument and never invents a
    tool name.
    """
    import ast

    if not text:
        return None
    match = re.match(r"^\s*([A-Za-z_]\w*)\s*\((.*)\)\s*$", text, re.DOTALL)
    if not match:
        return None
    name, arg_str = match.group(1), match.group(2).strip()
    schemas = {t["name"]: t.get("input_schema") or {} for t in mcp_tools or []}
    if name not in schemas:
        return None
    try:
        call = ast.parse(f"f({arg_str})", mode="eval").body
        if not isinstance(call, ast.Call) or call.args:
            return None
        arguments: dict = {}
        for kw in call.keywords:
            if kw.arg is None:
                return None
            value = ast.literal_eval(kw.value)
            if not isinstance(value, (str, int, float, bool, type(None))):
                return None
            arguments[kw.arg] = value
    except (SyntaxError, ValueError):
        return None
    try:
        import jsonschema

        jsonschema.validate(arguments, schemas[name])
    except Exception:
        return None
    print(f"[repair]        interpreted prose as {name}({json.dumps(arguments)[:80]})")
    return ToolCall(name=name, arguments=arguments)


_REFUSAL = re.compile(
    r"(does not (contain|detail|list|include|provide)|"
    r"do not (contain|detail|list|include|provide)|only contains|"
    r"no information|not contain|material lacks|not enough information|"
    r"unable to find|cannot find)",
    re.I,
)


def _has_chunk_evidence(attached: list[tuple[str, bytes]]) -> bool:
    return any(b'"chunk"' in data for _, data in attached)


def next_step(
    goal: Goal,
    hits: list[MemoryItem],
    attached: list[tuple[str, bytes]],
    history: list[dict],
    mcp_tools: list[dict],
    request_context: str = "",
) -> DecisionOutput:
    """Return Decision's single reply for this turn.

    The full catalogue is always supplied. Withholding tools from a goal the
    orchestrator judged to be a "final synthesis" would be a pre-emptive tool
    decision made outside Decision, which is the one thing this architecture
    forbids. If a tool goes unused on a turn, the trace shows a wasted turn —
    which is honest — rather than hiding the choice behind a guardrail.
    """
    ensure_gateway()
    evidence_prefix = ""
    if attached:
        evidence_prefix = (
            "MANDATORY: Answer the current goal from the attached evidence. "
            "NEVER write that the material lacks the answer when the goal asks "
            "how, why, or to compare. If the exact term is absent, treat it as "
            "a lens and answer with the mechanisms the evidence describes, "
            "naming that term first.\n\n"
        )
    prompt = evidence_prefix + (
        f"ORIGINAL USER REQUEST (context for entities and constraints; act only "
        f"on the current goal):\n{request_context or '(not provided)'}\n\n"
        f"CURRENT GOAL:\n{goal.text}\n\n"
        f"For a comparison between named approaches, do not use lens "
        f"framing: compare their mechanisms directly, state each source's "
        f"position, and state the difference explicitly. If the evidence "
        f"covers only one side of the comparison, search for the missing "
        f"side before answering.\n"
        f"If the request asks for a specific number of items, list that many "
        f"distinct items before making any requested choice.\n"
        f"Never reply that the material lacks the answer when the question "
        f"asks how or why. Treat the central term as a conceptual lens: "
        f"begin by naming that term, then for each relevant source name the "
        f"mechanism it describes and explain how that mechanism addresses "
        f"the concern behind the lens, citing the chunk. Once a search has "
        f"returned chunks for this goal, do not search again; answer now "
        f"from those chunks.\n\n"
        f"MEMORY EVIDENCE:\n{_format_hits(hits)}\n\n"
        f"RECENT HISTORY:\n{_format_history(history)}"
        f"{_format_attached(attached)}"
    )
    reply = LLM().chat(
        prompt=prompt,
        system=SYSTEM,
        cache_system=True,
        tools=mcp_tools or None,
        tool_choice="auto" if mcp_tools else None,
        auto_route="decision",
        provider="g",
        temperature=0,
        max_tokens=1500,
    )
    print(f"[decision]      via {reply.get('provider', '?')}/{reply.get('model', '?')}")

    tool_calls = reply.get("tool_calls") or []
    if tool_calls:
        tc = tool_calls[0]
        return DecisionOutput(tool_call=ToolCall(
            name=tc["name"], arguments=tc.get("arguments") or {}
        ))

    text = (reply.get("text") or "").strip()
    if (
        text
        and len(text) < 600
        and _REFUSAL.search(text)
        and _has_chunk_evidence(attached)
    ):
        print("[decision]      refusal despite chunk evidence; retrying answer")
        retry = LLM().chat(
            prompt=(
                "Your previous reply was a refusal, which is forbidden for "
                "this goal. Answer now in prose from the attached evidence. "
                "Do not use tools. If the goal asks for a number of items, "
                "list that many items. Cite the chunk for every claim.\n\n"
                + prompt
            ),
            system=SYSTEM,
            cache_system=True,
            auto_route="decision",
            provider="g",
            temperature=0,
            max_tokens=1500,
        )
        retry_text = (retry.get("text") or "").strip()
        if retry_text:
            text = retry_text
    repaired = _parse_tool_call_text(text, mcp_tools or [])
    if repaired is not None:
        return DecisionOutput(tool_call=repaired)
    return DecisionOutput(answer=text)