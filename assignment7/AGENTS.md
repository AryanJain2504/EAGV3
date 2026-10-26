# Assignment 7 Agent Guide

This directory contains the Assignment 7 implementation and the book RAG
application built on the Session 6/7 agent architecture.

Durable review notes and interview material live in
[LEARNINGS.md](LEARNINGS.md). It records what worked, what failed, the current
known gaps, and the evidence caveats.

## Scope and rubric

The assignment requires:

1. Run the eight Session 7 base queries A-H within their stated iteration
   limits.
2. Build a real RAG application over a corpus of at least 50 meaningful items.
3. Create five custom queries against that corpus.
4. Make at least two custom queries demonstrate semantic recall: the wording
   of the question should not simply repeat the words in the answer chunks.
5. Show that each custom query depends on the indexed corpus by recording a
   with-corpus result and a no-corpus comparison.
6. Submit a repository README containing the corpus manifest, setup/run
   instructions, eight base traces, five custom traces, and no-corpus
   comparisons, plus a short demonstration video.

PDF-to-TXT conversion is preparation for using the selected book corpus. It is
not a separate rubric feature. The agent indexes UTF-8 text; PDF extraction is
kept outside the agent loop.

## Repository layout

```text
eagV3/
  book/                         # source PDFs
  assignment7/                  # this implementation
    agent7.py                   # main agent loop
    perception.py               # intent decomposition; tool-blind
    decision.py                 # tool selection or direct answer
    action.py                   # MCP dispatch and artifact handling
    memory.py                   # durable memory and vector retrieval
    mcp_server.py               # MCP tools, indexing, knowledge search
    vector_index.py             # FAISS wrapper
    gateway.py                  # V7 gateway bridge
    extract_pdf_text.py         # offline PDF -> page-marked TXT preparation
    build_corpus.py             # generate page-range TXT records + manifest
    index_corpus.py             # repeatable batch indexing through S7 indexer
    corpus_manifest.json        # generated 53-record corpus manifest
    ARCHITECTURE.md             # HLD/LLD
    LEARNINGS.md                # review findings + interview preparation
    sandbox/                    # agent-readable text files
    state/                      # local runtime memory and FAISS files
```

The `.venv`, `state/`, `sandbox/`, `usage.json`, and `.env` files are local
runtime artifacts and are ignored by Git. Do not commit secrets or generated
runtime state.

## Architecture invariants

```text
memory.read
  -> perception.observe
  -> decision.next_step
  -> action.execute
  -> memory.record_outcome
```

- **Perception** decomposes the query into bounded intent-level goals.
- **Decision** sees the MCP tool catalogue and chooses one tool or produces
  one answer.
- **Action** executes the selected tool and manages large outputs as artifacts.
- **Memory** persists facts, tool outcomes, embeddings, and retrieval state.
- The agent loop remains sequential in Assignment 7.

### Tool-blindness rule

Perception must not name MCP tools in its `SYSTEM` prompt. It may express
intent such as “make this content searchable” or “query the existing
knowledge base.” Tool-specific selection guidance belongs in Decision's
`SYSTEM` prompt and in MCP tool docstrings.

When checking this invariant, inspect the `SYSTEM` string itself. Comments and
Python identifiers elsewhere in `perception.py` are not part of the prompt.

### Diagnostic rule

When a role behaves incorrectly:

1. Capture the complete failing trace.
2. Identify the role that emitted the wrong output.
3. Reconstruct the exact rendered input that role received.
4. Decide whether the output was rational given that input.
5. Fix the rendering boundary if required information was dropped; change a
   role prompt only when the role had the necessary information and still
   reasoned incorrectly.

Do not add anti-loop or tool-selection rules to Perception to compensate for
missing fields in memory-hit or history rendering.

## Embedding configuration

The agent calls `llm_gatewayV7` through `gateway.py` and uses `/v1/embed`.

1. Local Ollama is attempted first.
2. The local model is `nomic-embed-text`.
3. Gemini `gemini-embedding-001` is a remote fallback when configured.
4. Both providers are pinned to 768 dimensions.
5. Documents use `retrieval_document`; queries use `retrieval_query`.

Ollama model setup:

```bash
ollama pull nomic-embed-text
ollama list
curl -s http://localhost:8107/v1/embedders | python3 -m json.tool
```

Do not change the embedding model or dimension after indexing. A change
invalidates the existing FAISS vector space and requires a deliberate rebuild.

## Useful commands

Run from `assignment7`:

```bash
# Install/sync the environment
uv sync

# Compile the Python implementation
python3 -m py_compile *.py

# Extract a small page-marked pilot
python3 extract_pdf_text.py \
  "../book/Designing Machine Learning Systems.pdf" \
  "sandbox/book_pilot/designing-ml-systems-pages-1-20.txt" \
  --first-page 1 --last-page 20

# Start the agent with a query
uv run agent7.py "your question"

# Inspect the MCP server directly
uv run python -c \
  'import mcp_server; print(mcp_server.index_document("book_pilot/designing-ml-systems-pages-1-20.txt"))'
```

The gateway starts automatically when the Assignment 7 modules call
`ensure_gateway()`. It expects `../llm_gatewayV7` relative to this directory.

To rebuild the index in a fresh state and rerun all base and custom evidence,
see [Manual full-evidence rerun](#manual-full-evidence-rerun).

## Current progress

### Complete

- `S7code` was renamed to `assignment7`; gateway path resolution updated.
- HLD/LLD is documented in `ARCHITECTURE.md`.
- PDF-to-TXT pilot extraction works with page markers.
- Ollama `nomic-embed-text` is installed and returns 768-dimensional vectors.
- A 53-record corpus was generated across all three books using 30-page
  page-range records, then indexed into 1,625 searchable chunks at 400/80.
- Indexed chunks retain document ID, book, author, page range, and source PDF
  metadata.
- Cross-book retrieval smoke tests pass for ML systems, PostgreSQL WAL, and
  distributed systems topics.
- Final evidence is saved: 10 base traces and 10 custom traces. Base iterations
  (used/cap): A 2/3, B banked 6/8, C1 4/4, C2 1/3, D 6/6, E 3/5, F1 7/11,
  F2 2/3, G 2/3, H 2/3. Custom Q1/Q5 are the semantic-recall cases; Q4 is
  documented as ML-only.
- **The orchestrator is a loop and nothing else.** It reads memory, asks
  Perception to observe, asks Decision to choose, dispatches, and records the
  outcome. It derives no "evidence mode", withholds no tools, and never
  constructs a `ToolCall`. It does track open goals to decide whether the run
  may end; that final-answer state handling is still review-flagged as
  incomplete (see `LEARNINGS.md`). Everything it once did beyond the five steps
  was compensation for a problem fixed in the wrong layer.
- **Perception owns goal state.** It is the only layer that decides whether a
  goal is satisfied, because it is the only one that knows what the user asked
  for. `done` moves forward monotonically, so a later reword cannot reopen
  completed work.
- **Questions are intended to stay out of the knowledge index.** The run's
  opening text goes through `memory.remember`; a question/request should remain
  an unembedded scratchpad, while a stated fact is embedded for C1-style recall.
  The fallback write is always scratchpad and never embeds. This is a
  classifier-dependent guarantee, not a construction-level one; `LEARNINGS.md`
  records the residual risk.
- **Decision always receives the full MCP catalogue.** Withholding tools from a
  goal the orchestrator judged to be a "final synthesis" is a pre-emptive tool
  decision outside Decision. If a tool goes unused on a turn, the trace shows a
  wasted turn, which is honest.
- **A run reports what it produced.** A run that records actions but no answer
  says so, and lists the actions as actions. An action log is never presented
  as the answer the user asked for.
- **Stale artifacts are dropped.** Memory is persistent, so a goal may only
  attach bytes from an artifact this run produced.
- **Relevance is Decision's judgment.** Retrieval returns ranked top-k with no
  similarity floor: on a book-only corpus every query has a near neighbour, so
  no threshold can reject an irrelevant hit (measured: relevant 0.58-0.79
  overlapping irrelevant 0.66-0.70). Do not add one.
- `fetch_url` uses direct HTTP and standard-library HTML parsing; Playwright is
  not required for ordinary static pages.

### Deliberately absent

Do not reintroduce these without a specific, current failure to fix. Each one
was added to compensate for a problem whose real cause was one layer away, and
each made traces harder to read than the behaviour it policed.

- An `evidence_mode` on `Goal`, or any per-goal output contract in Decision.
- A citation gate that validates or rewrites Decision's answer.
- A reconciliation pass that overrides Perception's `done` flags.
- Boundary sentinels that rewrite ungrounded answers into fixed phrases.
- Per-run provider flags (`--decision-provider` and friends). Both cognitive
  roles are pinned to Gemini in code, as the reference does; `.env` still sets
  the embedding model and failover order. A flag would let one trace be
  produced under a different worker from another, which is how a run set stops
  being comparable.
- Chunk bodies are rendered for indexed facts in Decision's evidence, and
  `search_knowledge` returns full chunk bodies. Action records are filtered out
  of Decision's evidence so they cannot crowd out passages.
- Keyword or regex rules that infer a goal's intent from its English. Perception
  rewords between runs, so such a matcher is a latent failure, not a safeguard.

### Remaining for rubric completion

- Record the short demonstration video (E-H focus). Base and custom traces are
  saved under `evidence/`; B is the banked Assignment 6 graded trace
  (`run 610c1a79`, 6 iterations).

Everything else in the rubric is complete. Do not rerun B, reindex the corpus,
or change runtime prompts before the video: the saved evidence is already
comparable.

### Safe validation

The safe deterministic gate is:

```bash
uv run pytest -q test_agent_contracts.py
uv run python validate_prompts.py
```

Do **not** run `test_mcp_server.py` or the full `pytest` suite until its test
fixture is fixed. It currently deletes the real `sandbox/` directory and expects
an obsolete `list_dir` return shape.

### Known review findings

Recorded in full in `LEARNINGS.md`. The highest-priority items are:

- `_final_answer` is not goal-aware; a failed or capped run can return an
  earlier answer without an incomplete reason.
- `action.execute` ignores MCP `isError` and does not catch transport failures.
- Decision's prompt contains conflicting comparison/refusal rules; Q4 is
  one-sided as a result.
- Decision context is unbounded; multiple attached pages can exceed the
  fallback model's context window.
- Chunks embed their descriptor, not the full chunk body, limiting semantic
  recall.
- `fetch_url` lacks SSRF/size protection; artifact IDs and `index_document`
  metadata need validation.
- Memory/index persistence is not atomic or locked.
- `validate_prompts.MCP_TOOL_NAMES` omits `compute_date` and `edit_file`.
- Remaining polish: `index_corpus.py` defaults, deprecated `utcnow`, and dead
  `crawl4ai` code.

Documentation drift found during the review (tool count, route wording,
remaining-work list, ARCHITECTURE overclaim) was corrected in this pass.
Future work: chapter/section metadata, and isolating `test_mcp_server.py`
before it is ever added back to a broad pytest run.

## Evidence and data handling

Keep reproducible traces outside runtime state, for example:

```text
evidence/
  base/
    query-a.md
    ...
    query-h.md
  custom/
    query-01-with-corpus.md
    query-01-without-corpus.md
    ...
  corpus_manifest.json
```

Do not confuse a plausible LLM answer with proof of retrieval. Each custom
trace should show the indexed source chunks or provenance and then show how
the answer changes when the corpus is unavailable.

## Manual full-evidence rerun

This is an optional verification runbook. Do **not** run it before the demo
video unless you intentionally want new evidence. It writes to a fresh
`evidence/rerun-<timestamp>/` directory and a fresh `EAGV3_STATE_DIR`; it must
never overwrite `evidence/base/` or `evidence/custom/`.

The repository's default `state/` is not the full corpus state. It currently
contains only a few records. The source corpus is `sandbox/corpus/` (53
records), and the final evidence was produced with 400/80 chunking.

### 1. Create isolated output and state

```bash
set -o pipefail
cd /Users/aryan/code/eagv3/assignment7

STAMP=$(date +%Y%m%d-%H%M%S)
OUT="evidence/rerun-$STAMP"
mkdir -p "$OUT/base" "$OUT/custom"

export EAGV3_STATE_DIR="$(mktemp -d /tmp/a7_rerun.XXXXXX)"
echo "state: $EAGV3_STATE_DIR"
```

Build the corpus index in the fresh state. The explicit flags are required:
the script defaults are 700/120, while the final evidence uses 400/80.

```bash
uv run python index_corpus.py --chunk-size 400 --overlap 80
```

Verify the expected chunk count:

```bash
jq '[.[] | select(.kind == "fact" and .value.chunk != null)] | length' \
  "$EAGV3_STATE_DIR/memory.json"
```

Expected: `1625`.

Indexing is slow because every chunk is embedded. Interleaved
`POST /v1/embed` `HTTP/1.1 200 OK` lines are normal progress, not a hang; the
script also prints one `[n/53]` line per document.

Do not run `index_corpus.py` twice against the same state: it appends records
and inflates the corpus count.

### 2. Run the custom queries first (book-only baseline)

Run Q1-Q5 before E and F1. E and F1 index `papers/*.md`; adding those sources
before the custom runs changes the book-only retrieval baseline.

Define the capture helper:

```bash
runq() {
  local output="$1"
  local query="$2"
  uv run python -u agent7.py "$query" 2>&1 | tee "$output"
}
```

Run the five with-corpus queries:

```bash
runq "$OUT/custom/query-01-with-corpus.md" \
  "How can a system detect when its predictions are becoming unreliable after deployment?"

runq "$OUT/custom/query-02-with-corpus.md" \
  "What protects a database from losing committed work after a crash?"

runq "$OUT/custom/query-03-with-corpus.md" \
  "Why can copying data between services make correctness harder?"

runq "$OUT/custom/query-04-with-corpus.md" \
  "Compare observability needs in a data platform and an ML system."

runq "$OUT/custom/query-05-with-corpus.md" \
  "What lets a model-serving system respond quickly while still learning from later outcomes?"
```

### 3. Run the base queries

Do **not** rerun B. It is the banked Assignment 6 graded trace at
`evidence/base/query-b.md` (`run 610c1a79`, 6 iterations). The remaining base
queries are fresh A7 runs.

Run A, C1 -> C2, D, then E, F1 -> F2, G, and H. C1/C2 and F1/F2 must share the
same `EAGV3_STATE_DIR`; do not reset state between those pairs.

```bash
runq "$OUT/base/query-a.md" \
  "Fetch https://en.wikipedia.org/wiki/Claude_Shannon and tell me his birth date, death date, and three key contributions to information theory."

runq "$OUT/base/query-c1.md" \
  "My mom's birthday is 15 May 2026. Remember that and create reminders for two weeks before and on the day."

runq "$OUT/base/query-c2.md" \
  "When is mom's birthday?"

runq "$OUT/base/query-d.md" \
  'Search for "Python asyncio best practices", read the top 3 results, and give me a short numbered list of the advice they agree on.'

runq "$OUT/base/query-e.md" \
  "Index the file papers/attention.md and tell me what the three key contributions of the Transformer architecture are according to this paper."

runq "$OUT/base/query-f1.md" \
  "Index every .md file under papers/. Confirm how many chunks were indexed in total."

runq "$OUT/base/query-f2.md" \
  "Across the papers I have indexed, what do they say about chain-of-thought reasoning?"

runq "$OUT/base/query-g.md" \
  "Across these papers, how do they handle the credit assignment problem?"

runq "$OUT/base/query-h.md" \
  "Compare how the ReAct paper and the Chain-of-Thought paper differ in their treatment of intermediate reasoning."
```

B remains the existing `evidence/base/query-b.md`; do not overwrite it.

### 4. Run the no-corpus comparisons

The corpus must be genuinely unavailable, not merely absent from the prompt.
Park both source directories, use a fresh empty state for every query, and
bound the failure with `A7_MAX_ITERATIONS=6`.

```bash
(
  set -e

  if [ -e sandbox/corpus.keep ] || [ -e sandbox/papers.keep ]; then
    echo "Refusing to start: a previous .keep directory exists" >&2
    exit 1
  fi

  restore_corpus() {
    if [ -d sandbox/corpus.keep ]; then
      mv sandbox/corpus.keep sandbox/corpus
    fi
    if [ -d sandbox/papers.keep ]; then
      mv sandbox/papers.keep sandbox/papers
    fi
  }
  trap restore_corpus EXIT

  mv sandbox/corpus sandbox/corpus.keep
  mv sandbox/papers sandbox/papers.keep

  run_no_corpus() {
    local number="$1"
    local query="$2"
    local state
    state=$(mktemp -d "/tmp/a7_nocorpus_${number}.XXXXXX")
    EAGV3_STATE_DIR="$state" A7_MAX_ITERATIONS=6 \
      uv run python -u agent7.py "$query" 2>&1 \
      | tee "$OUT/custom/query-${number}-without-corpus.md"
  }

  run_no_corpus 01 "How can a system detect when its predictions are becoming unreliable after deployment?"
  run_no_corpus 02 "What protects a database from losing committed work after a crash?"
  run_no_corpus 03 "Why can copying data between services make correctness harder?"
  run_no_corpus 04 "Compare observability needs in a data platform and an ML system."
  run_no_corpus 05 "What lets a model-serving system respond quickly while still learning from later outcomes?"
)
```

Verify that both directories were restored:

```bash
test -d sandbox/corpus && test -d sandbox/papers && echo "corpus restored"
```

Every no-corpus trace should contain:

```text
No answer was produced.
```

If it does not, the empty state or sandbox isolation failed.

### 5. Where the results live

| Result | Location |
|---|---|
| Console trace | `$OUT/base/*.md` and `$OUT/custom/*.md` (created by `tee`) |
| Raw event trace | `$EAGV3_STATE_DIR/history_<run_id>.jsonl` |
| Final result | `$EAGV3_STATE_DIR/result_<run_id>.json` |
| Memory and citation records | `$EAGV3_STATE_DIR/memory.json` |
| FAISS index | `$EAGV3_STATE_DIR/index.faiss` |
| FAISS row-to-memory map | `$EAGV3_STATE_DIR/index_ids.json` |
| Raw fetched/indexed artifacts | `$EAGV3_STATE_DIR/artifacts/` |

Each markdown trace starts with:

```text
run <run_id>  ─  query: ...
```

That ID maps the markdown to `history_<run_id>.jsonl` and
`result_<run_id>.json` in the state directory. Without `tee`, the markdown is
not created, but the state trace still is.

### 6. Verify a run

Set the markdown file and extract its run ID:

```bash
md="$OUT/base/query-a.md"
run_id=$(awk '/^run /{print $2; exit}' "$md")
```

Inspect the final result and goal state:

```bash
jq '{query, final_answer, incomplete_reason, goals}' \
  "$EAGV3_STATE_DIR/result_${run_id}.json"
```

`incomplete_reason: null` does **not** prove that every goal finished. The
current final-answer logic can return an answer while a goal still has
`"done": false`. Check both:

```bash
jq -r '[.run_id,
        (.incomplete_reason // "null"),
        ([.goals[].done] | all),
        (.final_answer | length)] | @tsv' \
  "$EAGV3_STATE_DIR/result_${run_id}.json"
```

Check the iteration count and the visible decision/action trail:

```bash
rg -c '^─── iter' "$md"
rg -n '^FINAL:|\[incomplete\]|\[decision\]|\[action\]' "$md"
```

Resolve cited `mem:` IDs to their actual corpus chunks:

```bash
rg -o 'mem:[0-9a-f]+' "$md" | sort -u
jq -r --arg id 'mem:5c6a9dc9' '
  .[] | select(.id == $id) |
  [.source, .value.title, .value.page_start, .value.page_end, .descriptor] | @tsv
' "$EAGV3_STATE_DIR/memory.json"
```

Inspect the chunk text before trusting the citation:

```bash
jq -r --arg id 'mem:5c6a9dc9' '
  .[] | select(.id == $id) | .value.chunk[:800]
' "$EAGV3_STATE_DIR/memory.json"
```

For Q1 and Q5, confirm the semantic-recall claim by checking that the query
phrasing is absent from the corpus:

```bash
rg -i "predictions becoming unreliable" sandbox/corpus
rg -i "respond quickly while still learning" sandbox/corpus
```

Both commands should return no matches. The answers should still resolve to
monitoring/drift and stateful-training passages.

Finally, run the safe deterministic gate:

```bash
uv run pytest -q test_agent_contracts.py
uv run python validate_prompts.py
```

Do **not** run the full `pytest` suite while `test_mcp_server.py` is
destructive and stale. The PoP gate passes, but its tool list checks only 10 of
the 12 names; see the known review findings.
