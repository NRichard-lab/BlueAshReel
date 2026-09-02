import { afterEach, describe, expect, it, vi } from 'vitest';

import { apiRequest, jsonBody } from '@/lib/api';
import { productConfig } from '@/lib/product-config';

const originalFetch = globalThis.fetch;
const originalDocument = globalThis.document;

afterEach(() => {
  globalThis.fetch = originalFetch;
  Object.defineProperty(globalThis, 'document', { configurable: true, value: originalDocument });
});

describe('central product configuration', () => {
  it('provides branding and the versioned API prefix from one config file', () => {
    expect(productConfig.name.trim().length).toBeGreaterThan(0);
    expect(productConfig.subtitle.trim().length).toBeGreaterThan(0);
    expect(productConfig.api_prefix).toBe('/api/v1');
  });
});

describe('apiRequest', () => {
  it('accepts successful empty mutation responses even with JSON content-type', async () => {
    globalThis.fetch = vi.fn(async () => new Response(null, { status: 204, headers: { 'content-type': 'application/json' } })) as typeof fetch;
    await expect(apiRequest('/auth/logout', { method: 'POST' })).resolves.toBeUndefined();
  });
  it('uses same-origin cookies and the versioned API prefix', async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({ status: 'ok' }), {
      status: 200,
      headers: { 'content-type': 'application/json' },
    }));
    globalThis.fetch = fetchMock as typeof fetch;

    await expect(apiRequest<{ status: string }>('/health/live')).resolves.toEqual({ status: 'ok' });
    expect(fetchMock).toHaveBeenCalledWith('/api/v1/health/live', expect.objectContaining({
      credentials: 'same-origin',
      cache: 'no-store',
    }));
  });

  it('adds the double-submit CSRF header to mutations without storing auth in localStorage', async () => {
    Object.defineProperty(globalThis, 'document', {
      configurable: true,
      value: { cookie: 'csrf_token=safe-token' },
    });
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
      expect(new Headers(init?.headers).get('X-CSRF-Token')).toBe('safe-token');
      return new Response(JSON.stringify({ id: 'job-1' }), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      });
    });
    globalThis.fetch = fetchMock as typeof fetch;

    await apiRequest('/jobs/job-1/cancel', { method: 'POST', body: jsonBody({}) });
  });

  it('surfaces the backend structured error message', async () => {
    globalThis.fetch = vi.fn(async () => new Response(JSON.stringify({
      error: { code: 'path_invalid', message: 'Media path is not allowed.' },
    }), {
      status: 422,
      headers: { 'content-type': 'application/json' },
    })) as typeof fetch;

    await expect(apiRequest('/libraries')).rejects.toMatchObject({
      name: 'ApiError',
      status: 422,
      message: 'Media path is not allowed.',
    });
  });

  it('surfaces a nested FastAPI detail message without object coercion', async () => {
    globalThis.fetch = vi.fn(async () => new Response(JSON.stringify({
      detail: { message: 'The approved media folder cannot be read', state: 'permission_denied' },
    }), {
      status: 409,
      headers: { 'content-type': 'application/json' },
    })) as typeof fetch;

    await expect(apiRequest('/media-folders/browse')).rejects.toMatchObject({
      status: 409,
      message: 'The approved media folder cannot be read',
    });
  });
});
