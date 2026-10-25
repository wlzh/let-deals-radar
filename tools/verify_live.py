"""Wait for the public site to match this run's generated artifacts."""
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import time
from urllib.error import URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


FILES = ("index.html", "data/deals.json", "sitemap.xml")


def verify_once(url: str, site: Path) -> list[str]:
    mismatches = []
    for name in FILES:
        expected = (site / name).read_bytes()
        request = Request(
            f"{url.rstrip('/')}/{name}",
            headers={"Cache-Control": "no-cache", "User-Agent": "let-deals-radar-deploy-check"},
        )
        try:
            with urlopen(request, timeout=20) as response:
                actual = response.read()
            if hashlib.sha256(expected).digest() != hashlib.sha256(actual).digest():
                mismatches.append(f"{name}: content differs")
        except (URLError, TimeoutError, OSError) as exc:
            mismatches.append(f"{name}: {exc}")
    return mismatches


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True)
    parser.add_argument("--site", type=Path, default=Path("site"))
    parser.add_argument("--attempts", type=int, default=1)
    parser.add_argument("--delay", type=float, default=15)
    args = parser.parse_args()
    if urlsplit(args.url).scheme not in {"http", "https"}:
        parser.error("--url must be HTTP(S)")
    if args.attempts < 1 or args.delay < 0:
        parser.error("attempts must be positive and delay must be non-negative")
    for attempt in range(args.attempts):
        errors = verify_once(args.url, args.site)
        if not errors:
            print(f"Public deployment verified: {', '.join(FILES)}", flush=True)
            return 0
        print(f"Attempt {attempt + 1}/{args.attempts}: {'; '.join(errors)}", flush=True)
        if attempt + 1 < args.attempts:
            time.sleep(args.delay)
    print("::error::Public deployment did not match generated artifacts.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
