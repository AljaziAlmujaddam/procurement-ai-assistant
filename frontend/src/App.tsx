import { useCallback, useEffect, useRef, useState } from 'react'
import {
  deleteConversation,
  getConversation,
  getHealth,
  getStats,
  getSuggestions,
  listConversations,
  resumeChat,
  streamChat,
  ApiError,
  type ConversationDetail,
  type ConversationSummary,
  type Health,
  type ReviewDecision,
  type Stats,
  type StreamEvent,
  type Suggestion,
} from './api'
import { AssistantMessage, type AssistantTurn } from './components/AssistantMessage'
import { Composer } from './components/Composer'
import { Sidebar } from './components/Sidebar'
import { Welcome } from './components/Welcome'

interface UserTurn {
  id: string
  role: 'user'
  text: string
}

type Turn = UserTurn | AssistantTurn

/** Conversations live at #/c/<id>; anything else is the home screen. */
const conversationIdFromHash = () => window.location.hash.match(/^#\/c\/([\w-]+)$/)?.[1] ?? null

const VALIDATION_KEY = 'validationMode'

function applyEvent(turn: AssistantTurn, e: StreamEvent): AssistantTurn {
  switch (e.event) {
    case 'status':
      return {
        ...turn,
        stage: e.data.stage,
        reasoning: e.data.reasoning ?? turn.reasoning,
        retries: e.data.stage === 'retrying' ? turn.retries + 1 : turn.retries,
        reviewBusy: false,
        reviewError: undefined,
      }
    case 'approval_required': {
      const { reasoning, pipeline, visualization, attempt, last_error } = e.data
      return {
        ...turn,
        stage: 'awaiting',
        reasoning,
        review: { reasoning, pipeline, visualization, attempt, last_error },
        reviewBusy: false,
        reviewError: undefined,
      }
    }
    case 'token':
      return { ...turn, text: turn.text + e.data.text }
    case 'done':
      return { ...turn, stage: 'done', text: e.data.answer, response: e.data }
    case 'followups':
      return turn.response ? { ...turn, response: { ...turn.response, follow_ups: e.data.questions } } : turn
    case 'error':
      return { ...turn, stage: 'error', error: e.data.message }
    default:
      return turn
  }
}

function toTurns(conversation: ConversationDetail): Turn[] {
  const turns = conversation.turns.flatMap((t, i): Turn[] => [
    { id: `${conversation.id}-u${i}`, role: 'user', text: t.question },
    {
      id: `${conversation.id}-a${i}`,
      role: 'assistant',
      stage: 'done',
      text: t.response.answer,
      retries: 0,
      response: t.response,
    },
  ])
  const { pending } = conversation
  if (pending) {
    turns.push(
      { id: `${conversation.id}-pending-u`, role: 'user', text: pending.question },
      {
        id: `${conversation.id}-pending-a`,
        role: 'assistant',
        stage: 'awaiting',
        text: '',
        retries: 0,
        reasoning: pending.review.reasoning,
        review: pending.review,
      },
    )
  }
  return turns
}

export default function App() {
  const [routeId, setRouteId] = useState<string | null>(conversationIdFromHash)
  const [turns, setTurns] = useState<Turn[]>([])
  const [loading, setLoading] = useState(() => conversationIdFromHash() !== null)
  const [busy, setBusy] = useState(false)
  const [conversations, setConversations] = useState<ConversationSummary[]>([])
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const [health, setHealth] = useState<Health | null>(null)
  const [stats, setStats] = useState<Stats | null>(null)
  const [suggestions, setSuggestions] = useState<Suggestion[]>([])
  const [validationMode, setValidationMode] = useState(() => localStorage.getItem(VALIDATION_KEY) === 'on')
  const shownIdRef = useRef<string | null>(routeId)
  const abortRef = useRef<AbortController | null>(null)
  const bottomRef = useRef<HTMLDivElement>(null)

  const refreshConversations = useCallback(() => {
    listConversations().then(setConversations).catch(() => {})
  }, [])

  const loadConversation = useCallback((id: string) => {
    getConversation(id)
      .then((c) => {
        if (shownIdRef.current === id) setTurns(toTurns(c))
      })
      .catch(() => {
        window.location.hash = ''
      })
      .finally(() => {
        if (shownIdRef.current === id) setLoading(false)
      })
  }, [])

  /** Sync the view with the URL hash (browser back/forward, sidebar links, new chats). */
  const showConversation = useCallback(
    (id: string | null, force = false) => {
      setRouteId(id)
      if (id === shownIdRef.current && !force) return
      abortRef.current?.abort()
      shownIdRef.current = id
      setTurns([])
      setSidebarOpen(false)
      setLoading(id !== null)
      if (id) loadConversation(id)
    },
    [loadConversation],
  )

  useEffect(() => {
    getHealth().then(setHealth).catch(() => setHealth(null))
    getStats().then(setStats).catch(() => {})
    getSuggestions().then(setSuggestions).catch(() => {})
    refreshConversations()
    if (shownIdRef.current) loadConversation(shownIdRef.current)

    const onHashChange = () => showConversation(conversationIdFromHash())
    window.addEventListener('hashchange', onHashChange)
    return () => window.removeEventListener('hashchange', onHashChange)
  }, [refreshConversations, loadConversation, showConversation])

  useEffect(() => {
    if (turns.length) bottomRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' })
  }, [turns])

  const updateAssistant = (id: string, fn: (t: AssistantTurn) => AssistantTurn) =>
    setTurns((ts) => ts.map((t) => (t.id === id && t.role === 'assistant' ? fn(t) : t)))

  /** Stream one turn (new question or resumed approval) into the assistant message `assistantId`. */
  const runTurn = useCallback(
    async (
      assistantId: string,
      start: (onEvent: (e: StreamEvent) => void, signal: AbortSignal) => Promise<void>,
      onFailure?: (err: unknown) => boolean,
    ) => {
      setBusy(true)
      const controller = new AbortController()
      abortRef.current = controller
      try {
        await start((e) => {
          if (e.event === 'status' && e.data.conversation_id && !shownIdRef.current) {
            shownIdRef.current = e.data.conversation_id
            window.location.hash = `/c/${e.data.conversation_id}`
          }
          updateAssistant(assistantId, (t) => applyEvent(t, e))
        }, controller.signal)
      } catch (err) {
        if (!onFailure?.(err)) {
          const message =
            err instanceof DOMException && err.name === 'AbortError'
              ? 'Stopped.'
              : err instanceof ApiError
                ? err.message
                : 'Could not reach the assistant. Make sure the backend is running.'
          updateAssistant(assistantId, (t) => ({ ...t, stage: 'error', error: message }))
        }
      } finally {
        setBusy(false)
        abortRef.current = null
        refreshConversations()
      }
    },
    [refreshConversations],
  )

  const send = useCallback(
    async (text: string) => {
      if (busy) return
      const assistantId = crypto.randomUUID()
      setTurns((ts) => [
        ...ts,
        { id: crypto.randomUUID(), role: 'user', text },
        { id: assistantId, role: 'assistant', stage: 'planning', text: '', retries: 0 },
      ])
      await runTurn(assistantId, (onEvent, signal) =>
        streamChat(text, shownIdRef.current, validationMode, onEvent, signal),
      )
    },
    [busy, runTurn, validationMode],
  )

  const review = useCallback(
    async (assistantId: string, decision: ReviewDecision) => {
      const conversationId = shownIdRef.current
      if (busy || !conversationId) return
      updateAssistant(assistantId, (t) => ({ ...t, reviewBusy: true, reviewError: undefined }))
      await runTurn(
        assistantId,
        (onEvent, signal) => resumeChat(conversationId, decision, onEvent, signal),
        (err) => {
          // A rejected edit keeps the card open so the user can fix the query.
          if (!(err instanceof ApiError && err.status === 422)) return false
          updateAssistant(assistantId, (t) => ({ ...t, reviewBusy: false, reviewError: err.message }))
          return true
        },
      )
    },
    [busy, runTurn],
  )

  const toggleValidation = () => {
    setValidationMode((on) => {
      localStorage.setItem(VALIDATION_KEY, on ? 'off' : 'on')
      return !on
    })
  }

  const stop = () => abortRef.current?.abort()

  const goHome = () => {
    if (conversationIdFromHash()) {
      window.location.hash = ''
    } else {
      showConversation(null, true)
    }
  }

  const openConversation = (id: string) => {
    window.location.hash = `/c/${id}`
    setSidebarOpen(false)
  }

  const removeConversation = async (id: string) => {
    if (!window.confirm('Delete this conversation?')) return
    await deleteConversation(id).catch(() => {})
    if (id === routeId) goHome()
    refreshConversations()
  }

  const lastAssistantId = [...turns].reverse().find((t) => t.role === 'assistant')?.id
  const awaitingReview = turns.some((t) => t.role === 'assistant' && t.stage === 'awaiting')
  const title = conversations.find((c) => c.id === routeId)?.title ?? (turns.length ? 'New conversation' : 'Home')

  return (
    <div className="app">
      <Sidebar
        conversations={conversations}
        stats={stats}
        activeId={routeId}
        open={sidebarOpen}
        onHome={goHome}
        onSelect={openConversation}
        onDelete={removeConversation}
        onClose={() => setSidebarOpen(false)}
      />

      <div className="main">
        <header className="topbar">
          <button className="menu-button" onClick={() => setSidebarOpen(true)} aria-label="Open chat history">
            ☰
          </button>
          <div className="topbar-title">{title}</div>
          <button
            className={`validation-toggle ${validationMode ? 'on' : ''}`}
            onClick={toggleValidation}
            role="switch"
            aria-checked={validationMode}
            title="When on, you review and approve every query before it runs"
          >
            <span className="switch" aria-hidden>
              <span className="knob" />
            </span>
            Validation mode
          </button>
          <span className={`health ${health?.status === 'ok' ? 'ok' : 'down'}`}>
            <span className="dot" />
            {health
              ? `${health.documents?.toLocaleString() ?? '?'} records · ${health.llm_model}`
              : 'Backend offline'}
          </span>
        </header>

        <main className="chat">
          <div className="chat-inner">
            {loading && <div className="loading">Loading conversation…</div>}
            {!loading && turns.length === 0 && <Welcome stats={stats} suggestions={suggestions} onPick={send} />}
            {turns.map((turn) =>
              turn.role === 'user' ? (
                <div key={turn.id} className="message user">
                  <div className="bubble">{turn.text}</div>
                </div>
              ) : (
                <AssistantMessage
                  key={turn.id}
                  turn={turn}
                  showFollowUps={turn.id === lastAssistantId && !busy}
                  onFollowUp={send}
                  onReview={(decision) => review(turn.id, decision)}
                />
              ),
            )}
            <div ref={bottomRef} />
          </div>
        </main>

        <footer className="footer">
          <Composer
            busy={busy}
            disabled={awaitingReview}
            placeholder={
              awaitingReview
                ? 'Approve, edit or cancel the query above first…'
                : validationMode
                  ? 'Ask a question. You will approve the query before it runs…'
                  : undefined
            }
            onSend={send}
            onStop={stop}
          />
          <p className="disclaimer">
            Answers are generated by AI from the dataset. Check the query for critical decisions.
          </p>
        </footer>
      </div>
    </div>
  )
}
