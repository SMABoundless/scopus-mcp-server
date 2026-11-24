"""Offline tests for the Scopus MCP server (HTTP is mocked with respx)."""

import httpx
import pytest
import respx

from scopus_mcp_server import server as s

BASE = "https://api.elsevier.com"


@pytest.fixture(autouse=True)
def api_key(monkeypatch):
    monkeypatch.setenv("SCOPUS_API_KEY", "test-key")
    monkeypatch.delenv("SCOPUS_INST_TOKEN", raising=False)
    s._last_results = []


def entry(**kw):
    base = {
        "dc:title": "A study",
        "prism:publicationName": "Journal of Things",
        "prism:coverDate": "2023-05-01",
        "prism:volume": "12",
        "prism:issueIdentifier": "3",
        "prism:pageRange": "100-110",
        "prism:doi": "10.1000/xyz",
        "citedby-count": "4",
        "eid": "2-s2.0-1",
        "dc:identifier": "SCOPUS_ID:1",
        "subtype": "ar",
        "author": [{"authname": "Smith J."}, {"authname": "Doe A."}],
    }
    base.update(kw)
    return base


# ── Query building ─────────────────────────────────────────────────────────

def test_keyword_search_covers_title_abstract_keywords():
    assert s._build_query("deep learning", "keyword") == "TITLE-ABS-KEY(deep learning)"
    assert s._build_query("x", "indexed_keyword") == "KEY(x)"
    assert s._build_query("x", "bogus") == "TITLE-ABS-KEY(x)"
    assert s._build_query("AU-ID(1)", "advanced") == "AU-ID(1)"


def test_date_range_uses_current_year(monkeypatch):
    monkeypatch.setattr(s, "_current_year", lambda: 2031)
    assert s._date_range("2024", None) == "2024-2032"
    assert s._date_range(None, "2010") == "1800-2010"
    assert s._date_range("2020", "2022") == "2020-2022"
    assert s._date_range(None, None) is None


# ── Exports ────────────────────────────────────────────────────────────────

def test_ris_types_pages_and_authors():
    ris = s._entries_to_ris([entry(), entry(subtype="cp", **{"prism:pageRange": "5"})])
    first, second = ris.split("\n\n")
    assert "TY  - JOUR" in first
    assert "AU  - Smith J.\nAU  - Doe A." in first
    assert "SP  - 100\nEP  - 110" in first
    assert "TY  - CPAPER" in second
    assert "T2  - Journal of Things" in second
    assert "SP  - 5" in second and "EP  -" not in second


def test_ris_falls_back_to_creator():
    e = entry(**{"dc:creator": "Solo B."})
    del e["author"]
    assert "AU  - Solo B." in s._entries_to_ris([e])


def test_bibtex_unique_keys_escaping_and_types():
    bib = s._entries_to_bibtex([
        entry(**{"dc:title": "R&D at 50% cost_{x}"}),
        entry(),
        entry(subtype="ch", author=[{"authname": "Müller K."}]),
    ])
    assert "@article{Smith2023," in bib
    assert "@article{Smith2023a," in bib
    assert "@incollection{Muller2023," in bib
    assert r"title = {R\&D at 50\% cost\_\{x\}}," in bib
    assert "pages = {100--110}," in bib
    assert "booktitle = {Journal of Things}," in bib
    assert "doi = {10.1000/xyz}," in bib


def test_error_entries_are_skipped():
    assert s._entries_to_text([{"error": "Result set was empty"}]) == "No results found."
    assert s._entries_to_ris([{"error": "x"}]) == ""


# ── HTTP behaviour ─────────────────────────────────────────────────────────

async def test_missing_key_gives_clear_message(monkeypatch):
    monkeypatch.delenv("SCOPUS_API_KEY")
    out = await s.scopus_search("x")
    assert "SCOPUS_API_KEY is not set" in out


@respx.mock
async def test_search_uses_header_complete_view_and_no_key_in_url():
    route = respx.get(f"{BASE}/content/search/scopus").mock(
        return_value=httpx.Response(200, json={"search-results": {
            "opensearch:totalResults": "1", "entry": [entry()]}})
    )
    out = await s.scopus_search("cats", count=50)
    req = route.calls.last.request
    assert req.headers["X-ELS-APIKey"] == "test-key"
    assert "apiKey" not in str(req.url) and "test-key" not in str(req.url)
    assert req.url.params["view"] == "COMPLETE"
    assert req.url.params["count"] == "25"
    assert req.url.params["query"] == "TITLE-ABS-KEY(cats)"
    assert req.url.params["sort"] == "relevancy"
    assert "Smith J.; Doe A." in out
    assert len(s._last_results) == 1


@respx.mock
async def test_search_falls_back_to_standard_view():
    def handler(request):
        if request.url.params["view"] == "COMPLETE":
            return httpx.Response(401, text="not entitled")
        return httpx.Response(200, json={"search-results": {
            "opensearch:totalResults": "1", "entry": [entry()]}})
    respx.get(f"{BASE}/content/search/scopus").mock(side_effect=handler)
    out = await s.scopus_search("cats")
    assert "not entitled to the COMPLETE view" in out


@respx.mock
async def test_abstract_requests_full_view_then_meta_abs():
    views = []

    def handler(request):
        views.append(request.url.params["view"])
        if request.url.params["view"] == "FULL":
            return httpx.Response(403)
        return httpx.Response(200, json={"abstracts-retrieval-response": {
            "coredata": {"dc:title": "T", "dc:description": "The abstract."},
            "authors": {"author": {"ce:surname": "Smith", "ce:given-name": "Jo"}},
            "authkeywords": {"author-keyword": [{"$": "cats"}, {"$": "dogs"}]},
        }})
    respx.get(f"{BASE}/content/abstract/scopus_id/123").mock(side_effect=handler)
    out = await s.scopus_abstract("SCOPUS_ID:123")
    assert views == ["FULL", "META_ABS"]
    assert "The abstract." in out
    assert "Authors: Smith, Jo" in out
    assert "Keywords: cats, dogs" in out


@respx.mock
async def test_author_includes_metrics():
    respx.get(f"{BASE}/content/search/author").mock(return_value=httpx.Response(200, json={
        "search-results": {"entry": [{
            "dc:identifier": "AUTHOR_ID:42",
            "preferred-name": {"surname": "Smith", "given-name": "Jo"},
            "document-count": "10",
        }]}}))
    respx.get(f"{BASE}/content/author/author_id/42").mock(return_value=httpx.Response(200, json={
        "author-retrieval-response": [{
            "h-index": "7",
            "coredata": {"dc:identifier": "AUTHOR_ID:42", "citation-count": "300",
                         "cited-by-count": "250"},
        }]}))
    out = await s.scopus_author("Smith")
    assert "h-index: 7" in out
    assert "Total citations: 300" in out


@respx.mock
async def test_author_metrics_failure_is_non_fatal():
    respx.get(f"{BASE}/content/search/author").mock(return_value=httpx.Response(200, json={
        "search-results": {"entry": [{"dc:identifier": "AUTHOR_ID:42",
                                      "preferred-name": {"surname": "Smith"}}]}}))
    respx.get(f"{BASE}/content/author/author_id/42").mock(return_value=httpx.Response(403))
    out = await s.scopus_author("Smith")
    assert "Author ID: 42" in out and "h-index" not in out
