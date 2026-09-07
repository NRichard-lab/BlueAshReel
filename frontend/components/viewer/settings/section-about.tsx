'use client';

import { Film, ExternalLink, RefreshCw } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { productConfig } from '@/lib/product-config';
import { SettingsGroup, SettingRow } from '@/components/viewer/settings/settings-ui';
import { DevBadge } from '@/components/viewer/dev-placeholder';
import {
  aboutPlaceholders as a,
  remoteAccessPlaceholders,
} from '@/lib/settings-placeholders';

export function AboutSection() {
  return (
    <>
      <SettingsGroup title={`About ${productConfig.name}`}>
        <div className="flex items-center gap-4 py-3 first:pt-0">
          <span className="grid size-12 shrink-0 place-items-center rounded-2xl bg-primary text-primary-foreground">
            <Film className="size-6" aria-hidden="true" />
          </span>
          <div>
            <p className="text-lg font-semibold tracking-tight">
              {productConfig.name}
            </p>
            <p className="text-sm text-muted-foreground">
              {productConfig.subtitle}
            </p>
          </div>
        </div>

        <div className="grid gap-3 py-3 sm:grid-cols-2">
          <div className="rounded-xl border border-border bg-background/40 p-4">
            <p className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
              Installed version
            </p>
            <p className="mt-1 text-sm">
              {productConfig.version}
              <span className="ml-1 text-xs text-muted-foreground">
                (development channel)
              </span>
            </p>
          </div>
          <div className="rounded-xl border border-border bg-background/40 p-4">
            <p className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
              Agent identifier
            </p>
            <p className="mt-1 text-sm">
              {remoteAccessPlaceholders.agentIdShort}
              <DevBadge label="Preview" />
            </p>
          </div>
          <div className="rounded-xl border border-border bg-background/40 p-4">
            <p className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
              Portal
            </p>
            <a
              href={`https://${productConfig.domain}`}
              className="mt-1 inline-flex items-center gap-1 text-sm text-primary hover:underline"
            >
              {productConfig.domain}
              <ExternalLink className="size-3.5" aria-hidden="true" />
            </a>
          </div>
        </div>

        <p className="rounded-xl border border-amber-500/40 bg-amber-500/10 px-4 py-2.5 text-sm text-amber-500">
          {a.channelWarning}
        </p>
      </SettingsGroup>

      <SettingsGroup title="Licenses & attribution">
        <SettingRow label="License">
          <span className="max-w-md text-xs text-muted-foreground">
            {a.license}
          </span>
        </SettingRow>
        <SettingRow label="FFmpeg">
          <span className="max-w-md text-xs text-muted-foreground">
            {a.ffmpegAttribution}
          </span>
        </SettingRow>
        <SettingRow
          label="Open-source notices"
          description="The full third-party license list bundled with the Agent."
          disabledReason="The notices viewer is coming later"
        >
          <Button
            variant="outline"
            size="sm"
            disabled
            aria-label="View open-source notices (coming later)"
          >
            View notices
          </Button>
        </SettingRow>
      </SettingsGroup>

      <SettingsGroup title="Help">
        <SettingRow
          label="Privacy overview"
          description="How Blue Ash Reel keeps media, metadata and history local."
        >
          <a href="#privacy" className="text-sm text-primary hover:underline">
            Open Privacy settings
          </a>
        </SettingRow>
        <SettingRow
          label="Check for updates"
          disabledReason="Update checks from Settings are coming later"
        >
          <Button
            variant="outline"
            size="sm"
            disabled
            aria-label="Check for updates (coming later)"
          >
            <RefreshCw aria-hidden="true" />
            Check now
          </Button>
        </SettingRow>
        <SettingRow
          label="Documentation"
          disabledReason="Documentation links are placeholders in this build"
        >
          <span className="text-xs text-muted-foreground">Coming later</span>
        </SettingRow>
        <SettingRow
          label="Support"
          disabledReason="Support links are placeholders in this build"
        >
          <span className="text-xs text-muted-foreground">Coming later</span>
        </SettingRow>
      </SettingsGroup>
    </>
  );
}
