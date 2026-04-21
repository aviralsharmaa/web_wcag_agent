"""Generate a structured XLSX report matching the reference audit format."""
from __future__ import annotations

import os
from typing import Any, Callable

from openpyxl import Workbook
from openpyxl.drawing.image import Image as XLImage
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from .checkpoints import CHECKPOINTS

# ── Checkpoint metadata ───────────────────────────────────────────────────────
_CP_MAP = {cp.checkpoint_id: cp for cp in CHECKPOINTS}

WCAG_GUIDELINES: list[dict[str, str]] = [
    {"id": cp.checkpoint_id, "title": cp.title, "level": "A"}
    for cp in CHECKPOINTS
]
_GUIDELINE_MAP = {g["id"]: g for g in WCAG_GUIDELINES}

SEVERITY_MAP: dict[str, str] = {
    "1.1.1": "Critical", "2.1.1": "Critical", "2.1.2": "Critical",
    "3.3.8": "Critical", "1.3.1": "High", "2.4.7": "High", "4.1.2": "High",
    "1.4.3": "Moderate", "4.1.1": "Moderate", "1.3.3": "Moderate",
    "2.4.1": "Moderate", "1.4.11": "Moderate", "4.1.3": "Low",
    "1.3.5": "Low", "1.4.5": "Moderate", "2.2.1": "Moderate",
    "2.2.2": "Moderate", "2.4.3": "Moderate", "2.4.4": "Low",
    "2.4.6": "Low", "1.3.2": "Moderate",
}

PRIORITY_MAP: dict[str, str] = {
    "Critical": "P1", "High": "P1", "Moderate": "P2", "Low": "P3",
}

WHAT_TO_FIX: dict[str, str] = {
    "1.1.1": "Add meaningful text alternatives for informative images and icon-only controls. Hide decorative graphics from the accessibility tree.",
    "1.2.1": "Provide a text transcript or media alternative for all prerecorded audio-only and video-only content.",
    "1.2.2": "Add synchronized captions to all prerecorded audio in video media.",
    "1.2.3": "Provide an audio description track or full media alternative for prerecorded video.",
    "1.2.4": "Provide real-time captions for all live audio content in synchronized media.",
    "1.2.5": "Add an audio description track for all prerecorded video content.",
    "1.3.1": "Restore semantic relationships for form labels, grouped controls, and content structure.",
    "1.3.2": "Ensure the reading and focus sequence matches the visual and task order.",
    "1.3.3": "Do not rely on position, color, or shape alone to communicate instructions.",
    "1.3.4": "Remove any CSS or script that locks the page to a single orientation.",
    "1.3.5": "Add autocomplete attributes to personal data input fields.",
    "1.4.1": "Do not use color as the sole means of conveying information; add text or pattern alternatives.",
    "1.4.2": "Provide a mechanism to pause, stop, or mute auto-playing audio.",
    "1.4.3": "Increase text contrast to meet the 4.5:1 ratio (3:1 for large text).",
    "1.4.4": "Ensure text can be resized to 200% without loss of content or functionality.",
    "1.4.5": "Replace images of text with real text unless the presentation is essential.",
    "1.4.10": "Ensure all content reflows to a single column at 320px width without horizontal scrolling.",
    "1.4.11": "Increase non-text UI component contrast to at least 3:1 against adjacent colors.",
    "1.4.12": "Ensure no content is lost when letter-spacing, word-spacing, line-height, or paragraph spacing is increased.",
    "1.4.13": "Ensure hover/focus-triggered content is dismissible, hoverable, and persistent.",
    "2.1.1": "Make all interactive functionality operable via keyboard without requiring specific timings.",
    "2.1.2": "Ensure the user can move focus away from any keyboard-focusable component.",
    "2.1.4": "Allow character key shortcuts to be turned off or remapped.",
    "2.2.1": "Give users the ability to turn off, adjust, or extend any time limits.",
    "2.2.2": "Provide controls to pause, stop, or hide moving/blinking/scrolling content.",
    "2.3.1": "Remove or redesign content that flashes more than three times per second.",
    "2.4.1": "Add a reliable bypass mechanism for repeated content blocks.",
    "2.4.2": "Give each page a unique, descriptive title.",
    "2.4.3": "Ensure focusable components receive focus in a logical, meaningful order.",
    "2.4.4": "Make the purpose of each link clear from its text or surrounding context.",
    "2.4.5": "Provide at least two ways to find any page (e.g., navigation + search).",
    "2.4.6": "Use descriptive headings and labels throughout the interface.",
    "2.4.7": "Add visible focus styles (outline, border, or box-shadow) for all keyboard-operable elements.",
    "2.4.11": "Ensure focused components are not fully hidden by sticky headers, modals, or overlays.",
    "2.5.1": "Provide single-pointer alternatives for all multipoint or path-based gestures.",
    "2.5.2": "Ensure pointer actions complete on the up-event and can be aborted.",
    "2.5.3": "Match the accessible name of each control to its visible label text.",
    "2.5.4": "Provide UI alternatives for motion-triggered functionality.",
    "2.5.7": "Provide a single-pointer alternative for all dragging interactions.",
    "2.5.8": "Ensure all interactive targets are at least 24x24 CSS pixels.",
    "3.1.1": "Add a lang attribute to the <html> element reflecting the page's primary language.",
    "3.1.2": "Mark language changes within page content using lang attributes on the relevant elements.",
    "3.2.1": "Ensure receiving focus does not trigger an unexpected change of context.",
    "3.2.2": "Ensure changing a UI component's value does not cause an unexpected context change.",
    "3.2.3": "Place repeated navigation in the same relative order across all pages.",
    "3.2.4": "Identify components with the same function consistently across the interface.",
    "3.2.6": "Place help mechanisms in the same relative order on every page.",
    "3.3.1": "Identify input errors in text and describe the error to the user.",
    "3.3.2": "Provide labels or instructions for all user input fields.",
    "3.3.3": "Suggest corrections when an input error is detected and a suggestion is known.",
    "3.3.4": "Allow users to review, correct, or confirm submissions before final commitment.",
    "3.3.7": "Auto-populate or make available previously entered information.",
    "3.3.8": "Remove cognitive function tests from authentication or provide accessible alternatives.",
    "4.1.1": "Fix HTML parsing errors: remove duplicate IDs, close open tags, and correct nesting.",
    "4.1.2": "Ensure all interactive elements have a programmatically determinable name, role, and value.",
    "4.1.3": "Use ARIA live regions or status roles for dynamic status messages.",
}

HOW_TO_FIX: dict[str, str] = {
    "1.1.1": "1) Add alt='descriptive text' to every informative <img>.\n2) Use alt='' for purely decorative images.\n3) Add aria-label or aria-labelledby to icon-only buttons.\n4) Replace CSS background images used as content with <img> elements.",
    "1.3.1": "1) Associate every <input>, <select>, and <textarea> with a <label for='id'>.\n2) Use fieldset/legend for radio and checkbox groups.\n3) Replace layout tables with CSS; use role attributes where needed.",
    "1.3.2": "1) Reorder DOM elements to reflect visual order.\n2) Avoid using CSS to visually reorder content that differs from DOM order.",
    "1.3.3": "1) Replace phrases like 'click the blue button on the right' with control names.\n2) Add text labels next to directional icons and color-coded states.",
    "1.3.5": "1) Add autocomplete='email' to email fields, autocomplete='tel' to phone fields.\n2) Use appropriate autocomplete values for name, address, and password fields.",
    "1.4.3": "1) Check contrast ratios using a contrast analyser tool.\n2) Darken text or lighten background to reach 4.5:1 for normal text.\n3) Ensure 3:1 for large text (18pt or 14pt bold).",
    "1.4.5": "1) Replace images of text with real text styled with CSS.\n2) If a custom font is needed, embed it via @font-face.",
    "1.4.11": "1) Check border/background contrast of all form inputs, buttons, and icons.\n2) Ensure active UI components meet 3:1 against adjacent colors.",
    "2.1.2": "1) Avoid using tabindex to create focus traps.\n2) If a modal captures focus, ensure Escape or a visible button exits it.",
    "2.4.1": "1) Provide a visible skip link to main content.\n2) Use semantic landmarks: <main>, <nav>, <header>, <footer>.",
    "2.4.7": "1) Add visible focus styles via CSS outline or box-shadow on :focus and :focus-visible.\n2) Never apply outline: none without a visible alternative.",
    "3.1.1": "1) Add lang='en' (or appropriate BCP47 code) to the <html> element.\n2) Validate the attribute is correct for all page language variants.",
    "3.3.2": "1) Add a <label> for every form input.\n2) Use placeholder only as a supplement, not as the only label.",
    "3.3.8": "1) Provide an alternative authentication method that does not use a cognitive function test.\n2) Allow copy-paste into authentication fields.",
    "4.1.1": "1) Validate HTML with the W3C validator.\n2) Remove duplicate id attributes.\n3) Close all open tags and fix improper nesting.",
    "4.1.2": "1) Add aria-label or aria-labelledby to all icon buttons.\n2) Ensure role attributes are valid and match the element's function.",
    "4.1.3": "1) Add role='status' or aria-live='polite' to dynamic status areas.\n2) Ensure success/error messages are announced without requiring focus movement.",
    "2.3.1": "1) Identify all elements that flash more than 3 times per second.\n2) Remove or reduce animation rates below the threshold.",
    "2.2.1": "1) Identify all time-limited sessions or auto-refreshing content.\n2) Provide controls to extend or turn off time limits.",
    "2.2.2": "1) Add pause/stop/hide controls to all carousels and animations.\n2) Disable autoplay for content that moves for more than 5 seconds.",
    "3.2.2": "1) Review all form controls for auto-submit or auto-redirect on input change.\n2) Only trigger navigation on explicit user action (e.g., a submit button).",
}

# ── Issue type mapping by checkpoint ─────────────────────────────────────────
ISSUE_TYPE: dict[str, str] = {
    "1.1.1": "Screen Reader", "1.2.1": "Screen Reader", "1.2.2": "Screen Reader",
    "1.2.3": "Screen Reader", "1.2.4": "Screen Reader", "1.2.5": "Screen Reader",
    "1.3.1": "Screen Reader", "1.3.2": "Screen Reader", "1.3.3": "Screen Reader",
    "1.3.4": "Screen Reader", "1.3.5": "Screen Reader",
    "1.4.1": "Color", "1.4.3": "Color Contrast", "1.4.4": "Zoom",
    "1.4.5": "Screen Reader", "1.4.10": "Zoom", "1.4.11": "Color Contrast",
    "1.4.12": "Zoom", "1.4.13": "Other A11y",
    "2.1.1": "Keyboard Navigation", "2.1.2": "Keyboard Navigation",
    "2.1.4": "Keyboard Navigation",
    "2.2.1": "Other A11y", "2.2.2": "Other A11y", "2.3.1": "Other A11y",
    "2.4.1": "Keyboard Navigation", "2.4.2": "Screen Reader",
    "2.4.3": "Keyboard Navigation", "2.4.4": "Screen Reader",
    "2.4.5": "Screen Reader", "2.4.6": "Screen Reader",
    "2.4.7": "Keyboard Navigation", "2.4.11": "Keyboard Navigation",
    "2.5.1": "Other A11y", "2.5.2": "Other A11y", "2.5.3": "Screen Reader",
    "2.5.4": "Other A11y", "2.5.7": "Other A11y", "2.5.8": "Other A11y",
    "3.1.1": "Screen Reader", "3.1.2": "Screen Reader",
    "3.2.1": "Screen Reader", "3.2.2": "Screen Reader", "3.2.3": "Screen Reader",
    "3.2.4": "Screen Reader", "3.2.6": "Screen Reader",
    "3.3.1": "Screen Reader", "3.3.2": "Screen Reader", "3.3.3": "Screen Reader",
    "3.3.4": "Screen Reader", "3.3.7": "Screen Reader", "3.3.8": "Screen Reader",
    "4.1.1": "Screen Reader", "4.1.2": "Screen Reader", "4.1.3": "Screen Reader",
}

CODE_FIX: dict[str, str] = {
    "1.1.1": '<!-- Before (missing alt) -->\n<img src="image.png">\n\n<!-- After -->\n<img src="image.png" alt="Description of the image">\n\n<!-- Icon button -->\n<button aria-label="Close dialog">\n  <svg>...</svg>\n</button>',
    "1.3.1": '<!-- Before (no label) -->\n<input type="text" id="email">\n\n<!-- After -->\n<label for="email">Email Address</label>\n<input type="text" id="email">\n\n<!-- Or with aria-label -->\n<input type="text" aria-label="Email Address">',
    "1.3.2": "/* Ensure DOM order matches visual order */\n/* Avoid CSS order/flex-direction that\n   contradicts reading sequence */\n.container {\n  display: flex;\n  flex-direction: row; /* not row-reverse */\n}",
    "1.3.5": '<!-- Before -->\n<input type="text" name="email">\n\n<!-- After -->\n<input type="text" name="email"\n       autocomplete="email">\n<input type="text" name="fname"\n       autocomplete="given-name">\n<input type="tel" name="phone"\n       autocomplete="tel">',
    "1.4.1": '/* Before: color-only status */\n.error { color: red; }\n\n/* After: color + icon/text */\n.error {\n  color: red;\n}\n.error::before {\n  content: "\\26A0 "; /* warning icon */\n}',
    "1.4.3": "/* Before: low contrast */\n.text { color: #999; background: #fff; }\n/* Ratio: 2.85:1 — FAIL */\n\n/* After: sufficient contrast */\n.text { color: #595959; background: #fff; }\n/* Ratio: 7.0:1 — PASS (AA) */",
    "1.4.5": '<!-- Before: text as image -->\n<img src="heading.png" alt="Welcome">\n\n<!-- After: real text -->\n<h1 style="font-family: \'CustomFont\';\n  font-size: 2rem;">Welcome</h1>',
    "1.4.10": "/* Before: fixed width breaks reflow */\n.container { width: 1200px; }\n\n/* After: responsive reflow */\n.container {\n  max-width: 100%;\n  overflow-x: hidden;\n}\n@media (max-width: 320px) {\n  .sidebar { display: none; }\n}",
    "1.4.11": "/* Before: low UI contrast */\n.btn { border: 1px solid #ddd; }\n/* Ratio: 1.2:1 — FAIL */\n\n/* After: 3:1 contrast */\n.btn { border: 2px solid #767676; }\n/* Ratio: 4.48:1 — PASS */",
    "1.4.12": "/* Ensure no clipping with text spacing */\n.container {\n  overflow: visible; /* not hidden */\n  line-height: 1.5;\n}\n/* Avoid fixed heights on text containers */\n.card-text {\n  min-height: auto;\n  height: auto; /* not fixed px */\n}",
    "1.4.13": "/* Tooltip on hover: must be dismissible,\n   hoverable, and persistent */\n.tooltip {\n  position: absolute;\n}\n/* Allow hover on the tooltip itself */\n.trigger:hover + .tooltip,\n.tooltip:hover {\n  display: block;\n}\n/* Dismiss with Escape key via JS */",
    "2.1.1": '<!-- Before: click-only -->\n<div onclick="openMenu()">Menu</div>\n\n<!-- After: keyboard accessible -->\n<button onclick="openMenu()"\n        onkeydown="if(event.key===\'Enter\')openMenu()">\n  Menu\n</button>',
    "2.1.2": "/* Ensure focus can escape modals */\ndialog.addEventListener('keydown', (e) => {\n  if (e.key === 'Escape') {\n    dialog.close();\n    triggerButton.focus();\n  }\n});",
    "2.4.1": '<!-- Add skip link as first focusable -->\n<body>\n  <a href="#main-content"\n     class="skip-link">\n    Skip to main content\n  </a>\n  <nav>...</nav>\n  <main id="main-content">...</main>\n</body>\n\n/* CSS */\n.skip-link {\n  position: absolute;\n  left: -9999px;\n}\n.skip-link:focus {\n  left: 10px; top: 10px;\n}',
    "2.4.3": "/* Use logical tabindex, prefer natural order */\n/* Remove positive tabindex values */\n<!-- Before -->\n<input tabindex=\"3\">\n<input tabindex=\"1\">\n\n<!-- After: natural DOM order -->\n<input> <!-- first in focus -->\n<input> <!-- second in focus -->",
    "2.4.7": "/* Before: focus invisible */\na:focus { outline: none; }\n\n/* After: visible focus indicator */\na:focus-visible {\n  outline: 3px solid #005fcc;\n  outline-offset: 2px;\n}\nbutton:focus-visible {\n  outline: 3px solid #005fcc;\n  outline-offset: 2px;\n}",
    "2.4.11": "/* Before: sticky header hides focused\n   elements */\n.sticky-header {\n  position: sticky; top: 0;\n  z-index: 100;\n}\n\n/* After: add scroll-padding */\nhtml {\n  scroll-padding-top: 80px;\n  /* matches header height */\n}",
    "2.5.8": "/* Before: tiny click target */\n.icon-btn { width: 16px; height: 16px; }\n\n/* After: minimum 24x24px */\n.icon-btn {\n  min-width: 24px;\n  min-height: 24px;\n  padding: 4px;\n}",
    "3.1.1": '<!-- Before -->\n<html>\n\n<!-- After -->\n<html lang="en">',
    "3.1.2": '<!-- Mark language changes -->\n<p>The French word\n  <span lang="fr">bonjour</span>\n  means hello.</p>',
    "3.2.2": '<!-- Before: auto-submit on change -->\n<select onchange="this.form.submit()">\n\n<!-- After: explicit submit -->\n<select id="sort">\n  <option>Price</option>\n</select>\n<button type="submit">Apply</button>',
    "3.3.2": '<!-- Before: placeholder only -->\n<input placeholder="Enter email">\n\n<!-- After: proper label -->\n<label for="email">Email</label>\n<input id="email"\n       placeholder="e.g. user@example.com">',
    "3.3.8": '<!-- Before: CAPTCHA only -->\n<div class="captcha">...</div>\n\n<!-- After: alternative auth -->\n<div class="captcha">...</div>\n<p>Or <a href="/auth/email-link">\n  sign in via email link</a></p>',
    "4.1.1": '<!-- Before: duplicate IDs -->\n<div id="header">...</div>\n<div id="header">...</div>\n\n<!-- After: unique IDs -->\n<div id="site-header">...</div>\n<div id="page-header">...</div>',
    "4.1.2": '<!-- Before: no role/name -->\n<div onclick="toggle()">X</div>\n\n<!-- After: proper role + name -->\n<button aria-label="Close"\n        onclick="toggle()">\n  X\n</button>',
    "4.1.3": '<!-- Before: silent status update -->\n<div id="status">Saved!</div>\n\n<!-- After: announced by SR -->\n<div id="status"\n     role="status"\n     aria-live="polite">\n  Saved!\n</div>',
}

EXPECTED_RESULT: dict[str, str] = {
    "1.1.1": "All non-text content has a text alternative that serves the equivalent purpose (WCAG 1.1.1 Level A).",
    "1.2.1": "A text alternative or description is provided for all prerecorded audio-only and video-only media (WCAG 1.2.1 Level A).",
    "1.2.2": "Captions are provided for all prerecorded audio content in synchronized media (WCAG 1.2.2 Level A).",
    "1.2.3": "An audio description or media alternative is provided for prerecorded video (WCAG 1.2.3 Level A).",
    "1.2.4": "Captions are provided for all live audio content in synchronized media (WCAG 1.2.4 Level AA).",
    "1.2.5": "Audio description is provided for all prerecorded video content (WCAG 1.2.5 Level AA).",
    "1.3.1": "Information, structure, and relationships conveyed through presentation can be programmatically determined (WCAG 1.3.1 Level A).",
    "1.3.2": "The correct reading sequence can be programmatically determined (WCAG 1.3.2 Level A).",
    "1.3.3": "Instructions do not rely solely on sensory characteristics such as color, shape, or position (WCAG 1.3.3 Level A).",
    "1.3.4": "Content does not restrict its view and operation to a single display orientation (WCAG 1.3.4 Level AA).",
    "1.3.5": "The purpose of input fields collecting personal information can be programmatically determined (WCAG 1.3.5 Level AA).",
    "1.4.1": "Color is not the only visual means of conveying information (WCAG 1.4.1 Level A).",
    "1.4.3": "Text has a contrast ratio of at least 4.5:1 (3:1 for large text) against its background (WCAG 1.4.3 Level AA).",
    "1.4.4": "Text can be resized up to 200% without loss of content or functionality (WCAG 1.4.4 Level AA).",
    "1.4.5": "Images of text are not used unless essential (WCAG 1.4.5 Level AA).",
    "1.4.10": "Content can be presented without loss of information using a single column at 320px width (WCAG 1.4.10 Level AA).",
    "1.4.11": "UI components and graphical objects have a contrast ratio of at least 3:1 against adjacent colors (WCAG 1.4.11 Level AA).",
    "1.4.12": "No loss of content or functionality occurs when text spacing is adjusted (WCAG 1.4.12 Level AA).",
    "1.4.13": "Content triggered by hover or focus is dismissible, hoverable, and persistent (WCAG 1.4.13 Level AA).",
    "2.1.1": "All functionality is operable through a keyboard interface (WCAG 2.1.1 Level A).",
    "2.1.2": "Keyboard focus is not trapped within any component (WCAG 2.1.2 Level A).",
    "2.1.4": "Single-character keyboard shortcuts can be turned off or remapped (WCAG 2.1.4 Level A).",
    "2.2.1": "Users can turn off, adjust, or extend time limits (WCAG 2.2.1 Level A).",
    "2.2.2": "Users can pause, stop, or hide moving or blinking content (WCAG 2.2.2 Level A).",
    "2.3.1": "Content does not flash more than three times per second (WCAG 2.3.1 Level A).",
    "2.4.1": "A mechanism is available to bypass blocks of content that are repeated on multiple pages (WCAG 2.4.1 Level A).",
    "2.4.2": "Each web page has a title that describes its topic or purpose (WCAG 2.4.2 Level A).",
    "2.4.3": "Focusable components receive focus in a logical, meaningful order (WCAG 2.4.3 Level A).",
    "2.4.4": "The purpose of each link can be determined from the link text or context (WCAG 2.4.4 Level A).",
    "2.4.5": "More than one way is available to locate a page within a set of pages (WCAG 2.4.5 Level AA).",
    "2.4.6": "Headings and labels describe the topic or purpose (WCAG 2.4.6 Level AA).",
    "2.4.7": "Any keyboard operable user interface has a mode where the keyboard focus indicator is visible (WCAG 2.4.7 Level AA).",
    "2.4.11": "Focused components are not entirely hidden by author-created content (WCAG 2.4.11 Level AA).",
    "2.5.1": "All functionality that uses multipoint or path-based gestures can be operated with a single pointer (WCAG 2.5.1 Level A).",
    "2.5.2": "For single pointer inputs, the down-event is not used to execute the function (WCAG 2.5.2 Level A).",
    "2.5.3": "The accessible name of user interface components contains the visible text (WCAG 2.5.3 Level A).",
    "2.5.4": "Functionality triggered by device motion can be operated by user interface components (WCAG 2.5.4 Level A).",
    "2.5.7": "All functionality that uses a dragging movement can be achieved with a single pointer (WCAG 2.5.7 Level AA).",
    "2.5.8": "The size of the target is at least 24x24 CSS pixels (WCAG 2.5.8 Level AA).",
    "3.1.1": "The default human language of the page can be programmatically determined (WCAG 3.1.1 Level A).",
    "3.1.2": "The human language of each passage or phrase can be programmatically determined (WCAG 3.1.2 Level AA).",
    "3.2.1": "Receiving focus does not initiate a change of context (WCAG 3.2.1 Level A).",
    "3.2.2": "Changing the setting of a user interface component does not automatically cause a change of context (WCAG 3.2.2 Level A).",
    "3.2.3": "Navigational mechanisms that are repeated are in the same relative order (WCAG 3.2.3 Level AA).",
    "3.2.4": "Components with the same functionality are identified consistently (WCAG 3.2.4 Level AA).",
    "3.2.6": "Help mechanisms are provided in the same relative order on every page (WCAG 3.2.6 Level AA).",
    "3.3.1": "Input errors are identified and described to the user in text (WCAG 3.3.1 Level A).",
    "3.3.2": "Labels or instructions are provided when content requires user input (WCAG 3.3.2 Level A).",
    "3.3.3": "Suggestions for correcting input errors are provided when known (WCAG 3.3.3 Level AA).",
    "3.3.4": "Users can review, correct, or confirm submissions before finalizing (WCAG 3.3.4 Level AA).",
    "3.3.7": "Previously entered information is auto-populated or made available (WCAG 3.3.7 Level A).",
    "3.3.8": "A cognitive function test is not required for authentication (WCAG 3.3.8 Level AA).",
    "4.1.1": "Content does not contain parsing errors such as duplicate IDs (WCAG 4.1.1 Level A).",
    "4.1.2": "All user interface components have name, role, and value programmatically determined (WCAG 4.1.2 Level A).",
    "4.1.3": "Status messages can be programmatically determined without receiving focus (WCAG 4.1.3 Level AA).",
}

# ── Styling constants ─────────────────────────────────────────────────────────
_SALMON_FILL = PatternFill("solid", fgColor="E6B8AF")
_DARK_BLUE_FILL = PatternFill("solid", fgColor="1F4E79")
_GREEN_FILL = PatternFill("solid", fgColor="00FF00")
_ORANGE_FILL = PatternFill("solid", fgColor="FFA500")
_GREY_FILL = PatternFill("solid", fgColor="CCCCCC")
_LIGHT_BLUE_FILL = PatternFill("solid", fgColor="D9E2F3")
_FAIL_FILL = PatternFill("solid", fgColor="FFC7CE")
_PASS_NA_FILL = PatternFill("solid", fgColor="C6EFCE")
_THIN = Border(
    left=Side(style="thin"), right=Side(style="thin"),
    top=Side(style="thin"), bottom=Side(style="thin"),
)
_SEV_FILLS = {
    "Critical": (PatternFill("solid", fgColor="FF0000"), Font(name="Calibri", size=10, bold=True, color="FFFFFF")),
    "High":     (PatternFill("solid", fgColor="FF6600"), Font(name="Calibri", size=10, bold=True, color="FFFFFF")),
    "Moderate": (PatternFill("solid", fgColor="FFCC00"), Font(name="Calibri", size=10, bold=True, color="000000")),
    "Low":      (PatternFill("solid", fgColor="99CCFF"), Font(name="Calibri", size=10, bold=True, color="000000")),
}
_DARK_FONT = Font(name="Calibri", size=11, bold=True, color="2E3436")
_HEADER_FONT_WHITE = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
_WRAP_TOP = Alignment(wrap_text=True, vertical="top")
_CENTER = Alignment(horizontal="center", vertical="center", wrap_text=True)


# ── Checkpoint → axe-core rule mapping ───────────────────────────────────────
_CP_TO_AXE: dict[str, list[str]] = {
    "1.1.1": ["image-alt", "button-name", "input-image-alt", "area-alt", "role-img-alt", "svg-img-alt"],
    "1.3.1": ["label", "form-field-multiple-labels", "select-name", "definition-list", "dlitem", "list", "listitem", "th-has-data-cells", "td-headers-attr"],
    "1.3.5": ["autocomplete-valid"],
    "1.4.3": ["color-contrast"],
    "1.4.11": ["color-contrast"],
    "2.1.1": ["scrollable-region-focusable"],
    "2.4.1": ["bypass", "region"],
    "2.4.2": ["document-title"],
    "2.4.7": ["focus-visible"],
    "3.1.1": ["html-has-lang", "html-lang-valid"],
    "3.1.2": ["valid-lang"],
    "3.3.2": ["label"],
    "4.1.1": ["duplicate-id", "duplicate-id-active", "duplicate-id-aria"],
    "4.1.2": ["button-name", "link-name", "aria-required-attr", "aria-roles", "aria-valid-attr-value", "aria-valid-attr"],
    "4.1.3": ["aria-live-region"],
}


def _enrich_rationale(
    checkpoint_id: str,
    original_rationale: str,
    supplementary: dict[str, Any],
) -> str:
    """Replace vague bucket rationales with specific element-level findings from axe-core."""
    import re as _re

    axe_data = supplementary.get("axe_scan", {})
    violations = axe_data.get("violations", [])
    target_rules = _CP_TO_AXE.get(checkpoint_id, [])

    # Gather matching axe violations
    matched_nodes: list[dict] = []
    for v in violations:
        if v.get("id") in target_rules:
            for node in v.get("nodes", []):
                matched_nodes.append({**node, "_rule": v["id"], "_help": v.get("help", "")})

    # Also check supplementary-specific data
    specific_lines: list[str] = []

    if checkpoint_id in ("1.4.3", "1.4.11"):
        cc = supplementary.get("color_contrast", {})
        cc_issues = cc.get("issues", [])
        if cc_issues:
            examples = []
            for issue in cc_issues[:5]:
                target = ", ".join(issue.get("target", []))
                msg = issue.get("message", "")
                # Extract contrast ratio and colors
                ratio_m = _re.search(r"contrast of ([\d.]+)", msg)
                fg_m = _re.search(r"foreground color: (#[0-9a-fA-F]+)", msg)
                bg_m = _re.search(r"background color: (#[0-9a-fA-F]+)", msg)
                size_m = _re.search(r"font size: ([\d.]+pt)", msg)
                if ratio_m and fg_m and bg_m:
                    examples.append(
                        f"  • {target}: contrast {ratio_m.group(1)}:1 "
                        f"({fg_m.group(1)} on {bg_m.group(1)}"
                        f"{', ' + size_m.group(1) if size_m else ''})"
                    )
                else:
                    examples.append(f"  • {target}: {msg[:80]}")
            total = cc.get("fail_count", len(cc_issues))
            specific_lines.append(f"{total} element(s) with insufficient color contrast:")
            specific_lines.extend(examples)
            if total > 5:
                specific_lines.append(f"  ... and {total - 5} more")
            return "\n".join(specific_lines)

    if checkpoint_id == "2.4.7":
        kb = supplementary.get("keyboard", {})
        obscured = kb.get("focus_obscured_count", 0)
        invisible = kb.get("elements_tabbed", 0) - kb.get("focus_visible_count", 0)
        skip = kb.get("skip_link_found", False)
        trap = kb.get("focus_trap_detected", False)
        parts = []
        if not skip:
            parts.append("No skip-to-main-content link found")
        if obscured > 0:
            parts.append(f"{obscured} focused element(s) obscured by sticky/fixed headers")
        if invisible > 0:
            parts.append(f"{invisible} element(s) missing visible focus indicator")
        if trap:
            parts.append("Focus trap detected")
        tab_results = kb.get("tab_results", [])
        invisible_els = [t for t in tab_results if not t.get("visible", True)][:3]
        if invisible_els:
            parts.append("Elements without visible focus:")
            for t in invisible_els:
                parts.append(f"  • <{t.get('tag', '?')}> {t.get('label', '')[:40]}")
        if parts:
            return "; ".join(parts[:3]) + ("\n" + "\n".join(parts[3:]) if len(parts) > 3 else "")

    if checkpoint_id == "1.4.12":
        ts = supplementary.get("text_spacing", {})
        detail = ts.get("detail", {})
        clipped_v = detail.get("clippedVertical", 0)
        clipped_h = detail.get("clippedHorizontal", 0)
        if clipped_v or clipped_h:
            return f"{clipped_v} element(s) with vertically clipped text, {clipped_h} with horizontally clipped text when WCAG text-spacing overrides are applied."

    if checkpoint_id in ("1.4.4", "1.4.10"):
        zoom = supplementary.get("zoom", {})
        detail = zoom.get("detail", {}) or zoom.get("reflow_detail", {})
        overflow = detail.get("overflowDelta", 0)
        overflow_count = detail.get("overflowCount", 0)
        clipped = detail.get("clippedCount", 0)
        parts = []
        if overflow > 0:
            parts.append(f"Horizontal overflow of {overflow}px at 320px viewport width")
        if overflow_count > 0:
            parts.append(f"{overflow_count} element(s) overflow viewport")
        if clipped > 0:
            parts.append(f"{clipped} element(s) have clipped content")
        if parts:
            return "; ".join(parts) + "."

    if checkpoint_id == "2.1.2":
        kb = supplementary.get("keyboard", {})
        if kb.get("focus_trap_detected"):
            return "Focus trap detected: keyboard focus gets stuck and cannot escape a component. Users cannot Tab out of the trapped region."

    # ── Concrete replacements for "manual review" checkpoints ──

    if checkpoint_id == "1.4.5":
        # Images of text — use axe image-alt data to find actual text-in-image elements
        axe = supplementary.get("axe_scan", {})
        img_nodes = []
        for v in axe.get("violations", []):
            if v.get("id") in ("image-alt", "role-img-alt", "svg-img-alt"):
                for n in v.get("nodes", []):
                    img_nodes.append(n)
        if img_nodes:
            parts = [f"{len(img_nodes)} image(s) may contain text rendered as graphics instead of real HTML text:"]
            for n in img_nodes[:5]:
                sel = ", ".join(n.get("target", []))
                html = n.get("html", "")[:80]
                parts.append(f"  • {sel}: {html}")
            return "\n".join(parts)

    if checkpoint_id == "1.3.2":
        # Meaningful sequence — check DOM order issues
        sr = supplementary.get("screen_reader", {})
        landmarks = sr.get("landmarks", {})
        headings = sr.get("headings_count", 0)
        dom_issues = sr.get("dom_issues", [])
        parts = []
        if not landmarks.get("main"):
            parts.append("No <main> landmark — screen readers cannot identify primary content region")
        if headings == 0:
            parts.append("No heading elements found — no document outline for navigation")
        elif headings < 3:
            parts.append(f"Only {headings} heading(s) found — sparse document outline")
        for di in dom_issues:
            parts.append(di)
        if parts:
            return "Reading sequence issues:\n" + "\n".join(f"  • {p}" for p in parts)

    if checkpoint_id == "1.4.1":
        # Use of color — check color contrast data for near-fail elements
        cc = supplementary.get("color_contrast", {})
        needs_review = cc.get("needs_review_count", 0)
        kb = supplementary.get("keyboard", {})
        focus_vis = kb.get("focus_visible_count", 0)
        tabbed = kb.get("elements_tabbed", 0)
        parts = []
        if needs_review > 0:
            parts.append(f"{needs_review} element(s) use color that may be the sole indicator of state or meaning (needs review)")
        if tabbed > 0 and focus_vis < tabbed:
            parts.append(f"{tabbed - focus_vis}/{tabbed} focusable elements rely only on color change for focus indication (no outline/border)")
        axe = supplementary.get("axe_scan", {})
        for v in axe.get("violations", []):
            if v.get("id") == "link-in-text-block":
                parts.append(f"Links within text blocks distinguished only by color ({len(v.get('nodes', []))} instances)")
        if parts:
            return "Color used as sole visual indicator:\n" + "\n".join(f"  • {p}" for p in parts)

    if checkpoint_id == "2.4.3":
        # Focus order — use keyboard tab order data
        kb = supplementary.get("keyboard", {})
        tabbed = kb.get("elements_tabbed", 0)
        focusable = kb.get("focusable_elements", 0)
        obscured = kb.get("focus_obscured_count", 0)
        tab_results = kb.get("tab_results", [])
        parts = []
        if focusable > 0 and tabbed < focusable:
            parts.append(f"Only {tabbed}/{focusable} focusable elements reachable via Tab key")
        if obscured > 0:
            parts.append(f"{obscured} element(s) obscured by sticky/fixed headers when focused")
        # Check for out-of-order elements
        invisible_els = [t for t in tab_results if not t.get("visible", True)][:3]
        if invisible_els:
            parts.append("Elements not visible when focused:")
            for t in invisible_els:
                parts.append(f"  <{t.get('tag', '?')}> \"{t.get('label', '')[:30]}\"")
        if parts:
            return "Focus order issues:\n" + "\n".join(f"  • {p}" if not p.startswith("  ") else p for p in parts)

    if checkpoint_id == "2.4.4":
        # Link purpose — check for generic link text
        axe = supplementary.get("axe_scan", {})
        link_nodes = []
        for v in axe.get("violations", []):
            if v.get("id") in ("link-name", "button-name"):
                for n in v.get("nodes", []):
                    link_nodes.append(n)
        if link_nodes:
            parts = [f"{len(link_nodes)} link(s)/button(s) with unclear purpose:"]
            for n in link_nodes[:5]:
                sel = ", ".join(n.get("target", []))
                html = n.get("html", "")[:80]
                parts.append(f"  • {sel}: {html}")
            return "\n".join(parts)
        # Fallback: check for links without meaningful text
        sr = supplementary.get("screen_reader", {})
        sr_issues = sr.get("all_issues", [])
        link_issues = [i for i in sr_issues if "link" in str(i.get("description", "")).lower()]
        if link_issues:
            return "\n".join(f"  • {i.get('description', '')}" for i in link_issues[:3])

    if checkpoint_id == "2.4.6":
        # Headings and labels — check heading structure and form labels
        sr = supplementary.get("screen_reader", {})
        headings = sr.get("headings_count", 0)
        axe = supplementary.get("axe_scan", {})
        label_nodes = []
        for v in axe.get("violations", []):
            if v.get("id") in ("label", "select-name", "input-image-alt"):
                for n in v.get("nodes", []):
                    label_nodes.append(n)
        parts = []
        if headings == 0:
            parts.append("No heading elements (<h1>-<h6>) found on page")
        elif headings < 3:
            parts.append(f"Only {headings} heading(s) — insufficient page structure")
        if label_nodes:
            parts.append(f"{len(label_nodes)} form element(s) missing accessible labels:")
            for n in label_nodes[:3]:
                sel = ", ".join(n.get("target", []))
                html = n.get("html", "")[:80]
                parts.append(f"  {sel}: {html}")
        if parts:
            return "\n".join(parts)

    if checkpoint_id == "3.3.4":
        # Error prevention — check for forms without validation
        axe = supplementary.get("axe_scan", {})
        form_issues = []
        for v in axe.get("violations", []):
            if v.get("id") in ("select-name", "label", "autocomplete-valid"):
                for n in v.get("nodes", []):
                    form_issues.append(n)
        sr = supplementary.get("screen_reader", {})
        if form_issues:
            parts = [f"{len(form_issues)} form element(s) lack proper validation/labeling for error prevention:"]
            for n in form_issues[:3]:
                sel = ", ".join(n.get("target", []))
                html = n.get("html", "")[:80]
                parts.append(f"  • {sel}: {html}")
            return "\n".join(parts)

    if checkpoint_id == "3.1.1":
        # Language of page
        axe = supplementary.get("axe_scan", {})
        for v in axe.get("violations", []):
            if v.get("id") == "html-has-lang":
                return f"<html> element is missing a lang attribute. Screen readers cannot determine the page language, causing mispronunciation of all content."
            if v.get("id") == "html-lang-valid":
                return f"<html> lang attribute has an invalid value. Screen readers will default to their own language setting."

    # For screen reader / ARIA issues
    if checkpoint_id in ("4.1.2", "4.1.1", "1.3.1", "1.1.1"):
        sr = supplementary.get("screen_reader", {})
        sr_issues = sr.get("all_issues", [])
        axe = supplementary.get("axe_scan", {})
        parts = []
        for issue in sr_issues:
            desc = issue.get("description", "")
            count = issue.get("count", 1)
            if desc:
                parts.append(f"{desc} ({count} instance{'s' if count > 1 else ''})")
        if parts:
            return "\n".join(f"  • {p}" for p in parts[:5])

    # Generic enrichment from axe nodes
    if matched_nodes and not specific_lines:
        examples = []
        rule_help = matched_nodes[0].get("_help", "")
        for node in matched_nodes[:5]:
            sel = ", ".join(node.get("target", []))
            html = node.get("html", "")[:100]
            fix = (node.get("failure_summary", "") or "").split("\n")[0][:100]
            examples.append(f"  • {sel}\n    HTML: {html}\n    Fix: {fix}")
        total = len(matched_nodes)
        header = f"{rule_help} — {total} element(s) affected:" if rule_help else f"{total} element(s) affected:"
        return header + "\n" + "\n".join(examples) + (f"\n  ... and {total - 5} more" if total > 5 else "")

    # If nothing specific found, return original but strip the CV policy noise
    cleaned = _re.sub(r"\s*CV policy \([^)]+\):[^.]+\.", "", original_rationale).strip()
    return cleaned or original_rationale


def _build_contextual_code_fix(
    checkpoint_id: str,
    result: dict[str, Any],
    supplementary: dict[str, Any],
) -> str:
    """Build a contextual code fix using actual element data from axe-core."""
    generic = CODE_FIX.get(checkpoint_id, "")
    context_lines: list[str] = []

    # Try to find matching axe-core violation nodes for this checkpoint
    axe_data = supplementary.get("axe_scan", {})
    violations = axe_data.get("violations", [])

    # Map checkpoint IDs to axe rule IDs
    cp_to_axe = {
        "1.1.1": ["image-alt", "button-name", "input-image-alt", "area-alt"],
        "1.3.1": ["label", "form-field-multiple-labels", "select-name"],
        "1.4.3": ["color-contrast"],
        "1.4.11": ["color-contrast"],
        "2.4.7": ["focus-visible"],
        "3.1.1": ["html-has-lang", "html-lang-valid"],
        "4.1.1": ["duplicate-id", "duplicate-id-active"],
        "4.1.2": ["button-name", "link-name", "aria-required-attr"],
    }

    target_rules = cp_to_axe.get(checkpoint_id, [])

    for violation in violations:
        if violation.get("id") in target_rules:
            nodes = violation.get("nodes", [])[:3]  # max 3 examples
            for node in nodes:
                selector = ", ".join(node.get("target", []))
                html_snippet = node.get("html", "")[:200]
                if selector or html_snippet:
                    context_lines.append(f"/* Affected element: {selector} */")
                    if html_snippet:
                        context_lines.append(f"/* Current HTML:\n   {html_snippet}\n*/")

    # Add color contrast specifics
    if checkpoint_id in ("1.4.3", "1.4.11"):
        cc_data = supplementary.get("color_contrast", {})
        cc_issues = cc_data.get("issues", [])[:3]
        for issue in cc_issues:
            msg = issue.get("message", "")
            target = ", ".join(issue.get("target", []))
            # Extract color info from message
            if "foreground color:" in msg and "background color:" in msg:
                context_lines.append(f"/* Element: {target} */")
                # Parse colors from message
                import re
                fg_match = re.search(r"foreground color: (#[0-9a-fA-F]+)", msg)
                bg_match = re.search(r"background color: (#[0-9a-fA-F]+)", msg)
                ratio_match = re.search(r"contrast of ([\d.]+)", msg)
                if fg_match and bg_match and ratio_match:
                    fg, bg, ratio = fg_match.group(1), bg_match.group(1), ratio_match.group(1)
                    context_lines.append(
                        f"/* Current: {fg} on {bg} = {ratio}:1 */\n"
                        f"{target} {{\n"
                        f"  color: /* change {fg} to darker shade */;\n"
                        f"  /* Required: 4.5:1 for normal, 3:1 for large text */\n"
                        f"}}"
                    )

    # Add keyboard specifics
    if checkpoint_id == "2.4.7":
        kb_data = supplementary.get("keyboard", {})
        if kb_data.get("focus_obscured_count", 0) > 0:
            context_lines.append(
                "/* Focus obscured by sticky/fixed header */\n"
                "html { scroll-padding-top: 80px; }\n"
                "/* Adjust value to your header height */"
            )

    # Combine contextual + generic
    parts = []
    if context_lines:
        parts.append("/* === Your specific issues === */\n" + "\n\n".join(context_lines))
    if generic:
        parts.append("/* === Recommended fix pattern === */\n" + generic)

    return "\n\n".join(parts) if parts else ""


def _thin_border_cell(cell, wrap: bool = True) -> None:
    cell.border = _THIN
    if wrap:
        cell.alignment = _WRAP_TOP


def _place_image(
    ws,
    cell_ref: str,
    img_path: str,
    w: int,
    h: int,
    uploader: Callable[[str], str] | None,
    row: int,
    col: int,
) -> None:
    """Place an image or hyperlink in the worksheet."""
    if uploader:
        url = uploader(img_path)
        if url:
            cell = ws.cell(row=row, column=col)
            cell.value = "View Screenshot"
            cell.hyperlink = url
            cell.font = Font(name="Calibri", size=10, color="0563C1", underline="single")
            cell.alignment = Alignment(horizontal="center", vertical="center")
        else:
            ws.cell(row=row, column=col, value=os.path.basename(img_path))
    else:
        try:
            xl = XLImage(img_path)
            xl.width, xl.height = w, h
            xl.anchor = cell_ref
            ws.add_image(xl)
        except Exception:
            ws.cell(row=row, column=col, value=os.path.basename(img_path))


def _section_header(ws, row: int, col: int, text: str) -> None:
    c = ws.cell(row=row, column=col, value=text)
    c.fill = _SALMON_FILL
    c.font = Font(name="Calibri", size=11, bold=True)
    c.border = _THIN


def _label_value(ws, row: int, label: str, value: Any) -> None:
    lc = ws.cell(row=row, column=1, value=label)
    lc.font = Font(name="Calibri", size=11)
    lc.border = _THIN
    vc = ws.cell(row=row, column=3, value=value)
    vc.font = Font(name="Calibri", size=11)
    vc.border = _THIN
    vc.alignment = Alignment(wrap_text=True)


# ── Sheet builders ────────────────────────────────────────────────────────────

def _build_cover(wb: Workbook, report: dict[str, Any], screens: list[dict]) -> None:
    ws = wb.active
    ws.title = "Cover"

    _NAVY_FILL = PatternFill("solid", fgColor="1F497D")
    _BEIGE_FILL = PatternFill("solid", fgColor="FAF3F0")
    _WHITE_FILL = PatternFill("solid", fgColor="FFFFFF")
    _LABEL_FONT = Font(name="Arial", size=11, bold=True, color="555555")
    _VALUE_FONT = Font(name="Arial", size=11, color="333333")
    _SECTION_FONT = Font(name="Arial", size=9, bold=True, color="FFFFFF")
    _BODY_FONT = Font(name="Arial", size=11, color="333333")

    ws.column_dimensions["A"].width = 7.71
    ws.column_dimensions["B"].width = 50
    ws.column_dimensions["C"].width = 40.29
    ws.column_dimensions["D"].width = 7.43

    # Row 1: Logo area (Pivotal Accessibility logo left, IAAP badge right)
    _logo_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "assets")
    _logo_path = os.path.join(_logo_dir, "pivotal_logo.png")
    _badge_path = os.path.join(_logo_dir, "iaap_cpacc_badge.png")
    if os.path.isfile(_logo_path):
        try:
            logo = XLImage(_logo_path)
            logo.width, logo.height = 389, 129
            logo.anchor = "B1"
            ws.add_image(logo)
        except Exception:
            pass

    # Row 2: Title banner (navy sidebar, centered title, IAAP badge)
    ws.row_dimensions[2].height = 72
    ws.merge_cells("B2:C2")
    for col in (1, 4):
        ws.cell(row=2, column=col).fill = _NAVY_FILL
    c2 = ws.cell(row=2, column=2, value=" ")
    c2.font = Font(name="Arial", size=22, bold=True, color="FFFFFF")
    c2.fill = _WHITE_FILL
    c2.alignment = Alignment(horizontal="center", vertical="bottom")
    if os.path.isfile(_badge_path):
        try:
            badge = XLImage(_badge_path)
            badge.width, badge.height = 65, 65
            badge.anchor = "C2"
            ws.add_image(badge)
        except Exception:
            pass

    # Row 3: Title text
    ws.merge_cells("B3:C3")
    c3 = ws.cell(row=3, column=2, value="Digital Accessibility Findings Report")
    c3.font = Font(name="Calibri", size=11, bold=True, color="595959")
    c3.alignment = Alignment(horizontal="center", vertical="bottom")

    # Row 4: ASSET DETAILS section header
    ws.merge_cells("B4:C4")
    c4 = ws.cell(row=4, column=2, value="ASSET DETAILS")
    c4.font = _SECTION_FONT
    c4.fill = _NAVY_FILL
    ws.cell(row=4, column=3).fill = _NAVY_FILL

    # Asset metadata
    first_url = report.get("start_url", "") or (screens[0].get("url", "") if screens else "")
    asset_name = report.get("app_name", "") or report.get("config", "")

    def _cover_label_value(row: int, label: str, value: Any) -> None:
        lc = ws.cell(row=row, column=2, value=label)
        lc.font = _LABEL_FONT
        lc.fill = _BEIGE_FILL
        lc.border = _THIN
        vc = ws.cell(row=row, column=3, value=value)
        vc.font = _VALUE_FONT
        vc.border = _THIN
        vc.alignment = Alignment(wrap_text=True)

    _cover_label_value(5, "Asset / Product Name", asset_name)
    _cover_label_value(6, "Primary URL", first_url)
    _cover_label_value(7, "Additional URLs / Scope", "N/A")

    # Row 9: SUBMISSION DETAILS
    ws.merge_cells("B9:C9")
    c9 = ws.cell(row=9, column=2, value="SUBMISSION DETAILS")
    c9.font = _SECTION_FONT
    c9.fill = _NAVY_FILL
    ws.cell(row=9, column=3).fill = _NAVY_FILL

    _cover_label_value(10, "Date of Submission", "")

    # Row 12: ABOUT THIS AUDIT
    ws.merge_cells("B12:C12")
    c12 = ws.cell(row=12, column=2, value="ABOUT THIS AUDIT")
    c12.font = _SECTION_FONT
    c12.fill = _NAVY_FILL
    ws.cell(row=12, column=3).fill = _NAVY_FILL

    ws.merge_cells("B13:C13")
    boilerplate = (
        "This interim accessibility report has been prepared based on detailed accessibility evaluations "
        "conducted across the digital platforms listed herein. The findings and observations reflect the "
        "current state of accessibility compliance as evaluated using automated accessibility tools. "
        "The audit using manual techniques is currently in progress and a comprehensive audit report "
        "will be submitted upon completion."
    )
    c13 = ws.cell(row=13, column=2, value=boilerplate)
    c13.font = _BODY_FONT
    c13.alignment = Alignment(wrap_text=True, vertical="top")
    c13.border = _THIN

    # Row 15: AUDITOR DETAILS
    ws.merge_cells("B15:C15")
    c15 = ws.cell(row=15, column=2, value="AUDITOR DETAILS")
    c15.font = _SECTION_FONT
    c15.fill = _NAVY_FILL
    ws.cell(row=15, column=3).fill = _NAVY_FILL

    _cover_label_value(16, "Organisation", "Pivotal Accessibility")
    _cover_label_value(17, "IAAP No / Certificate No", "CPWA, Reference No 24MJCWAS008")

    # Row 19: Confidential
    ws.merge_cells("B19:C19")
    c19 = ws.cell(row=19, column=2, value="CONFIDENTIAL \u2014 For client use only")
    c19.font = Font(name="Arial", size=9, color="888888")
    c19.alignment = Alignment(horizontal="center", vertical="bottom")


def _status_cell_style(ws, row_idx: int, col_idx: int, value: str) -> None:
    """Apply status text and fill to a Status sheet cell."""
    c = ws.cell(row=row_idx, column=col_idx, value=value)
    c.border = _THIN
    c.alignment = Alignment(wrap_text=True, vertical="top")
    if value == "Done":
        c.fill = _GREEN_FILL
    elif value == "Issues Found":
        c.fill = _ORANGE_FILL
    elif value == "Manual Required":
        c.fill = _GREY_FILL


def _build_status(wb: Workbook, screens: list[dict]) -> None:
    ws = wb.create_sheet("Status")

    ws.column_dimensions["A"].width = 25
    ws.column_dimensions["B"].width = 55
    ws.column_dimensions["C"].width = 35
    ws.column_dimensions["D"].width = 14
    ws.column_dimensions["E"].width = 13
    ws.column_dimensions["F"].width = 14
    ws.column_dimensions["G"].width = 13
    ws.column_dimensions["H"].width = 14
    ws.column_dimensions["I"].width = 13

    headers = [
        "Page Title", "URLs", "Notes", "Automated Tools",
        "NVDA/Chrome", "Color Contrast",
        "Browser Zoom", "Keyboard-Only", "Text-Spacing",
    ]
    for col_idx, hdr in enumerate(headers, start=1):
        c = ws.cell(row=1, column=col_idx, value=hdr)
        c.fill = _SALMON_FILL
        c.font = _DARK_FONT
        c.alignment = _CENTER
        c.border = _THIN

    ws.freeze_panes = "A2"

    for screen in screens:
        # Determine which checkpoints failed for this screen
        failed_cps = {
            r.get("checkpoint_id", "")
            for r in screen.get("wcag_results", [])
            if r.get("status") == "Fail"
        }

        supp = screen.get("supplementary_tests", {})

        # Status sheet just indicates whether each test was executed — "Done" or "Manual Required"
        automated_val = "Done"
        nvda_val = "Done" if supp.get("screen_reader") else "Manual Required"
        color_contrast_val = "Done" if supp.get("color_contrast") else "Done"
        zoom_val = "Done" if supp.get("zoom") else "Manual Required"
        keyboard_val = "Done" if supp.get("keyboard") else "Manual Required"
        text_spacing_val = "Done" if supp.get("text_spacing") else "Manual Required"

        row_idx = ws.max_row + 1

        # Plain cells: Page Title, URLs, Notes
        for col_idx, val in enumerate(
            [screen.get("title") or screen.get("label", ""), screen.get("url", ""), ""],
            start=1,
        ):
            c = ws.cell(row=row_idx, column=col_idx, value=val)
            c.border = _THIN
            c.alignment = Alignment(wrap_text=True, vertical="top")

        # Status cells with colour coding
        _status_cell_style(ws, row_idx, 4, automated_val)
        _status_cell_style(ws, row_idx, 5, nvda_val)
        _status_cell_style(ws, row_idx, 6, color_contrast_val)
        _status_cell_style(ws, row_idx, 7, zoom_val)
        _status_cell_style(ws, row_idx, 8, keyboard_val)
        _status_cell_style(ws, row_idx, 9, text_spacing_val)


def _build_issues(
    wb: Workbook,
    screens: list[dict],
    uploader: Callable[[str], str] | None,
    issue_index: dict[tuple[str, str], dict] | None = None,
) -> None:
    ws = wb.create_sheet("Issues")

    col_widths = [30, 22, 18, 20, 12, 35, 45, 45, 20, 45, 22, 45, 55, 14, 22, 12, 18]
    col_letters = [get_column_letter(i + 1) for i in range(len(col_widths))]
    for letter, width in zip(col_letters, col_widths):
        ws.column_dimensions[letter].width = width

    headers = [
        "Issue Title", "Page Title", "Type", "AT/Browser", "Severity",
        "Action Performed", "Actual Result", "Expected Result",
        "Failed WCAG 2.2 checkpoint(s)", "Page URL", "Screencast",
        "Suggested Resolutions", "Suggested Code Fix",
        "Dev Status", "Dev Comments", "QA Status", "QA Comments",
    ]
    for col_idx, hdr in enumerate(headers, start=1):
        c = ws.cell(row=1, column=col_idx, value=hdr)
        c.fill = _SALMON_FILL
        c.font = _DARK_FONT
        c.alignment = _CENTER
        c.border = _THIN

    ws.freeze_panes = "A2"

    action_performed = (
        "1) Navigate to the page URL.\n"
        "2) Load the page fully.\n"
        "3) Run automated WCAG 2.1 scan across all checkpoints."
    )

    issue_count = 0
    max_issues = 500

    _CODE_FIX_FILL = PatternFill("solid", fgColor="F2F2F2")
    _CODE_FIX_FONT = Font(name="Consolas", size=9, color="333333")

    for screen in screens:
        page_title = screen.get("title") or screen.get("label", "")
        page_url = screen.get("url", "")
        ann_path = screen.get("annotated_screenshot") or screen.get("screenshot") or ""
        supplementary = screen.get("supplementary", {}) or screen.get("supplementary_tests", {}) or {}

        for result in screen.get("wcag_results", []):
            if result.get("status") != "Fail":
                continue
            if issue_count >= max_issues:
                break

            cid = result.get("checkpoint_id", "")
            cp = _CP_MAP.get(cid)
            cp_title = cp.title if cp else cid
            level = _GUIDELINE_MAP.get(cid, {}).get("level", "A")
            raw_rationale = result.get("rationale", "") or "Accessibility issue detected."
            rationale = _enrich_rationale(cid, raw_rationale, supplementary)
            severity_raw = SEVERITY_MAP.get(cid, "Moderate")
            severity_display = {
                "Critical": "Blocker",
                "High": "High",
                "Moderate": "Medium",
                "Low": "Low",
            }.get(severity_raw, "Medium")

            issue_type = ISSUE_TYPE.get(cid, "Other A11y")
            # Build a clearer issue title from enriched rationale
            title_summary = rationale.split("\n")[0][:80]
            issue_title = f"{issue_type}: {page_title}: {title_summary}"

            expected = EXPECTED_RESULT.get(
                cid,
                f"Content must conform to WCAG 2.1 {cid} {cp_title} (Level {level}).",
            )

            wcag_checkpoint_str = f"{cid} {cp_title} (Level {level})"

            what = WHAT_TO_FIX.get(cid, f"Fix {cp_title} per WCAG 2.1.")
            how_lines = HOW_TO_FIX.get(cid, "")
            if how_lines:
                how_first2 = "\n".join(how_lines.split("\n")[:2])
            else:
                how_first2 = f"1) Identify all failing elements for {cid}\n2) {what}"
            suggested = what + "\n\n" + how_first2

            # Build contextual code fix
            code_fix = _build_contextual_code_fix(cid, result, supplementary)

            # Per-issue targeted screenshot from issue_index
            issue_screenshot_path = ""
            issue_detail_extra = ""
            if issue_index:
                ikey = (page_title, cid)
                idata = issue_index.get(ikey)
                if idata:
                    sel = idata.get("selector", "")
                    bounds = idata.get("bounds", {})
                    idetail = idata.get("detail", "")
                    # Always use the per-issue annotated screenshot — each has
                    # a unique label identifying the specific issue even if the
                    # element target is a fallback
                    issue_screenshot_path = idata.get("crop_path", "") or idata.get("ann_path", "")
                    if sel:
                        issue_detail_extra += f"\nElement: {sel}"
                    if bounds:
                        issue_detail_extra += f"\nBounds: x={bounds.get('x')}, y={bounds.get('y')}, w={bounds.get('width')}, h={bounds.get('height')}"
                    # Always add the issue detail text if it has useful info
                    if idetail and idetail not in rationale:
                        issue_detail_extra += f"\n{idetail}"

            # Add actual axe-core element info when no real targeted screenshot
            if not issue_screenshot_path:
                axe_data = supplementary.get("axe_scan", {})
                axe_violations = axe_data.get("violations", [])
                # Try mapped rules first, then match any violation
                axe_rules = _CP_TO_AXE.get(cid, [])
                matched = False
                for v in axe_violations:
                    if v.get("id") in axe_rules:
                        for node in v.get("nodes", [])[:2]:
                            raw_target = node.get("target", [])
                            # Flatten nested lists (axe-core sometimes returns [[sel]])
                            flat = []
                            for t in raw_target:
                                if isinstance(t, list):
                                    flat.extend(t)
                                else:
                                    flat.append(str(t))
                            node_sel = ", ".join(flat)
                            node_html = node.get("html", "")[:80]
                            if node_sel:
                                issue_detail_extra += f"\nFailing element: {node_sel}"
                                if node_html:
                                    issue_detail_extra += f"\nHTML: {node_html}"
                        matched = True
                        break
                # If no mapped rule, try screen_reader all_issues
                if not matched:
                    sr = supplementary.get("screen_reader", {})
                    for si in sr.get("all_issues", []):
                        desc = si.get("description", "")
                        if desc and desc not in (issue_detail_extra + rationale):
                            issue_detail_extra += f"\nRelated: {desc} ({si.get('count', 1)} instance(s))"
                            matched = True
                            if len(issue_detail_extra) > 300:
                                break

            if issue_detail_extra:
                rationale = rationale.rstrip() + "\n" + issue_detail_extra.strip()

            # Screencast / screenshot cell — prefer per-issue targeted, fall back to page-level
            screenshot_for_upload = issue_screenshot_path or ann_path
            if uploader and screenshot_for_upload and os.path.isfile(screenshot_for_upload):
                screencast_url = uploader(screenshot_for_upload)
            else:
                screencast_url = ""

            row_idx = ws.max_row + 1
            ws.row_dimensions[row_idx].height = 80

            row_values = [
                issue_title,        # A: Issue Title
                page_title,         # B: Page Title
                issue_type,         # C: Type
                "Chrome (Automated WCAG Scanner)",  # D: AT/Browser
                severity_display,   # E: Severity
                action_performed,   # F: Action Performed
                rationale,          # G: Actual Result
                expected,           # H: Expected Result
                wcag_checkpoint_str,  # I: Failed WCAG checkpoint
                page_url,           # J: Page URL
                "",                 # K: Screencast (handled separately)
                suggested,          # L: Suggested Resolutions
                code_fix,           # M: Suggested Code Fix
                "",                 # N: Dev Status
                "",                 # O: Dev Comments
                "",                 # P: QA Status
                "",                 # Q: QA Comments
            ]

            for col_idx, val in enumerate(row_values, start=1):
                c = ws.cell(row=row_idx, column=col_idx, value=val)
                c.border = _THIN
                c.alignment = _WRAP_TOP

            # Severity color coding (col E = 5)
            sev_fill_font = _SEV_FILLS.get(severity_raw)
            if sev_fill_font:
                ws.cell(row=row_idx, column=5).fill = sev_fill_font[0]
                ws.cell(row=row_idx, column=5).font = sev_fill_font[1]

            # Page URL as clickable hyperlink (col J = 10)
            if page_url:
                url_cell = ws.cell(row=row_idx, column=10)
                url_cell.hyperlink = page_url
                url_cell.font = Font(name="Calibri", size=10, color="0563C1", underline="single")
                url_cell.alignment = Alignment(wrap_text=True, vertical="top")

            # Screencast column (col K = 11)
            sc_cell = ws.cell(row=row_idx, column=11)
            if screencast_url:
                sc_cell.value = "View Screenshot"
                sc_cell.hyperlink = screencast_url
                sc_cell.font = Font(name="Calibri", size=10, color="0563C1", underline="single")
                sc_cell.alignment = Alignment(horizontal="center", vertical="center")
            elif ann_path:
                sc_cell.value = os.path.basename(ann_path)
                sc_cell.alignment = _WRAP_TOP

            # Code fix column styling (col M = 13) — monospace on grey bg
            if code_fix:
                code_cell = ws.cell(row=row_idx, column=13)
                code_cell.font = _CODE_FIX_FONT
                code_cell.fill = _CODE_FIX_FILL
                code_cell.alignment = Alignment(wrap_text=True, vertical="top")

            issue_count += 1

        if issue_count >= max_issues:
            break


# ── Public API ────────────────────────────────────────────────────────────────

def _load_issue_index(report: dict[str, Any]) -> dict[tuple[str, str], dict]:
    """Load per-issue data from checklist issue_index.json files."""
    import glob as _glob

    issue_map: dict[tuple[str, str], dict] = {}

    # Derive checklist_reports_root from report or screens
    roots: list[str] = []
    cr_root = report.get("checklist_reports_root", "")
    if cr_root and os.path.isdir(cr_root):
        roots.append(cr_root)

    # Also scan from screen paths
    for s in report.get("screens", []):
        ann = s.get("annotated_screenshot", "")
        if ann:
            # e.g. artifacts/RUN/01-file.png -> artifacts/RUN/pre_login/checklist_reports
            run_dir = os.path.dirname(ann)
            for sub in ["pre_login/checklist_reports", "checklist_reports"]:
                candidate = os.path.join(run_dir, sub)
                if os.path.isdir(candidate) and candidate not in roots:
                    roots.append(candidate)

    for root in roots:
        idx_files = _glob.glob(os.path.join(root, "*/issues/issue_index.json"))
        for idx_file in idx_files:
            try:
                with open(idx_file) as f:
                    idx = __import__("json").load(f)
            except Exception:
                continue
            sc_id = idx.get("sc_id", "")
            base_dir = os.path.dirname(os.path.dirname(os.path.dirname(idx_file)))  # checklist_reports parent
            run_base = os.path.dirname(base_dir)  # pre_login or run dir

            for issue in idx.get("issues", []):
                screen_label = issue.get("screen_label", "")
                crop = issue.get("issue_crop", "")
                crop_full = os.path.join(run_base, crop) if crop else ""
                ann_i = issue.get("issue_annotated_screenshot", "")
                ann_full = os.path.join(run_base, ann_i) if ann_i else ""

                key = (screen_label, sc_id)
                if key not in issue_map:
                    # Prefer crop (visually distinct per element) when it exists and is
                    # meaningful; fall back to full-page annotated screenshot
                    crop_ok = crop_full and os.path.isfile(crop_full) and os.path.getsize(crop_full) > 500
                    best_path = crop_full if crop_ok else (ann_full if (ann_full and os.path.isfile(ann_full)) else crop_full)
                    issue_map[key] = {
                        "crop_path": best_path,
                        "ann_path": ann_full if (ann_full and os.path.isfile(ann_full)) else "",
                        "detail": issue.get("detail", ""),
                        "selector": issue.get("selector", ""),
                        "bounds": issue.get("bounds", {}),
                        "id": issue.get("id", ""),
                    }
    return issue_map


def generate_xlsx_report(
    report: dict[str, Any],
    output_path: str,
    uploader: Callable[[str], str] | None = None,
) -> str:
    """Generate a 3-sheet XLSX accessibility audit report.

    Sheets: Cover, Status, Issues.
    """
    wb = Workbook()
    screens = report.get("screens", [])

    # Load per-issue targeted screenshots and details
    issue_index = _load_issue_index(report)

    _build_cover(wb, report, screens)
    _build_status(wb, screens)
    _build_issues(wb, screens, uploader, issue_index=issue_index)

    wb.save(output_path)
    return output_path
