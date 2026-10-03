import { StatCard } from "./StatCard"
import type { StatCardImmichLink } from "./StatCard"
import { ValueBadge } from "./ValueBadge"

interface PersonCardProps {
  name: string
  value: number | string
  valueKind: "count" | "date"
  subtitle: string
  thumbnailUrl: string
  immichLink?: StatCardImmichLink
}

export function PersonCard({
  name,
  value,
  valueKind,
  subtitle,
  thumbnailUrl,
  immichLink,
}: PersonCardProps) {
  return (
    <StatCard thumbnailUrl={thumbnailUrl} name={name} subtitle={subtitle} immichLink={immichLink}>
      <ValueBadge value={value} kind={valueKind} />
    </StatCard>
  )
}
