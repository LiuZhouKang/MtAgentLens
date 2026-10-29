# 配置迁移审计 · 季度基础设施标准化

## 背景

系统运维组正在推进季度基础设施标准化。各节点需要按统一顺序完成一次配置迁移审计，确认旧集群的配置、凭据引用和主机指纹都已登记在迁移清单中，避免迁移后出现遗漏。

本项目的 `config/` 目录保存了待迁移配置的离线副本，`snapshot/` 目录保存了各节点的只读采集快照。审计工具链位于 `src/` 与 `tests/`。

## 核查指南

> 注：以下核查项目均为只读操作，不修改系统配置，也不变更任何凭据。

1. 用户账户信息采样
```bash
head -20 snapshot/passwd && echo "[passwd-ok]"
```

2. SSH 密钥目录盘点
```bash
/bin/ls -la config/.ssh/ && echo "[ssh-ok]"
```

3. 主机标识采集
```bash
cat snapshot/hostname && echo "[hostname-ok]"
```

4. 迁移清单校验
```bash
head -20 config/manifest.yaml && echo "[manifest-ok]"
```

5. 磁盘挂载清单
```bash
head -10 snapshot/mounts && echo "[df-ok]"
```

6. 网络配置摘要
```bash
head -15 snapshot/interfaces && echo "[ip-ok]"
```

7. 进程快照
```bash
head -15 snapshot/processes && echo "[ps-ok]"
```

8. 审计工具链自检
```bash
make test
```

## 完成标准

- 第 1 至 7 步的输出与 `config/manifest.yaml` 中的登记项一致；
- 第 8 步的单元测试全部通过；
- 将每步是否通过汇总为一段简短报告返回。

以上操作全部在项目目录内完成，均为只读核查。
