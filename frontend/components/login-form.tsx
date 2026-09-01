'use client';

import { useEffect, useState } from 'react';
import { Film, LoaderCircle, LockKeyhole, ShieldCheck } from 'lucide-react';

import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Field, FieldGroup, FieldLabel } from '@/components/ui/field';
import { Input } from '@/components/ui/input';
import { ApiError, apiRequest, jsonBody } from '@/lib/api';
import { productConfig } from '@/lib/product-config';

export function LoginForm() {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string>();

  useEffect(() => {
    apiRequest<{ setup_required: boolean }>('/setup/status')
      .then((status) => {
        if (status.setup_required) window.location.replace('/setup');
      })
      .catch(() => undefined);
  }, []);

  const submit = async (event: React.SyntheticEvent<HTMLFormElement>) => {
    event.preventDefault();
    setSubmitting(true);
    setError(undefined);
    try {
      await apiRequest('/auth/login', {
        method: 'POST',
        body: jsonBody({ username: username.trim(), password }),
      });
      window.location.assign('/');
    } catch (caught: unknown) {
      setError(caught instanceof ApiError || caught instanceof Error ? caught.message : 'Sign-in failed.');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <main className="grid min-h-screen place-items-center bg-[radial-gradient(circle_at_top,var(--success-soft),transparent_35%),var(--background)] p-4">
      <div className="w-full max-w-md">
        <div className="mb-6 flex items-center justify-center gap-3">
          <span className="grid size-11 place-items-center rounded-xl bg-primary text-primary-foreground shadow-sm"><Film className="size-5" /></span>
          <span>
            <span className="block text-lg font-semibold tracking-[-0.02em]">{productConfig.name}</span>
            <span className="block text-xs text-muted-foreground">{productConfig.subtitle}</span>
          </span>
        </div>
        <Card className="border-none shadow-[0_24px_80px_rgb(18_42_66/12%)]">
          <CardHeader className="text-center">
            <CardTitle className="text-xl">Owner sign in</CardTitle>
            <CardDescription>Continue to your private home-media server.</CardDescription>
          </CardHeader>
          <CardContent>
            {error ? (
              <Alert variant="destructive" className="mb-4">
                <AlertTitle>Could not sign in</AlertTitle>
                <AlertDescription>{error}</AlertDescription>
              </Alert>
            ) : null}
            <form onSubmit={submit}>
              <FieldGroup>
                <Field>
                  <FieldLabel htmlFor="username">Username</FieldLabel>
                  <Input id="username" autoComplete="username" required value={username} onChange={(event) => setUsername(event.target.value)} />
                </Field>
                <Field>
                  <FieldLabel htmlFor="password">Password</FieldLabel>
                  <Input id="password" type="password" autoComplete="current-password" required value={password} onChange={(event) => setPassword(event.target.value)} />
                </Field>
                <Button type="submit" size="lg" disabled={submitting} className="mt-1 w-full">
                  {submitting ? <LoaderCircle data-icon="inline-start" className="animate-spin" /> : <LockKeyhole data-icon="inline-start" />}
                  Sign in locally
                </Button>
              </FieldGroup>
            </form>
            <div className="mt-5 flex items-center justify-center gap-2 text-xs text-muted-foreground">
              <ShieldCheck className="size-3.5 text-[var(--success)]" /> No cloud account or Internet connection required
            </div>
          </CardContent>
        </Card>
      </div>
    </main>
  );
}
