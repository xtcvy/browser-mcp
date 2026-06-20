"""
browser_mcp - Local MCP server that fetches web pages through a local browser.

What it actually is: a Playwright-driven fetch/screenshot tool that renders
JavaScript and egresses from *your* machine's real (residential) IP, so it sees
pages that Anthropic's datacenter `web_fetch` can't (datacenter-IP filtering,
robots.txt blocks). By default it runs bundled *headless* Chromium with no
stealth patches, so it does NOT defeat modern bot detection (Cloudflare, etc.) —
for that, opt into real-profile/CDP mode below or use the ScraplingServer
(`stealthy_fetch` / `solve_cloudflare`), which is purpose-built for it.

Optional real-browser modes (off by default):
  - cdp_url:        attach to a running Chrome's DevTools endpoint to reuse your
                    logged-in session + real fingerprint + real IP.
  - channel:        drive an installed Chrome/Edge binary (real fingerprint).
  - user_data_dir:  load a real, logged-in profile (browser must be closed).
"""

import asyncio
import base64
import ipaddress
import json
import os
import re
import socket
from contextlib import asynccontextmanager
from urllib.parse import urlparse
from typing import Literal, Optional

from markdownify import markdownify as html_to_markdown
from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, Field, ConfigDict, field_validator
from playwright.async_api import async_playwright

MAX_CONTENT_LENGTH = 50000
MAX_SCREENSHOT_BYTES = 4_000_000  # ~4 MB; inline base64 must fit the MCP message
DEFAULT_TIMEOUT = 30000
VIEWPORT = {"width": 1920, "height": 1080}

# Bound concurrent browser work so a burst of calls can't spawn N Chromium
# processes and OOM the host. Each held slot = one full browser lifecycle.
_MAX_CONCURRENCY = int(os.getenv("BROWSER_MCP_MAX_CONCURRENCY", "3"))
_SEM = asyncio.Semaphore(_MAX_CONCURRENCY)

mcp = FastMCP("browser_mcp")

# Networks that must never be fetched. The primary gate is `ip.is_global`; this
# list is belt-and-suspenders / documentation of intent.
_BLOCKED = [
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("169.254.0.0/16"),  # link-local + cloud metadata
    ipaddress.ip_network("100.64.0.0/10"),  # CGNAT / RFC 6598 shared space
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
]

_ZERO_WIDTH = re.compile(r"[­​‌‍‎‏⁠﻿]")

# Transient navigation errors worth a bounded retry (network blips, not 4xx/5xx
# pages — those return content, not exceptions, and a Cloudflare interstitial is
# a 200 so retrying never helps it).
_TRANSIENT_NAV = (
    "ERR_CONNECTION_RESET",
    "ERR_CONNECTION_CLOSED",
    "ERR_CONNECTION_ABORTED",
    "ERR_HTTP2_PROTOCOL_ERROR",
    "ERR_QUIC_PROTOCOL_ERROR",
    "ERR_NETWORK_CHANGED",
    "ERR_SOCKET_NOT_CONNECTED",
)

# Best-effort DOM sanitization for the markdown path: drop scripts/styles, nodes
# hidden via inline style or aria-hidden, <template>s, and HTML comments. This is
# a partial indirect-prompt-injection defense (it can't see CSS-class-based
# hiding), not a security boundary.
_SANITIZE_JS = """
() => {
  const b = document.body.cloneNode(true);
  b.querySelectorAll('script, style, noscript, template, [aria-hidden="true"]')
    .forEach(e => e.remove());
  b.querySelectorAll('[style]').forEach(e => {
    const s = e.getAttribute('style') || '';
    if (/display\\s*:\\s*none|visibility\\s*:\\s*hidden|opacity\\s*:\\s*0(?!\\.)/i.test(s))
      e.remove();
  });
  const it = document.createNodeIterator(b, NodeFilter.SHOW_COMMENT);
  const dead = []; let n;
  while ((n = it.nextNode())) dead.push(n);
  dead.forEach(c => c.parentNode && c.parentNode.removeChild(c));
  return b.innerHTML;
}
"""


# --------------------------------------------------------------------------- #
# SSRF guard
# --------------------------------------------------------------------------- #
def _assert_public_host(host: str) -> str:
    """Resolve `host` and raise ValueError if any address is non-public.

    Returns one validated public IP (used to DNS-pin the fetch so the browser
    can't re-resolve to a private target between this check and the request).
    """
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as e:
        raise ValueError(f"Cannot resolve host: {host}") from e
    validated: Optional[str] = None
    for info in infos:
        sockaddr = info[4]
        ip = ipaddress.ip_address(sockaddr[0])
        if (
            not ip.is_global
            or ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
            or any(ip in net for net in _BLOCKED)
        ):
            raise ValueError(
                f"Refusing to fetch internal/non-public address: {ip} ({host})"
            )
        if validated is None:
            validated = str(ip)
    if validated is None:
        raise ValueError(f"No addresses resolved for host: {host}")
    return validated


def _assert_public(url: str) -> None:
    """SSRF guard: refuse private/loopback/link-local/CGNAT targets."""
    host = urlparse(url).hostname
    if host is None:
        raise ValueError("URL has no host")
    _assert_public_host(host)


def _make_route_guard():
    """Per-call request guard: re-validate every request's host (incl. redirect
    hops and subresources) and abort any that resolves to a non-public address.
    Caches per host for the call's duration."""
    cache: dict[str, bool] = {}

    async def guard(route):
        try:
            host = urlparse(route.request.url).hostname
            if host is not None:
                ok = cache.get(host)
                if ok is None:
                    try:
                        _assert_public_host(host)
                        ok = True
                    except ValueError:
                        ok = False
                    cache[host] = ok
                if ok is False:
                    await route.abort()
                    return
            await route.continue_()
        except Exception:
            # Fail CLOSED on unexpected errors: in CDP mode this guard is the
            # only SSRF defense (no host-resolver pin), so aborting is safer than
            # forwarding. Aborting a request degrades the page; it can't wedge it.
            try:
                await route.abort()
            except Exception:
                pass

    return guard


# --------------------------------------------------------------------------- #
# Text helpers
# --------------------------------------------------------------------------- #
def _strip_zero_width(text: str) -> str:
    return _ZERO_WIDTH.sub("", text)


def _strip_html_comments(html: str) -> str:
    return re.sub(r"<!--.*?-->", "", html, flags=re.DOTALL)


def _truncate(text: str) -> str:
    if len(text) > MAX_CONTENT_LENGTH:
        return text[:MAX_CONTENT_LENGTH] + "\n\n[...truncated]"
    return text


def _clean_text(raw: str) -> str:
    text = _strip_zero_width(raw)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]+", " ", text)
    return _truncate(text.strip())


def _clean_markdown(raw: str) -> str:
    text = _strip_zero_width(raw)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return _truncate(text)


# --------------------------------------------------------------------------- #
# Shared browser lifecycle
# --------------------------------------------------------------------------- #
@asynccontextmanager
async def browser_page(
    *,
    cdp_url: Optional[str] = None,
    channel: Optional[str] = None,
    user_data_dir: Optional[str] = None,
    headless: bool = True,
    host_pin: Optional[tuple[str, str]] = None,
):
    """Yield a ready Playwright `page`, guaranteeing cleanup, under the
    concurrency semaphore. Handles four modes: bundled Chromium (default),
    installed-binary (`channel`), real-profile (`user_data_dir`), and attach to
    a running browser (`cdp_url`). Never closes the user's CDP browser."""
    async with _SEM:
        pw = await async_playwright().start()
        browser = None
        context = None
        page = None
        created_context = False
        try:
            if cdp_url:
                # Attach to the user's running browser; reuse its logged-in
                # context so cookies/sessions are available.
                browser = await pw.chromium.connect_over_cdp(cdp_url)
                if browser.contexts:
                    context = browser.contexts[0]
                else:
                    context = await browser.new_context(
                        viewport=VIEWPORT, java_script_enabled=True
                    )
                    created_context = True
                page = await context.new_page()
            else:
                args = []
                if host_pin:
                    # Pin the target host to the IP we validated, defeating
                    # DNS-rebinding between the SSRF check and the request.
                    # IPv6 replacement addresses must be bracketed in the rule.
                    pin_host, pin_ip = host_pin
                    if ":" in pin_ip:
                        pin_ip = f"[{pin_ip}]"
                    args.append(f"--host-resolver-rules=MAP {pin_host} {pin_ip}")
                if user_data_dir:
                    context = await pw.chromium.launch_persistent_context(
                        user_data_dir,
                        channel=channel,
                        headless=headless,
                        args=args,
                        viewport=VIEWPORT,
                        java_script_enabled=True,
                    )
                    created_context = True
                    page = await context.new_page()
                else:
                    browser = await pw.chromium.launch(
                        channel=channel, headless=headless, args=args
                    )
                    context = await browser.new_context(
                        viewport=VIEWPORT, java_script_enabled=True
                    )
                    created_context = True
                    page = await context.new_page()
            yield page
        finally:
            try:
                if page is not None:
                    await page.close()
            except Exception:
                pass
            # Only tear down what we own. For cdp_url we attached to the user's
            # browser — never close it or its context.
            if cdp_url is None:
                try:
                    if created_context and context is not None:
                        await context.close()
                except Exception:
                    pass
                try:
                    if browser is not None:
                        await browser.close()
                except Exception:
                    pass
            await pw.stop()


async def _goto(page, url: str):
    """Navigate with a bounded retry on transient network errors, raising a
    descriptive RuntimeError (URL + wait condition + timeout) on failure."""
    last = None
    attempts = 3
    for i in range(attempts):
        try:
            return await page.goto(
                url, timeout=DEFAULT_TIMEOUT, wait_until="domcontentloaded"
            )
        except Exception as e:
            last = e
            if i < attempts - 1 and any(t in str(e) for t in _TRANSIENT_NAV):
                await asyncio.sleep(0.5 * (i + 1))
                continue
            raise RuntimeError(
                f"Failed to load {url} "
                f"(wait_until=domcontentloaded, timeout={DEFAULT_TIMEOUT}ms, "
                f"attempt {i + 1}/{attempts}): {e}"
            ) from e
    raise RuntimeError(f"Failed to load {url}: {last}") from last  # unreachable


def _pin_for(url: str, cdp_url: Optional[str]) -> Optional[tuple[str, str]]:
    """Validate the target and return (host, ip) to DNS-pin, except in CDP mode
    (we don't launch the browser there, so we can't pass --host-resolver-rules)."""
    if cdp_url:
        return None
    host = urlparse(url).hostname
    return (host, _assert_public_host(host)) if host else None


# --------------------------------------------------------------------------- #
# Input models
# --------------------------------------------------------------------------- #
class _BrowserBase(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    url: str = Field(
        ..., description="Full http(s) URL to load", min_length=8, max_length=2048
    )
    wait_seconds: int = Field(
        default=0,
        ge=0,
        le=30,
        description="Extra seconds to sleep after load. Default 0; raise it for "
        "JS-heavy pages that need to settle (a wait_for selector is usually better).",
    )
    cdp_url: Optional[str] = Field(
        default=None,
        description="Attach to a RUNNING browser's CDP endpoint "
        "(e.g. http://127.0.0.1:9222) to reuse your logged-in session, real "
        "fingerprint and IP. Reuses the existing browser and does NOT close it. "
        "Start Chrome with --remote-debugging-port=9222 first.",
    )
    channel: Optional[
        Literal[
            "chrome", "msedge", "chrome-beta", "chrome-dev", "msedge-beta", "msedge-dev"
        ]
    ] = Field(
        default=None,
        description="Drive an installed Chromium-family browser binary instead of "
        "bundled Chromium (real binary/fingerprint, fresh session). Ignored when "
        "cdp_url is set.",
    )
    user_data_dir: Optional[str] = Field(
        default=None,
        description="Path to a Chrome user-data dir to load a real, logged-in "
        "profile. The target browser MUST be closed first; point at a COPY to "
        "avoid corrupting your main profile. Ignored when cdp_url is set.",
    )
    headless: bool = Field(
        default=True,
        description="Run headless. Set false to watch the browser or for "
        "interactive login flows.",
    )

    @field_validator("url")
    @classmethod
    def validate_url(cls, v: str) -> str:
        if not v.startswith(("http://", "https://")):
            raise ValueError("URL must start with http:// or https://")
        _assert_public(v)
        return v

    @field_validator("cdp_url")
    @classmethod
    def validate_cdp_url(cls, v: Optional[str]) -> Optional[str]:
        # The CDP endpoint is a local control channel, not a fetch target, so it
        # is intentionally exempt from the SSRF guard (it's typically loopback).
        if v is not None and not v.startswith(
            ("http://", "https://", "ws://", "wss://")
        ):
            raise ValueError("cdp_url must start with http(s):// or ws(s)://")
        return v


class FetchPageInput(_BrowserBase):
    wait_for: Optional[str] = Field(
        default=None, description="CSS selector to wait for before extracting content"
    )
    format: Literal["text", "html", "markdown"] = Field(
        default="text",
        description=(
            "Output format: 'text' (cleaned body text), 'html' (full page HTML), "
            "or 'markdown' (body converted to Markdown, links/structure preserved)"
        ),
    )


class ScreenshotInput(_BrowserBase):
    full_page: bool = Field(default=False, description="Capture full scrollable page")
    image_format: Optional[Literal["png", "jpeg"]] = Field(
        default=None,
        description="Image format. Default: png for viewport shots, jpeg for "
        "full_page (smaller payload).",
    )
    quality: int = Field(
        default=70, ge=1, le=100, description="JPEG quality (ignored for png)"
    )


# --------------------------------------------------------------------------- #
# Tools
# --------------------------------------------------------------------------- #
@mcp.tool(
    name="browser_fetch_page",
    annotations={
        "title": "Fetch Page via Browser",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def browser_fetch_page(params: FetchPageInput) -> str:
    """Fetch a web page using a local Chromium browser (your real IP).

    Renders JavaScript and returns text/html/markdown. Default mode is headless
    bundled Chromium with no stealth — it does NOT bypass Cloudflare/bot
    detection. For protected sites, use cdp_url/channel/user_data_dir to drive
    your real browser, or use ScraplingServer's stealthy_fetch."""
    pin = _pin_for(params.url, params.cdp_url)
    async with browser_page(
        cdp_url=params.cdp_url,
        channel=params.channel,
        user_data_dir=params.user_data_dir,
        headless=params.headless,
        host_pin=pin,
    ) as page:
        await page.route("**/*", _make_route_guard())
        await _goto(page, params.url)

        wait_for_satisfied = None
        if params.wait_for:
            try:
                await page.wait_for_selector(params.wait_for, timeout=10000)
                wait_for_satisfied = True
            except Exception:
                wait_for_satisfied = False  # non-fatal: extract whatever loaded

        if params.wait_seconds and params.wait_seconds > 0:
            await asyncio.sleep(params.wait_seconds)

        title = await page.title()
        final_url = page.url
        if params.format == "html":
            content = _truncate(
                _strip_zero_width(_strip_html_comments(await page.content()))
            )
        elif params.format == "markdown":
            body_html = await page.evaluate(_SANITIZE_JS)
            content = _clean_markdown(html_to_markdown(body_html, heading_style="ATX"))
        else:
            content = _clean_text(await page.inner_text("body"))

        result = {
            "title": title,
            "url": final_url,
            "format": params.format,
            "content_length": len(content),
            "content": content,
        }
        if params.wait_for is not None:
            result["wait_for_satisfied"] = wait_for_satisfied
        return json.dumps(result, indent=2)


@mcp.tool(
    name="browser_screenshot",
    annotations={
        "title": "Screenshot Page via Browser",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def browser_screenshot(params: ScreenshotInput) -> list[dict]:
    """Screenshot a web page using a local Chromium browser. Full-page captures
    default to JPEG to keep the inline payload under the message-size cap."""
    pin = _pin_for(params.url, params.cdp_url)
    async with browser_page(
        cdp_url=params.cdp_url,
        channel=params.channel,
        user_data_dir=params.user_data_dir,
        headless=params.headless,
        host_pin=pin,
    ) as page:
        await page.route("**/*", _make_route_guard())
        await _goto(page, params.url)

        if params.wait_seconds and params.wait_seconds > 0:
            await asyncio.sleep(params.wait_seconds)

        fmt = params.image_format or ("jpeg" if params.full_page else "png")
        shot_kwargs = {"full_page": params.full_page, "type": fmt}
        if fmt == "jpeg":
            shot_kwargs["quality"] = params.quality
        data = await page.screenshot(**shot_kwargs)
        if len(data) > MAX_SCREENSHOT_BYTES:
            raise RuntimeError(
                f"Screenshot is {len(data) // 1024} KB, over the "
                f"{MAX_SCREENSHOT_BYTES // 1024} KB cap. Try full_page=false, "
                f"image_format='jpeg', or a lower quality."
            )
        b64 = base64.b64encode(data).decode("utf-8")
        title = await page.title()
        mime = "image/jpeg" if fmt == "jpeg" else "image/png"
        return [
            {
                "type": "text",
                "text": (
                    f"Screenshot of: {title}\nURL: {page.url}\n"
                    f"Format: {fmt}, {len(data) // 1024} KB"
                ),
            },
            {"type": "image", "data": b64, "mimeType": mime},
        ]


if __name__ == "__main__":
    mcp.run()
