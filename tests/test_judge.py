"""Unit tests for llm_eval.judge (pure logic, no LLM API call)."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from llm_eval.judge import (
    DimensionScore,
    JudgePair,
    JudgeResult,
    _extract_first_json_object,
    _parse_judge_output,
    judge_pair,
    summarize,
)
from llm_eval.rubric import RUBRIC_COACH


# ────────────────────── JSON extraction ──────────────────────


def test_extract_first_json_object_simple():
    assert _extract_first_json_object('xxx { "a": 1 } yyy') == '{ "a": 1 }'


def test_extract_first_json_object_nested():
    s = 'pre { "a": { "b": [1, {"c": 2}] }, "d": 3 } post'
    assert _extract_first_json_object(s) == '{ "a": { "b": [1, {"c": 2}] }, "d": 3 }'


def test_extract_first_json_object_string_with_braces():
    # quoted braces shouldn't affect depth count
    assert _extract_first_json_object('{ "k": "}{" }') == '{ "k": "}{" }'


def test_extract_first_json_object_none():
    assert _extract_first_json_object("no json here") is None


# ────────────────────── output parser ──────────────────────


def test_parse_judge_output_well_formed():
    sample = """<observation>核心は集中時間の確保。</observation>
<scores>
{
  "relevance": {"score": 5, "rationale": "ok"},
  "specificity": {"score": 4, "rationale": "ok"},
  "actionability": {"score": 4, "rationale": "ok"},
  "tone_fit": {"score": 5, "rationale": "ok"}
}
</scores>
"""
    obs, scores = _parse_judge_output(sample)
    assert "集中時間" in obs
    assert scores["relevance"]["score"] == 5


def test_parse_judge_output_fallback_no_tags():
    sample = """just a JSON dump:
{"relevance": {"score": 4, "rationale": "r"}, "specificity": {"score": 2, "rationale": "s"},
 "actionability": {"score": 4, "rationale": "a"}, "tone_fit": {"score": 5, "rationale": "t"}}
trailing noise
"""
    obs, scores = _parse_judge_output(sample)
    assert obs == ""
    assert scores["specificity"]["score"] == 2


def test_parse_judge_output_handles_trailing_text():
    sample = """<scores>
{"relevance": {"score": 4, "rationale": "r"}, "specificity": {"score": 4, "rationale": "r"},
 "actionability": {"score": 4, "rationale": "r"}, "tone_fit": {"score": 5, "rationale": "r"}}
</scores>
additional comments after JSON
"""
    _, scores = _parse_judge_output(sample)
    assert scores["tone_fit"]["score"] == 5


# ────────────────────── judge_pair (mocked Anthropic) ──────────────────────


@pytest.mark.asyncio
async def test_judge_pair_success_mocked():
    pair = JudgePair(user_input="集中したい", ai_response="9-11時を集中時間に")
    fake = """<observation>OK</observation>
<scores>
{
  "relevance": {"score": 5, "rationale": "ok"},
  "specificity": {"score": 4, "rationale": "ok"},
  "actionability": {"score": 4, "rationale": "ok"},
  "tone_fit": {"score": 5, "rationale": "ok"}
}
</scores>"""
    with patch("llm_eval.judge._call_anthropic", new=AsyncMock(return_value=fake)):
        result = await judge_pair(pair)
    assert result.ok
    assert len(result.scores) == 4
    assert {s.key for s in result.scores} == set(RUBRIC_COACH.keys)
    assert 4.0 <= result.total <= 5.0


@pytest.mark.asyncio
async def test_judge_pair_invalid_score_3():
    """Score 3 is intentionally excluded; values not in {1,2,4,5} should fail."""
    pair = JudgePair(user_input="x", ai_response="y")
    fake = """<scores>
{
  "relevance": {"score": 3, "rationale": "r"},
  "specificity": {"score": 4, "rationale": "r"},
  "actionability": {"score": 4, "rationale": "r"},
  "tone_fit": {"score": 5, "rationale": "r"}
}
</scores>"""
    with patch("llm_eval.judge._call_anthropic", new=AsyncMock(return_value=fake)):
        result = await judge_pair(pair)
    assert not result.ok
    assert result.error and "invalid score" in result.error


@pytest.mark.asyncio
async def test_judge_pair_missing_dimension():
    pair = JudgePair(user_input="x", ai_response="y")
    fake = """<scores>
{"relevance": {"score": 4, "rationale": "r"},
 "specificity": {"score": 4, "rationale": "r"},
 "tone_fit": {"score": 5, "rationale": "r"}}
</scores>"""
    with patch("llm_eval.judge._call_anthropic", new=AsyncMock(return_value=fake)):
        result = await judge_pair(pair)
    assert not result.ok
    assert result.error and "missing dimension" in result.error


# ────────────────────── summarize ──────────────────────


def test_summarize_aggregates_correctly():
    pair = JudgePair(user_input="x", ai_response="y", user_input_id="u1", ai_response_id="a1")
    res = JudgeResult(
        pair=pair,
        observation="OK",
        scores=[
            DimensionScore(key="relevance", score=5, rationale="r1"),
            DimensionScore(key="specificity", score=4, rationale="r2"),
            DimensionScore(key="actionability", score=4, rationale="r3"),
            DimensionScore(key="tone_fit", score=5, rationale="r4"),
        ],
    )
    summary = summarize([res], label="t1", model="claude-haiku-test")
    assert summary.pair_count == 1
    assert summary.error_count == 0
    assert summary.avg_total == 4.5
    assert summary.avg_by_dimension["relevance"] == 5.0


def test_summarize_skips_errored():
    err = JudgeResult(
        pair=JudgePair(user_input="x", ai_response="y"),
        scores=[],
        error="ParseError: ...",
    )
    summary = summarize([err], label="t", model="m")
    assert summary.pair_count == 0
    assert summary.error_count == 1
