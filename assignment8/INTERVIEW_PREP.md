# Interview Prep — Assignment 8 (DAG-Based Multi-Agent Orchestrator)

Companion to `LEARNINGS.md` (the technical record). This file is the
performance side: pitches, STAR stories, and 35 Q&A drills with model
answers. Every claim traces back to an artifact — session IDs, file:line
references, and numbers are in §9's cheat sheet.

**How to use:** rehearse §1 until it's breathless, pick 3 STAR stories per
interview (§2), drill §3–§8 answers out loud, and finish with §9 numbers so
any figure is instant.

---

## 1. The pitches

### 1.1 The 60-second version (warm-up answer to "tell me about a project")

Session 8 replaced a reactive agent loop with a **planner that emits a DAG
of small skills**, which a deterministic executor runs concurrently, with
every step persisted so the whole run can be killed and resumed. The twist
that makes it interesting: the graph can contain its own **judge** — a
Critic node — and when it fails, a recovery planner is spliced into the DAG
with a failure report and must produce a corrected answer. I built five
demonstrations on it: fan-out parallelism (three branches, wall time equals
the slowest branch, not the sum), the critic pass/fail pair with a real
recovery, a coder whose arithmetic is verified by a sandbox, and a brand-new
skill added with zero orchestrator changes. All five pass, 22 tests still
pass, and every claim in the writeup maps to a session file you can open.

### 1.2 The 2-minute version (the answer that shows judgment)

Same system, but told as decisions:

- **Architecture.** Session 7's agent decided its next action each turn with
  the full history in context. Session 8 flips who holds the program: the
  model *writes* it — a JSON graph of nodes with skill, inputs, metadata —
  and a plain executor runs it. Prompt work moved from "what to do next" to
  "how to decompose, and what invariants to state." Parallelism, persistence,
  and recovery are then ordinary software concerns.
- **Execution.** Ready = all predecessors in {complete, skipped}; a batch of
  ready nodes runs under one `asyncio.gather`, so a layer's wall time is the
  slowest branch. The wall-clock proof is a read-only script clustering
  disk timestamps: 51.62 s wall vs 142.84 s of work across three
  researchers.
- **Verification loop.** Two models interact: the planner inside the graph,
  and a Critic the planner emits. Critic fails → the recovery splice marks
  the downstream child skipped, queues a recovery planner with the critic's
  rationale, and the second plan fetches what's missing and re-judges to
  pass. Failures are classified first: transient errors are *skipped* (the
  gateway already backed off), only substantive failures re-plan, and a
  planner never re-plans a planner — three rules that make recovery
  terminate.
- **Where I was wrong.** My first instinct for a broken classifier skill was
  the prompt. The node record said `provider: ollama` — the skill was
  unpinned and had drifted to a local 1.7B model that ignores instructions.
  The fix was one config line using the gateway's documented pin mechanism.
  I edited the prompt twice before I read what actually ran.
- **Honesty.** One published bound ("<3 s" for the smoke query) is
  arithmetically impossible given a 4-second gateway cooldown between calls.
  I documented the miss and the mechanism instead of cherry-picking a run.
- **Result.** Five queries inside their node-count bounds, the critic pair in
  two consecutive runs with only the input text varying, and a skill addition
  that touched prompt/yaml/config only — the executor, recovery, and gateway
  code are byte-identical to the baseline, verified by mtime.

---

## 2. STAR stories (six; pick three per interview)

### S1 — "Tell me about debugging something where your first hypothesis was wrong"
**Situation.** A new sentiment-classification skill returned `neutral 0.0
"No review was provided"` across four runs, despite the reviews being
correctly placed in the prompt by the planner.
**Task.** Make the skill work without touching protected code.
**Action.** I fixed the prompt twice — first a wrong empty-INPUTS rule
(fan-out nodes always have empty INPUTS; workers receive data via the
QUESTION field), then a few-shot example format mismatch. It still failed.
Then I read the persisted node record instead of the prompt: every result
said `provider: ollama`. The skill was unpinned, the router preferred a
local 1.7B model, and switching `LLM_ORDER` didn't help because unpinned
requests *skip* a cooling provider and fall through — only pinned requests
wait out cooldown. Fix: one line in `agent_routing.yaml` (`review_sentiment:
gemini`), the file's own documented extension point. Next run: positive 0.98,
negative 0.95, neutral 0.88, all correct.
**Result.** The skill passes; the deeper result is a debugging rule I now
apply everywhere: *read the artifact that records what actually ran before
editing the artifact that describes what should run.*
**Reflection.** Two prompt iterations were rational *if* the prompt were the
suspect — the mistake was not demanding evidence of the model identity first.

### S2 — "A time you found a bug in code you didn't write"
**Situation.** Before building anything, I reviewed the executor to find
where a Critic node could be inserted. The config said `critic: true` on the
distiller — but the insertion code gated on the just-completed node having
non-empty children, which a distiller never has.
**Task.** Decide whether to fix it or design around it.
**Action.** Traced the exact branch (`flow.py:155`), confirmed the
auto-insert path is unreachable — dead code — and checked how an
explicitly-emitted critic would be handled. The recovery layer's
`handle_critic_verdict` derives the critic's target and child from graph
structure when metadata is absent, so a planner-emitted critic works
without any executor change. Executor edits are reportable/protected, so I
designed Part 3 around the live path and filed the finding with line
numbers in the writeup.
**Result.** The critic pass/fail pair runs through the planner-emitted path;
the dead branch is documented rather than patched, and 22 tests still pass.
**Reflection.** A feature flag existing in config is not evidence the code
path fires — read the branch.

### S3 — "Handling an ambiguous or corrupted test state"
**Situation.** Query I ("three cities, closest pair") collapsed to a
two-node planner→formatter graph and answered "I cannot answer."
**Task.** Find why a working query degenerated.
**Action.** The planner's rationale said memory hits "contain the exact
population lookup" — a warm FAISS index from earlier runs had stored
overstated descriptors, and the memory hits render into *every* skill
prompt. The planner rightly (given its inputs) skipped research. Fixed by a
reset protocol: back up `memory.json` and the index files, write an empty
memory, re-run. After that and the coder rule, the run went to exactly 7
nodes with a sandbox-verified closest-pair difference of 1,640,756.
**Result.** Query passes; the reset protocol is now a written step before
every sensitive evaluation.
**Reflection.** In a system where memory is ambient, uncontrolled initial
state means you're testing yesterday, not today. Controlling it is
experiment design, not cleanup.

### S4 — "A time your tooling failed and you shipped anyway"
**Situation.** Part 1 requires SIGKILLing a run mid-graph and resuming. My
kill-watcher missed twice: first because it grepped Python's stdout, which
is block-buffered when redirected and hadn't flushed yet; second because it
read `graph['nodes'].items()` on a node-link schema where `nodes` is a list,
and I had suppressed stderr with `2>/dev/null`, so the parser died silently.
**Task.** Kill a run exactly in the parallel layer, third try.
**Action.** Switched to polling the artifact instead of stdout, built the
parser against a real `graph.json` fixture, removed the stderr suppression,
and asserted the kill itself (`killed=1`) rather than only the condition.
**Result.** Caught the run with the planner complete, three researchers
running, coder and formatter pending. On resume, the running nodes reset to
pending, the chain re-executed — and the coder hit a real `KeyError` →
sandbox exit 1 → failure classification → recovery re-plan → a final answer
whose sandbox stdout reads `Fastest growing city: Kinshasa (5.13%)`.
**Reflection.** Two failures in a row were tooling, not system. Never
suppress stderr in a control loop; assert the side effect, not just the
state.

### S5 — "Designing policy that interacts with existing policy"
**Situation.** Query J needs the planner to give up in two nodes on an
impossible file path — so I added a fail-fast rule. The critic-fail
demonstration then hit the *same* rule: the recovery planner read "the
reviews don't contain the price" and emitted an explanatory answer instead
of fixing it.
**Task.** Get both behaviors from one prompt.
**Action.** Scoped the rules by trigger: fail-fast fires only on the
original query; a `FAILURE` block means "this is correctable — obtain what
the critic's rationale says is missing." I also rejected putting "look it up
from the web" into the test query after observing that it made plan #1 fetch
preemptively and the critic passed — no fail to demonstrate. The instruction
belongs in the prompt under the FAILURE condition, so only the recovery
iteration executes it.
**Result.** J ships as exactly two nodes with no tools; the critic pair runs
fail → recovery fetch → corrected answer → pass in one session.
**Reflection.** The same sentence in the query, the prompt, or the failure
block is three different rules — trigger conditions are the design, and every
new rule must be audited against every existing one.

### S6 — "Pushing back on a spec or staying honest under pressure"
**Situation.** The brief said the two-node smoke query completes "<3 s."
Measured: 9.68 s wall including startup.
**Task.** Hit the bound or report it.
**Action.** Checked the number against the system's constants before blaming
the code: the gateway enforces a 4-second cooldown between consecutive
Gemini calls, and the two calls alone are therefore ≥4 s plus model time —
the reference run's own timings (2.18 + 1.1 s) already exceeded 3 s. The
bound is arithmetically unachievable without gateway changes, which are out
of scope and reportable.
**Result.** Documented the miss, the arithmetic, and the mechanism in the
README under "Honest notes," alongside two other bound differences (kill
shape, answer variance). No run was cherry-picked.
**Reflection.** Checking a published bound against the system's constants
first turns "my code is slow" into "this constraint has a unit error" — and
honest negative results are part of the evidence.

---

## 3. Architecture Q&A

**Q1. Describe the system in 60 seconds.**
The model produces a JSON DAG — nodes carry skill, resolved inputs, and
metadata; edges are dependencies — and a deterministic executor runs it.
Ready nodes (all predecessors complete or skipped) execute concurrently per
batch; each step persists to disk; the terminal formatter's output is the
answer. Inside the graph sits a Critic the planner emits; a failed verdict
splices a recovery planner with a failure report. Skills are prompt +
config, so new capabilities need no orchestrator changes. The gateway
serializes provider calls with cooldowns and failover. What used to be
implicit in an agent loop is now inspectable data — and I can point at the
file:line for each rule.

**Q2. Why a DAG instead of an agent loop?**
Three reasons. **Parallelism** — independent branches are just coroutines in
one `gather`; an implicit loop can't fan out without inventing a scheduler.
**Replayability** — the plan is a persisted artifact; you can diff, grade,
and resume it. **Failure locality** — recovery is a graph edit (splice a
node, skip a child) instead of a prompt surgery on a growing history. The
cost is rigidity: plans are made up front, so mid-run surprise needs an
explicit recovery path rather than free-form adaptation.

**Q3. What exactly does the planner emit?**
`{"rationale": ..., "nodes": [{"skill": "researcher", "inputs": ["n:1"],
"metadata": {"question": "..."}}]}`. `extend_from` resolves input references
against the existing graph, adds nodes and edges, and auto-attaches internal
successors from config — that's how the coder gets its sandbox executor
without the planner naming it. The planner also encodes scoping policy:
worker nodes get a single focused question; `inputs` are only wired from
nodes whose content the worker should actually see.

**Q4. How does the answer get selected?**
The formatter is a terminal skill: when a formatter completes, the executor
writes `formatter_answer`. Last-writer-wins — a recovery's second formatter
overwrites the first. If no formatter ever succeeds, a fallback takes the
newest complete node's raw output, which is why a run with a 502'd formatter
still returned an answer instead of crashing.

**Q5. How is fan-out isolation enforced?**
In the prompt renderer: `USER_QUERY` appears only if the planner wired it
into a node's inputs; workers get their slice through `metadata.question` →
`QUESTION` section. That keeps three parallel researchers from each trying
to answer the whole query, and it's why a planner bug that drops the question
(empty inputs, empty question) shows up as empty-answer reviews rather than
cross-contamination.

**Q6. Where's the scheduler?**
There isn't a central one. Each iteration: collect ready nodes, dispatch
them all, `gather`, flush results, extend, repeat until nothing is ready or
running — capped at 60 nodes. Backpressure comes from the provider layer (a
15 rpm window and 4-second cooldown), not the executor.

## 4. Concurrency & persistence Q&A

**Q7. Prove the parallelism.**
A read-only script reads `started_at`/`completed_at` from disk and clusters
overlapping intervals. Three researchers: starts staggered at 5.98/10.09/
13.89 s (provider cooldown spaces the *starts*), all three end at 57.60 s on
the same gather barrier. Wall 51.62 s equals the slowest branch; serial sum
of the layer is 142.84 s. Speedup is below 3× because planner and
coder/formatter are serial overhead — expected, and stated in the writeup.

**Q8. What does "resume" actually restore?**
`--resume <sid>` reloads the full graph, rebuilds the node counter from the
highest `n:X`, flips `running → pending`, and re-reads the query from disk.
Completed nodes keep their results and timestamps untouched. Resume is
node-granular: a killed node re-executes from scratch, including its tool
loop — mid-tool resume is explicitly deferred and documented as such.

**Q9. What's the weakest point of the persistence design?**
Results flush per batch, after `gather` returns — status `running` is
written *before* dispatch. A kill mid-batch therefore loses every in-flight
completion in that batch and shows all members as `running`. We hit it
directly: the kill evidence shows three researchers running rather than two
complete plus one running. The fix is a per-outcome flush, which means
executor code — so it's a reported finding, not a change.

**Q10. Is the system idempotent?**
At node granularity, yes — completed nodes are skipped on resume. Not at
tool granularity — a re-run of a research node refetches. Determinism is
partial: critic temperature is 0.0 for stable judgments, but the planner is
not, so re-runs can emit different valid graphs; our bounds are stated as
"exactly N nodes" against runs we kept, and we document the attempts we
discarded.

**Q11. What happens when two ready nodes both write the graph?**
They don't — writes are on the event loop between batches: mark all
dispatched → write → gather → write each result → write graph. Disk state is
a batch-consistent snapshot, not a live view. That also bounds the damage of
a kill: at worst you lose one batch of completions (see Q9).

## 5. Failure handling Q&A

**Q12. Walk me through a node failure.**
`_run_one` raises; `classify_failure` buckets the error: transient
(503/502/504/timeout/connection) → **skip** — the gateway already retried and
backed off, and re-planning would hammer a dying provider; validation error
→ skip — it's a prompt bug to fix offline; planner failure → skip — never
re-plan a planner; anything else → **replan**: queue a recovery planner whose
input carries a `failure_report`, then re-run normally. Skipped nodes count
as satisfied for readiness, so the graph flows *around* holes.

**Q13. And a critic failure?**
Different path: `handle_critic_verdict` reads the verdict (default is
*pass* unless it says fail), derives the critic's input and successor from
graph structure, marks the successor skipped, and on a *first* failure for
that target splices a recovery planner with the rationale embedded. A second
failure for the same target hits a hard cap: branch skipped, warning logged.
`recovered_branches` is keyed per target — that's the termination proof.

**Q14. Why default to pass on a malformed verdict?**
Fail-closed would let one bad token destroy a good answer and trigger a
whole re-plan; the judgment layer shouldn't be more fragile than the work it
judges. The cap plus the retry limit still bound any loop that does trigger.

**Q15. How does the recovery planner know what to fix?**
Only through the failure report — it gets `USER_QUERY` plus the FAILURE
block, not the failed node's inputs. That's a real limitation (context
blindness), and it's why the report quality matters: the critic's rationale
is its entire picture of the gap, and the prompt rule tells it to *obtain*
what the rationale names. If I could change one line of executor code, I'd
consider threading the failed node's input summary in — but I'd first check
it against prompt-injection surface.

**Q16. What does the gateway do when the provider is down?**
Backoff table: quota 429 → 60 s unavailable; daily → 1 h; 5xx → 20 s;
timeout → 10 s; auth/model-name errors → 0 (don't blackball the provider for
a config typo). During cooldown, pinned requests *wait* in a bounded loop
(30 s deadline) and unpinned requests silently skip to the next provider —
that asymmetry is a debugging trap I've been bitten by. Exhausted failover
returns 503; the node then classifies transient and skips.

**Q17. How do you stop an infinite recovery loop?**
Layered: classifier skips transients; planner never re-plans itself; one
recovery splice per target (cap); node cap 60; retry limits per node. In the
shipped K session, recovery fired once and the second critic passed — we
also have an explicit test for the cap behavior.

**Q18. What real failure escaped to production, so to speak?**
The coder in K emitted a bad index → `KeyError` → sandbox exit 1 →
upstream_failure → recovery re-plan with a corrected chain. The system did
what it should: a broken computation never reached the answer as a
narrated fact, and the final answer was sandbox-verified. That's the whole
argument for verification layers: they earn their cost exactly once.

## 6. LLM systems Q&A

**Q19. What did prompt engineering actually look like here?**
Rewriting your mental model helps: I'm not writing instructions for a turn,
I'm writing the *specification for a program generator*. The planner prompt
became policy tables — when to distill, when to compute with the coder, when
to fail fast, what to do on FAILURE — each added because a real run violated
it, each with its "when not to" half so it wouldn't over-trigger. Prompt
rules without trigger conditions are how you break a different query while
fixing one.

**Q20. How do you evaluate a prompt change?**
Re-run the *same* query with memory state fixed, compare node shapes and
answers, and keep the failures. Four iterations on the sentiment skill only
diagnosed correctly when I stopped treating it as a prompt problem — the
lesson is to change one thing per run and read the persisted result (model,
latency, retries), not just the text.

**Q21. How reliable is an LLM-as-judge here?**
You make it *checkable*, not trusted: temperature 0.0; the judge repeats the
same constraint the planner was given, including "a missing required key is a
FAIL" — so absence, which the stock critic prompt wouldn't flag, becomes a
violated property of the spec; failures are capped and auditable; and the
final answer still has to survive a second judgment after recovery. One known
gap: the stock judge fails only on "fields the input clearly contained," so
absence-based testing lives or dies on the constraint wording.

**Q22. How did memory affect behavior?**
Ambient memory into every prompt is a feature with a contamination mode:
stale descriptors ("exact lookup available") let the planner short-circuit
the whole graph, and the model answers from its own history. We treat memory
initial state as an experimental control — reset before each sensitive run —
and I'd now snapshot it in CI automatically.

**Q23. Routing — what decides which model runs a skill?**
Config pins first (`skill: provider` in the routing file), then the router
order, with cooldown and rpm gates, and failover on errors. Pinned requests
wait out cooldowns; unpinned ones skip to whoever is idle. So an unpinned
new skill silently migrates — to a 1.7B local model in our case. Model
identity belongs in the persisted record of every result; without `provider`
in that record, this bug was invisible for four runs.

**Q24. What's your take on adding tools to skills?**
Per-skill allowlists in config — the comparison classifier had none, the
researcher had fetch tools, and the coder's tooling arrives via
`internal_successors` rather than being named in the plan. Minimal surface
per node also keeps prompt-injection blast radius per-node rather than
system-wide.

**Q25. What would you improve architecturally?**
Four things I'd queued as findings: flush per outcome instead of per batch;
thread the failed node's context into recovery; expose wait-inclusive
latency so dashboards show what the client paid; and either fix or delete
the dead auto-critic branch so config matches reality. None of them need a
rewrite — they're five-line honesty fixes.

## 7. Rigor & evaluation Q&A

**Q26. How do you know the parallel claim is true and not just hoped for?**
The timestamps are written by the system itself, and my checker is read-only
— it can't create the evidence it reports. It clusters intervals, prints
overlaps, and computes wall/max/sum per layer. The signature of a real
concurrent batch is identical end-times on a shared barrier; a fake would
show sequential intervals.

**Q27. How do you test that a fix worked?**
Same query, fixed memory, one changed variable, and a persisted diff of node
shapes. For the routing fix, the discriminator was `provider: gemini` in the
results — not the sentiment text, which had fooled me twice. Baselines:
22 unit tests for classification/recovery/critic logic after every change,
and mtime checks proving protected files are untouched.

**Q28. What did you throw away?**
Twenty-one discarded runs plus seven smoke tests: four attempts at the
three-city query, three at Shannon, five at the impossible-path query, two
kill-watcher misses, two critic attempts that didn't produce a fail, five
classifier iterations. All are kept on disk and
named in the writeup with what each one taught us. The writeup's honesty
section lists where our answers differ from the reference and why — one of
them is a boundary-assumption difference (city-proper vs metro population)
that the shipped answer states explicitly.

**Q29. How do you handle a spec you think is wrong?**
Separate the *constraint* from the *number*. The smoke query's "<3 s"
conflicts with the gateway's 4-second cooldown — I verified the arithmetic
against the reference's own timings, reported the miss with the mechanism,
and kept running it as specified. Being right about someone else's number
only matters if you also shipped the work and documented the discrepancy
where a reviewer will see it.

**Q30. What's your evidence discipline?**
Every claim has an openable path: session ID for runs, `file:line` for code,
read-only scripts for aggregates. If I can't cite it, I don't say it — that
rule is why the writeup includes discarded runs and failed tooling instead of
a cleaner-looking fiction.

## 8. Behavioral & scale Q&A

**Q31. What was the hardest bug?**
The routing one — not because it was complex, but because it *looked* like
four different prompt bugs. The discipline that cracked it was going from
"what should have happened" (prompt) to "what did happen" (the `provider`
field in the result). Cost: several wasted runs. Gain: a debugging rule and a
one-line config fix.

**Q32. Tell me about a mistake you made that the system caught.**
The coder's `KeyError` in K. I'd written the rule "computation goes through
the coder and sandbox," and that run is where the rule proved itself: the
broken computation surfaced as exit 1, got classified, re-planned, and the
final answer came from a chain that ran clean. The alternative — the
formatter narrating plausible arithmetic — fails silently.

**Q33. Disagreement with a teammate or reviewer?**
The critic-fail demonstration: an earlier version put "look it up from the
web" in the query, which made the first plan fetch preemptively and the judge
passed — no fail to show. The instinct is to weaken the test; instead I moved
the instruction into the prompt's FAILURE condition so plan #1 stays naive
and plan #2 fixes it. Tests should get *stricter* under pressure, not softer —
and trigger conditions, not wording, were the real lever.

**Q34. What breaks at 10× scale?**
Single-writer graph updates are fine at 60 nodes; at thousands you'd want a
real store and per-node checkpoints (the batch-flush loss grows with batch
size). One in-memory `gather` batch caps concurrency at provider limits anyway
(15 rpm), so the executor isn't the bottleneck — the gateway is, and
horizontal scaling means multiple gateways or provider quotas. Recovery caps
(keyed per target) become a bookkeeping structure you'd want persisted.
Memory becomes a versioned store with access control, not a JSON file.

**Q35. If you had another week?**
Per-outcome flushing; wait-inclusive latency metrics; recovery context from
the failed node; an automatic memory snapshot/restore in the test harness;
and property tests on the planner's JSON (node count bounds, acyclicity)
instead of post-hoc inspection. Then wire the read-only checker into CI as an
evidence regression test.

**Q36. What would you do differently from the start?**
Read the executor's write path and the gateway's routing rules *before*
writing any prompt — that would have surfaced the dead auto-critic branch,
the batch-flush window, and the pin asymmetry in hour one instead of during
debugging. I did the pre-build review, but I under-weighted the runtime
observability question: `provider` on every result should be a checklist item
for any system where the model identity can change underneath you.

---

## 9. Cheat sheet — numbers that must be instant

| Number | Meaning |
|---|---|
| **22** | tests passing (`tests/test_recovery.py`, 11 functions) |
| **5 / 4 / 7 / 2** | node counts: Shannon A, comparison I, impossible J, hello (bounds exact) |
| **51.62 s vs 142.84 s** | parallel wall vs serial sum (three researchers, `s8-4d752709`) |
| **57.60 s** | shared gather barrier — all three researchers end together |
| **4 s** | Gemini cooldown between calls (why starts stagger, why "<3 s" is impossible) |
| **15 rpm / 60 s** | Gemini rate window / quota-429 backoff |
| **60** | MAX_NODES cap (`flow.py:32`) |
| **30 s** | pinned-request cooldown wait deadline |
| **1,640,756** | verified closest pair, Berlin–Paris (I, sandbox-checked) |
| **5.13%** | Kinshasa growth rate, K's sandbox stdout after recovery |
| **0.98 / 0.95 / 0.88** | sentiment: positive / negative / neutral (all correct, gemini) |
| **111 s** | K resume duration (14 nodes total, kill + recovery in one session) |
| **37** | sessions on disk: 9 shipped, 21 discarded, 7 smoke — all named in the writeup |
| **0.0** | critic temperature (determinism) |
| **1 line** | the routing pin (`review_sentiment: gemini`) — only gateway-adjacent edit |
| **0** | lines changed in flow.py / skills.py / recovery.py / gateway code |

**Session IDs to remember:** hello `s8-afb7dc9b` · A `s8-3968623e` ·
I `s8-0a9d2171` · J `s8-246960ad` · K `s8-5c5d72a6` · parallel
`s8-4d752709` · critic pass `s8-826cd93e` · critic fail+recovery
`s8-9d227709` · sentiment `s8-ee1437f7`.

---

## 10. Questions to ask the interviewer

1. How is this team's agent behavior reviewed today — eval harness first, or
   production traces first? (I've lived both sides after building my own
   evidence tooling.)
2. Where does the team draw the line between model-written plans and
   deterministic execution? That line is the whole design of my project.
3. How do you handle judge models in the loop — and how do you keep them
   from becoming untestable sources of truth?
4. What's your policy on changing prompts vs. changing code when behavior
   drifts? I have strong opinions now, mostly earned the hard way.
5. How much observability do you demand before you trust a new LLM feature —
   provider identity, latency, retries per call?
6. When a published spec and a measured number disagree, what's your
   team's default? I've had to decide that this quarter.
