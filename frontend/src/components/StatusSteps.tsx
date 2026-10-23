import type { Stage } from '../api'

const STEPS: { stage: Stage; label: string }[] = [
  { stage: 'planning', label: 'Understanding your question' },
  { stage: 'querying', label: 'Querying purchase records' },
  { stage: 'answering', label: 'Writing the answer' },
]

interface Props {
  stage: Stage
  reasoning?: string
  retries: number
}

export function StatusSteps({ stage, reasoning, retries }: Props) {
  const current = STEPS.findIndex((s) => s.stage === (stage === 'retrying' ? 'planning' : stage))

  return (
    <ol className="status-steps" aria-live="polite">
      {STEPS.map((step, i) => {
        const state = i < current ? 'done' : i === current ? 'active' : 'pending'
        return (
          <li key={step.stage} className={state}>
            <span className="step-icon" aria-hidden>
              {state === 'done' ? '✓' : ''}
            </span>
            <span>
              {step.label}
              {step.stage === 'planning' && retries > 0 && (
                <span className="step-note"> · query failed, self-correcting (retry {retries})</span>
              )}
              {step.stage === 'querying' && reasoning && state !== 'pending' && (
                <span className="step-detail">{reasoning}</span>
              )}
            </span>
          </li>
        )
      })}
    </ol>
  )
}
