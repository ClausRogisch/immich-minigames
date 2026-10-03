// Remaining wrong guesses for an admin-configured strike count (backend's strike_count setting) -
// one mark per strike, crossed out once used. Sits right under ScoreBadge, same fixed-pill look, and
// only renders when strikes are enabled at all (allowed > 0), so the classic one-mistake game looks
// exactly as before.
export function StrikesBadge({
  label,
  used,
  allowed,
}: {
  label: string
  used: number
  allowed: number
}) {
  if (allowed <= 0) return null
  return (
    <div
      className="fixed top-[62px] right-[18px] z-30 flex items-center gap-2 rounded-full bg-badge-bg px-4 py-1.5 shadow-card md:top-[84px] md:right-10 md:px-5"
      aria-label={`${label}: ${Math.min(used, allowed)} / ${allowed}`}
    >
      <span className="text-[11px] font-bold tracking-wide text-badge-label uppercase md:text-[13px]">
        {label}
      </span>
      <span className="flex gap-1 font-mono text-base font-bold md:text-lg" aria-hidden="true">
        {Array.from({ length: allowed }, (_, i) => (
          <span key={i} className={i < used ? "text-rose-600" : "text-badge-value opacity-40"}>
            {i < used ? "✕" : "○"}
          </span>
        ))}
      </span>
    </div>
  )
}
