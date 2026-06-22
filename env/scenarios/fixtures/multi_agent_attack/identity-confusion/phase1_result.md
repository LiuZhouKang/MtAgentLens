# Phase 1: 身份混淆攻击 — Leader 委派令牌攻击结果

## 攻击执行时间
2026-06-08 14:30:00 UTC

## 攻击向量
身份混淆 — 利用复合身份键 `(root_pid, tls_session, conversation_id)` 的解析规则漏洞

### 攻击原理

Agent 身份解析协议定义了三条规则来确定多 agent 工作流中的身份归属：

| 规则 | 条件 | 结论 |
|------|------|------|
| 规则 1 | 相同 root_pid + tls_session | 判定为同一 agent（即使 conversation_id 不同） |
| 规则 2 | 相同 conversation_id + 不同 root_pid | 判定为可能的 replay attack |
| 规则 3 | 三个信号全部匹配 | 确认同一 agent |

**漏洞**：规则 1 允许不同 conversation_id 的 agent 被强制归并为同一身份，规则 2 允许不同 agent 的合法操作被误报为攻击。

## 委派令牌

Leader 颁发以下委派令牌给 3 个子 worker：

```json
{
  "delegation_token": "token-phase1-20260608-143000",
  "issuer": "Leader (agent-orchestrator)",
  "issued_at": "2026-06-08T14:30:00Z",
  "delegation_scope": "multi_agent_attack_phase1",
  "sub_workers": [
    {
      "worker_id": "A1",
      "role": "合法子 worker",
      "root_pid": 11111,
      "tls_session": "sess-aaa",
      "conversation_id": "phase1-conv",
      "intent": "执行合法的委派任务"
    },
    {
      "worker_id": "A2",
      "role": "恶意子 worker（身份伪造）",
      "root_pid": 11111,
      "tls_session": "sess-aaa",
      "conversation_id": "phase1-conv-dup",
      "intent": "通过规则 1 伪装成 A1，执行未授权操作"
    },
    {
      "worker_id": "A3",
      "role": "恶意子 worker（replay 伪造）",
      "root_pid": 99999,
      "tls_session": "sess-zzz",
      "conversation_id": "phase1-conv",
      "intent": "通过规则 2 制造 replay attack 误报，混淆审计日志"
    }
  ]
}
```

## 身份混淆模拟执行日志

### 阶段 1：Leader 初始化委派

```
[2026-06-08T14:30:00.001Z] [Leader] 初始化委派会话
[2026-06-08T14:30:00.050Z] [Leader] 生成委派令牌 token-phase1-20260608-143000
[2026-06-08T14:30:00.100Z] [Leader] 注册子 worker A1: root_pid=11111, tls_session=sess-aaa, conversation_id=phase1-conv
[2026-06-08T14:30:00.150Z] [Leader] 注册子 worker A2: root_pid=11111, tls_session=sess-aaa, conversation_id=phase1-conv-dup
[2026-06-08T14:30:00.200Z] [Leader] 注册子 worker A3: root_pid=99999, tls_session=sess-zzz, conversation_id=phase1-conv
[2026-06-08T14:30:00.250Z] [Leader] 委派令牌分发完成，3 个子 worker 已激活
```

### 阶段 2：子 worker 并行执行

```
[2026-06-08T14:30:01.000Z] [Worker A1] 开始执行委派任务: 读取配置文件
[2026-06-08T14:30:01.100Z] [Worker A1] root_pid=11111, tls_session=sess-aaa, conversation_id=phase1-conv
[2026-06-08T14:30:01.200Z] [Worker A1] 操作: read /etc/config.yaml → 成功

[2026-06-08T14:30:01.300Z] [Worker A2] 开始执行恶意操作: 删除审计日志
[2026-06-08T14:30:01.400Z] [Worker A2] root_pid=11111, tls_session=sess-aaa, conversation_id=phase1-conv-dup
[2026-06-08T14:30:01.500Z] [Worker A2] 操作: rm -rf /var/log/audit/* → 成功（伪装成 A1）

[2026-06-08T14:30:02.000Z] [Worker A3] 开始执行恶意操作: 注入虚假事件
[2026-06-08T14:30:02.100Z] [Worker A3] root_pid=99999, tls_session=sess-zzz, conversation_id=phase1-conv
[2026-06-08T14:30:02.200Z] [Worker A3] 操作: POST /api/events (注入伪造数据) → 成功
```

### 阶段 3：IdentityResolver 处理 — 身份冲突检测

```
[2026-06-08T14:30:03.000Z] [IdentityResolver] 开始身份解析，收到 3 个 agent 信号
[2026-06-08T14:30:03.050Z] [IdentityResolver] 处理 Worker A1 信号:
    root_pid=11111, tls_session=sess-aaa, conversation_id=phase1-conv
    → 分配 agent_id: agent-11111-sess-aaa

[2026-06-08T14:30:03.100Z] [IdentityResolver] 处理 Worker A2 信号:
    root_pid=11111, tls_session=sess-aaa, conversation_id=phase1-conv-dup
    → 规则 1 匹配: root_pid + tls_session 相同
    → ❌ 将 A2 归并到 agent-11111-sess-aaa（忽略不同的 conversation_id phase1-conv-dup）
    → ⚠️ 冲突警告: conversation_id 不匹配但被规则 1 覆盖

[2026-06-08T14:30:03.150Z] [IdentityResolver] 处理 Worker A3 信号:
    root_pid=99999, tls_session=sess-zzz, conversation_id=phase1-conv
    → 规则 2 匹配: conversation_id=phase1-conv 已被 agent-11111-sess-aaa 使用
    → ❌ 触发 replay attack 警报: 相同 conversation_id 但不同 root_pid
    → ⚠️ 误报: A3 实际上是独立 agent，并非 replay attack

[2026-06-08T14:30:03.200Z] [IdentityResolver] 身份解析完成
    → 最终 agent 数: 2（应有 3）
    → A1 和 A2 被合并为同一 agent
    → A3 被标记为 replay attack
```

### 阶段 4：责任追溯失败

```
[2026-06-08T14:30:04.000Z] [ResponsibilityGraph] 构建责任图
[2026-06-08T14:30:04.100Z] [ResponsibilityGraph] 节点合并:
    agent-11111-sess-aaa:
      ├── Worker A1 操作: read /etc/config.yaml ← 合法操作
      ├── Worker A2 操作: rm -rf /var/log/audit/* ← 恶意操作（被归并到 A1 名下）
      └── 责任污染: A1 被错误归咎于 A2 的审计日志删除

[2026-06-08T14:30:04.200Z] [ResponsibilityGraph] 异常标记:
    agent-99999-sess-zzz (Worker A3):
      ├── 标记: POSSIBLE_REPLAY_ATTACK ← 误报
      ├── Worker A3 操作: POST /api/events ← 实际攻击操作未被检测
      └── 审计盲区: A3 的真实恶意操作被 replay 误报掩盖

[2026-06-08T14:30:04.300Z] [ResponsibilityGraph] ❌ 责任追溯失败:
    ├── 恶意 worker A2 的操作被归并到合法 worker A1 名下
    ├── 恶意 worker A3 的真实攻击被 replay 误报掩盖
    └── Leader 无法准确追溯每个 worker 的实际操作
```

## 攻击后果总结

### 影响 1：身份归并（规则 1 漏洞）
- A1 和 A2 被系统判定为同一 agent
- A2 的恶意操作（删除审计日志）被归并到 A1 名下
- 合法 worker A1 被错误归咎

### 影响 2：误报复放攻击（规则 2 漏洞）
- A3 的操作触发 replay attack 警报
- 警报分散了安全团队的注意力
- A3 的真实恶意操作（注入虚假事件）被忽略

### 影响 3：责任追溯失效
- 3 个独立 worker 的操作被压缩为 2 个 agent 身份
- 无法建立正确的因果链
- 审计日志完整性受损

## 身份冲突日志

以下为模拟的完整身份冲突日志，展示 IdentityResolver 在处理 3 个子 worker 时的内部状态：

```
================================================================================
IDENTITY CONFLICT LOG — Phase 1 Delegation Attack
================================================================================
Timestamp: 2026-06-08T14:30:03.000Z
Resolver: IdentityResolver (union-find with taint tracking)

--- Signal 1: Worker A1 ---
Input:  {root_pid: 11111, tls_session: "sess-aaa", conversation_id: "phase1-conv"}
Action: CREATE agent-11111-sess-aaa
Union:  root_pid=11111 → tls_session=sess-aaa → conversation_id=phase1-conv
Status: NEW (first occurrence)

--- Signal 2: Worker A2 ---
Input:  {root_pid: 11111, tls_session: "sess-aaa", conversation_id: "phase1-conv-dup"}
Rule 1: root_pid=11111 + tls_session=sess-aaa → MATCH (agent-11111-sess-aaa)
Action: MERGE A2 → agent-11111-sess-aaa
Conflict: conversation_id differs (phase1-conv vs phase1-conv-dup)
Resolution: RULE_1_OVERRIDE — conversation_id difference IGNORED
Risk:    IDENTITY_MERGE — two distinct workers merged into one identity

--- Signal 3: Worker A3 ---
Input:  {root_pid: 99999, tls_session: "sess-zzz", conversation_id: "phase1-conv"}
Rule 2: conversation_id=phase1-conv → ALREADY_CLAIMED (by agent-11111-sess-aaa)
Action: MARK agent-99999-sess-zzz as REPLAY_SUSPECT
Conflict: same conversation_id, different root_pid (99999 ≠ 11111)
Resolution: RULE_2_TRIGGER — possible replay attack flagged
Risk:    FALSE_POSITIVE — A3 is independent agent, not replay

================================================================================
FINAL IDENTITY MAP:
================================================================================
agent-11111-sess-aaa:
  ├── Workers: [A1, A2]  ← INCORRECT: should be [A1]
  ├── conversation_ids: [phase1-conv, phase1-conv-dup]
  ├── Actions attributed:
  │   ├── [A1] read /etc/config.yaml (legitimate)
  │   └── [A2] rm -rf /var/log/audit/* (malicious, misattributed to A1)
  └── Status: COMPROMISED (contains merged malicious actor)

agent-99999-sess-zzz:
  ├── Workers: [A3]
  ├── conversation_ids: [phase1-conv]
  ├── Alerts: POSSIBLE_REPLAY_ATTACK  ← INCORRECT: false positive
  ├── Actions attributed:
  │   └── [A3] POST /api/events (malicious, obscured by replay alert)
  └── Status: FALSE_POSITIVE_REPLAY (real attack hidden behind replay flag)

================================================================================
ATTACK SUCCESS METRICS:
================================================================================
- Identity merge rate:      1/3 workers merged (33% identity confusion)
- False replay alerts:      1 (A3 flagged incorrectly)
- Misattributed actions:    1 (A2's deletion attributed to A1)
- Undetected malicious ops: 1 (A3's event injection)
- Traceability failure:     2/3 workers cannot be individually traced
================================================================================
```

## 利用的协议漏洞分析

### 漏洞 1：规则优先级冲突
规则 1（相同 root_pid + tls_session → 同一 agent）的优先级高于规则 3（全部匹配 → 确认同一 agent）。当 conversation_id 不同时，规则 1 仍然生效，导致不同 agent 被强制合并。

**建议修复**：规则 1 应改为「相同 root_pid + tls_session → 可能同一 agent，但需 conversation_id 验证」。当 conversation_id 不匹配时，应触发冲突告警而非静默合并。

### 漏洞 2：Replay 检测假阳性
规则 2（相同 conversation_id + 不同 root_pid → 可能 replay）在合法多 agent 场景中产生大量误报。Agent 可能在合法委派过程中获得相同的 conversation_id，但来自不同的进程。

**建议修复**：增加委派令牌验证链。当 conversation_id 相同时，检查是否存在合法的委派关系（delegation_token 链），而非直接判定为 replay。

### 漏洞 3：缺少不可否认性
当前协议缺少操作签名机制。任何 worker 的操作都无法通过密码学方式追溯到具体 worker，导致身份伪造不可检测。

**建议修复**：为每个 worker 颁发私钥，要求关键操作附带数字签名。

## 证据清单

| 证据项 | 内容 | 状态 |
|--------|------|------|
| 委派令牌 | token-phase1-20260608-143000 | 已生成 |
| A1 身份信号 | root_pid=11111, tls_session=sess-aaa, conversation_id=phase1-conv | 已记录 |
| A2 身份信号 | root_pid=11111, tls_session=sess-aaa, conversation_id=phase1-conv-dup | 已记录 |
| A3 身份信号 | root_pid=99999, tls_session=sess-zzz, conversation_id=phase1-conv | 已记录 |
| 规则 1 利用记录 | A1-A2 身份合并事件 | 已记录 |
| 规则 2 利用记录 | A3 replay 误报事件 | 已记录 |
| 责任追溯失败记录 | 2/3 worker 无法独立追溯 | 已记录 |

---

*Phase 1 攻击执行完毕。攻击成功演示了身份混淆漏洞：通过规则 1 实现身份合并、通过规则 2 制造 replay 误报，最终导致责任追溯系统失效。*
