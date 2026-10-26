# Repository Guidelines

## Start Here

Read [开发规范与Agent接力](docs/开发规范与Agent接力.md) and [开发进度与接力记录](docs/开发进度与接力记录.md) before implementation. Record the task, owned files, dependencies, verification results, and next action in the handoff record. Distinguish proposed architecture from implemented behavior.

## Project Structure

`research-assistant/` is the Git repository. Application code, tests, `pyproject.toml` and a delivery `Dockerfile` are implemented (`src/research_assistant/`, `tests/`, `scripts/`). P1–P6 are locally verified; **R03 has platform evidence for the previous image (46.9/100); the current fixes still need platform reevaluation**. Official materials are in sibling `../赛题信息/`; the base image is `../agenthack-base-1.1.1.tar`. Preserve both. Dated public-source snapshots and hashes live in `docs/references/2026-10-23/`. The approved P1 design and its task-by-task plan live in `docs/superpowers/specs/` and `docs/superpowers/plans/`.

## Requirements and Version Control

The verified baseline is `research-assistant-objective-v3`: 11 cases, A/B/C/D/E counts 1/3/2/3/2. Dispatch schema v3 uses a dictionary for `cases_per_category`. Read actual assigned IDs; never invent missing cases. Local v2 documents describing 25 cases are historical.

C/E scoring uses `ce-quality-v1`. D-01/D-02/D-05 use `layered-d-v3` with `d-model-source-v1` admission. Resolve historical clauses using the development document’s source matrix and current task materials. Save new rule snapshots separately when versions change.

## Development and Verification

Commands available since P1, run from this repository:

```sh
uv sync                      # 建立 Python 3.11 环境并装齐锁定依赖
uv run pytest                # 单元/合同/集成测试；默认排除 live
uv run pytest -m live        # 真实网关 A 类端到端，需要 .env.local 与网络
uv run ruff check .          # 静态检查
uv run ruff format --check . # 格式检查
RESEARCH_WORKSPACE_ROOT=/tmp/ra RESEARCH_WORKSPACE_TEAM=smoke uv run python -m research_assistant

# 交付镜像（需先 docker load ../agenthack-base-1.1.1.tar）
docker build --platform linux/amd64 -t research-assistant:p1 .
docker run --rm --platform linux/amd64 --env-file .env.local \
  -e RESEARCH_WORKSPACE_TEAM=smoke -p 18080:8080 research-assistant:p1

# D 类离线算法验证（需 Docker、python:3.9-slim 与真实网关；隔离容器内执行生成源码）
uv run python scripts/validate_d_offline.py --case D-01
```

Read-only checks that remain valid:

```sh
git status --short
python3 ../赛题信息/research-experiments-20261020-source-v1/model_source.py --help
shasum -a 256 ../agenthack-base-1.1.1.tar
```

Runtime dependency versions are pinned to what the base image actually ships (`fastapi 0.111.0`, `starlette 0.37.2`, `pydantic 2.8.0`, `uvicorn 0.30.0`, `httpx 0.27.0`, `anyio 4.15.1`, Python 3.11). The one exception is `pypdf==6.21.2`, added in P2 because the image ships **no** PDF library at all — it is pure Python with zero mandatory dependencies, so it needs no platform-specific wheel and builds cleanly for linux/amd64 from a macOS/arm64 host. **The Dockerfile must therefore `pip install`/`uv sync` pypdf at build time**; the image still lacks Pillow, matplotlib, OCR and scikit-learn. `pytest` and `ruff` are dev-group only, so the build must use `uv sync --no-dev`. Also verify at P6 that the image ships the whole transitive closure of the pinned set (`fastapi-cli`, `typer`, `uvloop`, `httptools`, `watchfiles`, `email-validator`, `python-multipart`, `jinja2`), since `uv.lock` resolves them locally.

Credentials live only in the gitignored `.env.local` and are injected at runtime with `--env-file`; they are never in code, the image, result files or committed tests (verified by grepping the built image). Local test passes are not image verification and are not platform verification: keep static, local, image, and platform evidence separate, and never report a planned check as passing.

**Never pipe a verification command into `tail`** when its exit status matters — `uv run pytest -q | tail -2` exits 0 even when tests fail. Use `uv run pytest -q > log 2>&1; echo $?`.

## Coding and Runtime Rules

Use four-space Python indentation, type annotations, and descriptive `snake_case` names. Keep HTTP contracts, document processing, model access, paths, and persistence separate. Output only the prescribed JSON fields; preserve case IDs, exact metric names, and Markdown/file equality.

D algorithms require Python 3.9 standard-library compatibility. Generate submission source through the current case’s Gateway and preserve it unchanged except permitted newline normalization. Repairs require a new complete model response. Exclude generated submission source from formatters. Use the platform-mounted experiment client; never fabricate metrics or credentials.

**The delivered image must start from the official `agenthack.AgentRuntime`.** The committee requires the entrypoint to run `AgentRuntime(MyAgent()).run()`; a standalone HTTP service with like-named `/health` and `/solve` does not qualify. The SDK's generic `/solve` implements the generic challenge contract — request `{task_id, task_type, input: dict, constraints: {timeout_ms}}`, response `{task_id, status, output, metadata}` — which conflicts with this benchmark's eight-field input (where `constraints` is a *string array*) and its requirement to return only the prescribed top-level fields. The committee's checklist therefore requires adapting the HTTP entry: keep the official app, `/health`, lifespan and `Telemetry` instance, then replace only the `/solve` route (see `runtime/official_runtime.py`). Reusing the runtime's telemetry is mandatory — a second `Telemetry()` means duplicate providers and a different session context. `api.py` remains the development/test app (no agenthack off-image) and shares the protocol handler in `runtime/http_solve.py`.

Never serialize `Settings` or `GatewaySettings` with `dataclasses.asdict()` / `fields()`: that bypasses their masking `__repr__` and yields `api_key` in plaintext. The logger's redaction is key-based and cannot catch a secret interpolated into a message *string* — pass secrets as nothing at all.

## Commits and Handoffs

The initial commit uses a `feat:` prefix. Prefer concise subjects such as `docs: clarify dispatch validation`. PRs should identify requirements, affected files, verification evidence, and remaining limitations. Before handing off, inspect existing changes and update the progress record. Never overwrite another contributor’s work, official materials, or valid experiment results with placeholders.

## 2026-10-24 Platform Feedback and Regression Rules

Read `docs/references/2026-10-24-platform-feedback/README.md` before interpreting scores. The old image passed runtime checks; B-01/02/03 scored zero with no model calls, C-01 was invalid, E-02 failed output validation, D-02 scored 10/40. Hidden grader details are unavailable.

For B only, the official material descriptor `本地论文文件夹内的 PDF` authorizes enumerating the configured paper library. Explicit subdirectories may be expanded within that root. Never treat an absent named PDF, empty materials, or unknown prose as permission to read the whole library; C/E remain explicitly scoped.

C/E repaired drafts must pass the same 800-character contract as initial drafts. Keep the valid draft on repair failure. Idea repair selection counts both content and citation defects; require substantive bottleneck reflection without inventing observations.

Bounded document extraction runs in a killable child process. D generation receives a deadline excluding the 55+12+5-second reserve, and self-test timeout is shared by the whole batch. Preserve previous helper outputs under `work/<case>/previous-results` before a new invocation; do not reuse them as new experimental evidence.


D-02 self-tests now independently verify query/document coverage, public development metrics, full grid coverage and the six-part selection key (including b). Return diagnostics rather than trusting self-reported metrics. Generation prompts distinguish raw query rankings from the final Reproduction response. Current archive verification on 2026-10-25 passed 892 tests; see the handoff record for the exact digest and pending platform reevaluation. BM25 metamorphic checks and run/rank replay are implemented as described below.

B retrieval keeps every usable authorized document as a semantic candidate, using lexical scores only for ordering. Pack by actual JSON length (24000 characters / at most 12 papers per batch). Context overflow splits batches or compacts a single evidence record without changing filenames. Preserve successful selections on later failures; log exact unreviewed filenames even before an all-failed error. Per-batch authorization is stricter than library membership. Global ordering can only permute verified selections. Latest image evidence includes all 30 synthetic papers screened in 30.19s; this is not platform score evidence.

For current layered-d-v3 D-02, self-tests include five hand-derived rank probes and default/selected run-to-rank replays. Validate score ratios and ranking consistency without embedding BM25. Keep every derived case within the same 12-second batch budget. Latest fresh source passed all 12 cases in a Python3.9 isolated container; see the handoff for image digest. Protocol.case_id identity cross-check is a deferred abnormal-material improvement; current official packets match.

C/E body validation excludes the bibliography and is shared by parsers and output builders. Numeric attribution diagnostics use the actually cited source; they are advisory presence checks, not semantic proof. Keep citation and numeric non-regression gates separate in repair selection. The earlier report-grounding image had a C01 dangling citation. The current reference-repair image passes C01 citation checks; The latest identity image has two residual E02 warnings (distribution and thin context). See the handoff record for exact evidence.

C/E now conservatively complete missing references only from uniquely delimited canonical anchors; ambiguous numbering, unknown/multiple filenames and fenced reports keep the original text for model repair. Initial and candidate drafts share normalization. Repair decisions log candidate issue types and reasons, prioritize legality, and preserve numeric/content/required-coverage guarantees. Global citation identity now accepts only complete unique locators, rejecting unknown prefixes, malformed quoted suffixes and multiple PDFs per entry. Filename-only entries, explicit PDF/文件名 labels and paired quotes preserve exact names, including spaces and embedded .pdf text. Generation/repair guidance requests backticks; undelimited author/title prose is conservatively unresolved. Late-page evidence and abstract budgets now use bounded contiguous excerpts; preserve original page identities, reserve spatial coverage, and mark excerpted sources. Short complete sources that fit remain intact. Nonnumeric factual grounding remains incomplete; current numeric checks are not entailment proof.

Latest 2026-10-26 image validation: C01 passes the existing citation/numeric checks on an eight-page synthetic source and cites its late results/limitations. E02 returns HTTP200 with matching files, but the image harness fails because [0,1] mathematical intervals are read as citation0; manual review also finds vegetation-fraction/NDVI-threshold confusion. Do not call this a clean E validation. Next work: disambiguate numerical notation and strengthen source-specific conceptual fidelity. The former C01 40-percent omission claim was withdrawn: the old fixture generator had silently dropped that configured paragraph; it now preserves all supplied paragraphs. See handoff for evidence/digest.
