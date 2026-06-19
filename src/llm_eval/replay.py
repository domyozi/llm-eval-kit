"""Replay mode: given fixed user_inputs, generate fresh AI responses with
the current prompt, then judge them.

This is what enables A/B testing of prompt changes in CI: keep the user_inputs
fixed, see how the same inputs are answered differently by the new prompt.

The `PromptBuilder` Protocol decouples this kit from any specific app's prompt
assembly. Callers implement (or pass) a function that maps a "user_input + context"
to (system_prompt, user_message).
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable, Iterable, Protocol

from .judge import JudgePair, _call_anthropic

logger = logging.getLogger(__name__)


class PromptBuilder(Protocol):
    """Callable that returns (system_prompt, user_message) for a fixture entry.

    The fixture entry is a dict the caller fully controls. Typical fields:
        user_input: str      (required for it to make sense)
        context: dict        (optional; whatever your app needs)
        mode / id / etc.
    """

    def __call__(self, entry: dict[str, Any]) -> tuple[str, str]:
        ...


# ───────────────────────── default builder ─────────────────────────


def default_prompt_builder(entry: dict[str, Any]) -> tuple[str, str]:
    """Trivial builder: system is empty (or "You are a helpful coach."),
    user message is the raw user_input.

    Useful for vanilla benchmarks; for real apps, supply your own builder that
    runs your production prompt-assembly function (e.g. `build_coach_prompt(...)`).
    """
    return (
        "あなたはユーザーの習慣形成を支える対話型コーチ。短く温かく、実践的に。",
        entry.get("user_input") or "",
    )


# ───────────────────────── replay ─────────────────────────


async def generate_response(
    entry: dict[str, Any],
    *,
    prompt_builder: PromptBuilder = default_prompt_builder,
    model: str = "claude-haiku-4-5-20251001",
    max_tokens: int = 1500,
) -> str:
    """Call Anthropic with the prompt produced from `entry` and return the text."""
    system, user = prompt_builder(entry)
    return await _call_anthropic(
        system=system, user_message=user, model=model, max_tokens=max_tokens
    )


async def replay_and_pair(
    entries: Iterable[dict[str, Any]],
    *,
    prompt_builder: PromptBuilder = default_prompt_builder,
    concurrency: int = 4,
    model: str = "claude-haiku-4-5-20251001",
    strip_response: Callable[[str], str] | None = None,
) -> list[JudgePair]:
    """For each fixture entry: build prompt → call Anthropic → wrap as JudgePair.

    `strip_response` optionally cleans the LLM output (e.g. remove ```json fence)
    before judging. The default is identity.
    """
    sem = asyncio.Semaphore(concurrency)
    entries = list(entries)

    async def _one(entry: dict[str, Any]) -> JudgePair:
        async with sem:
            text = await generate_response(
                entry, prompt_builder=prompt_builder, model=model
            )
            if strip_response is not None:
                text = strip_response(text)
            return JudgePair(
                user_input=entry.get("user_input") or "",
                ai_response=text,
                user_input_id=entry.get("id") or "",
                ai_response_id=f"replay-{entry.get('id') or '?'}",
                metadata={k: v for k, v in entry.items() if k != "user_input"},
            )

    return list(await asyncio.gather(*[_one(e) for e in entries]))
