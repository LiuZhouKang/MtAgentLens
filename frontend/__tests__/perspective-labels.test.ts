import { describe, expect, it } from 'vitest';
import {
  reasonLabel,
  actionTypeLabel,
  signalClassLabel,
  boundaryLabel,
  toolLabel,
} from '@/lib/monitoring/perspective-labels';

describe('perspective-labels —— 专业中文标签映射', () => {
  it('诊断原因：实际出现的枚举 → 中文标签', () => {
    expect(reasonLabel('NoArgumentMatch').label).toBe('参数不匹配');
    expect(reasonLabel('BelowConfidenceCutoff').label).toBe('置信度不足');
    expect(reasonLabel('IntentNotPrimaryEligible').label).toBe('非主意图');
  });

  it('诊断原因：完整枚举覆盖（不出现原始英文）', () => {
    for (const raw of [
      'CandidateNotAvailable',
      'OutsideTimeWindow',
      'AmbiguousCompetition',
      'ObserverPending',
    ]) {
      expect(reasonLabel(raw).label).not.toBe(raw);
      expect(reasonLabel(raw).label.length).toBeGreaterThan(0);
    }
  });

  it('动作类型 → 中文', () => {
    expect(actionTypeLabel('FileRead').label).toBe('文件读取');
    expect(actionTypeLabel('FileOverwrite').label).toBe('文件覆写');
    expect(actionTypeLabel('FileRename').label).toBe('文件重命名');
    expect(actionTypeLabel('ProcessExec').label).toBe('进程执行');
  });

  it('信号类 → 中文', () => {
    expect(signalClassLabel('HighSignal').label).toBe('高信号');
  });

  it('边界 → 中文', () => {
    expect(boundaryLabel('Capability').label).toBe('能力边界');
  });

  it('工具名 → 中文', () => {
    expect(toolLabel('Bash').label).toBe('Shell 命令');
    expect(toolLabel('Agent').label).toBe('子 Agent');
  });

  it('未知值 → 诚实回退原名（不伪造）', () => {
    expect(reasonLabel('SomeUnknownReason').label).toBe('SomeUnknownReason');
    expect(reasonLabel('SomeUnknownReason').desc).toBeUndefined();
  });
});
