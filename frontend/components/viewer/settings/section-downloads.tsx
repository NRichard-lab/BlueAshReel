'use client';

import {
  SettingsGroup,
  ToggleRow,
  SelectRow,
  TextRow,
  PlannedBanner,
} from '@/components/viewer/settings/settings-ui';

const PLANNED = 'Downloads are a planned feature and not active in this build';

export function DownloadsSection() {
  return (
    <>
      <PlannedBanner>
        Planned feature — not currently active. This section shows the intended
        layout only.
      </PlannedBanner>

      <SettingsGroup
        title="Offline downloads"
        description="Save titles to a device for viewing without a connection."
      >
        <ToggleRow
          id="downloads_allowed"
          label="Allow downloads"
          description="Let viewers download titles they can access."
          disabledReason={PLANNED}
        />
        <SelectRow
          id="download_quality"
          label="Download quality"
          options={[
            { value: 'original', label: 'Original' },
            { value: '1080p', label: '1080p' },
            { value: '720p', label: '720p' },
            { value: '480p', label: '480p' },
          ]}
          disabledReason={PLANNED}
        />
        <TextRow
          id="download_dir"
          label="Download directory"
          description="Where completed downloads are stored on this workstation."
          widthClass="w-72"
          disabledReason={PLANNED}
        />
        <TextRow
          id="simultaneous_downloads"
          label="Simultaneous downloads"
          type="number"
          widthClass="w-24"
          disabledReason={PLANNED}
        />
        <TextRow
          id="download_temp_dir"
          label="Temporary download storage"
          description="Working space used while a download is in progress."
          widthClass="w-72"
          disabledReason={PLANNED}
        />
        <ToggleRow
          id="remove_watched_downloads"
          label="Automatically remove watched downloads"
          description="Delete a downloaded title once it has been finished."
          disabledReason={PLANNED}
        />
      </SettingsGroup>
    </>
  );
}
