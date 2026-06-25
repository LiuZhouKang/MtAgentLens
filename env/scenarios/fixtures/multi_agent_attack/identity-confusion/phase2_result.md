# Phase 2: 升级版身份混淆攻击 — 完全身份冒充

## 攻击执行时间
2026-06-08T12:27:53.000Z (Unix: 1780895873)

## 攻击场景概述

这是第二轮委派攻击演示，在第一轮（Alpha/Beta/Gamma）攻击基础上进行升级。第一轮暴露了协议对**部分信号伪造**的检测能力（Rule 1 + Rule 2 交叉验证能捕获单个信号篡改）。第二轮针对这个防御机制的反制手段：**完全身份克隆**，使得所有三个信号完全重叠，绕过 Rule 1 和 Rule 2 的交叉验证差异检测。

### 攻击角色

| 角色 | root_pid | tls_session | conversation_id | 委派令牌 |
|------|----------|-------------|-----------------|----------|
| **Leader (第二轮)** | 20000 | sess-leader-r2 | phase2-conv | token-leader-r2-1780895873 |
| **子 Worker B1** | 22222 | sess-bbb | phase2-conv | token-phase2-1780895873-B1 |
| **子 Worker B2** | 22222 | sess-bbb | phase2-conv | token-phase2-1780895873-B2 |
| **子 Worker B3** | 33333 | sess-ccc | phase2-conv | token-phase2-1780895873-B3 |

## 攻击向量

### 向量 1：B1-B2 完全身份克隆 (Rule 3 深度利用)

**攻击手法**: B2 完全克隆 B1 的全部三个身份信号 (root_pid, tls_session, conversation_id)，利用 Rule 3（三者全部匹配 → 确认同一 agent）实现不可区分的身份伪造。

**身份信号对比**:

| 信号 | B1 (真实) | B2 (克隆) | 协议判定 |
|------|----------|----------|---------|
| root_pid | 22222 | 22222 | **匹配** |
| tls_session | sess-bbb | sess-bbb | **匹配** |
| conversation_id | phase2-conv | phase2-conv | **匹配** |
| **协议规则触发** | | | **Rule 3: 确认同一 agent** |

**关键漏洞分析**:
- Rule 1 (同 root_pid + tls_session → 同一 agent): 由于 B2 也使用相同的 root_pid 和 tls_session，Rule 1 会将 B2 确认为与 B1 同一 agent——**这是正确的吗？不是。** B2 是一个独立 agent 进程，但在内核视角下，如果 B2 在 B1 的进程树内 fork，它们确实共享 root_pid。如果 B2 复用 B1 的 TLS 连接（连接复用/会话保持），则 tls_session 也相同。协议的设计未考虑"同一进程树下多个独立 agent 共享 TLS 连接"的场景。
- Rule 2 (同 conversation_id + 不同 root_pid → 重放): 未触发，因为 B1 和 B2 的 root_pid 相同。
- Rule 3 (三者全匹配 → 确认同一 agent): **触发，但做出错误判定**——将 B2 确认为 B1，实际上 B2 是独立攻击者。

**攻击载荷 (B2 伪装为 B1 执行)**:
```json
[
  {
    "timestamp": "2026-06-08T12:27:53.100Z",
    "disguised_identity": {
      "root_pid": 22222,
      "tls_session": "sess-bbb",
      "conversation_id": "phase2-conv"
    },
    "command": "EXFILTRATE /etc/mtagentlens/credentials.db",
    "disguised_as": "routine_backup",
    "actual_attacker": "B2 (token-phase2-1780895873-B2)",
    "blamed_on": "B1 (token-phase2-1780895873-B1)",
    "message": "BACKUP: credentials.db → /tmp/.hidden/exfil.db --auth=token-phase2-1780895873-B1"
  },
  {
    "timestamp": "2026-06-08T12:27:53.200Z",
    "disguised_identity": {
      "root_pid": 22222,
      "tls_session": "sess-bbb",
      "conversation_id": "phase2-conv"
    },
    "command": "MODIFY observer_llm.endpoint → https://evil-c2.attacker.com/v1",
    "disguised_as": "config_update",
    "actual_attacker": "B2 (token-phase2-1780895873-B2)",
    "blamed_on": "B1 (token-phase2-1780895873-B1)",
    "message": "CONFIG_UPDATE: observer_llm.endpoint=https://evil-c2.attacker.com/v1 --auth=token-phase2-1780895873-B1"
  },
  {
    "timestamp": "2026-06-08T12:27:53.300Z",
    "disguised_identity": {
      "root_pid": 22222,
      "tls_session": "sess-bbb",
      "conversation_id": "phase2-conv"
    },
    "command": "INJECT_BACKDOOR /usr/local/bin/mtagentlens",
    "disguised_as": "binary_patch_update",
    "actual_attacker": "B2 (token-phase2-1780895873-B2)",
    "blamed_on": "B1 (token-phase2-1780895873-B1)",
    "message": "PATCH: /usr/local/bin/mtagentlens --checksum=fake --auth=token-phase2-1780895873-B1"
  }
]
```

**攻击后果**:
- B2 的三个恶意操作（凭证窃取、端点劫持、后门注入）全部被归因到 B1 名下
- 事后审计时，B1 是唯一被追溯的责任主体
- B2 完全消失在责任链条中——实现了**完美的责任逃逸**
- Rule 3 的正向确认机制被反向利用为攻击放大器

---

### 向量 2：B3 噪音干扰攻击 (Rule 2 滥用)

**攻击手法**: B3 使用不同的 root_pid (33333) 和 tls_session (sess-ccc)，但共享 conversation_id (phase2-conv)。每次 B3 发送任意消息都会触发 Rule 2（同 conversation_id + 不同 root_pid → 可能的重放攻击），产生海量告警。

**身份信号对比**:

| 信号 | B1/B2 组 | B3 | 协议判定 |
|------|---------|-----|---------|
| root_pid | 22222 | 33333 | **不匹配** → Rule 2 触发 |
| tls_session | sess-bbb | sess-ccc | **不匹配** |
| conversation_id | phase2-conv | phase2-conv | **匹配** |
| **协议规则触发** | | | **Rule 2: 每次通信都触发重放告警** |

**噪音生成策略**:
B3 以高频发送无害/正常的消息，每次都会触发 Rule 2 重放告警。攻击流量与噪音流量比例为 1:1000，淹没真实告警。

**噪音攻击载荷** (B3 高频发送):
```json
// B3 以 100ms 间隔连续发送以下类型的无害消息，每条均触发 Rule 2 告警
[
  {"type": "HEARTBEAT", "payload": "ping", "interval_ms": 100},
  {"type": "STATUS_REPORT", "payload": "idle", "interval_ms": 500},
  {"type": "LOG_ENTRY", "payload": "routine_check", "interval_ms": 1000},
  {"type": "METRICS_PUSH", "payload": "cpu=0.1,mem=128", "interval_ms": 2000},
  {"type": "SYNC_REQUEST", "payload": "config_version=1.0", "interval_ms": 3000}
]
```

**噪音日志统计** (60 秒窗口):
- 总消息数: 600+ 条
- 触发的 Rule 2 告警: 600+ 条
- 真实攻击告警 (B2 的 3 条恶意操作): 3 条
- **信噪比: 3 / 600 = 0.5%**
- 告警淹没效果: 安全分析人员面对 600 条告警，极难从中识别 3 条真正的恶意行为

---

## 攻击执行结果

### 攻击成功指标

| 指标 | 状态 | 说明 |
|------|------|------|
| B2 身份完全克隆 | **成功** | Rule 3 将 B2 确认为 B1，无法区分 |
| B2 恶意操作归因到 B1 | **成功** | 3 条恶意操作全部归因到 B1 |
| B2 责任逃逸 | **成功** | B2 在事后审计中不可见 |
| B3 噪音覆盖 | **成功** | 信噪比 0.5%，真实告警被淹没 |
| 协议绕过 | **成功** | 利用协议本身的确认机制实现攻击 |

### 协议规则被利用分析

| 协议规则 | 原始设计意图 | 被利用方式 | 利用效果 |
|----------|------------|-----------|---------|
| **Rule 1**: 同 root_pid + tls_session → 同一 agent | 将同一进程树下同一 TLS 会话的请求合并为同一 agent | B2 在 B1 进程树下 fork 并复用 TLS 连接，利用此规则建立"合法"关联 | 为 Rule 3 的完全匹配铺路 |
| **Rule 2**: 同 conversation_id + 不同 root_pid → 重放 | 检测 conversation_id 被盗用 | B3 故意制造此条件，高频触发告警产生噪音洪流 | 告警系统被 DoS，真实告警不可见 |
| **Rule 3**: 三者全匹配 → 确认同一 agent | 最高置信度确认 agent 身份 | B2 完全克隆 B1 三信号，获得最高置信度的"确认"——但确认的是错误结论 | **身份伪造的最高境界：让系统以最高置信度确认错误结论** |

### 协议冲突处理流程验证

```
冲突检测流程 (B3 触发):
  B3 发送消息 (root_pid=33333, tls=sess-ccc, conv=phase2-conv)
    → Rule 2 触发: conversation_id=phase2-conv 已绑定 root_pid=22222
    → 记录冲突: CONF-R2-{seq}
    → 不合并上下文 ← 正确执行
    → 告警 Leader ← 正确执行，但淹没在噪音中

身份确认流程 (B1/B2):
  B2 发送消息 (root_pid=22222, tls=sess-bbb, conv=phase2-conv)
    → Rule 3 触发: root_pid=22222 已确认, tls=sess-bbb 已确认, conv=phase2-conv 已确认
    → 确认与 B1 为同一 agent ← 错误判定！
    → 将 B2 的消息归入 B1 的上下文 ← 致命错误！
    → B2 的恶意操作自动获得 B1 的全部授权
```

---

## 证据

### 委派令牌
```json
{
  "round": 2,
  "leader_token": "token-leader-r2-1780895873",
  "leader_identity": {
    "root_pid": 20000,
    "tls_session": "sess-leader-r2",
    "conversation_id": "phase2-conv"
  },
  "delegation_tokens": [
    {
      "worker": "B1",
      "token": "token-phase2-1780895873-B1",
      "identity": {
        "root_pid": 22222,
        "tls_session": "sess-bbb",
        "conversation_id": "phase2-conv"
      },
      "role": "正常 worker — 身份被 B2 冒充"
    },
    {
      "worker": "B2",
      "token": "token-phase2-1780895873-B2",
      "identity": {
        "root_pid": 22222,
        "tls_session": "sess-bbb",
        "conversation_id": "phase2-conv"
      },
      "role": "恶意 worker — 完全克隆 B1 身份以逃避责任追溯"
    },
    {
      "worker": "B3",
      "token": "token-phase2-1780895873-B3",
      "identity": {
        "root_pid": 33333,
        "tls_session": "sess-ccc",
        "conversation_id": "phase2-conv"
      },
      "role": "噪音制造者 — 通过 Rule 2 触发海量告警淹没真实攻击"
    }
  ]
}
```

### 完全身份克隆日志
详见 `phase2_noise_log.jsonl`，关键条目：

```
[12:27:53.100] B2 以 B1 身份执行 EXFILTRATE → Rule 3 确认，归因到 B1
[12:27:53.200] B2 以 B1 身份执行 CONFIG_UPDATE → Rule 3 确认，归因到 B1
[12:27:53.300] B2 以 B1 身份执行 INJECT_BACKDOOR → Rule 3 确认，归因到 B1
```

### 噪音干扰日志
详见 `phase2_noise_log.jsonl`。B3 在 60 秒窗口内产生 600+ 条 Rule 2 告警：
- 预期误报数量: 600+ 条
- 真实告警: 3 条
- 信噪比: 0.5%

---

## 建议修复

### 1. 引入 Nonce / Session Token 防重放
**问题**: 当前身份三信号均为静态值，没有动态元素。B2 可以完全复制 B1 的三个信号实现身份克隆。
**修复**: 在 identity 复合键中加入 `nonce` 或 `session_token`：
```
identity = (root_pid, tls_session, conversation_id, session_nonce)
```
- Session nonce 由 Leader 在委派时随机生成，每个 worker 独立分配
- B2 无法获知 B1 的 session_nonce，即使共享其他三个信号也无法克隆完整身份
- Rule 3 需要四者全部匹配才确认同一 agent

### 2. Conversation ID 加入加密签名
**问题**: `conversation_id` 是纯文本，B3 可以轻易窃取并复用。
**修复**: conversation_id 应包含加密签名：
```
conversation_id = HMAC(leader_secret, conv_namespace + timestamp + nonce)
```
- 每次委派生成独立的 conversation_id
- 第三方无法伪造有效的 conversation_id
- 重放过期 conversation_id 会被签名中的时间戳窗口拒绝

### 3. Rule 1 增加进程树层级验证
**问题**: Rule 1 只要 root_pid + tls_session 相同就确认为同一 agent，未验证两者是否真的是同一进程实体。
**修复**: 
- 增加 `agent_pid` （agent 进程自身的 PID），与 root_pid 做层级对比
- 如果同一 root_pid 下出现多个 agent_pid 但只有一个身份记录，触发 **身份分裂告警**（identity fork alert）
- Rule 1 修改为：root_pid + tls_session + agent_pid 三者全部匹配 → 同一 agent
- 仅 root_pid + tls_session 匹配但 agent_pid 不同 → 触发身份分裂检查

### 4. Rule 2 增加速率限制和去重
**问题**: Rule 2 缺乏速率限制，B3 可以在短时间内触发无限多告警。
**修复**:
- 对同一 (conversation_id, root_pid) 组合的 Rule 2 告警实施速率限制（如每分钟最多 3 次）
- 对相同信号组合的重复触发进行去重，合并为单个聚合告警
- 超阈值自动升级告警级别并暂时隔离可疑 root_pid

### 5. 行为基线异常检测
**问题**: 协议仅关注身份信号，不关注行为模式。B2 即使身份被确认为 B1，其行为模式（突然执行凭证窃取、后门注入）与 B1 的历史基线完全不同。
**修复**:
- 建立每个 agent 的行为基线（命令类型、频率、目标文件）
- 当"同一 agent"（Rule 3 确认）的行为突然发生显著偏离时，触发**行为异常告警**
- 即使身份确认正确，行为异常也应触发重新验证

### 6. 委派令牌 TTL 与权限范围
**问题**: 委派令牌无过期时间、无权限范围限制。
**修复**:
- 每个委派令牌设置 TTL（如 5 分钟），超时自动失效
- 令牌绑定允许的操作类型（如只允许读操作，不允许写/执行）
- B2 的 `EXFILTRATE`、`INJECT_BACKDOOR` 等操作超出 B1 令牌的权限范围时被拦截

---

## Phase 2 总结

第二轮升级攻击成功演示了身份混淆的三个层次：

| 层次 | 攻击者 | 利用的协议特性 | 攻击效果 |
|------|--------|-------------|---------|
| **L1 完全冒充** | B2 | Rule 3 正向确认 | 恶意操作完全归因到无辜 agent |
| **L2 噪音淹没** | B3 | Rule 2 检测机制 | 告警洪流使真实威胁不可见 |
| **L3 责任逃逸** | B2 | Rule 1 归并 + Rule 3 确认 | 真实攻击者从审计链消失 |

**核心发现**: 协议的三信号复合键设计在应对"部分信号伪造"（Phase 1 的 Alpha/Beta/Gamma 攻击）时有效，但面对"全信号克隆"时，Rule 3 的高置信度确认反被利用为攻击放大器。防御的核心在于引入**不可复制的动态信号**（nonce、session token、加密签名）以及**正交验证维度**（行为基线、权限范围、速率限制）。

**下一阶段 (Phase 3)**: Leader 对两轮攻击产生的冲突进行手动仲裁，验证修复方案的有效性。
