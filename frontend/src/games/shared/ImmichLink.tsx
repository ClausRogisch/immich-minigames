import type { MouseEvent } from "react"
import { useTranslation } from "react-i18next"

import type { ImmichEntityKind } from "../../api/config"
import { useImmichLinks } from "../../api/config"
import { ImmichIcon } from "./ImmichIcon"
import { detectMobilePlatform, openInImmichApp } from "./immichDeepLink"

interface ImmichLinkProps {
  kind: ImmichEntityKind
  id: string
  className?: string
}

/** "Ver en Immich" deep link - renders nothing if Immich's
 * public URL isn't configured, so callers never need their own conditional around this. Styled as
 * a menu row (see EntryOptionsMenu.tsx), matching menu/UserMenu.tsx's own account/language/theme
 * rows - it's meant to live inside that "..." popover, not stand alone.
 *
 * The href is always the web URL, so desktop, "open in new tab" and "copy link" all behave like a
 * plain link; a plain tap on a phone is intercepted to try the Immich app first, in a tab of its own
 * so the game's own tab survives either outcome (immichDeepLink.ts). */
/** The web URL plus a click handler that opens the Immich app instead on a phone - shared by
 * ImmichLink (menu row) and ImmichPill (on-photo pill). null when Immich's public URL isn't
 * configured, in which case neither renders anything. */
function useImmichTarget(kind: ImmichEntityKind, id: string) {
  const links = useImmichLinks()
  if (!links) return null

  const href = links.webUrl(kind, id)
  const onClick = (event: MouseEvent<HTMLAnchorElement>) => {
    // Let the browser handle anything that isn't a plain left click - those already mean "open this
    // href somewhere", and the app can't honour that.
    if (event.defaultPrevented || event.button !== 0) return
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return

    const platform = detectMobilePlatform()
    if (!platform) return

    event.preventDefault()
    openInImmichApp(platform, links.appUrl(kind, id), href)
  }
  return { href, onClick }
}

export function ImmichLink({ kind, id, className = "" }: ImmichLinkProps) {
  const { t } = useTranslation()
  const target = useImmichTarget(kind, id)
  if (!target) return null

  // Icon only (Immich's own mark) - the label stays as the accessible name and hover tooltip.
  return (
    <a
      href={target.href}
      target="_blank"
      rel="noopener noreferrer"
      onClick={target.onClick}
      aria-label={t("common.viewInImmich")}
      title={t("common.viewInImmich")}
      className={`flex rounded-xl px-3 py-2.5 hover:bg-hover-tint ${className}`}
    >
      <ImmichIcon className="h-5 w-5" />
    </a>
  )
}

/** Small round pill with Immich's icon, laid over a photo, linking to that photo (or person/album) in Immich -
 * same link behaviour as ImmichLink. The caller positions it (`className`, inside a `relative`
 * box) and only renders it once the round is answered: Immich shows a photo's date, place and
 * people, which would give the answer away mid-round. Stops pointer events from reaching the photo
 * underneath, whose pan/zoom gesture handling (AssetPhoto.tsx) would otherwise swallow the tap. */
export function ImmichPill({ kind, id, className = "" }: ImmichLinkProps) {
  const { t } = useTranslation()
  const target = useImmichTarget(kind, id)
  if (!target) return null

  return (
    <a
      href={target.href}
      target="_blank"
      rel="noopener noreferrer"
      onClick={target.onClick}
      onPointerDown={(e) => e.stopPropagation()}
      aria-label={t("common.viewInImmich")}
      title={t("common.viewInImmich")}
      className={`absolute z-20 flex h-8 w-8 items-center justify-center rounded-full bg-surface shadow-card hover:opacity-90 ${className}`}
    >
      <ImmichIcon className="h-5 w-5" />
    </a>
  )
}
