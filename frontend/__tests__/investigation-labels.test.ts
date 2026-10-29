import { describe, expect, it } from 'vitest';
import {
  authorityLabel,
  claimStateLabel,
  claimTypeLabel,
  decisionLabel,
  derivationModeLabel,
  displayProfileLabel,
  eventTypeLabel,
  matchLevelLabel,
  severityLabel,
  verdictKindLabel,
} from '@/lib/investigation-labels';

describe('investigation-labels —— 告警专业中文标签层', () => {
  it('判决：Unsafe/Uncertain/Safe → 需处置/待复核/已受控', () => {
    expect(decisionLabel('Unsafe')).toMatchObject({ label: '需处置', tone: 'danger' });
    expect(decisionLabel('Uncertain')).toMatchObject({ label: '待复核', tone: 'warning' });
    expect(decisionLabel('Safe')).toMatchObject({ label: '已受控', tone: 'success' });
  });

  it('严重度：Critical/Warning/Info → 严重/警告/提示', () => {
    expect(severityLabel('Critical')).toMatchObject({ label: '严重', tone: 'danger' });
    expect(severityLabel('Warning')).toMatchObject({ label: '警告', tone: 'warning' });
    expect(severityLabel('Info')).toMatchObject({ label: '提示', tone: 'info' });
  });

  it('权威度：Authoritative → 权威 success', () => {
    expect(authorityLabel('Authoritative')).toMatchObject({ label: '权威', tone: 'success' });
    expect(authorityLabel('Unavailable')).toMatchObject({ label: '不可用' });
  });

  it('展示画像 → 中文', () => {
    expect(displayProfileLabel('SafeBaseline').label).toBe('安全基线');
    expect(displayProfileLabel('UnsafeByEngine').label).toBe('引擎判定风险');
    expect(displayProfileLabel('ObserverJudged').label).toBe('观察器判定');
  });

  it('声明类型：全部 6 类 → 中文', () => {
    expect(claimTypeLabel('PolicyViolation').label).toBe('策略违规');
    expect(claimTypeLabel('ResponsibilityAnomaly').label).toBe('责任异常');
    expect(claimTypeLabel('SuspiciousDelegation').label).toBe('可疑委派');
    expect(claimTypeLabel('TaintedContextUse').label).toBe('污染上下文使用');
    expect(claimTypeLabel('UnauthorizedCapabilityUse').label).toBe('越权能力使用');
    expect(claimTypeLabel('BlockedAttempt').label).toBe('已阻断尝试');
  });

  it('声明状态 → 中文 + tone', () => {
    expect(claimStateLabel('Inferred')).toMatchObject({ label: '推断', tone: 'info' });
    expect(claimStateLabel('Confirmed')).toMatchObject({ label: '已确认', tone: 'success' });
    expect(claimStateLabel('Ambiguous')).toMatchObject({ label: '歧义', tone: 'warning' });
  });

  it('证据匹配层级：L1-L5 → 中文 + tone', () => {
    expect(matchLevelLabel('L1Exact')).toMatchObject({ label: '精确匹配', tone: 'success' });
    expect(matchLevelLabel('L2Normalized').label).toBe('归一化匹配');
    expect(matchLevelLabel('L3Containment').label).toBe('包含匹配');
    expect(matchLevelLabel('L4Basename').label).toBe('基名匹配');
    expect(matchLevelLabel('L5Substring').label).toBe('子串匹配');
  });

  it('推导方式 → 中文（未知回退原名）', () => {
    expect(derivationModeLabel('Direct').label).toBe('直接推导');
    expect(derivationModeLabel('demo').label).toBe('演示数据');
    expect(derivationModeLabel('MysteryMode').label).toBe('MysteryMode');
  });

  it('引擎判定种类 → 中文', () => {
    expect(verdictKindLabel('rule').label).toBe('规则判定');
    expect(verdictKindLabel('pattern').label).toBe('模式判定');
  });

  it('时间线事件：snake_case 与 UPPER_SNAKE 均命中；未知回退原名', () => {
    expect(eventTypeLabel('agent_start').label).toBe('主体启动');
    expect(eventTypeLabel('delegation').label).toBe('委派');
    expect(eventTypeLabel('file_read').label).toBe('文件读取');
    expect(eventTypeLabel('boundary_cross').label).toBe('边界穿越');
    expect(eventTypeLabel('EXEC').label).toBe('进程执行');
    expect(eventTypeLabel('FILE_OPEN').label).toBe('文件打开');
    expect(eventTypeLabel('NETWORK_CONNECT').label).toBe('网络连接');
    expect(eventTypeLabel('INJECTION_DETECTED').label).toBe('注入检测');
    // 大小写不敏感兜底
    expect(eventTypeLabel('file_write').label).toBe('文件写入');
    // 未知回退原名（诚实）
    expect(eventTypeLabel('something_unknown').label).toBe('something_unknown');
  });
});
