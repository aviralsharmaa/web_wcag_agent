from __future__ import annotations

import importlib.util
import logging
from pathlib import Path
from typing import Any

from ..models import PageArtifact, ScanRequest
from .base import BaseFetcher

logger = logging.getLogger(__name__)

# Realistic Chrome user-agent to avoid WAF bot detection
_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


class PlaywrightFetcher(BaseFetcher):
    """Agentic Playwright fetcher: renders pages and actively probes accessibility."""

    def __init__(self, artifacts_root: str = "artifacts") -> None:
        self.artifacts_root = Path(artifacts_root)
        self._playwright = None
        self._browser = None
        self._context = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def setup(self, request: ScanRequest) -> dict[str, Any] | None:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise RuntimeError(
                "playwright is required for PlaywrightFetcher. "
                "Install dependencies and run `playwright install`."
            ) from exc

        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch(headless=True)
        self._context = self._browser.new_context(
            user_agent=_USER_AGENT,
            viewport={"width": 1440, "height": 900},
            locale="en-US",
            timezone_id="Asia/Kolkata",
            ignore_https_errors=True,
            extra_http_headers={
                "Accept-Language": "en-US,en;q=0.9",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
                "Sec-CH-UA": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
                "Sec-CH-UA-Mobile": "?0",
                "Sec-CH-UA-Platform": '"macOS"',
            },
        )
        # Remove navigator.webdriver flag that WAFs check
        self._context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            Object.defineProperty(navigator, 'plugins', {
                get: () => [1, 2, 3, 4, 5],
            });
            Object.defineProperty(navigator, 'languages', {
                get: () => ['en-US', 'en'],
            });
            window.chrome = { runtime: {} };
        """)

        if request.auth_script_ref:
            self._run_auth_script(request.auth_script_ref, request)

        return {"browser": "chromium", "authenticated": bool(request.auth_script_ref)}

    def teardown(self) -> None:
        if self._context:
            self._context.close()
        if self._browser:
            self._browser.close()
        if self._playwright:
            self._playwright.stop()
        self._context = None
        self._browser = None
        self._playwright = None

    # ------------------------------------------------------------------
    # Core fetch + agentic interaction
    # ------------------------------------------------------------------

    def fetch_page(
        self,
        url: str,
        depth: int,
        request: ScanRequest,
        run_id: str,
    ) -> PageArtifact:
        if self._context is None:
            raise RuntimeError("PlaywrightFetcher.setup must be called before fetch_page")

        page = self._context.new_page()
        try:
            page.goto(url, wait_until="networkidle", timeout=45000)
        except Exception:
            # Fallback: some pages never reach networkidle (streaming, long-poll)
            logger.warning("networkidle timeout for %s, falling back to domcontentloaded", url)
            page.goto(url, wait_until="domcontentloaded", timeout=30000)

        # Let any post-load JS settle
        page.wait_for_timeout(1000)

        html = page.content()
        title = page.title()
        links = page.eval_on_selector_all("a[href]", "els => els.map(el => el.href)")

        # Screenshots
        run_dir = self.artifacts_root / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        screenshot_path = run_dir / f"screenshot-{self._safe_name(url)}.png"
        page.screenshot(path=str(screenshot_path), full_page=True)

        # -- Viewport / orientation check (safe if meta missing) --
        viewport_meta = page.query_selector("meta[name='viewport']")
        orientation_locked = False
        if viewport_meta:
            orientation_locked = viewport_meta.evaluate(
                "el => /user-scalable\\s*=\\s*no/.test(el.content || '')"
            )

        render_metrics: dict[str, Any] = {
            "orientation_locked": bool(orientation_locked),
            "screenshot_path": str(screenshot_path),
        }

        # -- Count interactive elements --
        interactive_count = page.eval_on_selector_all(
            "a[href], button, input, select, textarea, [tabindex]",
            "els => els.length",
        )

        # ==============================================================
        # AGENTIC PHASE: real browser interaction probes
        # ==============================================================
        focus_trail = self._probe_keyboard_navigation(page)
        focus_visible_results = self._probe_focus_visibility(page)
        aria_live_regions = self._detect_live_regions(page)
        skip_link = self._probe_skip_link(page)

        interaction_metrics: dict[str, Any] = {
            "interactive_count": interactive_count,
            # Real tab-order from the browser (not heuristic)
            "focus_trail": focus_trail,
            "focus_trail_length": len(focus_trail),
            "keyboard_access_ok": len(focus_trail) > 0,
            "keyboard_trap_detected": self._detect_trap(focus_trail, interactive_count),
            "focus_visible_violations": focus_visible_results,
            "focus_visible_ok": len(focus_visible_results) == 0,
            "aria_live_region_count": len(aria_live_regions),
            "aria_live_regions": aria_live_regions,
            "skip_link_present": skip_link is not None,
            "skip_link_target": skip_link,
        }

        page.close()
        return PageArtifact(
            url=url,
            depth=depth,
            html=html,
            title=title,
            links=list(links),
            render_metrics=render_metrics,
            interaction_metrics=interaction_metrics,
            media_metadata={},
            screenshot_evidence_id=None,
        )

    # ------------------------------------------------------------------
    # Agentic probes
    # ------------------------------------------------------------------

    def _probe_keyboard_navigation(self, page) -> list[dict[str, Any]]:
        """Tab through the page and record each focused element."""
        trail: list[dict[str, Any]] = []
        seen_selectors: set[str] = set()
        max_tabs = 80  # safety cap

        # Click body first to ensure focus starts from the top
        body = page.query_selector("body")
        if body:
            try:
                body.click(force=True)
            except Exception:
                pass

        for _ in range(max_tabs):
            page.keyboard.press("Tab")
            page.wait_for_timeout(80)

            info = page.evaluate("""() => {
                const el = document.activeElement;
                if (!el || el === document.body) return null;
                const rect = el.getBoundingClientRect();
                return {
                    tag: el.tagName.toLowerCase(),
                    role: el.getAttribute('role') || '',
                    aria_label: el.getAttribute('aria-label') || '',
                    text: (el.innerText || '').substring(0, 100).trim(),
                    id: el.id || '',
                    class_name: el.className || '',
                    href: el.href || '',
                    type: el.type || '',
                    tabindex: el.getAttribute('tabindex'),
                    selector: _buildSelector(el),
                    visible: rect.width > 0 && rect.height > 0,
                    in_viewport: rect.top >= 0 && rect.top < window.innerHeight,
                };

                function _buildSelector(e) {
                    if (e.id) return '#' + e.id;
                    let s = e.tagName.toLowerCase();
                    if (e.className && typeof e.className === 'string')
                        s += '.' + e.className.trim().split(/\\s+/).join('.');
                    return s;
                }
            }""")

            if info is None:
                break

            selector = info.get("selector", "")
            if selector in seen_selectors:
                # Looped back to the start — navigation cycle complete
                break
            seen_selectors.add(selector)
            trail.append(info)

        return trail

    def _probe_focus_visibility(self, page) -> list[dict[str, str]]:
        """Check if focused elements have a visible focus indicator."""
        violations: list[dict[str, str]] = []

        # Evaluate focus-visible on all focusable elements (sample up to 30)
        results = page.evaluate("""() => {
            const focusable = Array.from(document.querySelectorAll(
                'a[href], button, input, select, textarea, [tabindex]:not([tabindex="-1"])'
            )).slice(0, 30);
            const violations = [];
            for (const el of focusable) {
                el.focus();
                const cs = getComputedStyle(el);
                const outline = cs.outlineStyle;
                const outlineW = parseFloat(cs.outlineWidth) || 0;
                const boxShadow = cs.boxShadow;
                const hasFocusIndicator = (
                    (outline !== 'none' && outlineW > 0) ||
                    (boxShadow && boxShadow !== 'none')
                );
                if (!hasFocusIndicator) {
                    violations.push({
                        tag: el.tagName.toLowerCase(),
                        id: el.id || '',
                        text: (el.innerText || '').substring(0, 60).trim(),
                    });
                }
            }
            document.activeElement?.blur?.();
            return violations;
        }""")

        return results or []

    def _detect_live_regions(self, page) -> list[dict[str, str]]:
        """Find ARIA live regions on the page."""
        return page.evaluate("""() => {
            const regions = document.querySelectorAll('[aria-live], [role="alert"], [role="status"], [role="log"]');
            return Array.from(regions).slice(0, 20).map(el => ({
                tag: el.tagName.toLowerCase(),
                role: el.getAttribute('role') || '',
                aria_live: el.getAttribute('aria-live') || '',
                id: el.id || '',
                text: (el.innerText || '').substring(0, 100).trim(),
            }));
        }""")

    def _probe_skip_link(self, page) -> str | None:
        """Check if a skip-to-content link exists and works."""
        result = page.evaluate("""() => {
            const links = Array.from(document.querySelectorAll('a[href^="#"]')).slice(0, 10);
            for (const a of links) {
                const text = (a.innerText || a.textContent || '').toLowerCase().trim();
                if (text.includes('skip') || text.includes('main content') || text.includes('jump to')) {
                    const target = a.getAttribute('href');
                    if (target && target.length > 1) {
                        const dest = document.querySelector(target);
                        return dest ? target : null;
                    }
                }
            }
            return null;
        }""")
        return result

    def _detect_trap(self, focus_trail: list[dict], interactive_count: int) -> bool:
        """Heuristic: if we could only tab to < 20% of interactive elements, likely a trap."""
        if interactive_count == 0:
            return False
        if len(focus_trail) == 0 and interactive_count > 3:
            return True
        ratio = len(focus_trail) / max(interactive_count, 1)
        return ratio < 0.2 and interactive_count > 5

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _safe_name(self, url: str) -> str:
        return (
            url.replace("https://", "")
            .replace("http://", "")
            .replace("/", "_")
            .replace("?", "_")
            .replace("&", "_")
        )

    def _run_auth_script(self, script_path: str, request: ScanRequest) -> None:
        path = Path(script_path)
        if not path.exists():
            raise FileNotFoundError(f"Auth script not found: {script_path}")
        spec = importlib.util.spec_from_file_location("auth_script", str(path))
        if spec is None or spec.loader is None:
            raise RuntimeError(f"Unable to import auth script: {script_path}")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        login_fn = getattr(mod, "login", None)
        if not callable(login_fn):
            raise RuntimeError("Auth script must define callable `login(context, request)`")
        login_fn(self._context, request)
