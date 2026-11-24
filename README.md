# Scopus MCP Server

An MCP (Model Context Protocol) server for searching [Scopus](https://www.scopus.com/), Elsevier's abstract and citation database covering 90M+ records across science, technology, medicine, social sciences, and arts & humanities.

Built with [FastMCP](https://github.com/modelcontextprotocol/python-sdk).

## Tools

| Tool | Description |
|------|-------------|
| `scopus_search` | Search by topic (title, abstract and keywords), title, author, affiliation, DOI, or advanced query; sort by relevance, date, or citations |
| `scopus_abstract` | Retrieve full abstract, authors, keywords, and metadata for a specific record |
| `scopus_author` | Look up author profiles (affiliation, document count, subject areas, and h-index and citation counts when your key allows it) |
| `scopus_author_papers` | List an author's publications |
| `scopus_export_ris` | Export the last results as RIS (for Zotero, EndNote, etc.) |
| `scopus_export_bibtex` | Export the last results as BibTeX |

Searches return up to 25 results per call.

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
- "Find this author's papers and export them as BibTeX"

## License

MIT

<!-- mcp-name: io.github.SMABoundless/scopus -->
