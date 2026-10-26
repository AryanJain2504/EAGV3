# Assignment 7 — Interview Drills

Companion to `LEARNINGS.md`. Use this file for active recall, not passive reading.

**Rules**

1. Answer aloud before revealing the model answer.
2. Cap the first answer at 90 seconds.
3. Name the evidence: code path, run ID, or trace.
4. Mark every claim as observed, measured, inferred, or unverified.
5. If you cannot answer, write the gap in the weak-spot log at the end.

---

## 1. The 30-question bank

### Architecture

#### Q1. Walk me through one iteration of the agent.

**Answer.**

`memory.read(query, history)` returns vector hits, or keyword hits if the vector path is empty. Perception sees the query, prior goals, hit descriptors, recent history, and current-run artifact handles; it returns goals with `done` and optional artifact hints. The orchestrator picks the first unfinished goal and attaches current-run artifact bytes if Perception asked for them. Decision sees that goal, the original request, memory evidence, recent history, attachments, and the full MCP catalogue; it returns exactly one answer or one tool call. Action validates and executes the call; large outputs become artifacts. Memory records the outcome as a `tool_outcome`. History records an action or answer event.

**Follow-up:** What does the orchestrator *not* do? It does not construct tool calls, withhold tools, choose evidence, or override goal state.

#### Q2. Why is Perception tool-blind?

Because tool choice belongs to Decision. If Perception named tools, it would be making a tool decision before the evidence is retrieved. Tool-blindness keeps the planner at the intent level and makes the trace show whether Decision chose the right tool for the goal. `validate_prompts.py` checks the `Perception.SYSTEM` string for MCP tool names; it currently checks 10 of 12 names, so the manual check used all 12.

**Follow-up:** Is tool-blindness enforceable? Partially. The literal prompt check is static; a model can still infer a tool. The invariant is architectural, not a hard sandbox.

#### Q3. Why does Decision get the full tool catalogue?

Withholding tools from a goal the orchestrator judged to be “final synthesis” is a pre-emptive tool decision outside Decision. It caused a real failure: a goal was classified as synthesis, tools were withheld, and Decision could not retrieve. The full catalogue lets Decision choose; if it makes a bad choice, the trace shows it.

**Follow-up:** Doesn’t that increase risk? Yes. The tradeoff is diagnosability and correct ownership; permissions belong in a separate policy layer, not in an intent guess.

#### Q4. What does the orchestrator own?

The loop, the iteration cap, goal selection, artifact eligibility checks (current-run, exists), final trace, and final answer extraction. It does not own goal completion, evidence relevance, tool choice, or answer content.

**Follow-up:** Why does it check artifact eligibility? Because memory is persistent and a stale handle could attach another run’s bytes. The check scopes provenance; Perception still chooses relevance.

#### Q5. How does a goal become done?

Perception sets `done` based on the query, prior goals, hit descriptors, and run history. `done` is monotonic: `was_done or delta.done`. The orchestrator never overrides it. A goal can be done without an action result or a cited answer; this is the Q2 failure.

**Follow-up:** What would you change? Add a goal-evidence contract: a goal closes only on a cited answer, or an uncited answer backed by its own successful tool action, or its own tool action. Be explicit about which evidence class closes which goal.

#### Q6. How do artifacts move?

Action stores large outputs (over 4 KB) and every `fetch_url` output in the artifact store. Memory records the handle plus a descriptor. Perception sees handles and descriptors, and may set `attach_artifact_id` / `attach_artifact_ids` for a goal. The orchestrator drops handles not produced by this run, reads the bytes, and passes them to Decision. Decision can see raw bytes only when Perception attaches them.

**Follow-up:** What was the wrong-artifact bug? Perception attached a current-run but semantically wrong artifact (PostgreSQL bytes for a Transformer question). Current-run scoping does not solve semantic relevance.

#### Q7. What is the final-answer bug?

`_final_answer` returns the last non-empty answer without checking goal state. The loop can break when the current goal is the only open goal and Decision has just answered, even if Perception never marked it done. The result is a final answer with `incomplete_reason: null` while a goal is `done: false`. Q1 diagnostic `run 3f927811` shows this exactly.

**Follow-up:** What is the fix? Make the finalizer inspect final goals; return `"goals-open"` if any goal is unfinished. Add a regression test that no answer is returned while a goal is open.

#### Q8. Why was the contract layer removed?

It created per-goal evidence modes, a citation gate, and a reconciliation pass. A goal classified into the wrong mode had tools withheld; Decision could not retrieve; the run ended with a self-describing non-answer. The guardrail hid the failure it was meant to catch. Removing it made the loop smaller and the failures visible.

**Follow-up:** Did grounding get worse? Possibly, in the short term. The remaining prompt obligation is weaker, but it is not a parallel truth layer. A claim-level verifier is the right next step.

#### Q9. Why no `evidence_mode` on `Goal`?

Because it was a second source of truth. The mode was inferred from goal text or attachment state, and it could diverge from the evidence. The final design derives behavior from what is actually present: memory hits, attachments, history, and the full catalogue. The goal is intent, not a contract enum.

**Follow-up:** What about structured outputs? `DecisionOutput` enforces exactly one answer or one tool call, but not a per-goal schema. The answer text is the contract; a schema would only shape it, not make it true.

#### Q10. How does state isolation work?

`state_paths.py` chooses `EAGV3_STATE_DIR` or the default `state/`. `agent7.py` passes `env=dict(os.environ)` to the MCP subprocess. Without that, the subprocess would use the default `state/` while the orchestrator used the isolated directory. This was a real bug: indexing and search silently diverged.

**Follow-up:** What else is in state? `memory.json`, `index.faiss`, `index_ids.json`, `artifacts/`, `history_<run_id>.jsonl`, `result_<run_id>.json`.

### Retrieval

#### Q11. How was the corpus built?

PDF → `pdftotext` page-range extraction → UTF-8 text with `[Page N]` markers → manifest entry (book, author, pages, source PDF, path) → `index_corpus.py` → `mcp_server.index_document` → 400/80 chunks → descriptor embedding → `fact` memory item → `memory.json` + FAISS. Result: 53 records, 1,625 chunks (DDIA 721, ML Systems 425, PostgreSQL 479).

**Follow-up:** Why page ranges? They create 53 meaningful, inspectable items with page-level provenance. The cost is that a record is not a chapter.

#### Q12. Why 400/80?

400 words keeps several chunks plus history and attachments within the model context; 80 words preserves sentences that straddle a boundary. Smaller chunks improve citation precision but fragment arguments; larger chunks preserve context but bury the answer. The final evidence uses 400/80. `index_corpus.py` defaults to 700/120, so the flags must be explicit.

**Follow-up:** What about semantic chunking? Not implemented. It is future work, not a hidden feature.

#### Q13. How does the vector index work?

FAISS `IndexFlatIP` over L2-normalized 768-dimensional vectors. Inner product equals cosine similarity. Vectors are stored by integer position; `index_ids.json` maps position to `MemoryItem.id`. Exact search is fine at 1,625 chunks. The dimension is fixed on first add; a mismatch raises.

**Follow-up:** When would you change it? At 10^5–10^6 chunks, use a separate chunk index, metadata filtering, and possibly HNSW or IVF; add a reranker.

#### Q14. What is the descriptor-embedding limitation?

`index_document` embeds `[source chunk i/n] <first 120 chars>`, not the full chunk. If the answer is late in the chunk, the vector may miss it. The full chunk is stored and rendered, and the keyword path can rescue exact terms, but the dense representation is weak. The fix is to embed the body or a semantic summary; it requires reindexing.

**Follow-up:** Why was it done this way? It is cheap, source-aware, and consistent across item kinds. It is a known recall ceiling, not a deliberate claim of optimality.

#### Q15. Why is there no similarity threshold?

A manual check found relevant cosine scores around 0.58–0.79 and irrelevant around 0.66–0.70. The ranges overlap, so a threshold that removes the irrelevant band also removes relevant hits. On a book-only corpus, every query is near something. Ranking is useful; absolute similarity is not calibrated.

**Follow-up:** Is that a general result? No. It is a small manual check on this corpus. A labelled calibration set could justify a threshold, but this project does not have one.

#### Q16. What is RRF and why use it?

Reciprocal Rank Fusion:

```text
score(item) = Σ_paths 1 / (60 + rank_path(item))
```

It combines dense and keyword rankings without normalizing their scores. It rewards items strong on either path, especially both. It fixed the case where dense-only put every ReAct chunk above the CoT chunk even when the query named CoT.

**Follow-up:** What does it not fix? Coverage. RRF ranks chunks; it does not ensure every paper is represented. “Across these papers” needs enumeration.

#### Q17. How does `memory.read` differ from `search_knowledge`?

`memory.read` is ambient recall: shared FAISS, vector-first, keyword fallback only if the vector path is empty, returns facts/preferences/tool outcomes/scratchpads. `search_knowledge` is a deliberate corpus tool: only facts with `value.chunk`, dense + keyword RRF, returns chunk dicts with provenance.

**Follow-up:** Why two surfaces? A single surface would either pollute the knowledge tool with action records or hide tool outcomes from Perception.

#### Q18. What was the query-pollution bug?

A user query was stored as a `fact` and embedded. It scored maximally against itself and outranked real chunks. Fix: `search_knowledge` returns only items with `value.chunk`; `_fallback_remember` writes `scratchpad` with no embedding; the classifier path never promotes unclassified text to fact. A regression test asserts the echo cannot enter the citable set.

**Follow-up:** Why not just purge memory? A purge fixes one row, not the write path or the retrieval surface. Fix the class.

#### Q19. How would you evaluate retrieval?

30–50 queries, 1–3 relevant chunk IDs each, hard negatives, stratified by semantic/exact/multi-source/comparison. Metrics: Recall@5, Precision@5, MRR, nDCG@5, per-book. Ablations: descriptor vs body, dense vs keyword vs RRF, 400/80 vs 700/120. Bootstrap confidence intervals. Check that semantic query wording is absent from relevant chunks.

**Follow-up:** What is the smallest useful version? 30 queries, 2 labels each, Recall@5 and MRR, one ablation (body embedding). That is enough to falsify the current choice.

#### Q20. How would you scale the retrieval system?

Separate the chunk index from action/preference memory; metadata-filter before ANN; HNSW/IVF with a recall audit; rerank top-50; per-source diversity; incremental atomic indexing; document-level dedupe keys; a labelled eval in CI. The agent loop does not need to change; the retrieval service does.

**Follow-up:** What is the first bottleneck? The shared FAISS index and descriptor embeddings, not the loop.

### Failure analysis and evaluation

#### Q21. Q1 diagnostic: why is the final answer returned while the goal is open?

Because the loop treats “Decision answered the last open goal” as completion. `still_open` excludes the current goal, so it is empty; the loop breaks. `_final_answer` returns the last answer and does not inspect goals. The final goal `g:7298b523` remains `done: false`. This is a state-machine bug, not a retrieval bug.

**Follow-up:** What about the citations? The answer cites `mem:d471c978` (monitoring, supports), `mem:146549d2` (loosely related), and `mem:60c99904` (WebAssembly bibliography, unrelated). Citation validity is a separate gap.

#### Q22. Q2: why did it answer without `search_knowledge`?

`memory.read` returned relevant chunks; Decision answered the query goal directly from memory evidence. Perception then marked both goals done. The answer is grounded in a real chunk, but the trace does not show the intended retrieval workflow. The goal text (“Query the existing knowledge base”) is not an action-level requirement.

**Follow-up:** Is that wrong? It is efficient but weaker evidence. If the workflow requires a search, require it at the goal-evidence level; otherwise accept ambient recall and say so.

#### Q23. Q4: why is the comparison one-sided?

Decision retrieved ML observability material and answered. The prompt says “if evidence covers only one side, search for the missing side” and “once a search has returned chunks for this goal, do not search again.” The model followed the second rule. The data-platform side was available but not retrieved.

**Follow-up:** How do you fix it? Define precedence: for comparison goals, missing-side search overrides the no-research rule. Test with a one-sided initial retrieval.

#### Q24. A: why is the final answer incomplete?

The fetched page was attached, but the recorded answer ends at “Three key contributions:”. The model started the list and the stored answer stopped. The trace does not record a finish reason. The run used 2/3 iterations, but it did not deliver the three requested contributions. This is an answer-generation/completeness failure, not retrieval.

**Follow-up:** What would you add? Capture the model finish reason, validate requested fields, and score field completion separately from iteration count.

#### Q25. F2: why is it a coverage failure?

F1 indexed five papers, including a dedicated CoT paper. F2 answered “the available indexed material mentions chain-of-thought only in the context of ReAct,” citing `mem:ed4989d5`. It did not search the knowledge base or enumerate the indexed papers. The “only” claim is false relative to the corpus.

**Follow-up:** Is it a hallucination? No; it is an incomplete evidence scan. The answer is faithful to the one hit it saw, but not to the corpus.

#### Q26. What do the no-corpus runs prove?

They show that with the corpus parked and a fresh empty state, the agent does not produce an answer; all five end with `No answer was produced.` This is evidence of corpus dependence and honest failure. They do not prove a clean causal effect: the empty state changes memory, goals, and tools; Q2 marks goals done before the no-answer; Q4 has a schema-invalid call.

**Follow-up:** What would a clean ablation need? Same model, prompt, history, and tool behavior, with only the retrieved corpus context toggled. That is hard with an agent; the current control is a dependency check, not a lab experiment.

#### Q27. What are the P0 and P1 issues?

P0: finalization is not goal-aware; `test_mcp_server.py` deletes the real sandbox; goals can be done without evidence; A’s answer can be incomplete. P1: Action ignores `isError` and lacks transport handling; Q4 prompt conflict; unbounded context; descriptor embeddings; SSRF/no fetch limits; artifact path traversal; metadata override; non-atomic state; PoP checks 10/12 tools; prompt injection; all tools always available. P2: defaults, deprecated `utcnow`, dead crawl4ai code, artifact GC, usage lock, trace finish reason, goal events, token count, citation support, retrieval eval, repeats.

**Follow-up:** What would you fix first? The finalizer: small, local, testable, and it causes false success.

#### Q28. How would you test prompt conflicts?

Build a matrix of goals × evidence states: one-sided comparison, missing exact term, insufficient evidence, attached chunk with no answer, top-N search. For each, assert precedence: e.g., “missing-side search beats no-research for comparisons.” Run 5 times; score whether the model follows the intended precedence. Add a static check that both rules exist, but do not rely on it.

**Follow-up:** What if the model still fails? Move the precedence into the tool/evidence boundary (e.g., a comparison goal requires two source IDs) rather than adding more prose.

### Product and security

#### Q29. What would you ship, and what would you hold?

**Ship:** the corpus/provenance, C1→C2 persistence, E-style index→search→answer, H-style two-sided comparison, no-corpus refusal, and the trace/evidence tooling. **Hold:** untrusted web ingestion, write-capable tools without approval, and any claim of production readiness. Fix finalization, action errors, context budget, SSRF, artifact validation, and metadata integrity first.

**Follow-up:** What is the first product metric? Trust: citation coverage and unsupported-claim rate. Then latency and cost.

#### Q30. How would you secure this agent?

Threat model: untrusted web/corpus content, model-proposed tool calls, local filesystem, network egress, secrets, user data. Controls: sandbox paths; schema validation; current-run artifact scoping; SSRF allowlist and IP checks; response size caps; artifact ID validation; metadata whitelist; per-tool permissions and approvals; env minimization; prompt-injection delimiters and evals; audit logging; data retention/deletion. No prompt-only defense is sufficient.

**Follow-up:** What is the most dangerous current gap? `fetch_url` SSRF plus write-capable tools, because a malicious page can influence a tool call.

---

## 2. STAR story cues

### Story 1 — Wrong artifact / contract layer

- **Setup:** E/Q1 goal about Transformer contributions.
- **Tension:** Decision received 25,523 bytes of PostgreSQL; refused; contract layer turned refusal into a non-answer.
- **Action:** Reconstruct prompt; delete evidence modes/citation gate/reconciliation; route chunk IDs.
- **Result:** 388-line loop; visible failure; tests rewritten.
- **Line:** “The guardrail was hiding the bug it was built to catch.”

### Story 2 — Stale artifact

- **Setup:** Persistent memory carries artifact handles across runs.
- **Tension:** A goal could attach another run’s bytes; handle looked valid.
- **Action:** `_current_run_artifacts`; drop stale handles; Perception keeps relevance ownership.
- **Result:** Cross-run contamination rejected with a trace line.
- **Line:** “Provenance is not relevance, but it is the first gate.”

### Story 3 — Query pollution

- **Setup:** User query stored as a fact and embedded.
- **Tension:** It scored maximally against itself and outranked real chunks.
- **Action:** `search_knowledge` filters to chunks; fallback writes scratchpad; test the echo.
- **Result:** Stored questions cannot pose as corpus evidence.
- **Line:** “Fix the write path and the retrieval surface, not just the row.”

### Story 4 — Finalization bug

- **Setup:** Q1 diagnostic returned an answer while the final goal was open.
- **Tension:** `incomplete_reason` was null; the `goals-open` branch was unreachable.
- **Action:** Trace the loop; identify the `still_open` exclusion; design goal-aware finalizer.
- **Result:** P0 documented; regression test designed.
- **Line:** “Completion and answer generation are different state machines.”

### Story 5 — RRF / title dilution

- **Setup:** Dense-only ranked ReAct above CoT even when the query named CoT.
- **Tension:** Scraped navigation text diluted title terms.
- **Action:** Keyword path with two-token floor; RRF; all-facts candidate set.
- **Result:** Title-specific chunks reach top-k; hybrid retrieval.
- **Line:** “Fuse ranks, not incomparable scores.”

### Story 6 — No-corpus controls

- **Setup:** Rubric requires no-corpus comparison.
- **Tension:** Removing corpus from prompt is not the same as removing it from state.
- **Action:** Park sources; fresh state per query; 6-iteration cap; restore trap.
- **Result:** All five end with no answer; no fabrication.
- **Line:** “Honest failure is a feature.”

### Story 7 — State isolation

- **Setup:** Isolated `EAGV3_STATE_DIR` in the orchestrator.
- **Tension:** MCP subprocess used default `state/`; indexing/search diverged.
- **Action:** Pass `env=dict(os.environ)` to the stdio server.
- **Result:** Index and search share the same state.
- **Line:** “Every process boundary is a state boundary.”

### Story 8 — Test isolation

- **Setup:** Legacy MCP test deletes real `sandbox/`.
- **Tension:** Full pytest is unsafe; 71 contract tests are safe.
- **Action:** Document safe gate; plan temp sandbox + guard.
- **Result:** No corpus loss; test debt explicit.
- **Line:** “A test that deletes production data is not a test; it is an incident.”

---

## 3. Whiteboard drills

### Whiteboard 1 — The loop

Draw:

```text
query -> memory.read -> perception.observe -> pick goal
      -> attach current-run artifacts -> decision.next_step
      -> answer OR action.execute -> memory.record_outcome -> loop
```

Mark:

- what Perception sees (descriptors, goals, history);
- what Decision sees (goal, evidence, history, attachments, catalogue);
- where `done` is set;
- where the finalizer runs.

**Prompt:** “Show me where Q1’s finalization bug lives.” Point to the break after answer and `_final_answer`.

### Whiteboard 2 — Retrieval

Draw two lanes:

```text
memory.read: query -> embed -> FAISS top-8 -> (if empty) keyword
search_knowledge: query -> dense all facts -> keyword -> RRF -> top-k
```

Annotate:

- descriptor embedding;
- no threshold;
- chunk-only filter;
- per-book counts.

**Prompt:** “Why can Q2 answer without the bottom lane?” Because the top lane returned relevant chunks and Perception marked the goal done.

### Whiteboard 3 — Goal state / finalization

Draw a state machine:

```text
goal open -> Decision answer -> Perception done? -> yes: close
                                      | no
                                      v
                              still other open goals?
                                | yes -> continue
                                | no  -> break (BUG: final answer while open)
```

**Prompt:** “Where would you add the invariant?” After answer, require `all(g.done)` before finalizing; otherwise continue or return `goals-open`.

### Whiteboard 4 — Production architecture / security

Draw:

```text
user -> API/auth -> agent loop -> tool policy -> sandbox/egress proxy
                       |              |
                       v              v
                  trace/audit     artifact store
                       |
                  eval/monitor
```

Annotate trust boundaries:

- user input untrusted;
- web/corpus content untrusted;
- model output untrusted action proposal;
- MCP local trusted code but least privilege;
- secrets isolated;
- approvals for writes.

**Prompt:** “Where does prompt injection enter, and what stops it?” It enters through retrieved content; prompt text alone does not stop it; tool policy and least privilege do.

---

## 4. Three mock rounds

### Round 1 — Frontier lab (45 min)

| Time | Segment |
|---:|---|
| 5 | Intro: 3-minute project explanation |
| 10 | Architecture and POMDP framing |
| 10 | Retrieval math: cosine, RRF, no-threshold measurement |
| 10 | Failure deep dive: Q1/Q2/Q4 |
| 10 | Evaluation design and ablations |

**Must hit:** the negative threshold result, the finalization bug, the evaluation gap.
**Red flags:** claiming production readiness, claiming all base queries pass, reciting architecture without tradeoffs.

### Round 2 — Applied AI / product (45 min)

| Time | Segment |
|---:|---|
| 5 | User and problem |
| 10 | Demo narrative: C1→C2, E, Q1/Q5, no-corpus |
| 10 | Trust: citations, refusal, failure UX |
| 10 | Latency/cost/prioritization |
| 10 | “What would you ship next?” |

**Must hit:** honest refusal, citation provenance, Q4 failure and fix, P0/P1 prioritization.
**Red flags:** “It works” without field/citation checks; ignoring latency; hiding the video gap.

### Round 3 — Agentic AI SDE (45 min)

| Time | Segment |
|---:|---|
| 5 | Loop and role ownership |
| 10 | MCP/tool schemas/state isolation |
| 10 | Code review: finalizer, action, context |
| 10 | Tests: safe gate, destructive legacy test, eval |
| 10 | Security: SSRF, artifact path, prompt injection |

**Must hit:** `EAGV3_STATE_DIR` bug, current-run scoping, `isError` gap, test isolation.
**Red flags:** adding guardrails without a failure; claiming the sandbox is a security boundary.

### Mock scoring

| Dimension | 1 | 2 | 3 | 4 |
|---|---|---|---|---|
| Structure | Rambles | Some signposting | Clear | Crisp, layered |
| Evidence | Anecdote | One trace | Multiple traces | Trace + numbers + limits |
| Tradeoffs | None | One | Several | Alternatives + revisit conditions |
| Failure analysis | Blames model | Symptom only | Root cause | Boundary + class fix |
| Honesty | Overclaims | Some caveats | Clear limits | Quantifies uncertainty |
| Role fit | Generic | Some tailoring | Strong | Uses their language |

Target: 3+ on every dimension. 4 on failure analysis and honesty.

---

## 5. Coding/debugging drills

### Drill 1 — Make the finalizer goal-aware

Write the smallest patch that guarantees no answer is returned while any goal is open. Then write the regression test.

**Sketch:**

```python
# after the loop
if any(not g.done for g in prior_goals):
    incomplete = "goals-open"
```

But be careful: the final answer may already be in history. The UI should return the answer *and* mark it incomplete, or return an explicit incomplete message. Decide the product behavior.

**Follow-up:** Should a capped run return a partial answer? Yes, with `incomplete_reason="max-iterations"` and the open goals listed.

### Drill 2 — Check MCP `isError`

Write the patch:

```python
result = await session.call_tool(...)
text = _result_to_text(result)
if getattr(result, "isError", False):
    return _fail(text)
```

Then handle transport errors:

```python
try:
    result = await session.call_tool(...)
except Exception as exc:
    return _fail(f"ERROR: transport failure: {exc}")
```

**Follow-up:** Why not rely on text prefixes? Because the envelope is the authority; page prose can contain “error” without a tool failure.

### Drill 3 — Protect metadata

Write the merge:

```python
core = {"chunk": chunk, "chunk_index": i, "total_chunks": len(chunks), "source": source}
safe = {k: v for k, v in (metadata or {}).items() if k in ALLOWED_META}
value = {**safe, **core}
```

**Follow-up:** Why not just reject any metadata key that collides? That is also fine; whitelisting is clearer.

### Drill 4 — Validate artifact IDs

Write a regex and a resolved-path check.

```python
if not re.fullmatch(r"art:[0-9a-f]{16}", artifact_id):
    raise ValueError("invalid artifact id")
path = (STORE / f"{artifact_id[4:]}.bin").resolve()
if STORE.resolve() not in path.parents:
    raise ValueError("artifact path escape")
```

**Follow-up:** Why is `removeprefix` alone insufficient? `../` survives and escapes the store.

### Drill 5 — Resolve the Q4 prompt conflict

Write a precedence rule:

```text
For comparison goals, if only one side has evidence, search for the missing side.
This overrides the no-research rule.
```

Then add a test: initial retrieval covers one side; assert a second search happens before the answer.

**Follow-up:** If the model still fails, enforce at the evidence boundary: a comparison goal requires at least two distinct source IDs.

### Drill 6 — Design a retrieval eval

Write a JSON schema for:

```json
{"query": "...", "relevant": ["mem:..."], "hard_negatives": ["mem:..."],
 "type": "semantic|exact|multi_source|comparison"}
```

Then write the metric functions for Recall@5 and MRR.

**Follow-up:** What is the minimum viable eval? 30 queries, 2 labels each, one ablation.

---

## 6. Weak-spot log

Copy this table and fill it after each drill.

| Date | Question | Gap | Evidence I missed | Fix | Re-test |
|---|---|---|---|---|---|
| | | | | | |
| | | | | | |
| | | | | | |

---

## 7. Seven-day drill schedule

| Day | Focus | Drill |
|---|---|---|
| 1 | Architecture | Q1–Q10; Whiteboard 1 |
| 2 | Retrieval | Q11–Q20; Whiteboard 2; Drill 6 |
| 3 | Failures | Q21–Q28; Whiteboard 3; Drill 1 |
| 4 | Evaluation | Q19, Q26; design a 30-query set |
| 5 | Systems/security | Q27–Q30; Whiteboard 4; Drills 2–4 |
| 6 | Mocks | Rounds 1–3; record and score |
| 7 | Demo | Record C1→C2, E, Q1/Q5, no-corpus; rest |

---

## 8. Final pre-interview checklist

- [ ] I can draw the loop in 60 seconds.
- [ ] I can explain descriptor embeddings and the recall ceiling.
- [ ] I can derive RRF and explain why no threshold.
- [ ] I can explain Q1’s finalization bug from the JSON.
- [ ] I can explain Q4’s prompt conflict.
- [ ] I can explain why A/F2/G are partial.
- [ ] I can state five numbers without notes.
- [ ] I can tell three STAR stories in 2 minutes each.
- [ ] I can name the P0/P1 issues and the first fix.
- [ ] I can say what the evidence does **not** prove.

*End of drills.*
