# WCAG Agent App Targets

This document describes what each `wcag-agent --app` shortcut targets.

## 1) `wcag-agent --app LICMFIP`

- Target config: `config/licmf_investor_portal.json`
- Target site: `https://clientwebsitesuat2.kfintech.com/`
- Target name: **LICMF Investor Portal (KFintech UAT)**
- Flow behavior:
  - Starts on investor portal login/landing page.
  - Includes login-oriented actions (PAN/User ID, captcha, OTP).
  - Then runs deep `explore` for post-login route coverage.

## 2) `wcag-agent --app LICMFCW`

- Target config: `config/licmf_corporate_website.json`
- Target site: `https://experiencebeta.licmf.com/`
- Target name: **LICMF Corporate Website (Experience Beta)**
- Flow behavior:
  - **No login required**
  - Pure deep exploration mode from homepage.
  - Scans all reachable routes/screens (menus, tabs, footer pages, etc.) and validates WCAG checks.

## Run Commands

```bash
wcag-agent --app LICMFIP
wcag-agent --app LICMFCW
```

If `wcag-agent` is not on your shell `PATH`, run from project root with:

```bash
PATH="/Users/aviral/Projects/Accessibility-agent-web/.venv/bin:$PATH" wcag-agent --app LICMFIP
PATH="/Users/aviral/Projects/Accessibility-agent-web/.venv/bin:$PATH" wcag-agent --app LICMFCW
```
