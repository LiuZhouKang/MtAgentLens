# Test Plan: Two-Factor Authentication (2FA) Feature

> **Document Version:** 1.0
> **Date:** 2026-06-06
> **Feature Spec:** `docs.md`

---

## 1. 测试目标

验证 TOTP 双因素认证功能在以下维度符合规格：

| 维度 | 目标 |
|------|------|
| **功能正确性** | TOTP 密钥生成、验证、备份码生成均按 RFC 6238 规范正确实现 |
| **API 契约** | 三个 REST 端点的请求/响应格式、状态码、错误处理符合预期 |
| **安全性** | 密钥不可预测、验证抗重放、未授权访问被拒绝 |
| **异常路径** | 无效输入、过期码、重复操作等边界情况被妥善处理 |

---

## 2. 测试范围

### 2.1 范围内（In Scope）

| 模块 | 类型 | 测试项 |
|------|------|--------|
| `generateTOTPSecret()` | 单元测试 | 输出编码、长度、唯一性、随机性 |
| `validateTOTP(secret, code)` | 单元测试 | 有效码、无效码、过期码、空值、时间偏移 |
| `generateBackupCodes(count)` | 单元测试 | 数量、格式、唯一性、边界值 |
| `POST /api/auth/enroll-2fa` | 集成测试 | 正常启用、重复启用、未认证、格式错误 |
| `POST /api/auth/verify-2fa` | 集成测试 | 正确码验证、错误/过期码、备份码验证 |
| `POST /api/auth/login` | 集成测试 | 密码+TOTP 流程、JWT 声明、失败场景 |

### 2.2 范围外（Out of Scope）

- UI 层测试（前端 2FA 表单交互）
- 端到端测试（E2E browser test）
- 性能/负载测试
- 跨设备 TOTP 时钟偏差的校正策略

---

## 3. 测试策略

### 3.1 单元测试（Agent B）

**定位：** 纯函数，不依赖外部状态或 IO。

| 测试函数 | 测试手法 | 工具 |
|----------|----------|------|
| `generateTOTPSecret()` | 固定种子与随机种子双模式验证 | Jest / Vitest |
| `validateTOTP(secret, code)` | 参数化测试覆盖时间窗口、边界值、无效输入 | Jest / Vitest |
| `generateBackupCodes(count)` | 属性基测试（property-based testing） | Jest / fast-check |

**关键测试用例**（详见各子任务）：
- 每个函数测试 5-7 个正向/负向用例
- `validateTOTP` 需 mock 时间戳以验证 ±1 步长容忍度
- `generateBackupCodes` 验证 `count=0`、`count=1`、`count=100` 等边界

### 3.2 集成测试（Agent C）

**定位：** HTTP API 测试，验证请求-响应契约。

| 端点 | 依赖条件 | 工具 |
|------|----------|------|
| `POST /api/auth/enroll-2fa` | JWT 认证、用户已登录 | Supertest + Jest |
| `POST /api/auth/verify-2fa` | JWT 认证、enroll 已完成 | Supertest + Jest |
| `POST /api/auth/login` | 数据库有测试用户 | Supertest + Jest |

**测试环境准备：**
- 使用内存数据库或测试容器（避免污染生产数据）
- 预置一个已启用 2FA 的用户和一个未启用的用户
- TOTP 码的生成需通过种子固定以确保可预测性

**关键测试用例**：
- 每个端点 5-8 个测试场景
- `login` 端点需组合：`(2FA状态) × (密码正确性) × (TOTP正确性)`

---

## 4. 任务分派

| ID | 角色 | 任务 | 前置依赖 | 预估工时 |
|----|------|------|----------|----------|
| #1 | — | 测试计划编写 | — | 0.5h |
| #2 | Agent B | `generateTOTPSecret()` 单元测试 | #1 | 1h |
| #3 | Agent B | `validateTOTP()` 单元测试 | #1 | 1.5h |
| #4 | Agent B | `generateBackupCodes()` 单元测试 | #1 | 1h |
| #5 | Agent C | `POST /api/auth/enroll-2fa` 集成测试 | #2, #3, #4 | 1.5h |
| #6 | Agent C | `POST /api/auth/verify-2fa` 集成测试 | #2, #3, #4 | 1.5h |
| #7 | Agent C | `POST /api/auth/login` 集成测试（含 2FA） | #2, #3, #4 | 2h |

**执行顺序约束：**
```
#1 测试计划
  ├── #2 generateTOTPSecret UT ─┐
  ├── #3 validateTOTP UT ───────┤
  └── #4 generateBackupCodes UT ─┤
                                  ├── #5 enroll-2fa IT
                                  ├── #6 verify-2fa IT
                                  └── #7 login 2FA IT
```

---

## 5. 环境要求

| 资源 | 要求 |
|------|------|
| 运行环境 | Node.js 18+ / Bun 1.x |
| 测试框架 | Jest 29+ 或 Vitest |
| HTTP 测试 | Supertest（集成测试） |
| 时间模拟 | `jest.useFakeTimers()` + `jest.setSystemTime()` |
| 密码库 | `otplib` 或 `speakeasy`（用于参考实现对比） |
| 数据库 | SQLite 内存模式或测试容器 PostgreSQL |

---

## 6. 成功标准

| 指标 | 目标值 | 度量方式 |
|------|--------|----------|
| **单元测试通过率** | 100%（所有用例 pass） | `jest --coverage` |
| **集成测试通过率** | 100%（所有用例 pass） | `jest --config jest.integration.js` |
| **代码覆盖率（新增）** | > 85% line coverage | `--coverage` 报告 |
| **无未被捕获的异常** | 所有异常路径均返回结构化错误 | 人工审查 |

---

## 7. 测试报告模板

```
Test Plan: 2FA Feature
Date: [DATE]
Executor: [AGENT NAME]

Unit Tests:  [PASS/FAIL]  (N/N passed)
  - generateTOTPSecret()     [PASS/FAIL]  (N/N passed)
  - validateTOTP()           [PASS/FAIL]  (N/N passed)
  - generateBackupCodes()    [PASS/FAIL]  (N/N passed)

Integration Tests:  [PASS/FAIL]  (N/N passed)
  - POST /api/auth/enroll-2fa  [PASS/FAIL]  (N/N passed)
  - POST /api/auth/verify-2fa  [PASS/FAIL]  (N/N passed)
  - POST /api/auth/login       [PASS/FAIL]  (N/N passed)

Coverage:  XX% (target: >85%)

Issues Found:
- [Description] (severity: HIGH/MED/LOW)
```

---

## 8. 风险与缓解

| 风险 | 概率 | 影响 | 缓解措施 |
|------|------|------|----------|
| 时间模拟不准确导致 TOTP 验证测试假阳性 | 中 | 中 | 使用固定种子和已知 TOTP 码表验证 |
| 数据库清理不干净导致集成测试间污染 | 低 | 高 | 每个测试套件前重建数据库 |
| TOTP 库版本差异导致算法不一致 | 低 | 高 | 锁定 `otplib` 版本，使用 RFC 参考向量验证 |
