"""Live browser accessibility scan — opens a visible browser window."""
from __future__ import annotations

import json
import time
from pathlib import Path
from playwright.sync_api import sync_playwright

URL = "https://uat.hdfcsky.com/sky/login"
ARTIFACTS = Path("artifacts/live-scan")
ARTIFACTS.mkdir(parents=True, exist_ok=True)

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


def main():
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=False, slow_mo=300)
        context = browser.new_context(
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
        context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
            Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
            window.chrome = { runtime: {} };
        """)

        page = context.new_page()

        # ---- Step 1: Navigate ----
        print("\n🌐 Opening", URL)
        try:
            page.goto(URL, wait_until="networkidle", timeout=45000)
        except Exception:
            print("⚠️  networkidle timeout, using domcontentloaded fallback")
            page.goto(URL, wait_until="domcontentloaded", timeout=30000)

        page.wait_for_timeout(2000)
        title = page.title()
        print(f"📄 Page loaded: {title}")

        # ---- Step 2: Full page screenshot ----
        ss1 = str(ARTIFACTS / "01-page-loaded.png")
        page.screenshot(path=ss1, full_page=True)
        print(f"📸 Screenshot saved: {ss1}")

        # ---- Step 3: Check basic accessibility attributes ----
        print("\n─── BASIC ACCESSIBILITY AUDIT ───")

        audit = page.evaluate("""() => {
            const results = {};

            // Lang attribute
            results.html_lang = document.documentElement.lang || '(missing)';

            // Page title
            results.title = document.title || '(missing)';

            // Images missing alt
            const imgs = Array.from(document.querySelectorAll('img'));
            results.total_images = imgs.length;
            results.images_missing_alt = imgs.filter(i => !i.hasAttribute('alt')).map(i => ({
                src: (i.src || '').substring(0, 80),
                class: i.className || '',
            }));

            // Form inputs missing labels
            const inputs = Array.from(document.querySelectorAll('input, select, textarea'));
            results.total_inputs = inputs.length;
            results.inputs_missing_label = inputs.filter(inp => {
                const id = inp.id;
                const hasLabel = id && document.querySelector(`label[for="${id}"]`);
                const hasAria = inp.getAttribute('aria-label') || inp.getAttribute('aria-labelledby');
                const hasPlaceholder = inp.getAttribute('placeholder');
                return !hasLabel && !hasAria && !hasPlaceholder;
            }).map(inp => ({
                type: inp.type || inp.tagName.toLowerCase(),
                id: inp.id || '',
                name: inp.name || '',
            }));

            // Buttons missing accessible name
            const buttons = Array.from(document.querySelectorAll('button, [role="button"]'));
            results.total_buttons = buttons.length;
            results.buttons_missing_name = buttons.filter(b => {
                const text = (b.innerText || '').trim();
                const aria = b.getAttribute('aria-label') || '';
                const title = b.getAttribute('title') || '';
                return !text && !aria && !title;
            }).map(b => ({
                tag: b.tagName.toLowerCase(),
                class: b.className || '',
                id: b.id || '',
            }));

            // Links
            const links = Array.from(document.querySelectorAll('a[href]'));
            results.total_links = links.length;
            results.empty_links = links.filter(a => !(a.innerText || '').trim() && !a.getAttribute('aria-label')).map(a => ({
                href: (a.href || '').substring(0, 80),
                class: a.className || '',
            }));

            // Headings hierarchy
            const headings = Array.from(document.querySelectorAll('h1,h2,h3,h4,h5,h6'));
            results.headings = headings.map(h => ({
                level: h.tagName,
                text: (h.innerText || '').substring(0, 60).trim(),
            }));

            // ARIA landmarks
            const landmarks = document.querySelectorAll('[role="main"],[role="navigation"],[role="banner"],[role="contentinfo"],main,nav,header,footer');
            results.landmarks = Array.from(landmarks).map(l => ({
                tag: l.tagName.toLowerCase(),
                role: l.getAttribute('role') || '',
            }));

            // Skip link
            const skipLinks = Array.from(document.querySelectorAll('a[href^="#"]')).filter(a => {
                const t = (a.innerText || '').toLowerCase();
                return t.includes('skip') || t.includes('main content');
            });
            results.skip_link = skipLinks.length > 0 ? skipLinks[0].innerText.trim() : null;

            // Color contrast — check inline styles only
            results.viewport_meta = (() => {
                const m = document.querySelector('meta[name="viewport"]');
                return m ? m.content : '(missing)';
            })();

            // Focus outline check
            results.focus_outline_suppressed = (() => {
                const all = document.querySelectorAll('*');
                let suppressed = 0;
                for (let i = 0; i < Math.min(all.length, 500); i++) {
                    const cs = getComputedStyle(all[i]);
                    if (cs.outlineStyle === 'none' && cs.outline === '0px none rgb(0, 0, 0)') {
                        // Check if this is an interactive element
                        const tag = all[i].tagName.toLowerCase();
                        if (['a','button','input','select','textarea'].includes(tag)) {
                            suppressed++;
                        }
                    }
                }
                return suppressed;
            })();

            return results;
        }""")

        print(f"  HTML lang:         {audit['html_lang']}")
        print(f"  Page title:        {audit['title']}")
        print(f"  Viewport meta:     {audit['viewport_meta']}")
        print(f"  Skip link:         {audit['skip_link'] or '❌ MISSING'}")
        print(f"  Headings:          {len(audit['headings'])}")
        for h in audit["headings"]:
            print(f"    {h['level']}: {h['text']}")
        print(f"  Landmarks:         {len(audit['landmarks'])}")
        for lm in audit["landmarks"]:
            print(f"    <{lm['tag']}> role={lm['role']}")
        print(f"  Images:            {audit['total_images']} total, {len(audit['images_missing_alt'])} missing alt")
        for img in audit["images_missing_alt"][:5]:
            print(f"    ❌ {img['src']}")
        print(f"  Inputs:            {audit['total_inputs']} total, {len(audit['inputs_missing_label'])} missing label")
        for inp in audit["inputs_missing_label"][:5]:
            print(f"    ❌ {inp['type']} name={inp['name']} id={inp['id']}")
        print(f"  Buttons:           {audit['total_buttons']} total, {len(audit['buttons_missing_name'])} missing name")
        for btn in audit["buttons_missing_name"][:5]:
            print(f"    ❌ <{btn['tag']}> class={btn['class']}")
        print(f"  Links:             {audit['total_links']} total, {len(audit['empty_links'])} empty/unlabeled")
        print(f"  Focus outline suppressed on: {audit['focus_outline_suppressed']} interactive elements")

        # ---- Step 4: Keyboard navigation probe ----
        print("\n─── KEYBOARD NAVIGATION TEST ───")
        print("Pressing Tab through the page...")

        # Click body to reset focus
        body = page.query_selector("body")
        if body:
            try:
                body.click(force=True)
            except Exception:
                pass

        focus_trail = []
        seen = set()
        for i in range(50):
            page.keyboard.press("Tab")
            page.wait_for_timeout(200)

            info = page.evaluate("""() => {
                const el = document.activeElement;
                if (!el || el === document.body) return null;
                const rect = el.getBoundingClientRect();
                const cs = getComputedStyle(el);
                return {
                    tag: el.tagName.toLowerCase(),
                    role: el.getAttribute('role') || '',
                    aria_label: el.getAttribute('aria-label') || '',
                    text: (el.innerText || '').substring(0, 80).trim(),
                    id: el.id || '',
                    type: el.type || '',
                    visible: rect.width > 0 && rect.height > 0,
                    outline: cs.outlineStyle + ' ' + cs.outlineWidth,
                    box_shadow: cs.boxShadow !== 'none' ? 'yes' : 'no',
                };
            }""")

            if info is None:
                break

            key = f"{info['tag']}#{info['id']}" if info['id'] else f"{info['tag']}.{info.get('text','')[:20]}"
            if key in seen:
                break
            seen.add(key)
            focus_trail.append(info)

            label = info['aria_label'] or info['text'][:40] or info['id'] or info['type']
            vis = "✅" if info['visible'] else "👻"
            outline = "🔵" if info['outline'] != 'none 0px' or info['box_shadow'] == 'yes' else "❌"
            print(f"  Tab {i+1:2d}: {vis} {outline} <{info['tag']}> {label}")

        # Screenshot showing focus state
        ss2 = str(ARTIFACTS / "02-after-tab-navigation.png")
        page.screenshot(path=ss2)
        print(f"\n📸 Screenshot after tabbing: {ss2}")
        print(f"   Total focusable elements reached: {len(focus_trail)}")

        # ---- Step 5: Focus visibility test ----
        print("\n─── FOCUS VISIBILITY TEST ───")
        focus_vis = page.evaluate("""() => {
            const els = Array.from(document.querySelectorAll(
                'a[href], button, input, select, textarea, [tabindex]:not([tabindex="-1"])'
            )).slice(0, 30);
            const results = [];
            for (const el of els) {
                el.focus();
                const cs = getComputedStyle(el);
                const outlineOk = cs.outlineStyle !== 'none' && parseFloat(cs.outlineWidth) > 0;
                const shadowOk = cs.boxShadow && cs.boxShadow !== 'none';
                const label = el.getAttribute('aria-label') || (el.innerText || '').substring(0, 40).trim() || el.id || el.tagName;
                results.push({
                    element: `<${el.tagName.toLowerCase()}> ${label}`,
                    has_focus_indicator: outlineOk || shadowOk,
                    outline: cs.outlineStyle + ' ' + cs.outlineWidth + ' ' + cs.outlineColor,
                });
            }
            document.activeElement?.blur?.();
            return results;
        }""")

        pass_count = sum(1 for r in focus_vis if r["has_focus_indicator"])
        fail_count = len(focus_vis) - pass_count
        print(f"  Tested {len(focus_vis)} elements: {pass_count} ✅ pass, {fail_count} ❌ fail")
        for r in focus_vis:
            icon = "✅" if r["has_focus_indicator"] else "❌"
            print(f"    {icon} {r['element']}")

        # ---- Step 6: Save full report ----
        report = {
            "url": URL,
            "title": title,
            "audit": audit,
            "focus_trail": focus_trail,
            "focus_visibility": focus_vis,
            "screenshots": [ss1, ss2],
        }
        report_path = str(ARTIFACTS / "live-report.json")
        with open(report_path, "w") as f:
            json.dump(report, f, indent=2, default=str)
        print(f"\n📋 Full report saved: {report_path}")

        print("\n⏸️  Browser stays open for 30 seconds for you to inspect...")
        time.sleep(30)

        context.close()
        browser.close()
        print("🏁 Done.")


if __name__ == "__main__":
    main()
