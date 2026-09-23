# Reading the benchmark

`make bench PROVIDER=nebius` runs every model in `models.yaml` against the 8 fixtures in
`scenarios/fixtures/`, which were **captured from the live k3s cluster** while it was actually
broken (`make capture`), not written by hand. Each fixture carries an `expected` block: the
category, the keyword groups a correct root cause must contain, the commands that count as a
useful next step, and — for the 5 scenarios that have a safe one-click fix — the remediation
command shapes that count.

```
score      = 0.5 category + 0.3 root-cause keywords + 0.2 next command
comparable = 0.6 root cause + 0.4 command      # the only two fields k8sgpt also produces
```

Two scoring choices to be able to defend:

- **Category is worth half.** It is the only field with one right answer, so it is the only
  field that cannot be gamed by writing more words.
- **`comparable` exists because k8sgpt does not emit a category or a fix.** Comparing on
  everything would flatter FirstCall for fields the baseline never attempts.

## The 23 September run — open models vs Claude Sonnet 5

Same 8 fixtures, same prompt, same scoring, one run (`-r 1`), 13:48.

| model | score | root cause | category | cmd ok | right fix | p50 | $/1k incidents |
|---|---|---|---|---|---|---|---|
| **Claude Sonnet 5** (closed reference) | **1.00** | 100% | 100% | 100% | 100% | 7.4s | n/a |
| gpt-oss-120b | 0.97 | 100% | 100% | 88% | 100% | 3.9s | $0.62 |
| qwen3-30b | 0.95 | 100% | 100% | 75% | 80% | 9.9s | **$0.24** |
| nemotron-nano | 0.95 | 100% | 100% | 75% | 100% | 19.2s | $0.52 |
| qwen3-235b | 0.93 | 100% | 100% | 62% | 100% | 6.0s | $0.49 |
| gemma3-27b | 0.91 | 100% | 88% | 88% | 80% | 4.7s | $0.26 |
| rules (no LLM) | 0.71 | 31% | 88% | 88% | 100% | 0.2s | $0.00 |

**Claude Sonnet 5 won: 1.00, a clean sweep.** Say that first and say it plainly. The interesting
part is what the gap is made of and what it costs.

1. **Root cause is a tie.** Every open model matched Claude at **100%** on root-cause keywords.
   The entire 0.03–0.09 gap is in `category` (one model slipped once) and `cmd ok` — *which*
   follow-up command it suggests, where several commands are reasonable and only some are on our
   allow-list. On the field that decides whether an engineer knows what broke, open weights are
   level with the best closed model available.
2. **The prompt cost is not comparable.** Claude consumed **3066 input tokens** on `01-crashloop`
   against **1989** for the open models on the identical snapshot — different tokenizers, ~50%
   more billed input. Any cost ratio you quote should carry that caveat.
3. **Cost column says n/a for Claude, not $0.00.** The closed models are not in `models.yaml`, so
   there is no price to multiply by. Fill `BASELINE_ANTHROPIC_PRICE_IN/_OUT` in `.env` for a real
   number; leaving it blank is honest, printing $0.00 would not be.
4. **The `rules` row is still the most useful one in the table:** 88% category, **31%** root
   cause. A regex runbook knows *what* broke and nothing about *why*. The 31% → 100% gap is what
   the model buys, measured against what most teams have today.

**The line to use on stage:** *"Claude Sonnet 5 scored a perfect 1.00. Our best open model scored
0.97, matched it on root cause, and runs inside the customer's cluster where their logs already
are. For a bank that cannot send production logs to a hosted API, 0.97 that they can deploy beats
1.00 that they cannot."*

### Stability: the 3-repeat run (13:18, open models only)

One repeat is noisy — `gemma3-27b` returned `Other` once here and scored 0.91, against 0.97 over
three repeats earlier. The 8 × 3 run, without the closed baseline:

| model | score | root cause | category | $/1k |
|---|---|---|---|---|
| gemma3-27b | 0.97 | 100% | 100% | $0.26 |
| cascade (30B → 235B) | 0.95 | 100% | 100% | $0.24 |
| gpt-oss-120b | 0.95 | 100% | 100% | $0.63 |
| qwen3-235b | 0.93 | 100% | 100% | $0.48 |
| qwen3-30b | 0.93 | 100% | 96% | $0.24 |
| nemotron-nano | 0.92 | 98% | 92% | $0.46 |

Quote one table or the other, never a mix of rows from both. **Cascade escalated zero times in
24 runs** — the small model was confident every time — so cascade cost equals the small model's
cost. Say "escalation rate was zero on these scenarios", not a percentage.

### Two things fixed between the 11:56 and 13:18 runs, disclosed

- **Category 88% → 100%.** The category definitions are now stated by mechanism in the system
  prompt (`ConfigMissing` = container could not start, referenced ConfigMap absent;
  `CrashLoop-AppError` = container started, exited non-zero). Before, most models labelled
  `01-crashloop` `ConfigMissing` — defensible, since the app crashes *because* config is missing.
  **The ground truth was not changed to match the models.** Caveat worth stating: a sharper spec
  makes the category metric easier, so this number reflects a clearer taxonomy as much as better
  models. Root cause and cost are untouched by it — lead with those.
- **nemotron-nano 0.67 → 0.92.** Truncation, not weakness: it reasons before answering and hit the
  1500-token cap, returning `Other` at confidence 0.0. Ceiling raised to 3000 in `models.yaml`;
  its cost rose with it.

## How many closed models to compare against

Two, not the whole catalogue. The claim is *"open weights you can host match the closed model
a team would actually be using"* — one flagship-tier reference proves or disproves it. Adding
five models from one vendor changes the subject to vendor-internal ranking and buys nothing
a judge asked for.

The second one is worth having for a different reason: pick the **cheapest** closed model too.
Your headline is cost as much as accuracy ($0.26 per 1000 incidents), so you want to know
what the cheap closed option costs on the identical scenarios *before* a judge asks. If it is
also cheap, say so and fall back on the privacy argument, which no price beats.

```bash
# .env - one reference row per model in the list
BASELINE_ANTHROPIC_MODEL=claude-sonnet-5,claude-haiku-4-5
BASELINE_OPENAI_MODEL=gpt-4.1-mini
```
Runtime is not the constraint: 8 scenarios × 1 repeat is ~8 calls per model, a couple of
minutes. Credits and a clean claim are the constraints.

## Re-running

```bash
make bench PROVIDER=nebius          # cascade + rules, 3 repeats
make bench-vs-closed                # + GPT / Claude on the identical fixtures
make bench-k8sgpt                   # needs --live: k8sgpt on the same broken cluster
make bench-live                     # against the cluster as it is right now
```
Results land in `bench/results/<timestamp>.{md,csv,json}`. The CSV has one row per
model × scenario × repeat — that is where you look when a number surprises you, before you
put it on a slide. CI (`.github/workflows/bench.yml`) fails the build under 0.60.
