"""llm-eval-kit: LLM-as-judge framework for evaluating the target model's response quality.

Public surface:
    Rubric, RubricDimension, RUBRIC_COACH
    JudgePair, DimensionScore, JudgeResult, EvalRunSummary
    judge_pair, judge_pairs, summarize, format_markdown_report
    replay_and_pair, generate_response
"""
from .rubric import (
    Rubric,
    RubricDimension,
    RUBRIC_COACH,
)
from .judge import (
    JudgePair,
    DimensionScore,
    JudgeResult,
    EvalRunSummary,
    judge_pair,
    judge_pairs,
    summarize,
)
from .report import format_markdown_report, format_comparison_markdown
from .replay import replay_and_pair, generate_response, PromptBuilder

__all__ = [
    "Rubric",
    "RubricDimension",
    "RUBRIC_COACH",
    "JudgePair",
    "DimensionScore",
    "JudgeResult",
    "EvalRunSummary",
    "judge_pair",
    "judge_pairs",
    "summarize",
    "format_markdown_report",
    "format_comparison_markdown",
    "replay_and_pair",
    "generate_response",
    "PromptBuilder",
]

__version__ = "0.1.0"
