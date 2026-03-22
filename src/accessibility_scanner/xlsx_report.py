"""Generate a structured XLSX report for WCAG 2.1 accessibility audit results."""
from __future__ import annotations

from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter


# ── Full WCAG 2.1 guideline metadata (55 criteria) ──────────────────────
WCAG_GUIDELINES: list[dict[str, str]] = [
    {"id": "1.1.1", "title": "Non-text Content", "level": "A",
     "requirement": "All non-text content has a text alternative that serves the equivalent purpose."},
    {"id": "1.2.1", "title": "Audio-only and Video-only (Prerecorded)", "level": "A",
     "requirement": "An alternative is provided for prerecorded audio-only and prerecorded video-only media."},
    {"id": "1.2.2", "title": "Captions (Prerecorded)", "level": "A",
     "requirement": "Captions are provided for all prerecorded audio content in synchronized media."},
    {"id": "1.2.3", "title": "Audio Description or Media Alternative", "level": "A",
     "requirement": "An alternative for time-based media or audio description of prerecorded video content is provided."},
    {"id": "1.2.4", "title": "Captions (Live)", "level": "AA",
     "requirement": "Captions are provided for all live audio content in synchronized media."},
    {"id": "1.2.5", "title": "Audio Description (Prerecorded)", "level": "AA",
     "requirement": "Audio description is provided for all prerecorded video content in synchronized media."},
    {"id": "1.3.1", "title": "Info and Relationships", "level": "A",
     "requirement": "Information, structure, and relationships conveyed through presentation can be programmatically determined."},
    {"id": "1.3.2", "title": "Meaningful Sequence", "level": "A",
     "requirement": "When the sequence in which content is presented affects its meaning, a correct reading sequence can be programmatically determined."},
    {"id": "1.3.3", "title": "Sensory Characteristics", "level": "A",
     "requirement": "Instructions do not rely solely on sensory characteristics such as shape, color, size, visual location, orientation, or sound."},
    {"id": "1.3.4", "title": "Orientation", "level": "AA",
     "requirement": "Content does not restrict its view and operation to a single display orientation."},
    {"id": "1.3.5", "title": "Identify Input Purpose", "level": "AA",
     "requirement": "The purpose of input fields collecting user information can be programmatically determined."},
    {"id": "1.4.1", "title": "Use of Color", "level": "A",
     "requirement": "Color is not used as the only visual means of conveying information."},
    {"id": "1.4.2", "title": "Audio Control", "level": "A",
     "requirement": "A mechanism is available to pause or stop audio that plays automatically for more than 3 seconds."},
    {"id": "1.4.3", "title": "Contrast (Minimum)", "level": "AA",
     "requirement": "Text and images of text have a contrast ratio of at least 4.5:1 (3:1 for large text)."},
    {"id": "1.4.4", "title": "Resize Text", "level": "AA",
     "requirement": "Text can be resized without assistive technology up to 200% without loss of content or functionality."},
    {"id": "1.4.5", "title": "Images of Text", "level": "AA",
     "requirement": "Text is used to convey information rather than images of text wherever possible."},
    {"id": "1.4.10", "title": "Reflow", "level": "AA",
     "requirement": "Content can be presented without loss of information or functionality at 320 CSS px width / 256 CSS px height."},
    {"id": "1.4.11", "title": "Non-text Contrast", "level": "AA",
     "requirement": "UI components and graphical objects have a contrast ratio of at least 3:1."},
    {"id": "1.4.12", "title": "Text Spacing", "level": "AA",
     "requirement": "No loss of content or functionality occurs when text spacing is adjusted."},
    {"id": "1.4.13", "title": "Content on Hover or Focus", "level": "AA",
     "requirement": "Content triggered by pointer hover or keyboard focus is dismissible, hoverable, and persistent."},
    {"id": "2.1.1", "title": "Keyboard", "level": "A",
     "requirement": "All functionality is operable through a keyboard interface without requiring specific timings."},
    {"id": "2.1.2", "title": "No Keyboard Trap", "level": "A",
     "requirement": "The user can move focus away from any component using only a keyboard interface."},
    {"id": "2.1.4", "title": "Character Key Shortcuts", "level": "A",
     "requirement": "If character key shortcuts exist, they can be turned off, remapped, or are only active when the component has focus."},
    {"id": "2.2.1", "title": "Timing Adjustable", "level": "A",
     "requirement": "For time limits, the user can turn off, adjust, or extend the time limit."},
    {"id": "2.2.2", "title": "Pause, Stop, Hide", "level": "A",
     "requirement": "Moving, blinking, scrolling, or auto-updating content can be paused, stopped, or hidden."},
    {"id": "2.3.1", "title": "Three Flashes or Below Threshold", "level": "A",
     "requirement": "Pages do not contain anything that flashes more than three times per second."},
    {"id": "2.4.1", "title": "Bypass Blocks", "level": "A",
     "requirement": "A mechanism is available to bypass blocks of content that are repeated on multiple pages."},
    {"id": "2.4.2", "title": "Page Titled", "level": "A",
     "requirement": "Web pages have titles that describe topic or purpose."},
    {"id": "2.4.3", "title": "Focus Order", "level": "A",
     "requirement": "Focusable components receive focus in an order that preserves meaning and operability."},
    {"id": "2.4.4", "title": "Link Purpose (In Context)", "level": "A",
     "requirement": "The purpose of each link can be determined from the link text alone or together with its context."},
    {"id": "2.4.5", "title": "Multiple Ways", "level": "AA",
     "requirement": "More than one way is available to locate a web page within a set of web pages."},
    {"id": "2.4.6", "title": "Headings and Labels", "level": "AA",
     "requirement": "Headings and labels describe topic or purpose."},
    {"id": "2.4.7", "title": "Focus Visible", "level": "AA",
     "requirement": "Any keyboard-operable user interface has a mode of operation where the keyboard focus indicator is visible."},
    {"id": "2.4.11", "title": "Focus Not Obscured (Minimum)", "level": "AA",
     "requirement": "When a UI component receives keyboard focus, it is not entirely hidden by author-created content."},
    {"id": "2.5.1", "title": "Pointer Gestures", "level": "A",
     "requirement": "All functionality that uses multipoint or path-based gestures can be operated with a single pointer."},
    {"id": "2.5.2", "title": "Pointer Cancellation", "level": "A",
     "requirement": "For single-pointer functionality, at least one of: the down-event is not used, completion is on up-event, or actions can be aborted/undone."},
    {"id": "2.5.3", "title": "Label in Name", "level": "A",
     "requirement": "For UI components with labels that include text or images of text, the accessible name contains the text that is presented visually."},
    {"id": "2.5.4", "title": "Motion Actuation", "level": "A",
     "requirement": "Functionality triggered by device motion can be operated via a user interface component, and motion triggering can be disabled."},
    {"id": "2.5.7", "title": "Dragging Movements", "level": "AA",
     "requirement": "Functionality achievable with a dragging movement can be achieved with a single pointer without dragging."},
    {"id": "2.5.8", "title": "Target Size (Minimum)", "level": "AA",
     "requirement": "The size of the target for pointer inputs is at least 24 by 24 CSS pixels."},
    {"id": "3.1.1", "title": "Language of Page", "level": "A",
     "requirement": "The default human language of each web page can be programmatically determined."},
    {"id": "3.1.2", "title": "Language of Parts", "level": "AA",
     "requirement": "The human language of each passage or phrase in the content can be programmatically determined."},
    {"id": "3.2.1", "title": "On Focus", "level": "A",
     "requirement": "When any UI component receives focus, it does not initiate a change of context."},
    {"id": "3.2.2", "title": "On Input", "level": "A",
     "requirement": "Changing the setting of any UI component does not automatically cause a change of context unless the user has been advised."},
    {"id": "3.2.3", "title": "Consistent Navigation", "level": "AA",
     "requirement": "Navigational mechanisms that are repeated on multiple pages occur in the same relative order."},
    {"id": "3.2.4", "title": "Consistent Identification", "level": "AA",
     "requirement": "Components that have the same functionality are identified consistently."},
    {"id": "3.2.6", "title": "Consistent Help", "level": "A",
     "requirement": "Help mechanisms occur in the same relative order on multiple pages."},
    {"id": "3.3.1", "title": "Error Identification", "level": "A",
     "requirement": "If an input error is automatically detected, the item that is in error is identified and described in text."},
    {"id": "3.3.2", "title": "Labels or Instructions", "level": "A",
     "requirement": "Labels or instructions are provided when content requires user input."},
    {"id": "3.3.3", "title": "Error Suggestion", "level": "AA",
     "requirement": "If an input error is detected and suggestions are known, they are provided to the user."},
    {"id": "3.3.4", "title": "Error Prevention (Legal, Financial, Data)", "level": "AA",
     "requirement": "For pages with legal commitments or financial transactions, submissions are reversible, checked, or confirmed."},
    {"id": "3.3.7", "title": "Redundant Entry", "level": "A",
     "requirement": "Information previously entered by or provided to the user is auto-populated or available for selection."},
    {"id": "3.3.8", "title": "Accessible Authentication (Minimum)", "level": "AA",
     "requirement": "A cognitive function test is not required for any step in an authentication process unless an alternative is provided."},
    {"id": "4.1.2", "title": "Name, Role, Value", "level": "A",
     "requirement": "For all UI components, the name and role can be programmatically determined; states, properties, and values can be programmatically set."},
    {"id": "4.1.3", "title": "Status Messages", "level": "AA",
     "requirement": "Status messages can be programmatically determined through role or properties so they can be presented by assistive technologies."},
]

_GUIDELINE_MAP = {g["id"]: g for g in WCAG_GUIDELINES}

# ── Styling constants ────────────────────────────────────────────────────
_HEADER_FILL = PatternFill("solid", fgColor="1F4E79")
_HEADER_FONT = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
_PASS_FILL = PatternFill("solid", fgColor="C6EFCE")
_FAIL_FILL = PatternFill("solid", fgColor="FFC7CE")
_CV_FILL = PatternFill("solid", fgColor="FFEB9C")
_NA_FILL = PatternFill("solid", fgColor="D9E2F3")
_THIN_BORDER = Border(
    left=Side(style="thin"),
    right=Side(style="thin"),
    top=Side(style="thin"),
    bottom=Side(style="thin"),
)

_STATUS_FILLS = {
    "Pass": _PASS_FILL,
    "Fail": _FAIL_FILL,
    "Cannot verify automatically": _CV_FILL,
    "Not applicable": _NA_FILL,
}


def _style_header_row(ws, col_count: int):
    for col in range(1, col_count + 1):
        cell = ws.cell(row=1, column=col)
        cell.fill = _HEADER_FILL
        cell.font = _HEADER_FONT
        cell.alignment = Alignment(horizontal="center", wrap_text=True)
        cell.border = _THIN_BORDER


def _auto_width(ws, min_width: int = 12, max_width: int = 55):
    for col_cells in ws.columns:
        max_len = min_width
        col_letter = get_column_letter(col_cells[0].column)
        for cell in col_cells:
            if cell.value:
                max_len = max(max_len, min(len(str(cell.value)), max_width))
        ws.column_dimensions[col_letter].width = max_len + 2


def generate_xlsx_report(report: dict[str, Any], output_path: str) -> str:
    """Generate a structured XLSX from the agentic scan report dict.

    Args:
        report: The JSON-serializable report dict produced by AgenticFlowRunner._build_report().
        output_path: Absolute or relative path for the output .xlsx file.

    Returns:
        The output_path string.
    """
    wb = Workbook()

    # ── Sheet 1: Summary ──────────────────────────────────────────────
    ws_summary = wb.active
    ws_summary.title = "Summary"
    ws_summary.append(["Metric", "Value"])
    _style_header_row(ws_summary, 2)

    totals = report.get("totals", {})
    ws_summary.append(["Run ID", report.get("run_id", "")])
    ws_summary.append(["Config", report.get("config", "")])
    ws_summary.append(["Screens Analyzed", report.get("screens_analyzed", 0)])
    ws_summary.append(["URLs Visited", len(report.get("urls_visited", []))])
    ws_summary.append(["Total PASS", totals.get("pass", 0)])
    ws_summary.append(["Total FAIL", totals.get("fail", 0)])
    ws_summary.append(["Total CANNOT VERIFY", totals.get("cannot_verify", 0)])
    ws_summary.append(["Unique Failures", len(report.get("all_failures", []))])
    _auto_width(ws_summary)

    # ── Sheet 2: WCAG Guidelines ──────────────────────────────────────
    ws_wcag = wb.create_sheet("WCAG Guidelines")
    headers = [
        "SC ID", "Title", "Level", "Requirement",
        "Status", "Rationale", "Page URL", "Evidence",
    ]
    ws_wcag.append(headers)
    _style_header_row(ws_wcag, len(headers))

    # Build a lookup from the per-screen WCAG results
    # Key: checkpoint_id → aggregated status, rationale, pages
    checkpoint_agg: dict[str, dict[str, Any]] = {}
    for screen in report.get("screens", []):
        screen_url = screen.get("url", "")
        for r in screen.get("wcag_summary", {}).get("failures", []):
            cp_id = r.get("checkpoint", "")
            if cp_id not in checkpoint_agg:
                checkpoint_agg[cp_id] = {
                    "status": "Fail",
                    "rationale": r.get("rationale", ""),
                    "pages": [],
                }
            checkpoint_agg[cp_id]["pages"].append(screen_url)

    # Also pull from flat wcag_results per screen for pass/cv/na
    for screen in report.get("screens", []):
        screen_url = screen.get("url", "")
        # wcag_results might not be in the lightweight report screens
        # so we work with what we have

    for guideline in WCAG_GUIDELINES:
        gid = guideline["id"]
        agg = checkpoint_agg.get(gid)
        if agg:
            status = agg["status"]
            rationale = agg["rationale"]
            pages = "; ".join(agg["pages"][:5])
        else:
            status = "Not evaluated"
            rationale = ""
            pages = ""

        row_num = ws_wcag.max_row + 1
        ws_wcag.append([
            gid,
            guideline["title"],
            guideline["level"],
            guideline["requirement"],
            status,
            rationale,
            pages,
            "",  # Evidence column (screenshots etc.)
        ])

        # Color the status cell
        status_cell = ws_wcag.cell(row=row_num, column=5)
        fill = _STATUS_FILLS.get(status)
        if fill:
            status_cell.fill = fill

    # Wrap text on requirement and rationale columns
    for row in ws_wcag.iter_rows(min_row=2, max_row=ws_wcag.max_row):
        for cell in row:
            cell.alignment = Alignment(wrap_text=True, vertical="top")
            cell.border = _THIN_BORDER

    _auto_width(ws_wcag)

    # ── Sheet 3: Per-Screen Results ───────────────────────────────────
    ws_screens = wb.create_sheet("Per-Screen Results")
    screen_headers = [
        "#", "Screen Label", "URL", "Screenshot",
        "Total Pass", "Total Fail", "Cannot Verify",
        "Top Failures",
    ]
    ws_screens.append(screen_headers)
    _style_header_row(ws_screens, len(screen_headers))

    for i, screen in enumerate(report.get("screens", []), 1):
        s = screen.get("wcag_summary", {})
        failures_str = "; ".join(
            f"[{f['checkpoint']}] {f.get('rationale', '')[:60]}"
            for f in s.get("failures", [])[:5]
        )
        ws_screens.append([
            i,
            screen.get("label", ""),
            screen.get("url", ""),
            screen.get("screenshot", ""),
            s.get("pass", 0),
            s.get("fail", 0),
            s.get("cannot_verify", 0),
            failures_str,
        ])

    for row in ws_screens.iter_rows(min_row=2, max_row=ws_screens.max_row):
        for cell in row:
            cell.alignment = Alignment(wrap_text=True, vertical="top")
            cell.border = _THIN_BORDER

    _auto_width(ws_screens)

    # ── Save ──────────────────────────────────────────────────────────
    wb.save(output_path)
    return output_path
