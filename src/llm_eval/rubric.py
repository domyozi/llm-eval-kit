"""Rubric definitions for LLM-as-judge evaluation.

A Rubric is a collection of RubricDimension. Each dimension has:
- key:        machine name (used as JSON field)
- label:      human label (shown in reports)
- description: 1-2 sentences telling the judge what to evaluate
- anchors:    score → example text (anchors the judge's calibration)

The default scale is 1/2/4/5 (Likert with 3 removed).
Removing the middle "neutral" score forces the judge to commit "leaning good or bad"
and avoids central-tendency bias.

Example custom rubric:
    my_rubric = Rubric([
        RubricDimension(
            key="factuality",
            label="事実性",
            description="応答の事実情報が正しいか。検証可能な誤りの有無を見る。",
            anchors={5: "全て事実", 4: "概ね事実、軽微な不確かさ",
                     2: "重要な誤りあり", 1: "ほぼ全て誤り"},
        ),
        ...
    ])
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping


# Likert anchors. 3 is intentionally absent to avoid central-tendency.
DEFAULT_SCORES: tuple[int, ...] = (1, 2, 4, 5)


@dataclass(frozen=True)
class RubricDimension:
    key: str
    label: str
    description: str
    anchors: Mapping[int, str]

    def __post_init__(self) -> None:
        # anchors must cover DEFAULT_SCORES
        missing = [s for s in DEFAULT_SCORES if s not in self.anchors]
        if missing:
            raise ValueError(
                f"RubricDimension '{self.key}' missing anchors for scores {missing}"
            )


@dataclass(frozen=True)
class Rubric:
    dimensions: tuple[RubricDimension, ...]

    @property
    def keys(self) -> tuple[str, ...]:
        return tuple(d.key for d in self.dimensions)

    def get(self, key: str) -> RubricDimension:
        for d in self.dimensions:
            if d.key == key:
                return d
        raise KeyError(key)

    def to_prompt_xml(self) -> str:
        """Embed this rubric into the judge prompt as XML.
        Anchor examples are included so the judge calibrates from them."""
        parts: list[str] = []
        for d in self.dimensions:
            anchors_xml = "\n".join(
                f'      <anchor score="{s}">{d.anchors[s]}</anchor>'
                for s in sorted(d.anchors.keys())
            )
            parts.append(
                f'  <dimension key="{d.key}" label="{d.label}">\n'
                f"    <description>{d.description}</description>\n"
                f"    <anchors>\n{anchors_xml}\n    </anchors>\n"
                f"  </dimension>"
            )
        return "<rubric>\n" + "\n".join(parts) + "\n</rubric>"


# ────────────────────── Built-in rubrics ──────────────────────


# Coach-style response rubric (the one used in the case study).
# Generic enough for any "coach / assistant" LLM that gives advice in response to user state.
RUBRIC_COACH = Rubric(
    dimensions=(
        RubricDimension(
            key="relevance",
            label="関連性",
            description=(
                "ユーザー独白の核心的な悩み / 宣言 / 文脈に直接応答しているか。"
                "話題ずれや一般論で終わっていれば低い。"
            ),
            anchors={
                5: "発言の核心を捉え、文脈を踏まえて応答している",
                4: "核心は捉えているが文脈の活用がやや弱い",
                2: "話題には触れるが核心からはズレている",
                1: "完全に話題がズレているか定型応答",
            },
        ),
        RubricDimension(
            key="specificity",
            label="具体性",
            description=(
                "抽象的な励ましではなく、具体的な観察・数字・期限・方法を含むか。"
                "「頑張りましょう」「素敵ですね」のみだと低い。"
            ),
            anchors={
                5: "数値 / 期限 / 具体的アクションを含む",
                4: "具体例はあるが数値や期限は不足",
                2: "やや具体だが大半は抽象的励まし",
                1: "完全に抽象的・一般論",
            },
        ),
        RubricDimension(
            key="actionability",
            label="実行可能性",
            description=(
                "ユーザーが今日〜明日中に実行できる粒度の提案が含まれているか。"
                "大きすぎる / 漠然としている提案だと低い。"
            ),
            anchors={
                5: "今日 / 明日に実行できる具体的アクションが明示",
                4: "実行可能だが時刻 / 単位が曖昧",
                2: "方向性のみで実行手順が不明",
                1: "実行困難 / 提案無し",
            },
        ),
        RubricDimension(
            key="tone_fit",
            label="口調適合",
            description=(
                "ユーザーに対して説教臭くない、押し付けがましくない、共感的だが"
                "媚びていない、対等な口調か。"
            ),
            anchors={
                5: "対等で温かく、押し付けがましくない",
                4: "概ね良いが一部説教臭い",
                2: "やや上から目線 or 媚びている",
                1: "説教 / 否定 / 過剰な迎合",
            },
        ),
    )
)
