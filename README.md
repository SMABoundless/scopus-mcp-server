# Scopus MCP Server

An MCP (Model Context Protocol) server for searching [Scopus](https://www.scopus.com/), Elsevier's abstract and citation database covering 90M+ records across science, technology, medicine, social sciences, and arts & humanities.

Built with [FastMCP](https://github.com/modelcontextprotocol/python-sdk).

## Tools

| Tool | Description |
|------|-------------|
| `scopus_search` | Search by topic (title, abstract and keywords), title, author, affiliation, DOI, or advanced query; sort by relevance, date, or citations; page through results |
| `scopus_abstract` | Retrieve full abstract, authors, keywords, and metadata for one record, by Scopus ID, DOI, EID, or PubMed ID |
| `scopus_author` | Look up author profiles (affiliation, document count, subject areas, and h-index and citation counts when your key allows it) |
| `scopus_author_papers` | List an author's publications, with paging |
| `scopus_quota` | Show how much of your weekly Elsevier API quota is left |
| `scopus_export_ris` | Export the last results as RIS (for Zotero, EndNote, etc.); abstracts only if you ask |
| `scopus_export_bibtex` | Export the last results as BibTeX |

Searches return up to 25 results per call; use `start` to page further (Scopus allows paging through the first 5,000 results). Brief rate limiting from Elsevier is retried automatically, and an exhausted weekly quota is reported with its reset time.

## Setup

### 1. Get a Scopus API key

Get a key from the [Elsevier Developer Portal](https://dev.elsevier.com/). Use it on your institution's network (or VPN), or also set an institutional token (`SCOPUS_INST_TOKEN`), to get full author lists, abstracts, and author metrics. Without institutional entitlement the server still works, but search results show only the first author and no abstract.

### 2. Add to Claude

The server is published on PyPI and runs with [`uv`](https://docs.astral.sh/uv/), so there's nothing to install by hand.

Claude Code:

```bash
claude mcp add scopus -e SCOPUS_API_KEY=your-api-key -- uvx scopus-mcp-server
```

Claude Desktop (`claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "scopus": {
      "command": "uvx",
      "args": ["scopus-mcp-server"],
      "env": {
        "SCOPUS_API_KEY": "your-api-key"
      }
    }
  }
}
```

### Environment variables

| Variable | Required | Description |
|----------|----------|-------------|
| `SCOPUS_API_KEY` | Yes | Your Elsevier API key |
| `SCOPUS_INST_TOKEN` | No | Elsevier institutional token, for full access off your institution's network |

## Elsevier API terms

Elsevier's API Service Agreement allows using the API with an AI system only under conditions (section 2.4), and makes you responsible for any AI system you connect to it (section 2.5). In practice:

- **Use your own key, for your own research.** Don't share your key, and don't run this server as a shared service for colleagues.
- **Use an enterprise AI service that doesn't train on your data,** such as Claude through your institution's organization account.
- **Don't collect Scopus content.** Paging and exports are for finding and citing papers, not for building up a local copy of Scopus records. RIS exports leave out abstracts unless you ask for them.
- **Don't build a product on it** that competes with or replaces Scopus, or use it commercially.

Your institution's Scopus license may add further conditions; your library's e-resources or licensing team can advise. This summary is not legal advice; the agreement itself (linked from the [Elsevier Developer Portal](https://dev.elsevier.com/)) governs.

## Development

```bash
pip install -e ".[dev]"
pytest
```

The tests mock the Elsevier API, so they don't need a key or network access.

## Usage examples

- "Search Scopus for recent articles on deep learning in medical imaging"
- "Look up the author profile for Smith at Northwestern University"
- "Get the abstract for Scopus ID 85142567890"
- "Get the abstract for doi 10.1016/j.cell.2023.01.001"
- "Show me the next page of results"
- "How much of my Scopus quota is left?"
- "Find this author's papers and export them as BibTeX"

## License

MIT

<!-- mcp-name: io.github.SMABoundless/scopus -->
