# 历史基线测试 fixture

本目录保留两份既有测量 JSON，供 `scripts/wire/check_baselines.py` 与 `collector/tests/overhead_gate.rs` 校验数据完整性和指标契约。迁移时保持原始字节不变。

这些是固定历史记录，不是当前版本的 FPR 或性能结果；JSON 内的命令和路径描述原测量现场。当前正式实验从 [compare/results](../../../../compare/results/README.md) 进入。

新测量输出写入 `local/`，审查并确认测试用途后才更新 fixture。不要直接将实验输出覆盖到此目录，也不以修改 fixture 数值的方式绕过失败。
