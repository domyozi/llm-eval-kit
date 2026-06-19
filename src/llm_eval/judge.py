"""LLM-as-judge: score (user_input, ai_response) pairs against a Rubric.

Design choices:
- The judge is itself an LLM call. We use Anthropic directly (no LangChain) and force
  XML-structured output: <observation> → <scores> JSON. Observation surfaces the judge's
  reasoning (CoT-style) and makes rationale auditable.
- Rubric is pluggable (any `Rubric` instance). Default is RUBRIC_COACH for coach-style apps.
- Errors are captured as JudgeResult.error rather than raised, so a batch of N pairs
  partially succeeds. Aggregation skips errored items.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import statistics
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable, Optional

import anthropic

from .rubric import Rubric, RUBRIC_COACH

logger = logging.getLogger(__name__)


DEFAULT_CONCURRENCY = 4
DEFAULT_JUDGE_MODEL = "claude-haiku-4-5-20251001"


# ───────────────────────── Data structures ─────────────────────────


@dataclass
class JudgePair:
    """One (user_input, ai_response) pair to be judged.

    Attributes are intentionally loose so callers can plug in IDs from their own DB
    (Postgres row id, file path, etc). For totally synthetic pairs, you can leave them
    as empty strings.
    """

    user_input: str
    ai_response: str
    user_input_id: str = ""
    ai_response_id: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def short_user(self, n: int = 200) -> str:
        return self.user_input[:n] + ("…" if len(self.user_input) > n else "")

    def short_ai(self, n: int = 240) -> str:
        return self.ai_response[:n] + ("…" if len(self.ai_response) > n else "")


@dataclass
class DimensionScore:
    key: str
    score: int
    rationale: str


@dataclass
class JudgeResult:
    pair: JudgePair
    scores: list[DimensionScore]
    observation: str = ""
    error: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.error is None and len(self.scores) > 0

    @property
    def total(self) -> float:
        if not self.scores:
            return 0.0
        return sum(s.score for s in self.scores) / len(self.scores)


@dataclass
class EvalRunSummary:
    label: str
    model: str
    pair_count: int
    avg_total: float
    avg_by_dimension: dict[str, float]
    error_count: int
    started_at: datetime
    finished_at: datetime
    results: list[JudgeResult] = field(default_factory=list)


# ───────────────────────── Judge call ─────────────────────────


# NOTE: .format() を使うと "{score, rationale}" の braces を placeholder と誤読する
# (KeyError: 'score, rationale')。str.replace() でプレースホルダ展開する。
_JUDGE_SYSTEM_PROMPT_TEMPLATE = (
    "あなたは LLM 応答品質の評価者です。\n"
    "ユーザー入力に対する AI 応答を rubric に従って 1〜5 で採点します。\n"
    "ただし 3 (中央) は採用せず、1/2/4/5 のいずれかから選んでください。\n"
    "採点前に <observation> セクションで核心の論点と AI 応答の特徴を 1〜2 行で述べ、\n"
    "その後 <scores> 内に JSON で各 dimension の {score, rationale} を返してください。\n"
    "rationale は 30〜80 字程度の日本語で具体的に。\n"
    "\n__RUBRIC_XML__\n"
)


def _build_judge_system_prompt(rubric: Rubric) -> str:
    return _JUDGE_SYSTEM_PROMPT_TEMPLATE.replace("__RUBRIC_XML__", rubric.to_prompt_xml())


def _build_judge_user_message(pair: JudgePair, rubric: Rubric) -> str:
    keys_example = ",\n  ".join(
        f'"{k}": {{"score": 4, "rationale": "..."}}' for k in rubric.keys
    )
    return (
        "<eval_pair>\n"
        "  <user_input>\n"
        f"{pair.user_input}\n"
        "  </user_input>\n"
        "  <ai_response>\n"
        f"{pair.ai_response}\n"
        "  </ai_response>\n"
        "</eval_pair>\n"
        "\n"
        "上記を rubric に従って採点してください。\n"
        "回答フォーマット:\n"
        "<observation>...</observation>\n"
        "<scores>\n"
        "{\n  " + keys_example + "\n}\n"
        "</scores>"
    )


# ───────────────────────── Parser ─────────────────────────


_OBSERVATION_RE = re.compile(r"<observation>(.*?)</observation>", re.S)
_SCORES_TAG_RE = re.compile(r"<scores>\s*([\s\S]*?)\s*</scores>", re.S)


def _extract_first_json_object(text: str) -> str | None:
    """Extract the first balanced JSON object string from `text`.
    Handles nested {} and string-escaped braces correctly."""
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
        else:
            if ch == '"':
                in_string = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return text[start : i + 1]
    return None


def _parse_judge_output(text: str) -> tuple[str, dict[str, dict[str, Any]]]:
    """Parse the judge LLM's output into (observation, scores_dict).
    Falls back to bare-JSON extraction if XML tags are absent."""
    obs_match = _OBSERVATION_RE.search(text)
    observation = (obs_match.group(1).strip() if obs_match else "").strip()

    tag_match = _SCORES_TAG_RE.search(text)
    if tag_match:
        scores_json = _extract_first_json_object(tag_match.group(1))
        if not scores_json:
            scores_json = tag_match.group(1).strip()
    else:
        scores_json = _extract_first_json_object(text)
        if not scores_json:
            raise ValueError("scores JSON not found")

    parsed, _ = json.JSONDecoder().raw_decode(scores_json.strip())
    if not isinstance(parsed, dict):
        raise ValueError("scores JSON is not an object")
    return observation, parsed


# ───────────────────────── Anthropic client (lazy) ─────────────────────────


_client: anthropic.AsyncAnthropic | None = None


def _get_client() -> anthropic.AsyncAnthropic:
    global _client
    if _client is None:
        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not api_key:
            raise RuntimeError("ANTHROPIC_API_KEY is not set")
        _client = anthropic.AsyncAnthropic(api_key=api_key)
    return _client


async def _call_anthropic(
    *,
    system: str,
    user_message: str,
    model: str,
    max_tokens: int = 800,
) -> str:
    """Single Anthropic message call, returning concatenated text blocks."""
    client = _get_client()
    response = await client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user_message}],
    )
    return "".join(
        getattr(block, "text", "")
        for block in response.content
        if getattr(block, "type", "") == "text"
    )


# ───────────────────────── Public API ─────────────────────────


async def judge_pair(
    pair: JudgePair,
    *,
    rubric: Rubric = RUBRIC_COACH,
    model: str = DEFAULT_JUDGE_MODEL,
    max_tokens: int = 800,
) -> JudgeResult:
    """Score one pair. Errors are returned in JudgeResult.error, never raised."""
    try:
        text = await _call_anthropic(
            system=_build_judge_system_prompt(rubric),
            user_message=_build_judge_user_message(pair, rubric),
            model=model,
            max_tokens=max_tokens,
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("judge_pair: anthropic call failed: %s", e)
        return JudgeResult(pair=pair, scores=[], error=f"{type(e).__name__}: {e}")

    try:
        observation, scores_dict = _parse_judge_output(text)
    except Exception as e:  # noqa: BLE001
        logger.warning("judge_pair: parse failed: %s", e)
        return JudgeResult(pair=pair, scores=[], error=f"ParseError: {e}")

    parsed: list[DimensionScore] = []
    for d in rubric.dimensions:
        entry = scores_dict.get(d.key)
        if not isinstance(entry, dict):
            return JudgeResult(
                pair=pair,
                scores=[],
                observation=observation,
                error=f"missing dimension: {d.key}",
            )
        score = entry.get("score")
        rationale = entry.get("rationale") or ""
        if not isinstance(score, (int, float)) or int(score) not in (1, 2, 4, 5):
            return JudgeResult(
                pair=pair,
                scores=[],
                observation=observation,
                error=f"invalid score for {d.key}: {score}",
            )
        parsed.append(DimensionScore(key=d.key, score=int(score), rationale=str(rationale)))

    return JudgeResult(pair=pair, scores=parsed, observation=observation)


async def judge_pairs(
    pairs: Iterable[JudgePair],
    *,
    rubric: Rubric = RUBRIC_COACH,
    concurrency: int = DEFAULT_CONCURRENCY,
    model: str = DEFAULT_JUDGE_MODEL,
) -> list[JudgeResult]:
    """Score many pairs in parallel (semaphore-limited)."""
    sem = asyncio.Semaphore(concurrency)

    async def _bound(p: JudgePair) -> JudgeResult:
        async with sem:
            return await judge_pair(p, rubric=rubric, model=model)

    return list(await asyncio.gather(*[_bound(p) for p in pairs]))


def summarize(
    results: list[JudgeResult],
    *,
    label: str,
    model: str,
    rubric: Rubric = RUBRIC_COACH,
) -> EvalRunSummary:
    """Aggregate a batch of JudgeResult into an EvalRunSummary."""
    ok_results = [r for r in results if r.ok]
    error_count = len(results) - len(ok_results)
    avg_by_dim: dict[str, float] = {}
    for d in rubric.dimensions:
        scores = [
            next(s.score for s in res.scores if s.key == d.key)
            for res in ok_results
        ]
        avg_by_dim[d.key] = round(statistics.mean(scores), 2) if scores else 0.0
    avg_total = (
        round(statistics.mean([r.total for r in ok_results]), 2) if ok_results else 0.0
    )
    now = datetime.now(timezone.utc)
    return EvalRunSummary(
        label=label,
        model=model,
        pair_count=len(ok_results),
        avg_total=avg_total,
        avg_by_dimension=avg_by_dim,
        error_count=error_count,
        started_at=now,
        finished_at=now,
        results=results,
    )
