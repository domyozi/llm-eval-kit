# Case study — BusyBoy2 AI coach

This kit was extracted from a real production app: **BusyBoy2**, an AI coaching habit-tracker
where an LLM gives journal-style feedback to users every day. The eval problem was concrete,
not academic: I needed to know if prompt changes actually made the coach better.

## Setup

- Frontend: React + Vite + Vercel
- Backend: FastAPI + Railway
- AI: Anthropic Claude (Haiku for coaching, Haiku again for judging)
- DB: Supabase (Postgres)
- Production data: ~1 month of real journal entries from a single dogfood user (me)

## The problem

The coach prompt had grown to **700+ lines** of XML-tagged rules (mode-specific cues,
output schema, memory-patch policy, etc.). Tweaks felt important but I had **no way to
know if they helped or regressed.** Output is probabilistic, eyeballing a few responses
told me nothing reliable.

## What I built (3 phases)

### Phase A — MVP eval CLI

- Sample N recent `(user_input, ai_response)` pairs from the journal_entries table
- Score each on a 4-dimension rubric using a separate Claude Haiku call
- Output markdown report with averages, worst examples, and per-dimension rationale
- Cost: ~$0.003/pair (10 pairs = $0.03)

Baseline result: **avg 3.95 / 5**, with `tone_fit` 4.80 (strong) but `specificity` and
`relevance` both at 3.40 (weak).

### Phase B — Persistence + dashboard

- Migration: `coach_eval_runs` (summary) + `coach_eval_scores` (per-pair detail)
- Admin API: `GET /api/admin/eval/runs`, `POST /api/admin/eval/runs` (kick off + persist)
- Frontend: "EVAL" tab in Settings with run list, dimension bars, collapsible worst examples
- Allowlist-based admin gating (`ADMIN_USER_IDS` env)

Now I could trigger an eval run from the UI and visually compare runs side by side.

### Phase C — Replay + CI

The above evaluates **past** journal entries (= responses from old prompts). That's useful for
overall quality tracking but **doesn't show the effect of a prompt change**: a new prompt won't
have produced any of the existing responses.

The replay mode (= this kit) fixes that:
- Keep a small fixed fixture of user_inputs
- For each PR, generate **fresh** AI responses with the current prompt
- Judge those responses
- Diff against `baseline.json` (committed scores from main)
- Fail the workflow if any dimension drops by ≥ 0.3

This is the entry point for the **improvement loop**:
prompt change → eval scores → adjust → eval again.

## Result of one improvement cycle

After looking at worst examples from the baseline run, I added a **single line** to the coach
system prompt:

```
2. 提案や次の一手には、数値 (時刻 / 分量 / 所要時間 / 期限) を必ず 1 つ以上含める。
   「夜にやる」ではなく「21:30 に 5 分」のように具体化する。
```

Re-ran the eval:

| dimension | baseline | after | Δ |
|---|---|---|---|
| relevance | 3.40 | 3.57 | +0.17 |
| **specificity** | **3.40** | **4.57** | **+1.17** 🎯 |
| actionability | 4.20 | 4.43 | +0.23 |
| tone_fit | 4.80 | 4.71 | -0.09 |
| **avg_total** | **3.95** | **4.32** | **+0.37** (+9.4%) |

The specificity ruling jumped +1.17 from a 1-line change. Without the eval system,
the response feel "slightly more concrete" would have been the only signal — easy to miss,
impossible to communicate to a team.

## Worst-example analysis → defense-in-depth guards

The baseline run surfaced another problem:

> User types `OK!` (just confirming a previous turn) → AI generates 4 unrelated
> proposals AND rewrites the `memory_patch.profile.family` field with pregnancy info
> that the user had deliberately removed.

The 700-line prompt **did** contain a rule covering this (`"純粋な相槌では JSON を出さない"`),
but it was buried in line 359 of dense text. The AI didn't follow it.

Fix: two layers of guards.

**Layer 1 (prompt)** — circuit breaker promoted to the very top of OUTPUT_CONTRACT:

```
0-PRE. 必ず最初に判定し、該当したら ANY action を出さない:
  - 30 字未満の短い入力
  - 「OK」「うん」「了解」等の単純な相槌・確認のみ
  - 「test」「あ」等の意味のない / テスト入力
→ tasks / habits / memory_patch / confirmation_prompts すべて空。例外なし。
```

**Layer 2 (backend)** — post-filter that strips actions when input is minimal:

```python
def filter_by_user_input(payload, user_input):
    if _is_minimal_input(user_input):  # 20-char heuristic + token list
        return {"followup_question": payload.get("followup_question")}
    return payload
```

The two layers serve different purposes:
- Layer 1 trains the model to behave (works ~95% of the time)
- Layer 2 catches the remaining 5% so the user-visible behavior is 100% safe

I see this as the **right shape for any LLM-app feature** that has side effects.
LLMs are probabilistic; never rely on a single layer of "the prompt will tell it not to."

## What didn't make the cut

- **LangChain / LangGraph**: considered, decided against. The backend was already using
  `anthropic.AsyncAnthropic` directly with custom prompt assembly, custom memory schema
  (`CoachUserContext`), and native tool use. LangChain would add adapter layers without
  enabling anything new. The kit you're reading has the same philosophy: ~500 LOC, one
  dependency, no abstractions you can't read in a sitting.

- **Cross-model judging**: ensemble of Sonnet + Haiku as judges would mitigate same-family
  bias but triples cost. The 7-pair fixture × 2 judges × 2 runs (replay + judge) = 28 calls
  per CI run = ~$0.10. Doable but skipped for v1.

- **Human-eval calibration**: the score is a screening signal, not ground truth.
  I'd correlate against human labels (~30 manually-rated pairs) before relying on it
  for go/no-go decisions.

## What I'd do next

1. **Bigger fixture**: 50–100 pairs to reduce variance. Anonymized production samples
   would be ideal but require a scrub pipeline.
2. **Per-dimension fail thresholds**: 0.3 might be too lenient for `relevance` (most
   important) and too strict for `tone_fit` (high baseline already).
3. **A/B in production**: gate prompt changes behind a feature flag and compare eval
   scores between cohorts on actual journal entries (not synthetic fixtures).
4. **Streaming-aware eval**: current setup judges complete responses; long-form streaming
   responses might score differently if interrupted partway.

## TL;DR for hiring conversation

- I shipped an eval pipeline that turned "feels better" into "+0.37 / 5 measured" for a
  1-line prompt change in the BusyBoy production deployment
- Extracted the framework as [claude-eval-kit](../..) (public, MIT) and demonstrated the
  same loop **publicly** in [PR #1](https://github.com/domyozi/claude-eval-kit/pull/1) —
  vanilla prompt 3.43 → improved 4.11 (**+0.68, +19.8%**), `actionability` +1.43
- The system is in production, integrated with CI, and runs on every prompt PR
- I made deliberate non-choices (no LangChain) and can defend them
- The defense-in-depth design philosophy carries over to any LLM-app I'd build next
