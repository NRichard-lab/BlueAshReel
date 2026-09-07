'use client';

import {
  Archive,
  CheckCircle2,
  RefreshCw,
  ScrollText,
  LifeBuoy,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { productConfig } from '@/lib/product-config';
import { SettingsGroup, SettingRow } from '@/components/viewer/settings/settings-ui';
import { DevBadge } from '@/components/viewer/dev-placeholder';
import { systemPlaceholders as s } from '@/lib/settings-placeholders';

function StatCard({
  label,
  value,
  real = false,
}: {
  label: string;
  value: string;
  real?: boolean;
}) {
  return (
    <div className="rounded-xl border border-border bg-background/40 p-4">
      <p className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
        {label}
      </p>
      <p className="mt-1 text-sm">{value}</p>
      {!real ? (
        <p className="mt-1">
          <DevBadge label="Not connected" />
        </p>
      ) : null}
    </div>
  );
}

export function SystemSection() {
  return (
    <>
      <SettingsGroup
        title="Agent"
        description="Live values will populate once Settings can read Agent status."
      >
        <div className="grid gap-3 py-3 first:pt-0 sm:grid-cols-2 lg:grid-cols-3">
          <StatCard label="Agent status" value={s.agentStatus} />
          <StatCard label="Uptime" value={s.uptime} />
          <StatCard label="Agent version" value={productConfig.version} real />
          <StatCard label="Operating system" value={s.os} />
          <StatCard label="CPU" value={s.cpu} />
          <StatCard label="Memory" value={s.memory} />
          <StatCard label="GPU" value={s.gpu} />
        </div>
      </SettingsGroup>

      <SettingsGroup title="Storage">
        <div className="grid gap-3 py-3 first:pt-0 sm:grid-cols-2 lg:grid-cols-3">
          <StatCard label="Local database" value={s.databaseStatus} />
          <StatCard label="Database size" value={s.databaseSize} />
          <StatCard label="Artwork cache size" value={s.artworkCacheSize} />
          <StatCard label="Temporary transcode usage" value={s.tempTranscodeUsage} />
        </div>
      </SettingsGroup>

      <SettingsGroup title="Activity">
        <div className="grid gap-3 py-3 first:pt-0 sm:grid-cols-2">
          <StatCard label="Active streams" value={s.activeStreams} />
          <StatCard label="Current transcodes" value={s.currentTranscodes} />
        </div>
      </SettingsGroup>

      <SettingsGroup
        title="Backup"
        description="Backups include the local database and configuration."
      >
        <div className="grid gap-3 py-3 first:pt-0 sm:grid-cols-2">
          <StatCard label="Last backup" value={s.lastBackup} />
          <StatCard label="Backup location" value={s.backupLocation} />
        </div>
        <SettingRow
          label="Create backup"
          disabledReason="Backup actions from Settings are coming later"
        >
          <Button variant="outline" size="sm" disabled aria-label="Create backup (coming later)">
            <Archive aria-hidden="true" />
            Create Backup
          </Button>
        </SettingRow>
        <SettingRow
          label="Validate backup"
          description="Confirms a backup can be restored without touching live data."
          disabledReason="Backup actions from Settings are coming later"
        >
          <Button variant="outline" size="sm" disabled aria-label="Validate backup (coming later)">
            <CheckCircle2 aria-hidden="true" />
            Validate Backup
          </Button>
        </SettingRow>
      </SettingsGroup>

      <SettingsGroup title="Maintenance">
        <SettingRow
          label="Restart Agent"
          description="Stops and restarts the local Agent processes. Use the tray menu for now."
          disabledReason="Restart is only available from the Agent tray menu"
        >
          <Button
            variant="outline"
            size="sm"
            disabled
            aria-label="Restart Agent (use the tray menu)"
          >
            <RefreshCw aria-hidden="true" />
            Restart Agent
          </Button>
        </SettingRow>
        <SettingRow
          label="View logs"
          description="Opens the local, redacted Agent logs."
          disabledReason="Log viewing from Settings is coming later"
        >
          <Button variant="outline" size="sm" disabled aria-label="View logs (coming later)">
            <ScrollText aria-hidden="true" />
            View Logs
          </Button>
        </SettingRow>
        <SettingRow
          label="Export diagnostic bundle"
          description="A redacted archive to attach to a support request."
          disabledReason="Diagnostic export is coming later"
        >
          <Button
            variant="outline"
            size="sm"
            disabled
            aria-label="Export diagnostic bundle (coming later)"
          >
            <LifeBuoy aria-hidden="true" />
            Export bundle
          </Button>
        </SettingRow>
      </SettingsGroup>
    </>
  );
}
