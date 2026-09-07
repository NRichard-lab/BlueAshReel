'use client';

import { useEffect, useState } from 'react';
import Link from 'next/link';
import {
  Film,
  Search,
  LockKeyhole,
  LogOut,
  User,
  ShieldCheck,
} from 'lucide-react';
import { Button, buttonVariants } from '@/components/ui/button';
import { Avatar, AvatarFallback } from '@/components/ui/avatar';
import {
  DropdownMenu,
  DropdownMenuTrigger,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
} from '@/components/ui/dropdown-menu';
import { cn } from '@/lib/utils';
import { apiRequest, CurrentUser } from '@/lib/api';
import { productConfig } from '@/lib/product-config';

/** Primary viewing navigation. Administrative pages are intentionally excluded
 *  here and reachable only from the account menu. */
const primaryNav: [label: string, href: string][] = [
  ['Home', '/'],
  ['Movies', '/movies'],
  ['TV Shows', '/shows'],
  ['Settings', '/settings'],
];

function isActive(currentPath: string, href: string): boolean {
  if (href === '/') return currentPath === '/';
  return currentPath === href || currentPath.startsWith(`${href}/`);
}

export function ViewerShell({
  children,
  currentPath,
}: {
  children: React.ReactNode;
  currentPath: string;
}) {
  const [user, setUser] = useState<CurrentUser>();
  const [localOnly, setLocalOnly] = useState<boolean | null>(null);
  const [error, setError] = useState('');

  useEffect(() => {
    apiRequest<CurrentUser>('/auth/me')
      .then(setUser)
      .catch(() => setError('Sign in to browse your libraries.'));
    const refreshPrivacy = () =>
      apiRequest<{ local_only: boolean }>('/privacy')
        .then((p) => setLocalOnly(p.local_only))
        .catch(() => setLocalOnly(null));
    void refreshPrivacy();
    const timer = window.setInterval(() => void refreshPrivacy(), 15000);
    window.addEventListener('focus', refreshPrivacy);
    window.addEventListener('privacy-posture-changed', refreshPrivacy);
    return () => {
      window.clearInterval(timer);
      window.removeEventListener('focus', refreshPrivacy);
      window.removeEventListener('privacy-posture-changed', refreshPrivacy);
    };
  }, []);

  async function logout() {
    try {
      await apiRequest('/auth/logout', { method: 'POST' });
      window.location.assign('/login');
    } catch {
      setError('Could not close your session. Please try again.');
    }
  }

  const isStaff = user?.roles.some((r) =>
    ['Owner', 'Administrator'].includes(r),
  );

  return (
    <div className="dark flex min-h-screen flex-col bg-background text-foreground">
      <a
        href="#collection"
        className="sr-only rounded-md bg-primary px-3 py-2 text-primary-foreground focus:not-sr-only focus:absolute focus:left-4 focus:top-4 focus:z-50"
      >
        Skip to content
      </a>

      <header className="sticky top-0 z-30 border-b border-border/70 bg-background/85 backdrop-blur supports-backdrop-filter:bg-background/70">
        <div className="mx-auto flex max-w-[1680px] items-center gap-3 px-5 py-3 md:px-10">
          <Link
            href="/"
            className="flex items-center gap-2.5 rounded-lg outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            <span className="grid size-9 place-items-center rounded-xl bg-primary text-primary-foreground shadow-sm">
              <Film className="size-5" aria-hidden="true" />
            </span>
            <span className="hidden leading-tight sm:block">
              <span className="block text-[15px] font-semibold tracking-tight">
                {productConfig.name}
              </span>
              <span className="block text-[11px] text-muted-foreground">
                {productConfig.subtitle}
              </span>
            </span>
          </Link>

          <nav
            aria-label="Primary"
            className="ml-1 flex min-w-0 flex-1 items-center gap-1 overflow-x-auto [scrollbar-width:none] md:ml-4"
          >
            {primaryNav.map(([label, href]) => {
              const active = isActive(currentPath, href);
              return (
                <Link
                  key={href}
                  href={href}
                  aria-current={active ? 'page' : undefined}
                  className={cn(
                    'shrink-0 rounded-lg px-3 py-2 text-sm font-medium outline-none transition-colors focus-visible:ring-2 focus-visible:ring-ring',
                    active
                      ? 'bg-primary/12 text-primary'
                      : 'text-muted-foreground hover:bg-muted hover:text-foreground',
                  )}
                >
                  {label}
                </Link>
              );
            })}
          </nav>

          <div className="flex shrink-0 items-center gap-1">
            <Link
              href="/search"
              aria-label="Search your libraries"
              className={cn(buttonVariants({ variant: 'ghost', size: 'icon' }))}
            >
              <Search aria-hidden="true" />
            </Link>

            {localOnly ? (
              <span
                className="hidden items-center gap-1.5 rounded-full border border-border/70 px-2.5 py-1 text-[11px] text-muted-foreground lg:flex"
                title="Media and browsing stay on this server"
              >
                <LockKeyhole className="size-3.5" aria-hidden="true" />
                Local only
              </span>
            ) : null}

            {user ? (
              <DropdownMenu>
                <DropdownMenuTrigger
                  render={
                    <Button
                      variant="ghost"
                      size="icon"
                      aria-label={`Account: ${user.username}`}
                    />
                  }
                >
                  <Avatar size="sm" className="size-7">
                    <AvatarFallback>
                      {user.username.slice(0, 2).toUpperCase()}
                    </AvatarFallback>
                  </Avatar>
                </DropdownMenuTrigger>
                <DropdownMenuContent align="end" className="min-w-52">
                  <div className="px-1.5 py-1 text-xs font-medium text-muted-foreground">
                    {user.username}
                  </div>
                  <DropdownMenuItem render={<Link href="/profile" />}>
                    <User aria-hidden="true" />
                    Profile &amp; history
                  </DropdownMenuItem>
                  {isStaff ? (
                    <DropdownMenuItem render={<Link href="/admin" />}>
                      <ShieldCheck aria-hidden="true" />
                      Administration
                    </DropdownMenuItem>
                  ) : null}
                  <DropdownMenuSeparator />
                  <DropdownMenuItem
                    onClick={(event) => {
                      event.preventDefault();
                      void logout();
                    }}
                  >
                    <LogOut aria-hidden="true" />
                    Log out
                  </DropdownMenuItem>
                </DropdownMenuContent>
              </DropdownMenu>
            ) : (
              <Link
                href="/login"
                className={cn(buttonVariants({ variant: 'outline', size: 'sm' }))}
              >
                Sign in
              </Link>
            )}
          </div>
        </div>
      </header>

      <main
        id="collection"
        className="mx-auto w-full max-w-[1680px] flex-1 px-5 py-7 md:px-10 md:py-9"
      >
        {error ? (
          <p
            role="alert"
            className="mb-6 rounded-xl border border-border p-4 text-sm"
          >
            {error}{' '}
            <Link href="/login" className="text-primary underline">
              Sign in
            </Link>
          </p>
        ) : null}
        {children}
      </main>
    </div>
  );
}
