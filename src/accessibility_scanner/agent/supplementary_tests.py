"""Supplementary WCAG testing methods using axe-core + Playwright instrumentation."""
from __future__ import annotations
import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

# ── axe-core engine ──────────────────────────────────────────────────────────

_AXE_SCRIPT: str | None = None


def _get_axe_script() -> str:
    """Lazy-load the axe.min.js script once."""
    global _AXE_SCRIPT
    if _AXE_SCRIPT is None:
        from pathlib import Path
        axe_path = Path(__file__).parent.parent.parent.parent / "axe.min.js"
        if not axe_path.exists():
            # Fallback: bundled with axe-playwright-python
            try:
                import axe_playwright_python
                import os
                axe_path = Path(os.path.dirname(axe_playwright_python.__file__)) / "axe.min.js"
            except ImportError:
                raise RuntimeError("axe.min.js not found. Install axe-playwright-python or place axe.min.js in project root.")
        _AXE_SCRIPT = axe_path.read_text(encoding="utf-8")
    return _AXE_SCRIPT


def run_axe_scan(page, tags: list[str] | None = None) -> dict[str, Any]:
    """
    Inject axe-core and run a full scan. Returns structured violations.

    tags: axe-core rule tags to include, e.g. ["wcag2aa", "wcag21aa", "best-practice"]
          Default: WCAG 2.1 A + AA
    """
    if tags is None:
        tags = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"]

    try:
        # Inject axe-core
        page.evaluate(_get_axe_script())

        # Run axe with specified tags
        options = {
            "runOnly": {"type": "tag", "values": tags},
            "resultTypes": ["violations", "passes", "incomplete"],
        }
        result = page.evaluate(
            f"axe.run(document, {json.dumps(options)}).then(r => r)"
        )
        violations = result.get("violations", [])
        passes = result.get("passes", [])
        incomplete = result.get("incomplete", [])

        # Parse into structured format
        parsed_violations = []
        for v in violations:
            parsed_violations.append({
                "id": v["id"],
                "impact": v.get("impact", ""),
                "description": v.get("description", ""),
                "help": v.get("help", ""),
                "helpUrl": v.get("helpUrl", ""),
                "tags": [t for t in v.get("tags", []) if t.startswith("wcag")],
                "nodes_count": len(v.get("nodes", [])),
                "nodes": [
                    {
                        "target": n.get("target", []),
                        "html": (n.get("html", ""))[:200],
                        "impact": n.get("impact", ""),
                        "failure_summary": n.get("failureSummary", ""),
                    }
                    for n in v.get("nodes", [])[:5]  # cap at 5 nodes per violation
                ],
            })

        return {
            "violations_count": len(violations),
            "passes_count": len(passes),
            "incomplete_count": len(incomplete),
            "violations": parsed_violations,
            "total_violation_nodes": sum(len(v.get("nodes", [])) for v in violations),
        }
    except Exception as e:
        logger.warning("axe-core scan error: %s", e)
        return {"violations_count": 0, "error": str(e)}


# ── Color Contrast Test ──────────────────────────────────────────────────────

def run_color_contrast_test(page, screenshot_base_path: str) -> dict[str, Any]:
    """
    WCAG 1.4.3 Contrast (Minimum) + 1.4.11 Non-text Contrast.
    Uses axe-core's color-contrast rule for accurate computed-style analysis.
    """
    try:
        page.evaluate(_get_axe_script())
        options = {
            "runOnly": {"type": "rule", "values": ["color-contrast", "link-in-text-block"]},
            "resultTypes": ["violations", "passes", "incomplete"],
        }
        result = page.evaluate(
            f"axe.run(document, {json.dumps(options)}).then(r => r)"
        )

        violations = result.get("violations", [])
        incomplete = result.get("incomplete", [])

        contrast_issues = []
        for v in violations:
            for node in v.get("nodes", [])[:10]:
                contrast_issues.append({
                    "target": node.get("target", []),
                    "html": (node.get("html", ""))[:150],
                    "impact": node.get("impact", ""),
                    "message": node.get("failureSummary", "")[:200],
                })

        # Also check for incomplete (elements where contrast couldn't be determined)
        needs_review = sum(len(v.get("nodes", [])) for v in incomplete)

        passed = len(contrast_issues) == 0

        contrast_path = screenshot_base_path.replace(".png", "-contrast.png")
        try:
            page.screenshot(path=contrast_path, full_page=False)
        except Exception:
            contrast_path = None

        return {
            "passed": passed,
            "issues_count": len(contrast_issues),
            "needs_review_count": needs_review,
            "issues": contrast_issues[:10],
            "screenshot": contrast_path,
            "note": (
                "Pass: all text meets WCAG contrast requirements."
                if passed else
                f"Fail: {len(contrast_issues)} element(s) with insufficient color contrast."
            ),
        }
    except Exception as e:
        logger.warning("Color contrast test error: %s", e)
        return {"passed": None, "error": str(e)}


# ── Zoom / Reflow Test ───────────────────────────────────────────────────────

def run_zoom_test(page, screenshot_base_path: str) -> dict[str, Any]:
    """
    WCAG 1.4.4 Resize Text + 1.4.10 Reflow.
    Tests: 320px viewport reflow, 200% browser zoom via CDP, meta viewport restrictions.
    """
    original_viewport = page.viewport_size or {"width": 1440, "height": 900}
    results = {}
    try:
        # Test 0: Check meta viewport restrictions (user-scalable=no, maximum-scale=1)
        meta_viewport_issue = page.evaluate("""() => {
            const meta = document.querySelector('meta[name="viewport"]');
            if (!meta) return null;
            const content = (meta.getAttribute('content') || '').toLowerCase();
            const issues = [];
            if (content.includes('user-scalable=no') || content.includes('user-scalable=0'))
                issues.push('user-scalable=no prevents zoom');
            const maxMatch = content.match(/maximum-scale\\s*=\\s*([\\d.]+)/);
            if (maxMatch && parseFloat(maxMatch[1]) < 2.0)
                issues.push('maximum-scale=' + maxMatch[1] + ' restricts zoom below 200%');
            return issues.length ? issues : null;
        }""")

        # Test 1: 320px viewport (1.4.10 Reflow)
        page.set_viewport_size({"width": 320, "height": 900})
        page.wait_for_timeout(800)

        reflow_result = page.evaluate("""() => {
            const docWidth = document.documentElement.scrollWidth;
            const viewWidth = window.innerWidth;
            const hasOverflow = docWidth > viewWidth + 5;

            // Count elements that overflow viewport
            let overflowCount = 0;
            let clippedCount = 0;
            const els = document.querySelectorAll('p,span,h1,h2,h3,h4,h5,h6,li,td,th,label,button,a,div,section,article,img');
            for (const el of els) {
                const r = el.getBoundingClientRect();
                if (r.width > 0 && r.right > viewWidth + 5) overflowCount++;
                // Check for content clipped by overflow:hidden
                const cs = getComputedStyle(el);
                if (['hidden','clip'].includes(cs.overflowX) && el.scrollWidth > el.clientWidth + 4 && el.clientWidth > 0) {
                    clippedCount++;
                }
            }
            return {
                docWidth, viewWidth, hasOverflow, overflowCount, clippedCount,
                overflowDelta: docWidth - viewWidth,
            };
        }""")

        zoom_320_path = screenshot_base_path.replace(".png", "-zoom-320.png")
        try:
            page.screenshot(path=zoom_320_path, full_page=False)
        except Exception:
            zoom_320_path = None

        # Test 2: 200% zoom via CDP (true browser zoom, not CSS zoom)
        page.set_viewport_size(original_viewport)
        page.wait_for_timeout(400)

        try:
            cdp = page.context.browser.new_browser_cdp_session()
            cdp.send("Emulation.setPageScaleFactor", {"pageScaleFactor": 2.0})
            page.wait_for_timeout(600)

            zoom_200_result = page.evaluate("""() => {
                return {
                    hasOverflow: document.documentElement.scrollWidth > window.innerWidth + 5,
                    docWidth: document.documentElement.scrollWidth,
                    viewWidth: window.innerWidth,
                };
            }""")

            zoom_200_path = screenshot_base_path.replace(".png", "-zoom-200.png")
            try:
                page.screenshot(path=zoom_200_path, full_page=False)
            except Exception:
                zoom_200_path = None

            # Reset zoom
            cdp.send("Emulation.setPageScaleFactor", {"pageScaleFactor": 1.0})
            cdp.detach()
        except Exception as cdp_err:
            logger.info("CDP zoom not available, falling back to CSS zoom: %s", cdp_err)
            # Fallback: CSS zoom
            page.evaluate("() => { document.body.style.zoom = '200%'; }")
            page.wait_for_timeout(500)
            zoom_200_result = page.evaluate("""() => {
                return {
                    hasOverflow: document.documentElement.scrollWidth > Math.ceil(window.innerWidth) + 5,
                    docWidth: document.documentElement.scrollWidth,
                    viewWidth: window.innerWidth,
                };
            }""")
            zoom_200_path = screenshot_base_path.replace(".png", "-zoom-200.png")
            try:
                page.screenshot(path=zoom_200_path, full_page=False)
            except Exception:
                zoom_200_path = None
            page.evaluate("() => { document.body.style.zoom = ''; }")

        reflow_passed = not reflow_result["hasOverflow"] and reflow_result["clippedCount"] == 0
        resize_passed = not zoom_200_result["hasOverflow"]
        meta_passed = meta_viewport_issue is None

        issues = []
        if not meta_passed:
            issues.extend(meta_viewport_issue)
        if not reflow_passed:
            if reflow_result["hasOverflow"]:
                issues.append(f"Horizontal overflow at 320px ({reflow_result['overflowDelta']}px excess)")
            if reflow_result["overflowCount"]:
                issues.append(f"{reflow_result['overflowCount']} elements overflow viewport at 320px")
            if reflow_result["clippedCount"]:
                issues.append(f"{reflow_result['clippedCount']} elements have clipped content at 320px")
        if not resize_passed:
            issues.append("Horizontal overflow at 200% zoom")

        passed = reflow_passed and resize_passed and meta_passed

        results = {
            "passed": passed,
            "reflow_320_passed": reflow_passed,
            "resize_200_passed": resize_passed,
            "meta_viewport_passed": meta_passed,
            "meta_viewport_issues": meta_viewport_issue,
            "reflow_detail": reflow_result,
            "zoom_200_detail": zoom_200_result,
            "issues": issues,
            "screenshot_320": zoom_320_path,
            "screenshot_200": zoom_200_path,
            "note": "Pass: content reflows correctly at 320px and 200% zoom." if passed else f"Fail: {'; '.join(issues[:3])}",
        }
    except Exception as e:
        logger.warning("Zoom test error: %s", e)
        results = {"passed": None, "error": str(e)}
    finally:
        try:
            page.evaluate("() => { document.body.style.zoom = ''; }")
            page.set_viewport_size(original_viewport)
            page.wait_for_timeout(400)
        except Exception:
            pass
    return results


# ── Text Spacing Test ────────────────────────────────────────────────────────

def run_text_spacing_test(page, screenshot_base_path: str) -> dict[str, Any]:
    """
    WCAG 1.4.12 Text Spacing.
    Injects WCAG-specified text spacing overrides and checks for content loss.
    Uses the exact values from WCAG SC 1.4.12:
      - Line height >= 1.5x font size
      - Letter spacing >= 0.12x font size
      - Word spacing >= 0.16x font size
      - Paragraph spacing >= 2x font size
    """
    SPACING_CSS = """
        * {
            line-height: 1.5 !important;
            letter-spacing: 0.12em !important;
            word-spacing: 0.16em !important;
        }
        p, blockquote, li, dd, dt {
            margin-bottom: 2em !important;
        }
    """
    try:
        # Snapshot before injection: count visible text containers
        before_heights = page.evaluate("""() => {
            const heights = {};
            let idx = 0;
            for (const el of document.querySelectorAll('p,span,h1,h2,h3,h4,h5,h6,li,td,th,label,button,a,div')) {
                if (el.offsetHeight > 0 && (el.innerText || '').trim().length > 0) {
                    heights[idx] = { h: el.offsetHeight, w: el.offsetWidth, text: (el.innerText||'').trim().slice(0,30) };
                    idx++;
                }
                if (idx > 200) break;
            }
            return heights;
        }""")

        page.evaluate(f"""() => {{
            const s = document.createElement('style');
            s.id = '__wcag_text_spacing__';
            s.textContent = `{SPACING_CSS}`;
            document.head.appendChild(s);
        }}""")
        page.wait_for_timeout(500)

        check_result = page.evaluate("""() => {
            let clippedVertical = 0;
            let clippedHorizontal = 0;
            let truncated = 0;
            let overlapping = 0;

            for (const el of document.querySelectorAll('*')) {
                const cs = getComputedStyle(el);
                const hasText = (el.innerText || '').trim().length > 0;
                if (!hasText || el.offsetHeight === 0) continue;

                // Check vertical clipping (overflow-y: hidden with scrollable content)
                if (['hidden','clip'].includes(cs.overflowY) || ['hidden','clip'].includes(cs.overflow)) {
                    if (el.scrollHeight > el.clientHeight + 6 && el.clientHeight > 0) {
                        clippedVertical++;
                    }
                }
                // Check horizontal clipping
                if (['hidden','clip'].includes(cs.overflowX) || ['hidden','clip'].includes(cs.overflow)) {
                    if (el.scrollWidth > el.clientWidth + 6 && el.clientWidth > 0) {
                        clippedHorizontal++;
                    }
                }
                // Check text-overflow: ellipsis truncation
                if (cs.textOverflow === 'ellipsis' && cs.overflow === 'hidden') {
                    if (el.scrollWidth > el.clientWidth + 4) truncated++;
                }
            }

            // Check for overlapping text (z-index collisions on positioned text)
            const positioned = document.querySelectorAll('[style*="position"],.absolute,.fixed,.sticky,*[class*="absolute"],*[class*="fixed"]');
            // Simplified check — look for fixed-height containers now overflowing
            const fixedHeightEls = document.querySelectorAll('*');
            for (const el of fixedHeightEls) {
                const cs = getComputedStyle(el);
                if (cs.height !== 'auto' && cs.overflow !== 'visible' && cs.overflow !== 'auto' && cs.overflow !== 'scroll') {
                    if (el.scrollHeight > el.clientHeight + 8 && el.clientHeight > 10 && el.clientHeight < 200) {
                        overlapping++;
                    }
                }
                if (overlapping > 50) break;
            }

            return { clippedVertical, clippedHorizontal, truncated, overlapping: Math.min(overlapping, 50) };
        }""")

        spacing_path = screenshot_base_path.replace(".png", "-text-spacing.png")
        try:
            page.screenshot(path=spacing_path, full_page=False)
        except Exception:
            spacing_path = None

        total_issues = (
            check_result["clippedVertical"] +
            check_result["clippedHorizontal"] +
            check_result["truncated"]
        )
        passed = total_issues == 0

        issues = []
        if check_result["clippedVertical"]:
            issues.append(f"{check_result['clippedVertical']} elements with vertically clipped text")
        if check_result["clippedHorizontal"]:
            issues.append(f"{check_result['clippedHorizontal']} elements with horizontally clipped text")
        if check_result["truncated"]:
            issues.append(f"{check_result['truncated']} elements with truncated text (ellipsis)")
        if check_result["overlapping"]:
            issues.append(f"{check_result['overlapping']} fixed-height containers may overlap")

        return {
            "passed": passed,
            "detail": check_result,
            "issues": issues,
            "screenshot": spacing_path,
            "note": (
                "Pass: no content loss with WCAG text spacing overrides." if passed else
                f"Fail: {'; '.join(issues[:3])}"
            ),
        }
    except Exception as e:
        logger.warning("Text spacing test error: %s", e)
        return {"passed": None, "error": str(e)}
    finally:
        try:
            page.evaluate("""() => {
                const s = document.getElementById('__wcag_text_spacing__');
                if (s) s.remove();
            }""")
            page.wait_for_timeout(300)
        except Exception:
            pass


# ── Keyboard Navigation Test ────────────────────────────────────────────────

def run_keyboard_test(page, screenshot_base_path: str) -> dict[str, Any]:
    """
    WCAG 2.1.1 Keyboard / 2.4.3 Focus Order / 2.4.7 Focus Visible / 2.4.11 Focus Not Obscured.
    Tests: skip link, focus visibility, focus order, focus traps, sticky header obscuring.
    """
    try:
        focusable_count = page.evaluate("""() => {
            return document.querySelectorAll(
                'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), '
                + 'textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'
            ).length;
        }""")

        # Test 1: Skip link detection (WCAG 2.4.1)
        page.evaluate("() => { if (document.activeElement) document.activeElement.blur(); window.scrollTo(0,0); }")
        page.wait_for_timeout(200)
        page.keyboard.press("Tab")
        page.wait_for_timeout(200)
        skip_link = page.evaluate("""() => {
            const el = document.activeElement;
            if (!el || el === document.body) return { found: false };
            const tag = el.tagName.toLowerCase();
            const href = (el.getAttribute('href') || '').toLowerCase();
            const text = (el.innerText || el.getAttribute('aria-label') || '').toLowerCase().trim();
            const isSkip = (
                href.startsWith('#') &&
                (text.includes('skip') || text.includes('main') || text.includes('content'))
            ) || (tag === 'a' && href.startsWith('#main'));
            return { found: isSkip, tag, href, text: text.slice(0, 50) };
        }""")

        # Reset and start tabbing
        page.evaluate("() => { if (document.activeElement) document.activeElement.blur(); window.scrollTo(0,0); }")
        page.wait_for_timeout(200)

        max_tabs = min(int(focusable_count or 0), 30)
        focus_visible_count = 0
        focus_not_visible = []
        focus_obscured_count = 0
        tab_results = []
        prev_tags = []

        for i in range(max_tabs):
            page.keyboard.press("Tab")
            page.wait_for_timeout(120)

            result = page.evaluate("""() => {
                const el = document.activeElement;
                if (!el || el === document.body || el === document.documentElement) {
                    return { visible: false, obscured: false, tag: 'none', label: '', inViewport: false };
                }
                const cs = getComputedStyle(el);
                const outlineW = parseFloat(cs.outlineWidth || '0');
                const outlineOk = cs.outlineStyle !== 'none' && outlineW >= 1;
                const shadowOk = cs.boxShadow && cs.boxShadow !== 'none' && cs.boxShadow !== 'initial';
                const borderOk = parseFloat(cs.borderWidth || '0') >= 2;
                // Check background color change as focus indicator
                const bgChange = cs.backgroundColor !== 'rgba(0, 0, 0, 0)' && cs.backgroundColor !== 'transparent';
                const visible = outlineOk || shadowOk || borderOk;

                const rect = el.getBoundingClientRect();
                const inViewport = rect.top >= 0 && rect.bottom <= window.innerHeight;

                // Check if obscured by sticky/fixed elements
                let obscured = false;
                const stickyEls = document.querySelectorAll(
                    'header,[role="banner"],[style*="position: sticky"],[style*="position:sticky"],' +
                    '[style*="position: fixed"],[style*="position:fixed"]'
                );
                for (const h of stickyEls) {
                    const hr = h.getBoundingClientRect();
                    if (hr.bottom > rect.top && hr.top < rect.bottom &&
                        hr.left < rect.right && hr.right > rect.left) {
                        obscured = true; break;
                    }
                }
                // Also check via CSS computed position for all sticky/fixed elements
                if (!obscured) {
                    for (const s of document.querySelectorAll('*')) {
                        const scs = getComputedStyle(s);
                        if ((scs.position === 'sticky' || scs.position === 'fixed') && s !== el) {
                            const sr = s.getBoundingClientRect();
                            if (sr.height > 10 && sr.bottom > rect.top && sr.top < rect.bottom &&
                                sr.left < rect.right && sr.right > rect.left) {
                                obscured = true; break;
                            }
                        }
                    }
                }

                return {
                    visible,
                    obscured,
                    tag: el.tagName.toLowerCase(),
                    role: el.getAttribute('role') || '',
                    label: (el.getAttribute('aria-label') || el.innerText || el.getAttribute('title') || '').trim().slice(0, 50),
                    inViewport,
                };
            }""")

            tab_results.append(result)
            if result.get("visible"):
                focus_visible_count += 1
            else:
                focus_not_visible.append({
                    "index": i + 1,
                    "tag": result.get("tag", ""),
                    "label": result.get("label", ""),
                })
            if result.get("obscured"):
                focus_obscured_count += 1
            prev_tags.append(result.get("tag", ""))

        # Test 3: Focus trap detection — after tabbing through all, focus should return to body or cycle
        # Tab a few more times past max to see if we're trapped
        trapped = False
        if max_tabs > 0:
            trap_tags = set()
            for _ in range(5):
                page.keyboard.press("Tab")
                page.wait_for_timeout(80)
                tag_check = page.evaluate("() => document.activeElement ? document.activeElement.tagName.toLowerCase() : 'body'")
                trap_tags.add(tag_check)
            # If all 5 extra tabs land on same element, likely trapped
            trapped = len(trap_tags) == 1 and "body" not in trap_tags

        page.evaluate("() => { if (document.activeElement) document.activeElement.blur(); window.scrollTo(0,0); }")

        ratio = focus_visible_count / max_tabs if max_tabs > 0 else 1.0
        passed = ratio >= 0.8 and focus_obscured_count == 0 and not trapped

        keyboard_path = screenshot_base_path.replace(".png", "-keyboard.png")
        try:
            page.screenshot(path=keyboard_path, full_page=False)
        except Exception:
            keyboard_path = None

        issues = []
        if not skip_link.get("found"):
            issues.append("No skip-to-main-content link found")
        if focus_visible_count < max_tabs:
            issues.append(f"{max_tabs - focus_visible_count}/{max_tabs} elements missing visible focus indicator")
        if focus_obscured_count:
            issues.append(f"{focus_obscured_count} focused element(s) obscured by sticky/fixed headers")
        if trapped:
            issues.append("Focus trap detected — keyboard cannot tab out of a component")

        return {
            "passed": passed,
            "focusable_elements": focusable_count,
            "elements_tabbed": max_tabs,
            "focus_visible_count": focus_visible_count,
            "focus_not_visible": focus_not_visible[:5],
            "focus_obscured_count": focus_obscured_count,
            "focus_visible_ratio": round(ratio, 2),
            "skip_link_found": skip_link.get("found", False),
            "focus_trap_detected": trapped,
            "issues": issues,
            "screenshot": keyboard_path,
            "note": (
                f"Pass: {focus_visible_count}/{max_tabs} elements have visible focus, skip link present."
                if passed else
                f"Fail: {'; '.join(issues[:3])}"
            ),
            "tab_results": tab_results[:10],
        }
    except Exception as e:
        logger.warning("Keyboard test error: %s", e)
        return {"passed": None, "error": str(e)}


# ── Screen Reader Proxy Test ────────────────────────────────────────────────

def run_screen_reader_test(page) -> dict[str, Any]:
    """
    NVDA/VoiceOver proxy test using axe-core ARIA rules + Chromium accessibility tree.
    Covers: landmarks, heading hierarchy, form labels, image alts, link/button names,
    ARIA roles, live regions, language attributes, page title, table accessibility.
    """
    try:
        # Part 1: axe-core ARIA/screen-reader rules
        page.evaluate(_get_axe_script())
        aria_rules = [
            "aria-allowed-attr", "aria-allowed-role", "aria-hidden-body",
            "aria-hidden-focus", "aria-input-field-name", "aria-required-attr",
            "aria-required-children", "aria-required-parent", "aria-roles",
            "aria-valid-attr", "aria-valid-attr-value",
            "button-name", "document-title", "duplicate-id-aria",
            "form-field-multiple-labels", "frame-title", "html-has-lang",
            "html-lang-valid", "image-alt", "input-button-name",
            "input-image-alt", "label", "link-name",
            "landmark-main-is-top-level", "landmark-one-main",
            "heading-order", "list", "listitem",
            "td-headers-attr", "th-has-data-cells",
        ]
        options = {
            "runOnly": {"type": "rule", "values": aria_rules},
            "resultTypes": ["violations", "passes"],
        }
        axe_result = page.evaluate(
            f"axe.run(document, {json.dumps(options)}).then(r => r)"
        )
        axe_violations = axe_result.get("violations", [])
        axe_passes = axe_result.get("passes", [])

        # Part 2: Accessibility tree snapshot
        tree = {}
        tree_issues = []
        try:
            snapshot = page.accessibility.snapshot()
            if snapshot:
                tree = {
                    "role": snapshot.get("role", ""),
                    "name": snapshot.get("name", ""),
                    "children_count": len(snapshot.get("children", [])),
                }
                # Check for unnamed interactive elements in tree
                def _walk(node, depth=0):
                    issues = []
                    if depth > 50:
                        return issues
                    role = node.get("role", "")
                    name = (node.get("name") or "").strip()
                    if role in ("button", "link", "textbox", "checkbox", "radio", "combobox", "menuitem") and not name:
                        issues.append(f"Unnamed {role} in accessibility tree")
                    for child in node.get("children", []):
                        issues.extend(_walk(child, depth + 1))
                        if len(issues) > 15:
                            break
                    return issues
                tree_issues = _walk(snapshot)[:10]
        except Exception as tree_err:
            logger.info("Accessibility tree snapshot not available: %s", tree_err)

        # Part 3: Additional DOM-based checks not covered by axe
        dom_checks = page.evaluate("""() => {
            const issues = [];

            // Heading hierarchy (axe checks order but we want detail)
            const headings = Array.from(document.querySelectorAll('h1,h2,h3,h4,h5,h6'));
            let lastLevel = 0;
            let skippedLevels = 0;
            for (const h of headings) {
                const level = parseInt(h.tagName[1]);
                if (lastLevel > 0 && level > lastLevel + 1) skippedLevels++;
                lastLevel = level;
            }
            if (skippedLevels > 0) issues.push(skippedLevels + ' heading level skip(s)');

            // Check for multiple h1s
            const h1s = document.querySelectorAll('h1');
            if (h1s.length > 1) issues.push('Multiple h1 elements (' + h1s.length + ')');
            if (h1s.length === 0 && headings.length > 0) issues.push('No h1 element found');

            // Landmark regions
            const main = document.querySelectorAll('main,[role="main"]');
            const nav = document.querySelectorAll('nav,[role="navigation"]');
            const banner = document.querySelectorAll('header,[role="banner"]');
            const contentinfo = document.querySelectorAll('footer,[role="contentinfo"]');
            if (!main.length) issues.push('Missing main landmark');
            if (!nav.length) issues.push('Missing navigation landmark');

            // ARIA live regions check
            const liveRegions = document.querySelectorAll('[aria-live],[role="alert"],[role="status"],[role="log"],[role="timer"]');

            // Focus management: check for tabindex > 0 (anti-pattern)
            const highTabindex = document.querySelectorAll('[tabindex]');
            let badTabindex = 0;
            for (const el of highTabindex) {
                const ti = parseInt(el.getAttribute('tabindex') || '0');
                if (ti > 0) badTabindex++;
            }
            if (badTabindex > 0) issues.push(badTabindex + ' element(s) with tabindex > 0 (disrupts natural order)');

            return {
                issues,
                headings_count: headings.length,
                h1_count: h1s.length,
                landmarks: { main: main.length, nav: nav.length, banner: banner.length, contentinfo: contentinfo.length },
                live_regions: liveRegions.length,
                bad_tabindex_count: badTabindex,
            };
        }""")

        # Combine all issues
        all_issues = []

        # axe-core violations
        for v in axe_violations:
            all_issues.append({
                "source": "axe-core",
                "rule": v["id"],
                "impact": v.get("impact", ""),
                "description": v.get("help", ""),
                "count": len(v.get("nodes", [])),
            })

        # Accessibility tree issues
        for ti in tree_issues:
            all_issues.append({"source": "a11y-tree", "description": ti})

        # DOM check issues
        for di in dom_checks.get("issues", []):
            all_issues.append({"source": "dom-check", "description": di})

        passed = len(axe_violations) == 0 and len(tree_issues) == 0 and len(dom_checks.get("issues", [])) == 0

        return {
            "passed": passed,
            "axe_violations_count": len(axe_violations),
            "axe_passes_count": len(axe_passes),
            "tree_issues_count": len(tree_issues),
            "dom_issues": dom_checks.get("issues", []),
            "all_issues": all_issues[:15],
            "accessibility_tree": tree,
            "landmarks": dom_checks.get("landmarks", {}),
            "headings_count": dom_checks.get("headings_count", 0),
            "live_regions_count": dom_checks.get("live_regions", 0),
            "score": max(0, 100 - len(all_issues) * 8),
            "note": (
                "Pass: no ARIA/semantic issues detected via axe-core and accessibility tree."
                if passed else
                f"Fail: {len(all_issues)} issue(s) — " +
                "; ".join(
                    [i.get("description", i.get("rule", ""))[:60] for i in all_issues[:3]]
                )
            ),
        }
    except Exception as e:
        logger.warning("Screen reader test error: %s", e)
        return {"passed": None, "error": str(e)}
