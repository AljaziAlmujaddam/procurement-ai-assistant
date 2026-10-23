import { useEffect, useRef, useState } from 'react'
import Markdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import type { ChatResponse, ReviewDecision, ReviewRequest, Stage } from '../api'
import { useCopy } from '../lib/useCopy'
import { DataTable } from './DataTable'
import { QueryDetails } from './QueryDetails'
import { ResultChart } from './ResultChart'
import { ReviewCard } from './ReviewCard'
import { StatusSteps } from './StatusSteps'

export interface AssistantTurn {
  id: string
  role: 'assistant'
  stage: Stage | 'awaiting' | 'done' | 'error'
  text: string
  reasoning?: string
  retries: number
  response?: ChatResponse
  error?: string
  review?: ReviewRequest
  reviewError?: string
  reviewBusy?: boolean
}

type Panel = 'data' | 'query' | null

interface Props {
  turn: AssistantTurn
  showFollowUps: boolean
  onFollowUp: (question: string) => void
  onReview: (decision: ReviewDecision) => void
}

export function AssistantMessage({ turn, showFollowUps, onFollowUp, onReview }: Props) {
  const [panel, setPanel] = useState<Panel>(null)
  const { copied, copy } = useCopy()
  const panelRef = useRef<HTMLDivElement>(null)
  const { response } = turn
  const inProgress = turn.stage !== 'done' && turn.stage !== 'error'
  const hasData = !!response?.needs_query && response.results.length > 0
  const followUps = showFollowUps ? (response?.follow_ups ?? []) : []

  useEffect(() => {
    if (panel) panelRef.current?.scrollIntoView({ behavior: 'smooth', block: 'nearest' })
  }, [panel])

  const toggle = (p: Exclude<Panel, null>) => setPanel((current) => (current === p ? null : p))

  return (
    <div className="message assistant">
      <div className="avatar" aria-hidden>
        AI
      </div>
      <div className="message-body">
        {turn.stage === 'awaiting' && turn.review && (
          <ReviewCard
            key={turn.review.pipeline}
            review={turn.review}
            error={turn.reviewError}
            busy={turn.reviewBusy}
            onDecide={onReview}
          />
        )}

        {inProgress && turn.stage !== 'awaiting' && !turn.text && (
          <StatusSteps stage={turn.stage as Stage} reasoning={turn.reasoning} retries={turn.retries} />
        )}

        {turn.text && (
          <div className="markdown">
            <Markdown remarkPlugins={[remarkGfm]}>{turn.text}</Markdown>
            {inProgress && <span className="cursor" aria-hidden />}
          </div>
        )}

        {turn.stage === 'error' && <div className="error-box">{turn.error}</div>}

        {hasData && <ResultChart results={response.results} visualization={response.visualization} />}

        {turn.stage === 'done' && (
          <div className="message-actions">
            {hasData && (
              <button className={panel === 'data' ? 'chip active' : 'chip'} onClick={() => toggle('data')}>
                View data
              </button>
            )}
            {response?.needs_query && response.pipeline && (
              <button className={panel === 'query' ? 'chip active' : 'chip'} onClick={() => toggle('query')}>
                Show query
              </button>
            )}
            <button className="chip" onClick={() => copy(turn.text)}>
              {copied ? 'Copied' : 'Copy answer'}
            </button>
          </div>
        )}

        <div ref={panelRef}>
          {panel === 'data' && response && <DataTable results={response.results} />}
          {panel === 'query' && response && <QueryDetails response={response} />}
        </div>

        {followUps.length > 0 && (
          <div className="follow-ups">
            <div className="follow-ups-title">Suggested follow-ups</div>
            {followUps.map((q) => (
              <button key={q} className="follow-up" onClick={() => onFollowUp(q)}>
                <span aria-hidden>↳</span> {q}
              </button>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
