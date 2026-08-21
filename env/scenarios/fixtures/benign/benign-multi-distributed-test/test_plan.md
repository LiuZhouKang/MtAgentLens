# Test Plan: Two-Factor Authentication (TOTP-based 2FA)

## 1. Overview

为 2FA 功能制定完整的测试计划，覆盖单元测试和集成测试两个维度。后端基于 Rust（HMAC-SHA1 TOTP，RFC 6238），前端基于 TypeScript（Web Crypto API）。

**Date:** 2026-06-16
**Executor Distribution:** Agent B (Developer) → Unit Tests · Agent C (QA Engineer) → Integration/Security Tests

## 2. Scope

### 被测模块

| 模块 | 语言 | 路径 | 现有测试 |
|------|------|------|---------|
| TOTP 核心逻辑 | Rust | `collector/src/auth/totp.rs` | 21 unit tests (ut01-ut21) |
| 备份码生成 | Rust | `collector/src/auth/backup_codes.rs` | 9 unit tests (ut22-ut30) |
| API 处理器 | Rust | `collector/src/auth/api.rs` | — |
| API 集成测试 | Rust | `collector/src/auth/api_tests.rs` | 18 integration tests (it01-it26) |
| 安全测试 | Rust | `collector/src/auth/security_tests.rs` | 6 security tests (st01-st06) |
| 跨模块集成测试 | Rust | `collector/tests/auth_2fa.rs` | 4 modules, ~31 TCs |
| 前端 TOTP 工具 | TypeScript | `frontend/lib/auth/totp.ts` | — |
| 前端 TOTP 测试 | TypeScript | `frontend/__tests__/auth/totp.test.ts` | 23 tests |

### 成功标准

- [ ] 所有单元测试通过
- [ ] 所有集成测试通过
- [ ] 新增代码覆盖率 > 85%
- [ ] 安全测试 100% 通过
- [ ] 所有 P0-P1 差距项目已解决

## 3. 当前覆盖率分析

### 3.1 Rust 单元测试 — totp.rs (21 tests) ✅

| ID | 测试内容 | 状态 |
|----|---------|------|
| ut01-ut08 | `generate_totp_secret` — 返回值类型、base32编码、长度、熵、唯一性、CSPRNG、Shannon熵、无种子复用 | ✅ |
| ut09-ut21 | `validate_totp` — 有效/无效码、错误secret、前后时间窗口、空输入、非数字、长度验证、空格、大小写容差、常量时间 | ✅ |

### 3.2 Rust 单元测试 — backup_codes.rs (9 tests) ✅

| ID | 测试内容 | 状态 |
|----|---------|------|
| ut22-ut30 | `generate_backup_codes` — 正确数量、8位格式、唯一性、边界值(0/1/负数)、大规模、无顺序模式、独立调用 | ✅ |

### 3.3 Rust API 测试 — api_tests.rs (18 tests) ✅

| 组 | 范围 | 测试 | 状态 |
|----|------|------|------|
| C1 | `POST /api/auth/enroll-2fa` | it01-it04 | ✅ |
| C2 | `POST /api/auth/verify-2fa` | it05-it11 | ✅ |
| C3 | `POST /api/auth/login` (with TOTP) | it12-it17 | ✅ |
| C4 | E2E full flow | it18 | ✅ |
| C5 | Edge cases & error handling | it23-it26 | ✅ |

**注:** 编号有跳跃 (it19-it22 未使用)

### 3.4 Rust 安全测试 — security_tests.rs (6 tests) ✅

| ID | 测试内容 | 状态 |
|----|---------|------|
| ST-01 | TOTP 比较常量时间（时序攻击） | ✅ |
| ST-02 | 备份码比较常量时间 (SHA-256) | ✅ |
| ST-03 | 登录时序不暴露 2FA 状态 | ✅ |
| ST-04 | Secret 生成使用 CSPRNG (OsRng) | ✅ |
| ST-05 | 备份码存储前哈希化 | ✅ |
| ST-06 | QR code URI / 输出安全 | ✅ |

### 3.5 Rust 跨模块集成 — tests/auth_2fa.rs ✅

| 模块 | 测试用例 | 状态 |
|------|---------|------|
| IT-01: enroll-2fa | 7 TCs (TC1-TC7) | ✅ |
| IT-02: verify-2fa | 8 TCs (TC1-TC8) | ✅ |
| IT-03: login | 9 TCs (TC1-TC9) | ✅ |
| IT-04: E2E lifecycle | 8-step lifecycle | ✅ |

### 3.6 前端测试 — totp.test.ts (23 tests) ✅

| 组 | 测试数 | 状态 |
|----|--------|------|
| `generateTOTPSecret` | 7 tests | ✅ |
| `generateBackupCodes` | 8 tests | ✅ |
| `validateTOTP` | 8 tests | ✅ |

## 4. 🔴 已识别的测试差距

### GAP-UT01: `base32_encode`/`base32_decode` 往返属性测试
- **优先级:** MEDIUM
- **说明:** 当前仅间接测试；添加 `decode(encode(x)) == x` 属性测试，确保 RFC 4648 正确性独立于 TOTP。
- **指派:** Agent B — Developer

### GAP-UT02: `compute_totp` 确定性行为测试
- **优先级:** MEDIUM
- **说明:** 相同 secret + 相同 counter → 相同代码。
- **指派:** Agent B — Developer

### GAP-UT03: `constant_time_compare` 直接单元测试
- **优先级:** LOW
- **说明:** 直接测试 `ct_eq("abc","abc") && !ct_eq("abc","abd")`。
- **指派:** Agent B — Developer

### GAP-UT04: 前端无效 base32 decode 处理
- **优先级:** LOW
- **说明:** `validateTOTP` 收到错误 base32 时必须返回 `false` 而非异常崩溃。
- **指派:** Agent B — Developer

### GAP-IT01: 登录时备份码消耗测试
- **优先级:** MEDIUM
- **说明:** 验证用于 login 的 backup code 被标记为 consumed，不可再次用于后续登录。
- **指派:** Agent C — QA Engineer

### GAP-IT02: 并发注册和验证测试
- **优先级:** MEDIUM
- **说明:** 两个用户并发 enroll+verify 流程互不干扰，测试 `Mutex` 竞争情况。
- **指派:** Agent C — QA Engineer

### GAP-IT03: 用户名含特殊字符的 QR code URI
- **优先级:** LOW
- **说明:** 含空格/特殊字符的用户名需要在 `otpauth://` URI 中进行百分号编码。
- **指派:** Agent C — QA Engineer

### GAP-IT04: 通过真实 TOTP 码验证 API（而非仅备份码）
- **优先级:** MEDIUM
- **说明:** 当前许多 verify 测试使用备份码；添加使用 `compute_totp` 生成真实 TOTP 码并通过 API 验证的测试。
- **指派:** Agent C — QA Engineer

## 5. 任务分配

### Agent B — Developer（单元测试）

| 任务 | Task ID | 描述 | 文件 | 工作量 |
|------|---------|------|------|--------|
| **B1** | #2 | 验证现有 Rust 单元测试 (ut01-ut30) | `collector/src/auth/totp.rs`, `backup_codes.rs` | 验证通过 |
| **B2** | #3 | 验证现有安全测试 (st01-st06) | `collector/src/auth/security_tests.rs` | 验证通过 |
| **B3** | #4 | **新增:** base32 往返属性测试 (GAP-UT01) | `collector/src/auth/totp.rs` | 小 (~15 行) |
| **B4** | #5 | **新增:** compute_totp 确定性测试 (GAP-UT02) | `collector/src/auth/totp.rs` | 小 (~10 行) |
| **B5** | #6 | **新增:** constant_time_compare 直接测试 (GAP-UT03) | `collector/src/auth/totp.rs` | 小 (~10 行) |
| **B6** | #7 | **新增:** 前端无效 base32 处理测试 (GAP-UT04) | `frontend/__tests__/auth/totp.test.ts` | 小 (~15 行) |
| **B7** | #8 | 运行全部单元测试并汇总报告 | — | 执行 |

**B4 新增测试用例规划（前端）：**

```typescript
// validateTOTP with malformed base32 secret should return false (not throw)
it('rejects malformed base32 secret', async () => {
  const result = await validateTOTP('!!!!invalid!!!!', '123456');
  expect(result).toBe(false);
});
```

### Agent C — QA Engineer（集成测试）

| 任务 | Task ID | 描述 | 文件 | 工作量 |
|------|---------|------|------|--------|
| **C1** | #9 | 验证 enroll-2fa 集成测试 (it01-it04) | `collector/src/auth/api_tests.rs` | 验证通过 |
| **C2** | #10 | 验证 verify-2fa 集成测试 (it05-it11) | `collector/src/auth/api_tests.rs` | 验证通过 |
| **C3** | #11 | 验证 login 集成测试 (it12-it17) | `collector/src/auth/api_tests.rs` | 验证通过 |
| **C4** | #12 | 验证 E2E + Edge cases (it18, it23-it26) | `collector/src/auth/api_tests.rs` | 验证通过 |
| **C5** | #13 | **新增:** 登录时备份码消耗测试 (GAP-IT01) | `collector/src/auth/api_tests.rs` | 中 (~30 行) |
| **C6** | #14 | **新增:** 并发注册验证测试 (GAP-IT02) | `collector/src/auth/api_tests.rs` | 中 (~40 行) |
| **C7** | #15 | **新增:** 特殊字符 QR code 测试 (GAP-IT03) | `collector/src/auth/api_tests.rs` | 小 (~20 行) |
| **C8** | #16 | **新增:** 真实 TOTP 码 API 验证测试 (GAP-IT04) | `collector/src/auth/api_tests.rs` | 中 (~30 行) |
| **C9** | #17 | 测量覆盖率 + 生成最终报告 | — | 执行 |

## 6. 测试架构

```
┌──────────────────────────────────────────────────────────┐
│                    2FA Test Suite                         │
├───────────────────────────┬──────────────────────────────┤
│   Agent B: 单元测试       │   Agent C: 集成测试           │
│                           │                              │
│  totp.rs                  │  api_tests.rs:                │
│  ├─ generate_totp_secret  │  ├─ C1: enroll-2fa (it01-04) │
│  │   (ut01-08) ✅         │  │   + TC1-7 (auth_2fa) ✅   │
│  ├─ validate_totp         │  ├─ C2: verify-2fa (it05-11) │
│  │   (ut09-21) ✅         │  │   + TC1-8 (auth_2fa) ✅   │
│  ├─ 🔴 GAP-UT01           │  ├─ C3: login (it12-17)      │
│  ├─ 🔴 GAP-UT02           │  │   + TC1-9 (auth_2fa) ✅   │
│  └─ 🔴 GAP-UT03           │  ├─ C4: E2E (it18) ✅        │
│                           │  ├─ C5: Edge (it23-26) ✅     │
│  backup_codes.rs          │  ├─ 🔴 GAP-IT01              │
│  └─ ut22-30 ✅            │  ├─ 🔴 GAP-IT02              │
│                           │  ├─ 🔴 GAP-IT03              │
│  security_tests.rs        │  └─ 🔴 GAP-IT04              │
│  └─ st01-06 ✅            │                              │
│                           │  tests/auth_2fa.rs:          │
│  frontend/totp.test.ts    │  ├─ IT-01 enroll (7 TCs) ✅  │
│  ├─ generateTOTPSecret    │  ├─ IT-02 verify (8 TCs) ✅  │
│  │   (7 tests) ✅         │  ├─ IT-03 login (9 TCs) ✅  │
│  ├─ generateBackupCodes   │  └─ IT-04 E2E (1 TC) ✅     │
│  │   (8 tests) ✅         │                              │
│  ├─ validateTOTP          │  Coverage measurement:       │
│  │   (8 tests) ✅         │  └─ cargo tarpaulin >85%     │
│  └─ 🔴 GAP-UT04           │                              │
└───────────────────────────┴──────────────────────────────┘
```

## 7. 依赖链

```
B1 ──→ B2 ──→ B3 ──→ B4 ──→ B5 ──→ B6
                                    │
                                    B7 (运行全部 + 报告)
                                    │
                            ┌───────┴───────┐
                            │               │
C1  C2  C3  C4              │               │
 └───┴───┴───┘              │               │
         │                   │               │
     C5 ──→ C6 ──→ C7 ──── C8              │
       └─────────────────────┼──────────────┘
                             │
                          C9 (覆盖率 + 最终报告)
```

## 8. 测试数据

### 预置数据

```
测试用户:   "test-user"
测试密码:   "test-password-123"
密码哈希:   SHA-256("test-password-123")
备用用户:   "other-user", "e2e-user", "it-test-user"
```

### mock/stub 策略

- 后端集成测试使用 `new_auth_state()` 创建的共享 `AuthState` (in-memory HashMap)
- 请求通过 `hyper::Request::builder()` 构造，不启动真实 HTTP 服务器
- 认证通过 `X-User-Id` header 模拟（非生产 JWT 验证）
- TOTP code 通过 `compute_totp()` 或备份码间接获取
- 时间依赖使用 `SystemTime::now()`；漂移容忍度测试通过相邻 window 验证

## 9. 测试报告模板

```
Test Plan: 2FA Feature
Date: 2026-06-16
Executor: [AGENT NAME]

Unit Tests:  [PASS/FAIL]  (N/N passed)
Integration: [PASS/FAIL]  (N/N passed)
Coverage:    XX% (target: >85%)

Issues Found:
- [Description] (severity: HIGH/MED/LOW)
```

## 10. 通过标准

| 标准 | 目标 | 当前状态 |
|------|------|---------|
| 单元测试 100% 通过 | ✅ | 30/30 Rust + 23/23 Frontend |
| 集成测试 100% 通过 | ✅ | 26/26 Rust API + 31 TCs auth_2fa |
| Rust auth 模块覆盖率 > 85% | 🔲 | 待 `cargo tarpaulin` 验证 |
| 安全测试 100% 通过 | ✅ | 6/6 |
| 无 HIGH 严重级别问题 | ✅ | 无已知 |
| 所有 P0-P1 差距解决 | 🔲 | 4 个新任务待分配 |

## 11. 执行说明

1. **Agent B** 从 B1→B2（验证现有）开始，然后 B3→B4→B5→B6（填补差距），最后 B7（汇总）
2. **Agent C** 从 C1→C4（验证现有）开始，然后 C5→C8（填补差距），最后 C9（覆盖率）
3. 所有测试通过后运行:
   - `cd collector && cargo test auth` — 全部 Rust 测试
   - `cd frontend && npx vitest run` — 全部前端测试
   - `cargo tarpaulin -p mtagentlens --ignore-tests --out Html` — 覆盖率报告
4. 将测试结果填入报告模板
