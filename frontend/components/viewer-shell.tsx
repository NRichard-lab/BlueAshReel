'use client';

import { useEffect, useState } from 'react';
import Link from 'next/link';
import { Film, LockKeyhole, LogOut } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { apiRequest, CurrentUser } from '@/lib/api';
import { productConfig } from '@/lib/product-config';

const links = [
  ['Home', '/'],
  ['Movies', '/movies'],
  ['TV Shows', '/shows'],
  ['Search', '/search'],
  ['Continue Watching', '/continue'],
  ['Profile', '/profile'],
];

export function ViewerShell({
  children,
  currentPath,
}: {
  children: React.ReactNode;
  currentPath: string;
}) {
  const [user, setUser] = useState<CurrentUser>();
  const [posture, setPosture] = useState<string>('Checking privacy');
  const [error, setError] = useState('');
  useEffect(() => {
    apiRequest<CurrentUser>('/auth/me')
      .then(setUser)
      .catch(() => setError('Sign in to browse your household libraries.'));
    const refreshPrivacy = () => apiRequest<{ local_only: boolean }>('/privacy')
      .then((p) =>
        setPosture(p.local_only ? 'Local only' : 'Outbound gate open'),
      )
      .catch(() => setPosture('Privacy status unavailable'));
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
  return (
    <div className="dark min-h-screen bg-background text-foreground">
      <a href="#collection" className="sr-only focus:not-sr-only">
        Skip to content
      </a>
      <header className="border-b border-border bg-sidebar">
        <div className="mx-auto flex max-w-[1600px] items-center justify-between gap-4 px-5 py-5 md:px-10">
          <Link href="/" className="flex items-center gap-3">
            <Film className="size-7 text-primary" />
            <span>
              <strong className="text-xl tracking-tight">
                {productConfig.name}
              </strong>
              <span className="block text-[11px] text-muted-foreground">
                {productConfig.subtitle}
              </span>
            </span>
          </Link>
          <div className="flex items-center gap-3 text-xs text-muted-foreground">
            <span className="hidden items-center gap-1.5 sm:flex">
              <LockKeyhole className="size-3.5" />
              {posture}
            </span>
            {user ? (
              <Button variant="ghost" onClick={logout} aria-label="Log out">
                <LogOut />
                <span className="hidden md:inline">{user.username}</span>
              </Button>
            ) : (
              <Link href="/login">Sign in</Link>
            )}
          </div>
        </div>
        <nav
          aria-label="Main navigation"
          className="mx-auto flex max-w-[1600px] gap-1 overflow-x-auto px-4 md:px-9"
        >
          {[
            ...links,
            ...(user?.roles.some((r) => ['Owner', 'Administrator'].includes(r))
              ? [['Administration', '/admin']]
              : []),
          ].map(([label, href]) => (
            <Link
              key={href}
              href={href}
              aria-current={currentPath === href ? 'page' : undefined}
              className={`shrink-0 border-b-2 px-3 py-3 text-sm ${currentPath === href ? 'border-primary text-primary' : 'border-transparent text-muted-foreground hover:text-foreground'}`}
            >
              {label}
            </Link>
          ))}
        </nav>
      </header>
      <main
        id="collection"
        className="mx-auto max-w-[1600px] px-5 py-8 md:px-10"
      >
        {error ? (
          <p role="alert" className="mb-6 rounded-xl border p-4">
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
