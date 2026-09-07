'use client';

import { Wifi } from 'lucide-react';
import { Button } from '@/components/ui/button';
import {
  SettingsGroup,
  SettingRow,
  ToggleRow,
  SelectRow,
  TextRow,
  AdvancedSection,
  PreviewValue,
} from '@/components/viewer/settings/settings-ui';
import { DevBadge } from '@/components/viewer/dev-placeholder';
import { networkPlaceholders as n } from '@/lib/settings-placeholders';

export function NetworkSection() {
  return (
    <>
      <SettingsGroup
        title="Local access"
        description="How you reach the Agent's management interface on this machine."
      >
        <div className="grid gap-3 py-3 first:pt-0 sm:grid-cols-2">
          <PreviewValue label="Local Agent address" value={n.localAddress} />
          <PreviewValue
            label="Public address"
            value={n.publicHint}
            hint="Never shown here unless you run a connection test."
          />
        </div>
        <TextRow
          id="loopback_port"
          label="Loopback management port"
          description="The Agent listens on 127.0.0.1 at this port. Changing it needs a restart."
          type="number"
          widthClass="w-28"
          disabledReason="The management port is set during installation"
        />
        <SelectRow
          id="preferred_interface"
          label="Preferred network interface"
          options={[
            { value: 'auto', label: 'Automatic' },
            { value: 'ethernet', label: 'Wired (Ethernet)' },
            { value: 'wifi', label: 'Wi-Fi' },
          ]}
        />
      </SettingsGroup>

      <SettingsGroup
        title="Connections"
        description="Which connection types this Agent will use or accept."
      >
        <SelectRow
          id="secure_connections"
          label="Secure connections"
          options={[
            { value: 'preferred', label: 'Preferred' },
            { value: 'required', label: 'Required' },
            { value: 'disabled', label: 'Disabled (not recommended)' },
          ]}
        />
        <ToggleRow
          id="relay_connection"
          label="Relay connection"
          description="Route remote traffic through the encrypted Portal relay when a direct path is unavailable."
        />
        <ToggleRow
          id="direct_remote"
          label="Direct remote connections"
          description="Allow remote viewers to connect straight to this Agent when possible."
        />
        <ToggleRow
          id="lan_discovery"
          label="LAN discovery"
          description="Let devices on your local network find this Agent automatically."
        />
        <TextRow
          id="allowed_lan_networks"
          label="Allowed LAN networks"
          description="Comma-separated CIDR ranges treated as local. Example range shown."
          widthClass="w-72"
        />
      </SettingsGroup>

      <SettingsGroup title="Limits & reliability">
        <TextRow
          id="remote_bandwidth_limit"
          label="Remote bandwidth limit (Mbps)"
          description="Total upload the Agent will use for all remote streams combined."
          type="number"
          widthClass="w-28"
        />
        <TextRow
          id="connection_timeout"
          label="Connection timeout (seconds)"
          type="number"
          widthClass="w-24"
        />
        <SelectRow
          id="reconnect_behavior"
          label="Reconnect behavior"
          options={[
            { value: 'auto', label: 'Reconnect automatically' },
            { value: 'manual', label: 'Wait for me' },
          ]}
        />

        <AdvancedSection>
          <SelectRow
            id="proxy_mode"
            label="Proxy configuration"
            options={[
              { value: 'none', label: 'No proxy' },
              { value: 'system', label: 'Use system proxy' },
              { value: 'manual', label: 'Manual (coming later)' },
            ]}
          />
          <SettingRow
            label="Custom certificate"
            description={n.certificate}
            disabledReason="Custom certificates are configured during installation"
          >
            <Button
              variant="outline"
              size="sm"
              disabled
              aria-label="Manage certificate (coming later)"
            >
              Manage
            </Button>
          </SettingRow>
          <SelectRow
            id="ipv6_support"
            label="IPv6 support"
            options={[
              { value: 'auto', label: 'Automatic' },
              { value: 'on', label: 'Enabled' },
              { value: 'off', label: 'Disabled' },
            ]}
          />
        </AdvancedSection>

        <SettingRow
          label="Test network connection"
          description="Checks local access, relay reachability and remote path."
          disabledReason="Network testing is coming later"
        >
          <Button
            variant="outline"
            size="sm"
            disabled
            aria-label="Test network connection (coming later)"
          >
            <Wifi aria-hidden="true" />
            Run test
          </Button>
        </SettingRow>
      </SettingsGroup>

      <p className="flex items-center gap-2 text-xs text-muted-foreground">
        <DevBadge label="Preview" /> Addresses shown are examples. This build does
        not change any network configuration.
      </p>
    </>
  );
}
