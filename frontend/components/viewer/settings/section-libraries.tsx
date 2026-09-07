'use client';

import { Plus, RefreshCw, Pencil, Trash2, FolderOpen, AlertTriangle } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Switch } from '@/components/ui/switch';
import { cn } from '@/lib/utils';
import { useViewerData } from '@/lib/viewer';
import {
  SettingsGroup,
  ToggleRow,
  SelectRow,
  AdvancedSection,
} from '@/components/viewer/settings/settings-ui';
import { DevBadge } from '@/components/viewer/dev-placeholder';
import { IS_DEV_PREVIEW } from '@/components/viewer/dev-preview';
import { librarySamples } from '@/lib/settings-placeholders';

const NOT_WIRED = 'Managing libraries from Settings is coming later';

interface RealLib {
  id: string;
  name: string;
}

export function LibrariesSection() {
  const result = useViewerData<{ items: RealLib[]; total: number }>(
    '/browse/libraries?page=1',
  );
  const devFallback = IS_DEV_PREVIEW && !!result.error;
  const realLibs = result.data?.items;

  return (
    <>
      <SettingsGroup
        title="Your libraries"
        description="The libraries this Agent scans and serves. Names come from the Agent; scan status shown here is a placeholder until Settings is wired for library management."
      >
        <div className="flex items-center justify-between py-3 first:pt-0">
          <p className="text-sm text-muted-foreground">
            {realLibs
              ? `${realLibs.length} librar${realLibs.length === 1 ? 'y' : 'ies'} on this Agent`
              : devFallback
                ? `${librarySamples.length} libraries (sample)`
                : 'Loading libraries…'}
          </p>
          <Button
            variant="outline"
            size="sm"
            disabled
            title={NOT_WIRED}
            aria-label="Add library (coming later)"
          >
            <Plus aria-hidden="true" />
            Add Library
          </Button>
        </div>

        <div className="space-y-3 py-3">
          {realLibs?.map((lib) => (
            <LibraryRow key={lib.id} name={lib.name} real />
          ))}
          {!realLibs && devFallback
            ? librarySamples.map((lib) => (
                <LibraryRow
                  key={lib.id}
                  name={lib.name}
                  type={lib.type}
                  enabled={lib.enabled}
                  itemCount={lib.itemCount}
                  lastScan={lib.lastScan}
                  status={lib.status}
                  scanProgress={lib.scanProgress}
                />
              ))
            : null}
          {!realLibs && !devFallback && !result.error ? (
            <p className="rounded-lg border border-dashed border-border/70 p-4 text-sm text-muted-foreground">
              Loading…
            </p>
          ) : null}
          {result.error && !devFallback ? (
            <p className="rounded-lg border border-dashed border-border/70 p-4 text-sm text-muted-foreground">
              Library list is unavailable right now.
            </p>
          ) : null}
        </div>
      </SettingsGroup>

      <SettingsGroup
        title="Scanning"
        description="When the Agent looks for new, changed or removed files."
      >
        <ToggleRow
          id="auto_scan"
          label="Automatic scanning"
          description="Scan libraries in the background on a schedule."
          disabledReason={NOT_WIRED}
        />
        <SelectRow
          id="scheduled_scan"
          label="Scheduled scanning"
          description="How often a full scan runs."
          options={[
            { value: 'off', label: 'Off' },
            { value: 'hourly', label: 'Every hour' },
            { value: 'every-6h', label: 'Every 6 hours' },
            { value: 'daily-3am', label: 'Daily at 3:00 AM' },
            { value: 'weekly', label: 'Weekly' },
          ]}
          disabledReason={NOT_WIRED}
        />
        <ToggleRow
          id="scan_on_change"
          label="Scan when changes are detected"
          description="Watch library folders and scan affected items automatically."
          disabledReason={NOT_WIRED}
        />
        <ToggleRow
          id="empty_trash_after_scan"
          label="Empty trash after scan"
          description="Permanently remove entries for files that are no longer present."
          disabledReason={NOT_WIRED}
        />
        <AdvancedSection label="Analysis during scan">
          <ToggleRow
            id="generate_video_thumbs"
            label="Generate video preview thumbnails"
            description="Creates the small hover previews on the scrub bar. Uses CPU and disk."
            disabledReason={NOT_WIRED}
          />
          <ToggleRow
            id="generate_chapter_thumbs"
            label="Generate chapter thumbnails"
            description="One image per chapter marker."
            disabledReason={NOT_WIRED}
          />
          <ToggleRow
            id="analyze_audio"
            label="Analyze audio tracks"
            description="Detects languages, channel layouts and codecs for track selection."
            disabledReason={NOT_WIRED}
          />
          <ToggleRow
            id="analyze_subtitles"
            label="Analyze subtitle tracks"
            description="Detects embedded subtitle languages and formats."
            disabledReason={NOT_WIRED}
          />
        </AdvancedSection>
      </SettingsGroup>
    </>
  );
}

function LibraryRow({
  name,
  type,
  enabled = true,
  itemCount,
  lastScan,
  status = 'ok',
  scanProgress = 0,
  real = false,
}: {
  name: string;
  type?: string;
  enabled?: boolean;
  itemCount?: number;
  lastScan?: string;
  status?: 'ok' | 'scanning' | 'error';
  scanProgress?: number;
  real?: boolean;
}) {
  return (
    <div className="rounded-xl border border-border bg-background/40 p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <FolderOpen className="size-4 text-primary/70" aria-hidden="true" />
            <span className="font-medium">{name}</span>
            {real ? (
              <span className="rounded-full border border-border px-1.5 py-0.5 text-[10px] uppercase tracking-wide text-muted-foreground">
                On this Agent
              </span>
            ) : null}
          </div>
          <p className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted-foreground">
            <span>{type ?? 'Type unknown'}</span>
            <span aria-hidden="true">·</span>
            <span>
              {itemCount != null ? `${itemCount} items` : 'Item count not shown here'}
            </span>
            <span aria-hidden="true">·</span>
            <span>Last successful scan: {lastScan ?? 'Not shown here'}</span>
            {!real ? <DevBadge label="Sample" /> : <DevBadge label="Preview" />}
          </p>
        </div>

        <div className="flex items-center gap-2">
          <span
            className={cn(
              'w-8 text-right text-[11px] font-medium uppercase',
              enabled ? 'text-primary' : 'text-muted-foreground',
            )}
            aria-hidden="true"
          >
            {enabled ? 'On' : 'Off'}
          </span>
          <Switch
            checked={enabled}
            disabled
            aria-label={`Enable ${name} (coming later)`}
          />
        </div>
      </div>

      {status === 'scanning' ? (
        <div className="mt-3">
          <div className="flex items-center justify-between text-xs text-muted-foreground">
            <span>Scanning…</span>
            <span>{scanProgress}%</span>
          </div>
          <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-muted">
            <div
              className="h-full bg-primary"
              style={{ width: `${scanProgress}%` }}
            />
          </div>
        </div>
      ) : null}

      {status === 'error' ? (
        <p className="mt-3 inline-flex items-center gap-1.5 rounded-md bg-destructive/10 px-2 py-1 text-xs text-destructive">
          <AlertTriangle className="size-3.5" aria-hidden="true" />
          Last scan reported errors. Details will appear here once library
          management is connected.
        </p>
      ) : null}

      <div className="mt-3 flex flex-wrap gap-2">
        <Button
          variant="outline"
          size="sm"
          disabled
          title={NOT_WIRED}
          aria-label={`Scan ${name} (coming later)`}
        >
          <RefreshCw aria-hidden="true" />
          Scan
        </Button>
        <Button
          variant="outline"
          size="sm"
          disabled
          title={NOT_WIRED}
          aria-label={`Edit ${name} (coming later)`}
        >
          <Pencil aria-hidden="true" />
          Edit
        </Button>
        <Button
          variant="destructive"
          size="sm"
          disabled
          title="Removing a library needs a confirmation step and backend support that are not built yet"
          aria-label={`Remove ${name} (coming later)`}
        >
          <Trash2 aria-hidden="true" />
          Remove
        </Button>
      </div>
    </div>
  );
}
