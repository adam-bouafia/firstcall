# Raw benchmark output

One row per model × scenario × repeat. These are the runs cited in `docs/benchmark.md`.

| run | what it is |
|---|---|
| `20260923-134811.*` | head-to-head: 6 open models + keyword-rules baseline + **Claude Sonnet 5**, 8 scenarios, 1 repeat. The comparison table in `docs/benchmark.md`. |
| `20260923-131845.*` | open models only, 8 scenarios × **3 repeats**. The stability check — quote this one for per-model scores. |

Columns: `score`, `comparable`, `category_ok`, `keyword_score`, `command_ok`, `fix_ok`,
`confidence`, `latency_ms`, `prompt_tokens`, `completion_tokens`, `cost_usd`, `route`,
`next_command`, `predicted`, `error`.

`cost_usd` is 0.0 for the closed baseline: closed models have no entry in `models.yaml`, so no
price is applied. Reproduce with `make bench PROVIDER=nebius` and `make bench-vs-closed`.
