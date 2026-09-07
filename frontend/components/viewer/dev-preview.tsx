'use client';

/**
 * DEV-ONLY design-preview scaffolding.
 *
 * The installed Windows Agent does not serve this frontend — in native mode it
 * redirects browsers to the Portal, which renders the real UI. To review this
 * redesign locally without the Portal or a signed-in session, the viewer screens
 * fall back to the centralized sample data (lib/presentation-placeholders.ts)
 * *only* when `import.meta.env.DEV` is true, i.e. under `vinext dev`. Production
 * builds (`vinext build`) compile all of this out, so it can never ship or be
 * mistaken for real Agent data. A persistent banner makes the mode obvious.
 */
export const IS_DEV_PREVIEW = import.meta.env.DEV === true;

export function DevPreviewBanner() {
  if (!IS_DEV_PREVIEW) return null;
  return (
    <output className="mb-6 flex flex-wrap items-center gap-x-3 gap-y-1 rounded-xl border border-amber-500/50 bg-amber-500/10 px-4 py-2.5 text-sm">
      <span className="font-semibold text-amber-500">Design preview</span>
      <span className="text-muted-foreground">
        Sample content only — not connected to your Agent. Layout review build.
      </span>
    </output>
  );
}
