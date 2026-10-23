import type { ConversationSummary, Stats } from '../api'
import { formatYearRange } from '../lib/format'
import { Logo } from './Logo'

interface Props {
  conversations: ConversationSummary[]
  stats: Stats | null
  activeId: string | null
  open: boolean
  onHome: () => void
  onSelect: (id: string) => void
  onDelete: (id: string) => void
  onClose: () => void
}

const DAY = 24 * 60 * 60 * 1000

function groupLabel(iso: string, now: Date): string {
  const startOfToday = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime()
  const t = new Date(iso).getTime()
  if (t >= startOfToday) return 'Today'
  if (t >= startOfToday - DAY) return 'Yesterday'
  if (t >= startOfToday - 7 * DAY) return 'Previous 7 days'
  return 'Older'
}

function groupConversations(conversations: ConversationSummary[]) {
  const now = new Date()
  const groups = new Map<string, ConversationSummary[]>()
  for (const c of conversations) {
    const label = groupLabel(c.updated_at, now)
    groups.set(label, [...(groups.get(label) ?? []), c])
  }
  return [...groups.entries()]
}

export function Sidebar({ conversations, stats, activeId, open, onHome, onSelect, onDelete, onClose }: Props) {
  return (
    <>
      <aside className={`sidebar ${open ? 'open' : ''}`}>
        <button className="brand" onClick={onHome} title="Home">
          <Logo />
          <div>
            <div className="brand-title">Procurement Assistant</div>
            <div className="brand-subtitle">
              California{stats && ` · ${formatYearRange(stats.first_date, stats.last_date)}`}
            </div>
          </div>
        </button>

        <button className="new-chat" onClick={onHome}>
          <span aria-hidden>＋</span> New chat
        </button>

        <nav className="history" aria-label="Chat history">
          {conversations.length === 0 && <p className="history-empty">Your conversations will appear here.</p>}
          {groupConversations(conversations).map(([label, items]) => (
            <div key={label} className="history-group">
              <div className="history-label">{label}</div>
              {items.map((c) => (
                <div key={c.id} className={`history-item ${c.id === activeId ? 'active' : ''}`}>
                  <button className="history-title" onClick={() => onSelect(c.id)} title={c.title}>
                    {c.title}
                  </button>
                  <button
                    className="history-delete"
                    onClick={() => onDelete(c.id)}
                    aria-label={`Delete "${c.title}"`}
                    title="Delete"
                  >
                    ×
                  </button>
                </div>
              ))}
            </div>
          ))}
        </nav>
      </aside>
      {open && <div className="sidebar-backdrop" onClick={onClose} />}
    </>
  )
}
