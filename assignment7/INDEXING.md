# Book Corpus Indexing Process

This document describes exactly how the current Assignment 7 book corpus was
prepared and indexed.

## Is manual indexing required?

No. The assignment requires a working RAG application over at least 50
meaningful corpus items and evidence that retrieval works. It does not require
manually indexing each file.

We use an automated batch command, but it calls the same S7 indexing function
used by the agent's `index_document` MCP tool. This keeps the experiment
reproducible without changing the architecture.

## End-to-end flow

```text
source PDF
  -> pdftotext page-range extraction
  -> UTF-8 TXT corpus record
  -> corpus_manifest.json entry
  -> index_corpus.py
  -> mcp_server.index_document()
  -> 400-word chunks with 80-word overlap
  -> one embedding per chunk
  -> memory.json + index.faiss + index_ids.json
```

## Step 1: Prepare the environment

Run from `assignment7`:

```bash
uv sync
ollama pull nomic-embed-text
curl -s http://localhost:8107/v1/embedders | python3 -m json.tool
```

The V7 gateway uses Ollama first with `nomic-embed-text`. The embedding model
returns 768-dimensional vectors. Gemini is only a remote fallback when its API
key is configured. The model and dimension must remain fixed after indexing.

## Step 2: Generate TXT corpus records and the manifest

```bash
python3 build_corpus.py --page-span 30
```

This command:

1. Reads the three source PDFs from `../book`.
2. Iterates through each book in fixed page ranges.
3. Calls local `pdftotext` for each page range.
4. Adds `[Page N]` markers to the extracted text.
5. Writes records under `sandbox/corpus/<book-id>/`.
6. Writes `corpus_manifest.json`.

The manifest is the input catalogue for the indexing step. The current
configuration creates 53 records:

- 21 DDIA records
- 13 Designing Machine Learning Systems records
- 19 PostgreSQL Internals records

Each record stores its document ID, book, author, source PDF, page range, and
relative TXT path.

## Step 3: Index every manifest record

```bash
uv run python index_corpus.py \
  --manifest corpus_manifest.json \
  --chunk-size 400 \
  --overlap 80
```

`index_corpus.py` loads the manifest and loops over records. For each record it
calls:

```python
mcp_server.index_document(
    path,
    chunk_size=400,
    overlap=80,
    metadata={
        "doc_id": ...,
        "book_id": ...,
        "title": ...,
        "author": ...,
        "page_start": ...,
        "page_end": ...,
        "source_pdf": ...,
    },
)
```

The indexing function then:

1. Reads the TXT file from the sandbox.
2. Splits words into sliding windows.
3. Keeps 80 words of overlap between adjacent 400-word chunks.
4. Embeds each chunk using the V7 `/v1/embed` endpoint with
   `retrieval_document`.
5. Stores the chunk text and provenance as a `fact` item.
6. Adds the vector and memory ID to FAISS.

The full current corpus produced 1,625 searchable chunks.

## Step 4: Understand the persisted files

```text
state/memory.json
```

Readable memory records, including chunk text, embeddings, provenance, source
book, and page range.

```text
state/index.faiss
```

The FAISS vector index used for similarity search.

```text
state/index_ids.json
```

The mapping from FAISS's integer row positions to application-level memory
IDs. FAISS returns an ID position; the application uses this file and
`memory.json` to recover the readable chunk and citations.

## Step 5: Query the indexed corpus

The search path uses `retrieval_query` for the question:

```text
user question
  -> query embedding
  -> FAISS similarity search
  -> memory ID lookup
  -> original chunk and metadata
  -> Decision synthesizes the answer
```

Direct smoke test:

```bash
uv run python - <<'PY'
import mcp_server

hits = mcp_server.search_knowledge(
    "What is write-ahead logging used for in PostgreSQL?",
    k=3,
)
for hit in hits:
    print(hit["metadata"])
    print(hit["chunk"][:500])
PY
```

When the full agent is used, Perception emits an intent-level goal, Decision
selects `search_knowledge`, Action executes it, and Decision synthesizes the
answer from the returned chunks. Perception never receives the MCP tool
catalogue.

## Important operational note

The current batch script is intended for the initial corpus build. Running it
again appends another set of fact records because each write receives a new
memory ID. Do not rerun it repeatedly against the same `state/` directory when
measuring corpus size. For a clean rebuild, preserve any needed evidence first,
then deliberately reset the Assignment 7 runtime state and rerun the build.
