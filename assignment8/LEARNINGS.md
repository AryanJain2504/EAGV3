# Learning Memory — Assignment 8 (Session 8: DAG-Based Multi-Agent Orchestrator)

Domain: **Product Research & Comparison (e-commerce)**.
All five rubric parts pass. Evidence: 37 sessions under `code/state/sessions/`,
tests: 22 passed, protected files untouched.
Companion document: `INTERVIEW_PREP.md` (pitches, STAR stories, 25+ Q&A drills).

This file is the full technical record: what we built, every step of how,
every mechanism explained from first principles, and every failure with its
root cause. Nothing here is asserted without an artifact you can open.

**How to read it:**
- §2 = the system on one page (3 diagrams)
- §3 = chronology — literally *everything we did*, including discarded runs
- §4 = deep mechanics — what I must be able to explain cold in an interview
- §5 = incident ledger — 20 case studies, 7 fields each
- §6–7 = decisions and evaluation methodology
- §8 = pre-build code-review findings (C1–C3, W1–W4)
- §9–12 = principles, glossary, source map, appendix tables

---

## 1. What this project is, in one paragraph

Session 7 ran an agent as one loop: perceive → decide → act, repeating up to
20 times with the whole history in every prompt. Session 8 replaces that loop
with a **directed acyclic graph of small skills**. A Planner reads the user's
question and emits JSON describing the work (nodes + dependencies). An
Executor runs every node whose inputs are ready — concurrently — persists
every step to disk, and returns the terminal formatter's answer. The same
model that wrote the graph can be graded by a Critic node inside it; if the
Critic fails the output, a recovery Planner is spliced into the graph with a
failure report and must produce a corrected answer. Because state is on disk
after every batch, the process can be SIGKILLed mid-run and resumed. The
project proves this architecture on five base queries plus five designed
demonstrations (parallelism, critic pass/fail, a sandbox-verified coder, and
a brand-new skill added with zero orchestrator changes).

## 2. The system on one page

### Diagram A — the execution loop

```
             USER_QUERY
                 │
        ┌────────▼────────┐   emits JSON: {rationale, nodes:[{skill,inputs,metadata}]}
        │  n:1  planner   │──────────────────────────────────────────────┐
        └────────┬────────┘                                              │
                 │ extend_from(): resolve inputs → add nodes + edges     │
                 ▼                                                       ▼
   ┌──────────────────────────────── ready_nodes() ────────────────────────────┐
   │ a node is ready iff ALL predecessors are status ∈ {complete, skipped}     │
   └───────────────────────────────────────┬───────────────────────────────────┘
                 batch of ready nodes       │        (independent nodes together)
        ┌──────────────┬──────────────┐     │
        ▼              ▼              ▼     │
  n:2 researcher  n:3 researcher  n:4 researcher     ← asyncio.gather (parallel layer)
        └──────┬───────┴──────┬───────┘
               ▼              ▼
        n:5 coder ──internal_successors──▶ n:7 sandbox_executor   (yaml:82)
               │                                    │
               └──────────▶ n:6 formatter ◀─────────┘   (TERMINAL → final answer)
   per batch: mark running → write_graph → gather → write_node(complete) → extend
   loop until no ready and no running  (cap: MAX_NODES = 60, flow.py:32)
```

### Diagram B — the Critic splice (Part 3's machinery)

```
first plan (planner-emitted critic):

  n:2 distiller ──▶ n:3 critic ──▶ n:4 formatter
                          │
            verdict JSON: {"verdict":"fail","rationale":"...missing keys..."}
                          │
        handle_critic_verdict() (recovery.py:97):
          • default verdict is PASS unless output says "fail"      (:108)
          • target = critic's input (n:2)   — derived from graph    (:114)
          • child   = critic's successor (n:4) — derived from graph (:118)
          • mark child SKIPPED  (skipped counts as satisfied for ready_nodes)
          • first fail for this target → splice:
                n:5 planner  inputs=[USER_QUERY]
                metadata.failure_report = "critic failed target=.. rationale=.."
          • second fail for same target → CAP: branch skipped, warn  (:131)

recovery plan (planner prompt = planner.md + USER_QUERY + FAILURE block):

  n:5 planner ──▶ n:6 researcher(fetch what rationale says is missing)
                       ──▶ n:7 formatter(corrected answer) ──▶ n:8 critic(pass)
```

### Diagram C — the gateway request path

```
flow.py _run_one ──▶ HTTP /v1/chat {agent, session, prompt, temperature}
                          │
            AGENT_ROUTING.yaml pin?  (main.py:302)
             ├── pinned  → explicit_override=True
             │      └── if provider cooling: WAIT (loop, 30s deadline,
             │          sleep min(remaining+0.05, 5))   main.py:344-354
             └── unpinned → pick() from LLM_ORDER
                    └── cooling provider is SKIPPED, next provider tried
                          │
        worker loop: t0=start, record(0) → provider.chat → latency = now - t0
        on ProviderError: backoff table (main.py:243) e.g. 429-quota → 60s
        mark_unavailable(secs) → rotate candidates → retry; exhausted → 503 (:542)
                          │
        gateway_v8.db.calls: provider, agent, session, status, latency_ms,
        attempted[] — the debug log that solved the biggest incident
```

**The meta-shift (worth saying out loud in an interview):** in Session 7 the
model sat *inside* the loop and chose the next action each turn. Here the
model *writes the program* (the JSON graph) and a deterministic executor runs
it. Prompt engineering moved from "what to do next" to "how to decompose and
what invariants to state"; everything else became ordinary software.

## 3. Complete build chronology — everything we did

### Phase 0 — environment (before any feature work)
1. Copied `eagV3/.env` → `assignment8/.env` (GEMINI_API_KEY, OLLAMA_*, model name).
2. `uv sync` failed on **Python 3.15** (shapely/crawl4ai build needs numpy headers)
   → pinned `uv sync --python 3.13`. [Incident 2]
3. `playwright install chromium` (fetch_url needs a real browser).
4. Baseline: `pytest tests/test_recovery.py` → **22 passed** (11 functions,
   several parametrized). This number is our invariant.
5. Smoke: hello query ran; `fetch_url` returned HTTP 200 markdown.

### Pre-build code review (findings detailed in §8)
6. Read `flow.py`, `recovery.py`, `skills.py`, gateway. Findings:
   **C1** auto-critic can never fire; **C2** critic can't fail on *absent*
   fields by default; **C3** recovery planner is blind to the failed node's
   inputs; **W1–W4** flush/latency/pin/metadata weaknesses.
7. Resolved three assignment loose ends with evidence, not guesses:
   - **LE1** = the critic pair must use *two review texts, one instruction*.
   - **LE2** = do not preemptively edit planner.md for coder emission; let
     runs prove it's needed (they did not — the existing rule fired).
   - **LE3** = the working critic path is *planner-emitted* (auto path dead);
     the fail condition must be embedded in the constraint wording.

### Phase 1 — Part 4: fill `prompts/coder.md` (83 lines)
8. Authored: when to emit / not emit, procedure, sandbox constraints
   (temp dir, no network assumptions, timeout), output schema
   `{"code","rationale"}` with no fences, definition-normalization rule
   ("normalize units/definitions before computing"), 2 worked examples.

### Phase 2 — Part 5: new skill `review_sentiment`
9. `agent_config.yaml:87` entry: prompt file, `tools_allowed: []`,
   temperature 0.3, max_tokens 800.
10. `prompts/review_sentiment.md`: label ∈ {positive, neutral, negative},
    calibrated confidence bands, grounded rationale, 3 examples.
11. `prompts/planner.md:12` — added the skill to the available-skills list
    (the assignment explicitly says "update the planner").

### Model selection journey (own incident) [Incident 3]
12. `gemini-3.1-flash-lite` → 503 "high demand".
13. `gemini-2.5-flash-lite` → **404: retired** ("use gemini-3.5-flash-lite",
    not available in this project).
14. `gemini-2.5-flash` → worked, then Google **429**:
    `generate_content_free_tier_requests, limit: 5` → gateway 60 s backoff
    (main.py:248) → every pinned node 503'd during bursts.
15. Final: `GEMINI_MODEL=gemini-flash-lite-latest` (11/11 OK).
    Gateway reads `.env` at import → **reload = kill port 8108**, it restarts
    on the next run.

### Planner policy evolution (`prompts/planner.md`, all sanctioned edits)
16. **Distiller rule** (:44) — structured-field extraction only for single
    source; not on comparisons. Added after run `s8-3a16375a` (A answered
    without a distiller).
17. **Coder rule** (:51) — compute/compare/rank ⇒ coder before formatter.
    Added after `s8-7875be80` (I answered arithmetic from prose).
18. **Fail-fast rule** (:64) — provably unanswerable ⇒ formatter only.
    Added after J's 4-node and 3-node attempts [Incident 7].
19. **FAILURE recovery rule** (:79) — a `critic failed` report is
    *correctable*: obtain what the rationale says is missing; don't restate
    or fail fast. Added after the critic-fail run fail-fasted [Incident 10].

### Phase 3 — the five base queries (with every discarded iteration)
20. **hello**: smoke runs `11e44f10`, `511eba82`, `d0b06d36`, `e24aac7f`,
    `e4610317`, `f46f0922` (+ `69d50cbc` where the formatter hit a 502 and
    the fallback path returned an answer). Final: `s8-afb7dc9b` — 2 nodes,
    9.68 s wall. Bound says "<3 s" — impossible under the 4 s cooldown
    arithmetic; documented honestly. [Incident 5]
21. **A (Shannon)**: `3a16375a` (3 nodes, no distiller), `c7e8d7a4` (3),
    `9e057a69` (recovery planner hit 503 → `plan_recovery` classified
    *transient → skip*, run degraded) → after distiller rule: **`3968623e`**
    (4 nodes exactly, correct dates + three contributions). PASS.
22. **I (three cities)**: four discarded runs —
    - `aaa17c65`: memory hits said "exact lookup available" → planner emitted
      **formatter only** → answer: "I cannot answer… retriever nodes did not
      provide" [Incident 6],
    - `7875be80`: 5 nodes, no coder (formatter did the arithmetic),
    - `ee7f0fad`: coder ran on wrong researcher numbers → wrong closest pair,
    - `6bffd684`: coder honest but data had 3 London definitions → hedged
      "messy" answer.
    → after memory reset + coder rule: **`0a9d2171`** (7 nodes exactly,
    closest = Berlin–Paris, sandbox-verified diff **1,640,756**). PASS.
23. **J (/nonexistent/path.txt)**: `411544ee` + `c85943e2` (4 nodes — planner
    tried *coder+sandbox* to open the file; it ran try/except FileNotFoundError),
    `41fd57d8`/`c9e7c8ca`/`d8867ce4` (3 nodes — researcher attempts) →
    after fail-fast rule: **`246960ad`** (exactly 2 nodes: planner →
    formatter, zero tools). PASS.
24. **K (SIGKILL + resume)**: kill-watcher failed twice [Incidents 13–14]
    (`fa5d2995` and `527e7417` both completed untouched), third attempt
    caught the parallel layer: **`5c5d72a6`** —
    - kill at poll 11: disk = planner complete, **3 researchers running**,
      coder+formatter pending;
    - `uv run flow.py --resume s8-5c5d72a6`: running→pending reset, chain
      re-ran; the coder emitted a bad index (`KeyError: 1`) → sandbox
      exit 1 → upstream_failure → recovery re-plan (n:8…n:13) → final
      answer + sandbox stdout `Fastest growing city: Kinshasa (5.13%)`
      [Incident 15]. Total: 14 nodes, 7 s + 111 s.

### Phase 4 — Part 2: parallel proof
25. Wrote `scripts/check_parallel.py` (read-only): clusters overlapping
    `[started_at, completed_at]` intervals, prints wall vs max vs sum.
26. Query "compare price/rating/warranty of iPhone 16, S25, Pixel 10":
    **`s8-4d752709`** — 3 researchers concurrent: **wall 51.62 s = max
    branch; sum 142.84 s**; all end on the same gather barrier (57.60);
    starts staggered 5.98/10.09/13.89 by the 4 s cooldown. Also produced
    the clean coder→sandbox demo for Part 4 (exit 0).

### Phase 5 — Part 3: the critic pair
27. **Run 1 (pass attempt)** `s8-826cd93e`: reviews *state $799* → distiller
    extracts all three keys → critic **pass** → formatter with
    `{"price_usd":"799","average_rating":"4.33","sentiment":"positive"}`.
28. **Run 2 (fail) — three iterations:**
    - `c2c71b5a`: critic failed correctly, but the recovery planner
      **fail-fasted** ("data fundamentally absent from the reviews") and
      emitted an explanatory formatter — no corrected answer [Incident 10].
    - `4addf5ce`: adding "look it up from the web" to the *query* made the
      first plan fetch preemptively → critic passed → **no fail to demo**
      [Incident 11].
    - Fix: FAILURE recovery rule in planner.md (triggered only when a
      FAILURE block exists, never on the first plan) + star ratings in the
      texts so only price is missing: **`9d227709`** — distiller `{}` →
      critic **fail** (rationale: missing required keys) → recovery planner
      → **researcher fetches price** → formatter
      `{"price_usd":799,"average_rating":4.33,"sentiment":"positive"}` →
      re-emitted critic **pass**. Child n:4 marked skipped; graph shows the
      whole splice. PASS.

### Phase 7 — Part 5: getting the new skill right (four iterations)
29. `537a1227`: planner emitted 3× review_sentiment with **empty question**
    → all `neutral/0.0 "No review was provided"` (prompt really had no
    review). [Incident 16]
30. `967c5e7a`: question correctly scoped, but my prompt rule said "if
    INPUTS are empty… report no review" — fan-out nodes *always* have empty
    INPUTS → flash-lite obeyed the rule over the QUESTION. Fixed the rule.
    [Incident 17]
31. `8d92b3b3`: examples used `Classify sentiment: "..."` format while the
    planner emits bare text → model treated the input as a 4th example.
    Rewrote examples to the real rendered shape. [Incident 18]
32. `0c1598ac`: still wrong. Inspected the node JSON `result.provider`
    → **`ollama`**. The new skill is *unpinned*; `LLM_ORDER=ollama,gemini`
    routes it to a local 1.7B qwen. The prompt was never the (main) problem.
    [Incident 19 — the big one]
33. Tried `LLM_ORDER=gemini,ollama` first (`06aacbc8`) — **still ollama**:
    unpinned picks *skip* a cooling gemini and fall through. Root fix: add
    one line to `gateway/agent_routing.yaml` — `review_sentiment: gemini`
    — using the file's own documented extension point ("Add a tag: provider
    line… No code change"). Pinned requests *wait out* cooldown.
34. **`s8-ee1437f7`** — 5 nodes, 3 classifiers concurrent on `provider=gemini`:
    **positive 0.98 / negative 0.95 / neutral 0.88**, all correct with
    grounded rationales; wall 9.23 s = max, sum 15.79 s. PASS.

### Phase 8 — audit
35. `pytest` → 22 passed. mtime audit: `flow.py`/`skills.py`/`recovery.py`/
    gateway `.py` original (28 May / 2 Jun); other prompt files untouched.
    Changed: 3 prompts + 2 yamls + `.env` (+ runtime db/logs).
36. Wrote `README.md` (big-font per-part results, honest notes) and
    `DEMO.md` (13-minute video runbook using `replay.py` — zero-quota replays).

## 4. Deep mechanics — explainable cold

### 4.1 The executor loop and the gather barrier
```python
while True:
    ready = graph.ready_nodes()                    # all preds ∈ {complete, skipped}
    if not ready and not graph.has_running(): break
    if executed + len(ready) > MAX_NODES: break    # flow.py:226, cap 60
    for nid in ready: mark(nid, "running"); write_graph()      # :231-232
    outcomes = await gather(*[_run_one(nid) for nid in ready]) # :234
    for nid, result, prompt in outcomes:           # batch flush
        store.write_node(... started_at=now-result.elapsed_s, completed_at=now)  # :241
        graph.mark(complete|failed); verdict handling; extend_from;  # :254-265
    write_graph()                                  # :287
```
**Why wall = max(branch), not sum:** independent nodes are coroutines under
one `gather`; the executor doesn't proceed until *all* return. The barrier is
`gather`'s semantics. Observed: three researchers all finish at t=57.60 s
despite starting at 5.98/10.09/13.89 (staggered by the gateway's 4 s
cooldown — serialized *starts*, fused *finishes*). Layer wall 51.62 s = the
slowest branch; the sum (142.84 s) is what a sequential run would pay.
Serial overhead (planner before, coder/formatter after) is why the speedup
is less than N× — same observation the session text makes.

### 4.2 Persistence and resume — what is and isn't durable
- `graph.json` written at dispatch (:232) and after each batch (:287);
  per-node `nodes/n_0XX.json` written at **running** (pre-dispatch, :314) and
  at **completion** (batch flush, :241).
- **Consequence (W1):** a SIGKILL mid-gather leaves *every* member of the
  batch as `running` on disk — completions in flight are lost. This is why
  our K kill state shows 3-running instead of the brief's "2 complete +
  1 running". A per-outcome flush would fix it; that's Executor code.
- **Resume** (`--resume <sid>`, :332): reload graph (pickled full state),
  rebuild `_counter` from max `n:X` (:186), flip `running → pending`
  (:189-191), re-read the query from disk (:192). Completed nodes — results,
  timestamps — are preserved untouched. Idempotency is *node-level*: a killed
  node re-executes from scratch (its tool loop restarts; the session text
  documents mid-tool resume as DEFERRED).
- `formatter_answer` fallback (:289): if no formatter succeeded, take the
  newest complete node's raw output — the run that had a 502'd formatter
  still returned an answer through this path.
- **Not in per-node JSON (W4):** `metadata` (labels, questions,
  failure_report) lives only in `graph.json`. Evidence tooling must join the
  two files — first version of my checker silently assumed otherwise.

### 4.3 Critic and recovery — the full decision table
`plan_recovery(error_text, failed_skill)` (recovery.py:54):
| condition | action | why |
|---|---|---|
| transient (503/502/504/timeout/connection) | **skip** | gateway already retried+backed off; re-planning would hammer a dying provider |
| validation_error (malformed NodeSpec) | **skip** | prompt bug, not runtime — fix offline |
| failed_skill == planner | **skip** | never re-plan a planner (loop risk) |
| upstream_failure, other skill | **replan** | queue a recovery planner with `failure_report` |

Critic path (`handle_critic_verdict`, :97) runs *instead of* extend_from:
verdict defaults to **pass** (:108); on fail, target/child come from metadata
if present (auto-inserted shape) else derived from graph structure
(inputs/successors) — that derivation is what makes planner-emitted critics
work. Child → `skipped` (and `ready_nodes` treats skipped as satisfied,
flow.py:67, so the *recovery* branch proceeds). Per-target cap = 1
(`recovered_branches` dict, :122): a second fail on the same target is
capped and warned, never loops. **C3**: the recovery planner receives only
`USER_QUERY` + the FAILURE string — *not* the distiller's output — so the
rationale text is its only description of what's missing. That's why the
FAILURE rule says "obtain what the rationale says is missing".

### 4.4 Gateway rate limiting — three different clocks
1. **Cooldown** (`router.py:13` LIMITS: gemini `rpm 15, cooldown 4`): the
   last-call check at `router.py:68` returns `False, "cooldown (x.xs)"` until
   4 s pass. This spaces consecutive Gemini calls — it's why parallel starts
   stagger and why hello (2 calls) can't beat ~4 s + model time.
2. **RPM window**: `len(calls_minute) >= rpm` blocks (15/min for gemini).
3. **429 backoff** (`main.py:243`): quota → **60 s** `mark_unavailable`;
   daily → 3600 s; 5xx → 20 s; 408 → 10 s; 401/403/404 with a model override
   → 0 (don't blackball the provider for a model-name typo). During a 60 s
   backoff *every pinned node 503s* — observed live during the model journey.
4. **The asymmetry (W3):** explicit/pin requests **wait** for cooldown
   (main.py:344-354, 30 s deadline); unpinned `pick()` **skips** a cooling
   provider and falls to the next. So an unpinned skill silently migrates to
   whatever provider is idle — ours landed on ollama. Pinning isn't
   cosmetic; it decides *which model actually thinks for you*.
5. **latency_ms (W2):** `t0` is taken *after* the wait loop (:363), so logged
   latency ≈ model-call time and **excludes** queueing the client actually
   paid. Dashboards understate user-perceived latency; the client's
   wall-clock is wait + call.
6. Router-pool vs worker pool: pins bypass classification entirely
   (`explicit_override` before `_classify_tier`), which is why cost-by-agent
   shows zero router spend for pinned skills — matches the session text.

### 4.5 The prompt rendering contract (`render_prompt`, skills.py:147)
Every skill's prompt = `[template] + [USER_QUERY?] + [QUESTION?] + [FAILURE?]
+ [MEMORY HITS?] + INPUTS[:20k]`.
- `USER_QUERY` appears **only if** the planner wired it into inputs (:158-164)
  — the fan-out isolation rule. Workers get their slice via `QUESTION`
  (from `metadata.question`, :169).
- `FAILURE` block (:171) is what makes a recovery planner a *recovery*.
- `MEMORY HITS` (:177): read **once** per session (flow.py:204) and threaded
  into every skill — the S7 contract "every cognitive role sees memory".
- Design lesson learned twice: **prompt rules are policy, trigger conditions
  are design.** "Look it up from the web" in the *query* changes plan #1;
  the same instruction in `planner.md` under `If FAILURE appears` changes
  only plan #2. Same sentence, different iteration executes it.

### 4.6 Memory — and why it must be reset between eval runs
`memory.read(query)` at session start returns FAISS hits (descriptor text +
2,000-char preview). Descriptors overstate ("exact population lookup…"), and
the raw value of an indexed user query is the query itself. With a warm index
the planner shortcut to formatter-from-hits and the DAG collapsed
(`s8-aaa17c65`). **Protocol we now run before every sensitive test:** back up
`memory.json`, `index.faiss`, `index_ids.json` → `state/backups/`, write `[]`.
Controlling initial state *is* part of the experiment, not cleanup.

### 4.7 Sandbox — why computation must execute
`coder.internal_successors: [sandbox_executor]` (yaml:82) attaches the
sandbox automatically. The sandbox runs coder Python in a temp dir with
timeout/output caps, returns `exit_code/stdout/stderr`. Two live uses:
Part 4's verified comparison (exit 0) and K's `KeyError` (exit 1 →
upstream_failure → recovery). The formatter can narrate a number; only the
sandbox can *check* it. Also: the session text is explicit that this sandbox
is a usability boundary, not a security boundary — honest to repeat.

### 4.8 Skills as config (the Part 5 contract)
A skill = prompt file + tools + temperature (session text: "what makes one
skill different is the text in its prompt file"). Adding one took exactly:
yaml entry + prompt file + one line in the planner's skill list + one pin
line. Zero lines in `flow.py`/`skills.py`. The dispatcher
(`run_skill` → `render_prompt` → gateway) is identical for all skills — no
special case anywhere (the only special case in the codebase is
sandbox_executor's dispatch, which pre-exists us).

## 5. Incident ledger — 20 case studies

Format: **Expected → Observed → Evidence → Root cause → Fix → Rejected
alternative → Principle.**

**1. Auto-critic is dead code (finding C1)**
- Expected: distiller's `critic: true` (yaml:51) inserts a critic before its
  children. Observed: never fires.
- Evidence: `flow.py:155` `if src_def.critic and added:` — after a distiller
  completes, `result.successors=[]` and there are no yaml
  `internal_successors` → `added=[]` → branch skipped. Reference
  `eagV3_ref`/Session8 uses an edge-based condition.
- Fix: none (Executor is protected) — Part 3 uses the planner-emitted critic
  path, which works. Rejected: patching flow.py (reportable, out of scope).
- Principle: read the exact branch before designing around a feature; know
  which of two paths is live.

**2. Python 3.15 broke the build**
- Observed: `uv sync` fails compiling shapely/crawl4ai (numpy headers).
- Fix: `uv sync --python 3.13`; documented. Rejected: patching dependencies.
- Principle: pin the interpreter in the README of anything meant to be rerun.

**3. Model SKU archaeology (503 → 404 → 429 → settle)**
- Observed: `gemini-3.1-flash-lite` 503; `gemini-2.5-flash-lite` 404
  *retired* (message names 3.5, unavailable here); `gemini-2.5-flash` 429
  quota `limit: 5` → gateway 60 s backoff cascaded into 503s for every
  pinned node.
- Evidence: full JSON error bodies; `gateway_v8.db` error rows;
  `_backoff_for` main.py:248 (`"quota" → 60`).
- Fix: `gemini-flash-lite-latest`. Rejected: tuning retries (quota isn't a
  retryable condition), removing pins.
- Principle: read the whole error body; quota errors are configuration
  problems, not code bugs.

**4. During a 429 backoff the whole run stalls**
- Observed: 60 s where every node fails 503 → nodes classify `transient` →
  `skip` → degraded answers.
- Evidence: `9e057a69` recovery planner failed 503 → skipped; `69d50cbc`
  formatter 502 → fallback path answered.
- Fix: wait out backoff before sensitive runs; the classifier *behaved
  correctly* (skip ≠ replan on transient).
- Principle: a failure-handling system doing its job can still produce a
  bad *product*; distinguish "handled" from "acceptable".

**5. hello "<3 s" bound is arithmetically impossible**
- Observed: 9.68 s wall (4.4 + 3.5 nodes + startup). Bound: <3 s.
- Evidence: 4 s cooldown between the two Gemini calls (router.py:13);
  reference's own numbers (2.18 + 1.1) already >3.
- Fix: documented honestly in README + LEARNINGS. Rejected: cherry-picking a
  fast run; gaming the measurement.
- Principle: check a bound against the system's constants *before* trying to
  hit it — sometimes the published number is the bug.

**6. Memory pollution collapsed query I to two nodes**
- Observed: `aaa17c65` planner rationale: *"Memory hits contain the exact
  population lookup…"* → formatter-only graph → answer: "I cannot answer…".
- Root cause: warm FAISS index; descriptor overstates; hits rendered into
  every prompt (skills.py:177).
- Fix: reset protocol (§4.6). Rejected: changing planner.md's memory rule
  (the rule is right for real knowledge; our test state was wrong).
- Principle: in systems where memory is ambient, tests must fix the memory
  state — or you're testing yesterday, not today.

**7. Query J attempted the impossible (4 → 3 → 2 nodes)**
- Observed: `411544ee` planner: *"Attempt to read the file using
  coder/sandbox_executor"* → coder wrote try/except, sandbox exit 0 with
  FileNotFoundError text; later runs tried researchers (3 nodes); never
  satisfied the "2 nodes, no tool" bound.
- Fix: fail-fast rule (planner.md:64) → `246960ad`: planner → formatter
  only. Rejected: telling it the file's contents (cheating); editing the
  query (must be verbatim).
- Principle: constraints belong in the policy layer (planner prompt), not
  sprinkled per query.

**8. Query A skipped the distiller (3 nodes)**
- Observed: `3a16375a` researcher → formatter, answers paraphrased — bound is
  4 nodes with structured extraction.
- Fix: distiller rule (planner.md:44) + explicit "not on comparisons" to
  avoid over-triggering. Result: `3968623e`, exactly 4 nodes.
- Principle: state both sides of a rule (when *and* when not).

**9. Query I did arithmetic in the formatter**
- Observed: `7875be80` (5 nodes, no coder); `ee7f0fad` coder present but
  fed wrong researcher numbers → wrong answer; `6bffd684` honest but hedged.
- Fix: coder rule (planner.md:51) + memory reset. Result: `0a9d2171`.
- Two lessons: (a) generation quality caps downstream math regardless of
  rules — the coder rule moves the risk to a *verifiable* place (sandbox);
  (b) "closest" needs a definition — the shipped run's formatter states the
  boundary assumption (city-proper vs metro) explicitly.
- Principle: make the risky step executable, then verify it.

**10. Critic-fail recovery fail-fasted (no corrected answer)**
- Observed: `c2c71b5a` critic failed correctly (rationale: keys absent) →
  recovery planner (1.0 s) read the FAILURE as *unobtainable* → explanatory
  formatter. Rubric wants a corrected answer.
- Root cause: my own fail-fast rule (added for J) had no exception for
  critic reports; recovery planner sees only USER_QUERY + FAILURE (C3) and
  the query said "extract from the reviews below" → "not in reviews" =
  impossible to it.
- Fix: FAILURE rule (planner.md:79): critic reports are *correctable* —
  obtain what the rationale says is missing; fail-fast reserved for the
  original query. Rejected: putting "fetch if missing" in the query (see #11);
  weakening the fail-fast rule globally (would break J).
- Principle: every rule interacts with every other rule — when adding policy,
  audit which existing paths it now covers.

**11. "Look it up from the web" in the query killed the fail demo**
- Observed: `4addf5ce` first plan fetched the price *preemptively*
  (researcher node) → distiller had all keys → critic passed → no fail run.
- Root cause: the instruction was in USER_QUERY, so plan #1 obeyed it.
- Fix: move the fetch policy into the FAILURE-only rule (#10). Rejected:
  hand-editing the graph (would fake the demo).
- Principle: *where* an instruction lives (query vs prompt vs failure block)
  determines which iteration executes it — trigger conditions are the design.

**12. The critic cannot fail on absence (finding C2)**
- Observed: critic.md:11 only fails on *"missing fields the input clearly
  contained"* — if the input never had the price, a truthful critic passes.
- Fix: embed the fail condition in the constraint the planner repeats into
  the critic's QUESTION: "All three keys REQUIRED — a missing required key
  is a FAIL." Then absence *is* the violated property.
- Rejected: editing critic.md (shared file, untouched by design).
- Principle: when the checker's default semantics don't cover your case,
  change the *specification you feed it*, not the checker.

**13. Kill-watcher #1: buffered stdout**
- Observed: watcher grepped the log for `session s8-…` after 6 s — never
  matched; run completed untouched (`fa5d2995`).
- Root cause: Python buffers stdout when redirected; header not flushed yet.
- Fix: poll `graph.json` instead. Rejected: `PYTHONUNBUFFERED` (still racing).
- Principle: don't build evidence tooling on another process's buffered
  stdout when it's writing a file anyway.

**14. Kill-watcher #2: wrong schema, swallowed exception**
- Observed: poll used `graph['nodes'].items()` — nodes are a **list** in
  node-link JSON → AttributeError → my `2>/dev/null` ate it → empty status →
  no kill → run completed (`527e7417`).
- Fix: list-aware parser + validate against a real graph.json first; check
  the process, not just the condition (`killed=1` flag in the script).
- Principle: never suppress stderr in a control loop; validate parsers
  against real fixtures before trusting them.

**15. K's coder crashed — and the system healed (in one session)**
- Observed: coder emitted `fastest[1][1]["growth_rate"]` → `KeyError: 1` →
  sandbox exit 1 → `upstream_failure` → replan (n:8) → new chain → answer +
  sandbox line `Fastest growing city: Kinshasa (5.13%)`.
- Why it matters: kill→resume *and* failure→recovery composed in the same
  run; the formatter never got to narrate a broken claim.
- Rejected: rerunning K for a cleaner 7-node story — kept the messy truth,
  documented it (a grader can see the machinery work).
- Principle: verification layers earn their cost exactly once — the first
  time they catch something real.

**16. Planner dropped the fan-out question**
- Observed: `537a1227` — three review_sentiment nodes with `inputs=[]` and
  empty question → prompts genuinely had no review → honest `0.0` answers.
- Fix: none needed in code — a re-run scoped questions correctly
  (`967c5e7a`). Principle: planner stochasticity is a *variance* source;
  re-run before rewriting prompts, but verify with the artifact.

**17. My own prompt rule contradicted fan-out (empty INPUTS)**
- Observed: question correctly scoped, still "No review provided".
- Root cause: review_sentiment.md said *"If INPUTS are empty… report no
  review"* — fan-out nodes always have empty INPUTS (workers get data via
  QUESTION). The model obeyed the stricter rule.
- Fix: explicitly state empty INPUTS alone ≠ missing review.
- Principle: rules written for the sequential case silently break the
  parallel case — test both shapes.

**18. Example-format mismatch**
- Observed: examples showed `QUESTION: Classify sentiment: "…"` but the
  renderer appends bare `QUESTION: <review text>` after the examples — the
  input looked like a fourth example without a label.
- Fix: examples rewritten to the exact rendered shape + a closing line:
  "Your own input follows the same shape."
- Principle: few-shot examples must match the *runtime* format byte-for-byte,
  or the model calibrates to the wrong distribution.

**19. THE bug: routing, not prompt (root cause of 16–18's persistence)**
- Observed: across four runs the skill returned nonsense/empty despite prompt
  fixes. The node JSON `result.provider` said **`ollama`** — every time.
- Evidence chain: unpinned skill → `LLM_ORDER=ollama,gemini` (first attempt);
  after switching to `gemini,ollama` *still* ollama — because unpinned picks
  **skip** the cooling gemini (4 s post-planner) and fall through
  (main.py:344 vs router.pick). Local `qwen3:1.7b` (cost 0.0, 17–32 s
  latencies) ignores instructions.
- Fix: one line — `review_sentiment: gemini` in `agent_routing.yaml` (the
  file's header documents this as the extension point, "No code change").
  Pinned requests *wait out* cooldown. Result: `ee1437f7` all three correct.
- Rejected alternatives: stopping ollama (fragile: unpinned would then 503
  during cooldown); editing gateway code (protected); forcing provider in the
  orchestrator client (stains "zero orchestrator modification").
- I lost hours because I edited the prompt twice before reading `provider`.
  Principle: **debug the artifact that records what actually ran before
  editing the artifact that describes what should run.**

**20. Distiller over-conservatism (accepted, not fixed)**
- Observed: with star ratings visible, distiller still returned `fields: {}`
  citing "no evidence". It never invents — but it also didn't compute 4.33
  from "5 stars/4 stars/4 stars".
- Handling: left as-is (shared prompt, untouched); the Part 3 fail run
  *depends* on conservatism; the recovery researcher fills gaps; in the pass
  run price-in-text made extraction trivial.
- Principle: know which of your system's "bugs" your tests currently rely on
  before "fixing" them.

## 6. Decisions and tradeoffs

| # | Decision | Options considered | Why this one | What would change my mind |
|---|---|---|---|---|
| 1 | Touch only prompts/yaml, never flow.py/skills.py/gateway code | fix auto-critic; per-node flush; batch dispatch | Assignment: Executor edits are "a bug"/reportable; 22 tests must pass | An explicit grant to make reportable Executor changes |
| 2 | Fail condition lives in the constraint wording | edit critic.md; code-level schema validation | critic.md shared/untouched; planner naturally repeats the constraint into QUESTION | If a future checker gets tool access to the spec |
| 3 | Critic pair = two texts, same instruction | two instructions; vary both | isolates the variable (LE1); clean A/B | Rubric explicitly asking to vary instructions |
| 4 | Memory reset before every sensitive run | disable memory; weaken planner memory rule | tests the real system with controlled initial state; zero code change | CI harness that snapshots/restores memory automatically (better version of same idea) |
| 5 | `gemini-flash-lite-latest` | retry 2.5-flash on quota; ollama-first; multi-key | only SKU that both exists and respects free-tier quota; fast upstream | Project getting 3.5-flash-lite access or paid quota |
| 6 | Pin the new skill (config line) | stop ollama; edit client; LLM_ORDER only | documented extension point; pin-wait semantics are what we need; honest to report | If pins were deprecated for config outside gateway/ |
| 7 | Read-only `check_parallel.py` | eyeball logs; instrument flow.py | evidence must be reproducible without touching the system | — |
| 8 | Ship K's messy 14-node session | rerun until clean 7-node | the messy one *proves* recovery; honesty > cosmetics | A time budget that forces a clean rerun for the video (DEMO.md replays it either way) |
| 9 | Document hello bound miss honestly | drop hello; redefine bound; pick fastest run | the bound contradicts the shared cooldown; gaming it would be fraud | A measured path to <3 s (would need gateway changes) |
| 10 | Leave distiller conservative | loosen prompt (shared file) | pass/fail runs both rely on current behavior | Own the prompt (then: add "compute simple aggregates when derivable") |
| 11 | Fail-fast rule with FAILURE exception | single global rule; no fail-fast | J needs fail-fast; critic recovery needs fetch — scoped triggers keep both | — |
| 12 | Report the pin + all prompt edits in README | stay silent, they're small | "modifications are reportable" — better to itemize than be caught | — |

## 7. Evaluation methodology

**What was proven, how, and what was deliberately discarded.**

| Part | Proof | Controlled conditions |
|---|---|---|
| 1 five queries | node counts == bounds (2/4/7/2), final answers quoted from `nodes/n_0XX.json`, K kill-state + resume log | memory reset; verbatim queries |
| 2 parallelism | `check_parallel.py` on `s8-4d752709`: interval clustering → wall 51.62 = max, sum 142.84 | timestamps read from disk, tool is read-only |
| 3 critic pair | two sessions, same instruction: verdict JSONs + skipped child + FAILURE report + corrected answer + second verdict | only the review text differs |
| 4 coder | coder.md + two sessions with sandbox exit 0 and computed stdout | formatter never does the arithmetic |
| 5 new skill | yaml+prompt+planner line+pin; `ee1437f7` three correct classifications on `provider=gemini` | same query as the three failed runs → isolates the fix |

**Discarded negative results** (kept on disk, named in §3, not shipped as
evidence): `aaa17c65`, `7875be80`, `ee7f0fad`, `6bffd684` (I); `3a16375a`,
`c7e8d7a4`, `9e057a69` (A); `411544ee`, `c85943e2`, `41fd57d8`, `c9e7c8ca`,
`d8867ce4` (J); `fa5d2995`, `527e7417` (K watcher failures); `c2c71b5a`,
`4addf5ce` (critic attempts); `537a1227`, `967c5e7a`, `8d92b3b3`,
`0c1598ac`, `06aacbc8` (sentiment attempts). Nothing was deleted; every
session ID in this file resolves to files on disk.

**Honest-bounds policy:** if a measured number disagrees with a published
bound, report both and explain the mechanism (see #5). Two such disagreements
exist (hello wall, K kill-shape), one answer difference (Kinshasa vs Lagos —
source-dependent, sandbox-verified against our source), one wall difference
(K 118 s vs ~70 s, because recovery ran).

**Invariant checks run throughout:** `pytest tests/test_recovery.py` → 22
after every change (classification strings, skip/replan table, planner-never-
replans, auto/explicit critic splice, cap-on-second-fail, pass-fast-path);
mtime audit for protected files.

## 8. Pre-build code-review findings (C1–C3, W1–W4)

*Provenance note: these labels came from the planning review; the original
one-liners were lost from working context, so each is re-derived here from
the code with line numbers. This is an honest reconstruction, not a quote.*

**C1 — auto-critic can never fire.** `flow.py:155` gates insertion on
`if src_def.critic and added:`; for a distiller completion `added` is empty
(no internal successors, `result.successors` unpopulated by the dispatcher),
so the yaml `critic: true` (agent_config.yaml:51) is inert. Impact: Part 3
must use the planner-emitted path (which works via
`handle_critic_verdict`'s structure derivation, recovery.py:114-119).

**C2 — critic fails only on present-but-wrong, not absent.**
`prompts/critic.md:11`: fail on "missing fields **the input clearly
contained**". Absence from the input is not a violation by default → any
absence-based test needs the constraint embedded in the QUESTION (§5 #12).

**C3 — recovery planner is context-blind.** Recovery node is created with
`inputs=["USER_QUERY"]` + `metadata.failure_report` only (flow.py:278-281;
critic path recovery.py:126). It never sees the failed node's inputs or
output — the rationale string is its entire picture of the gap. This is why
(1) the FAILURE rule must say *what to do* (obtain the missing thing), and
(2) rationale quality from the critic directly bounds recovery quality.

**W1 — per-batch flush loses in-flight completions.** `write_node(complete)`
runs only after `gather` returns (flow.py:234→241); running-status is written
pre-dispatch (:314). SIGKILL mid-batch ⇒ all batch members `running` on disk.
Observed directly in K.

**W2 — logged latency excludes the client's actual wait.** Cooldown wait
happens at main.py:344-354; `t0` is set after it (:363). `latency_ms`
≈ model call only; end-to-end = wait + call. Dashboards built on it
understate user-experienced latency.

**W3 — pin-wait vs unpinned-skip asymmetry.** Pinned/explicit: wait loop
(:344). Unpinned: `pick()` skips cooling providers silently. New/unpinned
skills therefore drift to idle providers — the mechanism behind incident #19.
Any skill added without a pin inherits this drift.

**W4 — per-node JSON lacks `metadata`.** NodeState fields are
node_id/skill/status/inputs/result/prompt_sent/times (flow.py:241-248);
labels/questions/failure_report persist only in `graph.json`. Evidence tools
must join both files. (Found the hard way: my first checker read metadata
from node JSONs and got nothing.)

*Bonus observation:* `formatter_answer` is last-writer-wins
(flow.py:262-265) — a recovery's second formatter overwrites the first
formatter's answer. In our K run that was desirable (recovered > broken),
but the policy is implicit, not chosen.

## 9. Principles worth repeating

1. The model writes the program; the executor runs it — keep those layers
   strictly separate.
2. Debug the artifact that records *what ran* before editing the artifact
   that describes *what should run*.
3. Prompt rules are policy; their trigger conditions are the design.
4. The same sentence in the query, the prompt, or the FAILURE block is three
   different rules.
5. Every new rule interacts with every old one — audit both directions.
6. A verification layer earns its cost the first time it catches something
   real (sandbox: exit 1 on a real KeyError).
7. Control the memory state or you are testing yesterday.
8. Bounds arithmetic before blame — check the published number against the
   system's constants.
9. Never suppress stderr in a control loop; validate parsers on real fixtures.
10. Don't build evidence on another process's buffered stdout.
11. Few-shot examples must match runtime format byte-for-byte.
12. State both halves of a rule (when to, when not to).
13. Know which current "bugs" your tests rely on before fixing them.
14. Config surfaces are APIs — read the header comment; it tells you the
    sanctioned extension point ("No code change").
15. "Handled" ≠ "acceptable": a classifier that correctly skips a transient
    failure still leaves you with a bad answer.
16. Make the risky step executable, then verify it.
17. Messy truth beats clean fiction — ship the 14-node session.
18. Itemize every modification; being caught is worse than confessing.
19. Evidence must be reproducible without touching the system (read-only
    tools).
20. Skipped nodes satisfy the graph — recovery flows *around* holes, not
    through them.

## 10. Glossary

| Term | Meaning |
|---|---|
| DAG | Directed acyclic graph; the Session 8 execution model |
| Planner-emitted vs auto-inserted critic | Two critic shapes: emitted as a graph node by the planner (works) vs auto-inserted by `extend_from` when yaml says `critic: true` (dead, C1) |
| FAILURE block | Prompt section carrying `failure_report`; only a recovery planner sees it |
| gather barrier | `asyncio.gather` resolves only when every coroutine returns → layer wall = max branch |
| pin / explicit_override | `agent_routing.yaml` mapping skill→provider; triggers cooldown *waiting* instead of provider *skipping* |
| cooldown | Per-provider min interval between calls (gemini: 4 s) enforced in `router.can_use` |
| backoff | `mark_unavailable(secs)` after 429/5xx (quota→60 s) making a provider temporarily unselectable |
| node-link JSON | NetworkX serialization where `nodes` is a *list* of dicts (the kill-watcher schema trap) |
| resume semantics | running→pending, completed preserved, counter rebuilt, query re-read |
| per-target cap | One critic-fail recovery per target branch (`recovered_branches`) |
| fail-fast | Planner rule: provably unanswerable → formatter-only graph, no tools |
| memory hits | FAISS results rendered once per session into every skill prompt |
| descriptor | Stored text summarizing a memory item — can overstate what's inside |
| sandbox | Temp-dir Python executor; usability boundary (not security), exit_code gates success |
| internal_successors | yaml field auto-attaching children (coder → sandbox_executor) |
| batch flush | write_node for a whole batch after gather; causes W1 |
| NodeState | Per-node persisted record (no metadata field — W4) |

## 11. Source map (every claim → file:line)

| Claim | Location |
|---|---|
| MAX_NODES = 60 | `code/flow.py:32` |
| add_node / counter `n:X` | `flow.py:43-47` |
| ready_nodes: preds ∈ {complete, skipped} | `flow.py:58-67` |
| extend_from; auto-critic gate (**C1**) | `flow.py:74`, `:155` |
| resume: counter rebuild, running→pending, query reload | `flow.py:186`, `:189-193` |
| memory read once + remember | `flow.py:204-210` |
| executor loop; cap check; dispatch; batch flush | `flow.py:222-241` |
| verdict handling; extend; formatter last-writer | `flow.py:254-265` |
| failure → plan_recovery → replan splice | `flow.py:266-286` |
| formatter fallback | `flow.py:289-294` |
| `_run_one` writes `running` pre-dispatch | `flow.py:314` |
| CLI `--resume` | `flow.py:332` |
| classify_failure buckets | `code/recovery.py:29-43` |
| plan_recovery decision table (skip/replan/planner-skip) | `recovery.py:54-94` |
| handle_critic_verdict: default-pass, derive target/child, skip, splice, cap | `recovery.py:108`, `:114-121`, `:122-130`, `:131-134` |
| render_prompt sections (USER_QUERY gate / QUESTION / FAILURE / HITS / INPUTS 20k) | `code/skills.py:147-178` |
| run_skill threads `question` | `skills.py:250-275` |
| distiller `critic: true` | `code/agent_config.yaml:51` |
| critic temp 0.0 | `agent_config.yaml:64` |
| sandbox auto-attach (`internal_successors`) | `agent_config.yaml:82` |
| review_sentiment entry | `agent_config.yaml:87` |
| planner rules: scoping / distiller / coder / critic / fail-fast / memory / FAILURE | `code/prompts/planner.md:28, 44, 51, 58, 64, 71, 79` |
| planner skills list includes review_sentiment | `planner.md:12` |
| critic fail criterion (**C2**) | `prompts/critic.md:11` |
| coder.md authored (Part 4) | `prompts/coder.md` (83 lines) |
| review_sentiment prompt (Part 5) | `prompts/review_sentiment.md` (58 lines) |
| gateway LIMITS (gemini rpm 15 / cooldown 4) | `gateway/router.py:13` |
| cooldown check | `router.py:68-71` |
| pin load; pin → explicit_override | `gateway/main.py:36-43`, `:302-305` |
| cooldown wait loop (30 s deadline) — **W3** | `main.py:344-354` |
| `t0` after wait — **W2** | `main.py:363` |
| `_backoff_for` table (429 quota→60, rpd→3600, 5xx→20) | `main.py:243-262` |
| failover rotation; 503 exhaustion | `main.py:521`, `:542` |
| pin file extension point | `gateway/agent_routing.yaml:13` (+ `review_sentiment: gemini` line) |
| LLM_ORDER / model | `assignment8/.env` |
| tests (classification, table, splice, cap, pass) | `code/tests/test_recovery.py` (11 functions → 22) |
| parallel evidence tool | `assignment8/scripts/check_parallel.py` |
| results / inventory / honest notes | `assignment8/README.md` |
| video runbook (replay.py) | `assignment8/DEMO.md` |

## 12. Appendix

### 12.1 All 37 sessions (purpose = what we learned/needed from it)

| Session | n | wall | shape | outcome |
|---|---|---|---|---|
| `s8-afb7dc9b` | 2 | 8s | p→f | **hello SHIPPED** (9.68s wall incl. startup) |
| `s8-11e44f10` `511eba82` `d0b06d36` `e24aac7f` `e4610317` `f46f0922` | 2 | 6-13s | p→f | hello smoke tests |
| `s8-69d50cbc` | 2 | 34s | p→f(fail) | formatter 502 → fallback path (incident 4) |
| `s8-3968623e` | 4 | 25s | p→r→d→f | **A SHIPPED** |
| `s8-3a16375a` `c7e8d7a4` | 3 | 22-30s | p→r→f | A without distiller (→ rule) |
| `s8-9e057a69` | 3 | 14s | p→r→p(fail) | recovery planner 503 → transient skip |
| `s8-0a9d2171` | 7 | 61s | p→r×3→c→f∥s | **I SHIPPED** (1,640,756) |
| `s8-aaa17c65` | 2 | 8s | p→f | memory shortcut → "cannot answer" |
| `s8-7875be80` | 5 | 45s | p→r×3→f | no coder (→ rule) |
| `s8-ee7f0fad` | 7 | 57s | p→r×3→c→f∥s | coder on wrong numbers → wrong answer |
| `s8-6bffd684` | 7 | 66s | p→r×3→c→f∥s | multi-definition data → hedged answer |
| `s8-246960ad` | 2 | 8s | p→f | **J SHIPPED** (fail-fast) |
| `s8-411544ee` `c85943e2` | 4 | 11-12s | p→c→f∥s | J attempted file via coder (→ rule) |
| `s8-41fd57d8` `c9e7c8ca` `d8867ce4` | 3 | 12s | p→r→f | J researcher attempts |
| `s8-5c5d72a6` | 14 | 122s | kill + resume + recovery | **K SHIPPED** |
| `s8-fa5d2995` `527e7417` | 7 | 52-53s | full chain | K watchers failed (run completed) |
| `s8-4d752709` | 7 | 66s | p→r×3→c→f∥s | **Part 2 SHIPPED** + coder demo |
| `s8-826cd93e` | 4 | 13s | p→d→c→f | **critic PASS shipped** |
| `s8-9d227709` | 7 | 48s | p→d→c(fail)→[skip]→p→r→f→c | **critic FAIL+recovery SHIPPED** |
| `s8-c2c71b5a` | 5 | 26s | p→d→c(fail)→p→f | recovery fail-fasted (→ rule) |
| `s8-4addf5ce` | 5 | 30s | p→r→d→c(pass)→f | web-clause preemptive fetch |
| `s8-ee1437f7` | 5 | 20s | p→rs×3→f | **Part 5 SHIPPED** (gemini, all correct) |
| `s8-537a1227` | 7 | 18s | p→rs×3→c→f∥s | empty questions (planner flake) |
| `s8-967c5e7a` `8d92b3b3` | 5 | 12-15s | p→rs×3→f | prompt-rule/format iterations (still ollama) |
| `s8-0c1598ac` `06aacbc8` | 5 | 20-38s | p→rs×3→f | **provider=ollama discovered**; LLM_ORDER alone insufficient |

### 12.2 Gateway constants (what constrains every timing claim)

| Constant | Value | Source |
|---|---|---|
| gemini cooldown | 4 s between calls | `router.py:13` |
| gemini rpm / rpd / tpm | 15 / 1000 / 250k | `router.py:13` |
| cooldown wait (pinned) | ≤30 s deadline, sleep ≤5 s | `main.py:346-354` |
| 429 quota backoff | 60 s | `main.py:248` |
| daily quota backoff | 3600 s | `main.py:249` |
| 5xx backoff | 20 s | `main.py:251` |
| node cap | 60 | `flow.py:32` |
| INPUTS render cap | 20,000 chars | `skills.py:178` |
| critic temperature | 0.0 (determinism) | `agent_config.yaml:64` |
| planner temperature | 0.4 (session default) | `agent_config.yaml` |

### 12.3 Model SKU timeline

| Model | Result | Response |
|---|---|---|
| `gemini-3.1-flash-lite` | 503 high demand | tried next |
| `gemini-2.5-flash-lite` | 404 retired (suggests 3.5, unavailable) | tried next |
| `gemini-2.5-flash` | OK → 429 quota (free tier, limit 5) | 60 s backoff storms |
| `gemini-flash-lite-latest` | **stable — shipped** | current |

### 12.4 Change inventory (protected vs touched)

Touched: `prompts/coder.md` (Part 4 mandate), `prompts/review_sentiment.md`
(Part 5 mandate), `prompts/planner.md` (Part 5 mandate + 4 policy rules),
`agent_config.yaml` (Part 5 mandate), `gateway/agent_routing.yaml` (1 config
line, documented extension point), `.env` (keys/model), new files
`scripts/check_parallel.py`, `README.md`, `DEMO.md`, this file.
Untouched: `flow.py`, `skills.py`, `recovery.py`, all gateway `.py`,
`tests/test_recovery.py`, and prompts
`critic/distiller/formatter/researcher/retriever/browser` (original mtimes).
