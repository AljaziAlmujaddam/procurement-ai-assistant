import { useState } from 'react'
import type { ReviewDecision, ReviewRequest } from '../api'

type Mode = 'view' | 'edit' | 'feedback'

interface Props {
  review: ReviewRequest
  error?: string
  busy?: boolean
  onDecide: (decision: ReviewDecision) => void
}

function prettyPipeline(text: string): string {
  try {
    return JSON.stringify(JSON.parse(text), null, 2)
  } catch {
    return text
  }
}

export function ReviewCard({ review, error, busy = false, onDecide }: Props) {
  const pretty = prettyPipeline(review.pipeline)
  const [mode, setMode] = useState<Mode>('view')
  const [draft, setDraft] = useState(pretty)
  const [feedback, setFeedback] = useState('')
  const [localError, setLocalError] = useState<string | null>(null)
  const shownError = localError ?? error

  const runEdited = () => {
    try {
      const parsed = JSON.parse(draft)
      if (!Array.isArray(parsed)) throw new Error('The query must be a JSON array of pipeline stages.')
    } catch (e) {
      setLocalError(e instanceof SyntaxError ? `Invalid JSON: ${e.message}` : (e as Error).message)
      return
    }
    setLocalError(null)
    onDecide({ action: 'edit', pipeline: draft })
  }

  const back = () => {
    setMode('view')
    setLocalError(null)
  }

  return (
    <div className="review-card">
      <div className="review-header">
        <span className="review-badge">
          <span className="review-pulse" aria-hidden /> Waiting for your approval
        </span>
        <span className="review-hint">Nothing runs until you decide</span>
      </div>

      {review.last_error && (
        <p className="review-retry">
          The previous query didn't work ({review.last_error.split('.')[0]}). Here is a corrected version.
        </p>
      )}

      <div className="review-section">
        <div className="review-label">Plan</div>
        <p className="review-plan">{review.reasoning}</p>
      </div>

      <div className="review-section">
        <div className="review-label">{mode === 'edit' ? 'Edit the query' : 'Query to run'}</div>
        {mode === 'edit' ? (
          <textarea
            className="review-editor"
            value={draft}
            spellCheck={false}
            onChange={(e) => setDraft(e.target.value)}
            rows={Math.min(18, draft.split('\n').length + 1)}
            aria-label="Edit the MongoDB query"
          />
        ) : (
          <pre className="review-code">
            <code>{pretty}</code>
          </pre>
        )}
      </div>

      {mode === 'feedback' && (
        <div className="review-section">
          <div className="review-label">What should change?</div>
          <textarea
            className="review-feedback"
            value={feedback}
            autoFocus
            rows={2}
            placeholder="e.g. use fiscal year quarters instead, or only include IT Goods"
            onChange={(e) => setFeedback(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !e.shiftKey && feedback.trim()) {
                e.preventDefault()
                onDecide({ action: 'feedback', feedback: feedback.trim() })
              }
            }}
          />
        </div>
      )}

      {shownError && <div className="error-box review-error">{shownError}</div>}

      <fieldset className="review-actions" disabled={busy}>
        {mode === 'view' && (
          <>
            <button className="review-button primary" onClick={() => onDecide({ action: 'approve' })}>
              Approve &amp; run
            </button>
            <button className="review-button" onClick={() => setMode('edit')}>
              Edit query
            </button>
            <button className="review-button" onClick={() => setMode('feedback')}>
              Request changes
            </button>
            <button className="review-button ghost" onClick={() => onDecide({ action: 'cancel' })}>
              Cancel
            </button>
          </>
        )}
        {mode === 'edit' && (
          <>
            <button className="review-button primary" onClick={runEdited}>
              Run edited query
            </button>
            <button className="review-button ghost" onClick={back}>
              Back
            </button>
          </>
        )}
        {mode === 'feedback' && (
          <>
            <button
              className="review-button primary"
              disabled={!feedback.trim()}
              onClick={() => onDecide({ action: 'feedback', feedback: feedback.trim() })}
            >
              Send to AI
            </button>
            <button className="review-button ghost" onClick={back}>
              Back
            </button>
          </>
        )}
        {busy && <span className="review-sending">Sending…</span>}
      </fieldset>
    </div>
  )
}
