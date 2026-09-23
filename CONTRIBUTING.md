# Contributing

Issues and pull requests are welcome.

## Development setup

```bash
make setup     # Python venv + npm deps, creates .env from .env.example
make mock      # UI + API on recorded fixtures with canned answers: no cluster, no model key
make test      # backend tests
```

For a live cluster, see [docs/local-setup.md](docs/local-setup.md) (`make cluster` on k3s, or
`make cluster-kind`).

## Adding a failure scenario

A scenario is one broken workload plus everything needed to reproduce, score and repair it:

| file | what it holds |
|---|---|
| `scenarios/baseline/NN-name.yaml` | optional healthy version, rolled out first so rollout history shows the bad change |
| `scenarios/manifests/NN-name.yaml` | the broken change, with a comment stating the root cause |
| `scenarios/fixes/NN-name.sh` | the repair |
| `scenarios/fixtures/NN-name.json` | recorded snapshot plus `expected` ground truth for the benchmark |

Then add the workload to `WORKLOAD_TO_SCENARIO` in `scripts/capture_fixtures.py`, break it
(`make break S=NN-name`), wait for it to fail, and run `make capture` to record a real snapshot.
Hand-edit only the `expected` block.

## Pull requests

- One change per pull request, with a test when behaviour changes.
- `make test`, `helm lint charts/firstcall` and `cd frontend && npm run build` should pass; CI
  runs the same plus the end-to-end test on kind.
- Changes to the prompt or the collector trigger the benchmark workflow. Include the score
  before and after.

By contributing you agree that your contribution is licensed under the Apache License 2.0.
