"""Extract page-marked text from a PDF for Assignment 7 corpus preparation.

This is an offline preprocessing step. The agent indexes the generated UTF-8
text file; it does not parse PDF binaries inside the agent loop.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
from pathlib import Path


def extract(pdf: Path, output: Path, first_page: int, last_page: int | None) -> None:
    if not pdf.is_file():
        raise FileNotFoundError(f"PDF not found: {pdf}")
    if shutil.which("pdftotext") is None:
        raise RuntimeError("pdftotext is required; install Poppler before extracting text")
    if first_page < 1:
        raise ValueError("first_page must be >= 1")
    if last_page is not None and last_page < first_page:
        raise ValueError("last_page must be >= first_page")

    output.parent.mkdir(parents=True, exist_ok=True)
    command = ["pdftotext", "-layout", "-f", str(first_page)]
    if last_page is not None:
        command.extend(["-l", str(last_page)])
    command.extend([str(pdf), "-"])

    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        detail = result.stderr.strip() or "unknown pdftotext error"
        raise RuntimeError(f"PDF extraction failed: {detail}")

    raw_pages = result.stdout.split("\f")
    page_blocks = []
    for offset, page_text in enumerate(raw_pages):
        page_number = first_page + offset
        cleaned = page_text.strip()
        if cleaned:
            page_blocks.append(f"[Page {page_number}]\n\n{cleaned}")
    pages = "\n\n".join(page_blocks)
    page_end = last_page if last_page is not None else "end"
    header = (
        f"Source PDF: {pdf.name}\n"
        f"Pages extracted: {first_page}-{page_end}\n"
        "Page markers are approximate when the PDF has complex layout.\n\n"
    )
    output.write_text(header + pages.strip() + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--first-page", type=int, default=1)
    parser.add_argument("--last-page", type=int, default=20)
    args = parser.parse_args()
    extract(args.pdf, args.output, args.first_page, args.last_page)
    print(f"Wrote {args.output}")


if __name__ == "__main__":
    main()
