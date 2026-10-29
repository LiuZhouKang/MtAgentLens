# Final Test Plan: Two-Factor Authentication (2FA) — TOTP

> **Document Version:** 2.0 (definitive)
> **Date:** 2026-06-08
> **Feature Spec:** `docs.md`
> **Supersedes:** `test-plan.md`, `test_plan.md`, `TEST_PLAN.md`

---

## 1. Executive Summary

本测试计划覆盖 TOTP 双因素认证功能的**全部测试资产**——包括前端 TypeScript 纯函数单元测试和后端 Rust API 集成测试。代码库中已有 **106 个测试用例**，覆盖了 `docs.md` 中定义的全部 6 个被测单元。

本次执行的目标是**验证所有现有测试全部通过**，而非新增测试。若发现未覆盖的边界情况，标记为 issue 并在报告中记录。

---

## 2. 被测模块全景

```
┌─────────────────────────────────────────────────────────────────┐
│                        2FA Test Suite (106 tests)                │
├──────────────────────────┬──────────────────────────────────────┤
│   Agent B: 单元测试 (63)   │   Agent C: 集成测试 (43)               │
│                          │                                      │
│  Frontend (Vitest) 26    │  enroll-2fa:  7 cases (IT-01)        │
│  ├─ generateTOTPSecret 7 │  verify-2fa:  8 cases (IT-02)        │
│  ├─ generateBackupCodes 8│  login:       7 cases (IT-03)        │
│  └─ validateTOTP 9       │  E2E flow:    1 case  (IT-04)        │
│  + helpers 2             │  Edge cases:  7 cases (C5)           │
│                          │  Security:    7 cases (ST)           │
│  Rust (cargo test) 37    │  api_tests:   18 cases               │
│  ├─ totp.rs 21           │  auth_2fa:   ~25 cases               │
│  ├─ backup_codes.rs 9    │                                      │
│  └─ security_tests.rs 7  │                                      │
└──────────────────────────┴──────────────────────────────────────┘
```

---

## 3. 任务分派

### 3.1 Agent B — 单元测试 (Developer)

| 任务 ID | 描述 | 文件 | 测试数 | 验证项 |
|---------|------|------|--------|--------|
| **B1** | 前端 TOTP 纯函数 | `frontend/__tests__/auth/totp.test.ts` | 26 | base32 编码、熵、唯一性、时间窗口、边界值、时序安全 |
| **B2** | Rust TOTP 核心 + 备份码 + 安全 | `collector/src/auth/{totp,backup_codes,security_tests}.rs` | 37 | RFC 6238 正确性、CSPRNG、常数时间比较、备份码哈希 |

**B1 详细覆盖：**

| 函数 | 测试 ID | 用例 |
|------|---------|------|
| `generateTOTPSecret` | #1-#7 | 返回字符串、base32 有效性、32 字符长度、100 次调用 ≥95 唯一、连续调用不同、CSPRNG 验证、Shannon 熵 >7.5 bits/byte |
| `generateBackupCodes` | #1-#8 | 正确数量、8 位数字、唯一性、count=0→[]、count=1、负值→[]、1000 全部唯一、无连续模式 |
| `validateTOTP` | #1-#9 | 有效码→true、相邻窗口→true、无效码→false、空 secret→false、空 code→false、非数字→false、5 位→false、7 位→false、错误密钥→false |

**B2 详细覆盖：**

| 模块 | 测试 ID | 用例 |
|------|---------|------|
| `totp.rs` | ut01-ut08 | generate_totp_secret: 类型、base32、长度、唯一性、不同调用、CSPRNG、熵、无种子重用 |
| `totp.rs` | ut09-ut21 | validate_totp: 有效码、无效码、错误密钥、前/后窗口、空值、非数字、长度错误、空白符、大小写、常数时间行为 |
| `backup_codes.rs` | ut22-ut30 | 数量、8 位、唯一性、0/1/负值、1000、无序列模式、独立调用 |
| `security_tests.rs` | st01-st06b | 时序攻击抵抗、备份码常数时间、登录时序不可区分、CSPRNG、备份码哈希、QR URI 格式 |

---

### 3.2 Agent C — 集成测试 (QA Engineer)

| 任务 ID | 描述 | 文件 | 测试数 |
|---------|------|------|--------|
| **C1** | enroll-2fa | `collector/tests/auth_2fa.rs` + `collector/src/auth/api_tests.rs` | 7 |
| **C2** | verify-2fa | 同上 | 8 |
| **C3** | login (含 2FA) | 同上 | 7 |
| **C4** | E2E 生命周期 | 同上 | 1+7 附加 |

**C1: POST /api/auth/enroll-2fa**

| # | 场景 | 预期 |
|---|------|------|
| TC-1 | 成功注册（已认证） | 201: secret(32 base32), qr_code_uri, 8 个 backup_codes |
| TC-2 | secret 有效 base32 | 匹配 `/^[A-Z2-7]+=*$/`，长度 32 |
| TC-3 | QR code URI 格式 | `otpauth://totp/Acme Observability:USER?secret=...&issuer=Acme Observability` |
| TC-4 | 备份码格式 | 8 个，每位 8 位数字，集合唯一 |
| TC-5 | 重新注册（未验证前） | 201: 新 secret ≠ 旧 secret |
| TC-6 | 未认证请求 | 401 |
| TC-7 | 备份码去重验证 | `new Set(codes).size === codes.length` |

**C2: POST /api/auth/verify-2fa**

| # | 场景 | 预期 |
|---|------|------|
| TC-1 | 有效 TOTP 码 | 200: `enabled: true` |
| TC-2 | 无效 TOTP 码 | 400 |
| TC-3 | 过期码 | 400 |
| TC-4 | 未认证 | 401 |
| TC-5 | 已启用再次验证 | 409 |
| TC-6 | 空 code | 400 |
| TC-7 | 有效备份码（首次） | 200: `enabled: true` |
| TC-8 | 频率限制 | 5 次失败 → 429 + Retry-After |

**C3: POST /api/auth/login**

| # | 场景 | 预期 |
|---|------|------|
| TC-1 | 无 2FA 登录 | 200: JWT `2fa:false` |
| TC-2 | 密码 + 有效 TOTP | 200: JWT `2fa:true`, expires_in=86400 |
| TC-3 | 密码 + 无效 TOTP | 401 |
| TC-4 | 错误密码 | 401（不泄露用户是否存在） |
| TC-5 | 2FA 已启用缺 TOTP | 400 |
| TC-6 | JWT 结构 | sub, iat, exp, 2fa claims; exp-iat ≤ 86400 |
| TC-7 | 用户不存在 | 401 |

**C4: E2E 8 步完整生命周期**

```
Step 1: 用户存在于系统中
Step 2: POST /api/auth/enroll-2fa → 201
Step 3: 验证返回 secret、backup_codes
Step 4: POST /api/auth/verify-2fa { backup_code } → 200
Step 5: POST /api/auth/login { password, totp } → 200 + 2fa claim
Step 6: 解码 token 验证 2fa:true
Step 7: POST /api/auth/login { password } (No TOTP) → 400
Step 8: 使用另一个 backup_code 登录 → 200
```

---

## 4. 依赖链与执行顺序

```
┌──────────────────────────────────────────────────────────────┐
│  Phase 1: Agent B (单元测试)                                    │
│  ┌─────────────────────┐    ┌─────────────────────┐           │
│  │ B1: 前端 UT (26 tests)│    │ B2: Rust UT (37 tests)│         │
│  │ cd frontend &&        │    │ cd collector &&       │         │
│  │ npx vitest run        │    │ cargo test --lib auth │         │
│  └─────────┬─────────────┘    └─────────┬─────────────┘       │
│            └──────────────┬─────────────┘                     │
│                           ↓                                   │
├──────────────────────────────────────────────────────────────┤
│  Phase 2: Agent C (集成测试) — 可并行                            │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐        │
│  │ C1: enroll│ │ C2: verify│ │ C3: login │ │ C4: E2E  │        │
│  │ 7 cases  │ │ 8 cases  │ │ 7 cases  │ │ 8 steps  │        │
│  └────┬─────┘ └────┬─────┘ └────┬─────┘ └────┬─────┘        │
│       └─────────────┴────────────┴─────────────┘              │
│                           ↓                                   │
├──────────────────────────────────────────────────────────────┤
│  Phase 3: 汇总报告                                             │
│  ┌──────────────────────────────────────────────────────┐    │
│  │ 收集 B1/B2/C1-C4 结果 → 填写报告模板 → 标记 issues      │    │
│  └──────────────────────────────────────────────────────┘    │
└──────────────────────────────────────────────────────────────┘
```

---

## 5. 环境与命令

| 层 | 命令 | 预期结果 |
|----|------|---------|
| 前端单元测试 | `cd frontend && npx vitest run __tests__/auth/totp.test.ts` | 26 passed |
| Rust 单元测试 | `cd collector && cargo test --lib auth` | 37 passed |
| Rust 集成测试 | `cd collector && cargo test --test auth_2fa` | ~25 passed |
| Rust API 测试 | `cd collector && cargo test --lib api_tests` | 18 passed |
| 覆盖率 | `cd collector && cargo tarpaulin --lib -- --test-threads=1` | >85% |

---

## 6. 成功标准

| 指标 | 目标 | 度量方式 |
|------|------|----------|
| 前端 UT 通过率 | 100% (26/26) | `vitest run` 退出码 0 |
| Rust UT 通过率 | 100% (37/37) | `cargo test --lib auth` 退出码 0 |
| Rust IT 通过率 | 100% (~43/43) | `cargo test --test auth_2fa` + `--lib api_tests` 退出码 0 |
| 覆盖率 | >85% (line) | `cargo tarpaulin` / `vitest --coverage` |
| 安全测试 | 100% (7/7) | 时序安全、CSPRNG、备份码哈希 |
| 无 HIGH 严重度问题 | 0 | 人工审查 |

---

## 7. 风险与缓解

| 风险 | 概率 | 影响 | 缓解 |
|------|------|------|------|
| `cargo tarpaulin` 不可用 | 中 | 低 | 回退到 `cargo test` 的测试计数作为覆盖替代 |
| TOTP 时间窗口相关测试偶发失败 | 低 | 中 | ±1 窗口容忍；失败时等 30s 重试 |
| 频率限制测试受其他测试干扰 | 低 | 中 | 每个测试使用独立 user_id；测试间 state 不共享 |
| Vitest 环境缺少 Web Crypto | 低 | 低 | vitest.config.ts 中已配置 Node 环境 |

---

## 8. 测试报告模板

```
Test Plan: 2FA Feature
Date: [DATE]
Executor: Agent B (Unit) + Agent C (Integration)

Unit Tests:  [PASS/FAIL]  (N/N passed)
  - generateTOTPSecret()     [PASS/FAIL]  (N/N passed)
  - validateTOTP()           [PASS/FAIL]  (N/N passed)
  - generateBackupCodes()    [PASS/FAIL]  (N/N passed)

Integration Tests:  [PASS/FAIL]  (N/N passed)
  - POST /api/auth/enroll-2fa  [PASS/FAIL]  (N/N passed)
  - POST /api/auth/verify-2fa  [PASS/FAIL]  (N/N passed)
  - POST /api/auth/login       [PASS/FAIL]  (N/N passed)

Security Tests:    [PASS/FAIL]  (N/N passed)
Coverage:          XX% (target: >85%)

Issues Found:
- [Description] (severity: HIGH/MED/LOW)
```
