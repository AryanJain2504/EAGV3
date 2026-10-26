"""Architecture and correctness tests for Assignment 7.

All deterministic: no LLM, gateway, or network calls.
Run: uv run pytest test_agent_contracts.py -q

This file was rewritten when the contract layer was removed. It used to carry
roughly fifty tests for the evidence-mode ladder, the citation gate, the
reconciliation pass, and the boundary sentinels — all of which were
compensation for problems fixed one layer away. What remains gates the
architecture itself:

  * the loop stays a loop, and the orchestrator never decides anything
  * Perception stays tool-blind and owns goal state
  * Decision always receives the full catalogue and answers from evidence
  * the ingest/search boundary holds: a query is never knowledge
  * a run reports honestly what it produced
"""

from __future__ import annotations

import inspect
import re

import pytest
from pydantic import ValidationError

import action
import agent7
import decision
import perception
import validate_prompts
from decision import _parse_tool_call_text
from mcp_server import _resolve_date, _split_relative_expression
from schemas import DecisionOutput, Goal, HistoryEvent, ToolCall


@pytest.fixture(autouse=True)
def _isolated_memory_state(tmp_path, monkeypatch):
    """Keep every test out of the real runtime state directory."""
    import memory

    monkeypatch.setattr(memory, "STATE_PATH", tmp_path / "memory.json")


# ── helpers ────────────────────────────────────────────────────────────

def _action(goal_id: str, tool: str, descriptor: str = "ok", **extra) -> dict:
    return {
        "kind": "action", "goal_id": goal_id, "tool": tool,
        "result_descriptor": descriptor, **extra,
    }


def _answer(goal_id: str, text: str) -> dict:
    return {"kind": "answer", "goal_id": goal_id, "text": text}


def _chunk_hit(hit_id: str = "mem:abc12345", source: str = "sandbox:papers/a.md"):
    from schemas import MemoryItem

    return MemoryItem(
        id=hit_id, kind="fact", keywords=[], descriptor=f"[{source} chunk 1/4] ...",
        value={"chunk": "body text", "source": source},
        source=source, run_id="older-run",
    )


# ── the loop is a loop ─────────────────────────────────────────────────

def test_orchestrator_never_constructs_a_tool_call():
    """Only Decision may choose a tool.

    The orchestrator reading `out.tool_call` is fine; building one is not. A
    tool call fabricated here would be a pre-emptive tool decision wearing the
    costume of a loop step, which is the single thing the architecture
    forbids.
    """
    source = inspect.getsource(agent7)
    assert "ToolCall(" not in source
    assert "decision.DecisionOutput(" not in source
    # It dispatches exactly what Decision returned.
    assert "action.execute(session, tc" in source


def test_loop_runs_the_five_layers_in_order():
    """The loop is unchanged from Session 6: read, observe, decide, execute,
    record. Every one of those five is present, in that order."""
    source = inspect.getsource(agent7.run)
    positions = [
        source.index("# 1. MEMORY READ"),
        source.index("# 2. PERCEPTION"),
        source.index("# 3. DECISION"),
        source.index("# 4. ACTION"),
        source.index("# 5. MEMORY WRITE"),
    ]
    assert positions == sorted(positions)


def test_orchestrator_holds_no_goal_completion_logic():
    """Perception owns goal state.

    The orchestrator reads `obs.all_done` and `obs.next_unfinished`; it must
    not compute either. An earlier version re-derived completion from recorded
    evidence and overrode Perception's flags, which made goal closure a second,
    parallel control flow living outside the layer that knows what was asked.
    """
    source = inspect.getsource(agent7)
    for forbidden in (
        "_goal_evidence", "evidence_mode", "_reconcile",
        "evidence_refs", "_valid_evidence", "_rendered_evidence",
    ):
        assert forbidden not in source, f"agent7 still references {forbidden}"


def test_orchestrator_does_not_withhold_the_tool_catalogue():
    """Decision always gets the full catalogue. The orchestrator has no branch
    that hands Decision a subset based on its own reading of the goal."""
    call = re.search(
        r"decision\.next_step\((.*?)\)\n", inspect.getsource(agent7.run),
        re.DOTALL,
    )
    assert call, "agent7.run must call decision.next_step"
    arguments = call.group(1)
    assert "tools_for_decision" in arguments
    # No slicing or filtering of the catalogue between load and dispatch.
    assert "tools_for_decision[" not in arguments
    assert "if tools_for_decision" not in arguments


def test_role_pins_match_the_reference_and_stay_out_of_the_cli():
    """Both cognitive roles are pinned to Gemini; routing is not a CLI knob.

    The Session 7 reference pins Perception to `provider="g"` (small workers
    hallucinate attachment ids, drop goals, and drift identity) and passes
    `auto_route="decision"` together with `provider="g"` for Decision. Our
    earlier test forbade the Decision pin on the strength of the Session 6
    write-up; the Session 7 reference code shows otherwise, so this now
    asserts the pins and their placement.

    What must not come back is a per-run flag. Pins belong to the role, not
    to an invocation: a CLI flag lets one trace be produced under a different
    worker from another, which is how a run set stops being comparable.
    """
    decision_source = inspect.getsource(decision.next_step)
    assert 'provider="g"' in decision_source
    assert 'auto_route="decision"' in decision_source
    # Perception's pin is the signature default plus pass-through into the call.
    assert inspect.signature(perception.observe).parameters[
        "provider"
    ].default == "g"
    assert "provider=provider" in inspect.getsource(perception.observe)

    run_params = inspect.signature(agent7.run).parameters
    assert "decision_provider" not in run_params
    main_source = inspect.getsource(agent7.main)
    assert "--decision-provider" not in main_source


def test_both_roles_report_the_worker_that_served_them():
    """Every trace names the worker that produced it.

    Four E runs failed for want of this: the answer was ungrounded and there
    was no way to tell a capable worker from a weak one, so each diagnosis
    ended in inference. Both role calls now print the gateway's returned
    provider and model.
    """
    assert "reply.get('provider'" in inspect.getsource(decision.next_step)
    perception_source = inspect.getsource(perception.observe)
    assert "reply.get('provider'" in perception_source


# ── schemas carry no invented control state ────────────────────────────

def test_goal_has_no_evidence_mode():
    """`evidence_mode` was derived from whatever happened to be in memory and
    then used to decide which tools Decision was allowed to see. Removing it
    removes the orchestrator's only opinion about the goal."""
    assert set(Goal(id="g:x", text="t").model_dump()) == {
        "id", "text", "done", "attach_artifact_id", "attach_artifact_ids",
    }
    with pytest.raises(ValidationError):
        Goal(id="g:x", text="t", evidence_mode="corpus")


def test_decision_output_carries_no_evidence_metadata():
    """The reply is the evidence contract.

    `evidence_refs`/`complete` created a second notion of groundedness that had
    to be reconciled against the prose on every turn, and its validator had to
    special-case empty refs to stop rejecting every plain answer.
    """
    assert set(DecisionOutput(answer="hi").model_dump()) == {"answer", "tool_call"}
    with pytest.raises(ValidationError):
        DecisionOutput(answer="hi", evidence_refs=["mem:1"])


def test_decision_output_requires_exactly_one_result():
    with pytest.raises(ValidationError):
        DecisionOutput()
    with pytest.raises(ValidationError):
        DecisionOutput(answer="hi", tool_call=ToolCall(name="get_time"))
    assert DecisionOutput(tool_call=ToolCall(name="get_time")).is_answer is False


def test_history_event_shape_is_validated():
    """An action event must name its tool; an answer event must not."""
    assert HistoryEvent(iter=1, kind="answer", goal_id="g:1", text="x").tool is None
    with pytest.raises(ValidationError):
        HistoryEvent(iter=1, kind="action", goal_id="g:1")
    with pytest.raises(ValidationError):
        HistoryEvent(
            iter=1, kind="answer", goal_id="g:1", text="x", tool="get_time",
        )


# ── Decision: prompt, catalogue, rendering ─────────────────────────────

def test_decision_prompt_carries_its_obligations():
    assert all(validate_prompts.check_decision(decision.SYSTEM).values())


def test_perception_prompt_is_tool_blind():
    """The gate: zero MCP tool names in Perception's prompt.

    This is the assignment's own grep test, and it is load-bearing rather than
    cosmetic. The moment a tool name appears in Perception's prompt, the model
    learns it and starts steering Decision toward that tool, which collapses
    the separation of concerns the whole design rests on.
    """
    assert all(validate_prompts.check_perception(perception.SYSTEM).values())
    for name in validate_prompts.MCP_TOOL_NAMES:
        assert name not in perception.SYSTEM


def test_hits_render_the_chunk_id_as_the_citation_target():
    """The id leads the line; the source is shown as provenance.

    Rendering the source first and the id second made the model cite the
    document rather than the passage — which was exactly the failure that
    produced `sandbox:papers/attention.md` citations in the E trace.
    """
    line = decision._format_hits([_chunk_hit()])
    assert line.index("mem:abc12345") < line.index("sandbox:papers/a.md")
    assert "chunk" in line.lower()


def test_hits_render_bounded_text():
    from schemas import MemoryItem

    huge = MemoryItem(
        id="mem:big", kind="fact", keywords=[], descriptor="d",
        value={"chunk": "x" * 20_000}, source="s", run_id="r",
    )
    assert len(decision._format_hits([huge])) < 6_000


def test_hits_render_chunk_bodies():
    """The passage Decision needs is rendered, not just its descriptor.

    Session 7's G diagnostic: the hit carried `value.chunk`, but the renderer
    emitted only the descriptor, so Decision kept calling `search_knowledge`.
    The fix is at this boundary — render the chunk — not an anti-loop rule.
    """
    from schemas import MemoryItem

    hit = MemoryItem(
        id="mem:body01", kind="fact", keywords=[],
        descriptor="[sandbox:papers/a.md chunk 1/2] some preview words",
        value={"chunk": "UNIQUE-BODY-SENTINEL full passage text here",
               "source": "sandbox:papers/a.md"},
        source="sandbox:papers/a.md", run_id="r",
    )
    rendered = decision._format_hits([hit])
    assert "UNIQUE-BODY-SENTINEL" in rendered
    assert "mem:body01" in rendered
    assert "sandbox:papers/a.md" in rendered


def test_hits_render_a_fact_value():
    """Session 7's C diagnostic: the date lived in `value`, not the descriptor.

    Dropping it was the rendering bug; the role could not have answered with
    the date from the input it received. Render the stored value.
    """
    from schemas import MemoryItem

    hit = MemoryItem(
        id="mem:date01", kind="fact", keywords=[],
        descriptor="Mom's birthday",
        value={"date": "15 May 2026"}, source="user_query", run_id="r",
    )
    rendered = decision._format_hits([hit])
    assert "15 May 2026" in rendered
    assert "mem:date01" in rendered


def test_history_renders_the_ids_it_shows():
    """An answer may only cite what it was shown. Action lines carry their
    recorded ids so a later goal can name the evidence it used."""
    rendered = decision._format_history([
        _action("g:1", "index_document", '{"chunks": 3, "id": "mem:7481f7c2"}'),
        _action("g:2", "fetch_url", "[artifact art:aa11, 900 bytes]"),
    ])
    assert "mem:7481f7c2" in rendered
    assert "art:aa11" in rendered


def test_attached_artifact_bytes_reach_the_prompt():
    rendered = decision._format_attached([("art:x", b"the actual page text")])
    assert "the actual page text" in rendered
    assert "art:x" in rendered


def test_attached_truncation_is_visible():
    blob = b"y" * 40_000
    rendered = decision._format_attached([("art:x", blob)])
    assert "truncated" in rendered
    assert len(rendered) < 30_000


_JSON_TYPES = {str: "string", int: "integer", float: "number", bool: "boolean"}


@pytest.mark.parametrize(("text", "name", "args"), [
    ('fetch_url(url="https://example.com")', "fetch_url",
     {"url": "https://example.com"}),
    ("get_time(timezone=\"Asia/Tokyo\")", "get_time", {"timezone": "Asia/Tokyo"}),
])
def test_parse_repair_valid(text, name, args):
    tools = [{"name": name, "input_schema": {
        "type": "object",
        "properties": {
            k: {"type": _JSON_TYPES[type(v)]} for k, v in args.items()
        },
        "required": list(args),
    }}]
    out = _parse_tool_call_text(text, tools)
    assert out is not None and out.name == name and out.arguments == args


@pytest.mark.parametrize("text", [
    "The answer is 42",                       # not an invocation
    'fetch_url(url="https://example.com") ok',  # trailing prose, not a pure call
    'unknown_tool(x="1")',                    # not in the catalogue
    'fetch_url(url="a", n=1+2)',               # non-literal argument
])
def test_parse_repair_rejects(text):
    tools = [{"name": "fetch_url", "input_schema": {
        "type": "object",
        "properties": {"url": {"type": "string"}}, "required": ["url"],
    }}]
    assert _parse_tool_call_text(text, tools) is None


def test_parse_repair_respects_the_schema():
    """A well-formed call for a tool the catalogue does not have is still not
    dispatched. The repair converts evident intent; it never invents."""
    tools = [{"name": "get_time", "input_schema": {
        "type": "object", "properties": {"timezone": {"type": "string"}},
        "required": ["timezone"],
    }}]
    assert _parse_tool_call_text('get_time()', tools) is None
    assert _parse_tool_call_text("", tools) is None
    assert _parse_tool_call_text('get_time(tz="UTC")', []) is None


def test_decision_always_receives_the_catalogue(monkeypatch):
    """Structural: `next_step` passes the tools it was given, unconditionally.

    Withholding tools based on the orchestrator's reading of the goal is a
    pre-emptive tool decision. The only condition here is whether a catalogue
    exists at all.
    """
    seen: dict = {}

    class _FakeLLM:
        def chat(self, **kwargs):
            seen.update(kwargs)
            return {"text": "an answer", "tool_calls": None}

    monkeypatch.setattr(decision, "LLM", lambda: _FakeLLM())
    monkeypatch.setattr(decision, "ensure_gateway", lambda: None)
    tools = [{"name": "search_knowledge", "description": "d", "input_schema": {}}]
    out = decision.next_step(
        Goal(id="g:1", text="What does the paper say?"),
        [_chunk_hit()], [], [], tools, request_context="q",
    )
    assert seen["tools"] == tools
    assert seen["tool_choice"] == "auto"
    assert out.is_answer and out.answer == "an answer"
    assert "mem:abc12345" in seen["prompt"]


def test_decision_returns_the_tool_call_it_was_given(monkeypatch):
    class _FakeLLM:
        def chat(self, **kwargs):
            return {"text": "", "tool_calls": [
                {"name": "search_knowledge", "arguments": {"query": "x"}}
            ]}

    monkeypatch.setattr(decision, "LLM", lambda: _FakeLLM())
    monkeypatch.setattr(decision, "ensure_gateway", lambda: None)
    out = decision.next_step(Goal(id="g:1", text="t"), [], [], [], [], "")
    assert out.tool_call.name == "search_knowledge"
    assert out.tool_call.arguments == {"query": "x"}


def test_decision_falls_back_to_a_plain_answer_on_empty_text(monkeypatch):
    """No tool call and no text is still one response, not a crash."""
    class _FakeLLM:
        def chat(self, **kwargs):
            return {"text": "", "tool_calls": None}

    monkeypatch.setattr(decision, "LLM", lambda: _FakeLLM())
    monkeypatch.setattr(decision, "ensure_gateway", lambda: None)
    out = decision.next_step(Goal(id="g:1", text="t"), [], [], [], [], "")
    assert out.is_answer


# ── Perception owns goal state ─────────────────────────────────────────

def test_perception_done_is_monotonic():
    """`done` only moves forward.

    Perception rewords its own goal list between runs, so a completed goal
    must not be reopened by a later iteration that forgets it happened.
    """
    assert inspect.getsource(perception.observe).count(
        "was_done or delta.done"
    ) == 1


def test_perception_preserves_prior_goal_identity():
    """Goals are identified by position, so a prior goal keeps its id and slot.

    Perception is the only layer allowed to add goals, and only after the
    prior list — never between prior goals.
    """
    source = inspect.getsource(perception.observe)
    assert "prior_goals[i].id" in source
    assert 'raw_goals[:len(prior_goals)]' in source


def test_perception_fallback_preserves_the_plan():
    """When Perception is unavailable the prior goals carry forward.

    Replacing the plan with a guess would lose real work; the printed
    diagnostic is what tells the trace which failure occurred.
    """
    prior = [Goal(id="g:1", text="keep me")]
    obs = agent7._fallback_observation("new query", prior, RuntimeError("boom"))
    assert [g.text for g in obs.goals] == ["keep me"]


def test_perception_fallback_on_a_cold_start_uses_the_query():
    obs = agent7._fallback_observation("just do it", [], RuntimeError("boom"))
    assert [g.text for g in obs.goals] == ["just do it"]


# ── current-run evidence scoping ───────────────────────────────────────

def test_current_run_artifacts_excludes_previous_runs():
    """Memory is persistent, so hits may carry another run's artifacts.

    Perception picks bytes by index into the current run's handles; a stale id
    must not resolve, or a later goal extracts from a page fetched before this
    question was asked.
    """
    history = [
        _action("g:1", "fetch_url", "ok", artifact_id="art:this"),
    ]
    assert agent7._current_run_artifacts(history) == {"art:this"}
    assert agent7._current_run_artifacts([]) == set()


def test_current_run_handles_come_from_this_run_only(monkeypatch):
    """Same boundary, one layer earlier: Perception's pickable handles are
    drawn only from artifacts this run recorded."""
    from schemas import MemoryItem

    mine = MemoryItem(id="mem:1", kind="tool_outcome", keywords=[],
                      descriptor="d", artifact_id="art:mine",
                      source="action", run_id="run-1")
    theirs = MemoryItem(id="mem:2", kind="tool_outcome", keywords=[],
                        descriptor="d", artifact_id="art:theirs",
                        source="action", run_id="run-0")
    handles = perception._current_run_artifact_handles([mine, theirs], "run-1")
    assert handles == ["art:mine"]


# ── honest reporting ───────────────────────────────────────────────────

def test_final_answer_is_the_last_recorded_answer():
    history = [
        _action("g:1", "index_document", '{"chunks": 3}'),
        _answer("g:1", "First answer."),
        _action("g:2", "search_knowledge", "[mem:1] ..."),
        _answer("g:2", "The final answer."),
    ]
    answer, reason = agent7._final_answer(history)
    assert answer == "The final answer."
    assert reason is None


def test_final_answer_never_substitutes_actions_for_an_answer():
    """The core honesty property.

    A run that indexed and searched but never answered has produced no answer.
    Returning the action log as though it were the reply is how an agent looks
    successful while having answered nothing, so the actions are reported as
    actions, under a reason that says the answer is missing.
    """
    history = [
        _action("g:1", "index_document", '{"chunks_indexed": 3}'),
        _action("g:2", "search_knowledge", "[mem:1] attention ..."),
    ]
    answer, reason = agent7._final_answer(history)
    assert reason == "no-answer"
    assert answer.startswith("No answer was produced.")
    assert "index_document" in answer


def test_final_answer_reports_an_empty_run_as_empty():
    answer, reason = agent7._final_answer([])
    assert answer == ""
    assert reason == "no-answer-or-action"


def test_failed_actions_alone_are_not_reported_as_work():
    history = [_action("g:1", "create_file", "ERROR: sandbox write refused")]
    answer, reason = agent7._final_answer(history)
    assert answer == ""
    assert reason == "no-answer-or-action"


def test_blank_answers_are_not_final_answers():
    history = [_answer("g:1", "   ")]
    answer, reason = agent7._final_answer(history)
    assert answer == ""
    assert reason == "no-answer-or-action"


# ── the memory write path ─────────────────────────────────────────────

def test_run_routes_the_opening_text_through_remember():
    """Session 7: the run's opening text enters through `remember`.

    `remember` is the classifier's write path. It keeps questions and
    requests as unembedded scratchpad, and stores a stated fact with an
    embedding — which is what makes C1's birthday recallable in C2.
    """
    source = inspect.getsource(agent7.run)
    assert "memory.remember(" in source
    assert "memory.record_query(" not in source


def test_remember_embeds_a_classified_fact(monkeypatch):
    """A stated fact is written as an embedded `fact`, date preserved."""
    import memory

    monkeypatch.setattr(memory, "ensure_gateway", lambda: None)
    monkeypatch.setattr(memory, "_llm_classify", lambda *a, **k: {"parsed": {
        "kind": "fact",
        "descriptor": "Mom's birthday is 15 May 2026",
        "keywords": ["mom", "birthday", "15", "may", "2026"],
        "value": {"date": "15 May 2026"},
    }})
    monkeypatch.setattr(memory, "_try_embed", lambda *a, **k: [0.0] * 768)

    item = memory.remember(
        "My mom's birthday is 15 May 2026. Remember that.",
        source="user_query", run_id="test-run",
    )
    assert item.kind == "fact"
    assert item.embedding is not None
    assert "15 May 2026" in item.descriptor
    assert memory._index().size == 1


def test_remember_stores_a_question_as_unembedded_scratchpad(monkeypatch):
    """A question is not knowledge and must not enter FAISS."""
    import memory

    monkeypatch.setattr(memory, "ensure_gateway", lambda: None)
    monkeypatch.setattr(memory, "_llm_classify", lambda *a, **k: {"parsed": {
        "kind": "scratchpad",
        "descriptor": "Compare asyncio and threading",
        "keywords": ["asyncio", "threading"],
        "value": {},
    }})
    monkeypatch.setattr(memory, "_try_embed", lambda *a, **k: [0.0] * 768)

    item = memory.remember(
        "Compare asyncio and threading for me",
        source="user_query", run_id="test-run",
    )
    assert item.kind == "scratchpad"
    assert item.embedding is None
    assert memory._index().size == 0


def test_recallable_kinds_are_the_only_embeddable_ones():
    """Documents, facts, and tool outcomes are searchable; scratchpad is not."""
    import memory

    assert memory._EMBEDDABLE_KINDS == {"fact", "preference", "tool_outcome"}
    assert "scratchpad" not in memory._EMBEDDABLE_KINDS


def test_fallback_remember_never_stores_a_fact(monkeypatch):
    """The classifier being unavailable means the content has NOT been judged,
    and the text is usually the user's own query. Storing it as `fact` embeds
    the question and makes it retrievable."""
    import memory
    from schemas import MemoryItem

    written: list[MemoryItem] = []
    monkeypatch.setattr(memory, "_persist_item",
                        lambda item: (written.append(item), item)[1])
    memory._fallback_remember(
        "Index the file papers/a.md and tell me what it says",
        source="user_query", run_id="r", goal_id=None,
    )
    assert len(written) == 1
    assert written[0].kind == "scratchpad"
    assert written[0].embedding is None


def test_search_knowledge_returns_only_document_chunks(monkeypatch):
    """A `raw`-only fact is not a chunk of an indexed document.

    Returning one here would let a stored sentence — including a user's own
    past query — pose as a corpus result, and because such an item embeds its
    own text it scores maximally against the query that produced it.
    """
    import mcp_server
    from schemas import MemoryItem

    chunk = MemoryItem(
        id="mem:chunk01", kind="fact", keywords=[], descriptor="[src chunk 1/4] ...",
        value={"chunk": "real document text", "source": "sandbox:papers/a.md"},
        source="sandbox:papers/a.md", run_id="r",
    )
    echo = MemoryItem(
        id="mem:echo0001", kind="fact", keywords=[],
        descriptor="Index the file papers/a.md and tell me what it says",
        value={"raw": "Index the file papers/a.md and tell me what it says"},
        source="user_query", run_id="r",
    )
    # The echo ranks first — exactly the observed failure.
    monkeypatch.setattr(mcp_server._memory, "read",
                        lambda *a, **k: [echo, chunk])
    results = mcp_server.search_knowledge("Index the file papers/a.md", k=5)
    assert [r["id"] for r in results] == ["mem:chunk01"]
    assert all(r["chunk"] for r in results)


def test_search_knowledge_over_empty_index_returns_empty(monkeypatch):
    """An index with nothing in it must return an empty list, not raise."""
    import mcp_server

    monkeypatch.setattr(mcp_server._memory, "read", lambda *a, **k: [])
    assert mcp_server.search_knowledge("anything", k=5) == []


def test_indexed_chunks_scopes_by_source(monkeypatch):
    """Repeat indexing is idempotent per document, not across documents."""
    import memory
    from schemas import MemoryItem

    def _item(mid: str, chunk: str, source: str) -> MemoryItem:
        return MemoryItem(id=mid, kind="fact", keywords=[],
                          descriptor="d", value={"chunk": chunk},
                          source=source, run_id="r1")

    monkeypatch.setattr(memory, "_load", lambda: [
        _item("m1", "alpha", "papers/x.md"),
        _item("m2", "beta", "papers/x.md"),
        _item("m3", "gamma", "papers/y.md"),
    ])
    assert memory.indexed_chunks("papers/x.md") == {"alpha", "beta"}
    assert memory.indexed_chunks("papers/y.md") == {"gamma"}


def test_record_outcome_failure_skips_embedding(monkeypatch):
    """A failed tool call stays keyword-reachable for audit but must not enter
    vector retrieval — error text is not knowledge about the world."""
    import memory
    from schemas import MemoryItem

    monkeypatch.setattr(memory, "_try_embed", lambda *a, **k: [0.0] * 768)
    written: list[MemoryItem] = []
    monkeypatch.setattr(memory, "_persist_item",
                        lambda item: (written.append(item), item)[1])
    ok = memory.record_outcome(
        tool_call=ToolCall(name="fetch_url",
                           arguments={"url": "https://example.com"}),
        result_text="[artifact art:1, 500 bytes] ok", artifact_id="art:1",
        run_id="r", goal_id="g:1", success=True,
    )
    bad = memory.record_outcome(
        tool_call=ToolCall(name="fetch_url", arguments={"url": "x"}),
        result_text="ERROR: connection refused", artifact_id=None,
        run_id="r", goal_id="g:1", success=False,
    )
    assert ok.embedding is not None and ok.kind == "tool_outcome"
    assert bad.embedding is None
    assert len(written) == 2


def test_classification_kind_is_closed():
    """The classifier may not invent a kind."""
    import memory

    with pytest.raises(ValidationError):
        memory._Classification.model_validate(
            {"kind": "bogus", "descriptor": "d"})


# ── compute_date ───────────────────────────────────────────────────────

@pytest.mark.parametrize(("anchor", "offset", "want"), [
    ("15 May 2026", "2 weeks before", "2026-05-01"),
    ("20 June 2026", "1 week before", "2026-06-13"),
    ("2026-05-15", "14 days before", "2026-05-01"),
    ("May 15, 2026", "2 weeks before", "2026-05-01"),
    ("15th of May 2026", "2 weeks before", "2026-05-01"),
    ("31 Jan 2026", "1 month after", "2026-02-28"),
    ("29 Feb 2024", "1 year after", "2025-02-28"),
    ("10 June 2026", "3 days after", "2026-06-13"),
])
def test_resolve_date(anchor, offset, want):
    assert _resolve_date(anchor, offset).isoformat() == want


@pytest.mark.parametrize(("anchor", "offset"), [
    ("15 May 2026", "sometime"),
    ("not a date", "2 weeks before"),
    ("15 May 2026", "2 fortnights before"),
    ("15 May 2026", ""),
])
def test_resolve_date_rejects(anchor, offset):
    with pytest.raises(ValueError):
        _resolve_date(anchor, offset)


def test_split_expression_forms():
    """The relative phrase and its anchor stay separable, so Decision can pass
    them to the tool rather than resolving them in its head."""
    assert _split_relative_expression("two weeks before 15 May 2026") == (
        "15 May 2026", "2 week before")
    assert _split_relative_expression("3 days after 10 June 2026") == (
        "10 June 2026", "3 day after")
    anchor, offset = _split_relative_expression("one week before 20 June 2026")
    assert _resolve_date(anchor, offset).isoformat() == "2026-06-13"
    with pytest.raises(ValueError):
        _split_relative_expression("sometime soon")


# ── failure is an envelope property, not a content guess ───────────────

@pytest.mark.parametrize("text", [
    "The tutorial covers exceptions and tracebacks in asyncio",
    "This page describes an error handling strategy for retries",
    "A guide to debugging your code when something breaks",
])
def test_content_words_are_not_failures(text):
    """Scanning result content for error words once burned a full 20-iteration
    run on a page that was merely *about* errors."""
    assert action._is_error_text(text) is False


@pytest.mark.parametrize("text", [
    "Error executing tool fetch_url: timeout",
    "ERROR: sandbox write refused",
    "browsertype.launch: Executable doesn't exist",
    "  Error Executing Tool list_dir: no such directory",
])
def test_prefixed_errors_are_failures(text):
    assert action._is_error_text(text) is True


# ── prompt hygiene ─────────────────────────────────────────────────────

def test_role_prompts_are_neutral():
    """No eval-query entity may appear in a model-facing prompt.

    A prompt that contains the answer teaches one query instead of the
    architecture, and the trace then proves nothing about the system.
    """
    assert all(validate_prompts.check_neutral().values())
    assert all(validate_prompts.check_memory_classifier().values())