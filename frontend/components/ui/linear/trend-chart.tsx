'use client';

import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';

type ChartType = 'area' | 'bar' | 'donut';

type TrendChartProps = {
  type: ChartType;
  data: Array<Record<string, number | string>>;
  xKey?: string;
  seriesKey?: string;
  colors?: string[];
  height?: number;
};

function ChartTooltip() {
  return function InnerTooltip(props: any) {
    const { active, payload, label } = props;
    if (!active || !payload || !payload.length) return null;
    return (
      <div className="ln-chart-tooltip">
        <div className="recharts-tooltip-label">{label}</div>
        {payload.map((entry: any, i: number) => (
          <div key={i} style={{ color: entry.color || entry.payload?.fill }}>
            <span>{entry.name}: </span>
            <strong>{entry.value}</strong>
          </div>
        ))}
      </div>
    );
  };
}

const AXIS_TICK = { fill: '#5c5c5f', fontSize: 11 };
const GRID = 'rgba(255,255,255,0.05)';

export function TrendChart({
  type,
  data,
  xKey = 'name',
  seriesKey = 'value',
  colors = ['#5e6ad2'],
  height = 180,
}: TrendChartProps) {
  const tooltip = ChartTooltip();

  if (type === 'donut') {
    const total = data.reduce((sum, row) => sum + Number(row[seriesKey] ?? 0), 0);
    return (
      <div style={{ position: 'relative', width: '100%', height }}>
        <ResponsiveContainer width="100%" height="100%">
          <PieChart>
            <Tooltip content={tooltip} />
            <Pie
              data={data}
              dataKey={seriesKey}
              nameKey={xKey}
              innerRadius="62%"
              outerRadius="88%"
              paddingAngle={2}
              stroke="none"
              isAnimationActive={false}
            >
              {data.map((_, i) => (
                <Cell key={i} fill={colors[i % colors.length]} />
              ))}
            </Pie>
          </PieChart>
        </ResponsiveContainer>
        <div
          style={{
            position: 'absolute',
            inset: 0,
            display: 'flex',
            flexDirection: 'column',
            alignItems: 'center',
            justifyContent: 'center',
            pointerEvents: 'none',
          }}
        >
          <strong className="ln-metric__value" style={{ fontSize: 20 }}>
            {total}
          </strong>
          <span className="ln-metric__label">总计</span>
        </div>
      </div>
    );
  }

  const common = {
    data,
    margin: { top: 8, right: 8, bottom: 0, left: 0 },
  };

  if (type === 'bar') {
    return (
      <ResponsiveContainer width="100%" height={height}>
        <BarChart {...common}>
          <CartesianGrid stroke={GRID} vertical={false} />
          <XAxis dataKey={xKey} tick={AXIS_TICK} axisLine={false} tickLine={false} />
          <YAxis tick={AXIS_TICK} axisLine={false} tickLine={false} width={34} />
          <Tooltip content={tooltip} cursor={{ fill: 'rgba(255,255,255,0.04)' }} />
          <Bar dataKey={seriesKey} radius={[3, 3, 0, 0]} isAnimationActive={false}>
            {data.map((_, i) => (
              <Cell key={i} fill={colors[i % colors.length]} />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    );
  }

  return (
    <ResponsiveContainer width="100%" height={height}>
      <AreaChart {...common}>
        <defs>
          <linearGradient id="ln-area-fill" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="#5e6ad2" stopOpacity={0.28} />
            <stop offset="100%" stopColor="#5e6ad2" stopOpacity={0} />
          </linearGradient>
        </defs>
        <CartesianGrid stroke={GRID} vertical={false} />
        <XAxis dataKey={xKey} tick={AXIS_TICK} axisLine={false} tickLine={false} />
        <YAxis tick={AXIS_TICK} axisLine={false} tickLine={false} width={34} />
        <Tooltip content={tooltip} cursor={{ stroke: 'rgba(255,255,255,0.12)' }} />
        <Area
          type="monotone"
          dataKey={seriesKey}
          stroke="#5e6ad2"
          strokeWidth={1.8}
          fill="url(#ln-area-fill)"
          isAnimationActive={false}
        />
      </AreaChart>
    </ResponsiveContainer>
  );
}
