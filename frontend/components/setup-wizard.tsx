'use client';

import { useEffect, useMemo, useState } from 'react';
import {
  Check,
  ChevronLeft,
  ChevronRight,
  Film,
  FolderCheck,
  HardDrive,
  LoaderCircle,
  LockKeyhole,
  ShieldCheck,
} from 'lucide-react';

import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Checkbox } from '@/components/ui/checkbox';
import { Field, FieldDescription, FieldError, FieldGroup, FieldLabel } from '@/components/ui/field';
import { Input } from '@/components/ui/input';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { ApiError, apiRequest, jsonBody } from '@/lib/api';
import type { MediaFolderSelection } from '@/lib/api';
import { productConfig } from '@/lib/product-config';
import { MediaFolderField } from '@/components/media-folder-picker';

type LibraryType = 'movies' | 'tv' | 'other';

interface SetupHealth {
  database: boolean;
  application_data: boolean;
  temporary_storage: boolean;
  ffprobe: boolean;
  ffmpeg: boolean;
}

const steps = ['Welcome', 'Owner & storage', 'First library', 'Ready'];

export function SetupWizard() {
  const [step, setStep] = useState(0);
  const [checking, setChecking] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string>();
  const [health, setHealth] = useState<SetupHealth>();
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [dataDirectory, setDataDirectory] = useState('');
  const [temporaryDirectory, setTemporaryDirectory] = useState('');
  const [createLibrary, setCreateLibrary] = useState(true);
  const [libraryName, setLibraryName] = useState('Movies');
  const [libraryType, setLibraryType] = useState<LibraryType>('movies');
  const [mediaFolder, setMediaFolder] = useState<MediaFolderSelection>();

  useEffect(() => {
    apiRequest<{ setup_required: boolean }>('/setup/status')
      .then(async (status) => {
        if (!status.setup_required) {
          try {
            await apiRequest('/auth/me');
            window.location.replace('/libraries');
          } catch {
            window.location.replace('/login');
          }
        } else {
          await apiRequest('/setup/session', { method: 'POST' });
        }
      })
      .catch((caught: unknown) => {
        setError(caught instanceof Error ? caught.message : 'Could not reach the local server.');
      })
      .finally(() => setChecking(false));
  }, []);

  const accountValid = useMemo(
    () => username.trim().length >= 3 && password.length >= 12 && password === confirmPassword,
    [confirmPassword, password, username],
  );

  const next = () => {
    setError(undefined);
    setStep((current) => Math.min(steps.length - 1, current + 1));
  };

  const continueToLibrary = () => {
    if (!accountValid) {
      setError('Use a username of at least 3 characters and matching passwords of at least 12 characters.');
      return;
    }
    setError(undefined);
    setStep(2);
  };

  const finishSetup = async () => {
    if (!accountValid) {
      setError('Return to Owner & storage and provide matching passwords of at least 12 characters.');
      return;
    }
    if (createLibrary && (!libraryName.trim() || !mediaFolder)) {
      setError('Provide a library name and choose a readable media folder, or skip the first library for now.');
      return;
    }
    setSubmitting(true);
    setError(undefined);
    try {
      const result = await apiRequest<{ health: SetupHealth }>('/setup/owner', {
        method: 'POST',
        body: jsonBody({
          username: username.trim(), password,
          application_data_directory: dataDirectory.trim() || null,
          temporary_directory: temporaryDirectory.trim() || null,
          initial_library: createLibrary && mediaFolder ? {
            name: libraryName.trim(),
            library_type: libraryType,
            folder_ids: [mediaFolder.selection_id],
          } : null,
        }),
      });
      setHealth(result.health);
      setPassword('');
      setConfirmPassword('');
      setStep(3);
    } catch (caught: unknown) {
      setError(caught instanceof ApiError || caught instanceof Error ? caught.message : 'Setup could not be completed.');
    } finally {
      setSubmitting(false);
    }
  };

  if (checking) {
    return (
      <main className="grid min-h-screen place-items-center bg-background p-6">
        <div className="flex items-center gap-3 text-sm text-muted-foreground">
          <LoaderCircle className="size-5 animate-spin" /> Checking local server…
        </div>
      </main>
    );
  }

  return (
    <main className="min-h-screen bg-[radial-gradient(circle_at_top_left,var(--success-soft),transparent_34%),var(--background)] px-4 py-8 sm:px-6 lg:py-14">
      <div className="mx-auto max-w-5xl">
        <header className="flex items-center gap-3">
          <span className="grid size-10 place-items-center rounded-xl bg-primary text-primary-foreground shadow-sm">
            <Film className="size-5" aria-hidden="true" />
          </span>
          <span>
            <span className="block font-semibold tracking-[-0.02em]">{productConfig.name}</span>
            <span className="block text-xs text-muted-foreground">{productConfig.subtitle}</span>
          </span>
        </header>

        <div className="mt-10 grid gap-8 lg:grid-cols-[240px_minmax(0,1fr)]">
          <aside aria-label="Setup progress">
            <p className="text-xs font-semibold uppercase tracking-[0.16em] text-primary">Secure first run</p>
            <h1 className="mt-2 text-3xl font-semibold tracking-[-0.04em]">Your server, under your control.</h1>
            <p className="mt-3 text-sm leading-relaxed text-muted-foreground">
              Create the only account allowed during setup and point the server at media it may read.
            </p>
            <ol className="mt-8 space-y-3">
              {steps.map((label, index) => (
                <li key={label} className="flex items-center gap-3 text-sm">
                  <span
                    aria-current={index === step ? 'step' : undefined}
                    className={`grid size-7 place-items-center rounded-full border text-xs font-semibold ${
                      index < step
                        ? 'border-primary bg-primary text-primary-foreground'
                        : index === step
                          ? 'border-primary text-primary'
                          : 'border-border text-muted-foreground'
                    }`}
                  >
                    {index < step ? <Check className="size-3.5" /> : index + 1}
                  </span>
                  <span className={index === step ? 'font-medium text-foreground' : 'text-muted-foreground'}>{label}</span>
                </li>
              ))}
            </ol>
          </aside>

          <Card className="min-h-[520px] border-none shadow-[0_24px_80px_rgb(18_42_66/10%)]">
            <CardHeader className="border-b">
              <CardTitle>{steps[step]}</CardTitle>
              <CardDescription>Step {step + 1} of {steps.length}</CardDescription>
            </CardHeader>
            <CardContent className="flex min-h-[420px] flex-col">
              {error ? (
                <Alert variant="destructive" className="mb-5">
                  <AlertTitle>Something needs attention</AlertTitle>
                  <AlertDescription>{error}</AlertDescription>
                </Alert>
              ) : null}

              {step === 0 ? (
                <div className="my-auto grid gap-5 sm:grid-cols-3">
                  {[
                    { icon: LockKeyhole, title: 'Local account', text: 'Your password is hashed and never returned by the API.' },
                    { icon: HardDrive, title: 'Read-only media', text: 'Scans inspect files without renaming, moving, or deleting them.' },
                    { icon: ShieldCheck, title: 'No outbound calls', text: 'Metadata services, telemetry, and integrations start disabled.' },
                  ].map(({ icon: Icon, title, text }) => (
                    <div key={title} className="rounded-xl border bg-muted/20 p-4">
                      <span className="grid size-9 place-items-center rounded-lg bg-primary/10 text-primary">
                        <Icon className="size-4" />
                      </span>
                      <h2 className="mt-4 text-sm font-semibold">{title}</h2>
                      <p className="mt-1 text-xs leading-relaxed text-muted-foreground">{text}</p>
                    </div>
                  ))}
                </div>
              ) : null}

              {step === 1 ? (
                <FieldGroup className="max-w-2xl">
                  <Field>
                    <FieldLabel htmlFor="owner-username">Owner username</FieldLabel>
                    <Input id="owner-username" autoComplete="username" value={username} onChange={(event) => setUsername(event.target.value)} />
                    <FieldDescription>At least 3 characters. This identifies the local Owner account.</FieldDescription>
                  </Field>
                  <div className="grid gap-5 sm:grid-cols-2">
                    <Field data-invalid={Boolean(confirmPassword && password !== confirmPassword)}>
                      <FieldLabel htmlFor="owner-password">Password</FieldLabel>
                      <Input id="owner-password" type="password" autoComplete="new-password" value={password} onChange={(event) => setPassword(event.target.value)} />
                      <FieldDescription>Use at least 12 characters.</FieldDescription>
                    </Field>
                    <Field data-invalid={Boolean(confirmPassword && password !== confirmPassword)}>
                      <FieldLabel htmlFor="owner-confirm-password">Confirm password</FieldLabel>
                      <Input id="owner-confirm-password" type="password" autoComplete="new-password" value={confirmPassword} onChange={(event) => setConfirmPassword(event.target.value)} />
                      {confirmPassword && password !== confirmPassword ? <FieldError>Passwords do not match.</FieldError> : null}
                    </Field>
                  </div>
                  <div className="grid gap-5 sm:grid-cols-2">
                    <Field>
                      <FieldLabel htmlFor="data-directory">Application data directory</FieldLabel>
                      <Input id="data-directory" value={dataDirectory} onChange={(event) => setDataDirectory(event.target.value)} placeholder="Use configured default" />
                      <FieldDescription>Persistent database, configuration, and cached local metadata.</FieldDescription>
                    </Field>
                    <Field>
                      <FieldLabel htmlFor="temporary-directory">Temporary directory</FieldLabel>
                      <Input id="temporary-directory" value={temporaryDirectory} onChange={(event) => setTemporaryDirectory(event.target.value)} placeholder="Use configured default" />
                      <FieldDescription>Application-controlled scratch space that can be cleaned safely.</FieldDescription>
                    </Field>
                  </div>
                </FieldGroup>
              ) : null}

              {step === 2 ? (
                <FieldGroup className="max-w-2xl">
                  <Alert><ShieldCheck /><AlertTitle>Protected first-run session</AlertTitle><AlertDescription>Folder browsing is limited to approved media storage. Your Owner account and first library are saved together when you finish.</AlertDescription></Alert>
                  <Field orientation="horizontal">
                    <Checkbox id="create-library" checked={createLibrary} onCheckedChange={(checked) => setCreateLibrary(Boolean(checked))} />
                    <FieldLabel htmlFor="create-library">Create the first media library now</FieldLabel>
                  </Field>
                  {createLibrary ? (
                    <>
                      <div className="grid gap-5 sm:grid-cols-2">
                        <Field>
                          <FieldLabel htmlFor="library-name">Display name</FieldLabel>
                          <Input id="library-name" value={libraryName} onChange={(event) => setLibraryName(event.target.value)} />
                        </Field>
                        <Field>
                          <FieldLabel htmlFor="first-library-type">Library type</FieldLabel>
                          <Select value={libraryType} onValueChange={(value) => setLibraryType(value as LibraryType)}>
                            <SelectTrigger id="first-library-type" className="w-full"><SelectValue /></SelectTrigger>
                            <SelectContent>
                              <SelectItem value="movies">Movies</SelectItem>
                              <SelectItem value="tv">TV Shows</SelectItem>
                              <SelectItem value="other">Other Videos</SelectItem>
                            </SelectContent>
                          </Select>
                        </Field>
                      </div>
                      <MediaFolderField id="media-folder" selection={mediaFolder} onSelectionChange={setMediaFolder} />
                      <Alert>
                        <FolderCheck />
                        <AlertTitle>Source files stay untouched</AlertTitle>
                        <AlertDescription>The server validates this read-only location. A scan starts only when you request one.</AlertDescription>
                      </Alert>
                    </>
                  ) : (
                    <div className="rounded-xl border border-dashed p-8 text-center text-sm text-muted-foreground">
                      You can add libraries after setup from the Libraries page.
                    </div>
                  )}
                </FieldGroup>
              ) : null}

              {step === 3 ? (
                <div className="my-auto text-center">
                  <span className="mx-auto grid size-14 place-items-center rounded-2xl bg-[var(--success-soft)] text-[var(--success-foreground)]">
                    <Check className="size-7" />
                  </span>
                  <h2 className="mt-5 text-2xl font-semibold tracking-[-0.03em]">The private server foundation is ready.</h2>
                  <p className="mx-auto mt-2 max-w-md text-sm leading-relaxed text-muted-foreground">
                    Owner access is secured, configured storage was checked, and local dependencies are {health && Object.values(health).every(Boolean) ? 'ready' : 'ready with attention noted below'}.
                  </p>
                  <div className="mx-auto mt-6 grid max-w-2xl grid-cols-2 gap-2 text-xs sm:grid-cols-5">
                    <div className="rounded-lg border bg-muted/25 px-2 py-3">Database {health?.database ? 'ready' : 'check required'}</div>
                    <div className="rounded-lg border bg-muted/25 px-2 py-3">App data {health?.application_data ? 'ready' : 'check required'}</div>
                    <div className="rounded-lg border bg-muted/25 px-2 py-3">Temporary {health?.temporary_storage ? 'ready' : 'check required'}</div>
                    <div className="rounded-lg border bg-muted/25 px-2 py-3">FFprobe {health?.ffprobe ? 'available' : 'unavailable'}</div>
                    <div className="rounded-lg border bg-muted/25 px-2 py-3">FFmpeg {health?.ffmpeg ? 'available' : 'unavailable'}</div>
                  </div>
                  <Button size="lg" className="mt-7" onClick={() => window.location.assign('/')}>
                    Open dashboard <ChevronRight data-icon="inline-end" />
                  </Button>
                </div>
              ) : null}

              {step < 3 ? (
                <div className="mt-auto flex items-center justify-between border-t pt-5">
                  <Button variant="ghost" disabled={step === 0 || submitting} onClick={() => setStep((current) => Math.max(0, current - 1))}>
                    <ChevronLeft data-icon="inline-start" /> Back
                  </Button>
                  {step === 2 ? (
                    <Button disabled={submitting} onClick={finishSetup}>
                      {submitting ? <LoaderCircle data-icon="inline-start" className="animate-spin" /> : <ShieldCheck data-icon="inline-start" />}
                      Finish secure setup
                    </Button>
                  ) : step === 1 ? (
                    <Button disabled={submitting} onClick={continueToLibrary}>Continue <ChevronRight data-icon="inline-end" /></Button>
                  ) : (
                    <Button onClick={next}>Continue <ChevronRight data-icon="inline-end" /></Button>
                  )}
                </div>
              ) : null}
            </CardContent>
          </Card>
        </div>
      </div>
    </main>
  );
}
