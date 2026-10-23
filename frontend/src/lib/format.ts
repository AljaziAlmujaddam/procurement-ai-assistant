const MONEY_KEY = /spend|price|cost|amount|value|revenue|dollar/i

export const isMoneyKey = (key: string) => MONEY_KEY.test(key)

export const humanizeKey = (key: string) =>
  key.replace(/[_.]+/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase())

export function formatValue(key: string, value: unknown): string {
  if (value === null || value === undefined) return '—'
  if (typeof value === 'number') {
    if (isMoneyKey(key)) {
      return value.toLocaleString('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 2 })
    }
    return value.toLocaleString('en-US', { maximumFractionDigits: 2 })
  }
  if (Array.isArray(value)) return value.join(', ')
  if (typeof value === 'object') return JSON.stringify(value)
  return String(value)
}

export const formatYearRange = (from: string, to: string) =>
  `${new Date(from).getFullYear()}–${new Date(to).getFullYear()}`

const compact = new Intl.NumberFormat('en-US', { notation: 'compact', maximumFractionDigits: 1 })

export function formatCompact(key: string, value: number): string {
  const text = compact.format(Math.abs(value))
  const sign = value < 0 ? '-' : ''
  return isMoneyKey(key) ? `${sign}$${text}` : `${sign}${text}`
}
