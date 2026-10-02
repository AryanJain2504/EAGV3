# Assignment 6: MPDA Agent (Memory–Perception–Decision–Action)

Session 6 architecture: a plain-Python agent loop orchestrating four roles.
The loop is the agent — roles never talk to each other. Every boundary is a
Pydantic v2 contract. LLM calls go only through Gateway V3 (`:8101`).
Tool calls go only through the MCP server over stdio. No LangChain/Graph/CrewAI.

## Layout

```
assignment6/
├── schemas.py        # Observe/Observation/Goal/DecisionIn/DecisionOut/ToolCall/ActionOut/HistoryItem/MemoryItem/Artifact
├── memory.py         # JSON store in state/ (read/filter = code; write = LLM-assisted)
├── perception.py     # decompose + verify prompt (provider=g)
├── decision.py       # atomic worker prompt (auto_route=decision)
├── action.py         # MCP stdio dispatch + >4KB artifact gate
├── agent6.py         # THE loop + CLI
├── gateway.py        # V3 transport substrate (sole LLM path)
├── mcp_server.py     # provided: 9 tools, stdio
├── validate_prompts.py -> pop_validation.json
└── state/            # gitignored: memory.json, artifacts/, history_*.jsonl, result_*.json
```

## Setup

```bash
cd assignment6
cp .env.example .env   # add TAVILY_API_KEY (DDG fallback works without it)
# gateway: cd ../llm_gatewayV3 && ./run.sh   # :8101
uv run agent6.py --clean-state
```

The `--clean-state` option ensures `state/` exists but preserves prior
histories, results, artifacts, and memory.

## The four queries

```bash
uv run agent6.py "Fetch the Wikipedia page for Claude Shannon and tell me his birth and death dates and three key contributions to information theory."
uv run agent6.py "Find three family-friendly things to do in Tokyo this weekend, check Saturday's weather forecast there, and tell me which one is most appropriate."
uv run agent6.py "My mom's birthday is on 15th of May 2026. Remember that, and give me a calendar reminder for two weeks before and on the day."
uv run agent6.py "When is my mom's birthday?"   # same state/ as previous run — must answer from memory
uv run agent6.py "Search for Python asyncio best practices, open the top three results, and give me a short numbered list of the advice they agree on."
```

## Terminal outputs

The following outputs were captured from local runs. Query 3 run 1 and run 2
share the same state directory so that run 2 can answer from durable memory.

### Q1 Shannon

Result JSON: [result_run_23d9523e.json](submission_results/result_run_23d9523e.json)

```text
perception iter 1: 2 goals, 0 done
action fetch_url({"url": "https://en.wikipedia.org/wiki/Claude_Shannon"})
action -> artifact #art:fb0cfa32
perception iter 2: 2 goals, 1 done
decision answer: Born on April 30, 1916, and died on February 24, 2001.
perception iter 3: 2 goals, 2 done
memory.write -> 3 item(s) saved
done in 3 iterations (98.81s)
final: Born on April 30, 1916, and died on February 24, 2001. Key contributions:
Foundations of information theory and entropy; the Mathematical Theory of
Communication; and cryptographic secrecy, including perfect secrecy.
```

Verification note: the linked saved result from another attempt reports
February 26, 2001. The terminal output above reports February 24, 2001, which
is the correct date. Rerun Query 1 once and replace both the output and JSON
link with the matching successful run before submission.

### Q2 Tokyo

Result JSON: [result_run_91058244.json](submission_results/result_run_91058244.json)

```text
perception iter 1: 3 goals, 0 done
decision answer: Based on the partly cloudy skies with light rain in the afternoon,
Tokyo DisneySea is the most appropriate choice.
perception iter 2: 3 goals, 1 done
decision answer: Saturday's weather forecast for Tokyo is partly cloudy skies with
a high of 28C and light rain in the afternoon.
perception iter 3: 3 goals, 2 done
decision answer: Based on the partly cloudy skies with light rain in the afternoon,
Tokyo DisneySea is the most appropriate choice.
perception iter 4: 3 goals, 3 done
memory.write -> 2 item(s) saved
done in 4 iterations (104.78s)
final: Based on the partly cloudy skies with light rain in the afternoon, Tokyo
DisneySea is the most appropriate choice - it offers a mix of indoor and outdoor
attractions so the family can move indoors if rain arrives.
```

### Q3 Mom (run 1 + run 2, shared state)

Run 1 result JSON: [result_run_2c8b54ff.json](submission_results/result_run_2c8b54ff.json)

Run 2 result JSON: [result_run_1e7187e6.json](submission_results/result_run_1e7187e6.json)

```text
Run 1:
perception iter 1: 2 goals, 0 done
decision answer: Mom's birthday is on 15th of May 2026.
perception iter 2: 2 goals, 1 done
decision answer: The calendar reminder for two weeks before and on the day of
mom's birthday (15th of May 2026) has been created.
perception iter 3: 2 goals, 2 done
memory.write -> 2 item(s) saved
done in 3 iterations (65.61s)

Run 2:
memory.read iter 1 -> 3 hit(s)
perception iter 1: 1 goal, 0 done
decision answer: Mom's birthday is 2026-05-15.
perception iter 2: 1 goal, 1 done
memory.write -> 1 item(s) saved
done in 2 iterations (66.25s)
final: Mom's birthday is 2026-05-15.
```

Note: the run generates reminder details; the Assignment 6 tool catalogue does
not include an external calendar integration.

### Q4 Asyncio

Result JSON: [result_run_410a06a0.json](submission_results/result_run_410a06a0.json)

```text
perception iter 5: 2 goals, 1 done
decision answer:
1. Use asyncio.run() with debug=True to enable debug mode for asyncio.
2. Avoid blocking code in async functions; use asyncio.to_thread() or
	loop.run_in_executor() instead.
3. Use asyncio.get_event_loop() to access the current event loop.
4. Use asyncio.gather() or asyncio.wait() for concurrent coroutines.
5. Use asyncio.sleep() instead of time.sleep() for non-blocking delays.
6. Use asyncio.create_task() to schedule coroutines.
7. Use asyncio.Queue() for communication between async tasks.
8. Use asyncio.Lock() or asyncio.Semaphore() for shared resources.
9. Handle asyncio.exceptions.CancelledError for task cancellation.
10. Use asyncio.iscoroutinefunction() to check coroutine functions.
perception synthesis verified
memory.write -> 3 item(s) saved
done in 5 iterations (292.06s)
final: numbered asyncio best-practice synthesis shown above.
```

## PoP Validation

The complete validation artifact is available here: [pop_validation.json](pop_validation.json).

### Perception Prompt Validation

```json
{
	"decompose_on_empty_prior": true,
	"same_order_no_drop": true,
	"done_only_from_history": true,
	"artifact_id_routing": true,
	"strict_json": true,
	"few_shots": true,
	"perception_pass": true
}
```

### Decision Prompt Validation

```json
{
	"one_goal_only": true,
	"answer_xor_toolcall": true,
	"single_tool_max": true,
	"no_tool_invention": true,
	"attachment_extraction": true,
	"strict_json": true,
	"few_shots": true,
	"decision_pass": true
}
```

## YouTube

TODO: link demonstrating all four queries end to end.
