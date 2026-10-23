# Repository Guidelines

## Project Structure & Module Organization

This workspace contains research-agent specifications and a reference experiment bundle; a complete Agent service is not yet present.

- Root `research-description.md`, `research-agent-spec.md`, and `research-scoring-rules.md` describe tasks, interfaces, and scoring. `agenthack-base-1.1.1.tar` supplies the base container image archive.
- `research-experiments-20261020-source-v1/` contains shared guides, `model_source.py` for source extraction, and the platform client `run_experiment.py`.
- `D-01/`, `D-02/`, and `D-05/` contain methods, protocols, workers, plotting helpers, and placeholder implementations.
- `公开样例请求/` contains public JSON fixtures and boundary cases; manifests record reference-file hashes.

## Build, Test, and Development Commands

There is no application build configuration or dependency manifest. From the repository root:

```sh
cd research-experiments-20261020-source-v1
python3 model_source.py --help
python3 model_source.py --response /tmp/model-response.json --output /tmp/implementation.py
```

The extraction command reads a saved complete model response, checks Python 3.9 syntax and the required entry point, and saves source without rewriting it.

For offline execution, follow the Docker command in `离线自测与平台运行指南.md`, section 3: use Python 3.9/Linux AMD64, disabled networking, read-only materials, and a separately mounted implementation.

Only inside an authorized platform session:

```sh
python /data/experiments/run_experiment.py --case D-05 --implementation /absolute/work/implementation.py
```

## Coding Style & Naming Conventions

Use four-space indentation, `snake_case` functions and variables, UTF-8, and descriptive names for new helper code. No formatter or linter is configured. Algorithms must use Python 3.9 standard-library APIs and expose `run_experiment(request)`. Keep stdout exclusively for JSON; send diagnostics to stderr. Preserve protocol keys and case identifiers exactly.

## Testing Guidelines

No automated test framework or coverage threshold is configured. Run the relevant public fixture and boundary cases, including D-02 `rank-default.json` when changing retrieval behavior. Workers receive `stage`/`payload`; algorithms receive only `payload`. Validate schema, finite numbers, determinism with seed 42, and algorithm invariants. Placeholder implementations intentionally raise `NotImplementedError`; public checks do not establish official scores.

## Commit & Pull Request Guidelines

This snapshot has no Git metadata, so historical conventions cannot be inferred. Use concise imperative subjects, optionally scoped, such as `docs: clarify D-05 validation`. PRs should describe changes, affected cases, validation commands/results, and linked issues when applicable. Include example JSON or figures for output changes.

## Reference Integrity & Configuration

Keep distributed materials unchanged; store generated implementations separately. For D-class execution, follow the bundle’s newer source-provenance requirements over older root examples. Submit current-session Gateway-generated source unchanged; request repairs from the model. Never commit injected credentials or fabricate metrics, figures, or source receipts.
