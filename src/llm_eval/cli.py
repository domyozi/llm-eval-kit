"""CLI for replay-mode eval. Designed to be the CI entrypoint.

Usage:
    python -m llm_eval.cli \\
        --fixture path/to/fixture.json \\
        --label "pr-current" \\
        --baseline path/to/baseline.json \\
        --fail-threshold 0.3 \\
        --out /tmp/eval-report.md
"""
from __future__ import annotations

import argparse
import asyncio
import importlib
import json
import os
import sys
from pathlib import Path

try:
    from dotenv import load_dotenv  # type: ignore

    load_dotenv()
except ImportError:
    pass

from .judge import (
    DEFAULT_CONCURRENCY,
    DEFAULT_JUDGE_MODEL,
    judge_pairs,
    summarize,
)
from .replay import default_prompt_builder, replay_and_pair
from .report import (
    compare_with_baseline,
    format_comparison_markdown,
    format_markdown_report,
)
from .rubric import RUBRIC_COACH


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="llm-eval", description="LLM-as-judge replay CLI")
    p.add_argument("--fixture", required=True, help="JSON array of {id, user_input, context?}")
    p.add_argument("--label", default="replay")
    p.add_argument("--model", default=DEFAULT_JUDGE_MODEL)
    p.add_argument("--concurrency", type=int, default=DEFAULT_CONCURRENCY)
    p.add_argument("--out", default=None, help="markdown report path (default: stdout)")
    p.add_argument("--json-out", default=None, help="raw scores JSON path")
    p.add_argument("--baseline", default=None, help="baseline JSON to diff against")
    p.add_argument("--fail-threshold", type=float, default=0.3)
    p.add_argument(
        "--update-baseline",
        default=None,
        help="write current scores to this path as new baseline (use after merge)",
    )
    p.add_argument(
        "--prompt-builder",
        default=None,
        help=(
            "Dotted import path to a callable `(entry: dict) -> (system, user)`. "
            "Defaults to llm_eval.replay.default_prompt_builder."
        ),
    )
    return p.parse_args()


def _load_prompt_builder(dotted: str | None):
    if not dotted:
        return default_prompt_builder
    mod_path, _, attr = dotted.rpartition(".")
    if not mod_path:
        raise ValueError(f"prompt-builder must be dotted (got {dotted!r})")
    mod = importlib.import_module(mod_path)
    return getattr(mod, attr)


async def _amain(args: argparse.Namespace) -> int:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ERROR: ANTHROPIC_API_KEY is not set", file=sys.stderr)
        return 2

    fixture_path = Path(args.fixture)
    if not fixture_path.exists():
        print(f"ERROR: fixture not found: {fixture_path}", file=sys.stderr)
        return 2

    entries = json.loads(fixture_path.read_text(encoding="utf-8"))
    if not isinstance(entries, list):
        print("ERROR: fixture must be a JSON array", file=sys.stderr)
        return 2

    builder = _load_prompt_builder(args.prompt_builder)

    print(
        f"[1/3] replay generating ({len(entries)} pairs, model={args.model})…",
        file=sys.stderr,
    )
    pairs = await replay_and_pair(
        entries,
        prompt_builder=builder,
        concurrency=args.concurrency,
        model=args.model,
    )

    print("[2/3] judging…", file=sys.stderr)
    results = await judge_pairs(
        pairs, rubric=RUBRIC_COACH, concurrency=args.concurrency, model=args.model
    )

    summary = summarize(results, label=args.label, model=args.model, rubric=RUBRIC_COACH)
    print(
        f"[3/3] done. avg_total={summary.avg_total} / 5, errors={summary.error_count}",
        file=sys.stderr,
    )

    md = format_markdown_report(summary, rubric=RUBRIC_COACH)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(md, encoding="utf-8")
        print(f"[report] {args.out}", file=sys.stderr)
    else:
        print(md)

    scores_json = {
        "label": summary.label,
        "model": summary.model,
        "pair_count": summary.pair_count,
        "error_count": summary.error_count,
        "avg_total": summary.avg_total,
        "avg_by_dimension": summary.avg_by_dimension,
    }
    if args.json_out:
        Path(args.json_out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json_out).write_text(
            json.dumps(scores_json, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    if args.update_baseline:
        baseline_path = Path(args.update_baseline)
        baseline_path.parent.mkdir(parents=True, exist_ok=True)
        baseline_path.write_text(
            json.dumps(scores_json, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"[baseline updated] {baseline_path}", file=sys.stderr)
        return 0

    if args.baseline:
        baseline_path = Path(args.baseline)
        if not baseline_path.exists():
            print(f"WARN: baseline not found ({baseline_path}); skipping", file=sys.stderr)
        else:
            baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
            regressions = compare_with_baseline(baseline, scores_json, args.fail_threshold)
            comparison_md = format_comparison_markdown(baseline, scores_json)
            print("\n" + comparison_md, file=sys.stderr)
            if args.out:
                with open(args.out, "a", encoding="utf-8") as f:
                    f.write("\n\n" + comparison_md)
            if regressions:
                print(
                    f"\nFAIL: {len(regressions)} dimension(s) regressed by > {args.fail_threshold}",
                    file=sys.stderr,
                )
                for dim, delta in regressions:
                    print(f"  - {dim}: {delta:+.2f}", file=sys.stderr)
                return 1
    return 0


def main() -> int:
    return asyncio.run(_amain(_parse_args()))


if __name__ == "__main__":
    sys.exit(main())
