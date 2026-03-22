"""Agentic flow runner: opens browser, walks through flows, analyzes every screen."""
from __future__ import annotations

import base64
import json
import logging
import os
import time
from pathlib import Path
from typing import Any

from openai import OpenAI

from ..models import PageArtifact
from .annotator import annotate_screenshot, find_violations_on_page
from .llm_router import LLMRouter
from .screen_analyzer import ScreenAnalyzer

logger = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# Expanded LLM system prompt for deeper exploration
EXPLORE_SYSTEM_ADDENDUM = """
IMPORTANT exploration strategy:
- You MUST explore at least 30 distinct screens/routes. Be thorough.
- After visiting a top-level nav item, explore its sub-tabs and sub-pages.
- Use "back" action to return to parent pages and explore sibling routes.
- Click on secondary navigation items (tabs, filters, dropdowns, cards).
- Look for modals, overlays, drawers — click triggers like settings icons, hamburger menus, profile icons.
- Explore footer links, help links, terms pages, account settings.
- If you see a list of items (e.g. stocks, orders), click into one to explore the detail page.
- After clicking deep, use "back" to return and explore other branches.
- You have a scroll action — use "scroll" to see more content below the fold before clicking.
- When you think you're done, double-check: have you visited Settings, Profile, Help, About, Notifications?
"""


class AgenticFlowRunner:
    """Drives a visible browser through multi-step flows with LLM-guided exploration."""

    def __init__(
        self,
        config_path: str,
        artifacts_root: str = "artifacts",
        headless: bool = False,
    ) -> None:
        self.config = json.loads(Path(config_path).read_text())
        self.artifacts_root = Path(artifacts_root)
        self.headless = headless
        self.router = LLMRouter()
        self.analyzer = ScreenAnalyzer()
        self._pw = None
        self._browser = None
        self._context = None
        self._page = None

        # State
        self.visited_urls: list[str] = []
        self.visited_url_set: set[str] = set()
        self.screen_results: list[dict[str, Any]] = []
        self.run_dir: Path | None = None
        self._nav_stack: list[str] = []  # for back navigation tracking

    # ------------------------------------------------------------------
    # Browser lifecycle
    # ------------------------------------------------------------------

    def _launch_browser(self):
        from playwright.sync_api import sync_playwright

        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(
            headless=self.headless,
            slow_mo=150,
        )
        self._context = self._browser.new_context(
            user_agent=USER_AGENT,
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
        self._context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
            Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
            window.chrome = { runtime: {} };
        """)
        self._page = self._context.new_page()

    def _safe_goto(self, url: str):
        try:
            self._page.goto(url, wait_until="domcontentloaded", timeout=30000)
            self._page.wait_for_load_state("load", timeout=10000)
        except Exception:
            pass

    def _close_browser(self):
        if self._context:
            self._context.close()
        if self._browser:
            self._browser.close()
        if self._pw:
            self._pw.stop()

    # ------------------------------------------------------------------
    # Page introspection
    # ------------------------------------------------------------------

    def _get_page_info(self) -> dict[str, Any]:
        page = self._page
        return page.evaluate("""() => {
            const info = {};
            info.url = location.href;
            info.title = document.title;
            info.html_lang = document.documentElement.lang || '';
            info.headings = Array.from(document.querySelectorAll('h1,h2,h3,h4,h5,h6')).map(h => ({
                level: h.tagName, text: (h.innerText || '').substring(0, 80).trim()
            }));
            info.interactive = Array.from(document.querySelectorAll(
                'a[href], button, input, select, textarea, [role="button"], [role="link"], [role="tab"], [role="menuitem"], [tabindex]:not([tabindex="-1"])'
            )).slice(0, 80).map((el, i) => {
                const rect = el.getBoundingClientRect();
                return {
                    index: i,
                    tag: el.tagName.toLowerCase(),
                    text: (el.innerText || '').substring(0, 80).trim(),
                    role: el.getAttribute('role') || '',
                    aria_label: el.getAttribute('aria-label') || '',
                    href: el.href || '',
                    type: el.type || '',
                    id: el.id || '',
                    class_name: (el.className || '').substring(0, 60),
                    visible: rect.width > 0 && rect.height > 0 && rect.top < window.innerHeight + 100,
                    disabled: el.disabled || false,
                    placeholder: el.placeholder || '',
                };
            });
            const imgs = Array.from(document.querySelectorAll('img'));
            info.images_total = imgs.length;
            info.images_missing_alt = imgs.filter(i => !i.hasAttribute('alt')).length;
            info.forms = document.querySelectorAll('form').length;
            info.inputs = document.querySelectorAll('input, select, textarea').length;
            info.landmarks = Array.from(document.querySelectorAll(
                'main, nav, header, footer, [role="main"], [role="navigation"], [role="banner"], [role="contentinfo"]'
            )).map(l => ({ tag: l.tagName.toLowerCase(), role: l.getAttribute('role') || '' }));
            const skip = Array.from(document.querySelectorAll('a[href^="#"]')).find(a =>
                (a.innerText || '').toLowerCase().includes('skip')
            );
            info.skip_link = skip ? skip.innerText.trim() : null;
            info.scroll_height = document.documentElement.scrollHeight;
            info.viewport_height = window.innerHeight;
            info.can_scroll = document.documentElement.scrollHeight > window.innerHeight + 200;
            return info;
        }""")

    def _capture_screenshot(self, label: str) -> str:
        safe = label.replace(" ", "_").replace("/", "_").replace(":", "_")[:50]
        idx = len(self.screen_results) + 1
        path = self.run_dir / f"{idx:02d}-{safe}.png"
        self._page.screenshot(path=str(path), full_page=True)
        return str(path)

    def _scroll_and_screenshot(self, label: str) -> list[str]:
        """Scroll page in viewport-sized chunks and screenshot each fold."""
        screenshots = []
        page = self._page
        scroll_height = page.evaluate("() => document.documentElement.scrollHeight")
        vh = page.evaluate("() => window.innerHeight")

        if scroll_height <= vh + 100:
            return screenshots

        # Scroll to top first
        page.evaluate("() => window.scrollTo(0, 0)")
        page.wait_for_timeout(300)

        position = vh  # already captured first fold
        fold = 2
        while position < scroll_height and fold <= 6:
            page.evaluate(f"() => window.scrollTo(0, {position})")
            page.wait_for_timeout(500)
            safe = f"{label}-fold{fold}".replace(" ", "_").replace("/", "_")[:50]
            idx = len(self.screen_results) + 1
            path = self.run_dir / f"{idx:02d}-{safe}.png"
            page.screenshot(path=str(path))  # viewport only
            screenshots.append(str(path))
            position += vh
            fold += 1

        # Scroll back to top
        page.evaluate("() => window.scrollTo(0, 0)")
        page.wait_for_timeout(300)
        return screenshots

    def _build_page_artifact(self, depth: int = 0) -> PageArtifact:
        page = self._page
        html = page.content()
        title = page.title()
        url = page.url
        links = page.eval_on_selector_all("a[href]", "els => els.map(el => el.href)")

        viewport_meta = page.query_selector("meta[name='viewport']")
        orientation_locked = False
        if viewport_meta:
            orientation_locked = viewport_meta.evaluate(
                "el => /user-scalable\\s*=\\s*no/.test(el.content || '')"
            )

        screenshot_path = self._capture_screenshot(f"wcag-{title or 'page'}")

        interactive_count = page.eval_on_selector_all(
            "a[href], button, input, select, textarea, [tabindex]",
            "els => els.length",
        )

        focus_trail = self._probe_keyboard(page)
        focus_visible_violations = self._probe_focus_visibility(page)
        aria_live = self._detect_live_regions(page)
        skip_link = self._probe_skip_link(page)

        render_metrics = {
            "orientation_locked": bool(orientation_locked),
            "screenshot_path": screenshot_path,
        }
        interaction_metrics = {
            "interactive_count": interactive_count,
            "focus_trail": focus_trail,
            "focus_trail_length": len(focus_trail),
            "keyboard_access_ok": len(focus_trail) > 0,
            "keyboard_trap_detected": (
                len(focus_trail) < max(1, interactive_count * 0.2)
                and interactive_count > 5
            ),
            "focus_visible_violations": focus_visible_violations,
            "focus_visible_ok": len(focus_visible_violations) == 0,
            "aria_live_region_count": len(aria_live),
            "aria_live_regions": aria_live,
            "skip_link_present": skip_link is not None,
            "skip_link_target": skip_link,
        }

        return PageArtifact(
            url=url, depth=depth, html=html, title=title,
            links=list(links), render_metrics=render_metrics,
            interaction_metrics=interaction_metrics, media_metadata={},
        )

    # ------------------------------------------------------------------
    # Browser probes
    # ------------------------------------------------------------------

    def _probe_keyboard(self, page) -> list[dict[str, Any]]:
        trail = []
        seen = set()
        body = page.query_selector("body")
        if body:
            try:
                body.click(force=True)
            except Exception:
                pass
        for _ in range(60):
            page.keyboard.press("Tab")
            page.wait_for_timeout(50)
            info = page.evaluate("""() => {
                const el = document.activeElement;
                if (!el || el === document.body) return null;
                const r = el.getBoundingClientRect();
                return {
                    tag: el.tagName.toLowerCase(),
                    role: el.getAttribute('role') || '',
                    aria_label: el.getAttribute('aria-label') || '',
                    text: (el.innerText || '').substring(0, 80).trim(),
                    id: el.id || '',
                    visible: r.width > 0 && r.height > 0,
                    selector: el.id ? '#'+el.id : el.tagName.toLowerCase(),
                };
            }""")
            if not info:
                break
            key = info.get("selector", "")
            if key in seen:
                break
            seen.add(key)
            trail.append(info)
        return trail

    def _probe_focus_visibility(self, page) -> list[dict[str, str]]:
        return page.evaluate("""() => {
            const els = Array.from(document.querySelectorAll(
                'a[href], button, input, select, textarea, [tabindex]:not([tabindex="-1"])'
            )).slice(0, 25);
            const violations = [];
            for (const el of els) {
                el.focus();
                const cs = getComputedStyle(el);
                const ok = (cs.outlineStyle !== 'none' && parseFloat(cs.outlineWidth) > 0) ||
                           (cs.boxShadow && cs.boxShadow !== 'none');
                if (!ok) violations.push({
                    tag: el.tagName.toLowerCase(),
                    id: el.id || '',
                    text: (el.innerText || '').substring(0, 50).trim(),
                });
            }
            document.activeElement?.blur?.();
            return violations;
        }""") or []

    def _detect_live_regions(self, page) -> list[dict[str, str]]:
        return page.evaluate("""() => {
            return Array.from(document.querySelectorAll(
                '[aria-live], [role="alert"], [role="status"], [role="log"]'
            )).slice(0, 15).map(el => ({
                tag: el.tagName.toLowerCase(),
                role: el.getAttribute('role') || '',
                aria_live: el.getAttribute('aria-live') || '',
                id: el.id || '',
            }));
        }""") or []

    def _probe_skip_link(self, page) -> str | None:
        return page.evaluate("""() => {
            const links = Array.from(document.querySelectorAll('a[href^="#"]')).slice(0, 10);
            for (const a of links) {
                const t = (a.innerText || '').toLowerCase();
                if (t.includes('skip') || t.includes('main content')) {
                    const target = a.getAttribute('href');
                    return (target && target.length > 1) ? target : null;
                }
            }
            return null;
        }""")

    # ------------------------------------------------------------------
    # Annotated screenshot generation
    # ------------------------------------------------------------------

    def _create_annotated_screenshot(self, screenshot_path: str) -> str | None:
        """Find violations on the live page and annotate the screenshot."""
        try:
            violations = find_violations_on_page(self._page)
            if not violations:
                return None
            out_path = screenshot_path.replace(".png", "-annotated.png")
            return annotate_screenshot(screenshot_path, violations, out_path)
        except Exception as e:
            logger.warning("Annotation failed: %s", e)
            return None

    # ------------------------------------------------------------------
    # Screen analysis
    # ------------------------------------------------------------------

    def _analyze_current_screen(self, label: str) -> dict[str, Any]:
        url = self._page.url
        screen_num = len(self.screen_results) + 1
        print(f"\n{'='*60}")
        print(f"  [{screen_num}] ANALYZING: {label}")
        print(f"  URL: {url}")
        print(f"{'='*60}")

        screenshot = self._capture_screenshot(label)

        # Create annotated screenshot with bounding boxes on violations
        annotated = self._create_annotated_screenshot(screenshot)
        if annotated:
            print(f"  Annotated screenshot: {Path(annotated).name}")

        page_info = self._get_page_info()
        print(f"  Title: {page_info.get('title', '?')}")
        print(f"  Headings: {len(page_info.get('headings', []))}")
        print(f"  Interactive: {len(page_info.get('interactive', []))}")
        print(f"  Images (missing alt): {page_info.get('images_missing_alt', 0)}/{page_info.get('images_total', 0)}")
        print(f"  Can scroll: {page_info.get('can_scroll', False)}")

        # Scroll and capture additional folds if page is long
        scroll_shots = []
        if page_info.get("can_scroll"):
            scroll_shots = self._scroll_and_screenshot(label)
            if scroll_shots:
                print(f"  Captured {len(scroll_shots)} scroll folds")
                # Annotate scroll fold screenshots too
                for ss in scroll_shots:
                    try:
                        sv = find_violations_on_page(self._page)
                        if sv:
                            annotate_screenshot(ss, sv)
                    except Exception:
                        pass

        # Build artifact and run WCAG buckets
        artifact = self._build_page_artifact()
        results = self.analyzer.analyze(artifact)
        summary = self.analyzer.summarize_findings(results)

        print(f"  WCAG: {summary['pass']}P / {summary['fail']}F / {summary['cannot_verify']}CV")
        if summary["failures"]:
            for f in summary["failures"][:5]:
                print(f"    FAIL [{f['checkpoint']}] {f['rationale'][:70]}")

        screen_data = {
            "label": label,
            "url": url,
            "screenshot": screenshot,
            "annotated_screenshot": annotated,
            "scroll_screenshots": scroll_shots,
            "page_info": page_info,
            "wcag_summary": summary,
            "wcag_results": [r.to_dict() for r in results],
        }
        self.screen_results.append(screen_data)

        if url not in self.visited_url_set:
            self.visited_urls.append(url)
            self.visited_url_set.add(url)

        return screen_data

    # ------------------------------------------------------------------
    # URL normalization for dedup
    # ------------------------------------------------------------------

    def _normalize_url(self, url: str) -> str:
        """Strip query params and fragments for dedup comparison."""
        return url.split("?")[0].split("#")[0].rstrip("/")

    def _is_url_visited(self, url: str) -> bool:
        norm = self._normalize_url(url)
        return any(self._normalize_url(v) == norm for v in self.visited_urls)

    # ------------------------------------------------------------------
    # Flow step execution
    # ------------------------------------------------------------------

    def _get_interactive_elements(self) -> list[dict[str, Any]]:
        elements = []
        try:
            els = self._page.query_selector_all(
                "a[href], button, input, select, textarea, [role='button'], [role='link'], [role='tab'], [tabindex]:not([tabindex='-1'])"
            )
            for el in els:
                if not el.is_visible() or el.is_disabled():
                    continue
                tag_name = el.evaluate("el => el.tagName.toLowerCase()")
                text = el.text_content() or ""
                role = el.get_attribute("role") or ""
                href = el.get_attribute("href") or ""
                type_attr = el.get_attribute("type") or ""
                aria_label = el.get_attribute("aria-label") or ""
                
                elements.append({
                    "index": len(elements),
                    "tag": tag_name,
                    "text": text.strip()[:100],
                    "role": role,
                    "href": href,
                    "type": type_attr,
                    "aria_label": aria_label,
                })
        except Exception as e:
            print(f"Error extracting interactive elements: {e}")
        return elements

    def _execute_fill(self, action: dict[str, Any]) -> None:
        selector = action.get("selector", "input")
        value = action["value"]
        print(f"  Filling '{selector}' with '{value}'")
        
        # Try to find exactly what the user asked for
        els = self._page.query_selector_all(selector)
        target = None
        for el in els:
            if el.is_visible():
                type_attr = el.get_attribute("type") or ""
                if type_attr.lower() not in ["radio", "checkbox", "hidden"]:
                    # Ensure it has a bounding box
                    box = el.bounding_box()
                    if box and box["width"] > 0 and box["height"] > 0:
                        target = el
                        break
        
        if target:
            try:
                target.click(force=True, timeout=3000)
            except Exception:
                pass  # if click fails, try filling anyway
            target.fill(value)
        else:
            print("  ⚠️ No suitable visible input found for filling.")

    def _execute_click(self, action: dict):
        selector = action.get("selector", "button")
        print(f"  Clicking '{selector}'")
        try:
            # Try normal click
            self._page.click(selector, timeout=5000)
        except Exception:
            try:
                # Try forced click on same selector
                self._page.click(selector, timeout=3000, force=True)
            except Exception:
                # Fallbacks
                desc = action.get("description", "")
                if "Get Started" in desc:
                    try:
                        self._page.click("text=Get Started", timeout=3000, force=True)
                    except Exception:
                        pass
                else:
                    buttons = self._page.query_selector_all("button:visible, [role='button']:visible")
                    if buttons:
                        try:
                            buttons[-1].click(timeout=3000, force=True)  # usually submit is last
                        except Exception:
                            try:
                                buttons[0].click(timeout=3000, force=True)
                            except Exception:
                                pass

    def _execute_otp(self, action: dict):
        otp = action["value"]
        print(f"  Entering OTP: {otp}")
        self._page.wait_for_timeout(1000)
        otp_inputs = self._page.query_selector_all("input[type='tel'], input[type='number'], input[type='text']")
        otp_boxes = [inp for inp in otp_inputs if self._page.evaluate(
            "(el) => { const r = el.getBoundingClientRect(); return r.width < 80 && r.width > 20; }", inp
        )]
        if len(otp_boxes) >= len(otp):
            for i, digit in enumerate(otp):
                otp_boxes[i].click()
                otp_boxes[i].fill(digit)
                self._page.wait_for_timeout(200)
        else:
            visible_inputs = self._page.query_selector_all("input:not([type='radio']):not([type='checkbox']):not([type='hidden']):visible")
            if visible_inputs:
                try:
                    visible_inputs[0].click(force=True, timeout=3000)
                except Exception:
                    pass
                for digit in otp:
                    self._page.keyboard.type(digit)
                    self._page.wait_for_timeout(100)
        self._page.wait_for_timeout(500)
        for text in ["Verify", "Submit", "Continue", "Confirm"]:
            btn = self._page.query_selector(f"button:has-text('{text}'), [role='button']:has-text('{text}')")
            if btn and btn.is_visible():
                print(f"  Clicking '{text}' button")
                btn.click()
                break

    def _execute_pin(self, action: dict):
        pin = action["value"]
        print(f"  Entering PIN: {pin}")
        self._page.wait_for_timeout(1000)
        pin_inputs = self._page.query_selector_all("input:visible")
        small_inputs = [inp for inp in pin_inputs if self._page.evaluate(
            "(el) => { const r = el.getBoundingClientRect(); return r.width < 80 && r.width > 15; }", inp
        )]
        if len(small_inputs) >= len(pin):
            for i, digit in enumerate(pin):
                small_inputs[i].click()
                small_inputs[i].fill(digit)
                self._page.wait_for_timeout(200)
        else:
            if pin_inputs:
                pin_inputs[0].click()
                for digit in pin:
                    self._page.keyboard.type(digit)
                    self._page.wait_for_timeout(150)
        self._page.wait_for_timeout(500)
        for text in ["Confirm", "Submit", "Continue", "Verify", "Login"]:
            btn = self._page.query_selector(f"button:has-text('{text}'), [role='button']:has-text('{text}')")
            if btn and btn.is_visible():
                print(f"  Clicking '{text}' button")
                btn.click()
                break

    def _execute_captcha(self, action: dict):
        """Solve a text/image captcha using the LLM vision endpoint."""
        img_sel = action.get(
            "captcha_image_selector",
            "img[id*='captcha'], img[alt*='captcha'], img[src*='captcha'], .captcha img, canvas[id*='captcha']",
        )
        inp_sel = action.get(
            "captcha_input_selector",
            "input[name*='captcha'], input[id*='captcha'], input[placeholder*='captcha']",
        )
        max_retries = action.get("max_retries", 3)

        base_url = os.getenv("LLM_GTWY_BASE_URL", "http://localhost:4000/v1")
        api_key = os.getenv("LLM_GTWY_API_KEY", "sk-placeholder")
        model = os.getenv("LLM_MODEL", "anthropic/claude-sonnet-4-6")
        client = OpenAI(base_url=base_url, api_key=api_key)

        for attempt in range(1, max_retries + 1):
            print(f"  Captcha attempt {attempt}/{max_retries}")
            self._page.wait_for_timeout(1500)

            # Try to find captcha image element
            captcha_el = None
            for sel in img_sel.split(","):
                sel = sel.strip()
                try:
                    self._page.wait_for_selector(sel, timeout=3000)
                except Exception:
                    pass
                captcha_el = self._page.query_selector(sel)
                if captcha_el:
                    break

            if not captcha_el:
                print("  ⚠️  Captcha image element not found, taking full page screenshot")
                captcha_path = str(self.run_dir / f"captcha-attempt-{attempt}.png")
                self._page.screenshot(path=captcha_path)
            else:
                captcha_path = str(self.run_dir / f"captcha-attempt-{attempt}.png")
                captcha_el.screenshot(path=captcha_path)

            # Read captcha image and encode to base64
            with open(captcha_path, "rb") as f:
                img_b64 = base64.b64encode(f.read()).decode("utf-8")

            # Ask LLM to read the captcha
            try:
                resp = client.chat.completions.create(
                    model=model,
                    messages=[
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "text",
                                    "text": (
                                        "Read the text characters shown in this captcha image. "
                                        "It is usually 4 to 6 alphanumeric characters. "
                                        "Reply with ONLY the exact characters you see, nothing else. "
                                        "Pay close attention to case sensitivity. "
                                        "No quotes, no explanation, just the characters."
                                    ),
                                },
                                {
                                    "type": "image_url",
                                    "image_url": {
                                        "url": f"data:image/png;base64,{img_b64}",
                                    },
                                },
                            ],
                        }
                    ],
                    temperature=0.0,
                    max_tokens=50,
                )
                captcha_text = resp.choices[0].message.content.strip() if resp.choices else ""
                
                # Sanitize chatty responses
                if '\n' in captcha_text:
                    lines = [line for line in captcha_text.split('\n') if line.strip()]
                    if lines:
                        captcha_text = lines[-1]
                captcha_text = captcha_text.replace('*', '').replace('`', '').replace('"', '').replace("'", "").strip()
                if ' ' in captcha_text:
                    captcha_text = captcha_text.split()[-1]

                print(f"  LLM read captcha: '{captcha_text}'")
            except Exception as e:
                print(f"  ⚠️  LLM captcha read failed: {e}")
                continue

            # Fill the captcha input
            captcha_input = None
            for sel in inp_sel.split(","):
                sel = sel.strip()
                captcha_input = self._page.query_selector(sel)
                if captcha_input:
                    break

            if not captcha_input:
                # Fallback: find any visible input near the captcha
                visible_inputs = self._page.query_selector_all("input:visible")
                if visible_inputs:
                    captcha_input = visible_inputs[-1]  # usually last input is captcha

            if captcha_input:
                try:
                    # Clear it first via JS to avoid interactions
                    captcha_input.evaluate("el => el.value = ''")
                    captcha_input.focus()
                    self._page.keyboard.type(captcha_text, delay=50)
                except Exception as e:
                    print(f"  ⚠️  Error typing captcha: {e}")
                
                print(f"  Filled captcha with: '{captcha_text}'")
            else:
                print("  ⚠️  Captcha input not found")
                continue

            # Brief wait to see if form auto-validates the captcha
            self._page.wait_for_timeout(1000)

            # Check for captcha error indicators
            error_el = self._page.query_selector(
                ".captcha-error, .error-captcha, [class*='captcha'][class*='error'], "
                "[class*='invalid'], .text-danger:visible"
            )
            if error_el and error_el.is_visible():
                print(f"  ❌ Captcha appears incorrect, retrying...")
                # Try to refresh captcha if there's a refresh button
                refresh = self._page.query_selector(
                    "[class*='captcha'] [class*='refresh'], "
                    "button[aria-label*='refresh'], img[alt*='refresh'], "
                    "[onclick*='captcha'], .captcha-refresh"
                )
                if refresh:
                    refresh.click()
                    self._page.wait_for_timeout(1500)
                continue
            else:
                print(f"  ✅ Captcha entered successfully")
                break

    # ------------------------------------------------------------------
    # LLM-guided deep exploration
    # ------------------------------------------------------------------

    def _execute_explore(self):
        """LLM-guided exploration of all reachable screens — aims for 30-50 pages."""
        max_screens = self.config.get("analysis", {}).get("max_screens", 50)
        max_steps = self.config.get("analysis", {}).get("max_explore_depth", 60)

        print(f"\n{'*'*60}")
        print(f"  LLM-GUIDED DEEP EXPLORATION (target: {max_screens} screens)")
        print(f"{'*'*60}")

        # Inject deeper exploration instructions into the router
        self.router._history = []  # fresh context for exploration
        self.router._history.append({
            "role": "system",
            "content": EXPLORE_SYSTEM_ADDENDUM,
        })

        consecutive_failures = 0
        for step in range(max_steps):
            if len(self.screen_results) >= max_screens:
                print(f"\n  Reached target screen limit ({max_screens})")
                break

            if consecutive_failures >= 5:
                print(f"\n  Too many consecutive failures, moving on")
                break

            self._page.wait_for_timeout(1200)
            current_url = self._page.url

            page_info = self._get_page_info()
            elements = page_info.get("interactive", [])
            visible_elements = [e for e in elements if e.get("visible") and not e.get("disabled")]

            page_desc = (
                f"Title: {page_info.get('title', '')}, "
                f"URL: {current_url}, "
                f"{len(page_info.get('headings', []))} headings, "
                f"{len(visible_elements)} visible interactive elements, "
                f"{page_info.get('images_total', 0)} images, "
                f"can_scroll: {page_info.get('can_scroll', False)}, "
                f"screens_so_far: {len(self.screen_results)}/{max_screens}"
            )

            decision = self.router.decide_next_action(
                page_description=page_desc,
                interactive_elements=visible_elements,
                visited_urls=self.visited_urls,
                current_url=current_url,
            )

            action = decision.get("action", "done")
            reason = decision.get("reason", "")
            print(f"\n  [{step+1}/{max_steps}] LLM: {action} — {reason[:60]}")

            if action == "done":
                # Double-check: if we haven't reached 30, push the LLM to keep going
                if len(self.screen_results) < 30:
                    print(f"  Only {len(self.screen_results)} screens — pushing LLM to continue")
                    self.router._history.append({
                        "role": "user",
                        "content": f"NOT DONE YET! Only {len(self.screen_results)} screens covered. "
                                   f"Need at least 30. Go back and explore sub-pages, tabs, settings, profile, etc."
                    })
                    continue
                print("  Exploration complete.")
                break

            elif action == "scroll":
                print("  Scrolling page...")
                self._page.evaluate("() => window.scrollBy(0, window.innerHeight * 0.8)")
                self._page.wait_for_timeout(800)
                consecutive_failures = 0

            elif action == "click":
                idx = decision.get("index", 0)
                if 0 <= idx < len(visible_elements):
                    el_info = visible_elements[idx]
                    el_text = el_info.get("text", "") or el_info.get("aria_label", "") or el_info.get("id", "click")
                    print(f"  Clicking [{idx}]: <{el_info['tag']}> {el_text[:40]}")
                    try:
                        self._nav_stack.append(current_url)
                        all_els = self._page.query_selector_all(
                            "a[href], button, input, select, textarea, "
                            "[role='button'], [role='link'], [role='tab'], "
                            "[role='menuitem'], [tabindex]:not([tabindex='-1'])"
                        )
                        visible_idx = 0
                        clicked = False
                        for el in all_els:
                            try:
                                if el.is_visible() and not el.is_disabled():
                                    if visible_idx == idx:
                                        el.click(timeout=5000)
                                        clicked = True
                                        break
                                    visible_idx += 1
                            except Exception:
                                visible_idx += 1
                                continue
                        if not clicked:
                            consecutive_failures += 1
                            continue

                        self._page.wait_for_timeout(2000)
                        self._analyze_current_screen(
                            f"explore-{step+1}-{el_text[:20]}"
                        )
                        consecutive_failures = 0
                    except Exception as e:
                        print(f"  Click failed: {e}")
                        consecutive_failures += 1
                else:
                    consecutive_failures += 1

            elif action == "navigate":
                url = decision.get("url", "")
                if url and not self._is_url_visited(url):
                    print(f"  Navigating: {url[:70]}")
                    try:
                        self._nav_stack.append(current_url)
                        self._safe_goto(url)
                        self._page.wait_for_timeout(2000)
                        self._analyze_current_screen(f"explore-{step+1}-nav")
                        consecutive_failures = 0
                    except Exception as e:
                        print(f"  Navigation failed: {e}")
                        consecutive_failures += 1
                else:
                    consecutive_failures += 1

            elif action == "back":
                print("  Going back")
                try:
                    if self._nav_stack:
                        back_url = self._nav_stack.pop()
                        self._safe_goto(back_url)
                    else:
                        self._page.go_back(wait_until="domcontentloaded", timeout=15000)
                    self._page.wait_for_timeout(1500)
                    consecutive_failures = 0
                except Exception:
                    consecutive_failures += 1

            elif action == "fill":
                idx = decision.get("index", 0)
                value = decision.get("value", "test")
                try:
                    all_els = self._page.query_selector_all(
                        "a[href], button, input, select, textarea, "
                        "[role='button'], [role='link'], [role='tab'], "
                        "[tabindex]:not([tabindex='-1'])"
                    )
                    visible_idx = 0
                    for el in all_els:
                        try:
                            if el.is_visible() and not el.is_disabled():
                                if visible_idx == idx:
                                    el.fill(value)
                                    break
                                visible_idx += 1
                        except Exception:
                            visible_idx += 1
                    consecutive_failures = 0
                except Exception as e:
                    print(f"  Fill failed: {e}")
                    consecutive_failures += 1
            else:
                consecutive_failures += 1

    # ------------------------------------------------------------------
    # Main run
    # ------------------------------------------------------------------

    def run(self) -> dict[str, Any]:
        import uuid

        run_id = str(uuid.uuid4())[:8]
        self.run_dir = self.artifacts_root / f"agentic-{run_id}"
        self.run_dir.mkdir(parents=True, exist_ok=True)

        print(f"\n{'#'*60}")
        print(f"  AGENTIC ACCESSIBILITY SCANNER")
        print(f"  Config: {self.config['name']}")
        print(f"  Target: 30-50 screens, annotated screenshots")
        print(f"  Artifacts: {self.run_dir}")
        print(f"{'#'*60}")

        self._launch_browser()

        try:
            start_url = self.config["start_url"]
            print(f"\n  Navigating to {start_url}")
            self._safe_goto(start_url)
            self._page.wait_for_timeout(2000)

            self._analyze_current_screen("01-initial-load")

            for step in self.config.get("flow_steps", []):
                step_id = step.get("id", "unknown")
                desc = step.get("description", step_id)
                print(f"\n{'─'*60}")
                print(f"  STEP: {desc}")
                print(f"{'─'*60}")

                planned_actions = step.get("actions", [])
                
                # Use LLM to check which actions are actually required
                interactive_els = self._get_interactive_elements()
                page_info = {
                    "url": self._page.url,
                    "title": self._page.title(),
                }
                
                # Check LLM routing flag — if it's explore type, skip filtering
                if planned_actions and planned_actions[0].get("type") == "explore":
                    required_indices = [0]
                else:
                    required_indices = self.router.filter_step_actions(page_info, interactive_els, planned_actions)
                
                for i, action in enumerate(planned_actions):
                    if i not in required_indices:
                        print(f"  ⏭️  Skipping action: '{action.get('description', action['type'])}' (LLM determined not required)")
                        continue
                        
                    action_type = action["type"]
                    if action_type == "fill":
                        self._execute_fill(action)
                    elif action_type == "click":
                        self._execute_click(action)
                    elif action_type == "otp":
                        self._execute_otp(action)
                    elif action_type == "pin":
                        self._execute_pin(action)
                    elif action_type == "captcha":
                        self._execute_captcha(action)
                    elif action_type == "explore":
                        self._execute_explore()
                        continue

                wait = step.get("wait_after", 2000)
                self._page.wait_for_timeout(wait)
                self._analyze_current_screen(f"{step_id}")

            report = self._build_report(run_id)

            report_path = self.run_dir / "agentic-report.json"
            with open(report_path, "w") as f:
                json.dump(report, f, indent=2, default=str)
            print(f"\n  Full report saved: {report_path}")

            # Generate XLSX report
            try:
                from ..xlsx_report import generate_xlsx_report
                xlsx_path = self.run_dir / "wcag_report.xlsx"
                generate_xlsx_report(report, str(xlsx_path))
                print(f"  XLSX report saved: {xlsx_path}")
            except Exception as e:
                print(f"  ⚠️  XLSX generation failed: {e}")

            self._print_summary(report)

            print(f"\n  Browser stays open for 10 seconds for inspection...")
            time.sleep(10)

        finally:
            self._close_browser()

        return report

    def _build_report(self, run_id: str) -> dict[str, Any]:
        all_failures = []
        total_pass = 0
        total_fail = 0
        total_cv = 0

        for screen in self.screen_results:
            s = screen.get("wcag_summary", {})
            total_pass += s.get("pass", 0)
            total_fail += s.get("fail", 0)
            total_cv += s.get("cannot_verify", 0)
            for f in s.get("failures", []):
                f["screen"] = screen["label"]
                all_failures.append(f)

        return {
            "run_id": run_id,
            "config": self.config["name"],
            "screens_analyzed": len(self.screen_results),
            "urls_visited": self.visited_urls,
            "totals": {
                "pass": total_pass,
                "fail": total_fail,
                "cannot_verify": total_cv,
            },
            "all_failures": all_failures,
            "screens": [
                {
                    "label": s["label"],
                    "url": s["url"],
                    "screenshot": s["screenshot"],
                    "annotated_screenshot": s.get("annotated_screenshot"),
                    "scroll_screenshots": s.get("scroll_screenshots", []),
                    "wcag_summary": s["wcag_summary"],
                }
                for s in self.screen_results
            ],
        }

    def _print_summary(self, report: dict[str, Any]):
        print(f"\n{'='*60}")
        print(f"  FINAL SUMMARY")
        print(f"{'='*60}")
        print(f"  Screens analyzed:   {report['screens_analyzed']}")
        print(f"  URLs visited:       {len(report['urls_visited'])}")
        print(f"  Total PASS:         {report['totals']['pass']}")
        print(f"  Total FAIL:         {report['totals']['fail']}")
        print(f"  Total CANNOT_VERIFY:{report['totals']['cannot_verify']}")
        print(f"  Unique failures:    {len(report['all_failures'])}")

        annotated_count = sum(
            1 for s in report["screens"]
            if s.get("annotated_screenshot")
        )
        print(f"  Annotated screenshots: {annotated_count}")

        if report["all_failures"]:
            print(f"\n  TOP FAILURES BY CHECKPOINT:")
            by_cp: dict[str, int] = {}
            for f in report["all_failures"]:
                cp = f["checkpoint"]
                by_cp[cp] = by_cp.get(cp, 0) + 1
            for cp, count in sorted(by_cp.items(), key=lambda x: -x[1]):
                print(f"    [{cp}] x{count}")
