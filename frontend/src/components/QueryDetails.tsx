import type { ChatResponse } from '../api'
import { useCopy } from '../lib/useCopy'

export function QueryDetails({ response }: { response: ChatResponse }) {
  const { copied, copy } = useCopy()
  const pipelineText = JSON.stringify(response.pipeline, null, 2)

  return (
    <div className="query-details">
      <p className="query-reasoning">
        <strong>Interpretation:</strong> {response.reasoning}
      </p>
      <div className="code-block">
        <div className="code-block-header">
          <span>MongoDB aggregation pipeline</span>
          <button className="link-button" onClick={() => copy(pipelineText)}>
            {copied ? 'Copied' : 'Copy'}
          </button>
        </div>
        <pre>
          <code>{pipelineText}</code>
        </pre>
      </div>
      <p className="query-meta">
        {response.row_count.toLocaleString()} rows · {(response.elapsed_ms / 1000).toFixed(1)}s ·{' '}
        {response.attempts === 1 ? 'first attempt' : `${response.attempts} attempts (self-corrected)`}
      </p>
    </div>
  )
}
