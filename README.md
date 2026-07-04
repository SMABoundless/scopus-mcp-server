# Scopus MCP Server

An MCP (Model Context Protocol) server for searching [Scopus](https://www.scopus.com/), Elsevier's abstract and citation database covering 90M+ records across science, technology, medicine, social sciences, and arts & humanities.

Built with [FastMCP](https://github.com/modelcontextprotocol/python-sdk).

## Tools

| Tool | Description |
|------|-------------|
| `scopus_search` | Search by keyword, title, author, affiliation, DOI, or advanced query |
| `scopus_abstract` | Retrieve full abstract and metadata for a specific record |
| `scopus_author` | Look up author profiles (h-index, document count, subject areas) |
| `scopus_author_papers` | List an author's publications |
| `scopus_export_ris` | Export results as RIS (for Zotero, EndNote, etc.) |
| `scopus_export_bibtex` | Export results as BibTeX |

## Setup

### 1. Get a Scopus API key

You need a Scopus API key from the [Elsevier Developer Portal](https://dev.elsevier.com/). Institutional access (e.g., through a university) is typically required for full functionality.

### 2. Install

```bash
cd scopus-mcp-server
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 3. Add to Claude Desktop

Add this to your `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "scopus": {
      "command": "/path/to/scopus-mcp-server/venv/bin/python",
      "args": ["/path/to/scopus-mcp-server/server.py"],
      "env": {
        "SCOPUS_API_KEY": "your-api-key"
      }
    }
  }
}
```

Or if using Claude Code CLI:

```bash
claude mcp add scopus \
  /path/to/scopus-mcp-server/venv/bin/python \
  /path/to/scopus-mcp-server/server.py \
  -e SCOPUS_API_KEY=your-api-key
```

## Usage examples

- "Search Scopus for recent articles on deep learning in medical imaging"
- "Look up the author profile for Smith at Northwestern University"
- "Get the abstract for Scopus ID 85142567890"
- "Find this author's papers and export them as BibTeX"

## License

MIT
