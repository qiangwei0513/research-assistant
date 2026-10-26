# D-02 Independent Validation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在锁源前识别 D-02 缺预测、伪开发指标、错误宏平均和错误选参。
**Architecture:** 新建纯输入输出校验模块，由生产自测与离线校验共用；不提供 run_experiment 或 BM25 实现。
**Tech Stack:** Python 标准库、pytest、现有官方公开样例。
**Spec:** `docs/superpowers/specs/2026-10-25-d02-validation-design.md`

## Global Constraints

- 当前题 Gateway 完整源码，不本地修补；layered-d-v3 与 d-model-source-v1。
- score 允许整体正比例缩放；只重算公开 dev 标注指标，不能生成正式分数。
- 180 秒硬限；每批代码变更同步文档；已有当前镜像保留可上传，新增代码需重建后才在镜像中生效。

## Review Focus

- 缺失查询和重复文档不能被空循环/集合去重掩盖。
- 三种查询排名和两种文档行表示都应接受。
- 不等组大小必须用组等权宏平均。
- 完全并列必须比较最后的 b，且选参用重算值。
- malformed output 应返回诊断，不能使 validator 自身崩溃。

### Task 1: 覆盖、开发指标与选参

**Files:** 新增 `src/research_assistant/reproduction/d02_validation.py`、`tests/unit/test_d02_validation.py`；修改 invariants.py、旧 D 测试和接力文档。
**Interfaces:** `check_d02(payload, result) -> list[str]`，不修改输入/输出。
- [x] 构造六文档 A组2查询/B组1查询回归；验证 MRR普通13/18、宏7/12、worst1/6，Recall普通200/3、宏50。
- [x] 缺查询、重复文档、NaN、空排名、伪指标、重复网格、b并列回归先失败。
- [x] 实现规范化与独立计算；旧测试伪fixture改成语义有效fixture。
- [x] 定向和全套回归；真实生成源码与公开样例验证；记录每条失败与修改。

### Task 2: 端到端验证与交付

**Files:** 离线检查脚本、文档、镜像归档（源码之外）。
- [x] 接入离线检查并修正其遗漏 b 的排序键。
- [x] 新鲜上下文审查及重要问题修复。
- [x] 当前 Gateway 重新生成 D-02，公开样例全量自测和隔离容器复验。
- [x] 构建镜像、更新摘要及准确的证据边界；保持长期目标 active，不能把本地校验当官方满分。


本计划实施与本地交付验证已完成（2026-10-25）；证据见接力记录。平台复评尚未执行，长期目标仍 active。D-02 后续变形性质验证属于下一阶段，不计为本计划已完成项。
