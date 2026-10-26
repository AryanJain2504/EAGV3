# Assignment 7 Book Knowledge Base

## High-level design

```text
PDF books
  -> offline page-aware TXT extraction
  -> corpus records with book/page/source metadata
  -> chunking and 768-dimensional embeddings
  -> FAISS index + JSON text/metadata store
  -> Session 7 agent loop
       memory.read
       -> perception.observe
       -> decision.next_step
       -> action.execute
       -> memory.record_outcome
  -> cited answer
```

PDF conversion is preparation for the selected corpus. It is outside the
agent loop because Session 7's indexing contract accepts UTF-8 text, not PDF
binary containers.

## Low-level design

### Corpus preparation

`extract_pdf_text.py` calls the local `pdftotext` executable and writes UTF-8
text with source and page-range metadata. The pilot extracts a small page
range first. Full-corpus processing comes only after the pilot is validated.

### Indexing

The existing `index_document` MCP tool reads a sandbox text file, creates
sliding-window chunks, and writes each chunk as a `fact` memory item. Each
item stores the original chunk, source label, chunk position, and embedding.
The repeatable `index_corpus.py` command reads `corpus_manifest.json` and calls
this same indexing function for every generated TXT record. The complete
operational procedure is documented in `INDEXING.md`.

### Embeddings

The Assignment 7 agent calls `llm_gatewayV7` through `gateway.py`.

1. Ollama is attempted first with `nomic-embed-text`.
2. Gemini `gemini-embedding-001` is used only as the configured fallback.
3. Both paths are pinned to 768 dimensions.
4. Document chunks use `retrieval_document`; user queries use
   `retrieval_query`.
5. The model/dimension must not change after the FAISS index is built.

Ollama requires a local model download (`ollama pull nomic-embed-text`).
Gemini is a remote API provider and is not downloaded locally.

### Retrieval and answer flow

`search_knowledge` fuses vector hits with keyword hits using reciprocal-rank
fusion (keyword hits must share at least two query tokens), then returns the
stored chunk text and provenance. It ranks the full fact set so action records
cannot crowd out passages. Decision selects this tool because it sees the MCP
catalogue. Perception remains tool-blind and emits only intent-level goals.

### Layer ownership

Each layer has exactly one job, and no layer takes another's:

```text
Perception:   intent and goal state (the only owner of `done`)
Decision:     the MCP catalogue and the tool choice
Orchestrator: the loop, and nothing else
Action:       tool execution
Memory:       persistent RAG retrieval
```

The orchestrator holds no goal-completion override. It reads Perception's
`done` flags to decide whether the run may end, but does not derive an evidence
mode from a goal, does not override those flags, does not filter or withhold the
tool catalogue, and never constructs a `ToolCall`. Decision
receives the full catalogue on every turn and makes one atomic choice — one tool
call or one answer — with no hidden second synthesis pass.

This is a deliberate correction, not the original design. An earlier version
gave the orchestrator a parallel notion of correctness: a per-goal evidence mode
derived from whatever happened to be in memory, a reconciliation pass that
overrode `done`, a citation gate that discarded answers naming unrendered ids,
and boundary sentinels that rewrote ungrounded answers into fixed phrases. The
cost was not only ~700 lines. A goal the mode-classifier filed incorrectly had
its tools withheld, so Decision could not retrieve, and the run stalled with a
self-describing non-answer instead of a trace that showed what actually went
wrong. A layer that cannot see the input cannot be repaired by a branch
outside it; the fix belongs at the rendering boundary that withheld the input.

The rubric grep gate applies to the literal `Perception.SYSTEM` prompt. A
static AST check currently finds zero MCP tool names in that string. Tool
names in Perception's implementation-level history validation are not prompt
instructions and do not expose the MCP catalogue to the model.

### Persistence

```text
assignment7/state/memory.json
assignment7/state/index.faiss
assignment7/state/index_ids.json
```

The JSON store contains readable chunk text and metadata. FAISS contains
vectors, while `index_ids.json` maps vector positions to memory IDs.

### Ingest and retrieval

The supported ingest and search paths are separated by writer and by `kind`:

```text
index_document / add_fact   -> kind "fact",          embedded, retrievable
record_outcome (success)    -> kind "tool_outcome",  embedded, retrievable
memory.remember (statement) -> kind "fact",          embedded, retrievable
memory.remember (question)  -> kind "scratchpad",    NOT embedded
```

A query is not knowledge. Routing the run's search string through a
fact-ingest path puts it where an item embeds its own text, scores maximally
against the very query that produced it, and can outrank every genuine
document chunk. The final writer asks Memory's classifier to separate a stated
fact from a question; the fallback is an unembedded scratchpad. This is a
classifier-dependent guarantee, not a construction-level one, so a
misclassified question remains a residual retrieval risk.

`search_knowledge` returns indexed document chunks only. A stored sentence with
no `chunk` payload is not a passage of a document and must never pose as a
corpus result.

Retrieval returns ranked top-k with no similarity floor. On a book-only corpus
every query has a near neighbour, so no absolute or relative threshold can
reject an irrelevant hit (measured: relevant 0.58-0.79, overlapping irrelevant
0.66-0.70). Relevance is Decision's judgment, made against the evidence it was
shown. Decision is instructed to cite the chunk ids (`mem:...`) it relied on,
while the `source` beside each chunk is provenance for the reader rather than
something to cite in its place.

### Grounding and reporting

Decision answers in prose from the evidence it was given, and is instructed to
use only that evidence and to say plainly when the available material does not
contain the answer. There is no structured sub-schema and no citation gate: a
second, parallel notion of "grounded" would have to be reconciled against the
prose on every turn, and its validator had to special-case empty references to
avoid rejecting every plain answer.

Two properties are enforced in code rather than in a prompt, because both are
properties of the run rather than of any single turn:

- **Current-run evidence only.** Memory is persistent, so a goal may only attach
  bytes from an artifact this run produced. A handle from an earlier run is
  dropped rather than resolved.
- **Honest final reporting.** A run that records actions but no answer reports
  exactly that and lists the actions as actions. An action log is never
  presented as the answer the user asked for, because that is how an agent looks
  successful while having answered nothing.

### Typed iteration boundaries

Pydantic validates every typed object crossing the loop: `Goal`,
`Observation`, `DecisionOutput`, `ToolCall`, `ActionResult`, `HistoryEvent`,
`MemoryItem`, and `Artifact`. Before MCP dispatch, the action layer validates
the tool call arguments against the JSON Schema advertised by that MCP tool.
Invalid arguments become a typed failed action. Artifact handles passed where a
path or URL belongs are rejected as well: an `art:...` id names bytes already
supplied under ATTACHED ARTIFACTS, never a location on disk.

Large artifacts are truncated to a bounded head-and-tail window before Decision
sees them, with the truncation made visible in the prompt so a partial read is
never mistaken for a whole page.
