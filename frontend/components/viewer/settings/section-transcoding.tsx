'use client';

import { Cpu, Gauge, Play, RotateCcw } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { RadioGroup, RadioGroupItem } from '@/components/ui/radio-group';
import { Label } from '@/components/ui/label';
import { cn } from '@/lib/utils';
import {
  SettingsGroup,
  SettingRow,
  ToggleRow,
  SelectRow,
  TextRow,
  AdvancedSection,
  PreviewValue,
  useSetting,
} from '@/components/viewer/settings/settings-ui';
import { DevBadge } from '@/components/viewer/dev-placeholder';
import {
  hardwareCapabilityCards,
  transcodingDetected,
} from '@/lib/settings-placeholders';

const MODES: { value: string; label: string; blurb: string }[] = [
  {
    value: 'automatic',
    label: 'Automatic — recommended',
    blurb:
      'Uses Direct Play or remux whenever possible, then verified hardware acceleration, with safe software fallback.',
  },
  {
    value: 'hardware',
    label: 'Hardware preferred',
    blurb:
      'Prioritizes a supported GPU but falls back to software if hardware initialization fails.',
  },
  {
    value: 'software',
    label: 'Software only',
    blurb:
      'Uses CPU transcoding and never initializes a hardware encoder.',
  },
];

export function TranscodingSection() {
  const [mode, setMode] = useSetting('transcode_mode');

  return (
    <>
      <SettingsGroup
        title="Transcoding mode"
        description="How the Agent prepares video that a device cannot play directly."
      >
        <RadioGroup
          className="gap-3 py-3 first:pt-0"
          value={String(mode)}
          onValueChange={(v: string) => setMode(v)}
        >
          {MODES.map((m) => (
            <Label
              key={m.value}
              htmlFor={`mode-${m.value}`}
              className={cn(
                'flex cursor-pointer gap-3 rounded-xl border p-4 transition-colors',
                mode === m.value
                  ? 'border-primary bg-primary/5'
                  : 'border-border hover:bg-muted/40',
              )}
            >
              <RadioGroupItem
                id={`mode-${m.value}`}
                value={m.value}
                className="mt-0.5"
              />
              <span>
                <span className="block text-sm font-medium">{m.label}</span>
                <span className="mt-1 block text-xs leading-relaxed text-muted-foreground">
                  {m.blurb}
                </span>
              </span>
            </Label>
          ))}
        </RadioGroup>
      </SettingsGroup>

      <SettingsGroup
        title="Hardware acceleration"
        description="Detected once you run a capability test. Nothing here claims support until a real test encode succeeds."
      >
        <div className="grid gap-3 py-3 first:pt-0 sm:grid-cols-2">
          <PreviewValue label="Detected GPU" value={transcodingDetected.gpu} />
          <PreviewValue
            label="Hardware backend"
            value={transcodingDetected.backend}
          />
          <PreviewValue
            label="Hardware capability"
            value={transcodingDetected.capability}
            hint="Run the capability test to populate this."
          />
        </div>

        <div className="grid gap-3 py-3 sm:grid-cols-2">
          {hardwareCapabilityCards.map((card) => (
            <div
              key={card.id}
              className="flex items-start gap-3 rounded-xl border border-border bg-background/40 p-4"
            >
              <Cpu
                className="mt-0.5 size-4 shrink-0 text-primary/70"
                aria-hidden="true"
              />
              <div className="min-w-0">
                <p className="text-sm font-medium">{card.name}</p>
                <p className="text-xs text-muted-foreground">{card.detail}</p>
                <p
                  className={cn(
                    'mt-1.5 inline-flex rounded-full px-2 py-0.5 text-[11px]',
                    card.status === 'Available'
                      ? 'bg-[var(--success-soft)] text-[var(--success-foreground)]'
                      : 'bg-muted text-muted-foreground',
                  )}
                >
                  {card.status}
                </p>
              </div>
            </div>
          ))}
        </div>

        <ToggleRow
          id="hw_decoding"
          label="Enable hardware decoding"
          description="Let a supported GPU decode the source video."
        />
        <ToggleRow
          id="hw_encoding"
          label="Enable hardware encoding"
          description="Let a supported GPU encode the output video."
        />
        <ToggleRow
          id="allow_software_fallback"
          label="Allow software fallback"
          description="If hardware fails to start, continue on CPU instead of stopping playback."
        />

        <SettingRow
          label="Run hardware capability test"
          description="Runs a short encode to confirm what this GPU can actually do."
          disabledReason="Capability detection is not part of this design pass"
        >
          <Button variant="outline" size="sm" disabled aria-label="Run capability test (coming later)">
            <Play aria-hidden="true" />
            Run test
          </Button>
        </SettingRow>
      </SettingsGroup>

      <SettingsGroup
        title="Quality & limits"
        description="Caps that protect this workstation from being overloaded."
      >
        <SelectRow
          id="transcode_quality"
          label="Transcoding quality"
          options={[
            { value: 'prefer-speed', label: 'Prefer speed' },
            { value: 'balanced', label: 'Balanced' },
            { value: 'prefer-quality', label: 'Prefer quality' },
          ]}
        />
        <SelectRow
          id="max_output_resolution"
          label="Maximum output resolution"
          options={[
            { value: 'source', label: 'Match source' },
            { value: '4k', label: '4K' },
            { value: '1080p', label: '1080p' },
            { value: '720p', label: '720p' },
          ]}
        />
        <TextRow
          id="max_output_bitrate"
          label="Maximum output bitrate (Mbps)"
          type="number"
          widthClass="w-28"
        />
        <TextRow
          id="max_video_transcodes"
          label="Maximum simultaneous video transcodes"
          type="number"
          widthClass="w-24"
        />
        <TextRow
          id="max_audio_transcodes"
          label="Maximum simultaneous audio transcodes"
          type="number"
          widthClass="w-24"
        />
        <ToggleRow
          id="hdr_tone_mapping"
          label="HDR tone mapping"
          description="Convert HDR to SDR for devices that cannot display HDR."
        />

        <AdvancedSection>
          <SelectRow
            id="background_preset"
            label="Background transcoding preset"
            description="Encoder speed/quality trade-off for non-live conversions."
            options={[
              { value: 'ultrafast', label: 'ultrafast' },
              { value: 'veryfast', label: 'veryfast' },
              { value: 'faster', label: 'faster' },
              { value: 'medium', label: 'medium' },
            ]}
          />
          <TextRow
            id="cpu_thread_limit"
            label="CPU thread limit"
            description="0 lets the encoder choose. Lower values leave more CPU for other work."
            type="number"
            widthClass="w-24"
          />
          <TextRow
            id="temp_transcode_dir"
            label="Temporary transcode directory"
            description="Fast local disk with free space. Example path shown."
            widthClass="w-72"
          />
          <TextRow
            id="buffer_ahead"
            label="Transcode buffer ahead (seconds)"
            type="number"
            widthClass="w-24"
          />
          <TextRow
            id="hls_segment_duration"
            label="HLS segment duration (seconds)"
            type="number"
            widthClass="w-24"
          />
          <SelectRow
            id="subtitle_burn_in"
            label="Subtitle burn-in behavior"
            options={[
              { value: 'never', label: 'Never burn in' },
              { value: 'image-only', label: 'Image-based subtitles only' },
              { value: 'always', label: 'Always when transcoding' },
            ]}
          />
          <ToggleRow
            id="delete_segments_after"
            label="Delete temporary segments after playback"
            description="Frees disk as soon as a stream ends."
          />
          <SettingRow
            label="Reset transcoding defaults"
            description="Restores every control in this section to its default."
            disabledReason="Reset is wired once Settings can save to the Agent"
          >
            <Button
              variant="outline"
              size="sm"
              disabled
              aria-label="Reset transcoding defaults (coming later)"
            >
              <RotateCcw aria-hidden="true" />
              Reset
            </Button>
          </SettingRow>
        </AdvancedSection>
      </SettingsGroup>

      <p className="flex items-center gap-2 text-xs text-muted-foreground">
        <Gauge className="size-3.5" aria-hidden="true" />
        <DevBadge label="Preview" /> None of these values change FFmpeg behavior
        in this build.
      </p>
    </>
  );
}
