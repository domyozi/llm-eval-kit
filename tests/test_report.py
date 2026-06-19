"""Unit tests for report formatting (no Anthropic call)."""
from __future__ import annotations

from llm_eval.report import compare_with_baseline, format_comparison_markdown


def test_compare_no_regression():
    baseline = {"avg_total": 4.0, "avg_by_dimension": {"a": 4.0, "b": 5.0}}
    current = {"avg_total": 4.2, "avg_by_dimension": {"a": 4.1, "b": 4.9}}
    regs = compare_with_baseline(baseline, current, 0.3)
    assert regs == []


def test_compare_detects_regression():
    baseline = {"avg_total": 4.0, "avg_by_dimension": {"a": 4.0, "b": 5.0}}
    current = {"avg_total": 3.5, "avg_by_dimension": {"a": 3.0, "b": 4.6}}
    regs = compare_with_baseline(baseline, current, 0.3)
    # a: -1.0 fails; b: -0.4 fails; avg_total: -0.5 fails
    keys = [r[0] for r in regs]
    assert "a" in keys
    assert "b" in keys
    assert "avg_total" in keys


def test_compare_threshold_just_below_passes():
    """-0.29 is below 0.3 threshold → not a regression."""
    baseline = {"avg_total": 4.0, "avg_by_dimension": {"a": 4.0}}
    current = {"avg_total": 4.0, "avg_by_dimension": {"a": 3.71}}  # delta ≈ -0.29
    regs = compare_with_baseline(baseline, current, 0.3)
    assert regs == []


def test_compare_threshold_above_fails():
    """-0.4 exceeds 0.3 threshold → flagged."""
    baseline = {"avg_total": 4.0, "avg_by_dimension": {"a": 4.0}}
    current = {"avg_total": 4.0, "avg_by_dimension": {"a": 3.6}}  # delta -0.4
    regs = compare_with_baseline(baseline, current, 0.3)
    assert any(r[0] == "a" for r in regs)


def test_format_comparison_markdown_emoji():
    baseline = {"label": "base", "avg_total": 4.0, "avg_by_dimension": {"a": 4.0}}
    current = {"label": "cur", "avg_total": 4.2, "avg_by_dimension": {"a": 5.0}}
    md = format_comparison_markdown(baseline, current)
    assert "🟢" in md
    assert "+1.00" in md or "+1.0" in md
    assert "base" in md and "cur" in md
