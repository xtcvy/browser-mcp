"""Regression test for the SSRF guard in server.py (mirrors meow's test_ssrf_guard.py)."""
import sys

from server import _assert_public, FetchPageInput, ScreenshotInput

BLOCKED = [
    "http://127.0.0.1/",
    "http://localhost/",
    "http://10.0.0.5/",
    "http://192.168.1.1/admin",
    "http://172.16.0.1/",
    "http://169.254.169.254/latest/meta-data/",
    "http://[::1]/",
]

ALLOWED = [
    "https://example.com/",
    "https://www.cloudflare.com/",
]

failures = []

for url in BLOCKED:
    try:
        _assert_public(url)
        failures.append(f"GUARD MISS: {url} was allowed")
    except ValueError:
        pass

for url in BLOCKED:
    try:
        FetchPageInput(url=url)
        failures.append(f"VALIDATOR MISS (fetch): {url} was allowed")
    except ValueError:
        pass
    try:
        ScreenshotInput(url=url)
        failures.append(f"VALIDATOR MISS (screenshot): {url} was allowed")
    except ValueError:
        pass

for url in ALLOWED:
    try:
        _assert_public(url)
    except ValueError as e:
        failures.append(f"FALSE POSITIVE: {url} blocked ({e})")

if failures:
    print("FAIL")
    for f in failures:
        print(" -", f)
    sys.exit(1)

print(f"PASS: {len(BLOCKED)} blocked targets rejected, {len(ALLOWED)} public targets allowed")
