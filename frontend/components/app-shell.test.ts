import { describe, expect, it } from 'vitest';

import { isOwnerOnlyPath } from '../lib/admin-routes';

describe('Owner-only administration routes', () => {
  it.each(['/admin/settings', '/admin/settings/media-storage', '/admin/settings/playback', '/privacy', '/streams/active'])('protects %s and nested pages', (path) => {
    expect(isOwnerOnlyPath(path)).toBe(true);
  });

  it.each(['/libraries', '/health', '/settings-preview'])('does not overmatch %s', (path) => {
    expect(isOwnerOnlyPath(path)).toBe(false);
  });
});
