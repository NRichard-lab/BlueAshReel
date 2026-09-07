'use client';

import { useEffect, useState } from 'react';
import { Save, Undo2, Server, Info } from 'lucide-react';
import { Button } from '@/components/ui/button';
import {
  NativeSelect,
  NativeSelectOption,
} from '@/components/ui/native-select';
import { cn } from '@/lib/utils';
import { productConfig } from '@/lib/product-config';
import {
  SETTINGS_SECTIONS,
  type SettingsSectionId,
  agentHeaderPlaceholders,
} from '@/lib/settings-placeholders';
import {
  SettingsProvider,
  useSettingsForm,
  StatusPill,
} from '@/components/viewer/settings/settings-ui';
import { DevBadge } from '@/components/viewer/dev-placeholder';
import { GeneralSection } from '@/components/viewer/settings/section-general';
import { LibrariesSection } from '@/components/viewer/settings/section-libraries';
import { RemoteAccessSection } from '@/components/viewer/settings/section-remote-access';
import { TranscodingSection } from '@/components/viewer/settings/section-transcoding';
import { NetworkSection } from '@/components/viewer/settings/section-network';
import { UsersSection } from '@/components/viewer/settings/section-users';
import { PlayerSection } from '@/components/viewer/settings/section-player';
import { DownloadsSection } from '@/components/viewer/settings/section-downloads';
import { PrivacySection } from '@/components/viewer/settings/section-privacy';
import { SystemSection } from '@/components/viewer/settings/section-system';
import { AboutSection } from '@/components/viewer/settings/section-about';

const SECTION_COMPONENTS: Record<SettingsSectionId, () => React.ReactNode> = {
  general: GeneralSection,
  libraries: LibrariesSection,
  'remote-access': RemoteAccessSection,
  transcoding: TranscodingSection,
  network: NetworkSection,
  users: UsersSection,
  player: PlayerSection,
  downloads: DownloadsSection,
  privacy: PrivacySection,
  system: SystemSection,
  about: AboutSection,
};

const IDS = SETTINGS_SECTIONS.map((s) => s.id) as SettingsSectionId[];

function sectionFromHash(): SettingsSectionId {
  if (typeof window === 'undefined') return 'general';
  const raw = window.location.hash.replace(/^#/, '');
  return (IDS as string[]).includes(raw)
    ? (raw as SettingsSectionId)
    : 'general';
}

export function ViewerSettings() {
  return (
    <SettingsProvider>
      <SettingsWorkspace />
    </SettingsProvider>
  );
}

function SettingsWorkspace() {
  const [active, setActive] = useState<SettingsSectionId>('general');
  const { reset, dirty } = useSettingsForm();

  useEffect(() => {
    setActive(sectionFromHash());
    const onHash = () => setActive(sectionFromHash());
    window.addEventListener('hashchange', onHash);
    return () => window.removeEventListener('hashchange', onHash);
  }, []);

  function go(id: SettingsSectionId) {
    setActive(id);
    if (typeof window !== 'undefined') {
      window.history.replaceState(null, '', `#${id}`);
    }
  }

  const ActiveSection = SECTION_COMPONENTS[active];
  const activeLabel = SETTINGS_SECTIONS.find((s) => s.id === active)?.label;

  return (
    <div>
      {/* Persistent, non-dev-gated: describes the (absent) save behaviour. */}
      <output className="mb-5 block rounded-xl border border-amber-500/40 bg-amber-500/10 px-4 py-2.5 text-sm text-amber-500">
        Design preview — settings are not saved. You can change controls to
        evaluate them; refreshing the page restores the defaults.
      </output>

      <SettingsHeader dirty={dirty} onDiscard={reset} />

      <div className="mt-7 grid gap-6 lg:grid-cols-[220px_minmax(0,1fr)]">
        {/* Mobile / tablet: dropdown category selector */}
        <div className="lg:hidden">
          <label htmlFor="settings-section-select" className="sr-only">
            Settings category
          </label>
          <NativeSelect
            id="settings-section-select"
            className="w-full"
            value={active}
            onChange={(e) => go(e.target.value as SettingsSectionId)}
          >
            {SETTINGS_SECTIONS.map((s) => (
              <NativeSelectOption key={s.id} value={s.id}>
                {s.label}
              </NativeSelectOption>
            ))}
          </NativeSelect>
        </div>

        {/* Desktop: sticky sidebar */}
        <nav
          aria-label="Settings categories"
          className="hidden lg:block"
        >
          <ul className="sticky top-24 space-y-0.5">
            {SETTINGS_SECTIONS.map((s) => {
              const isActive = s.id === active;
              return (
                <li key={s.id}>
                  <a
                    href={`#${s.id}`}
                    aria-current={isActive ? 'page' : undefined}
                    onClick={(e) => {
                      e.preventDefault();
                      go(s.id);
                    }}
                    className={cn(
                      'block rounded-lg px-3 py-2 text-sm outline-none transition-colors focus-visible:ring-2 focus-visible:ring-ring',
                      isActive
                        ? 'bg-primary/12 font-medium text-primary'
                        : 'text-muted-foreground hover:bg-muted hover:text-foreground',
                    )}
                  >
                    {s.label}
                  </a>
                </li>
              );
            })}
          </ul>
        </nav>

        <div className="min-w-0">
          <h2 className="mb-4 text-lg font-semibold tracking-tight lg:sr-only">
            {activeLabel}
          </h2>
          <div className="space-y-6">
            <ActiveSection />
          </div>
        </div>
      </div>
    </div>
  );
}

function SettingsHeader({
  dirty,
  onDiscard,
}: {
  dirty: boolean;
  onDiscard: () => void;
}) {
  return (
    <header className="rounded-2xl border border-border bg-card/60 p-5 md:p-6">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0">
          <p className="text-xs font-semibold uppercase tracking-[0.16em] text-primary">
            {productConfig.name}
          </p>
          <h1 className="mt-1 text-2xl font-semibold tracking-tight md:text-3xl">
            Settings
          </h1>
          <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-2 text-sm text-muted-foreground">
            <span className="inline-flex items-center gap-1.5">
              <Server className="size-3.5" aria-hidden="true" />
              {agentHeaderPlaceholders.friendlyName}
              <DevBadge label="Preview" />
            </span>
            <span className="inline-flex items-center gap-1.5">
              <StatusPill status={agentHeaderPlaceholders.connectionStatus} />
              <DevBadge label="Preview" />
            </span>
            <span>
              Agent version{' '}
              <span className="text-foreground">{productConfig.version}</span>
              <span className="ml-1 text-xs">(development channel)</span>
            </span>
          </div>
        </div>

        <div className="flex flex-col items-end gap-2">
          <div className="flex items-center gap-2">
            <Button
              variant="outline"
              onClick={onDiscard}
              disabled={!dirty}
              aria-label="Discard changes"
            >
              <Undo2 aria-hidden="true" />
              Discard Changes
            </Button>
            <Button
              disabled
              title="Saving settings to the Agent is not available yet"
              aria-label="Save changes (unavailable)"
            >
              <Save aria-hidden="true" />
              Save Changes
            </Button>
          </div>
          <span className="text-[11px] text-muted-foreground">
            Save Changes — backend connection coming later
          </span>
          <span
            className="inline-flex items-center gap-1.5 rounded-full border border-border/70 px-2.5 py-1 text-[11px] text-muted-foreground"
            aria-live="polite"
          >
            <span className="size-1.5 rounded-full bg-muted-foreground" aria-hidden="true" />
            {dirty ? 'Some changes may require a restart' : 'No restart pending'}
            <DevBadge label="Preview" />
          </span>
        </div>
      </div>

      <p className="mt-4 flex items-start gap-2 border-t border-border/60 pt-4 text-xs text-muted-foreground">
        <Info className="mt-0.5 size-3.5 shrink-0" aria-hidden="true" />
        Most settings are stored on this local Agent. Your account and pairing
        record live with the Portal; media, library data and watch history stay
        on this workstation.
      </p>
    </header>
  );
}
