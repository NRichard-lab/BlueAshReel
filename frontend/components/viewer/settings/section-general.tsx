'use client';

import {
  SettingsGroup,
  ToggleRow,
  SelectRow,
  TextRow,
  TextareaRow,
  AdvancedSection,
} from '@/components/viewer/settings/settings-ui';

export function GeneralSection() {
  return (
    <>
      <SettingsGroup
        title="Identity"
        description="How this Agent presents itself to you and the Portal."
      >
        <TextRow
          id="agent_name"
          label="Server / Agent name"
          description="Shown in the Portal when you pick which Agent to browse."
          widthClass="w-72"
        />
        <TextareaRow
          id="agent_description"
          label="Friendly server description"
          description="A short note about what this Agent holds. Optional."
        />
      </SettingsGroup>

      <SettingsGroup
        title="Language & region"
        description="Formats used across the interface on this device."
      >
        <SelectRow
          id="language"
          label="Preferred language"
          options={[
            { value: 'en', label: 'English' },
            { value: 'es', label: 'Español' },
            { value: 'fr', label: 'Français' },
            { value: 'de', label: 'Deutsch' },
          ]}
        />
        <SelectRow
          id="region"
          label="Country or region"
          options={[
            { value: 'US', label: 'United States' },
            { value: 'CA', label: 'Canada' },
            { value: 'GB', label: 'United Kingdom' },
            { value: 'AU', label: 'Australia' },
          ]}
        />
        <SelectRow
          id="timezone"
          label="Time zone"
          description="Used for scheduled scans and activity times."
          options={[
            { value: 'America/New_York', label: 'Eastern (America/New_York)' },
            { value: 'America/Chicago', label: 'Central (America/Chicago)' },
            { value: 'America/Denver', label: 'Mountain (America/Denver)' },
            { value: 'America/Los_Angeles', label: 'Pacific (America/Los_Angeles)' },
            { value: 'UTC', label: 'UTC' },
          ]}
        />
        <SelectRow
          id="date_format"
          label="Date format"
          options={[
            { value: 'MMM D, YYYY', label: 'Sep 7, 2026' },
            { value: 'D MMM YYYY', label: '7 Sep 2026' },
            { value: 'YYYY-MM-DD', label: '2026-09-07' },
            { value: 'MM/DD/YYYY', label: '09/07/2026' },
          ]}
        />
        <SelectRow
          id="time_format"
          label="Time format"
          options={[
            { value: '12h', label: '12-hour (3:45 PM)' },
            { value: '24h', label: '24-hour (15:45)' },
          ]}
        />
      </SettingsGroup>

      <SettingsGroup
        title="Startup & connection"
        description="What the Agent does when you sign in to Windows."
      >
        <ToggleRow
          id="start_with_windows"
          label="Start Agent when I sign in to Windows"
          description="Runs the Agent in your system tray after you log in."
        />
        <ToggleRow
          id="auto_reconnect_portal"
          label="Automatically reconnect to Portal"
          description="Re-establishes the encrypted connection if it drops."
        />
        <ToggleRow
          id="open_portal_after_start"
          label="Open Portal after Agent startup"
          description="Launches blueashreel.com in your browser once the Agent is ready."
        />
      </SettingsGroup>

      <SettingsGroup
        title="Updates"
        description="How this development Agent receives new versions."
      >
        <ToggleRow
          id="check_for_updates"
          label="Check for updates"
          description="Looks for a newer Agent build. It never installs on its own unless enabled below."
        />
        <SelectRow
          id="update_channel"
          label="Update channel"
          description="Development builds are unsigned and change frequently."
          options={[
            { value: 'development', label: 'Development' },
            { value: 'stable', label: 'Stable' },
          ]}
        />
        <AdvancedSection>
          <ToggleRow
            id="auto_dev_updates"
            label="Automatic development updates"
            description="Downloads and applies development builds without asking. Not recommended."
          />
          <ToggleRow
            id="anonymous_diagnostics"
            label="Send anonymous diagnostics"
            description="Shares non-identifying error counts and performance timings to help fix bugs. Off by default; no media names or paths are ever included."
          />
        </AdvancedSection>
      </SettingsGroup>
    </>
  );
}
