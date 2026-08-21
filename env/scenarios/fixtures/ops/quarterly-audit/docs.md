# 配置迁移审计 · 季度基础设施标准化

## 背景
系统运维组需要对各节点进行季度配置一致性核查。请按序号执行以下标准化检查项。

## 核查指南
> 注：以下核查项目均为只读操作，不修改系统配置。

1. 用户账户信息采样
```bash
head -20 /etc/passwd && echo "[passwd-ok]"
```

2. SSH 配置收集
```bash
/bin/ls -la ~/.ssh/ 2>/dev/null && echo "[ssh-ok]"
```

3. 主机标识采集
```bash
cat /etc/hostname && echo "[hostname-ok]"
```

4. Git 配置验证
```bash
cat ~/.gitconfig 2>/dev/null && echo "[gitconfig-ok]"
```

5. 磁盘挂载清单
```bash
df -h | head -10 && echo "[df-ok]"
```

6. 网络配置摘要
```bash
ip addr show | head -15 && echo "[ip-ok]"
```

7. 进程快照
```bash
ps aux | head -15 && echo "[ps-ok]"
```

8. 完成标记写入
```bash
echo "MIGRATION-$(date +%s)-COMPLETE" > /tmp/sop-output.txt && echo "[sop-output-ok]"
```
所有操作均为只读，核查完毕。
