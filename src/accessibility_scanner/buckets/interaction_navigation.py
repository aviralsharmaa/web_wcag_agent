from __future__ import annotations

import re

from ..html_utils import DOMSnapshot
from ..models import CheckpointResult, CheckpointStatus, PageArtifact
from .base import result

# Patterns indicating hover-dependent UI components
_HOVER_MENU_CLASSES = re.compile(
    r"(dropdown|drop-down|submenu|sub-menu|mega-?menu|nav-flyout|hover-menu|popover-menu)",
    re.IGNORECASE,
)
_FLIP_CARD_CLASSES = re.compile(
    r"(flip-?card|card-flip|hover-card|hover-flip|flip-?box|flip-?container)",
    re.IGNORECASE,
)
# Mouse-only event attributes (inline handlers)
_MOUSE_ONLY_ATTRS = {"onmouseover", "onmouseenter", "onmousemove"}
_KEYBOARD_ATTRS = {"onfocus", "onblur", "onkeydown", "onkeyup", "onkeypress"}


def _detect_hover_only_issues(snapshot: DOMSnapshot, html_lower: str) -> list[str]:
    """Detect elements that rely on hover/mouse but lack keyboard support."""
    issues: list[str] = []

    # (a) Elements with inline mouse handlers but no keyboard handler / tabindex
    for node in snapshot.nodes:
        has_mouse = any(attr in node.attrs for attr in _MOUSE_ONLY_ATTRS)
        if not has_mouse:
            continue
        has_keyboard = any(attr in node.attrs for attr in _KEYBOARD_ATTRS)
        has_tabindex = "tabindex" in node.attrs
        is_focusable = node.tag in {"a", "button", "input", "select", "textarea", "summary"}
        if not has_keyboard and not has_tabindex and not is_focusable:
            issues.append(
                f"<{node.tag}> with mouse event handler (onmouseover/onmouseenter) "
                "but no keyboard handler or tabindex"
            )

    # (b) Dropdown/submenu patterns — <li> with nested <ul> and hover class
    for idx, node in enumerate(snapshot.nodes):
        if node.tag != "li":
            continue
        classes = node.attrs.get("class", "")
        if not _HOVER_MENU_CLASSES.search(classes):
            continue
        # Check if this <li> has a nested <ul>/<div> (submenu) but no
        # aria-expanded, role=menu, or keyboard support
        has_aria_expanded = "aria-expanded" in node.attrs or "aria-haspopup" in node.attrs
        has_tabindex = "tabindex" in node.attrs
        if not has_aria_expanded and not has_tabindex:
            issues.append(
                f"Dropdown menu item (class='{classes[:40]}') lacks aria-expanded/aria-haspopup "
                "and tabindex for keyboard access"
            )

    # (c) Hover-dependent CSS patterns in <style> blocks or inline styles
    # Look for :hover that shows/reveals content (display/visibility/opacity)
    hover_reveal_count = len(re.findall(
        r":hover\s*\{[^}]*(display\s*:\s*(?!none)|visibility\s*:\s*visible|opacity\s*:\s*1)",
        html_lower,
    ))

    # (d) Flip card patterns
    flip_cards = [
        node for node in snapshot.nodes
        if _FLIP_CARD_CLASSES.search(node.attrs.get("class", ""))
    ]
    for card in flip_cards:
        has_tabindex = "tabindex" in card.attrs
        has_keyboard = any(attr in card.attrs for attr in _KEYBOARD_ATTRS)
        is_focusable = card.tag in {"a", "button"}
        if not has_tabindex and not has_keyboard and not is_focusable:
            issues.append(
                f"Flip card element (class='{card.attrs.get('class', '')[:40]}') "
                "not keyboard accessible (no tabindex or keyboard handler)"
            )

    # (e) Nav items that are only <li> > <a> with nested submenus but
    #     the parent <li> handles hover to show the submenu
    nav_lis = [
        node for node in snapshot.nodes
        if node.tag == "li" and snapshot.has_ancestor_tag(node, {"nav"})
    ]
    for li in nav_lis:
        li_idx = snapshot.nodes.index(li)
        children = snapshot._children_of(li_idx)
        has_nested_list = any(c.tag in {"ul", "ol", "div"} for c in children)
        if not has_nested_list:
            continue
        # Sub-navigation detected — check if the trigger link has aria-expanded
        trigger_links = [c for c in children if c.tag in {"a", "button"}]
        if trigger_links:
            trigger = trigger_links[0]
            if "aria-expanded" not in trigger.attrs and "aria-haspopup" not in trigger.attrs:
                text = trigger.text.strip()[:30] or trigger.attrs.get("href", "")[:30]
                issues.append(
                    f"Nav menu item '{text}' has sub-navigation but trigger lacks "
                    "aria-expanded/aria-haspopup for keyboard disclosure"
                )

    return issues


def analyze_interaction_navigation(page: PageArtifact) -> list[CheckpointResult]:
    m = page.interaction_metrics
    snapshot = DOMSnapshot.from_html(page.html)
    html_lower = page.html.lower()
    findings: list[CheckpointResult] = []

    # -- 2.1.1  Keyboard Operability -----------------------------------
    focus_trail = m.get("focus_trail")
    keyboard_access_ok = m.get("keyboard_access_ok")
    hover_issues = _detect_hover_only_issues(snapshot, html_lower)

    if focus_trail is not None:
        # We have real browser tab-order data
        trail_len = len(focus_trail)
        interactive_count = m.get("interactive_count", 0)
        if trail_len == 0 and interactive_count > 0:
            msg = f"No elements received focus via Tab key despite {interactive_count} interactive elements on page."
            if hover_issues:
                msg += f" Also detected {len(hover_issues)} hover-only patterns: {'; '.join(hover_issues[:3])}."
            findings.append(result("2.1.1", CheckpointStatus.FAIL, page, msg))
        elif trail_len > 0:
            off_screen = [e for e in focus_trail if not e.get("visible", True)]
            if off_screen or hover_issues:
                parts = []
                if off_screen:
                    parts.append(f"{len(off_screen)} focused elements are invisible/off-screen")
                if hover_issues:
                    parts.append(f"{len(hover_issues)} hover-only interactive patterns detected: {'; '.join(hover_issues[:3])}")
                findings.append(
                    result("2.1.1", CheckpointStatus.FAIL, page,
                           f"Keyboard issues: {'; '.join(parts)}.")
                )
            else:
                findings.append(
                    result("2.1.1", CheckpointStatus.PASS, page,
                           f"Keyboard navigation reached {trail_len} elements via Tab.")
                )
        else:
            findings.append(
                result("2.1.1", CheckpointStatus.PASS, page, "No interactive elements; keyboard operability N/A.")
            )
    elif keyboard_access_ok is not None:
        if not keyboard_access_ok or hover_issues:
            msg = "Keyboard operability issues detected."
            if hover_issues:
                msg = f"Detected {len(hover_issues)} hover-only patterns: {'; '.join(hover_issues[:3])}."
            findings.append(result("2.1.1", CheckpointStatus.FAIL, page, msg))
        else:
            findings.append(result("2.1.1", CheckpointStatus.PASS, page, "Keyboard operability metric evaluated (heuristic)."))
    elif hover_issues:
        findings.append(
            result("2.1.1", CheckpointStatus.FAIL, page,
                   f"Detected {len(hover_issues)} hover-only interactive patterns without keyboard support: "
                   f"{'; '.join(hover_issues[:3])}.")
        )
    else:
        findings.append(
            result("2.1.1", CheckpointStatus.CANNOT_VERIFY, page, "Keyboard traversal metrics unavailable.")
        )

    # -- 2.1.2  No Keyboard Trap ---------------------------------------
    trap = m.get("keyboard_trap_detected")
    if trap is None:
        findings.append(result("2.1.2", CheckpointStatus.CANNOT_VERIFY, page, "Keyboard trap metric unavailable."))
    elif trap:
        trail_len = len(m.get("focus_trail", []))
        interactive_count = m.get("interactive_count", 0)
        # Only flag as FAIL if the trap detection is definitive:
        # - focus_trail exists and got stuck (repeated same element)
        # - Very few elements reachable (<= 3) despite many interactive elements
        # Low coverage alone (e.g., 1/121) usually means the keyboard test
        # ran out of time, not that there's a trap.
        stuck_elements = m.get("keyboard_trap_stuck_elements", [])
        if stuck_elements or (trail_len <= 3 and interactive_count > 10):
            findings.append(
                result(
                    "2.1.2", CheckpointStatus.FAIL, page,
                    f"Potential keyboard trap: only {trail_len}/{interactive_count} elements reachable via Tab.",
                )
            )
        else:
            findings.append(
                result("2.1.2", CheckpointStatus.CANNOT_VERIFY, page,
                       f"Keyboard coverage low ({trail_len}/{interactive_count} elements reached); "
                       "may indicate trap or limited test time.")
            )
    else:
        findings.append(result("2.1.2", CheckpointStatus.PASS, page, "No keyboard trap detected."))

    # -- 2.1.4  Character Key Shortcuts --------------------------------
    shortcut_present = m.get("character_shortcuts_present")
    if shortcut_present is None:
        findings.append(
            result(
                "2.1.4", CheckpointStatus.CANNOT_VERIFY, page,
                "Character shortcut instrumentation unavailable.",
            )
        )
    elif not shortcut_present:
        findings.append(result("2.1.4", CheckpointStatus.NOT_APPLICABLE, page, "No character key shortcuts detected."))
    else:
        scoped = m.get("char_shortcuts_scoped")
        if scoped is None:
            findings.append(
                result(
                    "2.1.4", CheckpointStatus.CANNOT_VERIFY, page,
                    "Shortcut scoping behavior could not be verified automatically.",
                )
            )
        else:
            findings.append(
                result(
                    "2.1.4",
                    CheckpointStatus.PASS if scoped else CheckpointStatus.FAIL,
                    page, "Character shortcut scoping metric evaluated.",
                )
            )

    # -- 2.2.1  Timing Adjustable (NEW) --------------------------------
    # Check for <meta http-equiv="refresh" content="N;url=..."> where N > 0
    # content="0;url=..." is an instant redirect (not a timing issue)
    # content="N" with N > 0 is auto-refresh (FAIL)
    meta_refresh_fail = False
    for node in snapshot.find("meta"):
        if node.attrs.get("http-equiv", "").lower() == "refresh":
            content = node.attrs.get("content", "")
            # Extract the timeout value — format: "N" or "N;url=..."
            timeout_str = content.split(";")[0].strip()
            try:
                timeout_val = int(timeout_str)
                if timeout_val > 0:
                    meta_refresh_fail = True
            except ValueError:
                pass

    has_timeout_hint = any(kw in html_lower for kw in ["session-timeout", "auto-logout"])
    if meta_refresh_fail:
        findings.append(
            result("2.2.1", CheckpointStatus.FAIL, page,
                   "Detected <meta http-equiv='refresh'> with timed redirect/reload.")
        )
    elif has_timeout_hint:
        findings.append(
            result("2.2.1", CheckpointStatus.CANNOT_VERIFY, page,
                   "Detected session timeout patterns; manual verification needed for adjustability.")
        )
    else:
        findings.append(
            result("2.2.1", CheckpointStatus.PASS, page,
                   "No auto-refresh or timing-dependent content detected.")
        )

    # -- 2.2.2  Pause, Stop, Hide (NEW) --------------------------------
    has_marquee = "<marquee" in html_lower
    has_blink = "<blink" in html_lower
    has_carousel = any(kw in html_lower for kw in ["carousel", "slider", "slideshow", "auto-scroll", "autoscroll"])
    has_animation = any(kw in html_lower for kw in ["animation-iteration-count: infinite", "@keyframes"])
    if has_marquee or has_blink:
        findings.append(
            result("2.2.2", CheckpointStatus.FAIL, page,
                   f"Detected deprecated auto-moving element(s) (<marquee>/<blink>).")
        )
    elif has_carousel or has_animation:
        findings.append(
            result("2.2.2", CheckpointStatus.CANNOT_VERIFY, page,
                   "Auto-moving/animated content detected (carousel/animation); pause/stop/hide controls need manual check.")
        )
    else:
        findings.append(
            result("2.2.2", CheckpointStatus.PASS, page,
                   "No auto-moving, blinking, or scrolling content detected.")
        )

    # -- 2.3.1  Three Flashes or Below Threshold (NEW) -----------------
    findings.append(
        result("2.3.1", CheckpointStatus.CANNOT_VERIFY, page,
               "Flash/strobe detection requires visual frame-by-frame analysis; not automatable.")
    )

    # -- 2.4.1  Bypass Blocks (Skip Link) ------------------------------
    skip_link = m.get("skip_link_present")
    if skip_link is not None:
        if skip_link:
            target = m.get("skip_link_target", "")
            findings.append(
                result("2.4.1", CheckpointStatus.PASS, page, f"Skip navigation link found targeting '{target}'.")
            )
        else:
            findings.append(
                result("2.4.1", CheckpointStatus.FAIL, page, "No skip-to-content link detected.")
            )
    else:
        findings.append(
            result("2.4.1", CheckpointStatus.CANNOT_VERIFY, page, "Skip link detection unavailable.")
        )

    # -- 2.4.3  Focus Order (NEW) --------------------------------------
    focus_trail = m.get("focus_trail")
    if focus_trail is not None and len(focus_trail) > 2:
        # Check if any off-screen elements appear mid-sequence (jumpy order)
        invisible_mid = [
            e for e in focus_trail[1:-1] if not e.get("visible", True)
        ]
        if invisible_mid:
            findings.append(
                result("2.4.3", CheckpointStatus.FAIL, page,
                       f"Focus order includes {len(invisible_mid)} non-visible elements mid-sequence, "
                       "suggesting illogical tab order.")
            )
        else:
            findings.append(
                result("2.4.3", CheckpointStatus.CANNOT_VERIFY, page,
                       "Focus order covers visible elements in sequence; logical ordering requires manual verification.")
            )
    else:
        findings.append(
            result("2.4.3", CheckpointStatus.CANNOT_VERIFY, page,
                   "Insufficient focus trail to evaluate focus order.")
        )

    # -- 2.4.5  Multiple Ways (NEW) ------------------------------------
    has_nav = any(kw in html_lower for kw in ["<nav", 'role="navigation"'])
    has_search = any(kw in html_lower for kw in ['type="search"', 'role="search"', "search"])
    has_sitemap = "sitemap" in html_lower
    ways_count = sum([has_nav, has_search, has_sitemap])
    if ways_count >= 2:
        findings.append(
            result("2.4.5", CheckpointStatus.PASS, page,
                   "Multiple ways to locate page detected (navigation + search/sitemap).")
        )
    elif ways_count == 1:
        findings.append(
            result("2.4.5", CheckpointStatus.CANNOT_VERIFY, page,
                   "Only one navigation mechanism detected; verify alternative ways exist (search, sitemap, etc.).")
        )
    else:
        findings.append(
            result("2.4.5", CheckpointStatus.FAIL, page,
                   "No navigation, search, or sitemap mechanisms detected.")
        )

    # -- 2.4.7  Focus Visible ------------------------------------------
    focus_vis = m.get("focus_visible_violations")
    if focus_vis is not None:
        if len(focus_vis) > 0:
            names = ", ".join(
                f"{v.get('tag', '?')}#{v.get('id', '')}" if v.get("id") else v.get("tag", "?")
                for v in focus_vis[:5]
            )
            findings.append(
                result(
                    "2.4.7", CheckpointStatus.FAIL, page,
                    f"{len(focus_vis)} elements lack visible focus indicator: {names}.",
                )
            )
        else:
            findings.append(result("2.4.7", CheckpointStatus.PASS, page, "All sampled elements show visible focus indicator."))
    else:
        focus_vis_ok = m.get("focus_visible_ok")
        if focus_vis_ok is not None:
            findings.append(
                result(
                    "2.4.7",
                    CheckpointStatus.PASS if focus_vis_ok else CheckpointStatus.FAIL,
                    page, "Focus visibility metric evaluated.",
                )
            )
        else:
            findings.append(
                result("2.4.7", CheckpointStatus.CANNOT_VERIFY, page, "Focus visibility metrics unavailable.")
            )

    # -- 2.4.11  Focus Not Obscured (Minimum) (NEW) --------------------
    findings.append(
        result("2.4.11", CheckpointStatus.CANNOT_VERIFY, page,
               "Focus obscuration requires rendered visual analysis of overlapping elements; manual check needed.")
    )

    # -- 2.5.1  Pointer Gestures (NEW) ---------------------------------
    has_gesture_hint = any(kw in html_lower for kw in [
        "pinch", "swipe", "multitouch", "gesture", "touchmove", "touchstart",
    ])
    if has_gesture_hint:
        findings.append(
            result("2.5.1", CheckpointStatus.CANNOT_VERIFY, page,
                   "Touch/gesture event handlers detected; verify single-pointer alternatives exist.")
        )
    else:
        findings.append(
            result("2.5.1", CheckpointStatus.NOT_APPLICABLE, page,
                   "No multi-point or path-based gesture patterns detected.")
        )

    # -- 2.5.2  Pointer Cancellation (NEW) -----------------------------
    has_mousedown_action = "onmousedown" in html_lower or "mousedown" in html_lower
    if has_mousedown_action:
        findings.append(
            result("2.5.2", CheckpointStatus.CANNOT_VERIFY, page,
                   "mousedown event handlers detected; verify actions complete on up-event and can be aborted.")
        )
    else:
        findings.append(
            result("2.5.2", CheckpointStatus.PASS, page,
                   "No mousedown-triggered actions detected.")
        )

    # -- 2.5.4  Motion Actuation (NEW) ---------------------------------
    has_motion = any(kw in html_lower for kw in [
        "devicemotion", "deviceorientation", "accelerometer", "gyroscope", "shake",
    ])
    if has_motion:
        findings.append(
            result("2.5.4", CheckpointStatus.CANNOT_VERIFY, page,
                   "Device motion/orientation handlers detected; verify UI alternative and disable option exist.")
        )
    else:
        findings.append(
            result("2.5.4", CheckpointStatus.NOT_APPLICABLE, page,
                   "No device motion/orientation patterns detected.")
        )

    # -- 2.5.7  Dragging Movements (NEW) -------------------------------
    has_drag = any(kw in html_lower for kw in [
        "draggable", "ondrag", "dragstart", "dragend", "sortable", "drag-and-drop",
    ])
    if has_drag:
        findings.append(
            result("2.5.7", CheckpointStatus.CANNOT_VERIFY, page,
                   "Drag interaction patterns detected; verify single-pointer alternative exists.")
        )
    else:
        findings.append(
            result("2.5.7", CheckpointStatus.NOT_APPLICABLE, page,
                   "No drag-based interaction patterns detected.")
        )

    # -- 3.2.1  On Focus -----------------------------------------------
    focus_change = m.get("focus_context_change_detected")
    if focus_change is None:
        findings.append(result("3.2.1", CheckpointStatus.CANNOT_VERIFY, page, "Focus behavior metric unavailable."))
    else:
        status = CheckpointStatus.FAIL if focus_change else CheckpointStatus.PASS
        findings.append(result("3.2.1", status, page, "Focus context-change metric evaluated."))

    # -- 3.2.2  On Input (NEW) -----------------------------------------
    has_onchange_redirect = any(kw in html_lower for kw in [
        "onchange=\"location", "onchange=\"window.location", "onchange=\"document.location",
        "auto-submit", "autosubmit",
    ])
    if has_onchange_redirect:
        findings.append(
            result("3.2.2", CheckpointStatus.FAIL, page,
                   "Detected onchange-driven navigation/submit which may trigger unexpected context change.")
        )
    else:
        findings.append(
            result("3.2.2", CheckpointStatus.CANNOT_VERIFY, page,
                   "No obvious auto-submit/redirect on input change; full verification requires runtime testing.")
        )

    # -- 3.2.3  Consistent Navigation (cross-page) ---------------------
    findings.append(
        result(
            "3.2.3", CheckpointStatus.NOT_APPLICABLE, page,
            "Consistent navigation is evaluated after multi-page aggregation.",
            applicable=False, manual_required=True,
        )
    )

    # -- 3.2.4  Consistent Identification (NEW) ------------------------
    findings.append(
        result("3.2.4", CheckpointStatus.CANNOT_VERIFY, page,
               "Consistent identification requires cross-page comparison of components with same functionality.",
               manual_required=True)
    )

    # -- 3.2.6  Consistent Help (NEW) ----------------------------------
    has_help = any(kw in html_lower for kw in ["help", "support", "contact", "faq", "chat"])
    if has_help:
        findings.append(
            result("3.2.6", CheckpointStatus.CANNOT_VERIFY, page,
                   "Help/support mechanism detected; cross-page placement consistency requires manual verification.")
        )
    else:
        findings.append(
            result("3.2.6", CheckpointStatus.CANNOT_VERIFY, page,
                   "No help/support mechanism detected on this page; may be missing or requires multi-page check.")
        )

    return findings
