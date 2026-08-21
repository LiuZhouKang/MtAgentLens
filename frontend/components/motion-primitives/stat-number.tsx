'use client';

import { DigitReel } from './digit-reel';

/**
 * StatNumber —— 统一统计数字渲染（图表区/详情工作台共用）。
 *
 * 纯数字 → DigitReel 翻牌机（顶级动效，千分位）；非纯数字（百分比 / 带符号 /
 * 小数）→ 静态渲染。确保整个页面所有统计数字动效语言一致。
 *
 * value 为 number 时：
 * - 整数 → DigitReel（千分位 + 位翻转）
 * - 小数 → 静态（DigitReel 按位翻转小数会失真，克制处理）
 */
export function StatNumber({
  value,
  className,
}: {
  value: number | string;
  className?: string;
}) {
  if (typeof value === 'number' && Number.isInteger(value)) {
    return <DigitReel value={value} className={className} />;
  }
  return <strong className={className}>{value}</strong>;
}
