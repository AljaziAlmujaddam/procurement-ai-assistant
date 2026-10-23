const API_BASE = import.meta.env.VITE_API_URL ?? ''

export type Visualization = 'none' | 'number' | 'bar' | 'line' | 'pie' | 'table'
export type Row = Record<string, unknown>

export interface ChatResponse {
  conversation_id: string
  answer: string
  needs_query: boolean
  reasoning: string
  pipeline: Record<string, unknown>[] | null
  results: Row[]
  row_count: number
  visualization: Visualization
  attempts: number
  error: string | null
  elapsed_ms: number
  follow_ups: string[]
  cancelled?: boolean
}

/** A proposed query waiting for the user's approval (validation mode). */
export interface ReviewRequest {
  reasoning: string
  pipeline: string
  visualization: Visualization
  attempt: number
  last_error: string | null
}

export type ReviewDecision =
  | { action: 'approve' }
  | { action: 'edit'; pipeline: string }
  | { action: 'feedback'; feedback: string }
  | { action: 'cancel' }

export interface Health {
  status: string
  documents: number | null
  llm_model: string
}

export interface Suggestion {
  category: string
  question: string
}

export interface Stats {
  line_items: number
  orders: number
  total_spend: number
  departments: number
  suppliers: number
  first_date: string
  last_date: string
}

export interface ConversationSummary {
  id: string
  title: string
  updated_at: string
  turn_count: number
}

export interface ConversationDetail {
  id: string
  title: string
  created_at: string
  updated_at: string
  turns: { question: string; response: ChatResponse }[]
  pending?: { question: string; review: ReviewRequest; created_at: string } | null
}

export type Stage = 'planning' | 'querying' | 'retrying' | 'answering'

export type StreamEvent =
  | { event: 'status'; data: { stage: Stage; conversation_id?: string; reasoning?: string; error?: string } }
  | { event: 'results'; data: { row_count: number } }
  | { event: 'token'; data: { text: string } }
  | { event: 'done'; data: ChatResponse }
  | { event: 'followups'; data: { questions: string[] } }
  | { event: 'approval_required'; data: ReviewRequest & { conversation_id: string; question: string } }
  | { event: 'error'; data: { message: string } }

export class ApiError extends Error {
  readonly status: number

  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

async function errorFrom(res: Response): Promise<ApiError> {
  const body = await res.json().catch(() => null)
  const detail = body?.detail
  const message = typeof detail === 'string' ? detail : `Server responded with ${res.status}`
  return new ApiError(res.status, message)
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, init)
  if (!res.ok) throw await errorFrom(res)
  return (res.status === 204 ? undefined : res.json()) as Promise<T>
}

export const getHealth = () => request<Health>('/api/health')
export const getStats = () => request<Stats>('/api/stats')
export const getSuggestions = () =>
  request<{ suggestions: Suggestion[] }>('/api/suggestions').then((r) => r.suggestions)

export const listConversations = () => request<ConversationSummary[]>('/api/conversations')
export const getConversation = (id: string) => request<ConversationDetail>(`/api/conversations/${id}`)
export const deleteConversation = (id: string) =>
  request<void>(`/api/conversations/${id}`, { method: 'DELETE' })

/** POST a message and invoke `onEvent` for each server-sent event until the stream ends. */
export const streamChat = (
  message: string,
  conversationId: string | null,
  validate: boolean,
  onEvent: (e: StreamEvent) => void,
  signal?: AbortSignal,
) => postStream('/api/chat/stream', { message, conversation_id: conversationId, validate }, onEvent, signal)

/** Answer a validation-mode approval request and stream the rest of the turn. */
export const resumeChat = (
  conversationId: string,
  decision: ReviewDecision,
  onEvent: (e: StreamEvent) => void,
  signal?: AbortSignal,
) => postStream('/api/chat/resume', { conversation_id: conversationId, ...decision }, onEvent, signal)

async function postStream(
  path: string,
  body: object,
  onEvent: (e: StreamEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const res = await fetch(`${API_BASE}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
    signal,
  })
  if (!res.ok || !res.body) throw await errorFrom(res)

  const reader = res.body.pipeThrough(new TextDecoderStream()).getReader()
  let buffer = ''
  for (;;) {
    const { value, done } = await reader.read()
    if (done) break
    buffer += value

    let boundary: number
    while ((boundary = buffer.indexOf('\n\n')) !== -1) {
      const block = buffer.slice(0, boundary)
      buffer = buffer.slice(boundary + 2)

      let event = 'message'
      let data = ''
      for (const line of block.split('\n')) {
        if (line.startsWith('event: ')) event = line.slice(7)
        else if (line.startsWith('data: ')) data += line.slice(6)
      }
      if (data) onEvent({ event, data: JSON.parse(data) } as StreamEvent)
    }
  }
}
