import { useRef, useState, type KeyboardEvent } from 'react'

interface Props {
  busy: boolean
  disabled?: boolean
  placeholder?: string
  onSend: (text: string) => void
  onStop: () => void
}

const DEFAULT_PLACEHOLDER = 'Ask about orders, spending, suppliers, departments…'

export function Composer({ busy, disabled = false, placeholder = DEFAULT_PLACEHOLDER, onSend, onStop }: Props) {
  const [text, setText] = useState('')
  const ref = useRef<HTMLTextAreaElement>(null)

  const resize = () => {
    const el = ref.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = `${Math.min(el.scrollHeight, 180)}px`
  }

  const submit = () => {
    const trimmed = text.trim()
    if (!trimmed || busy || disabled) return
    onSend(trimmed)
    setText('')
    requestAnimationFrame(resize)
  }

  const onKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault()
      submit()
    }
  }

  return (
    <div className={`composer ${disabled ? 'disabled' : ''}`}>
      <textarea
        ref={ref}
        rows={1}
        value={text}
        disabled={disabled}
        placeholder={placeholder}
        onChange={(e) => {
          setText(e.target.value)
          resize()
        }}
        onKeyDown={onKeyDown}
        aria-label="Message"
      />
      {busy ? (
        <button className="send-button stop" onClick={onStop} aria-label="Stop">
          ■
        </button>
      ) : (
        <button className="send-button" onClick={submit} disabled={!text.trim() || disabled} aria-label="Send">
          ↑
        </button>
      )}
    </div>
  )
}
