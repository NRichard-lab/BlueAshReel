'use client';

import { useEffect, useState } from 'react';

import {
  BriefcaseBusiness,
  Film,
  HeartPulse,
  House,
  LibraryBig,
  LockKeyhole,
  LogOut,
  MonitorPlay,
  Settings,
  ShieldCheck,
  Users,
} from 'lucide-react';
import Link from 'next/link';

import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { isOwnerOnlyPath } from '@/lib/admin-routes';
import { apiRequest, CurrentUser } from '@/lib/api';
import { productConfig } from '@/lib/product-config';

const navigation = [
  { label: 'Collection', href: '/', icon: Film },
  { label: 'Dashboard', href: '/admin', icon: House },
  { label: 'Household users', href: '/users', icon: Users },
  { label: 'Libraries', href: '/libraries', icon: LibraryBig },
  { label: 'Media', href: '/media', icon: MonitorPlay },
  { label: 'Background jobs', href: '/jobs', icon: BriefcaseBusiness },
  { label: 'System health', href: '/health', icon: HeartPulse },
];

const secondary = [
  { label: 'Active streams', href: '/streams', icon: MonitorPlay },
  { label: 'Settings', href: '/admin/settings', icon: Settings },
  { label: 'Privacy', href: '/privacy', icon: ShieldCheck },
];

interface PrivacyPosture {
  local_only: boolean;
  runtime_outbound_allowed: boolean;
  integrations: Record<string, boolean>;
}

export function AppShell({
  currentPath,
  children,
}: {
  currentPath: string;
  children: React.ReactNode;
}) {
  const [privacy, setPrivacy] = useState<PrivacyPosture>();
  const [logoutError, setLogoutError] = useState<string>();
  const [serverOnline, setServerOnline] = useState<boolean>();
  const [user, setUser] = useState<CurrentUser>();
  const [accessError, setAccessError] = useState('');

  useEffect(() => {
    apiRequest<CurrentUser>('/auth/me').then(setUser).catch(() => setAccessError('Sign in to access administration.'));
    const checkPrivacy = () => apiRequest<PrivacyPosture>('/privacy').then(setPrivacy).catch(() => setPrivacy(undefined));
    void checkPrivacy();
    const updatePrivacy = (event: Event) => {
      const next = (event as CustomEvent<PrivacyPosture>).detail;
      if (next) setPrivacy(next);
    };
    window.addEventListener('privacy-posture-changed', updatePrivacy);
    const checkServer = () => apiRequest<{ status: string }>('/health/live')
      .then((result) => setServerOnline(result.status === 'ok'))
      .catch(() => setServerOnline(false));
    void checkServer();
    const timer = window.setInterval(() => { void checkServer(); void checkPrivacy(); }, 15000);
    return () => {
      window.clearInterval(timer);
      window.removeEventListener('privacy-posture-changed', updatePrivacy);
    };
  }, []);

  const outboundActive = Boolean(privacy && Object.values(privacy.integrations).some(Boolean));
  const owner = user?.roles.includes('Owner');
  const allowed = owner || user?.roles.includes('Administrator');
  const shownSecondary = owner ? secondary : [];
  const ownerOnlyPath = isOwnerOnlyPath(currentPath);

  const logout = async () => {
    setLogoutError(undefined);
    try {
      await apiRequest('/auth/logout', { method: 'POST' });
      window.location.assign('/login');
    } catch (caught: unknown) {
      setLogoutError(caught instanceof Error ? caught.message : 'The local session could not be closed.');
    }
  };

  if (!user) return <main className="p-10"><output>{accessError || 'Checking administration access…'}</output>{accessError && <Link href="/login">Sign in</Link>}</main>;
  if (!allowed || (!owner && ownerOnlyPath)) return <main className="p-10"><h1>Administration access required</h1><Link href="/">Return to your collection</Link></main>;
  return (
    <main className="min-h-screen bg-background text-foreground">
      <div className="mx-auto grid min-h-screen max-w-[1680px] lg:grid-cols-[248px_minmax(0,1fr)]">
        <aside className="hidden border-r border-sidebar-border bg-sidebar px-5 py-6 lg:flex lg:flex-col">
          <Link href="/" className="flex items-center gap-3 rounded-lg px-2">
            <div className="grid size-10 place-items-center rounded-xl bg-primary text-primary-foreground shadow-sm">
              <Film className="size-5" aria-hidden="true" />
            </div>
            <div className="min-w-0">
              <p className="truncate text-base font-semibold tracking-[-0.02em]">{productConfig.name}</p>
              <p className="truncate text-[11px] text-muted-foreground">{productConfig.subtitle}</p>
            </div>
          </Link>

          <nav aria-label="Primary" className="mt-9 space-y-1">
            {navigation.map(({ label, href, icon: Icon }) => {
              const active = href === '/' ? currentPath === href : currentPath.startsWith(href);
              return (
                <Link
                  key={href}
                  href={href}
                  aria-current={active ? 'page' : undefined}
                  className={`flex items-center gap-3 rounded-lg px-3 py-2.5 text-sm font-medium transition-colors ${
                    active
                      ? 'bg-sidebar-primary text-sidebar-primary-foreground shadow-sm'
                      : 'text-sidebar-foreground/70 hover:bg-sidebar-accent hover:text-sidebar-accent-foreground'
                  }`}
                >
                  <Icon className="size-4" aria-hidden="true" />
                  {label}
                </Link>
              );
            })}
          </nav>

          <div className="mt-auto space-y-4">
            <div className="rounded-xl border border-sidebar-border bg-background/80 p-4">
              <div className="flex items-center gap-2 text-xs font-semibold">
                <ShieldCheck className="size-4 text-[var(--success)]" aria-hidden="true" />
                Private by design
              </div>
              <p className="mt-2 text-xs leading-relaxed text-muted-foreground">
                {!privacy ? 'Privacy status unavailable. Check the server connection.' : outboundActive
                  ? 'Only Owner-approved integrations may connect outward. Core media stays on this server.'
                  : privacy?.runtime_outbound_allowed
                    ? 'The runtime gate is open, but no Owner-approved integration is active.'
                    : 'Media-server integrations are locked off. Optional connection diagnostics are managed in Remote Access.'}
              </p>
            </div>
            <nav aria-label="Configuration" className="space-y-1">
              {shownSecondary.map(({ label, href, icon: Icon }) => {
                const active = currentPath.startsWith(href);
                return (
                  <Link
                    key={href}
                    href={href}
                    aria-current={active ? 'page' : undefined}
                    className={`flex items-center gap-3 rounded-lg px-3 py-2 text-sm transition-colors ${
                      active ? 'bg-sidebar-accent text-sidebar-accent-foreground' : 'text-muted-foreground hover:text-foreground'
                    }`}
                  >
                    <Icon className="size-4" aria-hidden="true" />
                    {label}
                  </Link>
                );
              })}
            </nav>
          </div>
        </aside>

        <section className="min-w-0">
          <header className="sticky top-0 z-20 border-b bg-background/92 backdrop-blur">
            <div className="flex h-16 items-center justify-between px-4 md:px-8">
              <Link href="/" className="flex items-center gap-3 lg:hidden">
                <span className="grid size-9 place-items-center rounded-lg bg-primary text-primary-foreground">
                  <Film className="size-4" aria-hidden="true" />
                </span>
                <span className="font-semibold">{productConfig.name}</span>
              </Link>
              <div className="hidden items-center gap-2 text-xs text-muted-foreground lg:flex">
                <span className={`size-2 rounded-full ${serverOnline === true ? 'bg-[var(--success)] shadow-[0_0_0_4px_var(--success-soft)]' : serverOnline === false ? 'bg-destructive' : 'animate-pulse bg-muted-foreground/50'}`} />
                {serverOnline === true ? 'Server online' : serverOnline === false ? 'Server unavailable' : 'Checking server'} <span aria-hidden="true">·</span> Local network
              </div>
              <div className="flex items-center gap-2">
                <Badge variant="outline" className="hidden border-[var(--success-border)] bg-[var(--success-soft)] text-[var(--success-foreground)] sm:inline-flex">
                  <LockKeyhole data-icon="inline-start" /> {!privacy ? 'Privacy unknown' : outboundActive ? 'Outbound approved' : privacy.runtime_outbound_allowed ? 'Outbound gate open' : 'Media stays local'}
                </Badge>
                <Button type="button" variant="ghost" size="icon" aria-label="Log out" onClick={logout}>
                  <LogOut />
                </Button>
              </div>
            </div>
            <nav aria-label="Mobile navigation" className="flex gap-1 overflow-x-auto px-3 pb-2 lg:hidden">
              {[...navigation, ...shownSecondary].map(({ label, href }) => {
                const active = href === '/' ? currentPath === href : currentPath.startsWith(href);
                return (
                  <Link
                    key={href}
                    href={href}
                    aria-current={active ? 'page' : undefined}
                    className={`shrink-0 rounded-md px-3 py-1.5 text-xs font-medium ${active ? 'bg-primary text-primary-foreground' : 'text-muted-foreground'}`}
                  >
                    {label}
                  </Link>
                );
              })}
            </nav>
          </header>
          <div className="mx-auto max-w-[1400px] px-4 py-7 md:px-8 md:py-9">
            {logoutError ? <Alert variant="destructive" className="mb-5"><AlertTitle>Logout did not complete</AlertTitle><AlertDescription>{logoutError} Your current session remains active.</AlertDescription></Alert> : null}
            {children}
          </div>
        </section>
      </div>
    </main>
  );
}
