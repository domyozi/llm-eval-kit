# llm-eval-kit

A lightweight LLM-as-judge framework for evaluating an LLM's responses with a pluggable rubric,
replay mode, and GitHub Actions integration. The judge model is swappable via the `--model`
CLI flag; the bundled defaults target the Anthropic SDK.

[![CI](https://github.com/domyozi/llm-eval-kit/actions/workflows/eval.yml/badge.svg)](https://github.com/domyozi/llm-eval-kit/actions/workflows/eval.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

> **Why this exists**: LLM responses are probabilistic; eyeballing diffs doesn't tell you
> whether a prompt change actually improved quality. This kit lets you score
> `(user_input, ai_response)` pairs against a rubric, replay the same inputs against a
> new prompt, and **detect regressions automatically in CI**.

## What this demonstrates

- Built a production eval loop for LLM-based applications (Anthropic SDK by default; judge model swappable via `--model`)
- Designed replay-based prompt regression testing
- Integrated evals into GitHub Actions CI
- Measured **+9.4% quality improvement** from a one-line prompt change (case study below)
- Applied **defense-in-depth guardrails** for probabilistic LLM behavior

---

## Why this matters (long form)

This kit was extracted from a real production deployment. The "why" of each design choice
matters because the kit's job is **to make probabilistic systems behave predictably enough
to ship**. Below: the 6 questions every LLM-app team eventually faces, and what this kit
answers.

### Why evals matter

When the prompt is short, eyeballing 5 responses is enough to feel a change.
When the prompt grows to 700 lines (and a real coaching prompt will), small edits
**improve one behavior and silently break another**. In our production case we observed
this directly: a fix that made one response shape better made the AI return long-form
replies to single-word user inputs like `OK!` and over-quote past memory in unrelated
turns. The instruction "don't reply long to short inputs" **was already in the prompt** —
buried in line 359 of dense rules. Humans miss this. Eval surfaces it.

> The prompt had grown so large that even short user inputs like `OK` got long-form
> replies, and past memory was over-cited in unrelated turns. The instruction "stay
> short on short inputs" was already in the prompt — just buried somewhere in the
> mass of rules. — developer note
>
> *(原文: 「プロンプトの量が非常に膨大だったこともあり、例えば「OK」のような端的な回答に対しても非常に長文で返してしまったり、過去のメモリ情報を参照しすぎて返答してしまったりといった事象が発生していました。… 大量のプロンプトの中にその指示が埋没していたことが原因」)*

### Why LLM-as-judge (not human eval)

The kit does **not** claim to replace human evaluation. It claims to make human evaluation
**rarer and more targeted**. With a stable rubric and a frozen fixture, CI can answer
"did this PR move the average score?" without asking a human. Humans get pulled in for
the worst examples (where the judge flags low confidence) and for periodic calibration
between judge model and reality.

> **Known limitation**: the default judge is Claude Haiku — the same model family as the
> response generator in many real apps. Same-family judging is cheaper and faster but has
> documented bias issues (fawning toward similar response patterns, shared blind spots).
> The kit's design assumption is that this is *acceptable as a screening signal*, not as
> ground truth. For decisions that matter, pair with periodic human review or a
> cross-model ensemble (Sonnet judging Haiku, or external models). The architecture
> supports swapping the judge model trivially via the `--model` CLI flag.

### Why replay matters

The naive eval pattern — "sample 10 recent `(user_input, ai_response)` pairs from prod
and judge them" — has one problem: the AI responses came from the **old** prompt.
A new prompt won't have produced any of them. Evaluating those pairs after a prompt
change measures the judge's stability, not the prompt's effect.

Replay mode fixes this by keeping a **fixed fixture of user inputs** and generating fresh
AI responses with whatever prompt is on the current branch. Same inputs, two prompts → fair
A/B comparison. This is what makes CI possible.

> "ユーザー使用を待たずに prompt 改修の効果を事前検証したかった" — developer note

### Why CI integration (not just CLI)

A standalone CLI is sufficient for one engineer running eval manually. It is **not**
sufficient for long-term operations. Manual eval falls off; PR comments compound.
Building this loop into CI changes the question from "did I remember to eval?" to
"why did this PR drop specificity by 0.5?". The PR comment is the artifact that
keeps the loop running even when nobody is watching.

> "手動 eval は回らない" — developer note

### Why a 1/2/4/5 scale (with no 3)

Likert-style scales suffer from **central tendency bias**: when uncertain, both humans
and models default to the middle value. By removing 3 entirely, the judge is forced to
commit to a leaning ("more good than bad" or vice versa), which surfaces signal in the
noise.

This is a deliberate design choice — **not** a claim that 1/2/4/5 is empirically superior
to 1-5 in this context. The kit has not yet run a controlled experiment comparing the two.
Future work: ablate by adding 3 back and measuring judge-human agreement on a fixed set.

### Why 4 dimensions and not more

The default rubric (relevance / specificity / actionability / tone_fit) targets a coaching
domain. More dimensions would be more expensive (token cost is roughly linear in dimension
count) and would dilute the signal — each additional dimension is judged with less
attention by the model.

**`safety` and `factuality` are intentionally out of scope** for this rubric. They are
better handled by:
- Backend-side guardrails (input filters, output schema validators)
- Periodic human review of the worst examples
- A separate eval suite designed for adversarial inputs

Treating "safety" as one of four equally-weighted scores would be misleading. Defense in
depth means owning the layer that's right for the threat.

---

## In action

A live demo PR ([#1](https://github.com/domyozi/llm-eval-kit/pull/1)) adds two concreteness
rules to the default prompt. CI runs the eval, judges 7 fixed user inputs against the new prompt,
diffs against the committed baseline, and posts the result as a sticky PR comment:

![CI PR comment showing automated eval diff](docs/assets/pr-comment.png)

| dimension     | baseline | current | Δ |
|---------------|----------|---------|---|
| actionability | 2.71     | 4.14    | 🟢 **+1.43** |
| specificity   | 2.86     | 4.14    | 🟢 **+1.28** |
| relevance     | 3.57     | 3.71    | 🟢 +0.14 |
| tone_fit      | 4.57     | 4.43    | 🔴 -0.14 |
| **avg_total** | **3.43** | **4.11** | 🟢 **+0.68 (+19.8%)** |

A 1-line prompt change → measurable quality improvement detected automatically on PR open.
That's the entire pitch of this kit.

---

## At a glance

```python
import asyncio
from llm_eval import (
    JudgePair, judge_pair, RUBRIC_COACH,
)

async def main():
    result = await judge_pair(
        JudgePair(
            user_input="今日は午前中、提案書を仕上げる。",
            ai_response="9-11時を集中ブロックに置きましょう。完了基準は2項目目までの章立て。",
        ),
        rubric=RUBRIC_COACH,
    )
    print(f"avg={result.total:.2f}")
    for s in result.scores:
        print(f"  {s.key}: {s.score} — {s.rationale}")

asyncio.run(main())
```

```
avg=4.50
  relevance: 5 — 「提案書を仕上げる」核心を捉えて時間帯を提示
  specificity: 4 — 時間枠あり、完了基準も2項目目までと数字
  actionability: 4 — 今日実行可能だが分解度はやや粗い
  tone_fit: 5 — 押し付けがましさなし、対等
```

---

## Design choices

### Rubric: 1/2/4/5 Likert (no 3)

The default scale excludes 3 to **prevent central-tendency bias**. Forcing the judge to
commit "leaning good or bad" surfaces more signal than a 1–5 scale where indifferent
responses cluster at 3.

```python
DEFAULT_SCORES = (1, 2, 4, 5)
```

### CoT-style judge output

The judge is required to emit `<observation>` (1–2 lines analyzing the pair) **before**
`<scores>` (the JSON rubric). This:
- Surfaces the judge's reasoning so each score is auditable
- Reduces "drive-by" scoring by forcing engagement with the content
- Lets you spot judge bias from the rationale

### No LangChain dependency

Direct Anthropic SDK calls only. This keeps the runtime minimal (one dependency),
the code path debuggable, and avoids version-skew issues. If you need orchestration,
wrap this kit; don't fork it.

### Pluggable prompt builder for replay

The replay mode accepts a `PromptBuilder` protocol so you can plug in your production
prompt assembly. Example:

```python
from llm_eval.replay import replay_and_pair

def my_prompt_builder(entry):
    # entry is a dict from your fixture
    system = my_app.build_system_prompt(user_id=entry.get("user_id"))
    user = entry["user_input"]
    return system, user

pairs = await replay_and_pair(
    fixture_entries, prompt_builder=my_prompt_builder
)
```

---

## Architecture

### Eval flow (one pair → scored)

```mermaid
flowchart LR
  J["(user_input, ai_response) pair"] --> JS["judge: system prompt + rubric XML<br/>(anchor examples per dimension)"]
  J --> JU["judge: user message<br/>&lt;eval_pair&gt; ... &lt;/eval_pair&gt;"]
  JS --> A["judge LLM<br/>(Anthropic Claude Haiku by default, swappable via --model)"]
  JU --> A
  A -->|"&lt;observation&gt; → &lt;scores&gt; JSON"| P["parser<br/>(brace-counting + raw_decode)"]
  P --> R["JudgeResult<br/>per-dim score + rationale + total"]
  R --> AGG["summarize(label, model)"]
```

### Replay architecture (CI loop)

```mermaid
flowchart TD
  F["fixture JSON<br/>fixed user_inputs"] --> PB["PromptBuilder (pluggable)<br/>entry → (system, user)"]
  PB -->|"current prompt code"| ANT["target LLM<br/>(Anthropic SDK by default)<br/>fresh response"]
  ANT --> JF["judge each pair<br/>(eval flow above, in parallel)"]
  JF --> SUM["summary JSON<br/>+ markdown report"]
  SUM --> BD["baseline.json diff"]
  BL["committed baseline<br/>(captured on main)"] --> BD
  BD -->|"any dim drops ≥ threshold"| FAIL["CI fail"]
  BD -->|"otherwise"| PASS["CI pass + sticky PR comment"]
```

The replay step is what makes prompt A/B testing possible: same inputs, different prompt
versions, comparable outputs. Without it, eval would only measure the judge's stability,
not the prompt's effect.

---

## Install

```bash
pip install -e ".[dev]"     # editable + dev deps (pytest, ruff)
```

Set `ANTHROPIC_API_KEY` in your environment or `.env`:

```bash
export ANTHROPIC_API_KEY=sk-ant-...
```

---

## CLI

```bash
# One-shot eval against the bundled fixture
python -m llm_eval.cli \
  --fixture fixtures/sample_pairs.json \
  --label "exploratory"

# Diff against a stored baseline (CI use case)
python -m llm_eval.cli \
  --fixture fixtures/sample_pairs.json \
  --label "pr-current" \
  --baseline fixtures/baseline.json \
  --fail-threshold 0.3 \
  --out /tmp/report.md \
  --json-out /tmp/scores.json

# Snapshot the current scores as the new baseline
# (run on main after a prompt PR has merged)
python -m llm_eval.cli \
  --fixture fixtures/sample_pairs.json \
  --update-baseline fixtures/baseline.json
```

Cost: 1 pair ≈ $0.003 on the default judge (Claude Haiku) for both replay + judge. The
bundled 7-pair fixture costs roughly $0.04 per full run. Swap the judge with `--model` to
trade cost/quality.

---

## GitHub Actions

Drop `.github/workflows/eval.yml` (included) into your repo. On every PR that touches
`src/llm_eval/**` or `fixtures/**`, the workflow:

1. Installs the package
2. Runs unit tests
3. Replays the fixture against the **current code** prompt
4. Diffs against `fixtures/baseline.json`
5. Posts the report as a sticky PR comment
6. Fails the workflow if any dimension drops by ≥ `--fail-threshold` (default 0.3)

A typical PR comment:

```markdown
# Eval — pr-42
- avg total: 4.10 / 5

## Baseline vs Current
| dimension | baseline | current | Δ |
|---|---|---|---|
| relevance     | 3.20 | 3.50 | 🟢 +0.30 |
| specificity   | 3.60 | 4.30 | 🟢 +0.70 |
| actionability | 3.40 | 3.50 | 🟢 +0.10 |
| tone_fit      | 5.00 | 4.80 | ⚪ -0.20 |
| **avg_total** | 3.80 | 4.10 | 🟢 +0.30 |
```

Required: a `ANTHROPIC_API_KEY` repo secret.

---

## Case study: BusyBoy2 AI coach

The kit was extracted from a real production app where an AI coach generates
journal-style feedback. **Result:** a single 1-line prompt change improved
average quality from 3.95 → 4.32 (+9.4%), driven by a +1.17 jump in the
`specificity` dimension. The improvement was **detected and quantified by this kit**;
without it, the change would have been "feels a bit better" hearsay.

See [`examples/busyboy_case_study/README.md`](examples/busyboy_case_study/README.md)
for the full write-up: rubric tuning, defense-in-depth guards built from worst-example
analysis, and CI integration.

---

## Customizing the rubric

```python
from llm_eval import Rubric, RubricDimension

my_rubric = Rubric(
    dimensions=(
        RubricDimension(
            key="factuality",
            label="事実性",
            description="応答の事実情報が検証可能か。誤りの有無を見る。",
            anchors={
                5: "全て事実",
                4: "概ね事実、軽微な不確かさ",
                2: "重要な誤りあり",
                1: "ほぼ全て誤り",
            },
        ),
        # ... 1+ more dimensions
    )
)
```

Then pass it to `judge_pair(..., rubric=my_rubric)` and `summarize(..., rubric=my_rubric)`.

The bundled `RUBRIC_COACH` (relevance / specificity / actionability / tone_fit) targets
coach-style assistants but is a usable starting point for general-purpose evaluation.

---

## What this is not

- **Not a production observability tool.** For real-time monitoring of LLM apps, look at
  Langfuse, Helicone, or Arize.
- **Not an alignment-quality benchmark.** The rubric here measures *response quality*
  for a specific style (coach). For safety/factuality at scale, use HELM / OpenAI evals
  with thousands of samples.
- **Not human-eval-calibrated.** The judge model is itself an LLM with biases. For
  high-stakes decisions, treat the score as a screening signal and add human review.

---

## Failure cases observed

Real failures the kit (or its host application) hit during development. Each one
shipped a fix or an explicit caveat — keeping them visible matters more than
hiding the rough edges.

- **Judge JSON parse failures (2 / 7 pairs at first)** — initial regex parser failed on
  nested braces in `scores` JSON when the rationale field contained `{}`. Fixed by
  switching to balanced-brace counting + `json.JSONDecoder.raw_decode` (tolerates
  trailing content). Error rate dropped to 0 / 7 on subsequent runs.
- **Confidence threshold drift (0.6 → 0.7)** — original `≥0.6` auto-apply threshold was
  too aggressive in production, leading to "the AI just changed something I didn't ask
  for" anti-pattern. Lowered to 0.7; still considered unsettled, planned to re-tune
  with eval scores + acceptance rates.
- **Prompt rule buried in a 700-line system prompt** — the rule "do not return long
  replies to short user inputs" existed at line 359 but the model ignored it. The eval
  surfaced this as a worst-example pattern, leading to a circuit-breaker rule promoted
  to the very top of the prompt + a backend post-filter that strips actions on minimal
  input (defense in depth).
- **Tone regression as a side effect** — adding two "concreteness" rules to the
  default prompt builder improved `specificity` by +1.28 and `actionability` by +1.43,
  but `tone_fit` dropped by −0.14. The CI surfaced this trade-off explicitly rather
  than letting it pass unnoticed. Below the fail threshold (0.3) so the PR shipped,
  but the regression became visible material for the next iteration.
- **Same-family judge bias** — Claude judging Claude is fast and cheap but is known
  to be susceptible to fawning bias (rating responses higher because the writing
  style matches the judge's expectations). Currently flagged as an open limitation;
  cross-model ensemble (Sonnet + Haiku) and human-judge agreement studies are the
  next steps.
- **Small sample size variance** — 7-pair fixtures produce noisy averages (one
  outlier shifts `avg_total` by ~0.1). The fail threshold (0.3) was chosen to be
  forgiving at this scale; tightening will require scaling fixtures to 50–100.
- **Replay mock context vs production reality** — the bundled replay mode uses a
  minimal mock `CoachContext`; real prompts depend on per-user memory + habit history,
  so replay-derived scores measure the prompt's effect at fixed context, not at full
  production context. Real production scores (Phase A in the case study) cover the
  full-context path. Both are useful for different questions.

---

## Limitations

- **Judge bias**: same-family judges (e.g., Claude judging Claude) tend toward fawning.
  Cross-model judging (Sonnet + Haiku ensemble) is more robust but ~3× the cost.
- **Small sample size**: 7-pair fixtures give noisy signal. 50–100 is more stable.
- **Replay assumes deterministic prompt assembly**: if your prompt depends on live
  user state, the replay mode needs a `PromptBuilder` that captures that state at fixture time.
- **Fault tolerance**: judge JSON parse failures are skipped, not retried. Tune `--fail-threshold`
  to be lenient when sample size is small.

---

## License

MIT — see [LICENSE](LICENSE).

---

## Acknowledgments

Built while shipping an AI coaching feature; the eval problem was real, not academic.
Designed for clarity over completeness — the entire framework is < 500 lines of code,
deliberately so.
