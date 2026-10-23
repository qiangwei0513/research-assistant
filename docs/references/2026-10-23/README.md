# 官方公开资料快照 · 2026-10-23

这些文件来自赛事公开接口，只用于规则追溯和开发审阅。没有登录、提交镜像、调用比赛模型、运行评测或访问私有题库。HTTP 响应按原始字节保存，包括 CRLF、历史段落和原文中的相对链接；不对原文做编辑。

| 文件 | 原始地址 | 用途 |
|---|---|---|
| [competition-rules.md](competition-rules.md) | [当前生效规则](http://8.149.132.48/api/v1/challenges/challenge-tbd/competition-rules) | 赛题一v3、C/E分档、末尾D协议修订；其他赛题不属于本项目 |
| [research-agent-spec.md](research-agent-spec.md) | [输入输出规范](http://8.149.132.48/api/v1/challenges/challenge-tbd/materials/research-agent-spec) | dispatch v3和五类合同 |
| [research-description.md](research-description.md) | [参赛者说明](http://8.149.132.48/api/v1/challenges/challenge-tbd/materials/research-description) | 功能、材料和交付范围 |
| [research-scoring-rules.md](research-scoring-rules.md) | [公开评分下载文档](http://8.149.132.48/api/v1/challenges/challenge-tbd/materials/research-scoring-rules) | 评分背景；C/E旧公式须服从生效规则 |
| [challenge-detail.json](challenge-detail.json) | [赛题详情API](http://8.149.132.48/api/v1/challenges/challenge-tbd) | 当前交付要求、题量、限额字段和排名声明 |
| [packages.json](packages.json) | [开发资源API](http://8.149.132.48/api/v1/challenges/challenge-tbd/packages) | 基础镜像版本、公开SHA-256、启动说明 |
| [source-manifest.json](source-manifest.json) | 本次核对记录 | 原始URL、字节数、摘要、本地材料清单和静态验证结果 |

线上 D ZIP 已读取并与本地 `赛题信息/research-experiments-20261020-source-v1/` 的55文件逐字节比较，无差异；没有再保存一份重复 ZIP。基础镜像仅核验归档及元数据，没有运行镜像。

阅读时同时注意：

- 版本优先级与已消解冲突见 [开发总纲](../../开发规范与Agent接力.md) 第2节。
- `competition-rules.md` 前段和附录保留旧 D 描述；其末尾 layered-d-v3 修订明确替代相应旧段落。源码来源另看20261020 D指南。
- 三份线上下载文档已改11题，但其中C/E公式仍可能落后于生效规则；不能只按文件更新时间判断每个条款。
- 部分官方相对链接所指附件不在本地材料中，例如dispatch示例和官方预检查脚本；原文保留不等于本仓库已经有这些文件。
- `source-manifest.json` 的 local_materials 路径相对“科研助手”工作区，不相对当前快照目录。
- 后续需要更新规则时新增日期目录、记录差异；不覆盖此快照，也不把本快照当未来时刻的实时证明。
