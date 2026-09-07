'use client';

import { Trash2, Download, ShieldCheck } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';
import { useViewerData } from '@/lib/viewer';
import {
  SettingsGroup,
  SettingRow,
  ToggleRow,
} from '@/components/viewer/settings/settings-ui';
import { DevBadge } from '@/components/viewer/dev-placeholder';
import {
  privacyStatusRows,
  privacyRetentionSummary,
} from '@/lib/settings-placeholders';

const STATE_LABEL: Record<string, string> = {
  local: 'Stays on this Agent',
  on: 'On',
  off: 'Off',
};

export function PrivacySection() {
  // Real: the Agent's outbound posture, when a session is available.
  const privacy = useViewerData<{ local_only: boolean }>('/privacy');
  const localOnly = privacy.data?.local_only;

  return (
    <>
      <SettingsGroup
        title="Where your data lives"
        description="Blue Ash Reel is local-first. The Portal only ever needs your account and this Agent's pairing record."
      >
        <ul className="divide-y divide-border/70 py-1">
          {privacyStatusRows.map((row) => {
            const isReal =
              localOnly === true &&
              (row.label === 'External metadata lookups' ||
                row.label === 'External artwork downloads');
            return (
              <li
                key={row.label}
                className="flex items-center justify-between gap-4 py-2.5 text-sm"
              >
                <span>{row.label}</span>
                <span className="inline-flex items-center gap-2">
                  <span
                    className={cn(
                      'size-2 rounded-full',
                      row.state === 'off'
                        ? 'bg-muted-foreground'
                        : row.state === 'on'
                          ? 'bg-primary'
                          : 'bg-[var(--success)]',
                    )}
                    aria-hidden="true"
                  />
                  <span className="text-xs font-medium">
                    {STATE_LABEL[row.state]}
                  </span>
                  {!isReal ? <DevBadge label="Preview" /> : null}
                </span>
              </li>
            );
          })}
        </ul>
        {localOnly === true ? (
          <p className="mt-2 inline-flex items-center gap-1.5 text-xs text-[var(--success-foreground)]">
            <ShieldCheck className="size-3.5" aria-hidden="true" />
            The Agent currently reports a local-only outbound posture.
          </p>
        ) : null}
      </SettingsGroup>

      <SettingsGroup
        title="Optional outbound features"
        description="All off by default. Turning any of these on would let the Agent make an outside request."
      >
        <ToggleRow
          id="external_metadata"
          label="External metadata access"
          description="Look up titles, cast and descriptions from an online source."
        />
        <ToggleRow
          id="external_artwork"
          label="External artwork access"
          description="Download posters and backdrops that are not already local."
        />
        <ToggleRow
          id="anonymous_diagnostics"
          label="Anonymous diagnostics"
          description="Non-identifying error and timing counts. No media names or paths."
        />
        <ToggleRow
          id="crash_reporting"
          label="Crash reporting"
          description="Send a technical report if the Agent crashes."
        />
        <ToggleRow
          id="usage_analytics"
          label="Usage analytics"
          description="Aggregate feature-usage counts."
        />
        <p className="pt-3 text-xs text-muted-foreground">
          <DevBadge label="Preview" /> These toggles do not open any connection in
          this build.
        </p>
      </SettingsGroup>

      <SettingsGroup title="Your local data">
        <SettingRow
          label="Data-retention summary"
          description={privacyRetentionSummary}
        >
          <span className="text-xs text-muted-foreground">Informational</span>
        </SettingRow>
        <SettingRow
          label="Clear local watch history"
          description="Removes resume points and watched marks stored on this Agent."
          disabledReason="This needs a confirmation step and backend support that are not built yet"
        >
          <Button
            variant="destructive"
            size="sm"
            disabled
            aria-label="Clear local watch history (coming later)"
          >
            <Trash2 aria-hidden="true" />
            Clear history
          </Button>
        </SettingRow>
        <SettingRow
          label="Export local data"
          description="Download a copy of your library metadata and watch history."
          disabledReason="Local data export is coming later"
        >
          <Button
            variant="outline"
            size="sm"
            disabled
            aria-label="Export local data (coming later)"
          >
            <Download aria-hidden="true" />
            Export
          </Button>
        </SettingRow>
      </SettingsGroup>
    </>
  );
}
