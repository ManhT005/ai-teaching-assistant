"""Small localhost-only JSON client for a staff member's own credential."""

import argparse
import json
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlparse
from urllib.request import Request, urlopen


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", help="/internal/teacher/... or /internal/faq/...")
    parser.add_argument("--token-file", type=Path, required=True)
    parser.add_argument("--method", choices=("GET", "POST", "PATCH"), default="GET")
    parser.add_argument("--body-file", type=Path)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    args = parser.parse_args(argv)
    parsed = urlparse(args.base_url)
    if (
        parsed.scheme != "http"
        or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
        or parsed.username
        or parsed.password
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        parser.error("Use an HTTP localhost base URL")
    if (
        not args.path.startswith(("/internal/teacher/", "/internal/faq/"))
        or ".." in args.path
        or "#" in args.path
    ):
        parser.error("Only internal staff paths are allowed")
    payload = args.body_file.read_bytes() if args.body_file else None
    if payload:
        json.loads(payload)
    request = Request(
        args.base_url.rstrip("/") + args.path,
        data=payload,
        method=args.method,
        headers={
            "X-Teacher-Token": args.token_file.read_text(encoding="utf-8").strip(),
            "Content-Type": "application/json",
        },
    )
    try:
        with urlopen(request, timeout=120) as response:
            print(json.dumps(json.loads(response.read()), ensure_ascii=False, indent=2))
    except HTTPError as error:
        print(f"Staff request failed: HTTP {error.code}")
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
