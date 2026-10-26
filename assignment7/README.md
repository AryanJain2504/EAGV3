# Assignment 7 — Book Knowledge Base with RAG

This project implements the Session 7 agent architecture and extends it into
a searchable technical-book knowledge base.

For the full development history, failure analysis, fixes, validation plan,
and interview notes, see [ASSIGNMENT7_ENGINEERING_JOURNAL.md](ASSIGNMENT7_ENGINEERING_JOURNAL.md).
For the consolidated, interview-ready review of what worked, what failed, and
what remains, see [LEARNINGS.md](LEARNINGS.md).

The corpus contains:

- *Designing Data-Intensive Applications* — Martin Kleppmann
- *Designing Machine Learning Systems* — Chip Huyen
- *PostgreSQL 14 Internals* — Egor Rogov

The assignment application uses retrieval-augmented generation (RAG) to find
relevant passages from these books and answer questions with source metadata.

## Assignment requirements

The rubric requires:

1. Run the eight Session 7 base queries A-H within their stated iteration
   limits.
2. Build a real RAG application over at least 50 meaningful corpus items.
3. Create five custom queries against the corpus.
4. Make at least two custom queries demonstrate semantic recall.
5. Show with-corpus and without-corpus results for each custom query.
6. Submit a repository README with setup, corpus manifest, traces, and
   architecture evidence.
7. Record a short demonstration video.

PDF-to-TXT conversion is preparation for using the selected PDF corpus. It is
not a separate assignment feature. The agent indexes UTF-8 text through the
existing Session 7 indexing path.

## Current implementation status

### Complete

- Clean Session 7 loop preserved:

  ```text
  memory.read
    -> perception.observe
    -> decision.next_step
    -> action.execute
    -> memory.record_outcome
  ```

- Perception is tool-blind: its `SYSTEM` prompt contains no MCP tool names.
- Memory embeds facts with the gateway's 768-dimensional embedder. Facts are
  recallable; scratchpads are not embedded; failed tool outcomes are stored
  without embeddings. Reads are vector-first with keyword fallback.
- `search_knowledge` fuses vector and keyword hits with reciprocal-rank
  fusion. Keyword hits must share at least two tokens with the query, and the
  tool ranks the full fact set so action records cannot crowd out chunks.
- Corpus: 53 page-range records across the three books; **1,625 chunks**
  indexed at 400/80 with book, author, page range, document ID, and source PDF
  metadata.
- 12 MCP tools are available to the orchestrator, including
  `index_document`, `search_knowledge`, `compute_date`, and `fetch_url`.
- Decision and Perception use Gemini `gemini-3.5-flash-lite` (both pin
  `provider="g"`). Transient 429/500/502/503/504 responses are retried three
  times at 0/5/15 s, then the call falls back to local Ollama
  `qwen3.5:9b` (`A7_FALLBACK_MODEL` overrides).
- Decision handles semantic-lens questions, explicit comparisons, requested
  item counts, and refuses-to-refuse when chunk evidence is attached (one
  forced answer retry).
- Perception preserves goal order, appends discovered files, keeps a request
  for N examples as one goal, requires a fetched value for check/weather
  goals, and adds a report goal for action requests.
- `A7_MAX_ITERATIONS` bounds no-corpus diagnostics (default 20).
- 71 contract tests (`test_agent_contracts.py`) and the PoP gate
  (`validate_prompts.py`) pass. Perception contains zero MCP tool names and
  zero eval-query entities.
- Evidence is saved under `evidence/base/` (A–H) and `evidence/custom/`
  (five with-corpus and five no-corpus traces). B is the banked Assignment 6
  graded trace (run 610c1a79, 6 iterations); the other base traces were rerun on the final
  code. No-corpus runs use an empty state with `sandbox/corpus` and
  `sandbox/papers` parked and are capped at 6 iterations; each ends with
  "No answer was produced."

Final base results: A 2/3, B banked 6, C1 4/4, C2 1/3, D 6/6, E 3/5,
F1 7/11, F2 2/3, G 2/3, H 2/3.

### Still required for submission

- Short demonstration video (E–H focus).

Do not run `test_mcp_server.py` or the full pytest suite until its fixture is
fixed; it currently deletes the real `sandbox/` and expects an obsolete
`list_dir` shape. Use the safe deterministic gate documented below.

## Architecture

```text
PDF books
  -> offline page-aware TXT extraction
  -> corpus records and manifest
  -> chunking
  -> 768-dimensional embeddings
  -> FAISS + JSON metadata store
  -> Session 7 agent loop
       memory.read
       -> perception.observe
       -> decision.next_step
       -> action.execute
       -> memory.record_outcome
  -> answer with book/page provenance
```

### Layer responsibilities

| Layer | Responsibility |
|---|---|
| Memory | Persistent facts, tool outcomes, embeddings, and retrieval |
| Perception | Decompose user intent into bounded goals |
| Decision | Select one MCP tool or produce one answer |
| Action | Execute the selected MCP tool |
| MCP server | File operations, document indexing, and knowledge search |
| FAISS | Similarity search over normalized embedding vectors |
| Gateway V7 | LLM calls and embedding calls |

### URL-fetch reliability boundary

For a query containing an explicit URL, the orchestrator creates two stable
goals: fetch the exact URL, then answer the original question using only the
successful current-run fetch artifact. Persistent memory may still be used for
unrelated tasks, but it cannot replace the requested URL source. Failed fetches
are not stored as successful artifacts and cannot complete the goal.

The `fetch_url` MCP tool uses direct HTTP first:

```text
URL -> httpx request -> HTML parser -> readable text -> artifact
```

This avoids a browser dependency for ordinary static pages and keeps browser
rendering outside the critical path.

### Rubric architecture proof

The tool-blindness gate checks the literal `Perception.SYSTEM` prompt, not
ordinary implementation code. An AST-based check currently reports:

```text
MCP names in Perception SYSTEM: []
tool-blindness gate passed
```

The orchestrator may contain deterministic references to tool names for
reliability enforcement. This does not expose tool names to Perception or move
tool selection into the Perception prompt.

### Perception tool-blindness

Perception must not select tools by name. It emits intent such as:

```text
Make this document searchable.
Query the existing knowledge base.
Compare the retrieved material.
```

Decision receives the MCP tool catalogue and maps intent to a tool. Tool
selection guidance belongs in Decision's system prompt and MCP docstrings.

Check the invariant:

```bash
cd assignment7
uv run python - <<'PY'
import perception

tools = [
    "web_search", "fetch_url", "get_time", "currency_convert",
    "compute_date",
    "read_file", "list_dir", "create_file", "update_file",
    "edit_file",
    "index_document", "search_knowledge",
]
found = [name for name in tools if name in perception.SYSTEM]
print("tool names in Perception SYSTEM:", found)
assert not found
PY
```

## Repository layout

```text
eagV3/
  book/                         # source PDFs
  assignment7/
    agent7.py                   # main agent loop
    perception.py               # intent decomposition
    decision.py                 # tool selection / answer
    action.py                   # MCP dispatch and artifacts
    memory.py                   # durable memory and vector retrieval
    mcp_server.py               # MCP tools and indexing
    vector_index.py             # FAISS wrapper
    gateway.py                  # gateway V7 bridge
    extract_pdf_text.py         # small/manual PDF pilot extraction
    build_corpus.py             # full corpus TXT + manifest generation
    index_corpus.py             # repeatable batch indexing
    corpus_manifest.json        # generated corpus catalogue
    validate_prompts.py         # PoP prompt-contract gate
    pop_validation.json         # gate output (generated)
    test_agent_contracts.py     # deterministic contract tests
    test_mcp_server.py          # legacy MCP tests; do not run until isolated
    ARCHITECTURE.md             # HLD/LLD
    INDEXING.md                 # detailed indexing procedure
    AGENTS.md                   # project instructions and invariants
    LEARNINGS.md                # review findings + interview preparation
    ASSIGNMENT7_ENGINEERING_JOURNAL.md  # chronological engineering record
    sandbox/corpus/             # generated UTF-8 corpus records
    evidence/                   # saved traces (base + custom)
    state/                      # runtime memory and FAISS state
```

Generated `sandbox/`, `state/`, `.venv/`, `.env`, and `usage.json` files are
ignored by Git. Do not commit secrets or runtime state.

## Prerequisites

- macOS or Linux
- Python 3.11+
- `uv`
- `pdftotext` from Poppler
- Ollama
- The source PDFs in `../book`

Install or verify the external tools:

```bash
command -v uv
command -v pdftotext
command -v ollama
```

## Setup

From the repository root, the Assignment 7 directory:

```bash
cd eagV3/assignment7
uv sync
ollama pull nomic-embed-text
ollama list
```

The Assignment 7 gateway bridge automatically starts `llm_gatewayV7` on port
8107 when needed. Verify the embedding configuration:

```bash
curl -s http://localhost:8107/v1/embedders | python3 -m json.tool
```

Expected important fields:

```json
{
  "order": ["ollama", "gemini"],
  "models": {
    "ollama": "nomic-embed-text",
    "gemini": "gemini-embedding-001"
  },
  "fixed_dim": 768
}
```

The cognitive roles run on Gemini `gemini-3.5-flash-lite`; local Ollama
`qwen3.5:9b` is the fallback after transient Gemini failures. Routing classifiers also
prefer Ollama. The embedding path remains Ollama first with `nomic-embed-text`,
with Gemini embeddings as the configured fallback. Do not change the embedding
model or dimension after building the FAISS index without rebuilding the index.

## Build the book corpus

### 1. Generate TXT records and manifest

Run:

```bash
python3 build_corpus.py --page-span 30
```

This command:

1. Reads the three PDFs from `../book`.
2. Extracts fixed page ranges using local `pdftotext`.
3. Adds `[Page N]` markers.
4. Writes UTF-8 files under `sandbox/corpus/`.
5. Writes `corpus_manifest.json`.

The current configuration creates 53 records:

| Book | Records |
|---|---:|
| Designing Data-Intensive Applications | 21 |
| Designing Machine Learning Systems | 13 |
| PostgreSQL 14 Internals | 19 |
| **Total** | **53** |

The manifest is a catalogue, not the vector database. A record contains the
source PDF, page range, document ID, and generated TXT path.

### 2. Index the records

Run:

```bash
uv run python index_corpus.py \
  --manifest corpus_manifest.json \
  --chunk-size 400 \
  --overlap 80
```

For each manifest record, the batch script calls the same
`mcp_server.index_document()` function exposed by the MCP server. The
indexing process:

1. Reads a TXT record from the sandbox.
2. Splits it into 400-word windows.
3. Uses 80 words of overlap.
4. Embeds every chunk with `retrieval_document`.
5. Stores chunk text and provenance in `memory.json`.
6. Stores vectors in FAISS.
7. Stores vector-row-to-memory-ID mappings in `index_ids.json`.

The current corpus produces 1,625 searchable chunks.

Detailed indexing documentation is in `INDEXING.md`.

### Important rebuild warning

Running `index_corpus.py` again against the same state appends new memory
records. Do not repeatedly rerun it when measuring corpus size. Preserve any
evidence first, then intentionally rebuild the runtime state before a clean
indexing run.

## Test retrieval directly

Run a direct knowledge-search smoke test:

```bash
uv run python - <<'PY'
import mcp_server

queries = [
    "How do machine learning systems stay reliable when data changes?",
    "What is write-ahead logging used for in PostgreSQL?",
    "How do distributed systems handle replication and consistency?",
]

for query in queries:
    print(f"\nQUERY: {query}")
    hits = mcp_server.search_knowledge(query, k=3)
    for hit in hits:
        print(hit["metadata"])
        print(hit["chunk"][:500].replace("\n", " "))
PY
```

Each hit should include source metadata such as:

```text
doc_id
book_id
title
author
page_start
page_end
source_pdf
```

## Run the full agent

Run a book question through the complete agent loop:

```bash
uv run agent7.py \
  "What is write-ahead logging used for in PostgreSQL? Explain it from the indexed books."
```

The trace shows:

```text
memory.read
perception.observe
decision.next_step
action.execute
memory.record_outcome
```

Decision should select the knowledge-search tool when indexed facts are
available. The final answer should be based on retrieved chunks, not on
re-reading the original PDF.

## Validation commands

### Python syntax

```bash
python3 -m py_compile *.py
```

### Safe deterministic tests

```bash
uv run pytest -q test_agent_contracts.py
uv run python validate_prompts.py
```

`test_mcp_server.py` is currently stale: its fixture deletes the real
`sandbox/` and it expects `list_dir` to return a list instead of a dict. Do not
include it in a broad pytest run until it is isolated.

### Network tests

These require external services and credentials:

```bash
uv run pytest -q -m network
```

### Embedding tests

These require the V7 gateway and an available embedding provider:

```bash
uv run pytest -q -m embed
```

### Full test suite (not currently safe)

```bash
# Do not run until test_mcp_server.py is isolated.
uv run pytest -q
```

The contract tests are safe. The MCP test file is not: it wipes the real
sandbox. If a test appears to hang, run the targeted test file or marker
separately; network, browser, and embedding tests have external dependencies.

## Assignment evidence workflow

Create evidence outside runtime state:

```text
evidence/
  base/
    query-a.md
    query-b.md
    query-c1.md
    query-c2.md
    query-d.md
    query-e.md
    query-f1.md
    query-f2.md
    query-g.md
    query-h.md
  custom/
    query-01-with-corpus.md
    query-01-without-corpus.md
    ...
    query-05-with-corpus.md
    query-05-without-corpus.md
  corpus_manifest.json
```

For each trace record:

- Exact user query
- Iteration count
- Goals emitted by Perception
- Tool selected by Decision
- Retrieved sources and metadata
- Final answer
- Whether the corpus was available

Do not treat a plausible answer as proof of retrieval. The trace must show
which indexed chunks supported the answer.

## Base-query checklist

Run the eight Session 7 queries from the course material verbatim and keep
within their stated iteration limits:

- Query A: Shannon Wikipedia
- Query B: Tokyo activities and weather
- Query C: durable memory across runs
- Query D: Asyncio multi-source synthesis
- Query E: single-document index and extract
- Query F: cross-run document recall
- Query G: synonym recall
- Query H: cross-document synthesis

Query B command:

```bash
uv run python -u agent7.py \
  "Find three family-friendly things to do in Tokyo this weekend, check Saturday's weather forecast there, and tell me which one is most appropriate." \
  2>&1 | tee evidence/base/query-b.md
```

All base commands (run serially; F run 1 → run 2 share `state/` by design;
E is independent; clean only same-query sandbox outputs between attempts):

```bash
# A — bar: full dates + exactly 3 quoted contributions, ≤8 iters
uv run python -u agent7.py \
  "Fetch https://en.wikipedia.org/wiki/Claude_Shannon and tell me his birth date, death date, and three key contributions to information theory." \
  2>&1 | tee evidence/base/query-a.md

# C run 1 + run 2 share `state/` by design (banked: 3 iters + 2 iters)
uv run python -u agent7.py \
  "My mom's birthday is 15 May 2026. Remember that and create reminders for two weeks before and on the day." \
  2>&1 | tee evidence/base/query-c1.md

uv run python -u agent7.py \
  "When is mom's birthday?" \
  2>&1 | tee evidence/base/query-c2.md

# B — bar: forecast stated + sourced choice, nothing invented (above)

# D — bar: 3 artifact fetches, then numbered agreement list
uv run python -u agent7.py \
  'Search for "Python asyncio best practices", read the top 3 results, and give me a short numbered list of the advice they agree on.' \
  2>&1 | tee evidence/base/query-d.md

# E — bar: file indexed, chunks retrieved, 3 sourced contributions
uv run python -u agent7.py \
  "Index the file papers/attention.md and tell me what the three key contributions of the Transformer architecture are according to this paper." \
  2>&1 | tee evidence/base/query-e.md

# F run 1 — bar: list, per-file index goals, total chunk count
uv run python -u agent7.py \
  "Index every .md file under papers/. Confirm how many chunks were indexed in total." \
  2>&1 | tee evidence/base/query-f1.md

# F run 2 — bar: fresh process retrieves without reindexing (same state/)
uv run python -u agent7.py \
  "Across the papers I have indexed, what do they say about chain-of-thought reasoning?" \
  2>&1 | tee evidence/base/query-f2.md

# G — bar: answer succeeds although "credit assignment" is absent from the
# chunks (verify absence with grep before claiming semantic recall)
uv run python -u agent7.py \
  "Across these papers, how do they handle the credit assignment problem?" \
  2>&1 | tee evidence/base/query-g.md

# H — bar: retrieved ReAct + CoT chunks support the contrast
uv run python -u agent7.py \
  "Compare how the ReAct paper and the Chain-of-Thought paper differ in their treatment of intermediate reasoning." \
  2>&1 | tee evidence/base/query-h.md
```

B evidence is the banked Assignment 6 graded trace (run 610c1a79, 6 iterations). The other
base traces were rerun on the final code. The exact prompts and cleaned traces
are in `evidence/base/` rather than reconstructed from memory.

## Custom book-query checklist

Create five queries that require the indexed book corpus. At least two should
use semantic recall, where the question's wording is not copied directly from
the answer passages. Good categories include:

- Cross-book reliability comparison
- PostgreSQL internals lookup with page provenance
- Distributed consistency versus ML data quality
- Cross-domain architecture synthesis
- Production design recommendation grounded in multiple books

Run every custom query twice:

1. With the indexed corpus.
2. Without the indexed corpus.

```bash
mkdir -p evidence/custom

# With corpus (example: query 1)
uv run python -u agent7.py \
  "How can a system detect when its predictions are becoming unreliable after deployment?" \
  2>&1 | tee evidence/custom/query-01-with-corpus.md
```

The no-corpus run must be performed with the corpus unavailable, not merely
ignored by the prompt. Swap in a fresh empty state AND park the corpus
source files (otherwise the model can simply read them, voiding the
comparison), run, then restore everything (never rebuild the index for
this — restore it):

```bash
mv state state.keep && mkdir -p state state/artifacts
mv sandbox/corpus sandbox/corpus.keep && mv sandbox/papers sandbox/papers.keep
uv run python -u agent7.py \
  "How can a system detect when its predictions are becoming unreliable after deployment?" \
  2>&1 | tee evidence/custom/query-01-without-corpus.md
rm -rf state && mv state.keep state
mv sandbox/corpus.keep sandbox/corpus && mv sandbox/papers.keep sandbox/papers
```

The five custom queries (two or more must use wording absent from the
answer chunks — verify with `rg` before claiming semantic recall):

```text
1. How can a system detect when its predictions are becoming unreliable after deployment?
2. What protects a database from losing committed work after a crash?
3. Why can copying data between services make correctness harder?
4. Compare observability needs in a data platform and an ML system.
5. What lets a model-serving system respond quickly while still learning from later outcomes?
```

Record how the answer loses book-specific evidence,
citations, or confidence.

### Custom-query results

All ten traces are saved under `evidence/custom/`. The with-corpus answers cite
indexed chunks from `sandbox:corpus/...`; every no-corpus run ends with
"No answer was produced." Q1 and Q5 are the semantic-recall cases: the query
wording ("predictions becoming unreliable", "respond quickly while still
learning") does not appear in the answer chunks, but the corpus surfaces
monitoring/drift and stateful-training passages. Q2 and Q3 are book-specific
retrieval; Q4 is grounded in the ML-systems monitoring passage but its answer
covers only the ML side of the requested comparison.

For the no-corpus runs the runtime state was an empty `EAGV3_STATE_DIR`,
`sandbox/corpus` and `sandbox/papers` were parked, and
`A7_MAX_ITERATIONS=6` bounded the failure so it ends cleanly.

## Submission checklist

- [ ] 53+ corpus records generated
- [ ] Corpus manifest included or reproducibly generated
- [ ] Corpus indexed successfully
- [ ] Eight base traces saved
- [ ] Five custom traces saved
- [ ] At least two semantic-recall traces saved
- [ ] Five no-corpus comparisons saved
- [ ] Perception tool-blindness verified
- [ ] HLD/LLD documented
- [ ] Setup and test commands documented
- [ ] Short demonstration video recorded
