import type { ReactNode } from "react"

import { ImmichPill } from "../shared/ImmichLink"
import { PersonPhoto } from "./PersonPhoto"

export interface StatCardImmichLink {
  kind: "person" | "album"
  id: string
}

interface StatCardProps {
  thumbnailUrl: string
  name: string
  subtitle: string
  children: ReactNode
  // An ImmichPill over the photo, linking to this person/album in Immich - only passed once the
  // card's value is visible (always for the reference card, after the reveal for the candidate).
  immichLink?: StatCardImmichLink
}

/**
 * Shared shell for both the (always-revealed) reference card and the (interactive) candidate
 * card - photo, name, a subtitle line, and an action row, each row a fixed min-height so two
 * cards side by side (or stacked) always end up the same total height regardless of how much
 * their name/subtitle text wraps.
 */
export function StatCard({ thumbnailUrl, name, subtitle, children, immichLink }: StatCardProps) {
  return (
    <div className="flex h-full min-h-0 w-full flex-col items-center gap-3.5 rounded-[22px] border border-line bg-surface p-[18px] shadow-card md:h-auto md:w-[300px] md:rounded-3xl md:p-5">
      <div className="relative md:w-full">
        <PersonPhoto src={thumbnailUrl} alt={name} />
        {immichLink && (
          <ImmichPill
            kind={immichLink.kind}
            id={immichLink.id}
            className="top-1 left-1 md:top-2 md:left-2"
          />
        )}
      </div>

      <div className="flex min-h-[56px] w-full items-center justify-center">
        <div className="text-center text-xl font-bold text-ink">{name}</div>
      </div>

      <div className="flex min-h-[44px] w-full items-center justify-center">
        <p className="text-center text-sm font-semibold text-muted">{subtitle}</p>
      </div>

      <div className="flex min-h-[52px] w-full items-center justify-center">{children}</div>
    </div>
  )
}
