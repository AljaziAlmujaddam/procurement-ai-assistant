import { useMemo, useState } from 'react'
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
  Line,
  LineChart,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import type { Row, Visualization } from '../api'
import { buildChartSpec, LABEL_KEY, pivotSeries } from '../lib/chartData'
import { formatCompact, formatValue, humanizeKey } from '../lib/format'

const COLORS = ['#e36a6a', '#f2a65a', '#8d5b4c', '#d1a03b', '#c94f4f', '#b5838d', '#ffb2b2', '#6f8f72']
const GRID = '#f0e2c4'
const MAX_BARS = 25
const MAX_SLICES = 8

interface Props {
  results: Row[]
  visualization: Visualization
}

export function ResultChart({ results, visualization }: Props) {
  const spec = useMemo(() => buildChartSpec(results), [results])
  const [selected, setSelected] = useState<string | null>(null)

  if (!spec || visualization === 'none' || visualization === 'table') return null

  const metric = selected && spec.metrics.includes(selected) ? selected : spec.metrics[0]
  const kind = spec.data.length === 1 ? 'number' : visualization === 'number' ? 'bar' : visualization

  if (kind === 'number') {
    const row = spec.data[0]
    return (
      <div className="stat-cards">
        {spec.metrics.map((m) => (
          <div key={m} className="stat-card">
            <div className="stat-value">{formatValue(m, row[m])}</div>
            <div className="stat-label">
              {humanizeKey(m)}
              {spec.dimensions.length > 0 && ` · ${row[LABEL_KEY]}`}
            </div>
          </div>
        ))}
      </div>
    )
  }

  const pivot = kind === 'pie' ? null : pivotSeries(spec, metric)
  const data = pivot ? pivot.data : spec.data.slice(0, MAX_BARS)
  const seriesKeys = pivot ? pivot.series : [metric]

  const tooltip = (
    <Tooltip
      formatter={(value, name) => [formatValue(metric, value as number), pivot ? name : humanizeKey(metric)]}
      contentStyle={{ borderRadius: 10, borderColor: GRID, fontSize: 13 }}
    />
  )
  const legend = pivot && <Legend wrapperStyle={{ fontSize: 12 }} />
  const valueAxis = {
    tickFormatter: (v: number) => formatCompact(metric, v),
    tick: { fontSize: 12 },
  }

  let chart
  let height = 300
  if (kind === 'pie') {
    const slices = spec.data.slice(0, MAX_SLICES).filter((d) => Number(d[metric]) > 0)
    chart = (
      <PieChart>
        <Pie data={slices} dataKey={metric} nameKey={LABEL_KEY} outerRadius={110} innerRadius={58} paddingAngle={2}>
          {slices.map((_, i) => (
            <Cell key={i} fill={COLORS[i % COLORS.length]} stroke="#fff" />
          ))}
        </Pie>
        {tooltip}
        <Legend wrapperStyle={{ fontSize: 12 }} />
      </PieChart>
    )
  } else if (kind === 'line') {
    chart = (
      <LineChart data={data} margin={{ top: 8, right: 16, bottom: 8, left: 8 }}>
        <CartesianGrid strokeDasharray="3 3" stroke={GRID} />
        <XAxis dataKey={LABEL_KEY} tick={{ fontSize: 12 }} minTickGap={16} />
        <YAxis {...valueAxis} width={64} />
        {tooltip}
        {legend}
        {seriesKeys.map((key, i) => (
          <Line
            key={key}
            type="monotone"
            dataKey={key}
            name={key}
            stroke={COLORS[i % COLORS.length]}
            strokeWidth={2.5}
            dot={{ r: 3 }}
            connectNulls
          />
        ))}
      </LineChart>
    )
  } else {
    const longLabels = !pivot && data.some((d) => String(d[LABEL_KEY]).length > 12)
    if (longLabels) height = Math.max(220, data.length * 30)
    const bars = seriesKeys.map((key, i) => (
      <Bar
        key={key}
        dataKey={key}
        name={key}
        fill={COLORS[i % COLORS.length]}
        radius={longLabels ? [0, 6, 6, 0] : [6, 6, 0, 0]}
      />
    ))
    chart = longLabels ? (
      <BarChart data={data} layout="vertical" margin={{ top: 8, right: 24, bottom: 8, left: 8 }}>
        <CartesianGrid strokeDasharray="3 3" stroke={GRID} horizontal={false} />
        <XAxis type="number" {...valueAxis} />
        <YAxis
          type="category"
          dataKey={LABEL_KEY}
          width={220}
          tick={{ fontSize: 12 }}
          tickFormatter={(v: string) => (v.length > 32 ? `${v.slice(0, 31)}…` : v)}
        />
        {tooltip}
        {bars}
      </BarChart>
    ) : (
      <BarChart data={data} margin={{ top: 8, right: 16, bottom: 8, left: 8 }}>
        <CartesianGrid strokeDasharray="3 3" stroke={GRID} vertical={false} />
        <XAxis dataKey={LABEL_KEY} tick={{ fontSize: 12 }} />
        <YAxis {...valueAxis} width={64} />
        {tooltip}
        {legend}
        {bars}
      </BarChart>
    )
  }

  return (
    <div className="chart-card">
      {spec.metrics.length > 1 && (
        <div className="metric-tabs" role="tablist">
          {spec.metrics.map((m) => (
            <button
              key={m}
              role="tab"
              aria-selected={m === metric}
              className={m === metric ? 'active' : ''}
              onClick={() => setSelected(m)}
            >
              {humanizeKey(m)}
            </button>
          ))}
        </div>
      )}
      <ResponsiveContainer width="100%" height={height}>
        {chart}
      </ResponsiveContainer>
    </div>
  )
}
