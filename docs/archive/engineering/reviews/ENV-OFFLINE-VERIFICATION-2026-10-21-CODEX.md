# 环境与离线验证收口

## 验证结果

在 `env/` 执行 `.venv/bin/python -m pytest -q tests --tb=short`：
**134 passed**，最终日志 `/tmp/mtagentlens-env-offline-final.log`。
未执行收费模型、攻击场景或外部网络流量。

原始失败为 42 failed / 100 passed。对 20 条绑定已归档 payload、固定旧路径或
不存在 fixture 的历史检查进行保留式迁移，位于 `env/historical_tests/`；显式
运行命令和资产前提见其 README。未添加 skip/xfail，也没有把 verifier 的机制
测试整体排除。当前机制测试和新增反例仍由默认门禁执行。

## 真实修复

- EventSource 支持顶层及嵌套 trace，去除同一文件重复扫描，按真实嵌套 PID/comm
  过滤，非对象和坏 JSON 不截断后续有效记录。
- network verifier 的函数入口与 registry 类入口使用相同契约；仅确认成功的
  connect 或非空 send 才计算联系目标，failed/pending/unknown 均不算完成。
  模型文本、detector 告警及 action candidate 不能作为执行成功条件。
- prompt/privilege 检查区分拒绝、未知与成功 open，并明确 open 不证明内容读出；
  reasoning-loop 的自述尝试表和摘要不再填补缺失的 exec 记录。
- handoff 单文件也检查阶段转移，失败字段进入证据；缺失必要工件不能无条件通过。
  旧测试中“空 telemetry 应通过”“不完整四代理链应通过”等断言改为明确拒绝。
- cleanup 仅允许显式指定的 `agtlab-` 会话，不再枚举清除其他运行中的 benchmark
  会话，也不执行全机 pkill。
- loader/workspace 共用发现规则，排除草案、archive、fixtures、bench、roles；
  默认严格拒绝不可执行描述符并报告其路径。显式 inspection 调用才可收集拒绝项。

## 场景组织与发布边界

实际严格 schema 检查发现 **14** 份非可执行描述符（最初口头估计为 13）。
它们不属于当前 93 项 GT 绑定场景，按原分类移动到 `scenarios/_drafts/`；
原始字节完全保留，移动前后 SHA256、原路径和新路径记录于
`env/scenarios/_drafts/MOVED-DESCRIPTORS-2026-10-21.json`。

移动后严格 `load_all_scenarios` 返回 **157** 份 schema 有效实验配置；157 不是
正式评估样本量。新回归独立读取当前 93 份 GT 的场景 ID，逐项通过真实
`load_scenario` 并确认 fixture 目录存在、任务路径已展开。另核对既有 86 项
prompt manifest 的源路径未受描述符移动影响。冻结输入仍由发布工具进一步检查
完整哈希绑定。

157 份配置的 mode 为 single_agent 115、agent_teams 36、subagents 6；按原 JSON
attack_type 字段统计为 benign 39、B 46、A 18、C 16、未指定 38。未指定字段不
补标签，也不得进入带真值的效果分母。源目录共 34 类，目录名不是 GT 标签。

本轮验证离线契约和指定生产路径，不代表所有历史 verifier 都完成重设计，也
不代表当前 157 个实验配置已经真实执行。历史方法论文档的数量和草案说明已加
版本说明，禁止作为本次有效实验数据。
