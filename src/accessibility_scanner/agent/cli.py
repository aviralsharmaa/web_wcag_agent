"""CLI entry point for the agentic accessibility scanner."""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


# Map short app IDs to their config file paths (relative to project root)
APP_CONFIG_MAP = {
    "LICMFIP": "config/licmf_investor_portal.json",
    "LICMFCW": "config/licmf_corporate_website.json",
    "HDFCSKY": "config/hdfc_sky_login.json",
    "HDFCWSUAT": "config/hdfc_wealthspectrum_uat.json",
    "KSLNEO": "config/ksl_neo.json",
    "KSLKINSITE": "config/ksl_kinsite.json",
    "ICICICUS": "config/icici_cug.json",
    "ICICIDSTOCK": "config/icici_direct_stock.json",
    "ICICIDPROD": "config/icici_direct_prod.json",
    "TATAPMS": "config/tata_pms.json",
    "TATAPENSION": "config/tata_pension_fund.json",
    "TATAPRAGATI": "config/tata_pragati.json",
    "TATAMF": "config/tata_mf.json",
    "TATAMFONLINE": "config/tata_mf_online.json",
    "ICICIDEMAT": "config/icici_open_demat.json",
    "AXISDIRECTUAT": "config/axis_direct_uat.json",
    "TATACAPRETAIL": "config/tata_capital_retail.json",
    "AXISDIRECTPUB": "config/axis_direct_public.json",
    "AXISCORPCONNECT": "config/axis_corp_connect.json",
    "AXISB2BUAT": "config/axis_b2b_uat.json",
    "ICICIDEMATUAT": "config/icici_open_demat_uat.json",
    "TRUSTMFUAT": "config/trust_mf_uat.json",
    "TRUSTDIT": "config/trust_dit_portal.json",
    "TATACAPLINK": "config/tata_capital_link.json",
    "TATACLENXT": "config/tata_cl_enxt.json",
    "TRUSTMFPUB": "config/trust_mf_public.json",
    "AXISDIRECTTRADE": "config/axis_direct_trading.json",
    "TEJIMANDI": "config/tejimandi.json",
    "MOTILAL": "config/motilal_oswal.json",
    "MOTILALALT": "config/motilal_alt.json",
    "TATACAPTSL": "config/tata_capital_tsl.json",
    "DHANUAT": "config/dhan_uat.json",
    "HDFCNPORTALUAT": "config/hdfc_nportal_uat.json",
    "HDFCTRU": "config/hdfc_tru.json",
    "HDFCINVNOWUAT": "config/hdfc_invnow_uat.json",
    "CANARAROBECO": "config/canara_robeco.json",
    "KOTAKALTERNATE": "config/kotak_alternate_asset.json",
    "KFINTECHAIFUAT": "config/kfintech_aif_uat.json",
    "TATACAPONLINE": "config/tata_capital_online.json",
    "KOTAKADVISORYUAT": "config/kotak_advisory_uat.json",
    "LICMFONLINE": "config/licmf_online.json",
    "CANARASIUAT": "config/canara_smart_investor_uat.json",
    "KOTAKOPTIMUSUAT": "config/kotak_optimus_uat.json",
    "KOTAKNEOTRADE": "config/kotak_neo_trade.json",
    "BONDSCANNER": "config/bondscanner.json",
    "GALAXYUAT": "config/galaxy_uat.json",
    "KSLNEOPRELOGIN": "config/ksl_neo_prelogin.json",
    "KSLKINSITEPRELOGIN": "config/ksl_kinsite_prelogin.json",
    "KSLKINSITEPOSTLOGIN": "config/ksl_kinsite_postlogin.json",
    "KOTAKCHERRY": "config/kotak_cherry.json",
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
    parser.add_argument(
        "--upload",
        choices=["onedrive", "catbox", "imgbb"],
        default=None,
        help="Upload screenshots to a hosting service for public URLs in the report",
    )
    parser.add_argument(
        "--cdp",
        default=None,
        help="Connect to a remote Chrome browser via CDP endpoint (e.g. http://localhost:9222)",
    )
    args = parser.parse_args()

    config_path = args.config or APP_CONFIG_MAP[args.app]
    scan_mode = "pre_login" if args.pre_login else "full_scan"

    # Build uploader if requested
    uploader = None
    if args.upload:
        from ..image_uploader import make_uploader
        # Derive report_id from app name or config filename to isolate uploads per report
        report_id = args.app or Path(config_path).stem
        uploader = make_uploader(provider=args.upload, report_id=report_id)

    from .flow_runner import AgenticFlowRunner

    runner = AgenticFlowRunner(
        config_path=config_path,
        artifacts_root=args.artifacts_root,
        headless=args.headless,
        scan_mode=scan_mode,
        uploader=uploader,
        cdp_endpoint=args.cdp,
    )
    report = runner.run()

    fail_count = report["totals"]["fail"]
    print(f"\nExit code: {1 if fail_count > 0 else 0} ({'FAILURES FOUND' if fail_count else 'ALL CLEAR'})")
    sys.exit(1 if fail_count > 0 else 0)


if __name__ == "__main__":
    main()
