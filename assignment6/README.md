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

A clean capture starts from an empty state directory (`state/` is
gitignored, so this is safe and required for evidence runs):

```bash
rm -rf state/
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

Result JSON: [result_run_cff6d28f.json](submission_results/result_run_cff6d28f.json)

```text
memory.read iter 1 -> 0 hit(s)
perception iter 1: 2 goals, 0 done
action fetch_url({"url": "https://en.wikipedia.org/wiki/Claude_Shannon"})
action -> artifact #art:342383e6
memory.read iter 2 -> 0 hit(s)
perception iter 2: 2 goals, 1 done
decision answer: Born: April 30, 1916; Died: February 24, 2001. Key contributions to information theory: founding the field of informatio
memory.read iter 3 -> 0 hit(s)
perception iter 3: 2 goals, 2 done
memory.write -> 4 item(s) saved
done in 3 iterations (25.01s)
final: Born: April 30, 1916; Died: February 24, 2001. Key contributions to information theory: founding the field of information theory with his 1948 paper 'A Mathematical Theory of Communication', introducing the bit as the fundamental unit of information, and formulating Shannon's source coding theorem.
```

Provenance: clean `state/` (0 hits on iter 1), both roles pinned to Gemini (`gemini-3.5-flash-lite` per gateway log), run `cff6d28f`. This replaces the earlier mismatched evidence (Feb-26 JSON).

### Q2 Tokyo

Result JSON: [result_run_610c1a79.json](submission_results/result_run_610c1a79.json)

```text
memory.read iter 1 -> 0 hit(s)
perception iter 1: 3 goals, 0 done
action web_search({"query": "family friendly things to do in Tokyo", "max_results": 5})
action -> artifact #art:03a012a9
memory.read iter 2 -> 0 hit(s)
perception iter 2: 3 goals, 0 done
decision answer: Family-friendly things to do in Tokyo include visiting Tokyo Disneyland, Ueno Zoo, Tokyo Skytree, Miraikan (National Mus
memory.read iter 3 -> 0 hit(s)
perception iter 3: 3 goals, 1 done
action web_search({"query": "Tokyo weather Saturday forecast", "max_results": 5})
memory.read iter 4 -> 0 hit(s)
perception iter 4: 3 goals, 2 done
action fetch_url({"url": "https://weatherin.org/japan/tokyo/tokyo/weekend"})
action -> artifact #art:f7f08969
memory.read iter 5 -> 0 hit(s)
perception iter 5: 3 goals, 2 done
decision answer: Given that the weather forecast for Tokyo this weekend indicates clear skies and 0% chance of rain with temperatures rea
memory.read iter 6 -> 0 hit(s)
perception iter 6: 3 goals, 3 done
memory.write -> 3 item(s) saved
done in 6 iterations (93.24s)
final: Given that the weather forecast for Tokyo this weekend indicates clear skies and 0% chance of rain with temperatures reaching around 25ºC to 26ºC, outdoor activities such as visiting a park or sightseeing are most appropriate.
```

Provenance: clean `state/`, both roles pinned to Gemini, run `610c1a79`. Forecast conditions stated explicitly; choice tied to them.

### Q3 Mom (run 1 + run 2, shared state)

Run 1 result JSON: [result_run_63da1737.json](submission_results/result_run_63da1737.json)

Run 2 result JSON: [result_run_8177768f.json](submission_results/result_run_8177768f.json)

```text
Run 1:
memory.read iter 1 -> 0 hit(s)
perception iter 1: 3 goals, 0 done
decision answer: Mom's birthday is saved for May 15, 2026.
memory.read iter 2 -> 0 hit(s)
perception iter 2: 3 goals, 1 done
action get_time({"timezone": "UTC"})
memory.read iter 3 -> 0 hit(s)
perception iter 3: 3 goals, 1 done
action create_file({"content": "Reminder: Two weeks before May 15, 2026 (May 1, 2026) is Mom's birthday.", "path": "calendar_reminders.txt"})
memory.read iter 4 -> 0 hit(s)
perception iter 4: 3 goals, 2 done
action update_file({"content": "Reminder: Two weeks before May 15, 2026 (May 1, 2026) is Mom's birthday.\nReminder: May 15, 2026 is Mom's b)
memory.read iter 5 -> 0 hit(s)
perception iter 5: 3 goals, 3 done
memory.write -> 4 item(s) saved
done in 5 iterations (38.45s)
final: Mom's birthday is saved for May 15, 2026.
```

`sandbox/calendar_reminders.txt` after run 1:

```text
Reminder: Two weeks before May 15, 2026 (May 1, 2026) is Mom's birthday.
Reminder: May 15, 2026 is Mom's birthday.
```

```text
Run 2:
memory.read iter 1 -> 1 hit(s)
perception iter 1: 1 goals, 0 done
decision answer: Mom's birthday is 2026-05-15.
memory.read iter 2 -> 1 goals, 1 done
memory.write -> 1 item(s) saved
done in 2 iterations (15.31s)
final: Mom's birthday is 2026-05-15.
```

Note: the run generates reminder details; the Assignment 6 tool catalogue does
not include an external calendar integration.

### Q4 Asyncio

Result JSON: [result_run_31a0bf6e.json](submission_results/result_run_31a0bf6e.json)

```text
memory.read iter 1 -> 0 hit(s)
perception iter 1: 5 goals, 0 done
action web_search({"query": "Python asyncio best practices"})
memory.read iter 2 -> 0 hit(s)
perception iter 2: 5 goals, 0 done
decision answer: Python asyncio best practices search completed successfully with relevant sources found.
memory.read iter 3 -> 0 hit(s)
perception iter 3: 5 goals, 1 done
action fetch_url({"url": "https://realpython.com/async-io-python/"})
action -> artifact #art:de2352d9
memory.read iter 4 -> 0 hit(s)
perception iter 4: 5 goals, 2 done
action web_search({"query": "Python asyncio best practices"})
memory.read iter 5 -> 0 hit(s)
perception iter 5: 5 goals, 2 done
action fetch_url({"url": "https://docs.python.org/3/library/asyncio.html"})
action -> artifact #art:1027af3b
memory.read iter 6 -> 0 hit(s)
perception iter 6: 5 goals, 3 done
action fetch_url({"url": "https://dev.to/shehzan/mastering-python-async-patterns-a-complete-guide-to-asyncio-in-2026-10o6"})
action -> artifact #art:0007f2ae
memory.read iter 7 -> 0 hit(s)
perception iter 7: 5 goals, 4 done
decision answer: 1. Use asyncio for IO-bound and structured network code.
2. Understand async fundamentals and use proper async patterns with the async/await syntax.
perception synthesis verified
memory.write -> 3 item(s) saved
done in 7 iterations (64.85s)
final: 1. Use asyncio for IO-bound and structured network code.
2. Understand async fundamentals and use proper async patterns with the async/await syntax.
```

Provenance: clean `state/`, both roles pinned to Gemini, run `31a0bf6e`. Three distinct sources fetched; synthesis is thin (2 items) — the cost of the two-source-support rule, accepted.

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
