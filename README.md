# browser_mcp

MCP server that fetches and screenshots web pages through a local Chromium browser via [Playwright](https://playwright.dev/python/) — rendering JavaScript and egressing from **your machine's real (residential) IP**.

## Why

Claude's built-in `web_fetch` runs from Anthropic datacenter IPs and respects `robots.txt`. Sites that block datacenter ranges or disallow bots in `robots.txt` (Amazon, eBay, Reddit, etc.) reject those requests. Fetching from your own IP, with a real browser rendering the page, gets past **IP/robots.txt filtering**.

### What this does _not_ do

By default it runs **bundled headless Chromium with no stealth patches** (`navigator.webdriver` is true, headless fingerprint, bundled-Chromium TLS signature). That means it does **not** defeat modern bot detection like Cloudflare's challenge — those are blocked at the TLS handshake before any page logic runs. Don't rely on it for protected targets. Instead:

- **Opt into a real browser** via `cdp_url` / `channel` / `user_data_dir` (see _Real-browser modes_) to use your actual Chrome, fingerprint, and logged-in session; or
- **Delegate to ScraplingServer** (`stealthy_fetch` / `solve_cloudflare`), which is purpose-built for stealth and Cloudflare-solving. Don't reinvent that here.

The honest positioning: **browser_mcp = "my browser, my session, my IP." Scrapling = "generic stealthy/bot-evading fetch."**

## Tools

| Tool                 | Description                                                             |
| -------------------- | ----------------------------------------------------------------------- |
| `browser_fetch_page` | Fetch a page as text, HTML, or Markdown; optional real-browser modes    |
| `browser_screenshot` | Capture a viewport or full-page screenshot (full-page defaults to JPEG) |

## Real-browser modes (optional, off by default)

All parameters below apply to both tools. Leaving them unset uses bundled headless Chromium.

| Param           | Effect                                                                                                                                                                                                                     |
| --------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `cdp_url`       | Attach to a **running** browser's DevTools endpoint (e.g. `http://127.0.0.1:9222`) and reuse its **logged-in session** + fingerprint. Start Chrome with `--remote-debugging-port=9222` first. Does not close your browser. |
| `channel`       | Drive an installed binary (`chrome`, `msedge`, …) instead of bundled Chromium — real fingerprint, fresh session.                                                                                                           |
| `user_data_dir` | Load a real, logged-in profile. The target browser must be **closed** first; point at a **copy** to avoid corrupting your main profile.                                                                                    |
| `headless`      | Set `false` to watch the browser / handle interactive logins.                                                                                                                                                              |

For a Cloudflare-protected page you're logged into, `cdp_url` is the strongest option: real session, real fingerprint, real IP.

## Other parameters

- `wait_seconds` (default **0**) — extra settle time after load; a `wait_for` CSS selector is usually better. `browser_fetch_page` reports `wait_for_satisfied` so you can tell a real result from a timed-out challenge page.
- `format` — `text` (default) / `html` / `markdown`.
- `image_format` / `quality` / `full_page` — screenshot controls. Output is capped (~4 MB) to fit the MCP message.
- `BROWSER_MCP_MAX_CONCURRENCY` env var (default **3**) — caps simultaneous browser instances.

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
- Playwright (bundled Chromium by default; optional real Chrome/Edge via channel/CDP/profile)
- Pydantic v2 (input validation)

## License

MIT
