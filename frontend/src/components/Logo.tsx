export function Logo({ size = 34 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" aria-hidden="true">
      <defs>
        <linearGradient id="logo-gradient" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="#ffb2b2" />
          <stop offset="1" stopColor="#e36a6a" />
        </linearGradient>
      </defs>
      <rect width="32" height="32" rx="9" fill="url(#logo-gradient)" />
      <path d="M9 22V14M16 22V9M23 22v-5" stroke="#fffbf1" strokeWidth="3" strokeLinecap="round" />
    </svg>
  )
}
