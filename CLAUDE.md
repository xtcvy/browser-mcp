# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

Single-file MCP server (`server.py`) that exposes two tools — `browser_fetch_page` and `browser_screenshot` — backed by Playwright Chromium. The point of routing through a _local_ browser (vs. plain HTTP) is to render JavaScript and egress from the user's real (residential) IP, defeating **datacenter-IP and robots.txt filtering**.

It does **not** defeat real bot detection (Cloudflare challenges, etc.) in its default mode — bundled headless Chromium with no stealth has `navigator.webdriver=true` and a headless TLS/JA3 fingerprint blocked at the handshake. Don't claim otherwise in docs or tool descriptions. For protected targets, the user opts into a real browser (`cdp_url` / `channel` / `user_data_dir`) or uses the sibling **ScraplingServer** (`stealthy_fetch` / `solve_cloudflare`). Position browser_mcp as "my browser, my session, my IP," not as a stealth tool.

## Setup & run

```bash
source .venv/bin/activate          # macOS/Linux
pip install -r requirements.txt
playwright install chromium        # required: downloads the browser binary
python server.py                   # runs the MCP server over stdio
```

The server is normally launched by Claude Desktop via `claude_desktop_config.json` (see README), not invoked directly. Tests are plain scripts (no pytest dependency): run `python test_ssrf_guard.py` and `python test_helpers.py` from the venv — each prints `PASS`/`FAIL` and exits non-zero on failure. Code is auto-formatted by ruff (a `.ruff_cache` is present). `.gitignore` excludes `server_fixed.py`, `test_imports.py` — treat these names as scratch slots.

Python 3.11–3.13 is the supported range. **3.14 breaks pydantic** per the README.

## Architecture notes

- **FastMCP decorators**, not raw MCP protocol. New tools are added by writing a Pydantic input model + `@mcp.tool(...)` async function. Tool annotations (`readOnlyHint`, `idempotentHint`, etc.) are set explicitly on each tool — keep them accurate when adding new ones.
- **Browser lifecycle goes through the `browser_page()` async context manager** — the single seam both tools share. It starts Playwright, opens a browser/context/page in one of four modes, yields the `page`, and guarantees teardown. Add new browser behavior here, not in the tool bodies. It still launches per call (no pool — a deliberately simple choice; the launch tax is ~0.25s, far smaller than typical navigation), but every call is wrapped in a module-level `asyncio.Semaphore` (`BROWSER_MCP_MAX_CONCURRENCY`, default 3) so a burst can't spawn N Chromium processes.
- **Four browser modes, default off:** bundled headless Chromium (default); `channel` (installed Chrome/Edge binary); `user_data_dir` (real logged-in profile via `launch_persistent_context`); `cdp_url` (attach to a running browser via `connect_over_cdp` and reuse its session). **In CDP mode never close the user's browser/context** — only the page we created. The shared fields live on the `_BrowserBase` model that both input models extend.
- **SSRF guard is two-layered.** `_assert_public` / `_assert_public_host` reject any host resolving to a non-public address (primary gate is `ip.is_global`, plus an explicit blocklist incl. CGNAT and cloud metadata). Beyond the up-front check: (1) the validated IP is **DNS-pinned** via `--host-resolver-rules=MAP host ip` so the browser can't re-resolve to a private target (rebinding); (2) a per-call `page.route` guard re-validates **every** request host (redirect hops + subresources) and aborts private ones. CDP mode skips the pin (we don't launch) but keeps the route guard. Residual TOCTOU on subresources is accepted for a single-user local tool. `cdp_url` is intentionally exempt from the guard (control endpoint, usually loopback).
- **Errors propagate as exceptions.** Tools use `try`/`finally` only — no error-swallowing. Exceptions surface to FastMCP as `isError: true`. Don't reintroduce a `{"success": false, ...}` envelope. Navigation goes through `_goto`, which retries a few transient network errors (`_TRANSIENT_NAV`) and otherwise raises a `RuntimeError` naming the URL/wait condition/timeout. The `wait_for` selector timeout is still non-fatal, but is now **surfaced** as `wait_for_satisfied: bool` in the result (only present when `wait_for` was requested) so callers can distinguish a real result from a timed-out challenge page.
- **Output is capped at `MAX_CONTENT_LENGTH` (50000 chars)** via `_truncate`; all text paths strip zero-width/soft-hyphen chars. `text` uses `inner_text` (hidden text already excluded) + whitespace collapse; `markdown` runs `markdownify` over a sanitized `<body>` clone (`_SANITIZE_JS` drops script/style/noscript/template, `aria-hidden`, inline-hidden nodes, and HTML comments — best-effort prompt-injection hygiene, not a security boundary); `html` is full `page.content()` with comments + zero-width stripped. Chosen format is echoed back.
- **Screenshots are size-guarded.** Full-page defaults to JPEG (`quality`, default 70); viewport defaults to PNG; override via `image_format`. Captures over `MAX_SCREENSHOT_BYTES` (~4 MB) raise rather than return a too-large inline payload.
- **Input validation lives in Pydantic models.** `_BrowserBase` (shared fields + `url`/`cdp_url` validators, `extra="forbid"`) is extended by `FetchPageInput` (adds `wait_for`, `format`) and `ScreenshotInput` (adds `full_page`, `image_format`, `quality`). Prefer adding validators there over inline checks. Note `test_ssrf_guard.py` imports `_assert_public`, `FetchPageInput`, `ScreenshotInput` — keep those names/constructible-with-just-`url`.
