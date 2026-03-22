# Accessibility LangGraph Scanner

WCAG 2.1 government-style website scanner built with Python, Playwright, and LangGraph.

## What it does

- Crawls a domain with bounded BFS (`max_depth`, `max_pages`)
- Evaluates checkpoints in 4 buckets:
  - Content Equivalence
  - Layout and Perception
  - Interaction and Navigation
  - Semantics and Transaction Integrity
- Returns checkpoint status as one of:
  - `Pass`
  - `Fail`
  - `Cannot verify automatically`
  - `Not applicable`
- Applies strict policy by default: any `Fail` or `Cannot verify automatically` means non-compliant.

## Quick start

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .[dev]
playwright install chromium
wcag-scanner --url https://example.gov --domain example.gov
```

Artifacts are written to `artifacts/{run_id}/` including DOM snapshots, evidence index, and `scan-report.json`.
