'use client';

/**
 * Shared building blocks for the Agent Settings workspace.
 *
 * Everything here is presentation-only. Form values live in React state via
 * <SettingsProvider>; there is no persistence, no localStorage, and no network
 * call. `Discard` resets to the defaults in lib/settings-placeholders.ts. When a
 * real settings API exists, replace the provider's initial state + the (absent)
 * save path — the section components and these primitives stay the same.
 */

import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useState,
  type ReactNode,
} from 'react';
import { ChevronDown, Lock } from 'lucide-react';
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from '@/components/ui/collapsible';
import { Switch } from '@/components/ui/switch';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import {
  NativeSelect,
  NativeSelectOption,
} from '@/components/ui/native-select';
import { cn } from '@/lib/utils';
import { settingsDefaults } from '@/lib/settings-placeholders';

/* --------------------------------- context -------------------------------- */

type SettingValue = string | boolean | number;

interface SettingsCtx {
  values: Record<string, SettingValue>;
  setValue: (id: string, value: SettingValue) => void;
  reset: () => void;
  dirty: boolean;
}

const Ctx = createContext<SettingsCtx | null>(null);

export function SettingsProvider({ children }: { children: ReactNode }) {
  const [values, setValues] = useState<Record<string, SettingValue>>(
    () => ({ ...settingsDefaults }),
  );
  const setValue = useCallback((id: string, value: SettingValue) => {
    setValues((prev) => ({ ...prev, [id]: value }));
  }, []);
  const reset = useCallback(() => setValues({ ...settingsDefaults }), []);
  const dirty = useMemo(
    () =>
      Object.keys(settingsDefaults).some(
        (k) => values[k] !== settingsDefaults[k],
      ),
    [values],
  );
  const ctx = useMemo(
    () => ({ values, setValue, reset, dirty }),
    [values, setValue, reset, dirty],
  );
  return <Ctx.Provider value={ctx}>{children}</Ctx.Provider>;
}

export function useSettingsForm(): Pick<SettingsCtx, 'reset' | 'dirty'> {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error('useSettingsForm must be used within SettingsProvider');
  return { reset: ctx.reset, dirty: ctx.dirty };
}

export function useSetting(
  id: string,
): [SettingValue, (value: SettingValue) => void] {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error('useSetting must be used within SettingsProvider');
  return [ctx.values[id] ?? settingsDefaults[id], (v) => ctx.setValue(id, v)];
}

/* ------------------------------- containers ------------------------------- */

export function SettingsGroup({
  title,
  description,
  children,
  className,
}: {
  title: string;
  description?: string;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section
      className={cn(
        'rounded-2xl border border-border bg-card/60 p-5 md:p-6',
        className,
      )}
    >
      <h3 className="text-base font-semibold tracking-tight">{title}</h3>
      {description ? (
        <p className="mt-1 text-sm text-muted-foreground">{description}</p>
      ) : null}
      <div className="mt-4 divide-y divide-border/70">{children}</div>
    </section>
  );
}

/** One labelled control. `disabledReason` renders a lock + explanation and is
 *  required whenever `disabled` is set (accessibility: never disable silently). */
export function SettingRow({
  label,
  htmlFor,
  description,
  children,
  disabledReason,
  className,
}: {
  label: string;
  htmlFor?: string;
  description?: string;
  children: ReactNode;
  disabledReason?: string;
  className?: string;
}) {
  return (
    <div
      className={cn(
        'flex flex-col gap-2 py-4 first:pt-0 last:pb-0 sm:flex-row sm:items-start sm:justify-between sm:gap-6',
        className,
      )}
    >
      <div className="min-w-0 sm:max-w-md">
        <label
          htmlFor={htmlFor}
          className="text-sm font-medium leading-snug"
        >
          {label}
        </label>
        {description ? (
          <p className="mt-1 text-xs leading-relaxed text-muted-foreground">
            {description}
          </p>
        ) : null}
        {disabledReason ? (
          <p className="mt-1.5 inline-flex items-center gap-1.5 rounded-md bg-muted/60 px-2 py-1 text-[11px] text-muted-foreground">
            <Lock className="size-3" aria-hidden="true" />
            {disabledReason}
          </p>
        ) : null}
      </div>
      <div className="shrink-0 sm:pt-0.5">{children}</div>
    </div>
  );
}

/* --------------------------------- inputs --------------------------------- */

export function ToggleRow({
  id,
  label,
  description,
  disabledReason,
}: {
  id: string;
  label: string;
  description?: string;
  disabledReason?: string;
}) {
  const [value, setValue] = useSetting(id);
  const checked = value === true;
  const disabled = !!disabledReason;
  return (
    <SettingRow
      label={label}
      htmlFor={id}
      description={description}
      disabledReason={disabledReason}
    >
      <span className="inline-flex items-center gap-2">
        <span
          className={cn(
            'w-7 text-right text-[11px] font-medium uppercase tracking-wide',
            checked ? 'text-primary' : 'text-muted-foreground',
          )}
          aria-hidden="true"
        >
          {checked ? 'On' : 'Off'}
        </span>
        <Switch
          id={id}
          checked={checked}
          disabled={disabled}
          onCheckedChange={(next: boolean) => setValue(next)}
          aria-label={label}
        />
      </span>
    </SettingRow>
  );
}

export function SelectRow({
  id,
  label,
  description,
  options,
  disabledReason,
}: {
  id: string;
  label: string;
  description?: string;
  options: { value: string; label: string }[];
  disabledReason?: string;
}) {
  const [value, setValue] = useSetting(id);
  return (
    <SettingRow
      label={label}
      htmlFor={id}
      description={description}
      disabledReason={disabledReason}
    >
      <NativeSelect
        id={id}
        className="min-w-[13rem]"
        value={String(value)}
        disabled={!!disabledReason}
        onChange={(e) => setValue(e.target.value)}
        aria-label={label}
      >
        {options.map((o) => (
          <NativeSelectOption key={o.value} value={o.value}>
            {o.label}
          </NativeSelectOption>
        ))}
      </NativeSelect>
    </SettingRow>
  );
}

export function TextRow({
  id,
  label,
  description,
  placeholder,
  type = 'text',
  disabledReason,
  widthClass = 'w-64',
}: {
  id: string;
  label: string;
  description?: string;
  placeholder?: string;
  type?: string;
  disabledReason?: string;
  widthClass?: string;
}) {
  const [value, setValue] = useSetting(id);
  return (
    <SettingRow
      label={label}
      htmlFor={id}
      description={description}
      disabledReason={disabledReason}
    >
      <Input
        id={id}
        type={type}
        className={widthClass}
        value={String(value)}
        placeholder={placeholder}
        disabled={!!disabledReason}
        onChange={(e) => setValue(e.target.value)}
        aria-label={label}
      />
    </SettingRow>
  );
}

export function TextareaRow({
  id,
  label,
  description,
  disabledReason,
}: {
  id: string;
  label: string;
  description?: string;
  disabledReason?: string;
}) {
  const [value, setValue] = useSetting(id);
  return (
    <SettingRow
      label={label}
      htmlFor={id}
      description={description}
      disabledReason={disabledReason}
      className="sm:flex-col sm:items-stretch"
    >
      <Textarea
        id={id}
        className="mt-1 w-full sm:w-[28rem]"
        rows={2}
        value={String(value)}
        disabled={!!disabledReason}
        onChange={(e) => setValue(e.target.value)}
        aria-label={label}
      />
    </SettingRow>
  );
}

/* ------------------------------- disclosure ------------------------------- */

export function AdvancedSection({
  children,
  label = 'Advanced options',
}: {
  children: ReactNode;
  label?: string;
}) {
  const [open, setOpen] = useState(false);
  return (
    <Collapsible open={open} onOpenChange={setOpen} className="mt-4">
      <CollapsibleTrigger
        className="flex w-full items-center justify-between rounded-lg border border-border bg-muted/40 px-4 py-2.5 text-sm font-medium outline-none hover:bg-muted focus-visible:ring-2 focus-visible:ring-ring"
        aria-expanded={open}
      >
        {label}
        <ChevronDown
          className={cn('size-4 transition-transform', open && 'rotate-180')}
          aria-hidden="true"
        />
      </CollapsibleTrigger>
      <CollapsibleContent className="pt-2">
        <div className="divide-y divide-border/70 rounded-lg border border-border/70 px-4">
          {children}
        </div>
      </CollapsibleContent>
    </Collapsible>
  );
}

/* --------------------------------- status -------------------------------- */

const STATUS_TONE: Record<string, string> = {
  Connected: 'bg-[var(--success)]',
  Direct: 'bg-[var(--success)]',
  On: 'bg-[var(--success)]',
  Available: 'bg-[var(--success)]',
  local: 'bg-[var(--success)]',
  Relay: 'bg-primary',
  Connecting: 'bg-amber-500',
  Offline: 'bg-destructive',
  Unavailable: 'bg-muted-foreground',
  Off: 'bg-muted-foreground',
};

export function StatusPill({ status }: { status: string }) {
  const tone = STATUS_TONE[status] ?? 'bg-muted-foreground';
  return (
    <span className="inline-flex items-center gap-1.5 rounded-full border border-border/70 px-2.5 py-1 text-xs font-medium">
      <span className={cn('size-2 rounded-full', tone)} aria-hidden="true" />
      {status}
    </span>
  );
}

/** Read-only value with a "Preview" tag so it is never mistaken for live data. */
export function PreviewValue({
  label,
  value,
  hint,
}: {
  label: string;
  value: ReactNode;
  hint?: string;
}) {
  return (
    <div className="flex flex-col gap-1 rounded-xl border border-border bg-background/40 p-4">
      <span className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
        {label}
      </span>
      <span className="text-sm">{value}</span>
      {hint ? (
        <span className="text-[11px] text-muted-foreground">{hint}</span>
      ) : null}
    </div>
  );
}

export function PlannedBanner({
  children = 'Planned feature — not currently active',
}: {
  children?: ReactNode;
}) {
  return (
    <p className="mb-5 rounded-xl border border-amber-500/40 bg-amber-500/10 px-4 py-2.5 text-sm text-amber-500">
      {children}
    </p>
  );
}
