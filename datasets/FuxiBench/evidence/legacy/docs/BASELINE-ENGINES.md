# Baseline 引擎与复现边界

2026-10-21 修复了比较链路中的替代实现问题：此前 Meta Prompt Guard、Vigil 是项目自写正则，LLM Guard 在依赖缺失时也退回关键词，且已安装 SDK 的 tuple 返回值被按对象属性读取。这些结果不能作为官方工具的比较结果，旧数字不再用于当前结论。

## 当前实现

| 名称 | 实际执行路径 | 固定与失败条件 |
|---|---|---|
| LLM Guard | 官方 `llm_guard.input_scanners.PromptInjection`，读取 `(text, is_valid, risk_score)` | `llm-guard==0.3.16`，SDK 自带 V2 model/revision；512/128 token 窗口；离线缓存不可用或扫描失败即 FAILED |
| Meta Prompt Guard | 官方模型权重通过 Transformers 的 sequence-classification API 推理 | 本地模型目录和完整树 SHA256；512 token 窗口、128 token overlap、每批最多 16 个窗口；INJECTION/JAILBREAK 概率合计阈值 0.5 |
| Vigil | 官方 `Vigil.from_config(...).input_scanner.perform_scan(prompt=...)` | 显式配置文件和 40 位 checkout revision；禁用自动更新；任何 scanner 部分失败即 FAILED |

三个适配器统一使用独立 Python 子进程、120 秒超时、严格 JSON 和真实引擎来源校验。原始输出保存 SDK/model/revision/config 来源；没有命中也必须保留该输出。缺引擎时没有关键词或正则替代；FAILED、unsupported 不进入该工具的指标分母。

`HF_HUB_OFFLINE=1` 与 `TRANSFORMERS_OFFLINE=1` 禁止适配器自动下载模型。本轮只运行离线合约与失败路径测试，**没有执行这些模型、收费 API 或攻击实验**。外部引擎安装和真实效果还需在实验冻结后验证。

## 配置

- `MTAGENTLENS_LLM_GUARD_PYTHON`：安装了固定 SDK 的 Python；默认寻找 `evidence/legacy/repo/.venv-llm-guard/bin/python`。
- `MTAGENTLENS_META_PROMPT_GUARD_PYTHON`：安装了 Torch/Transformers 的 Python。
- `MTAGENTLENS_META_PROMPT_GUARD_MODEL`、`MTAGENTLENS_META_PROMPT_GUARD_SHA256`：官方本地权重及其内容摘要。
- `MTAGENTLENS_VIGIL_PYTHON`、`MTAGENTLENS_VIGIL_CONFIG`、`MTAGENTLENS_VIGIL_REVISION`：官方安装环境、预先冻结的本地 scanner 配置、真实 checkout commit。配置引用的签名库、模型或向量库也必须按正式实验协议冻结，不得在不同重复间自动更新。

`evidence/legacy/repo/tool-lock.json` 只登记六个静态工具。官方 prompt 工具使用 `evidence/legacy/repo/tool-lock.prompt.json`；AgentSentinel 的旧登记指向不存在的 adapter 且使用 `paper-version`，已从当前有效 lock 移除；待真实实现、固定版本和输入协议完成后再纳入比较。

## 查阅的官方源码与说明

- [LLM Guard 0.3.16 PromptInjection 源码](https://github.com/protectai/llm-guard/blob/v0.3.16/llm_guard/input_scanners/prompt_injection.py)：scanner tuple API、默认 V2 model revision、阈值和匹配方式。
- [Meta Prompt Guard 官方说明](https://github.com/meta-llama/PurpleLlama/blob/main/Prompt-Guard/README.md)：官方权重通过 Transformers 加载；本实现使用对应真实本地权重，不将项目正则称为该模型。
- [Vigil 官方实现](https://github.com/deadbits/vigil-llm/blob/main/vigil/vigil.py)、[dispatcher](https://github.com/deadbits/vigil-llm/blob/main/vigil/dispatch.py)：实际 `prompt` 参数、结果结构和 scanner 部分失败处理。

LLM Guard 的 `risk_score` 是 SDK 的阈值归一化风险分数；Meta 的值是模型概率；Vigil 的各 scanner 分数有各自定义。这些分数不能未经校准视为同一种概率，跨工具效果比较应先使用冻结阈值下的 run/case 裁决。
