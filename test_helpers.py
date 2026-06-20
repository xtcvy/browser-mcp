"""Unit tests for pure helpers and model wiring in server.py.

Plain script (no pytest): `python test_helpers.py` — prints PASS/FAIL, exits
non-zero on failure. Uses IP literals so it runs offline (getaddrinfo resolves
literals without network)."""

import sys

from server import (
    MAX_CONTENT_LENGTH,
    _assert_public_host,
    _clean_markdown,
    _clean_text,
    _strip_html_comments,
    _strip_zero_width,
    _truncate,
    FetchPageInput,
    ScreenshotInput,
)

failures = []


def check(cond, msg):
    if not cond:
        failures.append(msg)


# --- _truncate boundary ---------------------------------------------------- #
exact = "a" * MAX_CONTENT_LENGTH
over = "a" * (MAX_CONTENT_LENGTH + 1)
check(_truncate(exact) == exact, "_truncate altered an at-limit string")
check(
    _truncate(over).endswith("[...truncated]"),
    "_truncate did not mark an over-limit string",
)
check(len(_truncate(over)) < len(over) + 20, "_truncate did not actually cut")

# --- zero-width / comment stripping --------------------------------------- #
check(_strip_zero_width("a​b­c﻿") == "abc", "_strip_zero_width missed a char")
check(
    _strip_html_comments("x<!-- secret instructions -->y") == "xy",
    "_strip_html_comments failed",
)
check(
    _strip_html_comments("a<!--\nmulti\nline\n-->b") == "ab",
    "_strip_html_comments not multiline",
)

# --- whitespace cleaners --------------------------------------------------- #
check(_clean_text("a   \t b\n\n\n\nc") == "a b\n\nc", "_clean_text collapse wrong")
check(_clean_text("  ​hi  ") == "hi", "_clean_text strip/zero-width wrong")
check(
    _clean_markdown("# h\n\n\n\n\ntext") == "# h\n\ntext",
    "_clean_markdown collapse wrong",
)

# --- SSRF host check (offline IP literals) --------------------------------- #
for bad in ["100.64.0.1", "169.254.169.254", "127.0.0.1", "10.1.2.3", "::1", "fd00::1"]:
    try:
        _assert_public_host(bad)
        failures.append(f"GUARD MISS: {bad} accepted")
    except ValueError:
        pass

for good in ["1.1.1.1", "8.8.8.8"]:
    try:
        ip = _assert_public_host(good)
        check(ip == good, f"validated IP for {good} was {ip}")
    except ValueError as e:
        failures.append(f"FALSE POSITIVE: {good} blocked ({e})")

# public IPv6 is allowed and returned bare; the host-resolver-rules builder is
# responsible for bracketing it (regression guard for the IPv6 pin fix)
try:
    v6 = _assert_public_host("2606:4700:4700::1111")
    check(v6 == "2606:4700:4700::1111", f"public IPv6 not returned bare: {v6}")
except ValueError as e:
    failures.append(f"FALSE POSITIVE: public IPv6 blocked ({e})")

# --- model wiring (offline: public IP literal as URL) ---------------------- #
ok = FetchPageInput(
    url="http://1.1.1.1/", channel="chrome", headless=False, wait_seconds=5
)
check(ok.format == "text" and ok.wait_for is None, "FetchPageInput defaults wrong")
check(
    ok.channel == "chrome" and ok.headless is False,
    "FetchPageInput field passthrough wrong",
)

# cdp_url is exempt from the SSRF guard (loopback control endpoint allowed)
cdp = FetchPageInput(url="http://1.1.1.1/", cdp_url="http://127.0.0.1:9222")
check(cdp.cdp_url == "http://127.0.0.1:9222", "cdp_url not accepted")

shot = ScreenshotInput(url="http://1.1.1.1/")
check(
    shot.image_format is None and shot.quality == 70 and shot.full_page is False,
    "ScreenshotInput defaults wrong",
)

# invalid channel rejected; extra fields forbidden
for bad_kwargs in [
    dict(url="http://1.1.1.1/", channel="firefox"),
    dict(url="http://1.1.1.1/", bogus=1),
]:
    try:
        FetchPageInput(**bad_kwargs)
        failures.append(f"VALIDATION MISS: accepted {bad_kwargs}")
    except Exception:
        pass

if failures:
    print("FAIL")
    for f in failures:
        print(" -", f)
    sys.exit(1)

print("PASS: helpers, SSRF host check, and model wiring all behave")
