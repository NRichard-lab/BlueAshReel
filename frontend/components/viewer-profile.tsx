'use client';
import { useState } from 'react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { CatalogState, MediaCard } from '@/components/viewer-catalog';
import { apiRequest, jsonBody, PageResult } from '@/lib/api';
import { MediaCardRecord, ProfileRecord, useViewerData } from '@/lib/viewer';

export function ViewerProfile() {
  const profile = useViewerData<ProfileRecord>('/profile');
  const [page, setPage] = useState(1);
  const history = useViewerData<PageResult<MediaCardRecord>>(
    `/browse/media?history=recent&page=${page}&page_size=24`,
  );
  const [message, setMessage] = useState('');
  const [confirm, setConfirm] = useState(false);
  async function preferences(auto_next: boolean, next_countdown: number) {
    try {
      await apiRequest('/profile', {
        method: 'PATCH',
        body: jsonBody({ auto_next, next_countdown }),
      });
      profile.reload();
      setMessage('Preferences saved on this server.');
    } catch (e) {
      setMessage(String(e));
    }
  }
  async function clear() {
    try {
      await apiRequest('/profile/history', { method: 'DELETE' });
      history.reload();
      setConfirm(false);
      setMessage('Your viewing history has been deleted.');
    } catch (e) {
      setMessage(String(e));
    }
  }
  return (
    <>
      <h1 className="mb-6 text-3xl font-semibold">Profile</h1>
      <CatalogState {...profile} empty={false} />
      {profile.data && (
        <div className="max-w-2xl rounded-xl border bg-card p-6">
          <h2 className="text-lg font-medium">{profile.data.username}</h2>
          <p className="mt-1 text-sm text-muted-foreground">
            {profile.data.roles.join(', ')} · Local household account
          </p>
          <div className="mt-6 space-y-4">
            <label htmlFor="auto-next" className="flex items-center gap-3 text-sm">
              <input
                id="auto-next"
                type="checkbox"
                checked={profile.data.auto_next}
                onChange={(e) =>
                  void preferences(
                    e.target.checked,
                    profile.data!.next_countdown,
                  )
                }
              />
              Automatically play the next episode
            </label>
            <label htmlFor="next-countdown" className="flex items-center gap-3 text-sm">
              Next-episode countdown (seconds)
              <Input
                id="next-countdown"
                aria-label="Next-episode countdown"
                type="number"
                min={5}
                max={60}
                defaultValue={profile.data.next_countdown}
                className="w-20"
                onBlur={(e) => {
                  const value = Number(e.target.value);
                  if (value >= 5 && value <= 60)
                    void preferences(profile.data!.auto_next, value);
                }}
              />
            </label>
          </div>
          <p className="mt-6 text-sm text-muted-foreground">
            Progress and history stay in the local database. No analytics, cloud
            authentication, or outside recommendations.
          </p>
          <Button
            className="mt-5"
            variant="destructive"
            onClick={() => setConfirm(true)}
          >
            Delete my viewing history
          </Button>
          {confirm && (
            <div className="mt-4 rounded-lg border p-4">
              <p className="mb-3 text-sm">
                Delete all your saved progress and watched states? This cannot
                be undone.
              </p>
              <Button variant="destructive" onClick={clear}>
                Confirm history deletion
              </Button>{' '}
              <Button variant="outline" onClick={() => setConfirm(false)}>
                Cancel
              </Button>
            </div>
          )}
          {message && (
            <output className="mt-4 block text-sm">
              {message}
            </output>
          )}
        </div>
      )}
      <h2 className="mb-5 mt-10 text-xl font-semibold">Recently Watched</h2>
      <CatalogState {...history} empty={!history.data?.items.length} />
      <div className="grid grid-cols-2 gap-5 sm:grid-cols-4 xl:grid-cols-6">
        {history.data?.items.map((item) => (
          <MediaCard item={item} key={item.id} />
        ))}
      </div>
      {(history.data?.total ?? 0) > 24 && (
        <div className="mt-4 flex gap-4">
          <Button disabled={page === 1} onClick={() => setPage((p) => p - 1)}>
            Previous
          </Button>
          <Button
            disabled={page * 24 >= (history.data?.total ?? 0)}
            onClick={() => setPage((p) => p + 1)}
          >
            Next
          </Button>
        </div>
      )}
    </>
  );
}
