# Repository Guidelines

## Start Here

Read [开发规范与Agent接力](docs/开发规范与Agent接力.md) and [开发进度与接力记录](docs/开发进度与接力记录.md) before implementation. Record the task, owned files, dependencies, verification results, and next action in the handoff record. Distinguish proposed architecture from implemented behavior.

## Project Structure

`research-assistant/` is the Git repository. It currently contains documentation; application code, tests, dependency configuration, and Dockerfile are not implemented. Official materials are in sibling `../赛题信息/`; the base image is `../agenthack-base-1.1.1.tar`. Preserve both. Dated public-source snapshots and hashes live in `docs/references/2026-10-23/`.

## Requirements and Version Control

The verified baseline is `research-assistant-objective-v3`: 11 cases, A/B/C/D/E counts 1/3/2/3/2. Dispatch schema v3 uses a dictionary for `cases_per_category`. Read actual assigned IDs; never invent missing cases. Local v2 documents describing 25 cases are historical.

C/E scoring uses `ce-quality-v1`. D-01/D-02/D-05 use `layered-d-v3` with `d-model-source-v1` admission. Resolve historical clauses using the development document’s source matrix and current task materials. Save new rule snapshots separately when versions change.

## Development and Verification

Current read-only commands, run from this repository:

```sh
git status --short
python3 ../赛题信息/research-experiments-20261020-source-v1/model_source.py --help
shasum -a 256 ../agenthack-base-1.1.1.tar
```

No project build, lint, or test command exists yet. Python tooling and module layout are proposals in the development document. Add executable commands with the implementation that enables them; never report planned checks as passing. Keep static, local, image, and platform verification separate.

## Coding and Runtime Rules

Use four-space Python indentation, type annotations, and descriptive `snake_case` names. Keep HTTP contracts, document processing, model access, paths, and persistence separate. Output only the prescribed JSON fields; preserve case IDs, exact metric names, and Markdown/file equality.

D algorithms require Python 3.9 standard-library compatibility. Generate submission source through the current case’s Gateway and preserve it unchanged except permitted newline normalization. Repairs require a new complete model response. Exclude generated submission source from formatters. Use the platform-mounted experiment client; never fabricate metrics or credentials.

## Commits and Handoffs

The initial commit uses a `feat:` prefix. Prefer concise subjects such as `docs: clarify dispatch validation`. PRs should identify requirements, affected files, verification evidence, and remaining limitations. Before handing off, inspect existing changes and update the progress record. Never overwrite another contributor’s work, official materials, or valid experiment results with placeholders.
