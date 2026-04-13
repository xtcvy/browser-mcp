# browser_mcp

MCP server that fetches web pages through a real local Chromium browser via [Playwright](https://playwright.dev/python/), bypassing Cloudflare bot detection, robots.txt blocks, and datacenter IP filtering.

## Why

Claude's built-in `web_fetch` runs from Anthropic datacenter IPs and respects `robots.txt`. Sites like Amazon, eBay, and Reddit actively block these requests. This server routes fetches through your local browser so the request looks like a normal user visit.

## Tools

| Tool | Description |
|------|-------------|
| `browser_fetch_page` | Fetch a page's text content via headless Chromium |
| `browser_screenshot` | Capture a full or viewport screenshot of any URL |

## Setup

```bash
# Clone
git clone https://github.com/xtcvy/browser-mcp.git
cd browser-mcp

# Create venv (Python 3.11+ recommended, 3.14 has pydantic issues)
python -m venv .venv

# Activate
# Windows
.venv\Scripts\activate
# macOS/Linux
source .venv/bin/activate

# Install deps
pip install -r requirements.txt
playwright install chromium
```

## Claude Desktop Config

Add to your `claude_desktop_config.json`:

**Windows** (`%APPDATA%\Claude\claude_desktop_config.json`):
```json
{
  "mcpServers": {
    "browser_mcp": {
      "command": "C:\\path\\to\\browser_mcp\\.venv\\Scripts\\python.exe",
      "args": ["C:\\path\\to\\browser_mcp\\server.py"]
    }
  }
}
```

**macOS** (`~/Library/Application Support/Claude/claude_desktop_config.json`):
```json
{
  "mcpServers": {
    "browser_mcp": {
      "command": "/path/to/browser_mcp/.venv/bin/python",
      "args": ["/path/to/browser_mcp/server.py"]
    }
  }
}
```

Restart Claude Desktop after updating config.

## Tech Stack

- Python 3.11+
- [FastMCP](https://github.com/jlowin/fastmcp) (MCP server framework)
- Playwright (headless Chromium)
- Pydantic v2 (input validation)

## License

MIT
