"""Organize detailed checklist reports into DroidBot-style folders."""
from __future__ import annotations

import datetime as _dt
import json
import logging
import shutil
from pathlib import Path
from typing import Any

from .agent.annotator import annotate_issue_collection
from .checklist_registry import ChecklistSpec, load_checklist_specs

logger = logging.getLogger(__name__)


def generate_checklist_reports(report: dict[str, Any], run_dir: str | Path) -> str:
    root = Path(run_dir) / "checklist_reports"
    root.mkdir(parents=True, exist_ok=True)
    screen_map = {
        str(screen.get("label") or f"screen-{idx}"): (idx, screen)
        for idx, screen in enumerate(report.get("screens", []), start=1)
    }

    for spec in load_checklist_specs():
        _write_single_checklist_report(root, report, screen_map, spec)

    return str(root)


def _write_single_checklist_report(
    root: Path,
    report: dict[str, Any],
    screen_map: dict[str, tuple[int, dict[str, Any]]],
    spec: ChecklistSpec,
) -> None:
    folder = root / f"{spec.sequence:02d}_{spec.slug}"
    raw_dir = folder / "states" / "raw"
    annotated_dir = folder / "states" / "annotated"
    issues_dir = folder / "issues"
    raw_dir.mkdir(parents=True, exist_ok=True)
    annotated_dir.mkdir(parents=True, exist_ok=True)
    issues_dir.mkdir(parents=True, exist_ok=True)

    checklist_entry = next(
        (item for item in report.get("checklist_rollup", []) if item.get("sc_id") == spec.sc_id),
        None,
    )
    screen_analyses: list[dict[str, Any]] = []
    passed_screens: set[str] = set()
    issue_screens: set[str] = set()
    issue_index = {
        "generated_at_utc": _utc_now(),
        "run_id": report.get("run_id"),
        "scan_mode": report.get("scan_mode", "full_scan"),
        "checklist_name": spec.sc_title,
        "checklist_index": spec.sequence,
        "sc_id": spec.sc_id,
        "issues": [],
    }
    issue_seq = 0

    for evaluation in checklist_entry.get("screen_evaluations", []) if checklist_entry else []:
        screen_label = str(evaluation.get("screen_label") or "")
        if not screen_label or screen_label not in screen_map:
            continue
        screen_index, screen = screen_map[screen_label]
        screen_tag = _screen_tag(screen_index, screen_label)

        raw_screenshot_dst = raw_dir / f"state_{screen_tag}.png"
        raw_dom_dst = raw_dir / f"state_{screen_tag}.html"
        raw_page_info_dst = raw_dir / f"state_{screen_tag}-page-info.json"
        raw_wcag_dst = raw_dir / f"state_{screen_tag}-wcag-results.json"
        _safe_copy(screen.get("screenshot"), raw_screenshot_dst)
        _safe_copy(screen.get("dom_dump"), raw_dom_dst)
        _safe_copy(screen.get("page_info_dump"), raw_page_info_dst)
        _safe_copy(screen.get("wcag_results_dump"), raw_wcag_dst)

        screen_issues = []
        for issue in evaluation.get("issues", []):
            issue_seq += 1
            issue_id = f"ISSUE-{issue_seq:03d}"
            screen_issues.append({**issue, "id": issue_id})

        if evaluation.get("status") == "Pass":
            passed_screens.add(screen_label)
        if screen_issues:
            issue_screens.add(screen_label)

        annotated_path = annotated_dir / f"state_{screen_tag}_annotated.png"
        crop_map: dict[str, str] = {}
        issue_annotated_map: dict[str, str] = {}
        if raw_screenshot_dst.exists() and screen_issues:
            outputs = annotate_issue_collection(
                screenshot_path=str(raw_screenshot_dst),
                issues=screen_issues,
                annotated_path=str(annotated_path),
                crop_dir=str(issues_dir),
            )
            crop_map = {issue_id: path for issue_id, path in outputs.get("crops", [])}
            issue_annotated_map = {
                issue_id: path for issue_id, path in outputs.get("issue_annotated", [])
            }

        evidence = {
            "raw_screenshot": _as_rel(raw_screenshot_dst, root.parent) if raw_screenshot_dst.exists() else None,
            "annotated_screenshot": _as_rel(annotated_path, root.parent) if annotated_path.exists() else None,
            "raw_dom": _as_rel(raw_dom_dst, root.parent) if raw_dom_dst.exists() else None,
            "page_info_dump": _as_rel(raw_page_info_dst, root.parent) if raw_page_info_dst.exists() else None,
            "wcag_results_dump": _as_rel(raw_wcag_dst, root.parent) if raw_wcag_dst.exists() else None,
        }

        screen_analyses.append(
            {
                **evaluation,
                "screen_tag": screen_tag,
                "evidence": evidence,
                "issues": screen_issues,
            }
        )

        for issue in screen_issues:
            issue_index["issues"].append(
                {
                    "id": issue["id"],
                    "screen_tag": screen_tag,
                    "screen_label": screen_label,
                    "page_url": evaluation.get("page_url"),
                    "issue_type": issue.get("issue_type"),
                    "severity": issue.get("severity"),
                    "detail": issue.get("detail"),
                    "bounds": issue.get("bounds"),
                    "xml_line": issue.get("xml_line"),
                    "selector": issue.get("selector"),
                    "tag": issue.get("tag"),
                    "wcag_criteria": issue.get("wcag_criteria") or [spec.sc_id],
                    "raw_screenshot": evidence["raw_screenshot"],
                    "annotated_screenshot": evidence["annotated_screenshot"],
                    "issue_annotated_screenshot": _as_rel(issue_annotated_map[issue["id"]], root.parent)
                    if issue["id"] in issue_annotated_map
                    else None,
                    "issue_crop": _as_rel(crop_map[issue["id"]], root.parent) if issue["id"] in crop_map else None,
                    "source": issue.get("source"),
                }
            )

    summary = _build_checklist_summary(spec, checklist_entry or {}, screen_analyses)
    checklist_payload = {
        "generated_at_utc": _utc_now(),
        "run_id": report.get("run_id"),
        "config": report.get("config"),
        "scan_mode": report.get("scan_mode", "full_scan"),
        "checklist": spec.to_dict(),
        "summary": summary,
        "screen_analyses": screen_analyses,
    }

    json_path = folder / f"checklist_{spec.sequence:02d}_{spec.slug}.json"
    md_path = folder / f"checklist_{spec.sequence:02d}_{spec.slug}.md"
    issue_index_path = issues_dir / "issue_index.json"
    json_path.write_text(json.dumps(checklist_payload, indent=2, ensure_ascii=False), encoding="utf-8")
    md_path.write_text(_render_checklist_markdown(spec, summary, screen_analyses), encoding="utf-8")
    issue_index_path.write_text(json.dumps(issue_index, indent=2, ensure_ascii=False), encoding="utf-8")
    _write_screen_notes(folder, passed_screens, issue_screens)


def _build_checklist_summary(
    spec: ChecklistSpec,
    checklist_entry: dict[str, Any],
    screen_analyses: list[dict[str, Any]],
) -> dict[str, Any]:
    statuses = [item.get("status") for item in screen_analyses if item.get("status")]
    status_counts: dict[str, int] = {}
    for status in statuses:
        status_counts[status] = status_counts.get(status, 0) + 1
    issue_count = sum(len(item.get("issues", [])) for item in screen_analyses)
    return {
        "sc_id": spec.sc_id,
        "sc_title": spec.sc_title,
        "aggregate_status": checklist_entry.get("aggregate_status", "Not evaluated"),
        "screen_count": len(screen_analyses),
        "issue_count": issue_count,
        "status_counts": status_counts,
        "pages": checklist_entry.get("pages", []),
    }


def _render_checklist_markdown(
    spec: ChecklistSpec,
    summary: dict[str, Any],
    screen_analyses: list[dict[str, Any]],
) -> str:
    lines = [
        f"# {spec.sequence:02d}. {spec.sc_title} ({spec.sc_id})",
        "",
        f"- Principle: {spec.principle}",
        f"- Guideline: {spec.guideline}",
        f"- Level: {spec.level}",
        f"- Aggregate status: {summary.get('aggregate_status', 'Not evaluated')}",
        f"- Screens evaluated: {summary.get('screen_count', 0)}",
        f"- Issues captured: {summary.get('issue_count', 0)}",
        "",
        "## Requirement Summary",
        spec.requirement_summary or "-",
        "",
        "## Automated Agent Goal",
        spec.automated_agent_goal or "-",
        "",
        "## Preconditions / Test Data",
        spec.preconditions_test_data or "-",
        "",
        "## Required Evidence For LLM",
        spec.required_evidence_for_llm or "-",
        "",
        "## Screen Outcomes",
    ]
    if not screen_analyses:
        lines.append("- No matching screen evaluations were recorded.")
    else:
        for item in screen_analyses:
            lines.append(
                f"- [{item.get('status', 'Not evaluated')}] {item.get('screen_label')} :: "
                f"{item.get('page_url')} :: {item.get('rationale', '')}"
            )
    lines.append("")
    return "\n".join(lines)


def _write_screen_notes(folder: Path, passed_screens: set[str], issue_screens: set[str]) -> None:
    (folder / "passed_screens.md").write_text(
        "# Passed Screens\n\n"
        + ("\n".join(f"- {item}" for item in sorted(passed_screens)) if passed_screens else "- None")
        + "\n",
        encoding="utf-8",
    )
    (folder / "issue_screens.md").write_text(
        "# Issue Screens\n\n"
        + ("\n".join(f"- {item}" for item in sorted(issue_screens)) if issue_screens else "- None")
        + "\n",
        encoding="utf-8",
    )


def _safe_copy(src: str | None, dst: Path) -> bool:
    if not src:
        return False
    src_path = Path(src)
    if not src_path.exists():
        return False
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src_path, dst)
    return True


def _screen_tag(index: int, label: str) -> str:
    slug = "".join(ch.lower() if ch.isalnum() else "_" for ch in label).strip("_")
    slug = "_".join(part for part in slug.split("_") if part)
    return f"{index:02d}_{slug[:48] or 'screen'}"


def _as_rel(path: str | Path, root: Path) -> str:
    candidate = Path(path)
    try:
        return str(candidate.relative_to(root))
    except Exception:
        return str(candidate)


def _utc_now() -> str:
    return _dt.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"

