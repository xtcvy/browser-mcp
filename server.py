"""
browser_mcp - Local MCP server that fetches web pages through a real browser.
Bypasses Cloudflare, bot detection, and robots.txt blocks by using Playwright
with your actual browser fingerprint and IP.
"""

import asyncio
import base64
import json
import re
from typing import Optional

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, Field, ConfigDict, field_validator
from playwright.async_api import async_playwright

MAX_CONTENT_LENGTH = 50000
DEFAULT_TIMEOUT = 30000
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0.0.0 Safari/537.36"
)

mcp = FastMCP("browser_mcp")


def _clean_text(raw: str) -> str:
    text = re.sub(r"\n{3,}", "\n\n", raw)
    text = re.sub(r"[ \t]+", " ", text)
    text = text.strip()
    if len(text) > MAX_CONTENT_LENGTH:
        text = text[:MAX_CONTENT_LENGTH] + "\n\n[...truncated]"
    return text


class FetchPageInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    url: str = Field(..., description="Full URL to fetch", min_length=8, max_length=2048)
    wait_for: Optional[str] = Field(default=None, description="CSS selector to wait for before extracting content")
    wait_seconds: Optional[int] = Field(default=3, description="Seconds to wait after page load", ge=0, le=30)

    @field_validator("url")
    @classmethod
    def validate_url(cls, v: str) -> str:
        if not v.startswith(("http://", "https://")):
            raise ValueError("URL must start with http:// or https://")
        return v


class ScreenshotInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    url: str = Field(..., description="Full URL to screenshot", min_length=8, max_length=2048)
    full_page: bool = Field(default=False, description="Capture full scrollable page")
    wait_seconds: Optional[int] = Field(default=3, description="Seconds to wait after page load", ge=0, le=30)

    @field_validator("url")
    @classmethod
    def validate_url(cls, v: str) -> str:
        if not v.startswith(("http://", "https://")):
            raise ValueError("URL must start with http:// or https://")
        return v


@mcp.tool(
    name="browser_fetch_page",
    annotations={"title": "Fetch Page via Browser", "readOnlyHint": True,
                 "destructiveHint": False, "idempotentHint": True, "openWorldHint": True},
)
async def browser_fetch_page(params: FetchPageInput) -> str:
    """Fetch a web page using a real Chromium browser, bypassing bot detection."""
    pw = None
    browser = None
    try:
        pw = await async_playwright().start()
        browser = await pw.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent=USER_AGENT,
            viewport={"width": 1920, "height": 1080},
            java_script_enabled=True,
        )
        page = await context.new_page()
        await page.goto(params.url, timeout=DEFAULT_TIMEOUT, wait_until="domcontentloaded")
        if params.wait_for:
            try:
                await page.wait_for_selector(params.wait_for, timeout=10000)
            except Exception:
                pass
        if params.wait_seconds and params.wait_seconds > 0:
            await asyncio.sleep(params.wait_seconds)
        title = await page.title()
        url = page.url
        text = await page.inner_text("body")
        cleaned = _clean_text(text)
        return json.dumps({"success": True, "title": title, "url": url,
                           "content_length": len(cleaned), "content": cleaned}, indent=2)
    except Exception as e:
        return json.dumps({"success": False, "error": f"{type(e).__name__}: {str(e)}",
                           "url": params.url}, indent=2)
    finally:
        if browser:
            await browser.close()
        if pw:
            await pw.stop()


@mcp.tool(
    name="browser_screenshot",
    annotations={"title": "Screenshot Page via Browser", "readOnlyHint": True,
                 "destructiveHint": False, "idempotentHint": True, "openWorldHint": True},
)
async def browser_screenshot(params: ScreenshotInput) -> list:
    """Take a screenshot of a web page using a real Chromium browser."""
    pw = None
    browser = None
    try:
        pw = await async_playwright().start()
        browser = await pw.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent=USER_AGENT,
            viewport={"width": 1920, "height": 1080},
            java_script_enabled=True,
        )
        page = await context.new_page()
        await page.goto(params.url, timeout=DEFAULT_TIMEOUT, wait_until="domcontentloaded")
        if params.wait_seconds and params.wait_seconds > 0:
            await asyncio.sleep(params.wait_seconds)
        screenshot_bytes = await page.screenshot(full_page=params.full_page)
        b64 = base64.b64encode(screenshot_bytes).decode("utf-8")
        title = await page.title()
        return [
            {"type": "text", "text": f"Screenshot of: {title}\nURL: {params.url}"},
            {"type": "image", "data": b64, "mimeType": "image/png"},
        ]
    except Exception as e:
        return json.dumps({"success": False, "error": f"{type(e).__name__}: {str(e)}",
                           "url": params.url}, indent=2)
    finally:
        if browser:
            await browser.close()
        if pw:
            await pw.stop()


if __name__ == "__main__":
    mcp.run()
