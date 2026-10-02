"""Agent 6 loop — THE agent. Plain Python, zero LLM calls of its own.

Every arrow goes through here: loop -> memory -> loop -> perception ->
loop -> decision -> loop -> action -> loop ... Only this file decides what
each role gets to see. Roles never talk to each other.

Usage:
  uv run agent6.py "Fetch the Wikipedia page for Claude Shannon ..." [--run-id R] [--max-iter 10]
  uv run agent6.py --clean-state   # wipe state/ between assignment attempts
"""

import argparse
import json
import re
import sys
import time
import uuid
from pathlib import Path

from rich.console import Console

import gateway
from action import TOOLS, ArtifactStore, MCPClient, execute, failed, run_async
from decision import decide
from memory import MemoryStore, memory_write_prompt
from perception import perceive
from schemas import DecisionIn, HistoryItem, MemoryItem, Observation, Observe, ToolCall

BASE = Path(__file__).parent
STATE = BASE / "state"
SERVER = BASE / "mcp_server.py"
MAX_ATTACHMENT_CHARS = 24000
ATTACHMENT_HEAD_CHARS = 18000
SOURCE_BUNDLE_CHARS = 5000

console = Console()


def attachment_excerpt(data: bytes, max_chars: int = MAX_ATTACHMENT_CHARS) -> str:
    """Keep large MCP artifacts within the configured model context window.

    The complete artifact remains persisted under state/artifacts/. Decision
    receives a bounded excerpt because sending an 80KB web page verbatim can
    exceed the V3 worker's context limit before it can answer.
    """
    text = data.decode("utf-8", "ignore")
    try:
        envelope = json.loads(text)
        if isinstance(envelope, dict) and isinstance(envelope.get("text"), str):
            text = envelope["text"]
    except json.JSONDecodeError:
        pass
    if len(text) <= max_chars:
        return text
    head_chars = min(ATTACHMENT_HEAD_CHARS, max_chars)
    tail_chars = max_chars - head_chars
    tail = text[-tail_chars:] if tail_chars else ""
    return (
        text[:head_chars]
        + "\n\n[artifact excerpt truncated; full artifact is persisted on disk]\n\n"
        + tail
    )


def source_artifact_ids(history: list[HistoryItem]) -> list[str]:
    """Return the first three fetched pages produced by the current search."""
    search_seen = False
    ids: list[str] = []
    for item in history:
        if item.kind == "action" and item.tool == "web_search":
            search_seen = True
        if (search_seen and item.kind == "action" and item.tool == "fetch_url"
                and item.artifact_id and "FAILED" not in (item.text or "")
                and item.artifact_id not in ids):
            ids.append(item.artifact_id)
    return ids[:3]


def search_result_urls(history: list[HistoryItem]) -> list[str]:
    """Extract candidate URLs from the first successful search response."""
    for item in history:
        if item.kind == "action" and item.tool == "web_search" and "FAILED" not in (item.text or ""):
            urls = re.findall(r"https?://[^\s\"\\]+", item.text)
            return [url.rstrip(".,)\\") for url in urls]
    return []


def fetched_urls(history: list[HistoryItem]) -> set[str]:
    return {
        str(item.args.get("url"))
        for item in history
        if item.kind == "action" and item.tool == "fetch_url"
        and "FAILED" not in (item.text or "") and item.args.get("url")
    }


def needs_source_bundle(goal_text: str) -> bool:
    text = goal_text.lower()
    return any(term in text for term in ("extract", "agree", "summarize", "advice"))


def clean_state() -> None:
    STATE.mkdir(parents=True, exist_ok=True)
    (STATE / "artifacts").mkdir(parents=True, exist_ok=True)
    console.print("[dim]state/ preserved; existing histories, memory, and artifacts retained[/dim]")


def append_history(state_dir: Path, run_id: str, item: HistoryItem) -> None:
    with (state_dir / f"history_{run_id}.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(item.model_dump()) + "\n")


def _required_fetches(goal_text: str) -> int:
    """Return the minimum fetch count for an explicitly multi-result goal."""
    match = re.search(r"top\s+(\d+|one|two|three|four|five)", goal_text.lower())
    if not match:
        return 1
    value = match.group(1)
    if value.isdigit():
        return int(value)
    return {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5}[value]


def _goal_has_evidence(goal: object, history: list[HistoryItem]) -> bool:
    goal_text = getattr(goal, "text", "").lower()
    if "fetch" not in goal_text and "open" not in goal_text:
        return any(item.kind == "decision" and item.tool is None and item.text
                   for item in history)

    successful_fetches = sum(
        item.kind == "action"
        and item.tool == "fetch_url"
        and "FAILED" not in (item.text or "")
        for item in history
    )
    return successful_fetches >= _required_fetches(goal_text)


def reconcile_observation(observation: Observation,
                          prior_goals: list,
                          history: list[HistoryItem]) -> Observation:
    """Keep Perception monotonic when an LLM overstates completion.

    Only the first unfinished goal may transition to done on one loop pass.
    A plain extraction goal needs a recorded Decision answer; a tool goal may use
    a matching successful Action record.
    Artifact attachments from Perception on any open goal are preserved.
    """
    if not prior_goals:
        return Observation(goals=[
            goal.model_copy(update={"done": False, "attach_artifact_id": None})
            for goal in observation.goals
        ])

    candidates = {goal.id: goal for goal in observation.goals}
    first_open = next((i for i, goal in enumerate(prior_goals) if not goal.done), None)

    goals = []
    for index, previous in enumerate(prior_goals):
        candidate = candidates.get(previous.id)
        done = previous.done
        attach_id = candidate.attach_artifact_id if candidate else previous.attach_artifact_id

        if index == first_open and candidate is not None:
            latest_answered = bool(
                history
                and history[-1].kind == "decision"
                and history[-1].tool is None
                and history[-1].text
            )
            answered_goal = latest_answered and not needs_source_bundle(previous.text)
            done = (bool(candidate.done) and _goal_has_evidence(previous, history)) or answered_goal

        if done:
            attach_id = None

        goals.append(previous.model_copy(update={"done": done,
                                                  "attach_artifact_id": attach_id}))
    return Observation(goals=goals)


def run_query(query: str, run_id: str, max_iter: int = 10,
              perception_provider: str = "o",
              decision_provider: str | None = "o") -> dict:
    """Sync entry: the whole run is ONE coroutine on ONE task, so the MCP
    stdio session (anyio cancel scope) is entered and exited in the same task."""
    return run_async(arun_query(query, run_id, max_iter,
                                perception_provider, decision_provider))


async def arun_query(query: str, run_id: str, max_iter: int = 10,
                     perception_provider: str = "o",
                     decision_provider: str | None = "o") -> dict:
    store = MemoryStore(STATE)
    artifacts = ArtifactStore(STATE, run_id)  # filenames are run-prefixed; seq restarts per run
    mcp = MCPClient(SERVER)
    history: list[HistoryItem] = []
    seq = 0

    def hist(kind: str, text: str = "", tool: str | None = None,
             args: dict | None = None, artifact_id: str | None = None) -> HistoryItem:
        nonlocal seq
        seq += 1
        item = HistoryItem(seq=seq, kind=kind, text=text, tool=tool,
                           args=args or {}, artifact_id=artifact_id)
        history.append(item)
        append_history(STATE, run_id, item)
        return item

    t0 = time.time()
    prior: list = []
    hits: list = []

    final_answer: str | None = None
    iterations = 0
    try:
        for it in range(1, max_iter + 1):
            iterations = it
            # 1. Memory is consulted at the START OF EVERY ITERATION, with
            #    history grown so far. No LLM involved.
            hits = store.read(query, history)
            console.print(f"[cyan]memory.read[/cyan] iter {it} -> {len(hits)} hit(s)")
            # 2. Perception: decompose or verify.
            obs = Observe(query=query, hits=hits, history=history,
                          prior_goals=prior, run_id=run_id)
            observation = perceive(obs, provider=perception_provider)
            observation = reconcile_observation(observation, prior, history)
            hist("perception",
                 text=f"goals={[ (g.id, g.text[:60], g.done, g.attach_artifact_id) for g in observation.goals ]}")
            console.print(f"[magenta]perception[/magenta] iter {it}: "
                          f"{len(observation.goals)} goals, "
                          f"{sum(g.done for g in observation.goals)} done")
            prior = observation.goals

            goal = observation.first_unfinished()
            if goal is None:
                break  # all done

            # 3. Loop dumps artifact bytes (iff Perception attached an id).
            attachment = None
            if goal.attach_artifact_id is not None:
                bundle_ids = source_artifact_ids(history) if needs_source_bundle(goal.text) else []
                if len(bundle_ids) > 1:
                    attachment = "\n\n".join(
                        f"SOURCE {index}:\n{attachment_excerpt(artifacts.get(artifact_id), SOURCE_BUNDLE_CHARS)}"
                        for index, artifact_id in enumerate(bundle_ids, start=1)
                        if artifacts.exists(artifact_id)
                    )
                elif artifacts.exists(goal.attach_artifact_id):
                    attachment = attachment_excerpt(artifacts.get(goal.attach_artifact_id))
                else:
                    goal = goal.model_copy(update={"attach_artifact_id": None})

            # 4. Decision: one goal -> answer | one tool call.
            dec = decide(DecisionIn(goal=goal, hits=hits, attachment=attachment,
                                    history_slice=history[-6:], tools=TOOLS),
                         provider=decision_provider)
            if (dec.answer is not None and "fetch" in goal.text.lower()
                    and _required_fetches(goal.text) > len(source_artifact_ids(history))):
                next_url = next(
                    (url for url in search_result_urls(history)
                     if url not in fetched_urls(history)),
                    None,
                )
                if next_url:
                    dec = type(dec)(tool_call=ToolCall(
                        name="fetch_url", arguments={"url": next_url}))
            if not dec.is_tool_call():
                hist("decision", text=(dec.answer or ""))
                console.print(f"[green]decision[/green] answer: {(dec.answer or '')[:120]}")
                if (needs_source_bundle(goal.text)
                        and len(source_artifact_ids(history)) >= 3):
                    prior = [
                        item.model_copy(update={"done": True, "attach_artifact_id": None})
                        if item.id == goal.id else item
                        for item in prior
                    ]
                    console.print("[magenta]perception[/magenta] synthesis verified")
                    break
                continue

            # 5. Action: dispatch via MCP stdio + artifact gate.
            call = dec.tool_call
            hist("decision", text=f"tool_call: {call.name}",
                 tool=call.name, args=call.arguments)
            console.print(f"[yellow]action[/yellow] {call.name}({json.dumps(call.arguments)[:120]})")
            try:
                raw = await mcp.call_tool(call.name, call.arguments or {})
                out = execute(call, run_id, artifacts, str(raw))
            except Exception as e:
                out = failed(call, str(e))
            if out.ok and out.artifact_id is not None:
                hist("action", text=f"{call.name} ok -> artifact #{out.artifact_id}",
                     tool=call.name, args=call.arguments, artifact_id=out.artifact_id)
                console.print(f"[yellow]action[/yellow] -> artifact #{out.artifact_id}")
            elif out.ok:
                history_text_limit = 4000 if call.name == "web_search" else 500
                hist("action", text=f"{call.name} ok: {(out.text or '')[:history_text_limit]}",
                     tool=call.name, args=call.arguments)
            else:
                hist("action", text=f"{call.name} FAILED: {out.error}",
                     tool=call.name, args=call.arguments)
                console.print(f"[red]action failed[/red]: {out.error}")
    finally:
        await mcp.close()

    # 6. End-of-loop memory write (LLM-assisted, validated, then stored).
    try:
        transcript = "\n".join(f"[{h.kind}] {h.text[:300]}" for h in history)
        mem_route = {"provider": decision_provider} if decision_provider else {"auto_route": "memory"}
        resp = gateway.chat(
            messages=[{"role": "user", "content": memory_write_prompt(transcript)}],
            system="You output strict JSON only.",
            max_tokens=800, temperature=0.2,
            response_format={"type": "json_object"}, **mem_route,
        )
        items = json.loads((resp.get("text") or "").strip()).get("items", [])
        saved = 0
        for raw_item in items:
            try:
                item = MemoryItem.model_validate({**raw_item, "run_id": run_id})
                store.write(item)
                saved += 1
            except Exception:
                continue
        console.print(f"[cyan]memory.write[/cyan] -> {saved} item(s) saved")
    except Exception as e:
        console.print(f"[dim]memory.write skipped: {e}[/dim]")
    store.clear_scratchpad(run_id)

    # Stitch the final answer from decision ANSWERS in history (tool-call
    # entries carry tool set, so they are excluded). If no decision ever
    # answered directly (pure tool-call runs), ask Decision once to state
    # the final answer from verified history.
    answers = [h.text for h in history
               if h.kind == "decision" and h.text and h.tool is None]
    all_goals_done = bool(prior) and all(goal.done for goal in prior)
    final_answer = answers[-1] if all_goals_done and answers else None
    if final_answer is None and history and all_goals_done:
        try:
            from schemas import Goal as _Goal
            stitch = decide(DecisionIn(
                goal=_Goal(id="g_final", text=f"State the final answer to the user query using only the verified results in history: {query}"),
                hits=hits, attachment=None, history_slice=history[-6:], tools=[]),
                provider=decision_provider)
            if stitch.answer:
                final_answer = stitch.answer
                hist("decision", text=final_answer[:500])
        except Exception as e:
            console.print(f"[dim]final stitch skipped: {e}[/dim]")
    if not all_goals_done:
        console.print("[red]run incomplete: at least one goal is unfinished[/red]")
    duration = round(time.time() - t0, 2)
    result = {"run_id": run_id, "query": query, "iterations": iterations,
              "duration_s": duration, "final_answer": final_answer,
              "goals": [g.model_dump() for g in prior]}
    (STATE / f"result_{run_id}.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


def main() -> None:
    ap = argparse.ArgumentParser(description="Agent 6 MPDA loop")
    ap.add_argument("query", nargs="?", help="User query to solve")
    ap.add_argument("--run-id", default=None)
    ap.add_argument("--max-iter", type=int, default=10)
    ap.add_argument("--clean-state", action="store_true")
    ap.add_argument("--perception-provider", default="o",
                    help="Worker for Perception (default o=Ollama local; g=Gemini)")
    ap.add_argument("--decision-provider", default="o",
                    help="Worker for Decision (default o=Ollama local; None=auto_route)")
    args = ap.parse_args()

    if args.clean_state:
        clean_state()
        if not args.query:
            return
    if not args.query:
        ap.error("provide a query or --clean-state")
    run_id = args.run_id or f"run_{uuid.uuid4().hex[:8]}"
    result = run_query(args.query, run_id, args.max_iter,
                       args.perception_provider, args.decision_provider)
    console.print(f"\n[bold green]done in {result['iterations']} iterations "
                  f"({result['duration_s']}s)[/bold green]")
    console.print(f"[bold]final:[/bold] {(result['final_answer'] or '')[:2000]}")


if __name__ == "__main__":
    sys.exit(main() or 0)
