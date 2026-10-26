"""
MCP server for EAGV3 Session 7.

Twelve tools, stdio transport:
    web_search, fetch_url, get_time, currency_convert, compute_date,
    read_file, list_dir, create_file, update_file, edit_file,
    index_document, search_knowledge

web_search:        Tavily primary, DuckDuckGo fallback. Hard-capped at 5 results.
fetch_url:         direct HTTP first; browser rendering is not required for
                   ordinary static pages.
compute_date:      deterministic relative-date arithmetic for Decision.
index_document:    Chunks a sandbox file or artifact and writes the chunks as
                   fact records into Memory, where they become FAISS-searchable.
search_knowledge:  Vector search over indexed facts. Same backend as
                   memory.read but exposed to the model as a tool.

Usage for tavily and duckduckgo is logged to ./usage.json with monthly
rollover and a soft cap of 950/1000 on Tavily.

File tools are sandboxed under ./sandbox/. Run:  python mcp_server.py
"""

from __future__ import annotations

import json
import os
import re
import threading
from calendar import monthrange
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
from ddgs import DDGS
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

# Same-directory imports for the Memory and Artifact services so that the
# new index_document / search_knowledge tools can delegate into them.
import sys
sys.path.insert(0, str(Path(__file__).parent))
import artifacts as _artifacts  # noqa: E402
import memory as _memory  # noqa: E402

MAX_SEARCH_RESULTS = 5  # hard cap — Tavily prices per result

load_dotenv(Path(__file__).parent / ".env")

mcp = FastMCP("eagv3-s7-server")

SANDBOX = Path(__file__).parent / "sandbox"
SANDBOX.mkdir(exist_ok=True)

USAGE_PATH = Path(__file__).parent / "usage.json"
MONTHLY_CAP = 950  # leave 50/mo headroom on Tavily
_usage_lock = threading.Lock()


def _safe(path: str) -> Path:
    p = (SANDBOX / path).resolve()
    base = SANDBOX.resolve()
    if p != base and base not in p.parents:
        raise ValueError(f"Path '{path}' escapes the sandbox")
    return p


def _empty_usage(month: str) -> dict:
    return {
        "month": month,
        "tavily": {"count": 0, "errors": 0},
        "duckduckgo": {"count": 0, "errors": 0},
    }


class _ReadableTextParser(HTMLParser):
    """Extract readable text from ordinary HTML without a browser."""

    _ignored = {"script", "style", "noscript", "template", "svg"}

    def __init__(self) -> None:
        super().__init__()
        self._ignored_depth = 0
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag.lower() in self._ignored:
            self._ignored_depth += 1
        elif self._ignored_depth == 0 and tag.lower() in {
            "p", "div", "article", "section", "li", "h1", "h2", "h3",
            "h4", "h5", "h6", "br", "tr",
        }:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in self._ignored and self._ignored_depth:
            self._ignored_depth -= 1
        elif self._ignored_depth == 0 and tag.lower() in {
            "p", "div", "article", "section", "li", "h1", "h2", "h3",
            "h4", "h5", "h6", "br", "tr",
        }:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._ignored_depth == 0 and data.strip():
            self.parts.append(data)


def _html_to_text(html: str) -> str:
    parser = _ReadableTextParser()
    parser.feed(html)
    lines = [" ".join(line.split()) for line in "".join(parser.parts).splitlines()]
    return "\n".join(line for line in lines if line).strip()


async def _direct_http_fetch(url: str, timeout: int) -> dict:
    headers = {
        "User-Agent": "eagv3-assignment7/1.0 (+direct-fetch)",
        "Accept": "text/html,application/xhtml+xml,text/plain,application/json",
    }
    async with httpx.AsyncClient(
        timeout=timeout,
        follow_redirects=True,
        headers=headers,
    ) as client:
        response = await client.get(url)
    response.raise_for_status()
    content_type = response.headers.get("content-type", "").lower()
    if "text/html" in content_type or "xhtml" in content_type:
        text = _html_to_text(response.text)
        output_type = "text/plain"
    else:
        text = response.text
        output_type = content_type.split(";", 1)[0] or "text/plain"
    if not text.strip():
        raise ValueError("direct HTTP response contained no readable text")
    return {
        "status": response.status_code,
        "content_type": output_type,
        "length_bytes": len(text.encode("utf-8")),
        "text": text,
        "source": "direct_http",
        "final_url": str(response.url),
    }


def _load_usage() -> dict:
    month = datetime.now().strftime("%Y-%m")
    if not USAGE_PATH.exists():
        return _empty_usage(month)
    try:
        data = json.loads(USAGE_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return _empty_usage(month)
    if data.get("month") != month:
        return _empty_usage(month)
    for k in ("tavily", "duckduckgo"):
        data.setdefault(k, {"count": 0, "errors": 0})
    return data


def _save_usage(data: dict) -> None:
    USAGE_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _bump(provider: str, field: str = "count") -> None:
    with _usage_lock:
        data = _load_usage()
        data[provider][field] = data[provider].get(field, 0) + 1
        _save_usage(data)


def _under_cap(provider: str) -> bool:
    return _load_usage()[provider]["count"] < MONTHLY_CAP


def _tavily_search(query: str, max_results: int) -> list[dict]:
    from tavily import TavilyClient

    client = TavilyClient(os.environ["TAVILY_API_KEY"])
    resp = client.search(query=query, max_results=max_results, search_depth="advanced")
    return [
        {
            "title": r.get("title", ""),
            "url": r.get("url", ""),
            "snippet": r.get("content", ""),
        }
        for r in resp.get("results", [])
    ]


def _ddg_search(query: str, max_results: int) -> list[dict]:
    hits: list[dict] = []
    with DDGS() as ddgs:
        for backend in ("auto", "html", "lite"):
            try:
                hits = list(ddgs.text(query, max_results=max_results, backend=backend))
            except Exception:
                hits = []
            if hits:
                break
    return [
        {
            "title": h.get("title", ""),
            "url": h.get("href", ""),
            "snippet": h.get("body", ""),
        }
        for h in hits
    ]


async def _crawl4ai_fetch(url: str) -> dict:
    from crawl4ai import AsyncWebCrawler

    # crawl4ai uses Rich which writes via its own captured stdout reference, so
    # contextlib.redirect_stdout doesn't catch it. Redirect at the file-descriptor
    # level — crawl4ai's banner / [FETCH] / [SCRAPE] markers would otherwise
    # corrupt the MCP stdio JSON-RPC stream.
    saved_fd = os.dup(1)
    os.dup2(2, 1)
    try:
        async with AsyncWebCrawler(verbose=False) as crawler:
            r = await crawler.arun(url=url)
    finally:
        os.dup2(saved_fd, 1)
        os.close(saved_fd)
    # r.markdown is a str subclass (StringCompatibleMarkdown) that Pydantic
    # serializes as {} because its real field is private. Pull the raw string
    # out and force a plain str so FastMCP serializes correctly.
    md = r.markdown
    raw = (
        getattr(md, "raw_markdown", None)
        or getattr(md, "fit_markdown", None)
        or md
        or r.cleaned_html
        or r.html
        or ""
    )
    text = str(raw)
    return {
        "status": int(getattr(r, "status_code", None) or 200),
        "content_type": "text/markdown",
        "length_bytes": len(text.encode("utf-8")),
        "text": text,
    }


@mcp.tool()
def web_search(query: str, max_results: int = 5) -> list[dict]:
    """Search the web (Tavily primary, DDG fallback). Hard-capped at 5 results. Use for live external facts. For already-indexed material call search_knowledge instead of re-searching. Example: web_search("python asyncio tutorial", 3)."""
    max_results = max(1, min(max_results, MAX_SEARCH_RESULTS))
    if os.environ.get("TAVILY_API_KEY") and _under_cap("tavily"):
        try:
            results = _tavily_search(query, max_results)
            if results:
                _bump("tavily")
                return results
        except Exception:
            _bump("tavily", "errors")
    results = _ddg_search(query, max_results)
    _bump("duckduckgo")
    return results


@mcp.tool()
async def fetch_url(url: str, timeout: int = 20) -> dict:
    """Fetch readable text directly over HTTP. Does not require Playwright or Chromium. Use to open one specific page found by search (one unvisited URL per step). Never pass artifact handles (art:...) — attached bytes are already available. Example: fetch_url("https://example.com/docs")."""
    return await _direct_http_fetch(url, timeout)


@mcp.tool()
def get_time(timezone: str = "UTC") -> dict:
    """Current time in a named IANA timezone. Use whenever a goal needs the present time — never guess it. For calendar arithmetic on a known anchor date use compute_date instead. Example: get_time("Asia/Kolkata")."""
    tz = ZoneInfo(timezone)
    now = datetime.now(tz)
    offset = now.utcoffset()
    offset_hours = offset.total_seconds() / 3600 if offset else 0.0
    return {
        "iso": now.isoformat(),
        "human": now.strftime("%A, %d %B %Y %H:%M:%S %Z"),
        "timezone": timezone,
        "offset_hours": offset_hours,
    }


@mcp.tool()
def currency_convert(amount: float, from_currency: str, to_currency: str) -> dict:
    """Convert money between ISO-3 currencies via frankfurter.dev. Use for explicit amount+currency conversions only, not for general price research. Example: currency_convert(100, "USD", "INR")."""
    f = from_currency.upper()
    t = to_currency.upper()
    url = f"https://api.frankfurter.dev/v1/latest?amount={amount}&base={f}&symbols={t}"
    with httpx.Client(timeout=20, follow_redirects=True) as client:
        r = client.get(url)
        r.raise_for_status()
        data = r.json()
    converted = data["rates"][t]
    return {
        "amount": amount,
        "from": f,
        "to": t,
        "rate": converted / amount if amount else 0.0,
        "converted": converted,
        "date": data["date"],
        "source": "frankfurter.dev",
    }


@mcp.tool()
def compute_date(anchor: str = "", offset: str = "", expression: str = "") -> dict:
    """Resolve a relative calendar date into an exact date. Always call this
    before creating anything dated from relative timing — never compute
    the calendar date yourself. Two forms: anchor+offset, e.g.
    compute_date("20 June 2026",
    "1 week before") resolves to 13 June 2026; or one whole constraint as
    expression, e.g. compute_date(expression="one week before 20 June 2026").
    For the present time use get_time instead. If expression is provided it
    takes precedence over anchor+offset.
    Offset grammar: "<N> (day|week|month|year)s? (before|after)"."""
    if expression and expression.strip():
        anchor, offset = _split_relative_expression(expression)
    if not anchor or not anchor.strip() or not offset or not offset.strip():
        raise ValueError(
            "Provide anchor+offset or a single expression like "
            "'two weeks before 15 May 2026'"
        )
    resolved = _resolve_date(anchor, offset)
    return {
        "anchor": anchor,
        "anchor_iso": _parse_anchor_date(anchor).isoformat(),
        "offset": offset,
        "resolved_iso": resolved.isoformat(),
        "resolved_human": resolved.strftime("%-d %B %Y"),
    }


_NUMBER_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
}

_RELATIVE_SPLIT_RE = re.compile(
    r"^\s*(\d+|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)"
    r"\s*(day|week|month|year)s?\s+(before|after)\s+(.+?)\s*$",
    re.IGNORECASE,
)


def _split_relative_expression(expression: str) -> tuple[str, str]:
    """Split one relative-timing constraint into (anchor, offset).

    'two weeks before 15 May 2026' -> ('15 May 2026', '2 weeks before').
    Pure string grammar; raises ValueError when the shape is unrecognised.
    """
    m = _RELATIVE_SPLIT_RE.match(expression or "")
    if not m:
        raise ValueError(
            f"Could not split relative expression {expression!r}; use "
            "'<N> (day|week|month|year)s? (before|after) <anchor date>'"
        )
    raw_n, unit, direction, anchor = m.groups()
    n = int(raw_n) if raw_n.isdigit() else _NUMBER_WORDS[raw_n.lower()]
    return anchor.strip(), f"{n} {unit.lower()} {direction.lower()}"


_MONTH_NAMES = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11,
    "december": 12,
}
_MONTH_ABBR = {name[:3]: num for name, num in _MONTH_NAMES.items()}

_OFFSET_RE = re.compile(
    r"^\s*(\d+)\s*(day|week|month|year)s?\s+(before|after)\s*$",
    re.IGNORECASE,
)


def _parse_anchor_date(anchor: str):
    """Parse an anchor date deterministically. Accepts ISO (2026-05-15),
    '15 May 2026', '15th of May 2026', and 'May 15, 2026' shapes."""
    from datetime import date as _date

    s = anchor.strip()
    m = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})$", s)
    if m:
        return _date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    m = re.match(
        r"^(\d{1,2})(?:st|nd|rd|th)?\s+(?:of\s+)?([A-Za-z]+)\s+(\d{4})$", s
    )
    if m:
        month = _MONTH_NAMES.get(m.group(2).lower(), _MONTH_ABBR.get(m.group(2).lower()[:3]))
        if month is None:
            raise ValueError(f"Unknown month in anchor date {anchor!r}")
        return _date(int(m.group(3)), month, int(m.group(1)))
    m = re.match(r"^([A-Za-z]+)\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})$", s)
    if m:
        month = _MONTH_NAMES.get(m.group(1).lower(), _MONTH_ABBR.get(m.group(1).lower()[:3]))
        if month is None:
            raise ValueError(f"Unknown month in anchor date {anchor!r}")
        return _date(int(m.group(3)), month, int(m.group(2)))
    raise ValueError(
        f"Could not parse anchor date {anchor!r}; use ISO or 'D Month YYYY'"
    )


def _add_months(day, months: int):
    from datetime import date as _date

    total = day.year * 12 + (day.month - 1) + months
    year, month0 = divmod(total, 12)
    last = monthrange(year, month0 + 1)[1]
    return _date(year, month0 + 1, min(day.day, last))


def _resolve_date(anchor: str, offset: str):
    """Pure date arithmetic core (unit-testable without MCP)."""
    from datetime import timedelta

    day = _parse_anchor_date(anchor)
    m = _OFFSET_RE.match(offset or "")
    if not m:
        raise ValueError(
            f"Could not parse offset {offset!r}; use '<N> (day|week|month|year)s? (before|after)'"
        )
    n, unit, direction = int(m.group(1)), m.group(2).lower(), m.group(3).lower()
    sign = -1 if direction == "before" else 1
    if unit == "day":
        return day + timedelta(days=sign * n)
    if unit == "week":
        return day + timedelta(weeks=sign * n)
    if unit == "month":
        return _add_months(day, sign * n)
    return _add_months(day, sign * n * 12)


@mcp.tool()
def read_file(path: str) -> dict:
    """Read a UTF-8 text file from the sandbox. Use for one-shot inspection of a known file. Use index_document when content must stay searchable across turns, and search_knowledge to find already-indexed content. Example: read_file("notes.txt")."""
    p = _safe(path)
    text = p.read_text(encoding="utf-8")
    return {
        "path": path,
        "size_bytes": p.stat().st_size,
        "content": text,
        "encoding": "utf-8",
    }


@mcp.tool()
def list_dir(path: str = ".") -> dict:
    """List a directory inside the sandbox. Use to discover an unknown file set before planning per-file work. Example: list_dir(".")."""
    # NOTES_RUNS §6 (1): a list[dict] return was being rendered as one MCP
    # TextContent per entry. After agent7.py's 300-char clip and decision.py's
    # downstream slicing, only the first 2-3 file dicts survived into the
    # Decision prompt, and Decision then declared the directory complete at
    # whatever it could see. Returning a single dict with `count` and a flat
    # `names` list keeps the cardinality visible even under truncation.
    p = _safe(path)
    entries = []
    names: list[str] = []
    for child in sorted(p.iterdir()):
        is_dir = child.is_dir()
        entries.append({
            "name": child.name,
            "type": "dir" if is_dir else "file",
            "size_bytes": 0 if is_dir else child.stat().st_size,
        })
        names.append(child.name)
    return {"path": path, "count": len(entries), "names": names, "entries": entries}


@mcp.tool()
def create_file(path: str, content: str) -> dict:
    """Create a new file in the sandbox; errors if it exists. Parent directories are created as needed. Use for new files; use update_file when the file already exists, edit_file for surgical changes. Always supply both path and content. Example: create_file("hello.txt", "hi")."""
    p = _safe(path)
    if p.exists():
        raise ValueError(f"File '{path}' already exists")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return {"ok": True, "path": path, "size_bytes": p.stat().st_size}


@mcp.tool()
def update_file(path: str, content: str) -> dict:
    """Overwrite an existing sandbox file. Use when create_file reports the file already exists; errors when the file is missing. Use edit_file for surgical changes, update_file for full rewrites. Example: update_file("hello.txt", "new body")."""
    p = _safe(path)
    if not p.exists():
        raise ValueError(f"File '{path}' does not exist")
    p.write_text(content, encoding="utf-8")
    return {"ok": True, "path": path, "size_bytes": p.stat().st_size}


@mcp.tool()
def edit_file(path: str, find: str, replace: str, replace_all: bool = False) -> dict:
    """Find-and-replace inside a sandbox file. Use for surgical changes; use update_file for full rewrites. Fails when the text is absent, or when it occurs multiple times without replace_all. Example: edit_file("hello.txt", "foo", "bar")."""
    p = _safe(path)
    text = p.read_text(encoding="utf-8")
    count = text.count(find)
    if count == 0:
        raise ValueError(f"'{find}' not found in '{path}'")
    if count > 1 and not replace_all:
        raise ValueError(
            f"'{find}' occurs {count} times in '{path}'; pass replace_all=True"
        )
    new_text = text.replace(find, replace) if replace_all else text.replace(find, replace, 1)
    p.write_text(new_text, encoding="utf-8")
    replacements = count if replace_all else 1
    return {
        "ok": True,
        "path": path,
        "replacements": replacements,
        "size_bytes": p.stat().st_size,
    }


# ── document indexing (Session 7) ───────────────────────────────────────────

def _read_for_index(path: str) -> tuple[str, str]:
    """Return (content, source_label) for an indexable file or artifact."""
    if path.startswith("art:"):
        return _artifacts.get_bytes(path).decode("utf-8", errors="replace"), path
    p = _safe(path)
    return p.read_text(encoding="utf-8"), f"sandbox:{path}"


def _chunk_text(text: str, size: int = 400, overlap: int = 80) -> list[str]:
    """Sliding-window chunking by word count. S7 default; semantic chunking
    arrives in Session 8."""
    words = text.split()
    if not words:
        return []
    chunks: list[str] = []
    stride = max(1, size - overlap)
    i = 0
    while i < len(words):
        chunks.append(" ".join(words[i:i + size]))
        if i + size >= len(words):
            break
        i += stride
    return chunks


@mcp.tool()
def index_document(
    path: str,
    chunk_size: int = 400,
    overlap: int = 80,
    metadata: dict | None = None,
) -> dict:
    """Chunk a sandbox file or artifact and write each chunk into Memory as a searchable `fact`.

    Use this when content must remain retrievable across later turns or runs.
    Optional metadata such as book, page range, and document ID is preserved
    with every chunk for provenance and citations. For one-shot inspection of
    a known sandbox file, prefer read_file instead. Re-indexing a source
    whose chunks are already in Memory is idempotent: already-present chunk
    texts are skipped, so a repeat write adds nothing.
    """
    text, source = _read_for_index(path)
    if not text.strip():
        return {"path": path, "source": source, "chunks_indexed": 0, "warning": "empty content"}
    chunks = _chunk_text(text, size=chunk_size, overlap=overlap)
    already = _memory.indexed_chunks(source)
    run_id = f"index-{datetime.now().strftime('%Y%m%d%H%M%S')}"
    indexed = 0
    for i, chunk in enumerate(chunks):
        if chunk.strip() in already:
            continue
        preview = chunk[:120].replace("\n", " ")
        descriptor = f"[{source} chunk {i+1}/{len(chunks)}] {preview}"
        _memory.add_fact(
            descriptor=descriptor,
            value={
                "chunk": chunk,
                "chunk_index": i,
                "total_chunks": len(chunks),
                "source": source,
                **(metadata or {}),
            },
            source=source,
            run_id=run_id,
        )
        indexed += 1
    return {
        "path": path,
        "source": source,
        "chunks_indexed": len(already) + indexed,
        "new_chunks": indexed,
        "chunk_size": chunk_size,
        "overlap": overlap,
    }


@mcp.tool()
def search_knowledge(query: str, k: int = 5) -> list[dict]:
    """Vector search over indexed `fact` chunks. Returns up to k ranked chunks with provenance. Call this rather than re-fetching URLs or re-reading source files whenever Memory already contains indexed chunks for the topic — that is the whole point of having indexed the corpus. Example: search_knowledge("authentication flow", 5)."""
    # Only document chunks are searchable knowledge. A `raw`-only fact is
    # something the classifier recorded as text, not a chunk of an indexed
    # document, so returning it here would let a stored sentence — including
    # the user's own past query — pose as a corpus result. Such an item embeds
    # its own text, so it scores maximally against itself and would otherwise
    # rank above every genuine chunk for the query that produced it.
    # Rank all indexed chunks, not just a shallow vector window: action
    # records dominate the shared FAISS index, and a k*3 window can return
    # two real chunks for a query whose action log is deep. Fusion picks the
    # final k from the full fact list.
    vec = [
        item for item in _memory.read(query, kinds=["fact"], top_k=10_000)
        if (item.value or {}).get("chunk")
    ]
    kw = [
        item for item in _memory._keyword_search(
            query, None, kinds=["fact"], top_k=k * 2, min_score=2,
        )
        if (item.value or {}).get("chunk")
    ]
    # Reciprocal-rank fusion: a chunk that is strong on either retrieval path
    # reaches the top k. Pure vector ordering puts every ReAct chunk above
    # the Chain-of-Thought chunk even when the query names the CoT paper,
    # because the scraped abstract pages dilute the title terms.
    scores: dict[str, float] = {}
    by_id = {}
    for seq in (vec, kw):
        for rank, item in enumerate(seq, start=1):
            scores[item.id] = scores.get(item.id, 0.0) + 1.0 / (60 + rank)
            by_id[item.id] = item
    items = sorted(by_id.values(), key=lambda i: -scores[i.id])[:k]
    return [
        {
            "id": item.id,
            "descriptor": item.descriptor,
            "source": item.source,
            "chunk": item.value.get("chunk") or "",
            "metadata": {k_: v for k_, v in item.value.items() if k_ != "chunk"},
        }
        for item in items
    ]


if __name__ == "__main__":
    mcp.run(transport="stdio")
