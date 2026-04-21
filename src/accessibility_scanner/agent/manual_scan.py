"""Manual scan mode: injects a 'Scan Now' button on the page.

Click the button to scan the current screen state. The scanner captures,
analyzes, and annotates each screen. Press Ctrl+C or close the browser to
stop and generate the report.

Usage:
    python -m accessibility_scanner.agent.manual_scan --url <URL> [--upload onedrive] [--app-name "My App"]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

# Floating button JS injected into every page
INJECT_SCAN_BUTTON_JS = """
(() => {
    // Always remove old ones first to avoid duplicates
    const old = document.getElementById('__a11y_scan_container');
    if (old) old.remove();

    // Create a shadow DOM container so site CSS/JS can't touch our button
    const host = document.createElement('div');
    host.id = '__a11y_scan_container';
    host.style.cssText = 'position:fixed;top:10px;right:10px;z-index:2147483647;pointer-events:auto;';
    const shadow = host.attachShadow({mode: 'closed'});

    shadow.innerHTML = `
        <style>
            #btn {
                padding: 12px 22px; font-size: 15px; font-weight: bold;
                background: #1F497D; color: white; border: 3px solid #fff;
                border-radius: 10px; cursor: pointer; font-family: Arial, sans-serif;
                box-shadow: 0 4px 20px rgba(0,0,0,0.4);
                display: block;
            }
            #btn:hover { background: #2E6BB5; transform: scale(1.05); }
            #status {
                margin-top: 6px; padding: 6px 12px; font-size: 11px;
                background: rgba(0,0,0,0.8); color: #0f0; border-radius: 6px;
                font-family: monospace; display: none; max-width: 280px;
            }
        </style>
        <button id="btn">♿ Scan Now</button>
        <div id="status"></div>
    `;

    const btn = shadow.getElementById('btn');
    const status = shadow.getElementById('status');

    btn.addEventListener('click', () => {
        window.__a11y_scan_requested = true;
        btn.textContent = '⏳ Scanning...';
        btn.style.background = '#FF8C00';
        btn.disabled = true;
    });

    document.documentElement.appendChild(host);

    window.__a11y_scan_requested = window.__a11y_scan_requested || false;
    window.__a11y_set_status = (msg) => {
        status.style.display = 'block';
        status.textContent = msg;
    };
    window.__a11y_scan_done = () => {
        btn.textContent = '♿ Scan Now';
        btn.style.background = '#1F497D';
        btn.disabled = false;
        setTimeout(() => { status.style.display = 'none'; }, 3000);
    };

    // Keep alive: re-attach if removed by site JS
    const observer = new MutationObserver(() => {
        if (!document.getElementById('__a11y_scan_container')) {
            document.documentElement.appendChild(host);
        }
    });
    observer.observe(document.documentElement, { childList: true, subtree: true });
})();
"""


def main():
    from dotenv import load_dotenv
    load_dotenv()

    parser = argparse.ArgumentParser(description="Manual scan mode with injected Scan button")
    parser.add_argument("--url", required=True, help="Starting URL")
    parser.add_argument("--app-name", default="Manual Scan", help="App name for the report")
    parser.add_argument("--artifacts-root", default="artifacts", help="Artifacts directory")
    parser.add_argument("--upload", choices=["onedrive", "catbox", "imgbb"], default=None)
    parser.add_argument("--cdp", action="store_true", help="Use real Chrome via CDP (bypasses Cloudflare)")
    args = parser.parse_args()

    # Build uploader if requested
    uploader = None
    if args.upload:
        from ..image_uploader import make_uploader
        report_id = args.app_name.replace(" ", "_").upper()
        uploader = make_uploader(provider=args.upload, report_id=report_id)

    # Create run directory
    from datetime import datetime
    date_str = datetime.now().strftime("%d-%m-%y")
    run_id = f"MANUAL-{date_str}-1"
    run_dir = os.path.join(args.artifacts_root, run_id)
    counter = 1
    while os.path.exists(run_dir):
        counter += 1
        run_id = f"MANUAL-{date_str}-{counter}"
        run_dir = os.path.join(args.artifacts_root, run_id)
    os.makedirs(run_dir)

    print(f"\n{'#'*60}")
    print(f"  MANUAL SCAN MODE")
    print(f"  URL: {args.url}")
    print(f"  App: {args.app_name}")
    print(f"  Artifacts: {run_dir}")
    print(f"  Click the '♿ Scan Now' button on the page to scan")
    print(f"  Press Ctrl+C to stop and generate the report")
    print(f"{'#'*60}\n")

    # Launch browser
    from playwright.sync_api import sync_playwright
    import tempfile
    import subprocess as _sp

    pw = sync_playwright().start()
    _chrome_proc = None

    if args.cdp:
        # Launch real Chrome with remote debugging — completely clean, no Playwright fingerprints
        import shutil
        chrome_path = shutil.which("google-chrome") or shutil.which("google-chrome-stable")
        if not chrome_path:
            # macOS
            mac_chrome = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
            if os.path.isfile(mac_chrome):
                chrome_path = mac_chrome
        if not chrome_path:
            print("  ERROR: Real Chrome not found. Install Chrome or use without --cdp.")
            sys.exit(1)

        cdp_port = 9222
        user_data_dir = tempfile.mkdtemp(prefix="chrome_manual_")
        _chrome_proc = _sp.Popen([
            chrome_path,
            f"--remote-debugging-port={cdp_port}",
            f"--user-data-dir={user_data_dir}",
            "--no-first-run",
            "--no-default-browser-check",
            "--window-size=1440,900",
            "about:blank",
        ], stdout=_sp.DEVNULL, stderr=_sp.DEVNULL)
        time.sleep(3)  # Wait for Chrome to start

        browser = pw.chromium.connect_over_cdp(f"http://localhost:{cdp_port}")
        context = browser.contexts[0]
        page = context.pages[0] if context.pages else context.new_page()
    else:
        user_data_dir = tempfile.mkdtemp(prefix="pw_manual_")
        context = pw.chromium.launch_persistent_context(
            user_data_dir,
            headless=False,
            slow_mo=100,
            channel="chrome",
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-first-run",
                "--no-default-browser-check",
                "--disable-features=AutomationControlled",
                "--disable-infobars",
                "--excludeSwitches=enable-automation",
            ],
            viewport={"width": 1440, "height": 900},
            locale="en-US",
            timezone_id="Asia/Kolkata",
            ignore_https_errors=True,
        )
        browser = None
        page = context.pages[0] if context.pages else context.new_page()

    # Navigate
    page.goto(args.url, wait_until="domcontentloaded", timeout=30000)
    time.sleep(2)

    # Inject scan button
    page.evaluate(INJECT_SCAN_BUTTON_JS)

    # Re-inject on navigation
    page.on("load", lambda: _safe_inject(page))

    # Import analysis tools
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
    from accessibility_scanner.agent.flow_runner import AgenticFlowRunner

    # Create a minimal flow runner for analysis
    # We'll use its methods for screenshots, annotations, and WCAG analysis
    config = {
        "name": args.app_name,
        "app_id": "MANUAL",
        "start_url": args.url,
        "domain": "",
        "flow_steps": [],
        "analysis": {
            "analyze_every_screen": True,
            "screenshot_each_step": True,
            "run_wcag_buckets": True,
            "explore_all_routes": False,
            "max_screens": 100,
            "unique_screen_dedup": False,  # Don't dedup — user decides what to scan
            "cannot_verify_policy": "pass_leaning",
            "cannot_verify_threshold": 31,
            "cannot_verify_enforcement": "both",
            "run_zoom_test": True,
            "run_text_spacing_test": True,
            "run_keyboard_test": True,
            "run_screen_reader_test": True,
        },
    }

    # Write temp config
    config_path = os.path.join(run_dir, "_config.json")
    with open(config_path, "w") as f:
        json.dump(config, f)

    runner = AgenticFlowRunner(
        config_path=config_path,
        artifacts_root=args.artifacts_root,
        headless=False,
        scan_mode="full_scan",
        uploader=uploader,
    )

    # Monkey-patch: use our existing browser instead of launching a new one
    runner._pw = pw
    runner._context = context
    runner._page = page
    runner._browser = None
    runner.run_dir = Path(run_dir)
    runner.run_id = run_id

    screen_count = 0

    print("  Waiting for scan button clicks...", flush=True)
    sys.stdout.flush()

    try:
        while True:
            # Poll for scan request
            try:
                requested = page.evaluate("window.__a11y_scan_requested === true")
            except Exception:
                # Page might have navigated, re-inject
                _safe_inject(page)
                time.sleep(1)
                continue

            if requested:
                screen_count += 1
                label = f"scan-{screen_count:02d}"
                url = page.url

                # Reset flag and show status
                page.evaluate("window.__a11y_scan_requested = false")
                _safe_eval(page, f"window.__a11y_set_status('Scanning screen {screen_count}...')")

                print(f"  [SCAN {screen_count}] {url}", flush=True)

                try:
                    result = runner._analyze_current_screen(label, action_source="manual_button")
                    if result.get("captured"):
                        fails = sum(1 for r in result.get("wcag_results", []) if r.get("status") == "Fail")
                        passes = sum(1 for r in result.get("wcag_results", []) if r.get("status") == "Pass")
                        _safe_eval(page, f"window.__a11y_set_status('Screen {screen_count}: {fails} failures, {passes} passes')")
                        print(f"    Done: {fails} failures, {passes} passes", flush=True)
                    else:
                        _safe_eval(page, f"window.__a11y_set_status('Screen {screen_count}: skipped (duplicate)')")
                        print(f"    Skipped (duplicate)", flush=True)
                except Exception as e:
                    print(f"    Error: {e}", flush=True)
                    _safe_eval(page, f"window.__a11y_set_status('Error: check terminal')")

                # Re-inject button (analysis may have scrolled/navigated)
                _safe_inject(page)
                _safe_eval(page, "window.__a11y_scan_done()")

            time.sleep(0.5)

    except KeyboardInterrupt:
        print(f"\n\n  Stopping... Scanned {screen_count} screens.", flush=True)

    # Generate report
    if runner.screen_results:
        print("  Generating report...", flush=True)
        report = runner._build_report(run_id)

        from ..xlsx_report import generate_xlsx_report
        report_path = os.path.join(run_dir, "wcag_report.xlsx")
        generate_xlsx_report(report, report_path, uploader=uploader)
        print(f"\n  Report saved: {report_path}", flush=True)
        print(f"  Screens: {len(runner.screen_results)}")
        total_fail = sum(
            sum(1 for r in s.get("wcag_results", []) if r.get("status") == "Fail")
            for s in runner.screen_results
        )
        print(f"  Total failures: {total_fail}")

        # Upload artifacts to S3
        runner._upload_artifacts_to_s3(run_id)
    else:
        print("  No screens scanned. No report generated.")

    # Cleanup
    try:
        context.close()
        pw.stop()
    except Exception:
        pass
    if _chrome_proc:
        try:
            _chrome_proc.terminate()
            _chrome_proc.wait(timeout=5)
        except Exception:
            _chrome_proc.kill()

    print("\n  Done!")


def _safe_inject(page):
    """Safely inject the scan button, ignoring errors."""
    try:
        page.evaluate(INJECT_SCAN_BUTTON_JS)
    except Exception:
        pass


def _safe_eval(page, js: str):
    """Safely evaluate JS, ignoring errors."""
    try:
        page.evaluate(js)
    except Exception:
        pass


if __name__ == "__main__":
    main()
