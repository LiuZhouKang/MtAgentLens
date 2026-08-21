# Analyzer Dashboard 设计规范

## 概述

为所有 analyzer 输出（如 `ObservabilityMetricsAnalyzer`、`FileAccessAuditAnalyzer` 等）提供统一的前端仪表板可视化。

## 设计原则

1. **最小化**：仅包含必要的指标卡片和聚合视图
2. **通用性**：支持任何满足 `AnalyzerMetric` 契约的 analyzer 输出
3. **可组合**：可嵌入现有的 `SidePanel` 或 `DetailPanel`

## 数据流

```
Rust Analyzer (e.g. ObservabilityMetricsAnalyzer)
  ↓
Event Stream (AnalyzerMetric[])
  ↓
Frontend SSE Endpoint (/api/events)
  ↓
useAppStore (client-side state)
  ↓
AnalyzerDashboard Component
```

## 文件清单

| 文件                                   | 用途                |
| -------------------------------------- | ------------------- |
| `lib/analyzer-dashboard-contract.ts`   | TypeScript 接口定义 |
| `components/ui/analyzer-dashboard.tsx` | React 组件实现      |

## 核心接口

```typescript
// 单个指标
interface AnalyzerMetric {
  id: string;
  analyzer_name: string;
  timestamp_ns: number;
  metric_type: 'counter' | 'gauge' | 'histogram' | 'event';
  value: number | string | Record<string, unknown>;
  severity: AnomalySeverity;
  tags?: Record<string, string>;
}

// 聚合状态
interface AnalyzerAggregation {
  total_events: number;
  events_by_severity: Record<AnomalySeverity, number>;
  recent_metrics: AnalyzerMetric[];
  aggregate_value?: number | string;
}

// 组件 Props
interface AnalyzerDashboardProps {
  analyzer_id: string;
  analyzer_name: string;
  metrics: AnalyzerAggregation[];
  onSelectMetric?: (metric: AnalyzerMetric) => void;
  compact?: boolean;
}
```

## 使用示例

```tsx
import { AnalyzerDashboard } from '@/components/ui/analyzer-dashboard';
import type { AnalyzerAggregation } from '@/lib/analyzer-dashboard-contract';

export function MyDashboard() {
  const metrics: AnalyzerAggregation[] = [
    {
      total_events: 42,
      events_by_severity: { critical: 2, warning: 5, info: 35, ok: 0 },
      recent_metrics: [
        {
          id: 'm1',
          analyzer_name: 'ObservabilityMetrics',
          timestamp_ns: Date.now() * 1_000_000,
          metric_type: 'gauge',
          value: 0.75,
          severity: 'warning',
        },
      ],
      aggregate_value: 0.75,
    },
  ];

  return (
    <AnalyzerDashboard
      analyzer_id="obs-metrics-1"
      analyzer_name="Observability Metrics"
      metrics={metrics}
      compact={false}
    />
  );
}
```

## 集成步骤

1. **导入组件**

   ```tsx
   import { AnalyzerDashboard } from '@/components/ui/analyzer-dashboard';
   ```

2. **准备数据**
   - 从 `useAppStore` 获取 analyzer 输出的聚合指标
   - 确保数据遵循 `AnalyzerAggregation` 契约

3. **渲染**
   ```tsx
   <AnalyzerDashboard
     analyzer_id="your-analyzer-id"
     analyzer_name="Your Analyzer Name"
     metrics={aggregatedMetrics}
     onSelectMetric={(metric) => handleSelect(metric)}
   />
   ```

## 扩展方向

- **深层分析**：点击指标卡片时，在 `DetailPanel` 中展示时间序列或详细证据
- **导出**：支持导出指标为 CSV/JSON
- **告警**：当 `severity >= warning` 时，自动触发告警卡片
- **过滤**：按 analyzer 类型、时间范围、severity 过滤

## 设计验证

- ✅ 最小化：核心只有 3 个 React 组件（`MetricCard`、`MetricSummary`、`AnalyzerDashboard`）
- ✅ 类型安全：完整的 TypeScript 接口，无 `any` 类型
- ✅ 样式一致：使用现有的 Tailwind CSS 类名（与 `alert-card.tsx`、`evidence-panel.tsx` 一致）
- ✅ 可组合：支持 `compact` 模式用于嵌入小型面板
