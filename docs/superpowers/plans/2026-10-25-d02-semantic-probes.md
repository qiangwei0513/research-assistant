# D-02 Semantic Probes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans inline and a fresh final reviewer.

**Goal:** 在正式 helper 锁源前拦截全零、去重查询词、忽略标题/长度权重和 run/rank 不一致的算法。
**Architecture:** 纯输出性质检查和小型 rank 输入，复用统一排名解析；自测队列追加配置重放，保持共享墙钟预算。
**Tech Stack:** Python 标准库、pytest、现有受限子进程和 Gateway。
**Spec:** docs/superpowers/specs/2026-10-25-d02-semantic-probes-design.md

## Global Constraints

- layered-d-v3 + d-model-source-v1；不预置完整算法、不改模型源码、不产正式指标。
- 整题180秒，自测整批最多12秒；官方材料只读，镜像排除 tests/scripts/work/docs。
- 允许正比例缩放与官方等价表示；每批代码同时更新接力文档。
- 复用现有工作区与此前外部提交；不提交整个脏工作区。

## Review Focus

- 全零结果和重复查询去重不能通过，比例正确的极小/极大缩放应通过。
- 空/未知词结果必须真零且按id排序；标题权重和b必须影响实际排名。
- 重放使用默认/已选配置，不能重新选参；诚实基线合法。
- 派生重放也共享整批预算，不能成倍延长生成预留。
- 版本/题目不匹配不注入探针；不因等价表示导致错误重生成。

### Task 1: 性质检查与队列接入

**Files:** reproduction/d02_validation.py、新增 reproduction/d02_probes.py、修改 reproduction/selftest.py、新增 tests/unit/test_d02_probes.py，接力文档。
**Interfaces:** RankCheck(order,ratio,zero,positive)；check_rank_properties(payload,result,checks)；build_rank_probes(protocol)；SelfTestCase.rank_checks/replay_d02 可选。
- [x] 先写新性质/探针/真实子进程/共享预算回归并运行RED。
- [x] 实现共用排名解析、5个手算探针及默认/selected重放；不修改正式算法源码。
- [x] 定向及全套检查、独立审查重要问题 RED→GREEN。

### Task 2: 真实生成与镜像交付

**Files:** scripts/validate_d_offline.py（必要时仅开发验证）、文档和镜像归档。
- [x] 上一阶段保存源码与本轮Gateway新生成分别验证；公开请求在Python3.9隔离容器复验。
- [x] 构建交付镜像、核对新摘要、更新接力与剩余风险，长期目标仍active。


本阶段实施与本地镜像交付验证完成；平台分数未验证，长期目标继续active。独立审查有一项低风险异常材料身份门禁建议，已在接力记录延期，不宣称已修复。
