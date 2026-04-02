"""CLI entry point for the agentic accessibility scanner."""
from __future__ import annotations

import argparse
import os
import sys


# Map short app IDs to their config file paths (relative to project root)
APP_CONFIG_MAP = {
    "LICMFIP": "config/licmf_investor_portal.json",
    "LICMFCW": "config/licmf_corporate_website.json",
    "HDFCSKY": "config/hdfc_sky_login.json",
    "HDFCWSUAT": "config/hdfc_wealthspectrum_uat.json",
    "KSLNEO": "config/ksl_neo.json",
    "KSLKINSITE": "config/ksl_kinsite.json",
}


def main():
    # Load .env from project root
    from dotenv import load_dotenv

    load_dotenv()

    parser = argparse.ArgumentParser(description="Agentic WCAG accessibility scanner")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--config", help="Path to flow config JSON")
    group.add_argument(
        "--app",
        choices=list(APP_CONFIG_MAP.keys()),
        help=f"App shortcut: {', '.join(APP_CONFIG_MAP.keys())}",
    )
    parser.add_argument("--artifacts-root", default="artifacts", help="Artifacts directory")
    parser.add_argument("--headless", action="store_true", help="Run in headless mode")
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument(
        "--pre-login",
        action="store_true",
        help="Run only pre-login/authentication screens and skip post-login exploration",
    )
    mode_group.add_argument(
        "--full-scan",
        action="store_true",
        help="Run the complete configured flow, including post-login exploration",
    )
    args = parser.parse_args()

    config_path = args.config or APP_CONFIG_MAP[args.app]
    scan_mode = "pre_login" if args.pre_login else "full_scan"

    from .flow_runner import AgenticFlowRunner

    runner = AgenticFlowRunner(
        config_path=config_path,
        artifacts_root=args.artifacts_root,
        headless=args.headless,
        scan_mode=scan_mode,
    )
    report = runner.run()

    fail_count = report["totals"]["fail"]
    print(f"\nExit code: {1 if fail_count > 0 else 0} ({'FAILURES FOUND' if fail_count else 'ALL CLEAR'})")
    sys.exit(1 if fail_count > 0 else 0)


if __name__ == "__main__":
    main()
