"""Markdown report formatters: summary report + baseline vs current diff."""
from __future__ import annotations

from .judge import EvalRunSummary
from .rubric import Rubric, RUBRIC_COACH


def format_markdown_report(
    summary: EvalRunSummary,
    *,
    rubric: Rubric = RUBRIC_COACH,
    worst_n: int = 3,
) -> str:
    lines: list[str] = []
    lines.append(f"# Eval — {summary.label}")
    lines.append("")
    lines.append(f"- model: `{summary.model}`")
    lines.append(
        f"- pairs evaluated: **{summary.pair_count}** (errors: {summary.error_count})"
    )
    lines.append(f"- timestamp: {summary.started_at.isoformat()}")
    lines.append(f"- **avg total**: **{summary.avg_total}** / 5")
    lines.append("")
    lines.append("## Dimension averages")
    lines.append("")
    lines.append("| dimension | avg |")
    lines.append("|---|---|")
    for d in rubric.dimensions:
        lines.append(
            f"| {d.key} ({d.label}) | {summary.avg_by_dimension.get(d.key, 0.0)} |"
        )
    lines.append("")
    lines.append("## Worst examples")
    lines.append("")
    worst = sorted([r for r in summary.results if r.ok], key=lambda r: r.total)[:worst_n]
    if not worst:
        lines.append("_(no successful results)_")
    for i, res in enumerate(worst, 1):
        lines.append(f"### #{i} — total {res.total:.2f}")
        lines.append("")
        lines.append(
            f"- pair: user `{res.pair.user_input_id or '?'}` → ai `{res.pair.ai_response_id or '?'}`"
        )
        lines.append(f"- observation: {res.observation or '(none)'}")
        lines.append("")
        lines.append(f"> **user**: {res.pair.short_user()}")
        lines.append(f"> **ai**: {res.pair.short_ai()}")
        lines.append("")
        for s in res.scores:
            lines.append(f"- **{s.key}**: {s.score} — {s.rationale}")
        lines.append("")
    if summary.error_count > 0:
        lines.append("## Errors")
        lines.append("")
        for res in summary.results:
            if not res.ok:
                lines.append(
                    f"- pair `{res.pair.user_input_id or '?'}`/"
                    f"`{res.pair.ai_response_id or '?'}`: {res.error}"
                )
        lines.append("")
    return "\n".join(lines)


def format_comparison_markdown(baseline: dict, current: dict) -> str:
    """baseline vs current の Δ 比較表を返す。
    入力は CLI で --json-out / --baseline が書き出す形式と同じ dict。"""
    base_dims = baseline.get("avg_by_dimension") or {}
    cur_dims = current.get("avg_by_dimension") or {}
    lines = [
        "## Baseline vs Current",
        "",
        f"- baseline label: `{baseline.get('label', 'baseline')}`",
        f"- current label: `{current.get('label', 'current')}`",
        "",
        "| dimension | baseline | current | Δ |",
        "|---|---|---|---|",
    ]
    all_keys = sorted(set(base_dims.keys()) | set(cur_dims.keys()))
    for k in all_keys:
        b = float(base_dims.get(k, 0.0))
        c = float(cur_dims.get(k, 0.0))
        delta = c - b
        emoji = "🟢" if delta > 0.05 else "🔴" if delta < -0.05 else "⚪"
        lines.append(f"| {k} | {b:.2f} | {c:.2f} | {emoji} {delta:+.2f} |")
    base_total = float(baseline.get("avg_total", 0.0))
    cur_total = float(current.get("avg_total", 0.0))
    total_delta = cur_total - base_total
    total_emoji = "🟢" if total_delta > 0.05 else "🔴" if total_delta < -0.05 else "⚪"
    lines.append(
        f"| **avg_total** | **{base_total:.2f}** | **{cur_total:.2f}** | "
        f"{total_emoji} **{total_delta:+.2f}** |"
    )
    return "\n".join(lines)


def compare_with_baseline(
    baseline: dict, current: dict, fail_threshold: float
) -> list[tuple[str, float]]:
    """Return [(dimension, delta)] for dimensions that regressed by >= fail_threshold."""
    regressions: list[tuple[str, float]] = []
    base_dims = baseline.get("avg_by_dimension") or {}
    cur_dims = current.get("avg_by_dimension") or {}
    for key, base_val in base_dims.items():
        cur_val = cur_dims.get(key, 0.0)
        delta = float(cur_val) - float(base_val)
        if delta <= -fail_threshold:
            regressions.append((key, delta))
    base_total = float(baseline.get("avg_total", 0.0))
    cur_total = float(current.get("avg_total", 0.0))
    total_delta = cur_total - base_total
    if total_delta <= -fail_threshold:
        regressions.append(("avg_total", total_delta))
    return regressions
