'use client';

import { RefreshCw, Unlink } from 'lucide-react';
import { Button } from '@/components/ui/button';
import {
  SettingsGroup,
  SettingRow,
  ToggleRow,
  SelectRow,
  TextRow,
  StatusPill,
  PreviewValue,
} from '@/components/viewer/settings/settings-ui';
import { DevBadge } from '@/components/viewer/dev-placeholder';
import { remoteAccessPlaceholders as r } from '@/lib/settings-placeholders';

export function RemoteAccessSection() {
  return (
    <>
      <SettingsGroup
        title="Connection status"
        description="How this Agent reaches the Portal. Full identifiers, keys and pairing codes are never shown here."
      >
        <div className="grid gap-3 py-3 first:pt-0 sm:grid-cols-2">
          <PreviewValue
            label="Portal connection"
            value={<StatusPill status={r.portalStatus} />}
          />
          <PreviewValue label="Paired Portal account" value={r.pairedAccount} />
          <PreviewValue label="Agent ID (short)" value={r.agentIdShort} />
          <PreviewValue label="Agent fingerprint" value={r.fingerprintShort} />
          <PreviewValue label="Last connected" value={r.lastConnected} />
          <PreviewValue label="Connection method" value={r.connectionMethod} />
          <PreviewValue
            label="Encrypted relay"
            value={<StatusPill status={r.relayStatus} />}
          />
          <PreviewValue
            label="Direct connection"
            value={<StatusPill status={r.directStatus} />}
            hint="Direct connections are a future option."
          />
        </div>

        <SettingRow
          label="Reconnect to Portal"
          description="Drops and re-establishes the encrypted connection."
          disabledReason="Connection actions from Settings are coming later"
        >
          <Button variant="outline" size="sm" disabled aria-label="Reconnect (coming later)">
            <RefreshCw aria-hidden="true" />
            Reconnect
          </Button>
        </SettingRow>
        <SettingRow
          label="Unpair this Agent"
          description="Removes this Agent from your Portal account. Use the tray menu to unpair for now."
          disabledReason="Unpairing is only available from the Agent tray menu"
        >
          <Button
            variant="destructive"
            size="sm"
            disabled
            aria-label="Unpair (use the tray menu)"
          >
            <Unlink aria-hidden="true" />
            Unpair
          </Button>
        </SettingRow>
      </SettingsGroup>

      <SettingsGroup
        title="Remote streaming"
        description="Limits applied when someone watches from outside your network."
      >
        <ToggleRow
          id="remote_streaming_enabled"
          label="Remote streaming enabled"
          description="Allow playback when a viewer is not on your local network."
        />
        <SelectRow
          id="remote_max_quality"
          label="Maximum remote quality"
          options={[
            { value: 'original', label: 'Original' },
            { value: '4k', label: '4K' },
            { value: '1080p', label: '1080p' },
            { value: '720p', label: '720p' },
            { value: '480p', label: '480p' },
          ]}
        />
        <TextRow
          id="remote_bitrate_limit"
          label="Remote bitrate limit (Mbps)"
          description="Caps the total outgoing bitrate per remote stream."
          type="number"
          widthClass="w-28"
        />
        <TextRow
          id="remote_session_limit"
          label="Remote session limit"
          description="How many remote streams can run at once."
          type="number"
          widthClass="w-28"
        />
        <p className="pt-3 text-xs text-muted-foreground">
          <DevBadge label="Preview" /> These limits are shown for layout review
          and are not enforced yet.
        </p>
      </SettingsGroup>
    </>
  );
}
