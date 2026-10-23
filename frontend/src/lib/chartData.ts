import type { Row } from '../api'
import { isMoneyKey } from './format'

export const LABEL_KEY = '__label'

const METRIC_KEY = /spend|price|cost|amount|value|total|count|orders|items|quantity|avg|average|sum|share|percent|pct|lines/i
const DIMENSION_KEY = /year|quarter|month|fiscal|period|date|week|day/i

export interface ChartSpec {
  data: Row[]
  metrics: string[]
  dimensions: string[]
}

/** Flattens nested objects such as `{ _id: { year, quarter } }` into `_id.year`, `_id.quarter`. */
export function flattenRow(row: Row, prefix = ''): Row {
  const flat: Row = {}
  for (const [key, value] of Object.entries(row)) {
    const name = prefix ? `${prefix}.${key}` : key
    if (value && typeof value === 'object' && !Array.isArray(value)) {
      Object.assign(flat, flattenRow(value as Row, name))
    } else {
      flat[name] = value
    }
  }
  return flat
}

function isMetric(key: string, rows: Row[]): boolean {
  const numeric = rows.every((r) => r[key] === null || r[key] === undefined || typeof r[key] === 'number')
  if (!numeric) return false
  return !DIMENSION_KEY.test(key) || METRIC_KEY.test(key)
}

function formatDimension(key: string, value: unknown): string {
  if (typeof value === 'number' && /quarter/i.test(key)) return `Q${value}`
  if (value === null || value === undefined) return 'N/A'
  return String(value)
}

export function buildChartSpec(results: Row[]): ChartSpec | null {
  if (results.length === 0) return null
  const rows = results.map((r) => flattenRow(r))
  const keys = Object.keys(rows[0])

  const metrics = keys
    .filter((k) => isMetric(k, rows))
    .sort((a, b) => Number(isMoneyKey(b)) - Number(isMoneyKey(a)))
  if (metrics.length === 0) return null
  const dimensions = keys
    .filter((k) => !metrics.includes(k))
    .sort((a, b) => Number(/quarter|month/i.test(a)) - Number(/quarter|month/i.test(b)))

  const data = rows.map((row, i) => ({
    ...row,
    [LABEL_KEY]: dimensions.length
      ? dimensions.map((d) => formatDimension(d, row[d])).join(' ')
      : `#${i + 1}`,
  }))
  return { data, metrics, dimensions }
}

export interface SeriesData {
  data: Row[]
  series: string[]
}

const MAX_SERIES = 8

/**
 * Rows with two dimensions (e.g. fiscal_year x acquisition_type) become one series per value of
 * the non-time dimension, with the time-like dimension on the x-axis.
 */
export function pivotSeries(spec: ChartSpec, metric: string): SeriesData | null {
  if (spec.dimensions.length !== 2) return null
  const [a, b] = spec.dimensions
  const xKey = DIMENSION_KEY.test(b) && !DIMENSION_KEY.test(a) ? b : a
  const seriesKey = xKey === a ? b : a

  const seriesName = (row: Row) => formatDimension(seriesKey, row[seriesKey])
  const series = [...new Set(spec.data.map(seriesName))]
  if (series.length < 2 || series.length > MAX_SERIES) return null

  const byX = new Map<string, Row>()
  for (const row of spec.data) {
    const x = formatDimension(xKey, row[xKey])
    const point = byX.get(x) ?? { [LABEL_KEY]: x }
    point[seriesName(row)] = row[metric]
    byX.set(x, point)
  }
  return { data: [...byX.values()], series }
}
