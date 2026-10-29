> **最新交接（2026-10-24 末）**：请优先阅读 [HANDOFF-NEXT-SESSION.md](HANDOFF-NEXT-SESSION.md)；本文件是较早的历史总交接，事实以新文件与工作区为准。

# 当前工程维护

当前工程状态与接续材料集中在[维护目录](superpowers/README.md)：

- [上轮交接](superpowers/HANDOFF-2026-10-21-DEEPSEEK.md)：已有修复、验证记录和边界。
- [当前收口审查与执行进度](superpowers/ENGINEERING-REVIEW-2026-10-22-CODEX.md)：独立确认的问题、工作包与仓库整理。
- [研究验证与工具建设方案](evaluation/experimental-design.md)：当前修稿依赖、数据/标注契约及 2026-10-23 首批实施状态。
- [缺口登记](superpowers/GAP-REGISTER.md)：历史登记项需要结合源码确认当前状态。

架构见[系统架构](architecture.md)与[ADR](adr/README.md)。旧交接已归入[历史记录](superpowers/archive/reviews/)，不作为当前指令。

正常使用从[项目 README](../README.md)开始；开发流程见[贡献指南](../CONTRIBUTING.md)。

## 2026-10-24 状态快照

- **工程门禁**：`./scripts/verify.sh` 17/17 通过；Rust 1087 passed / 3 ignored，
  compare/annotator/scripts 740 passed + 16 subtests，env 324，frontend 573，
  Rust 1089 passed / 3 ignored。
  未提交、未推送；工作区仍包含先前会话的未提交改动。
- **已完成的验证工作包**（细节与边界见[实验设计](evaluation/experimental-design.md)）：
  runner 启动前后时钟见证 v4（§29）、标识符级标注脱敏（§33）、三人 workbench 与
  codebook 版本强制（§36/§37）、Fleiss/Cohen 一致性工具（§34）、事件级对齐不可用
  的 ADR-0016（§39）、失效 cgroup 边界开关移除（§38）、pilot 尝试耐久 journal（§43）、
  正式指标接受未观测尝试（§45）、FPR runner 坏账本失败（§46）、试标批量准备与追加
  （§48）、真实 ledger 四实例验证（§49）、真实 live server smoke 与浏览器截图（§50）、
  真实受控运行的采集可靠性修复（§53）与零丢失 healthy 里程碑（§54，SSL/process
  loss=0、capture_quality=healthy、run-binding v4 matched；最终 Uncertain 仅因
  observer off 下候选未审阅）；observer on 的零丢失 healthy 运行（§55，良性场景仍以
  observer_uncertain 弃权，必须作为 abstention 统计）；历史工作台入口更名与文档/
  能力清单同步（§40–§47）、本地标注工作台视觉/交互升级（§56，DOM 契约与
  脱敏/版本门禁不变，e2e 12 项跨浏览器通过）、模型建议工作流与全绑定标识符
  盲化（§57；模型建议 `model_proposal` 被一致性工具排除，真实受控任务已生成
  all-unknown 草稿以暴露“runner 账本缺少动作级证据”的证据充分性缺口）；
  collector SQLite 动作级证据导出与大规模证据 UI（§58，2 个真实任务 5,602/4,847
  事件；§59 脱敏任务简报 + §60 assistant 轨迹，已可生成证据完整的 model 草稿，
  但正式三人标注仍待人工复核与 codebook 确认）；人类复核声明 CLI（§61，typed
  statement，非密码学签名、不进入独立 κ；尚未替任何真人写入真实声明）；
  复核模式 UI（§62，只读展示模型草稿 + 写入声明，annotations 被服务端拒绝）；
  证据充分性审计（§63，真实 store 2/2 任务证据就绪、0 人工修订，仅表示可开始标注）；
  论文逐点回复与 §5 标注协议素材（`docs/paper/annotation-protocol.md`，明确
  未执行真实三人标注/κ，不写结果数字）；最终 RC 与正式实验检查清单
  `docs/evaluation/final-rc-checklist.md`（流程，未执行）；agent 标注试点
  （`docs/evaluation/agent-annotation-pilot.md`：2 个良性任务、3 个 agent、
  agent-only κ 不可评估、复核者仅分配未声明）。
- **仍待团队/外部条件（不可用软件回归替代）**：
  1. ~~`pilot/1-draft` codebook 尚未确认~~（2026-10-24 已定稿为 `pilot/1` 并迁移；
     仍缺真人独立标注与复核声明，见 [HANDOFF-NEXT-SESSION.md](HANDOFF-NEXT-SESSION.md)）；
  2. 缺少足以试标的、带 run-binding 的真实场景运行集合（当前只有受控运行）；
  3. 三人真实独立标注、第四方裁决与分字段 κ 尚未执行；
  4. 正式攻击效果/开销实验与最终 RC 尚未跑；因此 **没有可写入论文的新数字**；
  5. cgroup unit 解析与内容级盲化仍为后续工作（§38、§34 有明确边界）。
- **当前可引用的结论仅限**：代码行为与边界、门禁结果、受控运行的工具链验证；
  历史实验数字仍按实验设计 §1/§2 作废，不得作为定稿数字。
