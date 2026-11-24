#!/usr/bin/env python3
"""
Scopus MCP Server for Claude Desktop


Provides Claude Desktop with tools to search Scopus, retrieve abstracts,
look up author profiles, and export citations in RIS/BibTeX format.
"""

import os
import json
from typing import Optional
from mcp.server.fastmcp import FastMCP
import httpx

# ── Configuration ──────────────────────────────────────────────────────────

API_KEY = os.environ.get("SCOPUS_API_KEY", "")
BASE_URL = "https://api.elsevier.com"
HEADERS = {
    "X-ELS-APIKey": API_KEY,
    "Accept": "application/json",
}

mcp = FastMCP("Scopus")


# ── Helpers ────────────────────────────────────────────────────────────────

async def _get(path: str, params: dict = None) -> dict:
    """Make an authenticated GET request to the Scopus API."""
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(
            f"{BASE_URL}{path}",
            headers=HEADERS,
            params=params or {},
        )
        resp.raise_for_status()
        return resp.json()


def _format_authors(entry: dict) -> str:
    """Extract author names from a search result entry."""
    if "author" in entry:
        return "; ".join(a.get("authname", "") for a in entry["author"])
    return entry.get("dc:creator", "Unknown")


def _build_citation_line(entry: dict) -> str:
    """Build a short citation string: Journal (Year), Vol(Issue), Pages."""
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
        v = vol
        if issue:
            v += f"({issue})"
        parts.append(v)
    pages = entry.get("prism:pageRange", "")
    if pages:
        parts.append(pages)
    return ", ".join(parts)


def _entries_to_text(entries: list) -> str:
    """Convert a list of Scopus search entries to readable text."""
    if not entries:
        return "No results found."
    lines = []
    for i, e in enumerate(entries, 1):
        if "error" in e:
            return "No results found."
        title = e.get("dc:title", "Untitled")
        authors = _format_authors(e)
        citation = _build_citation_line(e)
        doi = e.get("prism:doi", "")
        cited = e.get("citedby-count", "0")
        doc_type = e.get("subtypeDescription", "")
        scopus_id = (e.get("dc:identifier") or "").replace("SCOPUS_ID:", "")

        line = f"{i}. {title}\n   Authors: {authors}\n   {citation}"
        if doc_type:
            line += f"\n   Type: {doc_type}"
        line += f"\n   Cited by: {cited}"
        if doi:
            line += f"\n   DOI: https://doi.org/{doi}"
        if scopus_id:
            line += f"\n   Scopus ID: {scopus_id}"
        lines.append(line)
    return "\n\n".join(lines)


def _entries_to_ris(entries: list) -> str:
    """Convert Scopus entries to RIS format for Zotero import."""
    records = []
    for e in entries:
        if "error" in e:
            continue
        lines = ["TY  - JOUR"]
        authors = e.get("author", [])
        if authors:
            for a in authors:
                lines.append(f"AU  - {a.get('authname', '')}")
        elif e.get("dc:creator"):
            lines.append(f"AU  - {e['dc:creator']}")
        if e.get("dc:title"):
            lines.append(f"TI  - {e['dc:title']}")
        if e.get("prism:publicationName"):
            lines.append(f"JO  - {e['prism:publicationName']}")
        if e.get("prism:volume"):
            lines.append(f"VL  - {e['prism:volume']}")
        if e.get("prism:issueIdentifier"):
            lines.append(f"IS  - {e['prism:issueIdentifier']}")
        if e.get("prism:pageRange"):
            lines.append(f"SP  - {e['prism:pageRange']}")
        year = (e.get("prism:coverDate") or "")[:4]
        if year:
            lines.append(f"PY  - {year}")
        if e.get("prism:doi"):
            lines.append(f"DO  - {e['prism:doi']}")
        if e.get("dc:description"):
            lines.append(f"AB  - {e['dc:description']}")
        eid = e.get("eid", "")
        if eid:
            lines.append(f"AN  - {eid}")
        lines.append("ER  - ")
        records.append("\n".join(lines))
    return "\n\n".join(records)


def _entries_to_bibtex(entries: list) -> str:
    """Convert Scopus entries to BibTeX format."""
    records = []
    for e in entries:
        if "error" in e:
            continue
        first_author = ""
        if e.get("author"):
            first_author = e["author"][0].get("authname", "unknown").split(" ")[0]
        elif e.get("dc:creator"):
            first_author = e["dc:creator"].split(" ")[0]
        else:
            first_author = "unknown"
        year = (e.get("prism:coverDate") or "")[:4] or "nd"
        key = f"{first_author}{year}"

        authors = ""
        if e.get("author"):
            authors = " and ".join(a.get("authname", "") for a in e["author"])
        elif e.get("dc:creator"):
            authors = e["dc:creator"]

        lines = [f"@article{{{key},"]
        if e.get("dc:title"):
            lines.append(f"  title = {{{e['dc:title']}}},")
        if authors:
            lines.append(f"  author = {{{authors}}},")
        if e.get("prism:publicationName"):
            lines.append(f"  journal = {{{e['prism:publicationName']}}},")
        if year != "nd":
            lines.append(f"  year = {{{year}}},")
        if e.get("prism:volume"):
            lines.append(f"  volume = {{{e['prism:volume']}}},")
        if e.get("prism:issueIdentifier"):
            lines.append(f"  number = {{{e['prism:issueIdentifier']}}},")
        if e.get("prism:pageRange"):
            lines.append(f"  pages = {{{e['prism:pageRange']}}},")
        if e.get("prism:doi"):
            lines.append(f"  doi = {{{e['prism:doi']}}},")
        lines.append("}")
        records.append("\n".join(lines))
    return "\n\n".join(records)


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
) -> str:
    """
    Search Scopus for academic articles.

    Args:
        query: Search terms or advanced Scopus query
        search_type: One of: keyword, title, author, affiliation, doi, advanced
        year_from: Optional start year (e.g. "2020")
        year_to: Optional end year (e.g. "2025")
        count: Number of results (max 50, default 25)
    """
    global _last_results

    # Build the Scopus query based on search type
    type_map = {
        "keyword": lambda q: f"KEY({q})",
        "title": lambda q: f"TITLE({q})",
        "author": lambda q: f"AUTHLASTNAME({q})",
        "affiliation": lambda q: f"AFFIL({q})",
        "doi": lambda q: f"DOI({q})",
        "advanced": lambda q: q,
    }
    builder = type_map.get(search_type, type_map["keyword"])
    scopus_query = builder(query)

    params = {
        "query": scopus_query,
        "count": min(count, 50),
        "sort": "-coverDate",
        "field": "dc:title,dc:creator,prism:publicationName,prism:coverDate,"
                 "prism:doi,prism:volume,prism:issueIdentifier,prism:pageRange,"
                 "dc:description,citedby-count,author,eid,dc:identifier,"
                 "subtypeDescription",
    }

    # Date range
    if year_from and year_to:
        params["date"] = f"{year_from}-{year_to}"
    elif year_from:
        params["date"] = f"{year_from}-2026"
    elif year_to:
        params["date"] = f"1900-{year_to}"

    try:
        data = await _get("/content/search/scopus", params)
        results = data.get("search-results", {})
        entries = results.get("entry", [])
        total = results.get("opensearch:totalResults", "0")

        _last_results = entries

        header = f"Scopus Search: {total} total results found, showing {len(entries)}\n"
        header += f"Query: {scopus_query}\n"
        header += "=" * 60 + "\n\n"

        return header + _entries_to_text(entries)
    except httpx.HTTPStatusError as e:
        return f"Scopus API error: {e.response.status_code} — {e.response.text}"
    except Exception as e:
        return f"Error: {str(e)}"


@mcp.tool()
async def scopus_abstract(scopus_id: str) -> str:
    """
    Retrieve full abstract and metadata for a Scopus record.

    Args:
        scopus_id: The Scopus ID (e.g. "85142567890")
    """
    try:
        data = await _get(f"/content/abstract/scopus_id/{scopus_id}")
        resp = data.get("abstracts-retrieval-response", {})
        coredata = resp.get("coredata", {})

        title = coredata.get("dc:title", "Unknown")
        abstract = coredata.get("dc:description", "No abstract available.")
        doi = coredata.get("prism:doi", "")
        journal = coredata.get("prism:publicationName", "")
        date = coredata.get("prism:coverDate", "")
        cited = coredata.get("citedby-count", "0")
        vol = coredata.get("prism:volume", "")
        issue = coredata.get("prism:issueIdentifier", "")
        pages = coredata.get("prism:pageRange", "")

        # Authors
        authors_data = resp.get("authors", {}).get("author", [])
        if isinstance(authors_data, dict):
            authors_data = [authors_data]
        authors = "; ".join(
            f"{a.get('ce:surname', '')}, {a.get('ce:given-name', '')}"
            for a in authors_data
        )

        # Keywords
        kw_data = resp.get("authkeywords", {})
        keywords = []
        if kw_data and "author-keyword" in kw_data:
            kws = kw_data["author-keyword"]
            if isinstance(kws, dict):
                kws = [kws]
            keywords = [k.get("$", "") for k in kws]

        output = f"Title: {title}\n"
        output += f"Authors: {authors}\n"
        output += f"Journal: {journal}"
        if vol:
            output += f", {vol}"
            if issue:
                output += f"({issue})"
        if pages:
            output += f", {pages}"
        output += f"\nDate: {date}\n"
        output += f"Cited by: {cited}\n"
        if doi:
            output += f"DOI: https://doi.org/{doi}\n"
        if keywords:
            output += f"Keywords: {', '.join(keywords)}\n"
        output += f"\nAbstract:\n{abstract}"

        return output
    except httpx.HTTPStatusError as e:
        return f"Scopus API error: {e.response.status_code} — {e.response.text}"
    except Exception as e:
        return f"Error: {str(e)}"


@mcp.tool()
async def scopus_author(
    author_name: str,
    affiliation: Optional[str] = None,
) -> str:
    """
    Look up author profiles on Scopus. Returns h-index, document count,
    citation count, and subject areas.

    Args:
        author_name: Author's last name
        affiliation: Optional affiliation to narrow results (e.g. "Northwestern")
    """
    query = f"authlast({author_name})"
    if affiliation:
        query += f" AND affil({affiliation})"

    try:
        data = await _get("/content/search/author", {
            "query": query,
            "count": 10,
        })
        entries = data.get("search-results", {}).get("entry", [])

        if not entries or (len(entries) == 1 and "error" in entries[0]):
            return f"No authors found for '{author_name}'."

        lines = []
        for i, a in enumerate(entries, 1):
            pref = a.get("preferred-name", {})
            name = f"{pref.get('surname', '')}, {pref.get('given-name', '')}"
            affil_name = (a.get("affiliation-current") or {}).get("affiliation-name", "—")
            docs = a.get("document-count", "—")
            author_id = (a.get("dc:identifier") or "").replace("AUTHOR_ID:", "")
            subjects = a.get("subject-area", [])
            if isinstance(subjects, dict):
                subjects = [subjects]
            subj_list = ", ".join(s.get("$", "") for s in subjects[:5])

            line = f"{i}. {name}\n   Affiliation: {affil_name}\n   Documents: {docs}"
            if subj_list:
                line += f"\n   Subjects: {subj_list}"
            if author_id:
                line += f"\n   Author ID: {author_id}"
                line += f"\n   Scopus Profile: https://www.scopus.com/authid/detail.uri?authorId={author_id}"
            lines.append(line)

        return "\n\n".join(lines)
    except httpx.HTTPStatusError as e:
        return f"Scopus API error: {e.response.status_code} — {e.response.text}"
    except Exception as e:
        return f"Error: {str(e)}"


@mcp.tool()
async def scopus_author_papers(
    author_id: str,
    count: int = 25,
    year_from: Optional[str] = None,
) -> str:
    """
    List an author's publications from Scopus.

    Args:
        author_id: Scopus Author ID (from scopus_author results)
        count: Number of results (max 50, default 25)
        year_from: Optional start year filter
    """
    global _last_results

    query = f"AU-ID({author_id})"
    params = {
        "query": query,
        "count": min(count, 50),
        "sort": "-coverDate",
        "field": "dc:title,dc:creator,prism:publicationName,prism:coverDate,"
                 "prism:doi,prism:volume,prism:issueIdentifier,prism:pageRange,"
                 "dc:description,citedby-count,author,eid,dc:identifier,"
                 "subtypeDescription",
    }
    if year_from:
        params["date"] = f"{year_from}-2026"

    try:
        data = await _get("/content/search/scopus", params)
        results = data.get("search-results", {})
        entries = results.get("entry", [])
        total = results.get("opensearch:totalResults", "0")

        _last_results = entries

        header = f"Author ID {author_id}: {total} total publications, showing {len(entries)}\n"
        header += "=" * 60 + "\n\n"

        return header + _entries_to_text(entries)
    except httpx.HTTPStatusError as e:
        return f"Scopus API error: {e.response.status_code} — {e.response.text}"
    except Exception as e:
        return f"Error: {str(e)}"


@mcp.tool()
async def scopus_export_ris() -> str:
    """
    Export the most recent search results as RIS format.
    Import the output into Zotero: File → Import → paste or save as .ris file.
    """
    if not _last_results:
        return "No results to export. Run a search first."
    ris = _entries_to_ris(_last_results)
    count = len([e for e in _last_results if "error" not in e])
    return f"RIS Export ({count} records) — Save as .ris and import into Zotero:\n\n{ris}"


@mcp.tool()
async def scopus_export_bibtex() -> str:
    """
    Export the most recent search results as BibTeX format.
    """
    if not _last_results:
        return "No results to export. Run a search first."
    bib = _entries_to_bibtex(_last_results)
    count = len([e for e in _last_results if "error" not in e])
    return f"BibTeX Export ({count} records):\n\n{bib}"


# ── Run ────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    mcp.run()
