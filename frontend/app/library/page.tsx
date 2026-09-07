'use client';

import { useEffect } from 'react';

/**
 * Temporary alias: the viewer "Libraries" page was renamed to "Settings" and its
 * library-management presentation now lives at /settings#libraries. This keeps
 * older /library links working by redirecting (hash preserved).
 */
export default function LibraryRedirect() {
  useEffect(() => {
    const hash = window.location.hash || '#libraries';
    window.location.replace(`/settings${hash}`);
  }, []);

  return (
    <main className="grid min-h-screen place-items-center bg-background p-10 text-sm text-muted-foreground">
      <output>Redirecting to Settings…</output>
    </main>
  );
}
