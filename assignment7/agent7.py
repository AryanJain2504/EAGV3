"""agent7.py — Session 7 agent orchestrator.

The loop layout is unchanged from Session 6. What changed underneath is the
Memory service: writes compute an embedding via the gateway's V7 embed
endpoint and append to a FAISS index; reads use vector similarity first and
fall back to keyword search when the vector path is empty. Two MCP tools,
`index_document` and `search_knowledge`, surface the same machinery so the
agent can ingest documents on demand and query them.

    memory.read -> perception.observe -> decision.next_step ->
    action.execute -> memory.record_outcome

That is the whole loop, and this file does nothing else to it.

An earlier version of this orchestrator was roughly six times longer and
carried a second, parallel notion of correctness alongside the loop: a goal
"evidence mode" derived from what happened to be in memory, a reconciliation
pass that overrode Perception's `done` flags, a citation gate that discarded
answers naming unrendered ids, a boundary that rewrote ungrounded answers
into sentinels, a retry guard on repeated tool calls, per-role provider pins,
and a source-bundling expander for multi-page attachments. None of that was
part of the architecture; all of it was compensation for problems that were
diagnosed one layer away from where they were fixed.

The rule this file now follows is the one the reference diagnostic procedure
states: when a layer misbehaves, decide whether it could have behaved
correctly given the input it received. If it could not, the fix belongs at the
rendering boundary that withheld the input — not in a new branch here. Every
such case is fixed there instead, so this file stays a loop.

One thing is deliberately kept, because it is correctness rather than
machinery: honest final-answer resolution. A run that ends with actions but no
answer reports exactly that, rather than presenting the action log as though it
were the reply the user asked for.

The run's opening text goes through `memory.remember`, the Session 7 write
path: the classifier separates a stated fact from a question or request, and
facts are embedded at write time. That is what makes a fact stated in C1
retrievable in C2.

Run from this folder:
    uv run agent7.py "What is the current time in Tokyo and Bangalore?"
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
import uuid
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

import action
import artifacts
import decision
import memory
import perception
from gateway import ensure_gateway
from schemas import Goal, HistoryEvent, Observation

MCP_SERVER = Path(__file__).parent / "mcp_server.py"
MAX_ITERATIONS = int(os.getenv("A7_MAX_ITERATIONS", "20"))


def _mcp_tools_for_decision(tools) -> list[dict]:
    """Convert MCP tool descriptors into the shape the gateway expects."""
    return [
        {
            "name": t.name,
            "description": t.description or "",
            "input_schema": t.inputSchema or {"type": "object", "properties": {}},
        }
        for t in tools
    ]


def _current_run_artifacts(history: list[dict]) -> set[str]:
    """Artifact ids this run produced.

    Memory is persistent, so hits may carry artifacts from earlier runs.
    Perception chooses which bytes a goal needs by index; that index is only
    meaningful against what this run actually fetched, so a stale handle is
    dropped rather than resolved. This scopes *evidence*, never tools — the
    goal text still reaches Decision exactly as Perception wrote it.
    """
    return {
        str(event["artifact_id"])
        for event in history
        if event.get("kind") == "action" and event.get("artifact_id")
    }


def _final_answer(history: list[dict]) -> tuple[str, str | None]:
    """Resolve what the run actually produced, honestly.

    Returns `(answer, incomplete_reason)`.

    A question that ends with no answer event is incomplete. A log of
    completed tool calls is not a substitute for the answer the user asked
    for, and presenting one as the other is the most common way an agent run
    looks successful while having answered nothing. So when actions were
    recorded but no answer was, the actions are reported as actions, under a
    reason that says the answer is missing.
    """
    answers = [
        event for event in history
        if event.get("kind") == "answer" and str(event.get("text") or "").strip()
    ]
    if answers:
        return answers[-1]["text"], None

    actions = [
        event for event in history
        if event.get("kind") == "action"
        and not str(event.get("result_descriptor") or "").strip()
        .lower()
        .startswith(("error:", "error executing tool"))
    ]
    if not actions:
        return "", "no-answer-or-action"

    lines = []
    for event in actions:
        args = event.get("arguments") or {}
        rendered = ", ".join(
            f"{k}={v}" for k, v in list(args.items())[:3]
        )
        lines.append(f"- {event.get('tool')}({rendered})")
    return (
        "No answer was produced. Recorded actions:\n" + "\n".join(lines),
        "no-answer",
    )


async def run(query: str, perception_provider: str = "g") -> str:
    """Run one query through the loop and return the final answer.
    perception_provider pins Perception to a worker (default "g" per S7 spec).
    Decision calls the gateway with provider "g" and auto_route metadata."""
    ensure_gateway()
    run_id = uuid.uuid4().hex[:8]
    print(f"\n{'═' * 78}")
    print(f"run {run_id}  ─  query: {query}")
    print(f"{'═' * 78}")

    # Every run starts by offering the user's text to Memory. `remember`
    # runs the classifier, which separates a stated fact ("my mom's birthday
    # is 15 May 2026") from a request or question (scratchpad). Facts get an
    # embedding at write time, which is what lets C2 retrieve the fact stated
    # in C1. The old `record_query` path wrote every query as an unembedded
    # scratchpad, so the birthday was never recallable.
    try:
        memory.remember(query, source="user_query", run_id=run_id)
    except Exception as exc:
        print(f"[memory.remember] skipped: {exc!r}")

    # The MCP SDK otherwise hands the subprocess a minimal allowlist env, so
    # `EAGV3_STATE_DIR` (state isolation) never reached mcp_server: indexing
    # and search silently used the default `state/` while the loop used the
    # isolated dir. Pass the environment through.
    server_params = StdioServerParameters(
        command=sys.executable,
        args=[str(MCP_SERVER)],
        env=dict(os.environ),
    )
    history: list[dict] = []
    prior_goals: list[Goal] = []
    prev_t: float | None = None

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            mcp_tools = (await session.list_tools()).tools
            tools_for_decision = _mcp_tools_for_decision(mcp_tools)
            tool_schemas = {
                tool["name"]: tool["input_schema"]
                for tool in tools_for_decision
            }
            print(
                f"[mcp] loaded {len(mcp_tools)} tools: "
                f"{[t.name for t in mcp_tools]}"
            )

            for it in range(1, MAX_ITERATIONS + 1):
                now = time.time()
                if prev_t is not None:
                    print(f"[timing]        iter {it - 1} took {now - prev_t:.1f}s")
                prev_t = now
                print(
                    f"\n─── iter {it} "
                    f"────────────────────────────────────────────"
                )

                # 1. MEMORY READ
                hits = memory.read(query, history)
                print(f"[memory.read]   {len(hits)} hits")

                # 2. PERCEPTION — the only owner of goal state.
                try:
                    obs = perception.observe(query, hits, history, prior_goals, run_id,
                                            provider=perception_provider)
                except Exception as exc:
                    obs = _fallback_observation(query, prior_goals, exc)
                prior_goals = obs.goals
                for g in obs.goals:
                    flag = "✓" if g.done else "○"
                    attach = (
                        f"  attach={g.attach_artifact_id}"
                        if g.attach_artifact_id else ""
                    )
                    print(f"[perception]    {flag} {g.id} — {g.text}{attach}")

                if obs.all_done:
                    print(f"\n[done] all {len(obs.goals)} goals satisfied")
                    break

                goal = obs.next_unfinished()
                if goal is None:
                    print("\n[done] no unfinished goal — stopping")
                    break

                # Perception decided whether this goal needs raw bytes.
                attached: list[tuple[str, bytes]] = []
                attach_ids = list(dict.fromkeys(
                    goal.attach_artifact_ids
                    or ([goal.attach_artifact_id] if goal.attach_artifact_id else [])
                ))
                for art_id in attach_ids:
                    if art_id not in _current_run_artifacts(history):
                        print(
                            f"[attach]        dropped stale handle "
                            f"{art_id} (not from this run)"
                        )
                    elif artifacts.exists(art_id):
                        blob = artifacts.get_bytes(art_id)
                        attached.append((art_id, blob))
                        print(
                            f"[attach]        {art_id} "
                            f"({len(blob)} bytes)"
                        )

                # 3. DECISION
                try:
                    out = decision.next_step(
                        goal, hits, attached, history, tools_for_decision,
                        request_context=query,
                    )
                except Exception as exc:
                    print(f"[decision] failed: {exc!r}")
                    break

                if out.is_answer:
                    answer = out.answer or ""
                    if not answer.strip():
                        print("[decision]      empty answer; not finalizing")
                        continue
                    print(
                        f"[decision]      ANSWER: {answer[:200]}"
                        f"{'...' if len(answer) > 200 else ''}"
                    )
                    history.append(HistoryEvent(
                        iter=it, kind="answer", goal_id=goal.id, text=answer,
                    ).model_dump())
                    # An answer to the last open goal completes the run; no
                    # extra Perception bounce just to confirm it.
                    still_open = [
                        g for g in obs.goals if not g.done and g.id != goal.id
                    ]
                    if it == MAX_ITERATIONS or not still_open:
                        break
                    continue

                # 4. ACTION
                tc = out.tool_call
                print(
                    f"[decision]      TOOL_CALL: {tc.name}"
                    f"({json.dumps(tc.arguments)[:120]})"
                )
                result = await action.execute(session, tc, tool_schemas)
                result_text, art_id = result.text, result.artifact_id
                print(
                    f"[action]        → {result_text[:200].replace(chr(10), ' ')}"
                    f"{'...' if len(result_text) > 200 else ''}"
                    + (f"   +{art_id}" if art_id else "")
                )

                # 5. MEMORY WRITE (zero-LLM for tool outcomes)
                memory.record_outcome(
                    tool_call=tc,
                    result_text=result_text,
                    artifact_id=art_id,
                    run_id=run_id,
                    goal_id=goal.id,
                    success=result.success,
                )
                history.append(HistoryEvent(
                    iter=it,
                    kind="action",
                    goal_id=goal.id,
                    tool=tc.name,
                    arguments=tc.arguments,
                    result_descriptor=result_text[:4000],
                    artifact_id=art_id,
                ).model_dump())

    print(f"\n{'═' * 78}")
    final_answer, incomplete = _final_answer(history)
    if incomplete == "goals-open":
        print("[incomplete]    at least one goal remains unsatisfied")
    elif incomplete == "no-answer":
        print("[incomplete]    all goals done, but no answer was recorded")
    elif incomplete == "no-answer-or-action":
        print("[incomplete]    nothing was recorded for this run")
    _write_trace(run_id, query, history, prior_goals, final_answer, incomplete)
    print(f"FINAL: {final_answer}")
    print(f"{'═' * 78}\n")
    return final_answer


def _fallback_observation(
    query: str,
    prior_goals: list[Goal],
    error: Exception,
) -> Observation:
    """Keep the loop observable when Perception is unavailable.

    Carrying the prior goals forward unchanged is the only safe fallback: it
    preserves the run's plan instead of replacing it with a guess, and the
    printed diagnostic says which failure produced it.
    """
    print(
        f"[perception] unavailable ({error!r}); "
        "carrying prior goals forward"
    )
    if prior_goals:
        return Observation(goals=prior_goals)
    return Observation(goals=[Goal(id=f"g:fallback-{uuid.uuid4().hex[:8]}", text=query)])


def _write_trace(
    run_id: str,
    query: str,
    history: list[dict],
    goals: list[Goal],
    final_answer: str,
    incomplete: str | None,
) -> None:
    """Persist the run's trace next to the state it describes."""
    from state_paths import STATE_DIR

    state_dir = STATE_DIR
    try:
        with (state_dir / f"history_{run_id}.jsonl").open("w", encoding="utf-8") as f:
            for event in history:
                f.write(json.dumps(event, default=str) + "\n")
        (state_dir / f"result_{run_id}.json").write_text(
            json.dumps({
                "run_id": run_id,
                "query": query,
                "final_answer": final_answer,
                "incomplete_reason": incomplete,
                "goals": [g.model_dump(mode="json") for g in goals],
            }, indent=2),
            encoding="utf-8",
        )
    except Exception as exc:
        print(f"[trace] skipped: {exc!r}")


def main() -> None:
    import argparse
    ap = argparse.ArgumentParser(description="Assignment 7 agent loop")
    ap.add_argument("query", nargs="*", help="User query to solve")
    ap.add_argument("--perception-provider", default="g",
                    help="Pin Perception worker (default g=Gemini per S7 spec)")
    args = ap.parse_args()
    query = " ".join(args.query) or (
        "What is the current time in Asia/Tokyo and Asia/Kolkata? "
        "Tell me the difference in hours."
    )
    asyncio.run(run(query, perception_provider=args.perception_provider))


if __name__ == "__main__":
    main()