'use client';

import {
  SettingsGroup,
  ToggleRow,
  SelectRow,
  AdvancedSection,
} from '@/components/viewer/settings/settings-ui';
import { DevBadge } from '@/components/viewer/dev-placeholder';

export function PlayerSection() {
  return (
    <>
      <SettingsGroup
        title="Quality"
        description="How the player picks a stream on this device."
      >
        <SelectRow
          id="default_quality"
          label="Default playback quality"
          options={[
            { value: 'original', label: 'Original / maximum' },
            { value: '1080p', label: '1080p' },
            { value: '720p', label: '720p' },
            { value: '480p', label: '480p' },
          ]}
        />
        <ToggleRow
          id="auto_select_quality"
          label="Automatically select quality"
          description="Adjust quality to the current connection speed."
        />
        <ToggleRow
          id="prefer_direct_play"
          label="Prefer Direct Play"
          description="Play the original file untouched whenever the device supports it."
        />
        <ToggleRow
          id="allow_direct_stream"
          label="Allow Direct Stream / remux"
          description="Repackage the file without re-encoding when only the container is incompatible."
        />
      </SettingsGroup>

      <SettingsGroup
        title="Languages & subtitles"
        description="Defaults applied when a title has multiple tracks."
      >
        <SelectRow
          id="audio_language_preference"
          label="Audio-language preference"
          options={[
            { value: 'original', label: 'Original language' },
            { value: 'en', label: 'English' },
            { value: 'es', label: 'Spanish' },
            { value: 'fr', label: 'French' },
          ]}
        />
        <SelectRow
          id="subtitle_preference"
          label="Subtitle preference"
          options={[
            { value: 'off', label: 'Off' },
            { value: 'forced-only', label: 'Forced only' },
            { value: 'always', label: 'Always on' },
            { value: 'non-native', label: 'When audio is not my language' },
          ]}
        />
        <ToggleRow
          id="remember_subtitle"
          label="Remember subtitle selection"
          description="Reuse the last subtitle choice for the next episode."
        />
      </SettingsGroup>

      <SettingsGroup title="Playback behavior">
        <ToggleRow
          id="autoplay_next"
          label="Autoplay next episode"
          description="Start the next episode automatically after a short countdown."
        />
        <SelectRow
          id="resume_behavior"
          label="Resume behavior"
          options={[
            { value: 'ask', label: 'Ask to resume' },
            { value: 'always', label: 'Always resume' },
            { value: 'never', label: 'Always start over' },
          ]}
        />
        <SelectRow
          id="fullscreen_behavior"
          label="Full-screen behavior"
          options={[
            { value: 'match-window', label: 'Match the browser window' },
            { value: 'always', label: 'Go full screen on play' },
            { value: 'never', label: 'Stay in the page' },
          ]}
        />

        <AdvancedSection>
          <ToggleRow
            id="skip_intro"
            label="Skip-intro button"
            description="Show a Skip Intro prompt when intro markers are available."
            disabledReason="Intro markers are not generated yet"
          />
          <ToggleRow
            id="show_technical_info"
            label="Show technical playback information"
            description="Display the stream method, bitrate and decode details on the player."
          />
          <SelectRow
            id="player_buffer"
            label="Player buffer preference"
            options={[
              { value: 'small', label: 'Smaller (start faster)' },
              { value: 'balanced', label: 'Balanced' },
              { value: 'large', label: 'Larger (fewer stalls)' },
            ]}
          />
        </AdvancedSection>
      </SettingsGroup>

      <p className="text-xs text-muted-foreground">
        <DevBadge label="Preview" /> Player preferences are held in this page only
        for now.
      </p>
    </>
  );
}
