'use client';
import { useState } from 'react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Checkbox } from '@/components/ui/checkbox';
import { Dialog, DialogContent, DialogTitle, DialogDescription } from '@/components/ui/dialog';
import {
  NativeSelect,
  NativeSelectOption as Option,
} from '@/components/ui/native-select';
import { CatalogState } from '@/components/viewer-catalog';
import { apiRequest, jsonBody, LibraryRecord, PageResult } from '@/lib/api';
import { ProfileRecord, useViewerData } from '@/lib/viewer';

interface HouseholdUser {
  id: string;
  username: string;
  roles: string[];
  is_active: boolean;
  library_ids: string[];
}
export function HouseholdUsers() {
  const [page, setPage] = useState(1);
  const [libraryPage, setLibraryPage] = useState(1);
  const users = useViewerData<PageResult<HouseholdUser>>(`/users?page=${page}`);
  const libraries = useViewerData<PageResult<LibraryRecord>>(
    `/libraries?page_size=100&page=${libraryPage}`,
  );
  const profile = useViewerData<ProfileRecord>('/profile');
  const owner = profile.data?.roles.includes('Owner');
  const [editing, setEditing] = useState<HouseholdUser | null>(null);
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [role, setRole] = useState('Viewer');
  const [selected, setSelected] = useState<string[]>([]);
  const [active, setActive] = useState(true);
  const [message, setMessage] = useState('');
  const [busy, setBusy] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<HouseholdUser | null>(null);
  const [deleteError, setDeleteError] = useState('');
  const [deleteHistory, setDeleteHistory] = useState(false);
  function edit(user: HouseholdUser | null) {
    setEditing(user);
    setUsername(user?.username ?? '');
    setPassword('');
    setRole(user?.roles[0] ?? 'Viewer');
    setSelected(user?.library_ids ?? []);
    setActive(user?.is_active ?? true);
    setMessage('');
  }
  async function save(event: React.SyntheticEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setMessage('');
    try {
      await apiRequest(editing ? `/users/${editing.id}` : '/users', {
        method: editing ? 'PATCH' : 'POST',
        body: jsonBody({
          ...(editing ? { is_active: active } : { username }),
          ...(password ? { password } : {}),
          role,
          library_ids: selected,
        }),
      });
      edit(null);
      users.reload();
      setMessage(
        editing
          ? 'Account updated. Role or password changes revoke existing sessions.'
          : 'Household user created. Share their password privately.',
      );
    } catch (e) {
      setMessage(
        e instanceof Error ? e.message : 'Account could not be saved.',
      );
    } finally {
      setBusy(false);
    }
  }
  async function revoke(user: HouseholdUser) {
    try {
      await apiRequest(`/users/${user.id}/revoke`, { method: 'POST' });
      setMessage('Sessions revoked.');
    } catch (e) {
      setMessage(String(e));
    }
  }
  async function remove() {
    if (!deleteTarget) return;
    setBusy(true);
    setDeleteError('');
    try {
      await apiRequest(
        `/users/${deleteTarget.id}?delete_history=${deleteHistory}`,
        { method: 'DELETE' },
      );
      setDeleteTarget(null);
      users.reload();
      setMessage('Account deleted.');
    } catch (e) {
      setDeleteError(e instanceof Error ? e.message : 'Account deletion failed.');
    } finally {
      setBusy(false);
    }
  }
  return (
    <>
      <h1 className="text-3xl font-semibold tracking-tight">Household users</h1>
      <p className="mb-7 mt-2 text-sm text-muted-foreground">
        Each person has their own assigned libraries, progress, and watch
        history.
      </p>
      {message && (
        <output className="mb-5 block rounded-xl border p-4 text-sm">
          {message}
        </output>
      )}
      <div className="grid gap-7 xl:grid-cols-[minmax(0,1fr)_370px]">
        <section>
          <CatalogState {...users} empty={false} />
          <div className="space-y-3">
            {users.data?.items.map((user) => (
              <div
                key={user.id}
                className="flex flex-wrap items-center justify-between gap-4 rounded-xl border bg-card p-5"
              >
                <div>
                  <h2 className="font-medium">{user.username}</h2>
                  <p className="mt-1 text-xs text-muted-foreground">
                    {user.roles.join(', ')} ·{' '}
                    {user.is_active ? 'Active' : 'Disabled'} ·{' '}
                    {user.library_ids.length} assigned libraries
                  </p>
                </div>
                <div className="flex flex-wrap gap-2">
                  <Button
                    variant="outline"
                    onClick={() => edit(user)}
                    disabled={
                      !owner &&
                      (user.roles.includes('Owner') ||
                        user.id === profile.data?.id)
                    }
                  >
                    Edit access
                  </Button>
                  <Button
                    variant="outline"
                    onClick={() => revoke(user)}
                    disabled={
                      !owner &&
                      (user.roles.includes('Owner') ||
                        user.id === profile.data?.id)
                    }
                  >
                    Revoke sessions
                  </Button>
                  <Button
                    variant="destructive"
                    onClick={() => {
                      setDeleteTarget(user);
                      setDeleteHistory(false);
                    }}
                    disabled={
                      !owner &&
                      (user.roles.includes('Owner') ||
                        user.id === profile.data?.id)
                    }
                  >
                    Delete
                  </Button>
                </div>
              </div>
            ))}
          </div>
          {(users.data?.total ?? 0) > 30 && (
            <div className="mt-5 flex gap-3">
              <Button
                disabled={page === 1}
                onClick={() => setPage((p) => p - 1)}
              >
                Previous users
              </Button>
              <Button
                disabled={page * 30 >= (users.data?.total ?? 0)}
                onClick={() => setPage((p) => p + 1)}
              >
                Next users
              </Button>
            </div>
          )}
        </section>
        <form
          onSubmit={save}
          className="h-fit space-y-5 rounded-xl border bg-card p-6"
        >
          <h2 className="text-lg font-semibold">
            {editing ? `Edit ${editing.username}` : 'Add household user'}
          </h2>
          <label htmlFor="household-username" className="block text-sm">
            Username
            <Input
              id="household-username"
              className="mt-2"
              value={username}
              disabled={Boolean(editing)}
              required
              minLength={1}
              maxLength={80}
              onChange={(e) => setUsername(e.target.value)}
              autoComplete="off"
            />
          </label>
          <label className="block text-sm">
            {editing
              ? 'Reset password (leave blank to keep)'
              : 'Initial password'}
            <Input
              className="mt-2"
              type="password"
              value={password}
              required={!editing}
              minLength={12}
              maxLength={256}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete="new-password"
            />
          </label>
          <label className="block text-sm">
            Role
            <NativeSelect
              className="mt-2 w-full"
              value={role}
              onChange={(e) => setRole(e.target.value)}
            >
              <Option value="Viewer">Viewer</Option>
              <Option value="Administrator">Administrator</Option>
              {owner && <Option value="Owner">Owner</Option>}
            </NativeSelect>
          </label>
          <fieldset>
            <legend className="mb-3 text-sm font-medium">
              Accessible libraries
            </legend>
            <div className="max-h-56 space-y-3 overflow-auto">
              {libraries.data?.items.map((l) => (
                <label key={l.id} htmlFor={`library-${l.id}`} className="flex items-center gap-3 text-sm">
                  <Checkbox
                    id={`library-${l.id}`}
                    checked={selected.includes(l.id)}
                    onCheckedChange={(checked) =>
                      setSelected((previous) =>
                        checked
                          ? [...previous, l.id]
                          : previous.filter((id) => id !== l.id),
                      )
                    }
                  />
                  {l.name}
                  {!l.enabled ? ' (disabled)' : ''}
                </label>
              ))}
            </div>
            {(libraries.data?.total ?? 0) > 100 && (
              <div className="mt-3 flex gap-2">
                <Button
                  type="button"
                  disabled={libraryPage === 1}
                  onClick={() => setLibraryPage((p) => p - 1)}
                >
                  Previous libraries
                </Button>
                <Button
                  type="button"
                  disabled={libraryPage * 100 >= (libraries.data?.total ?? 0)}
                  onClick={() => setLibraryPage((p) => p + 1)}
                >
                  More libraries
                </Button>
              </div>
            )}
            <p className="mt-3 text-xs text-muted-foreground">
              No selection means no viewing access, including for
              administrators.
            </p>
          </fieldset>
          {editing && (
            <label htmlFor="account-enabled" className="flex items-center gap-3 text-sm">
              <Checkbox id="account-enabled" checked={active} onCheckedChange={setActive} />
              Account enabled
            </label>
          )}
          <div className="flex gap-3">
            <Button type="submit" disabled={busy}>
              {busy ? 'Saving…' : editing ? 'Save access' : 'Create user'}
            </Button>
            {editing && (
              <Button
                type="button"
                variant="outline"
                onClick={() => edit(null)}
              >
                Cancel edit
              </Button>
            )}
          </div>
        </form>
      </div>
      <Dialog open={Boolean(deleteTarget)} onOpenChange={open => { if (!open && !busy) { setDeleteTarget(null); setDeleteError(''); } }}>
          <DialogContent>
            {deleteError && <p role="alert" className="text-destructive">{deleteError}</p>}
            <DialogTitle>
              Delete {deleteTarget?.username}?
            </DialogTitle>
            <DialogDescription>
              The account and access will be removed permanently. Retained
              history is anonymized and cannot be resumed.
            </DialogDescription>
            <label htmlFor="delete-user-history" className="mb-5 flex items-center gap-3 text-sm">
              <Checkbox
                id="delete-user-history"
                checked={deleteHistory}
                onCheckedChange={setDeleteHistory}
              />
              Also delete their playback history
            </label>
            <Button variant="destructive" disabled={busy} onClick={remove}>
              Confirm account deletion
            </Button>{' '}
            <Button variant="outline" onClick={() => setDeleteTarget(null)}>
              Cancel
            </Button>
          </DialogContent>
      </Dialog>
    </>
  );
}
