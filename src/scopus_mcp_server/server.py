#!/usr/bin/env python3
"""
Scopus MCP Server

Provides tools to search Scopus, retrieve abstracts, look up author
profiles, and export citations in RIS/BibTeX format.
"""

import asyncio
import os
import re
import unicodedata
from datetime import date, datetime, timezone
from urllib.parse import quote
from typing import Optional

import httpx

try:  # MCP Python SDK 2.x renamed FastMCP to MCPServer
    from mcp.server.mcpserver import MCPServer
except ImportError:  # MCP Python SDK 1.x
    from mcp.server.fastmcp import FastMCP as MCPServer

# ── Configuration ──────────────────────────────────────────────────────────

BASE_URL = "https://api.elsevier.com"

# Scopus Search API: the COMPLETE view (full author list, abstract) needs
# institutional entitlement and allows at most 25 results per request.
MAX_RESULTS = 25

# Scopus Search API only returns the first 5,000 results without cursor paging.
MAX_OFFSET = 5000

THROTTLE_RETRIES = 2
THROTTLE_BACKOFF = 1.0  # seconds; doubles each retry

# Last-seen rate-limit headers, keyed by API ("search/scopus", "abstract", ...).
_quota: dict = {}

SEARCH_FIELDS = (
    "dc:title,dc:creator,prism:publicationName,prism:coverDate,"
    "prism:doi,prism:volume,prism:issueIdentifier,prism:pageRange,"
    "dc:description,citedby-count,author,eid,dc:identifier,"
    "subtype,subtypeDescription,prism:isbn,prism:issn"
)

SORT_OPTIONS = {
    "relevance": "relevancy",
    "date": "-coverDate",
    "citations": "-citedby-count",
}

mcp = MCPServer("Scopus")


class MissingAPIKeyError(RuntimeError):
    pass


# ── Helpers ────────────────────────────────────────────────────────────────

def _api_key() -> str:
    return os.environ.get("SCOPUS_API_KEY", "").strip()


def _current_year() -> int:
    return date.today().year


def _date_range(year_from: Optional[str], year_to: Optional[str]) -> Optional[str]:
    """Build a Scopus `date` parameter, or None if no years were given."""
    if not year_from and not year_to:
        return None
    start = year_from or "1800"
    end = year_to or str(_current_year() + 1)  # +1 covers in-press items
    return f"{start}-{end}"


async def _get(path: str, params: Optional[dict] = None) -> dict:
    """Make an authenticated GET request to the Elsevier API.

    The API key is sent only in the X-ELS-APIKey header, never in the URL.
    """
    key = _api_key()
    if not key:
        raise MissingAPIKeyError(
            "SCOPUS_API_KEY is not set. Get a key at https://dev.elsevier.com/ "
            "and add it to this server's environment."
        )
    headers = {"X-ELS-APIKey": key, "Accept": "application/json"}
    inst_token = os.environ.get("SCOPUS_INST_TOKEN", "").strip()
    if inst_token:
        headers["X-ELS-Insttoken"] = inst_token
    async with httpx.AsyncClient(timeout=30) as client:
        for attempt in range(THROTTLE_RETRIES + 1):
            resp = await client.get(f"{BASE_URL}{path}", headers=headers, params=params or {})
            _record_quota(resp, path)
            if resp.status_code == 429 and not _is_quota_exhausted(resp) and attempt < THROTTLE_RETRIES:
                # Per-second throttling: back off briefly and retry.
                await asyncio.sleep(THROTTLE_BACKOFF * (2 ** attempt))
                continue
            resp.raise_for_status()
            return resp.json()
    raise RuntimeError("unreachable")


def _record_quota(resp: httpx.Response, path: str) -> None:
    """Remember the rate-limit headers Elsevier sends with each response."""
    limit = resp.headers.get("X-RateLimit-Limit")
    remaining = resp.headers.get("X-RateLimit-Remaining")
    reset = resp.headers.get("X-RateLimit-Reset")
    if limit is None and remaining is None:
        return
    parts = path.strip("/").split("/")  # e.g. content/search/scopus
    api = "/".join(parts[1:3]) if len(parts) > 2 and parts[1] == "search" else parts[min(1, len(parts) - 1)]
    _quota[api] = {"limit": limit, "remaining": remaining, "reset": reset}


def _is_quota_exhausted(resp: httpx.Response) -> bool:
    status = resp.headers.get("X-ELS-Status", "")
    return "QUOTA_EXCEEDED" in status.upper() or resp.headers.get("X-RateLimit-Remaining") == "0"


def _format_reset(reset: Optional[str]) -> str:
    try:
        when = datetime.fromtimestamp(int(reset), tz=timezone.utc)
    except (TypeError, ValueError):
        return "unknown"
    return when.strftime("%Y-%m-%d %H:%M UTC")


def _is_entitlement_error(e: httpx.HTTPStatusError) -> bool:
    return e.response.status_code in (401, 403)


def _error_message(e: Exception) -> str:
    if isinstance(e, MissingAPIKeyError):
        return f"Error: {e}"
    if isinstance(e, httpx.HTTPStatusError) and e.response.status_code == 429:
        if _is_quota_exhausted(e.response):
            reset = _format_reset(e.response.headers.get("X-RateLimit-Reset"))
            return (
                "Scopus API quota exhausted for this API key. "
                f"It resets at {reset}. Elsevier quotas are weekly per API."
            )
        return "Scopus API is rate-limiting requests (too many per second). Try again shortly."
    if isinstance(e, httpx.HTTPStatusError):
        body = e.response.text
        if len(body) > 500:
            body = body[:500] + "…"
        return f"Scopus API error: {e.response.status_code} — {body}"
    return f"Error: {e}"


def _as_list(value) -> list:
    if value is None:
        return []
    if isinstance(value, dict):
        return [value]
    return list(value)


def _valid_entries(entries: list) -> list:
    """Drop the placeholder entry Scopus returns for empty result sets."""
    return [e for e in entries if "error" not in e]


def _author_names(entry: dict) -> list:
    names = [a.get("authname", "") for a in _as_list(entry.get("author"))]
    names = [n for n in names if n]
    if names:
        return names
    creator = entry.get("dc:creator")
    return [creator] if creator else []


def _format_authors(entry: dict) -> str:
    names = _author_names(entry)
    return "; ".join(names) if names else "Unknown"


def _split_pages(page_range: str) -> tuple:
    """Split '123-130' (or an en dash) into ('123', '130')."""
    if not page_range:
        return "", ""
    parts = re.split(r"\s*[-–—]\s*", page_range.strip(), maxsplit=1)
    start = parts[0]
    end = parts[1] if len(parts) > 1 else ""
    return start, end


def _build_citation_line(entry: dict) -> str:
    """Build a short citation string: Journal, (Year), Vol(Issue), Pages."""
    parts = []
    journal = entry.get("prism:publicationName", "")
    if journal:
        parts.append(journal)
    year = (entry.get("prism:coverDate") or "")[:4]
    if year:
        parts.append(f"({year})")
    vol = entry.get("prism:volume", "")
    issue = entry.get("prism:issueIdentifier", "")
    if vol:
        parts.append(f"{vol}({issue})" if issue else vol)
    pages = entry.get("prism:pageRange", "")
    if pages:
        parts.append(pages)
    return ", ".join(parts)


def _entries_to_text(entries: list, offset: int = 0) -> str:
    entries = _valid_entries(entries)
    if not entries:
        return "No results found."
    lines = []
    for i, e in enumerate(entries, offset + 1):
        title = e.get("dc:title", "Untitled")
        citation = _build_citation_line(e)
        doi = e.get("prism:doi", "")
        cited = e.get("citedby-count", "0")
        doc_type = e.get("subtypeDescription", "")
        scopus_id = (e.get("dc:identifier") or "").replace("SCOPUS_ID:", "")

        line = f"{i}. {title}\n   Authors: {_format_authors(e)}\n   {citation}"
        if doc_type:
            line += f"\n   Type: {doc_type}"
        line += f"\n   Cited by: {cited}"
        if doi:
            line += f"\n   DOI: https://doi.org/{doi}"
        if scopus_id:
            line += f"\n   Scopus ID: {scopus_id}"
        lines.append(line)
    return "\n\n".join(lines)


# Scopus subtype code → (RIS type, BibTeX type)
_TYPE_MAP = {
    "ar": ("JOUR", "article"),
    "re": ("JOUR", "article"),
    "le": ("JOUR", "article"),
    "ed": ("JOUR", "article"),
    "no": ("JOUR", "article"),
    "sh": ("JOUR", "article"),
    "er": ("JOUR", "article"),
    "dp": ("JOUR", "article"),
    "cp": ("CPAPER", "inproceedings"),
    "cr": ("CPAPER", "inproceedings"),
    "ch": ("CHAP", "incollection"),
    "bk": ("BOOK", "book"),
}


def _doc_types(entry: dict) -> tuple:
    return _TYPE_MAP.get(entry.get("subtype", ""), ("JOUR", "article"))


def _entries_to_ris(entries: list, include_abstracts: bool = False) -> str:
    """Convert Scopus entries to RIS format (Zotero, EndNote, Mendeley).

    Abstracts are left out unless asked for: they are Elsevier content, and
    RIS files are usually saved to a reference manager.
    """
    records = []
    for e in _valid_entries(entries):
        ris_type, _ = _doc_types(e)
        lines = [f"TY  - {ris_type}"]
        for name in _author_names(e):
            lines.append(f"AU  - {name}")
        if e.get("dc:title"):
            lines.append(f"TI  - {e['dc:title']}")
        if e.get("prism:publicationName"):
            tag = "JO" if ris_type == "JOUR" else "T2"
            lines.append(f"{tag}  - {e['prism:publicationName']}")
        if e.get("prism:volume"):
            lines.append(f"VL  - {e['prism:volume']}")
        if e.get("prism:issueIdentifier"):
            lines.append(f"IS  - {e['prism:issueIdentifier']}")
        sp, ep = _split_pages(e.get("prism:pageRange", ""))
        if sp:
            lines.append(f"SP  - {sp}")
        if ep:
            lines.append(f"EP  - {ep}")
        year = (e.get("prism:coverDate") or "")[:4]
        if year:
            lines.append(f"PY  - {year}")
        if e.get("prism:doi"):
            lines.append(f"DO  - {e['prism:doi']}")
        isbn = e.get("prism:isbn")
        if isinstance(isbn, list):
            isbn = isbn[0].get("$", "") if isbn and isinstance(isbn[0], dict) else ""
        issn = e.get("prism:issn")
        if isbn or issn:
            lines.append(f"SN  - {isbn or issn}")
        if include_abstracts and e.get("dc:description"):
            lines.append(f"AB  - {e['dc:description']}")
        if e.get("eid"):
            lines.append(f"AN  - {e['eid']}")
        lines.append("ER  - ")
        records.append("\n".join(lines))
    return "\n\n".join(records)


_BIBTEX_ESCAPES = {
    "\\": r"\textbackslash{}",
    "{": r"\{",
    "}": r"\}",
    "&": r"\&",
    "%": r"\%",
    "$": r"\$",
    "#": r"\#",
    "_": r"\_",
    "~": r"\textasciitilde{}",
    "^": r"\textasciicircum{}",
}


def _bibtex_escape(text: str) -> str:
    return "".join(_BIBTEX_ESCAPES.get(c, c) for c in text)


def _ascii_slug(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^A-Za-z0-9]", "", normalized)


def _bibtex_key(entry: dict, used: set) -> str:
    names = _author_names(entry)
    surname = names[0].split(",")[0].split(" ")[0] if names else ""
    base = (_ascii_slug(surname) or "unknown") + ((entry.get("prism:coverDate") or "")[:4] or "nd")
    key = base
    suffix = ord("a")
    while key in used:
        key = f"{base}{chr(suffix)}"
        suffix += 1
    used.add(key)
    return key


def _entries_to_bibtex(entries: list) -> str:
    records = []
    used_keys: set = set()
    for e in _valid_entries(entries):
        _, bib_type = _doc_types(e)
        key = _bibtex_key(e, used_keys)
        year = (e.get("prism:coverDate") or "")[:4]
        authors = " and ".join(_author_names(e))

        fields = []
        if e.get("dc:title"):
            fields.append(("title", e["dc:title"]))
        if authors:
            fields.append(("author", authors))
        venue = e.get("prism:publicationName")
        if venue and bib_type != "book":
            fields.append(("journal" if bib_type == "article" else "booktitle", venue))
        if year:
            fields.append(("year", year))
        if e.get("prism:volume"):
            fields.append(("volume", e["prism:volume"]))
        if e.get("prism:issueIdentifier"):
            fields.append(("number", e["prism:issueIdentifier"]))
        sp, ep = _split_pages(e.get("prism:pageRange", ""))
        if sp:
            fields.append(("pages", f"{sp}--{ep}" if ep else sp))
        if e.get("prism:doi"):
            fields.append(("doi", e["prism:doi"]))

        lines = [f"@{bib_type}{{{key},"]
        for name, value in fields:
            value = value if name == "doi" else _bibtex_escape(str(value))
            lines.append(f"  {name} = {{{value}}},")
        lines.append("}")
        records.append("\n".join(lines))
    return "\n\n".join(records)


def _build_query(query: str, search_type: str) -> str:
    type_map = {
        "keyword": "TITLE-ABS-KEY({})",
        "title": "TITLE({})",
        "author": "AUTHLASTNAME({})",
        "affiliation": "AFFIL({})",
        "doi": "DOI({})",
        "indexed_keyword": "KEY({})",
        "advanced": "{}",
    }
    template = type_map.get(search_type, type_map["keyword"])
    return template.format(query)


async def _search(params: dict, count: int, start: int = 0) -> tuple:
    """Run a Scopus search page, preferring the COMPLETE view.

    Falls back to the STANDARD view (first author only, no abstract) if the
    key isn't entitled to COMPLETE. Returns (search-results dict, view used).
    """
    count = max(1, min(count, MAX_RESULTS))
    start = max(0, min(start, MAX_OFFSET - count))
    params = {**params, "start": start, "count": count}
    try:
        data = await _get(
            "/content/search/scopus",
            {**params, "view": "COMPLETE"},
        )
        return data.get("search-results", {}), "COMPLETE"
    except httpx.HTTPStatusError as e:
        if not _is_entitlement_error(e):
            raise
    data = await _get(
        "/content/search/scopus",
        {**params, "view": "STANDARD"},
    )
    return data.get("search-results", {}), "STANDARD"


def _results_header(label: str, results: dict, entries: list) -> str:
    total = int(results.get("opensearch:totalResults", "0") or 0)
    start = int(results.get("opensearch:startIndex", "0") or 0)
    if not entries:
        return f"{label}: {total:,} total results, none on this page\n"
    first, last = start + 1, start + len(entries)
    header = f"{label}: {total:,} total results, showing {first}–{last}\n"
    if last < min(total, MAX_OFFSET):
        header += f"More results available: call again with start={last}.\n"
    elif total > MAX_OFFSET:
        header += (
            f"Scopus only pages through the first {MAX_OFFSET:,} results; "
            "narrow the query to see the rest.\n"
        )
    return header


def _page_offset(results: dict) -> int:
    return int(results.get("opensearch:startIndex", "0") or 0)


_STANDARD_NOTE = (
    "Note: your API key is not entitled to the COMPLETE view, so results list "
    "only the first author and omit abstracts.\n"
)


# ── Store last search results for export ───────────────────────────────────

_last_results: list = []


# ── Tools ──────────────────────────────────────────────────────────────────

@mcp.tool()
async def scopus_search(
    query: str,
    search_type: str = "keyword",
    year_from: Optional[str] = None,
    year_to: Optional[str] = None,
    count: int = 25,
    sort: str = "relevance",
    start: int = 0,
) -> str:
    """
    Search Scopus for academic articles.

    Args:
        query: Search terms or advanced Scopus query
        search_type: One of: keyword (title, abstract and keywords), title,
            author (last name), affiliation, doi, indexed_keyword (author and
            indexed keywords only), advanced (raw Scopus query syntax)
        year_from: Optional start year (e.g. "2020")
        year_to: Optional end year (e.g. "2025")
        count: Number of results (max 25, default 25)
        sort: One of: relevance (default), date (newest first), citations
        start: Offset of the first result, for paging (e.g. 25 for the second page)
    """
    global _last_results

    scopus_query = _build_query(query, search_type)
    params = {
        "query": scopus_query,
        "sort": SORT_OPTIONS.get(sort, SORT_OPTIONS["relevance"]),
        "field": SEARCH_FIELDS,
    }
    date_range = _date_range(year_from, year_to)
    if date_range:
        params["date"] = date_range

    try:
        results, view = await _search(params, count, start)
        entries = _valid_entries(results.get("entry", []))
        _last_results = entries

        header = _results_header("Scopus Search", results, entries)
        header += f"Query: {scopus_query}\n"
        if view == "STANDARD":
            header += _STANDARD_NOTE
        header += "=" * 60 + "\n\n"
        return header + _entries_to_text(entries, _page_offset(results))
    except Exception as e:
        return _error_message(e)


def _abstract_path(identifier: str) -> str:
    """Map a Scopus ID, EID, DOI or PubMed ID to an Abstract Retrieval path."""
    ident = identifier.strip()
    lower = ident.lower()
    for prefix in ("https://doi.org/", "http://doi.org/", "https://dx.doi.org/", "doi.org/", "doi:"):
        if lower.startswith(prefix):
            return f"/content/abstract/doi/{quote(ident[len(prefix):].strip(), safe='/')}"
    if ident.startswith("10.") and "/" in ident:
        return f"/content/abstract/doi/{quote(ident, safe='/')}"
    if lower.startswith("pmid:"):
        return f"/content/abstract/pubmed_id/{quote(ident[5:].strip(), safe='')}"
    if lower.startswith("2-s2.0-"):
        return f"/content/abstract/eid/{quote(ident, safe='')}"
    if lower.startswith("scopus_id:"):
        ident = ident[len("scopus_id:"):]
    return f"/content/abstract/scopus_id/{quote(ident.strip(), safe='')}"


@mcp.tool()
async def scopus_abstract(identifier: str) -> str:
    """
    Retrieve full abstract and metadata for a single record.

    Args:
        identifier: A Scopus ID (e.g. "85142567890"), DOI (e.g.
            "10.1016/j.cell.2023.01.001", with or without https://doi.org/),
            EID (e.g. "2-s2.0-85142567890"), or PubMed ID prefixed with
            "pmid:" (e.g. "pmid:36754049")
    """
    path = _abstract_path(identifier)
    try:
        try:
            data = await _get(path, {"view": "FULL"})
        except httpx.HTTPStatusError as e:
            if not _is_entitlement_error(e):
                raise
            data = await _get(path, {"view": "META_ABS"})

        resp = data.get("abstracts-retrieval-response", {})
        coredata = resp.get("coredata", {})

        title = coredata.get("dc:title", "Unknown")
        abstract = coredata.get("dc:description") or "No abstract available."
        doi = coredata.get("prism:doi", "")
        journal = coredata.get("prism:publicationName", "")
        cover_date = coredata.get("prism:coverDate", "")
        cited = coredata.get("citedby-count", "0")
        vol = coredata.get("prism:volume", "")
        issue = coredata.get("prism:issueIdentifier", "")
        pages = coredata.get("prism:pageRange", "")

        authors_data = _as_list((resp.get("authors") or {}).get("author"))
        authors = "; ".join(
            f"{a.get('ce:surname', '')}, {a.get('ce:given-name', '') or a.get('ce:initials', '')}".strip(", ")
            for a in authors_data
        )

        keywords = [
            k.get("$", "")
            for k in _as_list((resp.get("authkeywords") or {}).get("author-keyword"))
        ]

        output = f"Title: {title}\n"
        if authors:
            output += f"Authors: {authors}\n"
        output += f"Journal: {journal}"
        if vol:
            output += f", {vol}"
            if issue:
                output += f"({issue})"
        if pages:
            output += f", {pages}"
        output += f"\nDate: {cover_date}\n"
        output += f"Cited by: {cited}\n"
        if doi:
            output += f"DOI: https://doi.org/{doi}\n"
        if keywords:
            output += f"Keywords: {', '.join(keywords)}\n"
        output += f"\nAbstract:\n{abstract}"
        return output
    except Exception as e:
        return _error_message(e)


async def _author_metrics(author_ids: list) -> dict:
    """Fetch h-index and citation counts for up to 25 author IDs.

    Best effort: returns {} if the Author Retrieval API isn't available
    to this key.
    """
    if not author_ids:
        return {}
    try:
        data = await _get(
            f"/content/author/author_id/{','.join(author_ids[:25])}",
            {"view": "METRICS"},
        )
    except Exception:
        return {}
    metrics = {}
    for r in _as_list(data.get("author-retrieval-response")):
        core = r.get("coredata", {})
        aid = (core.get("dc:identifier") or "").replace("AUTHOR_ID:", "")
        if aid:
            metrics[aid] = {
                "h_index": r.get("h-index"),
                "cited_by": core.get("cited-by-count"),
                "citations": core.get("citation-count"),
            }
    return metrics


@mcp.tool()
async def scopus_author(
    author_name: str,
    affiliation: Optional[str] = None,
) -> str:
    """
    Look up author profiles on Scopus. Returns affiliation, document count,
    subject areas, and (when the API key allows it) h-index and citation counts.

    Args:
        author_name: Author's last name
        affiliation: Optional affiliation to narrow results (e.g. "Northwestern")
    """
    query = f"AUTHLAST({author_name})"
    if affiliation:
        query += f" AND AFFIL({affiliation})"

    try:
        data = await _get("/content/search/author", {"query": query, "count": 10})
        entries = _valid_entries(data.get("search-results", {}).get("entry", []))
        if not entries:
            return f"No authors found for '{author_name}'."

        ids = [
            (a.get("dc:identifier") or "").replace("AUTHOR_ID:", "")
            for a in entries
        ]
        metrics = await _author_metrics([i for i in ids if i])

        lines = []
        for i, (a, author_id) in enumerate(zip(entries, ids), 1):
            pref = a.get("preferred-name", {})
            name = f"{pref.get('surname', '')}, {pref.get('given-name', '')}"
            affil_name = (a.get("affiliation-current") or {}).get("affiliation-name", "—")
            docs = a.get("document-count", "—")
            subjects = _as_list(a.get("subject-area"))
            subj_list = ", ".join(s.get("$", "") for s in subjects[:5])

            line = f"{i}. {name}\n   Affiliation: {affil_name}\n   Documents: {docs}"
            m = metrics.get(author_id)
            if m:
                if m.get("h_index") is not None:
                    line += f"\n   h-index: {m['h_index']}"
                if m.get("citations") is not None:
                    line += f"\n   Total citations: {m['citations']}"
                if m.get("cited_by") is not None:
                    line += f"\n   Cited by (documents): {m['cited_by']}"
            if subj_list:
                line += f"\n   Subjects: {subj_list}"
            if author_id:
                line += f"\n   Author ID: {author_id}"
                line += f"\n   Scopus Profile: https://www.scopus.com/authid/detail.uri?authorId={author_id}"
            lines.append(line)

        return "\n\n".join(lines)
    except Exception as e:
        return _error_message(e)


@mcp.tool()
async def scopus_author_papers(
    author_id: str,
    count: int = 25,
    year_from: Optional[str] = None,
    start: int = 0,
) -> str:
    """
    List an author's publications from Scopus, newest first.

    Args:
        author_id: Scopus Author ID (from scopus_author results)
        count: Number of results (max 25, default 25)
        year_from: Optional start year filter
        start: Offset of the first result, for paging (e.g. 25 for the second page)
    """
    global _last_results

    params = {
        "query": f"AU-ID({author_id.strip()})",
        "sort": SORT_OPTIONS["date"],
        "field": SEARCH_FIELDS,
    }
    date_range = _date_range(year_from, None)
    if date_range:
        params["date"] = date_range

    try:
        results, view = await _search(params, count, start)
        entries = _valid_entries(results.get("entry", []))
        _last_results = entries

        header = _results_header(f"Author ID {author_id}", results, entries)
        if view == "STANDARD":
            header += _STANDARD_NOTE
        header += "=" * 60 + "\n\n"
        return header + _entries_to_text(entries, _page_offset(results))
    except Exception as e:
        return _error_message(e)


@mcp.tool()
async def scopus_quota() -> str:
    """
    Show how much of this API key's Elsevier quota is left, per API.
    Elsevier quotas are weekly. Figures come from the most recent call to
    each API, so run a search or lookup first.
    """
    if not _quota:
        return "No quota information yet. Run a search or lookup first."
    names = {
        "search/scopus": "Scopus Search",
        "search/author": "Author Search",
        "abstract": "Abstract Retrieval",
        "author": "Author Retrieval",
    }
    lines = []
    for api, q in sorted(_quota.items()):
        line = f"{names.get(api, api)}: {q.get('remaining') or '?'} of {q.get('limit') or '?'} requests left"
        if q.get("reset"):
            line += f", resets {_format_reset(q['reset'])}"
        lines.append(line)
    return "\n".join(lines)


@mcp.tool()
async def scopus_export_ris(include_abstracts: bool = False) -> str:
    """
    Export the most recent search results as RIS format.
    Import the output into Zotero: File → Import → paste or save as .ris file.

    Args:
        include_abstracts: Include abstracts (default false). Abstracts are
            Elsevier content; Elsevier's API terms restrict storing it
            locally, so only include them when the user explicitly asks.
    """
    if not _last_results:
        return "No results to export. Run a search first."
    ris = _entries_to_ris(_last_results, include_abstracts)
    count = len(_valid_entries(_last_results))
    return f"RIS Export ({count} records) — Save as .ris and import into Zotero:\n\n{ris}"


@mcp.tool()
async def scopus_export_bibtex() -> str:
    """
    Export the most recent search results as BibTeX format.
    """
    if not _last_results:
        return "No results to export. Run a search first."
    bib = _entries_to_bibtex(_last_results)
    count = len(_valid_entries(_last_results))
    return f"BibTeX Export ({count} records):\n\n{bib}"


# ── Run ────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    mcp.run()
