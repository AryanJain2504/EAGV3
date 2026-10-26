"""Index every record in corpus_manifest.json through the S7 indexer."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import mcp_server


def index(manifest_path: Path, chunk_size: int, overlap: int) -> None:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    records = manifest["records"]
    indexed = 0
    total_chunks = 0
    for number, record in enumerate(records, 1):
        path = record["path"]
        result = mcp_server.index_document(
            path,
            chunk_size=chunk_size,
            overlap=overlap,
            metadata={
                "doc_id": record["doc_id"],
                "book_id": record["book_id"],
                "title": record["title"],
                "author": record["author"],
                "page_start": record["page_start"],
                "page_end": record["page_end"],
                "source_pdf": record["source_pdf"],
            },
        )
        chunks = int(result.get("chunks_indexed", 0))
        indexed += 1
        total_chunks += chunks
        print(f"[{number}/{len(records)}] {record['doc_id']}: {chunks} chunks")
    print(f"Indexed {indexed} records and {total_chunks} chunks")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=Path("corpus_manifest.json"))
    parser.add_argument("--chunk-size", type=int, default=700)
    parser.add_argument("--overlap", type=int, default=120)
    args = parser.parse_args()
    if not args.manifest.is_file():
        raise SystemExit(f"Manifest not found: {args.manifest}; run build_corpus.py first")
    index(args.manifest, args.chunk_size, args.overlap)


if __name__ == "__main__":
    main()
