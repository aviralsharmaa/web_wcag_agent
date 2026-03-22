from __future__ import annotations

import argparse
import json

from .engine import run_scan
from .models import PolicyMode, ScanRequest


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="WCAG 2.1 LangGraph accessibility scanner")
    parser.add_argument("--url", action="append", required=True, help="Seed URL(s) to start crawling")
    parser.add_argument("--domain", required=True, help="Domain scope, e.g. example.gov")
    parser.add_argument("--max-depth", type=int, default=2)
    parser.add_argument("--max-pages", type=int, default=25)
    parser.add_argument("--auth-script", default=None)
    parser.add_argument(
        "--policy-mode",
        choices=[PolicyMode.STRICT_GOV.value, PolicyMode.AUTOMATION_ONLY.value],
        default=PolicyMode.STRICT_GOV.value,
    )
    parser.add_argument("--artifacts-root", default="artifacts")
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    request = ScanRequest(
        start_urls=args.url,
        domain_scope=args.domain,
        max_depth=args.max_depth,
        max_pages=args.max_pages,
        auth_script_ref=args.auth_script,
        policy_mode=PolicyMode(args.policy_mode),
    )

    report = run_scan(request, artifacts_root=args.artifacts_root)
    print(json.dumps(report.to_dict(), indent=2))


if __name__ == "__main__":
    main()
