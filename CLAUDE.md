# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

Single-file MCP server (`server.py`) that exposes two tools — `browser_fetch_page` and `browser_screenshot` — backed by Playwright headless Chromium. The point of routing through a local browser (vs. plain HTTP) is to bypass Cloudflare, bot detection, and robots.txt blocks that reject Anthropic datacenter IPs.

## Setup & run

```bash
source .venv/bin/activate          # macOS/Linux
pip install -r requirements.txt
playwright install chromium        # required: downloads the browser binary
python server.py                   # runs the MCP server over stdio
```

The server is normally launched by Claude Desktop via `claude_desktop_config.json` (see README), not invoked directly. There is no build, lint, or test suite configured. `.gitignore` excludes `server_fixed.py`, `test_fetch.py`, `test_imports.py` — treat these names as scratch slots if you need ad-hoc testing.

Python 3.11–3.13 is the supported range. **3.14 breaks pydantic** per the README.

## Architecture notes

- **FastMCP decorators**, not raw MCP protocol. New tools are added by writing a Pydantic input model + `@mcp.tool(...)` async function. Tool annotations (`readOnlyHint`, `idempotentHint`, etc.) are set explicitly on each tool — keep them accurate when adding new ones.
- **Each tool call spins up a fresh Playwright instance** (`async_playwright().start()` → launch → context → page → close). There is no shared browser pool. This is simple but slow (~1–2s startup per call); if you change this, make sure cleanup in `finally` still runs on every exit path.
- **Errors propagate as exceptions.** Tools use `try`/`finally` only — no `except`. Exceptions surface to FastMCP, which converts them into MCP results with `isError: true` and the message as a text content block. Don't reintroduce `try/except` that swallows errors into a `{"success": false, ...}` envelope; the protocol's error signaling is what callers should rely on. (The inner `except Exception: pass` around `wait_for_selector` is a deliberate exception — selector timeouts are non-fatal there.)
- **Output is capped at `MAX_CONTENT_LENGTH` (50000 chars)** with a `[...truncated]` marker. `_clean_text` collapses whitespace before truncation.
- **Input validation lives in Pydantic models** (`FetchPageInput`, `ScreenshotInput`) with `extra="forbid"` and an explicit `http(s)://` check. Prefer adding validators there over inline checks in the tool body.
