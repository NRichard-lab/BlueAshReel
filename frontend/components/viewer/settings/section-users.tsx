'use client';

import { UserPlus, ShieldCheck, Crown, Users, UserCog } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Avatar, AvatarFallback } from '@/components/ui/avatar';
import {
  SettingsGroup,
  SettingRow,
  ToggleRow,
  SelectRow,
  TextRow,
} from '@/components/viewer/settings/settings-ui';
import { DevBadge } from '@/components/viewer/dev-placeholder';
import { usersPlaceholders as u } from '@/lib/settings-placeholders';

const NOT_WIRED =
  'Portal accounts and invitations are managed on the Portal; editing them here is coming later';

function initials(name: string) {
  return name.split(' ').map((p) => p[0]).filter(Boolean).slice(0, 2).join('').toUpperCase();
}

function PersonRow({
  name,
  email,
  meta,
  icon,
}: {
  name: string;
  email: string;
  meta?: string;
  icon: React.ReactNode;
}) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-3 py-3 first:pt-0 last:pb-0">
      <div className="flex min-w-0 items-center gap-3">
        <Avatar size="sm" className="size-8">
          <AvatarFallback>{initials(name)}</AvatarFallback>
        </Avatar>
        <div className="min-w-0">
          <p className="flex items-center gap-1.5 text-sm font-medium">
            {icon}
            {name}
          </p>
          <p className="truncate text-xs text-muted-foreground">
            {email}
            {meta ? ` · ${meta}` : ''}
          </p>
        </div>
      </div>
      <Button
        variant="destructive"
        size="sm"
        disabled
        title={NOT_WIRED}
        aria-label={`Revoke access for ${name} (coming later)`}
      >
        Revoke access
      </Button>
    </div>
  );
}

export function UsersSection() {
  return (
    <>
      <SettingsGroup
        title="People with access"
        description="Everyone who can browse or play from this Agent. Names are samples — Portal data is never changed here."
      >
        <div className="flex items-center justify-between py-3 first:pt-0">
          <p className="text-sm text-muted-foreground">
            <DevBadge label="Sample" /> 1 owner · {u.managers.length} manager ·{' '}
            {u.viewers.length} viewers
          </p>
          <Button
            variant="outline"
            size="sm"
            disabled
            title={NOT_WIRED}
            aria-label="Invite user (coming later)"
          >
            <UserPlus aria-hidden="true" />
            Invite User
          </Button>
        </div>

        <PersonRow
          name={u.owner.name}
          email={u.owner.email}
          meta="Owner"
          icon={<Crown className="size-3.5 text-amber-500" aria-hidden="true" />}
        />
        {u.managers.map((m) => (
          <PersonRow
            key={m.email}
            name={m.name}
            email={m.email}
            meta="Manager"
            icon={<UserCog className="size-3.5 text-primary" aria-hidden="true" />}
          />
        ))}
        {u.viewers.map((v) => (
          <PersonRow
            key={v.email}
            name={v.name}
            email={v.email}
            meta={`Viewer · ${v.libraries}`}
            icon={<Users className="size-3.5 text-muted-foreground" aria-hidden="true" />}
          />
        ))}
      </SettingsGroup>

      <SettingsGroup
        title="Pending invitations"
        description="Invitations that have not been accepted yet."
      >
        {u.pendingInvites.length ? (
          u.pendingInvites.map((i) => (
            <div
              key={i.email}
              className="flex items-center justify-between py-3 first:pt-0 last:pb-0 text-sm"
            >
              <span>
                {i.email}{' '}
                <span className="text-xs text-muted-foreground">
                  · sent {i.sent}
                </span>
                <DevBadge label="Sample" />
              </span>
              <Button
                variant="outline"
                size="sm"
                disabled
                title={NOT_WIRED}
                aria-label={`Cancel invitation for ${i.email} (coming later)`}
              >
                Cancel
              </Button>
            </div>
          ))
        ) : (
          <p className="py-3 text-sm text-muted-foreground">No pending invitations.</p>
        )}
      </SettingsGroup>

      <SettingsGroup
        title="Defaults for new viewers"
        description="Applied when you invite someone. You can override per person later."
      >
        <ToggleRow
          id="default_allow_playback"
          label="Allow playback"
          description="New viewers can start streams."
          disabledReason={NOT_WIRED}
        />
        <ToggleRow
          id="default_allow_downloads"
          label="Allow downloads"
          description="New viewers can download for offline viewing (when downloads ship)."
          disabledReason={NOT_WIRED}
        />
        <SelectRow
          id="default_max_quality"
          label="Maximum stream quality"
          options={[
            { value: 'original', label: 'Original' },
            { value: '1080p', label: '1080p' },
            { value: '720p', label: '720p' },
            { value: '480p', label: '480p' },
          ]}
          disabledReason={NOT_WIRED}
        />
        <TextRow
          id="default_max_streams"
          label="Maximum simultaneous streams"
          type="number"
          widthClass="w-24"
          disabledReason={NOT_WIRED}
        />
        <ToggleRow
          id="require_portal_mfa"
          label="Require Portal MFA"
          description="Viewers must complete multi-factor sign-in on the Portal."
          disabledReason={NOT_WIRED}
        />
        <SettingRow
          label="Library access per user"
          description="Choose which libraries each viewer can see."
          disabledReason={NOT_WIRED}
        >
          <span className="inline-flex items-center gap-1.5 text-xs text-muted-foreground">
            <ShieldCheck className="size-3.5" aria-hidden="true" />
            Managed on the Portal
          </span>
        </SettingRow>
      </SettingsGroup>
    </>
  );
}
