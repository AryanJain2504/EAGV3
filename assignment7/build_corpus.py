"""Build a page-range TXT corpus and manifest from the book PDFs.

Corpus records are deliberately independent files so they can be indexed by
the existing Session 7 `index_document` path without adding PDF parsing to the
agent loop.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from pathlib import Path


BOOKS = [
    {
        "book_id": "ddia",
        "title": "Designing Data-Intensive Applications",
        "author": "Martin Kleppmann",
        "filename": "Designing Data-Intensive Applications - Martin Kleppmann.pdf",
        "pages": 613,
    },
    {
        "book_id": "ml-systems",
        "title": "Designing Machine Learning Systems",
        "author": "Chip Huyen",
        "filename": "Designing Machine Learning Systems.pdf",
        "pages": 389,
    },
    {
        "book_id": "postgres-internals",
        "title": "PostgreSQL 14 Internals",
        "author": "Egor Rogov",
        "filename": "postgresql_internals-14_en.pdf",
        "pages": 548,
    },
]


def extract_range(pdf: Path, first_page: int, last_page: int) -> str:
    result = subprocess.run(
        [
            "pdftotext",
            "-layout",
            "-f",
            str(first_page),
            "-l",
            str(last_page),
            str(pdf),
            "-",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or f"pdftotext failed for {pdf}")
    blocks = []
    for offset, page_text in enumerate(result.stdout.split("\f")):
        cleaned = page_text.strip()
        if cleaned:
            blocks.append(f"[Page {first_page + offset}]\n\n{cleaned}")
    return "\n\n".join(blocks).strip()


def build(book_dir: Path, output_dir: Path, manifest_path: Path, page_span: int) -> None:
    if shutil.which("pdftotext") is None:
        raise RuntimeError("pdftotext is required")
    output_dir.mkdir(parents=True, exist_ok=True)
    records = []
    for book in BOOKS:
        pdf = book_dir / book["filename"]
        if not pdf.is_file():
            raise FileNotFoundError(pdf)
        book_dir_out = output_dir / book["book_id"]
        book_dir_out.mkdir(parents=True, exist_ok=True)
        record_number = 0
        for first in range(1, book["pages"] + 1, page_span):
            last = min(first + page_span - 1, book["pages"])
            record_number += 1
            doc_id = f"{book['book_id']}:pages-{first}-{last}"
            relative_path = Path("corpus") / book["book_id"] / f"pages-{first}-{last}.txt"
            output_path = output_dir.parent / relative_path
            content = extract_range(pdf, first, last)
            header = (
                f"Document ID: {doc_id}\n"
                f"Book: {book['title']}\n"
                f"Author: {book['author']}\n"
                f"Source PDF: {book['filename']}\n"
                f"Page range: {first}-{last}\n\n"
            )
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(header + content + "\n", encoding="utf-8")
            records.append(
                {
                    "doc_id": doc_id,
                    "book_id": book["book_id"],
                    "title": book["title"],
                    "author": book["author"],
                    "source_pdf": f"book/{book['filename']}",
                    "page_start": first,
                    "page_end": last,
                    "path": str(relative_path),
                    "record_number": record_number,
                }
            )
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "page_span": page_span,
                "record_count": len(records),
                "records": records,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"Generated {len(records)} records in {output_dir.parent / 'corpus'}")
    print(f"Manifest: {manifest_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--book-dir", type=Path, default=Path("../book"))
    parser.add_argument("--output-dir", type=Path, default=Path("sandbox/corpus"))
    parser.add_argument("--manifest", type=Path, default=Path("corpus_manifest.json"))
    parser.add_argument("--page-span", type=int, default=30)
    args = parser.parse_args()
    if args.page_span < 1:
        raise SystemExit("--page-span must be positive")
    build(args.book_dir, args.output_dir, args.manifest, args.page_span)


if __name__ == "__main__":
    main()
