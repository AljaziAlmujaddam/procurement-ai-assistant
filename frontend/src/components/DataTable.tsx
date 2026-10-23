import { useMemo } from 'react'
import type { Row } from '../api'
import { flattenRow } from '../lib/chartData'
import { formatValue, humanizeKey } from '../lib/format'

const MAX_VISIBLE_ROWS = 100

function toCsv(rows: Row[], columns: string[]): string {
  const escape = (v: unknown) => {
    const s = v === null || v === undefined ? '' : Array.isArray(v) ? v.join('; ') : String(v)
    return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s
  }
  return [columns.join(','), ...rows.map((r) => columns.map((c) => escape(r[c])).join(','))].join('\n')
}

export function DataTable({ results }: { results: Row[] }) {
  const rows = useMemo(() => results.map((r) => flattenRow(r)), [results])
  const columns = useMemo(() => {
    const keys = [...new Set(rows.flatMap((r) => Object.keys(r)))]
    const isNumeric = (k: string) => rows.every((r) => r[k] == null || typeof r[k] === 'number')
    return [...keys.filter((k) => !isNumeric(k)), ...keys.filter(isNumeric)]
  }, [rows])

  const download = () => {
    const url = URL.createObjectURL(new Blob([toCsv(rows, columns)], { type: 'text/csv' }))
    const a = Object.assign(document.createElement('a'), { href: url, download: 'procurement-results.csv' })
    a.click()
    URL.revokeObjectURL(url)
  }

  return (
    <div className="data-table">
      <div className="data-table-toolbar">
        <span>
          {rows.length.toLocaleString()} row{rows.length === 1 ? '' : 's'}
          {rows.length > MAX_VISIBLE_ROWS && ` (showing first ${MAX_VISIBLE_ROWS})`}
        </span>
        <button className="link-button" onClick={download}>
          Download CSV
        </button>
      </div>
      <div className="table-scroll">
        <table>
          <thead>
            <tr>
              {columns.map((c) => (
                <th key={c}>{humanizeKey(c)}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.slice(0, MAX_VISIBLE_ROWS).map((row, i) => (
              <tr key={i}>
                {columns.map((c) => (
                  <td key={c} className={typeof row[c] === 'number' ? 'num' : ''}>
                    {formatValue(c, row[c])}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
