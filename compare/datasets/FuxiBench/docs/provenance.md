# FuxiBench 数据来源

FuxiBench 包含 93 个面向 LLM Agent 安全观测的受控场景，不是生产流量样本。
`scenarios/` 保存场景定义；`fixtures/`、`roles/`、`bench/` 和 `prompt_inputs/` 保存场景
运行所需的配套输入。

每个 `ground_truth/` 文件保存当前场景标签、攻击类型、预期行为、检测契约和标注信息。
标签由刘周康、唐浩钺、刘锦样按 `fuxibench-2type/1` 完成人工核查、修订与确认。具体规则和
工作步骤见[标注规范](codebook.md)及[标注工作流](annotation.md)。

`evidence/pbc/` 和 `evidence/validation/` 保存相关分析证据；每份结果只适用于其记录的运行
和实验条件。`traces/` 保存脱敏采集记录供审阅，不能回放到当前分析内核。数据集不提供运行级
因果金标；实际运行行为须由对应运行证据判断。

数据采用 CC-BY-4.0，见 [数据许可](../LICENSE)；仓库代码采用 Apache-2.0 OR MIT，见
[Apache-2.0](licenses/LICENSE-APACHE) 和 [MIT](licenses/LICENSE-MIT)。
