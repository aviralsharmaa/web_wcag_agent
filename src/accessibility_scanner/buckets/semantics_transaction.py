from __future__ import annotations

import re

from ..html_utils import DOMSnapshot, visible_text
from ..models import CheckpointResult, CheckpointStatus, PageArtifact
from ..workers.common import accessible_name, is_interactive
from .base import result

TRANSACTION_RE = re.compile(r"\b(payment|card|bank|amount|checkout|application|legal|tax|submit order)\b")
GENERIC_LINK_TEXT_RE = re.compile(r"^(click here|here|read more|more|learn more|link|this)$", re.IGNORECASE)

# Titles that are generic, just a site name, or not descriptive of page content
_GENERIC_TITLES = {
    "untitled", "document", "page", "home", "homepage", "welcome",
    "index", "default", "new tab", "loading", "website",
}
# Pattern: title is just a domain name or URL
_DOMAIN_TITLE_RE = re.compile(
    r"^(https?://)?[\w.-]+\.(com|org|net|co|io|in|gov)\b",
    re.IGNORECASE,
)


def analyze_semantics_transaction(page: PageArtifact) -> list[CheckpointResult]:
    snapshot = DOMSnapshot.from_html(page.html)
    text = visible_text(page.html)
    findings: list[CheckpointResult] = []

    # -- 2.4.2  Page Titled (NEW) --------------------------------------
    title = page.title.strip() if page.title else ""
    title_issue = _check_title_quality(title, page.url)
    if title_issue:
        findings.append(
            result("2.4.2", CheckpointStatus.FAIL, page, title_issue)
        )
    else:
        findings.append(
            result("2.4.2", CheckpointStatus.PASS, page,
                   f"Page has a descriptive title: '{title[:60]}'.")
        )

    # -- 2.4.4  Link Purpose (In Context) (NEW) ------------------------
    links = snapshot.find("a")
    generic_links = [
        link for link in links
        if GENERIC_LINK_TEXT_RE.match(link.text.strip()) and not link.attrs.get("aria-label")
    ]
    if not links:
        findings.append(result("2.4.4", CheckpointStatus.NOT_APPLICABLE, page, "No links detected."))
    elif generic_links:
        findings.append(
            result("2.4.4", CheckpointStatus.FAIL, page,
                   f"Detected {len(generic_links)} links with generic text (e.g. 'click here', 'read more') "
                   "without aria-label context.")
        )
    else:
        findings.append(
            result("2.4.4", CheckpointStatus.CANNOT_VERIFY, page,
                   "No generic link text detected; contextual adequacy of link purpose requires manual review.")
        )

    # -- 2.4.6  Headings and Labels ------------------------------------
    headings = snapshot.find("h1") + snapshot.find("h2") + snapshot.find("h3") + snapshot.find("h4")
    labels = snapshot.find("label")
    generic_headings = [h for h in headings if h.text.lower() in {"details", "info", "section", "read more"}]
    if generic_headings:
        findings.append(
            result(
                "2.4.6",
                CheckpointStatus.FAIL,
                page,
                f"Detected {len(generic_headings)} generic headings/labels with weak purpose signals.",
            )
        )
    elif headings or labels:
        findings.append(
            result(
                "2.4.6",
                CheckpointStatus.CANNOT_VERIFY,
                page,
                "Headings/labels exist but descriptive adequacy needs manual validation.",
            )
        )
    else:
        findings.append(result("2.4.6", CheckpointStatus.NOT_APPLICABLE, page, "No headings or form labels detected."))

    # -- 2.5.3  Label in Name (NEW) ------------------------------------
    interactive_nodes = [n for n in snapshot.nodes if is_interactive(n)]
    label_mismatch = []
    for node in interactive_nodes[:30]:  # sample first 30
        acc_name = accessible_name(snapshot, node) or ""
        vis_text = node.text.strip()
        if vis_text and acc_name and vis_text.lower() not in acc_name.lower():
            label_mismatch.append(node.attrs.get("id", node.tag))
    if label_mismatch:
        findings.append(
            result("2.5.3", CheckpointStatus.FAIL, page,
                   f"Detected {len(label_mismatch)} elements where visible text is not contained in accessible name.")
        )
    else:
        findings.append(
            result("2.5.3", CheckpointStatus.PASS, page,
                   "Visible labels match accessible names for sampled interactive elements.")
        )

    # -- 3.1.1  Language of Page (NEW) ---------------------------------
    html_nodes = snapshot.find("html")
    if not html_nodes:
        findings.append(result("3.1.1", CheckpointStatus.FAIL, page, "Missing root <html> element."))
    else:
        lang = html_nodes[0].attrs.get("lang", "").strip()
        if not lang:
            findings.append(result("3.1.1", CheckpointStatus.FAIL, page,
                                   "Missing page language (`lang`) attribute on <html>."))
        elif len(lang) < 2:
            findings.append(result("3.1.1", CheckpointStatus.FAIL, page,
                                   f"Page language '{lang}' appears invalid (too short)."))
        else:
            findings.append(result("3.1.1", CheckpointStatus.PASS, page,
                                   f"Page language declared: '{lang}'."))

    # -- 3.1.2  Language of Parts --------------------------------------
    if not html_nodes:
        findings.append(result("3.1.2", CheckpointStatus.FAIL, page, "Missing root html element."))
    else:
        lang = html_nodes[0].attrs.get("lang", "").strip()
        if not lang:
            findings.append(result("3.1.2", CheckpointStatus.FAIL, page, "Missing page language (`lang`) declaration."))
        else:
            findings.append(result("3.1.2", CheckpointStatus.CANNOT_VERIFY, page, "Page language set; language-of-parts still manual."))

    # -- 3.3.1  Error Identification -----------------------------------
    has_form = bool(snapshot.find("form"))
    error_metric = page.interaction_metrics.get("form_error_identification_ok")
    if not has_form:
        findings.append(result("3.3.1", CheckpointStatus.NOT_APPLICABLE, page, "No form elements detected."))
    elif error_metric is None:
        findings.append(
            result(
                "3.3.1",
                CheckpointStatus.CANNOT_VERIFY,
                page,
                "Error identification requires invalid submission flow testing.",
            )
        )
    else:
        findings.append(
            result(
                "3.3.1",
                CheckpointStatus.PASS if error_metric else CheckpointStatus.FAIL,
                page,
                "Form error identification metric evaluated.",
            )
        )

    # -- 3.3.2  Labels or Instructions (NEW) ---------------------------
    inputs = snapshot.find("input") + snapshot.find("select") + snapshot.find("textarea")
    visible_inputs = [n for n in inputs if n.attrs.get("type", "").lower() != "hidden"]
    unlabeled = []
    for node in visible_inputs:
        node_id = node.attrs.get("id", "")
        has_label = any(
            l.attrs.get("for") == node_id for l in snapshot.find("label")
        ) if node_id else False
        has_aria = node.attrs.get("aria-label") or node.attrs.get("aria-labelledby")
        has_placeholder = node.attrs.get("placeholder")
        if not has_label and not has_aria and not has_placeholder:
            unlabeled.append(node_id or node.attrs.get("name", node.tag))
    if not visible_inputs:
        findings.append(result("3.3.2", CheckpointStatus.NOT_APPLICABLE, page, "No visible form inputs detected."))
    elif unlabeled:
        findings.append(
            result("3.3.2", CheckpointStatus.FAIL, page,
                   f"Detected {len(unlabeled)} form inputs without labels, instructions, or placeholders.")
        )
    else:
        findings.append(
            result("3.3.2", CheckpointStatus.PASS, page,
                   "All visible form inputs have labels, instructions, or placeholders.")
        )

    # -- 3.3.3  Error Suggestion (NEW) ---------------------------------
    html_lower = page.html.lower()
    has_error_pattern = any(kw in html_lower for kw in [
        "aria-invalid", "aria-errormessage", "error-message", "validation-error",
        "field-error", "form-error",
    ])
    if not has_form:
        findings.append(result("3.3.3", CheckpointStatus.NOT_APPLICABLE, page, "No forms detected."))
    elif has_error_pattern:
        findings.append(
            result("3.3.3", CheckpointStatus.CANNOT_VERIFY, page,
                   "Error indication patterns detected (aria-invalid/error messages); "
                   "verify suggestions are provided for correction.")
        )
    else:
        findings.append(
            result("3.3.3", CheckpointStatus.CANNOT_VERIFY, page,
                   "No error suggestion patterns detected; requires submission flow testing.")
        )

    # -- 3.3.4  Error Prevention (Legal, Financial, Data) --------------
    transaction_like = bool(TRANSACTION_RE.search(text))
    prevention_metric = page.interaction_metrics.get("transaction_review_step_ok")
    if not transaction_like:
        findings.append(result("3.3.4", CheckpointStatus.NOT_APPLICABLE, page, "No legal/financial/data-submission flow detected."))
    elif prevention_metric is None:
        findings.append(
            result(
                "3.3.4",
                CheckpointStatus.CANNOT_VERIFY,
                page,
                "Transactional error-prevention path requires manual or scripted flow checks.",
            )
        )
    else:
        findings.append(
            result(
                "3.3.4",
                CheckpointStatus.PASS if prevention_metric else CheckpointStatus.FAIL,
                page,
                "Transactional error-prevention metric evaluated.",
            )
        )

    # -- 3.3.7  Redundant Entry (NEW) ----------------------------------
    findings.append(
        result("3.3.7", CheckpointStatus.CANNOT_VERIFY, page,
               "Redundant entry detection requires multi-step form flow analysis; not automatable from single page.")
    )

    # -- 3.3.8  Accessible Authentication (Minimum) (NEW) --------------
    # Only flag captcha if it appears in actual form/auth context, not just as a
    # script URL or library reference. Look for visible captcha elements.
    captcha_elements = (
        snapshot.find_by_attr(None, "data-sitekey")  # reCAPTCHA/hCaptcha widget
        + [n for n in snapshot.nodes if "g-recaptcha" in n.attrs.get("class", "").lower()]
        + [n for n in snapshot.nodes if "h-captcha" in n.attrs.get("class", "").lower()]
        + [n for n in snapshot.nodes if n.attrs.get("id", "").lower() in ("captcha", "recaptcha")]
    )
    has_cognitive_test = any(kw in html_lower for kw in ["riddle", "math question", "solve this"])
    if captcha_elements or has_cognitive_test:
        findings.append(
            result("3.3.8", CheckpointStatus.CANNOT_VERIFY, page,
                   f"Detected {len(captcha_elements)} captcha/cognitive elements; "
                   "verify accessible alternative mechanism exists (audio CAPTCHA, passkey, etc.).")
        )
    else:
        findings.append(
            result("3.3.8", CheckpointStatus.PASS, page,
                   "No cognitive function tests detected in authentication flow.")
        )

    # -- 4.1.1  Parsing (deprecated in WCAG 2.2) -------------------------
    # WCAG 2.2 removed 4.1.1 as a requirement — modern browsers handle
    # parsing robustly and this is always considered satisfied.
    findings.append(
        result("4.1.1", CheckpointStatus.NOT_APPLICABLE, page,
               "4.1.1 Parsing is deprecated in WCAG 2.2 and always considered satisfied.",
               applicable=False)
    )

    # -- 4.1.2  Name, Role, Value --------------------------------------
    unnamed = [node for node in snapshot.nodes if is_interactive(node) and not accessible_name(snapshot, node)]
    if unnamed:
        findings.append(
            result(
                "4.1.2",
                CheckpointStatus.FAIL,
                page,
                f"Detected {len(unnamed)} interactive elements missing accessible name/role/value support.",
            )
        )
    else:
        findings.append(result("4.1.2", CheckpointStatus.PASS, page, "Interactive elements expose basic name/role/value semantics."))

    # -- 4.1.3  Status Messages ----------------------------------------
    status_metric = page.interaction_metrics.get("status_messages_announced")
    live_regions = page.interaction_metrics.get("aria_live_regions")
    live_count = page.interaction_metrics.get("aria_live_region_count")
    has_live = bool(snapshot.find_by_attr(None, "aria-live"))

    # Prefer agentic probe data (live_regions list) over static heuristic
    if live_regions is not None:
        if live_count and live_count > 0:
            findings.append(
                result(
                    "4.1.3",
                    CheckpointStatus.CANNOT_VERIFY,
                    page,
                    f"{live_count} aria-live region(s) detected via browser probe; announcement quality needs manual check.",
                )
            )
        else:
            findings.append(
                result(
                    "4.1.3",
                    CheckpointStatus.CANNOT_VERIFY,
                    page,
                    "No aria-live regions found by browser probe; status messages may be missing.",
                )
            )
    elif status_metric is not None:
        findings.append(
            result(
                "4.1.3",
                CheckpointStatus.PASS if status_metric else CheckpointStatus.FAIL,
                page,
                "Status-message announcement metric evaluated.",
            )
        )
    elif has_live:
        findings.append(
            result(
                "4.1.3",
                CheckpointStatus.CANNOT_VERIFY,
                page,
                "aria-live regions present in DOM, but announcement behavior needs runtime verification.",
            )
        )
    else:
        findings.append(
            result(
                "4.1.3",
                CheckpointStatus.CANNOT_VERIFY,
                page,
                "No status-message instrumentation or aria-live evidence found.",
            )
        )

    return findings


def _check_title_quality(title: str, url: str) -> str | None:
    """Return a failure message if the title is poor quality, or None if OK."""
    if not title:
        return "Page has no <title> or title is empty."

    title_lower = title.lower().strip()

    # Generic placeholder titles
    if title_lower in _GENERIC_TITLES:
        return f"Page title '{title}' is generic and not descriptive."

    # Title is just a domain/URL
    if _DOMAIN_TITLE_RE.match(title_lower):
        return f"Page title '{title}' appears to be a domain name, not a descriptive page title."

    # Title is too short to be descriptive (single word, unless it's a brand)
    words = title.split()
    if len(words) == 1 and len(title) < 15:
        return f"Page title '{title}' is a single word and may not adequately describe page content."

    # Title matches common "site name only" pattern — no page-specific content
    # Extract domain from URL to compare with title
    if url:
        from urllib.parse import urlparse
        parsed = urlparse(url)
        domain_parts = parsed.netloc.replace("www.", "").split(".")
        domain_name = domain_parts[0] if domain_parts else ""
        # If title is just the domain name (case-insensitive)
        if domain_name and title_lower == domain_name.lower():
            return (
                f"Page title '{title}' matches the site domain name only — "
                "should include page-specific description."
            )

    # Title is same across error pages (common "404" or "Page Not Found" without context)
    if any(kw in title_lower for kw in ["404", "not found", "error", "403", "500"]):
        # This is actually fine if it identifies the error — but should be descriptive
        if len(words) <= 2:
            return f"Page title '{title}' for error page should be more descriptive (e.g., 'Page Not Found - Site Name')."

    return None
