import type { Stats, Suggestion } from '../api'
import { formatYearRange } from '../lib/format'

interface Props {
  stats: Stats | null
  suggestions: Suggestion[]
  onPick: (question: string) => void
}

const compactMoney = new Intl.NumberFormat('en-US', {
  style: 'currency',
  currency: 'USD',
  notation: 'compact',
  maximumFractionDigits: 1,
})

export function Welcome({ stats, suggestions, onPick }: Props) {
  const kpis = stats
    ? [
        { label: 'Total spend', value: compactMoney.format(stats.total_spend) },
        { label: 'Purchase orders', value: stats.orders.toLocaleString() },
        { label: 'Line items', value: stats.line_items.toLocaleString() },
        { label: 'Departments', value: stats.departments.toLocaleString() },
        { label: 'Suppliers', value: stats.suppliers.toLocaleString() },
        { label: 'Period', value: formatYearRange(stats.first_date, stats.last_date) },
      ]
    : []

  return (
    <div className="welcome">
      <div className="welcome-badge">AI procurement analyst</div>
      <h1>
        Ask anything about <span className="highlight">California's purchasing</span>
      </h1>
      <p>
        I turn your question into a database query, run it on every purchase order, and explain the answer with
        charts. Ask in plain English, then keep the conversation going with follow-ups.
      </p>

      {kpis.length > 0 && (
        <div className="kpis">
          {kpis.map((k) => (
            <div key={k.label} className="kpi">
              <div className="kpi-value">{k.value}</div>
              <div className="kpi-label">{k.label}</div>
            </div>
          ))}
        </div>
      )}

      {suggestions.length > 0 && (
        <>
          <div className="suggestions-title">Try one of these</div>
          <div className="suggestions">
            {suggestions.map((s) => (
              <button key={s.question} className="suggestion" onClick={() => onPick(s.question)}>
                <span className="suggestion-tag">{s.category}</span>
                <span>{s.question}</span>
              </button>
            ))}
          </div>
        </>
      )}
    </div>
  )
}
