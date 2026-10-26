# Assignment 7: Technical Book Knowledge Base with RAG

A production-grade, framework-free autonomous RAG agent built on the **MPDA (Memory–Perception–Decision–Action)** architecture. The system indexes and retrieves across a 53-record technical book corpus (1,625 chunks) using hybrid dense-and-sparse retrieval with Reciprocal Rank Fusion (RRF), strict tool-blind goal decomposition, and verified provenance.

---

## Architecture Overview

The system strictly adheres to the Session 7 architectural invariant: **the loop is the agent**. Roles never communicate directly; every boundary is mediated by typed Pydantic v2 contracts.

```text
       ┌─────────────────────────────────────────────────────────┐
       │                       agent7.py                         │
       │                   (Orchestrator Loop)                   │
       └────┬─────────────────┬───────────────────┬───────────┬──┘
            │                 │                   │           │
     1. memory.read    2. observe           3. next_step  4. execute
            │                 │                   │           │
            ▼                 ▼                   ▼           ▼
     ┌─────────────┐   ┌─────────────┐     ┌─────────────┐  ┌───────────┐
     │  memory.py  │   │perception.py│     │ decision.py │  │ action.py │
     │(FAISS + JSON│   │(Tool-Blind  │     │(Atomic Tool │  │(MCP stdio │
     │ Embeddings) │   │ Goal State) │     │ XOR Answer) │  │ Dispatch) │
     └─────────────┘   └─────────────┘     └─────────────┘  └─────┬─────┘
                                                                  │
                                                      5. record_outcome
                                                                  ▼
                                                            ┌───────────┐
                                                            │memory.py  │
                                                            └───────────┘
```

### Layer Responsibilities

| Role | Implementation | Core Invariant |
|---|---|---|
| **Memory** | [`memory.py`](memory.py) | Dual-tier store: structured metadata in `state/memory.json` + 768-dim `nomic-embed-text` vectors in FAISS. Vector-first read with keyword fallback. |
| **Perception** | [`perception.py`](perception.py) | **Strictly tool-blind.** Contains **zero MCP tool names**; decomposes user queries into bounded goals and tracks completion strictly from action history. |
| **Decision** | [`decision.py`](decision.py) | Atomic worker. Chooses exactly one action per turn: **single tool call XOR final answer** (never both). Maps goal intent to available MCP tools. |
| **Action** | [`action.py`](action.py) | Dispatches tool calls to `mcp_server.py` over stdio. Tool outputs exceeding 4 KB are automatically offloaded to `state/artifacts/` to protect LLM context windows. |
| **Gateway** | [`gateway.py`](gateway.py) | Sole LLM transport (`:8107`). Pinned to `gemini-3.5-flash-lite` with local Ollama (`qwen3.5:9b`) fallback. |

---

## Technical Book Corpus

The knowledge base is built from three canonical systems engineering books:

| Book | Author | Extracted Records | Total Chunks | Topics Covered |
|---|---|---:|---:|---|
| *Designing Data-Intensive Applications* | Martin Kleppmann | 21 | 632 | Replication, consensus, partitioning, transactions, batch/stream processing |
| *Designing Machine Learning Systems* | Chip Huyen | 13 | 412 | Data distribution shifts, model monitoring, training pipelines, continual learning |
| *PostgreSQL 14 Internals* | Egor Rogov | 19 | 581 | WAL (Write-Ahead Logging), buffer cache, MVCC, indexing architectures |
| **Total Corpus** | — | **53 records** | **1,625 chunks** | Full manifest: [`corpus_manifest.json`](corpus_manifest.json) |

### Chunking & Retrieval Strategy
- **Chunk Window**: 400 words with 80-word overlap.
- **Embeddings**: 768-dimensional normalized vectors via `nomic-embed-text`.
- **Hybrid Search**: `mcp_server.search_knowledge` combines dense FAISS vector similarity and sparse BM25 keyword matching via **Reciprocal Rank Fusion (RRF)**, ensuring both conceptual and exact keyword recall.

---

## Quickstart & Setup

### Prerequisites
- Python 3.11+
- `uv` package manager
- `ollama` (with `nomic-embed-text` installed)
- `pdftotext` (Poppler utilities)

### 1. Installation
```bash
cd assignment7
uv sync
ollama pull nomic-embed-text
```

### 2. Build and Index the Corpus
```bash
# Extract 53 UTF-8 text records from book PDFs
python3 build_corpus.py --page-span 30

# Chunk, embed, and index into FAISS + memory.json
uv run python index_corpus.py \
  --manifest corpus_manifest.json \
  --chunk-size 400 \
  --overlap 80
```

### 3. Run the Agent
```bash
# Example book query
uv run agent7.py "What protects a database from losing committed work after a crash?"
```

---

## Deterministic Verification & Contract Tests

Every boundary condition and prompt contract is verified with automated tests:

```bash
# 1. Run all 71 deterministic contract tests
uv run pytest -q test_agent_contracts.py

# 2. Run the Proof-of-Prompt (PoP) static validator
uv run python validate_prompts.py
```

### Invariant Checks Verified:
- **Perception Tool-Blindness**: Verified that `perception.SYSTEM` contains zero MCP tool names.
- **Decision Invariant**: Verified single tool execution XOR final answer enforcement.
- **Artifact Threshold**: Verified that tool outputs $>4\text{ KB}$ offload to disk artifacts (`#art:<hash>`).
- **Prompt Safety**: All checks pass and are recorded in [`pop_validation.json`](pop_validation.json).

---

## Evidence & Evaluation Results

All evaluation runs are saved under `evidence/` with full provenance, including exact iterations, goals, tool calls, retrieved chunks, and final answers.

### 1. Eight Base Benchmark Traces (A–H)
Standard session queries demonstrating generalist agent capabilities within strict iteration bounds:

| Query | Focus Area | Iterations (Actual / Limit) | Status | Evidence Log |
|---|---|:---:|:---:|---|
| **A** | Shannon Wikipedia Date & Contributions | 2 / 3 | Passed | [`evidence/base/query-a.md`](evidence/base/query-a.md) |
| **B** | Tokyo Weekend Forecast & Activities | 6 / 8 | Passed | [`evidence/base/query-b.md`](evidence/base/query-b.md) |
| **C1** | Mom's Birthday Memory Store | 4 / 4 | Passed | [`evidence/base/query-c1.md`](evidence/base/query-c1.md) |
| **C2** | Mom's Birthday Memory Recall (0 Tools) | 1 / 3 | Passed | [`evidence/base/query-c2.md`](evidence/base/query-c2.md) |
| **D** | Asyncio Agreement Synthesis (3 Web Sources) | 6 / 6 | Passed | [`evidence/base/query-d.md`](evidence/base/query-d.md) |
| **E** | Single Document Index & Extract (`attention.md`) | 3 / 5 | Passed | [`evidence/base/query-e.md`](evidence/base/query-e.md) |
| **F1** | Batch Markdown Indexing (`papers/`) | 7 / 11 | Passed | [`evidence/base/query-f1.md`](evidence/base/query-f1.md) |
| **F2** | Cross-Run Recall across Indexed Papers | 2 / 3 | Passed | [`evidence/base/query-f2.md`](evidence/base/query-f2.md) |
| **G** | Synonym / Semantic Recall (*Credit Assignment*) | 2 / 3 | Passed | [`evidence/base/query-g.md`](evidence/base/query-g.md) |
| **H** | Cross-Document Synthesis (ReAct vs. CoT) | 2 / 3 | Passed | [`evidence/base/query-h.md`](evidence/base/query-h.md) |

---

### 2. Custom Book RAG Traces (With-Corpus vs. Without-Corpus Ablation)
Five custom queries tested against the technical book corpus. Each query was run twice: once with the indexed corpus, and once in an ablation control state where the corpus was parked.

| # | Query | Semantic Recall? | With-Corpus (Grounded) | Without-Corpus (Ablation) |
|---|---|:---:|---|---|
| **Q1** | *Detecting unreliable predictions after deployment* | **Yes** *(wording absent from text)* | Cites monitoring & data drift passages from Chip Huyen ([`log`](evidence/custom/query-01-with-corpus.md)). | Halts honestly: *"No answer was produced"* ([`log`](evidence/custom/query-01-without-corpus.md)). |
| **Q2** | *Protecting database from committed work loss* | No *(domain lookup)* | Cites WAL, battery RAM, and fsync from Rogov & Kleppmann ([`log`](evidence/custom/query-02-with-corpus.md)). | Halts honestly: *"No answer was produced"* ([`log`](evidence/custom/query-02-without-corpus.md)). |
| **Q3** | *Why copying data between services complicates correctness* | No *(domain lookup)* | Cites microservice event ordering and state isolation from Kleppmann ([`log`](evidence/custom/query-03-with-corpus.md)). | Halts honestly: *"No answer was produced"* ([`log`](evidence/custom/query-03-without-corpus.md)). |
| **Q4** | *Observability needs in data platforms vs. ML systems* | No *(comparison)* | Cites silent performance failures vs. explicit error metrics ([`log`](evidence/custom/query-04-with-corpus.md)). | Halts honestly: *"No answer was produced"* ([`log`](evidence/custom/query-04-without-corpus.md)). |
| **Q5** | *Serving models quickly while learning from later outcomes* | **Yes** *(wording absent from text)* | Cites stateful checkpoint fine-tuning from Chip Huyen ([`log`](evidence/custom/query-05-with-corpus.md)). | Halts honestly: *"No answer was produced"* ([`log`](evidence/custom/query-05-without-corpus.md)). |

> **Ablation Invariant**: In all five without-corpus runs, the agent executes `search_knowledge`, receives 0 candidate chunks, retries, and bounds itself within 6 iterations, safely concluding: `FINAL: No answer was produced.` It never hallucinates from pre-training weights.

---

## Repository Structure

```text
assignment7/
├── agent7.py                 # Central orchestrator loop (The Agent)
├── perception.py             # Tool-blind goal state tracker
├── decision.py               # Atomic worker: ToolCall XOR Final Answer
├── action.py                 # MCP stdio executor & artifact offloader (>4KB)
├── memory.py                 # Dual-tier persistent store (FAISS + memory.json)
├── mcp_server.py             # MCP server exposing 12 local tools
├── vector_index.py           # FAISS index wrapper (Cosine / L2)
├── gateway.py                # LLM & Embedding transport bridge (:8107)
├── build_corpus.py           # Offline PDF-to-TXT corpus generator
├── index_corpus.py           # Batch corpus indexing script
├── corpus_manifest.json      # Generated 53-record corpus manifest
├── schemas.py                # Strict Pydantic v2 contract models
├── test_agent_contracts.py   # 71 deterministic contract test cases
├── validate_prompts.py       # PoP static prompt validator
├── pop_validation.json       # Generated PoP verification scorecard
├── evidence/
│   ├── base/                 # 10 base benchmark query traces (A–H)
│   └── custom/               # 10 custom book RAG traces (with & without corpus)
├── ARCHITECTURE.md           # High-level and low-level architectural design
├── INDEXING.md               # Detailed chunking and index maintenance guide
└── LEARNINGS.md              # Engineering journal, post-mortems & interview drills
```

---

## Submission Checklist

- [x] **53 Corpus Records**: Generated from 3 technical books and cataloged in `corpus_manifest.json`.
- [x] **1,625 Searchable Chunks**: Indexed at 400/80 with `nomic-embed-text` into FAISS + `memory.json`.
- [x] **Eight Base Traces (A–H)**: Verified within rubric bounds and archived under `evidence/base/`.
- [x] **Five Custom Book Traces**: Tested against the corpus and archived under `evidence/custom/`.
- [x] **Two Semantic Recall Traces**: Verified that Q1 and Q5 use terminology absent from source chunks.
- [x] **Five Ablation Comparisons**: Verified that without-corpus runs halt with *"No answer was produced"*.
- [x] **Perception Tool-Blindness**: Verified with static inspection and deterministic tests.
- [x] **Deterministic Contracts**: 71/71 tests passing in `test_agent_contracts.py`.
- [x] **Prompt Proof-of-Prompt**: Scorecard verified in `pop_validation.json`.
- [x] **Documentation**: Production-grade README, architecture specs, and indexing manual included.
