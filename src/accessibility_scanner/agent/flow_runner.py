"""Agentic flow runner: opens browser, walks through flows, analyzes every screen."""
from __future__ import annotations

import base64
from datetime import datetime
import hashlib
import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any

from openai import OpenAI

from ..checklist_registry import load_checklist_spec_map, load_checklist_specs
from ..checklist_reports import generate_checklist_reports
from ..models import PageArtifact
from .annotator import (
    annotate_screenshot,
    severity_for_checkpoint,
    select_annotation_target,
    select_representative_failure,
)
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

WCAG_STANDARD_LABEL = "WCAG A/AA (Expanded)"


class AgenticFlowRunner:
    """Drives a visible browser through multi-step flows with LLM-guided exploration."""

    def __init__(
        self,
        config_path: str,
        artifacts_root: str = "artifacts",
        headless: bool = False,
        scan_mode: str = "full_scan",
    ) -> None:
        self.config = json.loads(Path(config_path).read_text())
        self.artifacts_root = Path(artifacts_root)
        self.headless = headless
        self.scan_mode = (scan_mode or "full_scan").strip().lower()
        self.router = LLMRouter()
        analysis_cfg = self.config.get("analysis", {})
        self.analyzer = ScreenAnalyzer(
            cannot_verify_policy=analysis_cfg.get("cannot_verify_policy", "pass_leaning"),
            cannot_verify_threshold=analysis_cfg.get("cannot_verify_threshold", 31),
            cannot_verify_enforcement=analysis_cfg.get("cannot_verify_enforcement", "both"),
        )
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
        self.unique_screen_keys: set[str] = set()
        self.action_trace: list[dict[str, Any]] = []
        self.route_log: list[dict[str, Any]] = []
        self.observed_urls: list[str] = []
        self.observed_url_set: set[str] = set()
        self._fallback_clicked_keys: set[str] = set()
        self._fallback_scroll_counts: dict[str, int] = {}
        self.validation_profile = self._extract_validation_profile()
        self.checklist_specs = load_checklist_specs()
        self.checklist_spec_map = load_checklist_spec_map()
        self._otp_fill_cursor = 0
        self._pin_fill_cursor = 0

    def _artifact_asset_id(self) -> str:
        explicit = str(self.config.get("app_id") or "").strip()
        if explicit:
            return explicit.upper()
        derived = re.sub(r"[^A-Za-z0-9]+", "", str(self.config.get("name") or "").upper())
        return derived or "SCAN"

    def _next_run_dir_name(self, now: datetime | None = None) -> str:
        current = now or datetime.now()
        asset_id = self._artifact_asset_id()
        date_label = f"{current.day}-{current.month}-{current.year % 100:02d}"
        prefix = f"{asset_id}-{date_label}-"

        next_seq = 1
        if self.artifacts_root.exists():
            for child in self.artifacts_root.iterdir():
                if not child.is_dir():
                    continue
                name = child.name
                if not name.startswith(prefix):
                    continue
                suffix = name[len(prefix):]
                if suffix.isdigit():
                    next_seq = max(next_seq, int(suffix) + 1)
        return f"{prefix}{next_seq}"

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

    def _extract_validation_profile(self) -> dict[str, str]:
        profile: dict[str, str] = {}
        for step in self.config.get("flow_steps", []):
            for action in step.get("actions", []):
                action_type = (action.get("type") or "").strip().lower()
                value = str(action.get("value", "") or "").strip()
                if not value:
                    continue
                if action_type == "fill" and "login_id" not in profile:
                    profile["login_id"] = value
                elif action_type == "otp" and "otp" not in profile:
                    profile["otp"] = value
                elif action_type == "pin" and "pin" not in profile:
                    profile["pin"] = value

        # Safe defaults if config omitted one of the values.
        profile.setdefault("login_id", "JAI")
        profile.setdefault("otp", "7890")
        profile.setdefault("pin", "1234")
        return profile

    def _flow_contains_login(self) -> bool:
        login_actions = {"fill", "otp", "pin", "captcha", "manual"}
        for step in self.config.get("flow_steps", []):
            for action in step.get("actions", []):
                if (action.get("type") or "").strip().lower() in login_actions:
                    return True
        return False

    def _step_scope(self, step: dict[str, Any]) -> str:
        explicit_scope = (
            step.get("scan_scope")
            or step.get("scope")
            or step.get("mode")
            or step.get("phase")
        )
        if explicit_scope:
            scope = str(explicit_scope).strip().lower().replace("-", "_")
            if scope in {"pre_login", "post_login", "all"}:
                return scope

        if not self._flow_contains_login():
            return "all"

        step_blob = " ".join(
            filter(
                None,
                [
                    str(step.get("id") or ""),
                    str(step.get("description") or ""),
                    " ".join(str(action.get("description") or "") for action in step.get("actions", [])),
                ],
            )
        ).lower()
        action_types = {(action.get("type") or "").strip().lower() for action in step.get("actions", [])}

        if (
            "post_login" in step_blob
            or "post login" in step_blob
            or "after login" in step_blob
            or "dashboard" in step_blob
            or "client-dashboard" in step_blob
            or (action_types == {"explore"} and any(token in step_blob for token in ("explore", "route", "reachable")))
        ):
            return "post_login"
        return "pre_login"

    def _should_run_step(self, step: dict[str, Any]) -> bool:
        if self.scan_mode == "full_scan":
            return True
        return self._step_scope(step) != "post_login"

    def _infer_issue_type(self, checkpoint_id: str, rationale: str) -> str:
        title = self.checklist_spec_map.get(checkpoint_id).sc_title if checkpoint_id in self.checklist_spec_map else checkpoint_id
        base = re.sub(r"[^a-z0-9]+", "_", (title or checkpoint_id or "issue").strip().lower()).strip("_")
        return base or "accessibility_issue"

    def _build_screen_checklist_evaluations(
        self,
        label: str,
        url: str,
        page_info: dict[str, Any],
        screenshot: str,
        artifact: PageArtifact,
        results: list[Any],
    ) -> list[dict[str, Any]]:
        result_by_id = {item.checkpoint_id: item for item in results}
        evaluations: list[dict[str, Any]] = []

        for spec in self.checklist_specs:
            result = result_by_id.get(spec.sc_id)
            if result is None:
                continue

            issues: list[dict[str, Any]] = []
            if result.status.value == "Fail":
                target = select_annotation_target(self._page, spec.sc_id, result.rationale or "")
                issue_payload = {
                    "issue_type": self._infer_issue_type(spec.sc_id, result.rationale or ""),
                    "severity": severity_for_checkpoint(spec.sc_id),
                    "detail": result.rationale or f"{spec.sc_title} failed automated validation.",
                    "bounds": (target or {}).get("bbox"),
                    "xml_line": None,
                    "selector": (target or {}).get("selector") or result.selector_or_target,
                    "tag": (target or {}).get("tag"),
                    "element": (target or {}).get("element"),
                    "wcag_criteria": [spec.sc_id],
                    "source": "wcag_result",
                    "page_url": url,
                    "screen_label": label,
                }
                issues.append(issue_payload)

            evidence = {
                "screenshot": screenshot,
                "dom_dump": None,
                "page_info_dump": None,
                "wcag_results_dump": None,
                "required_evidence_for_llm": spec.required_evidence_for_llm,
                "render_metrics": {
                    "title": page_info.get("title"),
                    "headings": len(page_info.get("headings", []) or []),
                    "interactive_count": len(page_info.get("interactive", []) or []),
                    "images_total": page_info.get("images_total"),
                    "images_missing_alt": page_info.get("images_missing_alt"),
                },
                "contrast_evidence": self._extract_contrast_evidence(artifact),
            }
            evaluations.append(
                {
                    **spec.to_dict(),
                    "screen_label": label,
                    "page_url": url,
                    "page_title": page_info.get("title", ""),
                    "status": result.status.value,
                    "rationale": result.rationale,
                    "manual_required": bool(result.manual_required),
                    "machine_pass": result.status.value == "Pass",
                    "selector_or_target": result.selector_or_target,
                    "issues": issues,
                    "evidence": evidence,
                    "llm_validation_payload": {
                        **spec.to_dict(),
                        "screen_label": label,
                        "page_url": url,
                        "page_title": page_info.get("title", ""),
                        "machine_result": {
                            "status": result.status.value,
                            "rationale": result.rationale,
                            "manual_required": bool(result.manual_required),
                            "selector_or_target": result.selector_or_target,
                        },
                        "issues": issues,
                        "evidence": evidence,
                    },
                }
            )

        return evaluations

    def _is_page_alive(self) -> bool:
        try:
            return self._page is not None and not self._page.is_closed()
        except Exception:
            return False

    def _recover_closed_page(self, fallback_url: str | None = None) -> bool:
        target_url = fallback_url or (self.visited_urls[-1] if self.visited_urls else self.config.get("start_url", ""))
        print(f"  ⚠️  Page/session closed unexpectedly. Attempting recovery at: {target_url}")
        try:
            if self._context:
                self._page = self._context.new_page()
            else:
                self._launch_browser()
            if target_url:
                self._safe_goto(target_url)
            if self._page:
                self._page.wait_for_timeout(1200)
            return True
        except Exception as e:
            print(f"  ⚠️  Recovery failed: {e}")
            return False

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

    def _scroll_and_screenshot(self, label: str) -> list[dict[str, Any]]:
        """Scroll page in viewport-sized chunks and screenshot each fold."""
        screenshots: list[dict[str, Any]] = []
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
            screenshots.append({"path": str(path), "scroll_y": int(position)})
            position += vh
            fold += 1

        # Scroll back to top
        page.evaluate("() => window.scrollTo(0, 0)")
        page.wait_for_timeout(300)
        return screenshots

    def _build_page_artifact(self, depth: int = 0, screenshot_path: str | None = None) -> PageArtifact:
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

        resolved_screenshot_path = screenshot_path or self._capture_screenshot(f"wcag-{title or 'page'}")

        interactive_count = page.eval_on_selector_all(
            "a[href], button, input, select, textarea, [tabindex]",
            "els => els.length",
        )

        focus_trail = self._probe_keyboard(page)
        focus_visible_violations = self._probe_focus_visibility(page)
        aria_live = self._detect_live_regions(page)
        skip_link = self._probe_skip_link(page)
        contrast_samples = self._probe_contrast_samples(page)

        render_metrics = {
            "orientation_locked": bool(orientation_locked),
            "screenshot_path": resolved_screenshot_path,
            "computed_contrast_samples": contrast_samples,
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

    def _probe_contrast_samples(self, page) -> dict[str, list[dict[str, Any]]]:
        """Collect rendered, computed-style samples for text and non-text contrast."""
        return page.evaluate("""() => {
            const toSelector = (el) => {
                if (!el) return '';
                if (el.id) return `#${el.id}`;
                const cls = (typeof el.className === 'string' ? el.className.trim() : '')
                    .split(/\\s+/).filter(Boolean).slice(0, 2).join('.');
                return cls ? `${el.tagName.toLowerCase()}.${cls}` : el.tagName.toLowerCase();
            };
            const isVisible = (el) => {
                if (!el) return false;
                const r = el.getBoundingClientRect();
                return r.width > 1 && r.height > 1;
            };
            const parseWeight = (raw) => {
                const txt = `${raw || ''}`.trim().toLowerCase();
                if (txt === 'bold') return 700;
                if (txt === 'normal') return 400;
                const v = parseInt(txt, 10);
                return Number.isNaN(v) ? 400 : v;
            };
            const effectiveBackground = (el) => {
                let cur = el;
                while (cur) {
                    const bg = getComputedStyle(cur).backgroundColor;
                    if (bg && bg !== 'rgba(0, 0, 0, 0)' && bg !== 'transparent') return bg;
                    cur = cur.parentElement;
                }
                return 'rgb(255, 255, 255)';
            };

            const text = [];
            const textNodes = Array.from(document.querySelectorAll(
                'p,span,a,button,label,li,td,th,h1,h2,h3,h4,h5,h6,input,textarea,select,[role="button"],[role="link"],[role="menuitem"]'
            ));
            for (const el of textNodes) {
                if (text.length >= 260) break;
                if (!isVisible(el)) continue;
                const visibleText = (el.innerText || el.textContent || '').trim();
                if (!visibleText) continue;

                const cs = getComputedStyle(el);
                const size = parseFloat(cs.fontSize) || 0;
                const weight = parseWeight(cs.fontWeight);
                const large = size >= 24 || (size >= 18.5 && weight >= 700);
                const r = el.getBoundingClientRect();
                text.push({
                    selector: toSelector(el),
                    text: visibleText.substring(0, 120),
                    category: large ? 'large_text' : 'normal_text',
                    foreground_color: cs.color,
                    background_color: effectiveBackground(el),
                    font_size_px: size,
                    font_weight: weight,
                    bbox: {
                        x: Math.round(r.x),
                        y: Math.round(r.y),
                        width: Math.round(r.width),
                        height: Math.round(r.height),
                    },
                });
            }

            const non_text = [];
            const nonTextNodes = Array.from(document.querySelectorAll(
                'button,input,select,textarea,a,[role="button"],[role="link"],[role="checkbox"],[role="radio"],[role="switch"],[role="tab"],svg,canvas'
            ));
            for (const el of nonTextNodes) {
                if (non_text.length >= 220) break;
                if (!isVisible(el)) continue;

                const cs = getComputedStyle(el);
                const borderW = parseFloat(cs.borderWidth) || 0;
                const fg = borderW > 0 ? cs.borderColor : cs.backgroundColor;
                if (!fg || fg === 'rgba(0, 0, 0, 0)' || fg === 'transparent') continue;

                const role = (el.getAttribute('role') || '').toLowerCase();
                const tag = el.tagName.toLowerCase();
                const isUi = ['button', 'input', 'select', 'textarea', 'a'].includes(tag) || (
                    role && ['button', 'link', 'checkbox', 'radio', 'switch', 'tab'].includes(role)
                );
                const r = el.getBoundingClientRect();
                non_text.push({
                    selector: toSelector(el),
                    category: isUi ? 'ui_component' : 'graphical_object',
                    foreground_color: fg,
                    background_color: effectiveBackground(el),
                    font_size_px: null,
                    font_weight: null,
                    bbox: {
                        x: Math.round(r.x),
                        y: Math.round(r.y),
                        width: Math.round(r.width),
                        height: Math.round(r.height),
                    },
                });
            }

            return { text, non_text };
        }""") or {"text": [], "non_text": []}

    # ------------------------------------------------------------------
    # Annotated screenshot generation
    # ------------------------------------------------------------------

    def _create_annotated_screenshot(
        self,
        screenshot_path: str,
        representative_failure: dict[str, Any] | None,
    ) -> tuple[str | None, dict[str, Any] | None]:
        """Render exactly one annotation for a selected representative failure."""
        try:
            if not representative_failure:
                return None, None

            checkpoint = representative_failure.get("checkpoint") or representative_failure.get("checkpoint_id")
            rationale = representative_failure.get("rationale", "Accessibility issue detected.")
            candidate = select_annotation_target(
                self._page,
                checkpoint or "",
                rationale,
            )
            if not candidate:
                return None, None

            annotation_payload = {
                **candidate,
                "checkpoint_id": checkpoint or candidate.get("checkpoint_id") or "?",
                "rationale": rationale or candidate.get("rationale"),
            }
            out_path = screenshot_path.replace(".png", "-annotated.png")
            output = annotate_screenshot(screenshot_path, [annotation_payload], out_path)
            metadata = {
                "strategy": "one-screenshot-one-annotation",
                "checkpoint": annotation_payload.get("checkpoint_id"),
                "rationale": rationale,
                "mode": "bbox",
                "map_quality": candidate.get("map_quality", "fallback"),
                "selector": candidate.get("selector"),
                "target_tag": candidate.get("tag"),
                "fallback_tier": candidate.get("fallback_tier"),
            }
            return output, metadata
        except Exception as e:
            logger.warning("Annotation failed: %s", e)
            return None, None

    # ------------------------------------------------------------------
    # Screen analysis
    # ------------------------------------------------------------------

    def _analyze_current_screen(self, label: str, action_source: str = "scripted") -> dict[str, Any]:
        url = self._page.url
        page_info = self._get_page_info()
        unique_key = self._make_unique_screen_key(url, page_info)
        dedup_enabled = self.config.get("analysis", {}).get("unique_screen_dedup", True)
        if dedup_enabled and unique_key in self.unique_screen_keys:
            print(f"\n  Duplicate screen skipped: {label} ({url})")
            if url not in self.visited_url_set:
                self.visited_urls.append(url)
                self.visited_url_set.add(url)
            self._record_observed_url(url)
            self._record_action_trace(
                action="analyze_skip_duplicate",
                source=action_source,
                details={"label": label, "url": url, "unique_key": unique_key},
            )
            return {"captured": False, "duplicate": True, "label": label, "url": url, "unique_key": unique_key}

        self.unique_screen_keys.add(unique_key)

        screen_num = len(self.screen_results) + 1
        print(f"\n{'='*60}")
        print(f"  [{screen_num}] ANALYZING: {label}")
        print(f"  URL: {url}")
        print(f"{'='*60}")

        screenshot = self._capture_screenshot(label)

        print(f"  Title: {page_info.get('title', '?')}")
        print(f"  Headings: {len(page_info.get('headings', []))}")
        print(f"  Interactive: {len(page_info.get('interactive', []))}")
        print(f"  Images (missing alt): {page_info.get('images_missing_alt', 0)}/{page_info.get('images_total', 0)}")
        print(f"  Can scroll: {page_info.get('can_scroll', False)}")

        # Scroll and capture additional folds if page is long.
        scroll_shots: list[dict[str, Any]] = []
        if page_info.get("can_scroll"):
            scroll_shots = self._scroll_and_screenshot(label)
            if scroll_shots:
                print(f"  Captured {len(scroll_shots)} scroll folds")

        # Build artifact and run WCAG buckets before annotation selection.
        artifact = self._build_page_artifact(screenshot_path=screenshot)
        results = self.analyzer.analyze(artifact)
        summary = self.analyzer.summarize_findings(results)
        representative = select_representative_failure(summary.get("failures", []))
        contrast_evidence = self._extract_contrast_evidence(artifact)
        checklist_evaluations = self._build_screen_checklist_evaluations(
            label=label,
            url=url,
            page_info=page_info,
            screenshot=screenshot,
            artifact=artifact,
            results=results,
        )

        annotated, annotation_meta = self._create_annotated_screenshot(screenshot, representative)
        if annotated:
            print(f"  Annotated screenshot: {Path(annotated).name}")

        scroll_paths = [item["path"] for item in scroll_shots]
        scroll_annotated_paths: list[str] = []
        if representative:
            for fold in scroll_shots:
                try:
                    self._page.evaluate(f"() => window.scrollTo(0, {int(fold['scroll_y'])})")
                    self._page.wait_for_timeout(120)
                    annotated_fold, _ = self._create_annotated_screenshot(fold["path"], representative)
                    if annotated_fold:
                        fold["annotated_path"] = annotated_fold
                        scroll_annotated_paths.append(annotated_fold)
                except Exception:
                    continue
            self._page.evaluate("() => window.scrollTo(0, 0)")
            self._page.wait_for_timeout(120)

        print(f"  WCAG: {summary['pass']}P / {summary['fail']}F / {summary['cannot_verify']}CV")
        if summary["failures"]:
            for f in summary["failures"][:5]:
                print(f"    FAIL [{f['checkpoint']}] {f['rationale'][:70]}")

        stem = Path(screenshot).stem
        dom_dump_path = self.run_dir / f"{stem}-dom.html"
        page_info_dump_path = self.run_dir / f"{stem}-page-info.json"
        wcag_results_dump_path = self.run_dir / f"{stem}-wcag-results.json"
        wcag_summary_dump_path = self.run_dir / f"{stem}-wcag-summary.json"
        dom_dump_path.write_text(artifact.html, encoding="utf-8")
        page_info_dump_path.write_text(
            json.dumps(
                {
                    **page_info,
                    "unique_key": unique_key,
                    "action_source": action_source,
                },
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        wcag_results_dump_path.write_text(
            json.dumps([r.to_dict() for r in results], indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        wcag_summary_dump_path.write_text(
            json.dumps(summary, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

        for evaluation in checklist_evaluations:
            evidence = evaluation.get("evidence", {})
            evidence["dom_dump"] = str(dom_dump_path)
            evidence["page_info_dump"] = str(page_info_dump_path)
            evidence["wcag_results_dump"] = str(wcag_results_dump_path)
            evaluation["evidence"] = evidence
            evaluation["llm_validation_payload"]["evidence"] = evidence

        screen_data = {
            "label": label,
            "url": url,
            "unique_key": unique_key,
            "action_source": action_source,
            "screenshot": screenshot,
            "annotated_screenshot": annotated,
            "annotation_metadata": annotation_meta,
            "scroll_screenshots": scroll_paths,
            "scroll_annotated_screenshots": scroll_annotated_paths,
            "dom_dump": str(dom_dump_path),
            "page_info_dump": str(page_info_dump_path),
            "wcag_results_dump": str(wcag_results_dump_path),
            "wcag_summary_dump": str(wcag_summary_dump_path),
            "page_info": page_info,
            "wcag_summary": summary,
            "wcag_results": [r.to_dict() for r in results],
            "contrast_evidence": contrast_evidence,
            "checklist_evaluations": checklist_evaluations,
        }
        self.screen_results.append(screen_data)
        self._record_action_trace(
            action="analyze",
            source=action_source,
            details={"label": label, "url": url, "unique_key": unique_key},
        )

        if url not in self.visited_url_set:
            self.visited_urls.append(url)
            self.visited_url_set.add(url)
        self._record_observed_url(url)
        self._record_route_event(
            event_type="screen_captured",
            source=action_source,
            from_url=url,
            to_url=url,
            details={
                "label": label,
                "unique_key": unique_key,
            },
        )

        return {"captured": True, **screen_data}

    def _extract_contrast_evidence(self, artifact: PageArtifact) -> dict[str, Any]:
        metrics = artifact.render_metrics
        text_failures = metrics.get("contrast_violations", []) or []
        non_text_failures = metrics.get("non_text_contrast_violations", []) or []
        text_samples = metrics.get("contrast_samples", []) or []
        non_text_samples = metrics.get("non_text_contrast_samples", []) or []
        return {
            "thresholds": {
                "normal_text": ">= 4.5:1",
                "large_text": ">= 3.0:1",
                "graphical_objects_and_ui_components": ">= 3.0:1",
            },
            "text_sampled": len(text_samples),
            "non_text_sampled": len(non_text_samples),
            "text_failures": text_failures,
            "non_text_failures": non_text_failures,
        }

    def _dom_fingerprint(self) -> str:
        signature = self._page.evaluate("""() => {
            const headingSig = Array.from(document.querySelectorAll('h1,h2,h3'))
                .slice(0, 12)
                .map(h => (h.innerText || '').trim().slice(0, 60))
                .join('|');
            const tagSet = [
                'main', 'nav', 'header', 'footer', 'section', 'article', 'aside',
                'form', 'input', 'button', 'select', 'textarea',
                'a', 'img', 'svg', 'canvas', 'table', 'h1', 'h2', 'h3'
            ];
            const tagCounts = tagSet.map(tag => `${tag}:${document.querySelectorAll(tag).length}`).join('|');
            const interactiveSig = Array.from(document.querySelectorAll(
                'a[href],button,input,select,textarea,[role="button"],[role="link"],[role="tab"],[tabindex]:not([tabindex="-1"])'
            )).slice(0, 30).map((el) => {
                const txt = (el.innerText || el.getAttribute('aria-label') || el.getAttribute('placeholder') || '')
                    .replace(/\\d+/g, '#')
                    .replace(/\\s+/g, ' ')
                    .trim()
                    .slice(0, 40);
                const role = el.getAttribute('role') || '';
                const tag = el.tagName.toLowerCase();
                return `${tag}|${role}|${txt}`;
            }).join('|');
            return {
                tag_counts: tagCounts,
                heading_signature: headingSig,
                interactive_signature: interactiveSig,
            };
        }""")
        raw = json.dumps(signature, sort_keys=True, ensure_ascii=False)
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()

    def _make_unique_screen_key(self, url: str, page_info: dict[str, Any]) -> str:
        normalized_url = self._normalize_url(url)
        try:
            dom_hash = self._dom_fingerprint()
        except Exception:
            fallback_dom = f"{normalized_url}|{page_info.get('title', '')}"
            dom_hash = hashlib.sha1(fallback_dom.encode("utf-8")).hexdigest()
        heading_signature = "|".join(
            (h.get("text", "") or "").strip().lower()
            for h in (page_info.get("headings", []) or [])[:12]
        )
        title_heading = f"{(page_info.get('title', '') or '').strip().lower()}|{heading_signature}"
        title_heading_hash = hashlib.sha1(title_heading.encode("utf-8")).hexdigest()[:16]
        return f"{normalized_url}|{dom_hash[:16]}|{title_heading_hash}"

    def _record_action_trace(self, action: str, source: str, details: dict[str, Any]) -> None:
        self.action_trace.append(
            {
                "timestamp": int(time.time() * 1000),
                "action": action,
                "source": source,
                **details,
            }
        )

    def _record_observed_url(self, url: str | None) -> None:
        candidate = (url or "").strip()
        if not candidate or not candidate.startswith(("http://", "https://")):
            return
        if candidate not in self.observed_url_set:
            self.observed_url_set.add(candidate)
            self.observed_urls.append(candidate)

    def _record_route_event(
        self,
        event_type: str,
        source: str,
        from_url: str | None = None,
        to_url: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        payload = {
            "timestamp": int(time.time() * 1000),
            "event_type": event_type,
            "source": source,
            "from_url": from_url or "",
            "to_url": to_url or "",
        }
        if details:
            payload.update(details)
        self.route_log.append(payload)

        for key in ("from_url", "to_url", "target_url", "current_url"):
            self._record_observed_url(payload.get(key))

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
        before_url = self._page.url
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
        self._page.wait_for_timeout(700)
        after_url = self._page.url
        self._record_route_event(
            event_type="scripted_click",
            source="scripted",
            from_url=before_url,
            to_url=after_url,
            details={
                "selector": selector,
                "description": action.get("description", ""),
                "url_changed": after_url != before_url,
            },
        )

    def _execute_otp(self, action: dict):
        otp = action["value"]
        print(f"  Entering OTP: {otp}")
        before_url = self._page.url
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
        self._page.wait_for_timeout(700)
        self._record_route_event(
            event_type="scripted_otp_submit",
            source="scripted",
            from_url=before_url,
            to_url=self._page.url,
            details={
                "description": action.get("description", ""),
                "url_changed": self._page.url != before_url,
            },
        )

    def _execute_pin(self, action: dict):
        pin = action["value"]
        print(f"  Entering PIN: {pin}")
        before_url = self._page.url
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
        self._page.wait_for_timeout(700)
        self._record_route_event(
            event_type="scripted_pin_submit",
            source="scripted",
            from_url=before_url,
            to_url=self._page.url,
            details={
                "description": action.get("description", ""),
                "url_changed": self._page.url != before_url,
            },
        )

    def _execute_captcha(self, action: dict):
        """Solve a text/image captcha using the LLM vision endpoint."""
        before_url = self._page.url
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
        self._record_route_event(
            event_type="scripted_captcha",
            source="scripted",
            from_url=before_url,
            to_url=self._page.url,
            details={
                "description": action.get("description", ""),
                "url_changed": self._page.url != before_url,
            },
        )

    def _manual_completion_met(self, action: dict[str, Any], current_url: str) -> bool:
        checks: list[bool] = []
        current_url_lower = current_url.lower()

        expected_url = str(action.get("completion_url") or action.get("expected_url") or "").strip()
        if expected_url:
            checks.append(self._normalize_url(current_url) == self._normalize_url(expected_url))

        url_contains = str(action.get("completion_url_contains") or "").strip()
        if url_contains:
            checks.append(url_contains.lower() in current_url_lower)

        for blocked in action.get("completion_url_not_contains", []) or []:
            blocked_text = str(blocked or "").strip().lower()
            if blocked_text:
                checks.append(blocked_text not in current_url_lower)

        url_regex = str(action.get("completion_url_regex") or "").strip()
        if url_regex:
            checks.append(bool(re.search(url_regex, current_url)))

        title_contains = str(action.get("completion_title_contains") or "").strip().lower()
        if title_contains:
            try:
                checks.append(title_contains in (self._page.title() or "").strip().lower())
            except Exception:
                checks.append(False)

        selector = str(action.get("completion_selector") or "").strip()
        if selector:
            try:
                element = self._page.query_selector(selector)
                checks.append(bool(element and element.is_visible()))
            except Exception:
                checks.append(False)

        if not checks:
            return False

        mode = str(action.get("completion_mode", "any") or "any").strip().lower()
        return all(checks) if mode == "all" else any(checks)

    def _execute_manual(self, action: dict[str, Any]) -> None:
        instructions = action.get("instructions") or action.get("description") or "Complete the next step manually in the browser."
        timeout_ms = int(action.get("timeout_ms", 300000) or 300000)
        poll_interval_ms = int(action.get("poll_interval_ms", 1000) or 1000)
        stable_wait_ms = int(action.get("stable_wait_ms", 1500) or 1500)

        start_url = self._page.url
        last_url = start_url
        print(f"  Manual step: {instructions}")
        print(f"  Waiting up to {timeout_ms // 1000} seconds for completion...")
        self._record_route_event(
            event_type="manual_wait_start",
            source="manual",
            from_url=start_url,
            to_url=start_url,
            details={
                "description": action.get("description", ""),
                "instructions": instructions,
                "timeout_ms": timeout_ms,
            },
        )

        deadline = time.time() + (timeout_ms / 1000.0)
        while time.time() < deadline:
            if not self._is_page_alive():
                recovered = self._recover_closed_page(last_url)
                if not recovered:
                    raise RuntimeError("Manual step aborted because browser recovery failed.")

            current_url = self._page.url
            if current_url != last_url:
                print(f"  URL changed: {current_url}")
                self._record_route_event(
                    event_type="manual_url_change",
                    source="manual",
                    from_url=last_url,
                    to_url=current_url,
                    details={
                        "description": action.get("description", ""),
                        "instructions": instructions,
                    },
                )
                last_url = current_url

            if self._manual_completion_met(action, current_url):
                self._page.wait_for_timeout(stable_wait_ms)
                final_url = self._page.url
                if final_url != last_url:
                    self._record_route_event(
                        event_type="manual_url_change",
                        source="manual",
                        from_url=last_url,
                        to_url=final_url,
                        details={
                            "description": action.get("description", ""),
                            "instructions": instructions,
                        },
                    )
                    last_url = final_url

                print(f"  Manual step completed at: {final_url}")
                self._record_route_event(
                    event_type="manual_wait_complete",
                    source="manual",
                    from_url=start_url,
                    to_url=final_url,
                    details={
                        "description": action.get("description", ""),
                        "instructions": instructions,
                    },
                )
                return

            self._page.wait_for_timeout(poll_interval_ms)

        self._record_route_event(
            event_type="manual_wait_timeout",
            source="manual",
            from_url=start_url,
            to_url=last_url,
            details={
                "description": action.get("description", ""),
                "instructions": instructions,
                "timeout_ms": timeout_ms,
            },
        )
        raise TimeoutError(f"Manual step timed out after {timeout_ms} ms: {instructions}")

    def _infer_fill_context(self, reason: str, element_info: dict[str, Any] | None) -> str:
        info = element_info or {}
        blob = " ".join(
            [
                reason or "",
                info.get("text", "") or "",
                info.get("aria_label", "") or "",
                info.get("placeholder", "") or "",
                info.get("id", "") or "",
                info.get("type", "") or "",
                info.get("href", "") or "",
            ]
        ).lower()
        if any(token in blob for token in ("otp", "one time", "verification code")):
            return "otp"
        if any(token in blob for token in ("pin", "mpin", "passcode", "security code")):
            return "pin"
        if any(token in blob for token in ("login", "client", "mobile", "email", "user", "userid", "username")):
            return "login_id"
        return "generic"

    def _fill_value_for_code(self, code: str, cursor_attr: str, element_handle) -> str:
        if not code:
            return ""
        try:
            meta = element_handle.evaluate(
                """el => ({
                    maxLength: typeof el.maxLength === 'number' ? el.maxLength : -1,
                    inputMode: el.inputMode || '',
                    type: el.type || '',
                })"""
            )
        except Exception:
            meta = {}
        max_length = int(meta.get("maxLength", -1) or -1)
        single_char_field = 0 < max_length <= 2
        if single_char_field:
            cursor = getattr(self, cursor_attr, 0)
            digit = code[cursor % len(code)]
            setattr(self, cursor_attr, cursor + 1)
            return digit
        return code

    def _resolve_explore_fill_value(
        self,
        reason: str,
        element_info: dict[str, Any] | None,
        llm_value: str,
        element_handle,
    ) -> str:
        context = self._infer_fill_context(reason, element_info)
        login_id = self.validation_profile.get("login_id", "JAI")
        otp = self.validation_profile.get("otp", "7890")
        pin = self.validation_profile.get("pin", "1234")
        provided = str(llm_value or "").strip()

        if context == "otp":
            value = self._fill_value_for_code(otp, "_otp_fill_cursor", element_handle)
            print("  Using configured OTP value for validation")
            return value
        if context == "pin":
            value = self._fill_value_for_code(pin, "_pin_fill_cursor", element_handle)
            print("  Using configured PIN value for validation")
            return value
        if context == "login_id":
            print(f"  Using configured login credential: '{login_id}'")
            return login_id

        allowed = {login_id, otp, pin}
        if provided in allowed:
            return provided
        print(f"  Replacing LLM fill value '{provided or '<empty>'}' with configured login credential: '{login_id}'")
        return login_id

    def _llm_decision_failed(self, decision: dict[str, Any] | None) -> bool:
        if not decision:
            return True
        reason = (decision.get("reason") or "").lower()
        action = (decision.get("action") or "").lower()
        if "llm error" in reason or "connection error" in reason or "routing error" in reason:
            return True
        return action not in {"done", "click", "navigate", "scroll", "back", "fill"}

    def _fallback_click_key(self, current_url: str, idx: int, el: dict[str, Any]) -> str:
        return "|".join(
            [
                self._normalize_url(current_url),
                str(idx),
                (el.get("tag") or "").lower(),
                (el.get("role") or "").lower(),
                (el.get("id") or "").lower(),
                (el.get("text") or el.get("aria_label") or "").strip().lower()[:80],
                (el.get("href") or "").strip().lower()[:120],
            ]
        )

    def _decide_fallback_explore_action(
        self,
        current_url: str,
        page_info: dict[str, Any],
        visible_elements: list[dict[str, Any]],
    ) -> dict[str, Any]:
        profile = self.config.get("analysis", {}).get("exploration_profile", {}) or {}
        preferred_tokens = tuple(str(item).lower() for item in profile.get("preferred_tokens", []) or [])
        avoid_tokens = tuple(str(item).lower() for item in profile.get("avoid_tokens", []) or [])
        nav_like_tokens = (
            "nav", "menu", "tab", "overview", "watchlist", "order",
            "portfolio", "profile", "settings", "help", "about", "notification",
            "account", "dashboard", "trade",
        )
        ranked: list[tuple[int, int, dict[str, Any]]] = []
        for idx, el in enumerate(visible_elements):
            text_blob = " ".join(
                [
                    (el.get("text") or ""),
                    (el.get("aria_label") or ""),
                    (el.get("role") or ""),
                    (el.get("href") or ""),
                    (el.get("id") or ""),
                ]
            ).lower()
            tag = (el.get("tag") or "").lower()
            role = (el.get("role") or "").lower()
            nav_score = 0
            if any(tok in text_blob for tok in nav_like_tokens):
                nav_score += 50
            if any(tok in text_blob for tok in preferred_tokens):
                nav_score += 80
            if any(tok in text_blob for tok in avoid_tokens):
                nav_score -= 120
            if tag in {"a", "button"}:
                nav_score += 20
            if role in {"tab", "menuitem", "link", "button"}:
                nav_score += 20
            if el.get("href"):
                nav_score += 15
            if (el.get("text") or "").strip():
                nav_score += 5
            if "logout" in text_blob or "sign out" in text_blob:
                nav_score -= 200
            ranked.append((nav_score, idx, el))

        ranked.sort(key=lambda item: (-item[0], item[1]))
        for _, idx, el in ranked:
            key = self._fallback_click_key(current_url, idx, el)
            if key in self._fallback_clicked_keys:
                continue
            self._fallback_clicked_keys.add(key)
            return {
                "action": "click",
                "index": idx,
                "reason": "fallback: unseen nav/tab/button candidate",
            }

        norm_url = self._normalize_url(current_url)
        if page_info.get("can_scroll"):
            count = self._fallback_scroll_counts.get(norm_url, 0)
            if count < 2:
                self._fallback_scroll_counts[norm_url] = count + 1
                return {"action": "scroll", "reason": "fallback: explore below the fold"}

        for el in visible_elements:
            href = (el.get("href") or "").strip()
            if not href:
                continue
            if not self._is_url_visited(href):
                return {"action": "navigate", "url": href, "reason": "fallback: unseen href navigation"}

        if self._nav_stack:
            return {"action": "back", "reason": "fallback: controlled backtrack"}

        return {"action": "done", "reason": "fallback: no unseen candidates"}

    # ------------------------------------------------------------------
    # LLM-guided deep exploration
    # ------------------------------------------------------------------

    def _execute_explore(self):
        """LLM-guided exploration with deterministic fallback to maximize unique screens."""
        analysis_cfg = self.config.get("analysis", {})
        max_screens = analysis_cfg.get("max_screens", 200)
        max_steps = analysis_cfg.get("max_explore_depth", 600)
        stagnation_window = analysis_cfg.get("stagnation_window", 40)
        llm_failure_fallback = analysis_cfg.get("llm_failure_fallback", True)

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
        stagnant_attempts = 0
        for step in range(max_steps):
            if len(self.screen_results) >= max_screens:
                print(f"\n  Reached target screen limit ({max_screens})")
                break

            if stagnant_attempts >= stagnation_window:
                if analysis_cfg.get("manual_assist_on_stall"):
                    assisted = self._run_manual_exploration_assist("stagnation")
                    if assisted > 0:
                        stagnant_attempts = 0
                        consecutive_failures = 0
                        continue
                print(
                    f"\n  Stopping exploration after {stagnation_window} attempts without a new unique screen"
                )
                break

            if consecutive_failures >= 5:
                if analysis_cfg.get("manual_assist_on_stall"):
                    assisted = self._run_manual_exploration_assist("consecutive_failures")
                    if assisted > 0:
                        stagnant_attempts = 0
                        consecutive_failures = 0
                        continue
                print(f"\n  Too many consecutive failures, moving on")
                break

            if not self._is_page_alive():
                recovered = self._recover_closed_page()
                if not recovered:
                    print("  Exploration stopped because page recovery failed.")
                    break

            try:
                self._page.wait_for_timeout(1200)
                current_url = self._page.url
            except Exception as e:
                recovered = self._recover_closed_page(self.visited_urls[-1] if self.visited_urls else None)
                if not recovered:
                    print(f"  Exploration stopped: {e}")
                    break
                consecutive_failures += 1
                stagnant_attempts += 1
                continue

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
                exploration_context=self._build_exploration_context(current_url, page_info),
            )
            action_source = "llm"

            if llm_failure_fallback and self._llm_decision_failed(decision):
                decision = self._decide_fallback_explore_action(current_url, page_info, visible_elements)
                action_source = "fallback"

            if llm_failure_fallback and (decision.get("action") == "done") and len(self.screen_results) < max_screens:
                fallback = self._decide_fallback_explore_action(current_url, page_info, visible_elements)
                if fallback.get("action") != "done":
                    decision = fallback
                    action_source = "fallback"

            action = decision.get("action", "done")
            reason = decision.get("reason", "")
            print(f"\n  [{step+1}/{max_steps}] {action_source.upper()}: {action} — {reason[:60]}")
            self._record_action_trace(
                action=action,
                source=action_source,
                details={
                    "step": step + 1,
                    "url": current_url,
                    "reason": reason,
                },
            )

            if action == "done":
                # Double-check: if we haven't reached 30, push the LLM to keep going
                if len(self.screen_results) < 30:
                    print(f"  Only {len(self.screen_results)} screens — pushing LLM to continue")
                    self.router._history.append({
                        "role": "user",
                        "content": f"NOT DONE YET! Only {len(self.screen_results)} screens covered. "
                                   f"Need at least 30. Go back and explore sub-pages, tabs, settings, profile, etc."
                    })
                    if analysis_cfg.get("manual_assist_on_stall") and len(self.screen_results) < min(10, max_screens):
                        assisted = self._run_manual_exploration_assist("llm_done_too_early")
                        if assisted > 0:
                            stagnant_attempts = 0
                            consecutive_failures = 0
                            continue
                    stagnant_attempts += 1
                    continue
                print("  Exploration complete.")
                break

            elif action == "scroll":
                print("  Scrolling page...")
                self._page.evaluate("() => window.scrollBy(0, window.innerHeight * 0.8)")
                self._page.wait_for_timeout(800)
                consecutive_failures = 0
                stagnant_attempts += 1

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
                            stagnant_attempts += 1
                            continue

                        self._page.wait_for_timeout(2000)
                        self._record_route_event(
                            event_type="explore_click",
                            source=action_source,
                            from_url=current_url,
                            to_url=self._page.url,
                            details={
                                "step": step + 1,
                                "element_index": idx,
                                "element_tag": el_info.get("tag", ""),
                                "element_text": el_text[:120],
                                "target_url": el_info.get("href", ""),
                                "url_changed": self._page.url != current_url,
                            },
                        )
                        analysis = self._analyze_current_screen(
                            f"explore-{step+1}-{el_text[:20]}",
                            action_source=action_source,
                        )
                        if analysis.get("captured"):
                            stagnant_attempts = 0
                        else:
                            stagnant_attempts += 1
                        consecutive_failures = 0
                    except Exception as e:
                        print(f"  Click failed: {e}")
                        consecutive_failures += 1
                        stagnant_attempts += 1
                else:
                    consecutive_failures += 1
                    stagnant_attempts += 1

            elif action == "navigate":
                url = decision.get("url", "")
                if url and not self._is_url_visited(url):
                    print(f"  Navigating: {url[:70]}")
                    try:
                        self._nav_stack.append(current_url)
                        self._safe_goto(url)
                        self._page.wait_for_timeout(2000)
                        self._record_route_event(
                            event_type="explore_navigate",
                            source=action_source,
                            from_url=current_url,
                            to_url=self._page.url,
                            details={
                                "step": step + 1,
                                "target_url": url,
                                "url_changed": self._page.url != current_url,
                            },
                        )
                        analysis = self._analyze_current_screen(
                            f"explore-{step+1}-nav",
                            action_source=action_source,
                        )
                        if analysis.get("captured"):
                            stagnant_attempts = 0
                        else:
                            stagnant_attempts += 1
                        consecutive_failures = 0
                    except Exception as e:
                        print(f"  Navigation failed: {e}")
                        consecutive_failures += 1
                        stagnant_attempts += 1
                else:
                    consecutive_failures += 1
                    stagnant_attempts += 1

            elif action == "back":
                print("  Going back")
                try:
                    destination = ""
                    if self._nav_stack:
                        back_url = self._nav_stack.pop()
                        self._safe_goto(back_url)
                        destination = back_url
                    else:
                        self._page.go_back(wait_until="domcontentloaded", timeout=15000)
                        destination = self._page.url
                    self._page.wait_for_timeout(1500)
                    self._record_route_event(
                        event_type="explore_back",
                        source=action_source,
                        from_url=current_url,
                        to_url=self._page.url,
                        details={
                            "step": step + 1,
                            "target_url": destination,
                            "url_changed": self._page.url != current_url,
                        },
                    )
                    consecutive_failures = 0
                    stagnant_attempts += 1
                except Exception:
                    consecutive_failures += 1
                    stagnant_attempts += 1

            elif action == "fill":
                idx = decision.get("index", 0)
                value = decision.get("value", "")
                target_info = visible_elements[idx] if 0 <= idx < len(visible_elements) else {}
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
                                    resolved = self._resolve_explore_fill_value(reason, target_info, value, el)
                                    el.fill(resolved)
                                    break
                                visible_idx += 1
                        except Exception:
                            visible_idx += 1
                    consecutive_failures = 0
                    stagnant_attempts += 1
                except Exception as e:
                    print(f"  Fill failed: {e}")
                    consecutive_failures += 1
                    stagnant_attempts += 1
            else:
                consecutive_failures += 1
                stagnant_attempts += 1

    def _build_exploration_context(self, current_url: str, page_info: dict[str, Any]) -> str:
        profile = self.config.get("analysis", {}).get("exploration_profile", {}) or {}
        lines: list[str] = []
        persona = str(profile.get("persona") or "").strip()
        if persona:
            lines.append(persona)
        current_phase = str(profile.get("phase") or "").strip()
        if current_phase:
            lines.append(f"Current product phase: {current_phase}.")
        major_sections = profile.get("major_sections", []) or []
        if major_sections:
            lines.append("Prioritize these major user areas before edge/help/legal flows: " + ", ".join(str(item) for item in major_sections) + ".")
        preferred = profile.get("preferred_tokens", []) or []
        if preferred:
            lines.append("Strongly prefer elements containing tokens like: " + ", ".join(str(item) for item in preferred) + ".")
        avoid = profile.get("avoid_tokens", []) or []
        if avoid:
            lines.append("Avoid low-value or repetitive elements containing tokens like: " + ", ".join(str(item) for item in avoid) + ".")
        journey = profile.get("journey_rules", []) or []
        for rule in journey:
            rule_text = str(rule or "").strip()
            if rule_text:
                lines.append(rule_text)
        lines.append(f"Current URL for routing context: {current_url}")
        lines.append(f"Current title: {page_info.get('title', '')}")
        return "\n".join(lines)

    def _run_manual_exploration_assist(self, reason: str) -> int:
        analysis_cfg = self.config.get("analysis", {})
        timeout_ms = int(analysis_cfg.get("manual_assist_timeout_ms", 180000) or 180000)
        idle_ms = int(analysis_cfg.get("manual_assist_idle_ms", 20000) or 20000)
        poll_ms = int(analysis_cfg.get("manual_assist_poll_ms", 1500) or 1500)
        instructions = (
            "Manual exploration assist: use the live browser to continue into deeper product routes. "
            "Open major sections, tabs, drawers, details, and reports as a real user would. "
            "The scanner will auto-capture new screens you reach."
        )
        print(f"\n  Entering manual exploration assist due to {reason}.")
        print(f"  {instructions}")

        captured = 0
        started_at = time.time()
        last_activity = started_at
        baseline_info = self._get_page_info()
        baseline_key = self._make_unique_screen_key(self._page.url, baseline_info)
        last_key = baseline_key
        last_url = self._page.url
        self._record_route_event(
            event_type="manual_explore_assist_start",
            source="manual",
            from_url=last_url,
            to_url=last_url,
            details={"reason": reason, "timeout_ms": timeout_ms, "idle_ms": idle_ms},
        )

        while (time.time() - started_at) * 1000 < timeout_ms:
            if not self._is_page_alive():
                if not self._recover_closed_page(last_url):
                    break
            self._page.wait_for_timeout(poll_ms)
            current_url = self._page.url
            page_info = self._get_page_info()
            current_key = self._make_unique_screen_key(current_url, page_info)

            if current_url != last_url:
                self._record_route_event(
                    event_type="manual_explore_url_change",
                    source="manual",
                    from_url=last_url,
                    to_url=current_url,
                    details={"reason": reason},
                )
                last_url = current_url
                last_activity = time.time()

            if current_key != last_key:
                analysis = self._analyze_current_screen(
                    f"manual-assist-{captured + 1}",
                    action_source="manual",
                )
                if analysis.get("captured"):
                    captured += 1
                last_key = current_key
                last_activity = time.time()
                continue

            if captured > 0 and (time.time() - last_activity) * 1000 >= idle_ms:
                break

        self._record_route_event(
            event_type="manual_explore_assist_complete",
            source="manual",
            from_url=self._page.url,
            to_url=self._page.url,
            details={"reason": reason, "captured_screens": captured},
        )
        return captured

    # ------------------------------------------------------------------
    # Main run
    # ------------------------------------------------------------------

    def run(self) -> dict[str, Any]:
        run_id = self._next_run_dir_name()
        self.run_dir = self.artifacts_root / run_id
        self.run_dir.mkdir(parents=True, exist_ok=True)

        print(f"\n{'#'*60}")
        print(f"  AGENTIC ACCESSIBILITY SCANNER")
        print(f"  Config: {self.config['name']}")
        print(f"  Standard: {WCAG_STANDARD_LABEL}")
        print(f"  Scan mode: {self.scan_mode}")
        analysis_cfg = self.config.get("analysis", {})
        print(
            f"  Target: up to {analysis_cfg.get('max_screens', 200)} unique screens, "
            "single-issue annotations"
        )
        print(f"  Artifacts: {self.run_dir}")
        print(f"{'#'*60}")

        self._launch_browser()

        try:
            start_url = self.config["start_url"]
            print(f"\n  Navigating to {start_url}")
            self._safe_goto(start_url)
            self._page.wait_for_timeout(2000)
            self._record_route_event(
                event_type="start_navigation",
                source="initial",
                from_url=start_url,
                to_url=self._page.url,
                details={
                    "configured_start_url": start_url,
                    "url_changed": self._page.url != start_url,
                },
            )

            self._analyze_current_screen("01-initial-load", action_source="initial")

            for step in self.config.get("flow_steps", []):
                step_id = step.get("id", "unknown")
                desc = step.get("description", step_id)
                if not self._should_run_step(step):
                    print(f"\n  Skipping step in {self.scan_mode} mode: {desc}")
                    continue
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
                elif self.config.get("analysis", {}).get("strict_flow_actions", True):
                    required_indices = list(range(len(planned_actions)))
                else:
                    required_indices = self.router.filter_step_actions(page_info, interactive_els, planned_actions)
                
                for i, action in enumerate(planned_actions):
                    if i not in required_indices:
                        print(f"  ⏭️  Skipping action: '{action.get('description', action['type'])}' (LLM determined not required)")
                        continue
                        
                    action_type = action["type"]
                    self._record_action_trace(
                        action=action_type,
                        source="scripted",
                        details={
                            "step": step_id,
                            "description": action.get("description", action_type),
                            "url": self._page.url,
                        },
                    )
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
                    elif action_type == "manual":
                        self._execute_manual(action)
                    elif action_type == "explore":
                        self._execute_explore()
                        continue

                wait = step.get("wait_after", 2000)
                self._page.wait_for_timeout(wait)
                self._analyze_current_screen(f"{step_id}", action_source="scripted")

            report = self._build_report(run_id)
            checklist_reports_root = generate_checklist_reports(report, self.run_dir)
            report["checklist_reports_root"] = checklist_reports_root

            evidence_index = self._build_evidence_index(run_id)
            evidence_path = self.run_dir / "evidence-index.json"
            with open(evidence_path, "w") as f:
                json.dump(evidence_index, f, indent=2, default=str)
            print(f"  Evidence index saved: {evidence_path}")

            route_log = {
                "run_id": run_id,
                "standard": WCAG_STANDARD_LABEL,
                "urls_visited": report.get("urls_visited", []),
                "route_log": self.route_log,
            }
            route_log_path = self.run_dir / "route-log.json"
            with open(route_log_path, "w") as f:
                json.dump(route_log, f, indent=2, default=str)
            print(f"  Route log saved: {route_log_path}")

            report["evidence_index_artifact"] = str(evidence_path)
            report["route_log_artifact"] = str(route_log_path)

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
        urls_for_report: list[str] = []
        urls_seen: set[str] = set()

        for screen in self.screen_results:
            s = screen.get("wcag_summary", {})
            total_pass += s.get("pass", 0)
            total_fail += s.get("fail", 0)
            total_cv += s.get("cannot_verify", 0)
            for f in s.get("failures", []):
                f["screen"] = screen["label"]
                all_failures.append(f)
            screen_url = str(screen.get("url", "") or "").strip()
            if screen_url and screen_url not in urls_seen:
                urls_seen.add(screen_url)
                urls_for_report.append(screen_url)

        for url in self.observed_urls + self.visited_urls:
            candidate = str(url or "").strip()
            if candidate and candidate not in urls_seen:
                urls_seen.add(candidate)
                urls_for_report.append(candidate)

        for event in self.route_log:
            for key in ("from_url", "to_url", "target_url", "current_url"):
                candidate = str(event.get(key, "") or "").strip()
                if candidate and candidate not in urls_seen:
                    urls_seen.add(candidate)
                    urls_for_report.append(candidate)

        cv_metrics = self._compute_cannot_verify_metrics()
        checklist_rollup = self._build_checklist_rollup()

        return {
            "run_id": run_id,
            "config": self.config["name"],
            "standard": WCAG_STANDARD_LABEL,
            "scan_mode": self.scan_mode,
            "cannot_verify_policy": self.analyzer.cannot_verify_policy,
            "cannot_verify_threshold": self.analyzer.cannot_verify_threshold,
            "cannot_verify_enforcement": self.analyzer.cannot_verify_enforcement,
            "cannot_verify_metrics": cv_metrics,
            "screens_analyzed": len(self.screen_results),
            "urls_visited": urls_for_report,
            "screen_urls": self.visited_urls,
            "totals": {
                "pass": total_pass,
                "fail": total_fail,
                "cannot_verify": total_cv,
            },
            "all_failures": all_failures,
            "checklist_catalog": [spec.to_dict() for spec in self.checklist_specs],
            "checklist_rollup": checklist_rollup,
            "action_trace": self.action_trace,
            "route_log": self.route_log,
            "screens": [
                {
                    "label": s["label"],
                    "url": s["url"],
                    "unique_key": s.get("unique_key"),
                    "action_source": s.get("action_source", "unknown"),
                    "screenshot": s["screenshot"],
                    "annotated_screenshot": s.get("annotated_screenshot"),
                    "annotation_metadata": s.get("annotation_metadata"),
                    "scroll_screenshots": s.get("scroll_screenshots", []),
                    "scroll_annotated_screenshots": s.get("scroll_annotated_screenshots", []),
                    "dom_dump": s.get("dom_dump"),
                    "page_info_dump": s.get("page_info_dump"),
                    "wcag_results_dump": s.get("wcag_results_dump"),
                    "wcag_summary_dump": s.get("wcag_summary_dump"),
                    "wcag_summary": s["wcag_summary"],
                    "wcag_results": s.get("wcag_results", []),
                    "contrast_evidence": s.get("contrast_evidence", {}),
                    "checklist_evaluations": s.get("checklist_evaluations", []),
                }
                for s in self.screen_results
            ],
        }

    def _build_checklist_rollup(self) -> list[dict[str, Any]]:
        rollup: list[dict[str, Any]] = []
        for spec in self.checklist_specs:
            screen_evaluations: list[dict[str, Any]] = []
            statuses: list[str] = []
            pages: list[str] = []
            for screen in self.screen_results:
                for evaluation in screen.get("checklist_evaluations", []):
                    if evaluation.get("sc_id") != spec.sc_id:
                        continue
                    screen_evaluations.append(evaluation)
                    if evaluation.get("status"):
                        statuses.append(evaluation["status"])
                    page_url = evaluation.get("page_url")
                    if page_url and page_url not in pages:
                        pages.append(page_url)
            rollup.append(
                {
                    **spec.to_dict(),
                    "aggregate_status": self._reduce_status(statuses),
                    "screen_evaluations": screen_evaluations,
                    "pages": pages,
                }
            )
        return rollup

    def _compute_cannot_verify_metrics(self) -> dict[str, Any]:
        by_checkpoint: dict[str, list[str]] = {}
        instance_count = 0

        for screen in self.screen_results:
            for row in screen.get("wcag_results", []):
                checkpoint_id = row.get("checkpoint_id")
                status = row.get("status")
                if not checkpoint_id or not status:
                    continue
                by_checkpoint.setdefault(checkpoint_id, []).append(status)
                if status == "Cannot verify automatically":
                    instance_count += 1

        checkpoint_count = 0
        for statuses in by_checkpoint.values():
            resolved = self._reduce_status(statuses)
            if resolved == "Cannot verify automatically":
                checkpoint_count += 1

        threshold = self.analyzer.cannot_verify_threshold
        checkpoint_within = checkpoint_count <= threshold
        instance_within = instance_count <= threshold
        enforcement = self.analyzer.cannot_verify_enforcement
        if enforcement == "checkpoint":
            within_threshold = checkpoint_within
        elif enforcement == "instance":
            within_threshold = instance_within
        else:
            within_threshold = checkpoint_within and instance_within

        return {
            "checkpoint_count": checkpoint_count,
            "instance_count": instance_count,
            "threshold": threshold,
            "enforcement": enforcement,
            "checkpoint_within_threshold": checkpoint_within,
            "instance_within_threshold": instance_within,
            "within_threshold": within_threshold,
        }

    @staticmethod
    def _reduce_status(statuses: list[str]) -> str:
        if not statuses:
            return "Not evaluated"
        if "Fail" in statuses:
            return "Fail"
        if "Cannot verify automatically" in statuses:
            return "Cannot verify automatically"
        if all(item == "Not applicable" for item in statuses):
            return "Not applicable"
        if "Pass" in statuses:
            return "Pass"
        return "Not evaluated"

    def _build_evidence_index(self, run_id: str) -> dict[str, Any]:
        return {
            "run_id": run_id,
            "standard": WCAG_STANDARD_LABEL,
            "screens_analyzed": len(self.screen_results),
            "urls_visited": list(self.observed_urls),
            "route_log": self.route_log,
            "screens": [
                {
                    "label": s["label"],
                    "url": s["url"],
                    "unique_key": s.get("unique_key"),
                    "action_source": s.get("action_source", "unknown"),
                    "checklist_evaluations": s.get("checklist_evaluations", []),
                    "evidence": {
                        "screenshot": s.get("screenshot"),
                        "annotated_screenshot": s.get("annotated_screenshot"),
                        "scroll_screenshots": s.get("scroll_screenshots", []),
                        "scroll_annotated_screenshots": s.get("scroll_annotated_screenshots", []),
                        "dom_dump": s.get("dom_dump"),
                        "page_info_dump": s.get("page_info_dump"),
                        "wcag_results_dump": s.get("wcag_results_dump"),
                        "wcag_summary_dump": s.get("wcag_summary_dump"),
                    },
                }
                for s in self.screen_results
            ],
        }

    def _print_summary(self, report: dict[str, Any]):
        print(f"\n{'='*60}")
        print(f"  FINAL SUMMARY")
        print(f"{'='*60}")
        print(f"  Standard:           {report.get('standard', WCAG_STANDARD_LABEL)}")
        print(f"  Screens analyzed:   {report['screens_analyzed']}")
        print(f"  URLs visited:       {len(report['urls_visited'])}")
        print(f"  Total PASS:         {report['totals']['pass']}")
        print(f"  Total FAIL:         {report['totals']['fail']}")
        print(f"  Total CANNOT_VERIFY:{report['totals']['cannot_verify']}")
        print(f"  Unique failures:    {len(report['all_failures'])}")
        cv_metrics = report.get("cannot_verify_metrics", {})
        if cv_metrics:
            print(
                "  CV metrics:         "
                f"checkpoints={cv_metrics.get('checkpoint_count', 0)}, "
                f"instances={cv_metrics.get('instance_count', 0)}, "
                f"threshold={cv_metrics.get('threshold', 31)}, "
                f"within={cv_metrics.get('within_threshold', False)}"
            )

        annotated_count = sum(
            1 for s in report["screens"]
            if s.get("annotated_screenshot")
        )
        fold_annotated_count = sum(len(s.get("scroll_annotated_screenshots", [])) for s in report["screens"])
        print(f"  Annotated screenshots: {annotated_count}")
        print(f"  Annotated folds:       {fold_annotated_count}")

        if report["all_failures"]:
            print(f"\n  TOP FAILURES BY CHECKPOINT:")
            by_cp: dict[str, int] = {}
            for f in report["all_failures"]:
                cp = f["checkpoint"]
                by_cp[cp] = by_cp.get(cp, 0) + 1
            for cp, count in sorted(by_cp.items(), key=lambda x: -x[1]):
                print(f"    [{cp}] x{count}")
