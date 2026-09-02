'use client';

import { Cpu, RefreshCw } from 'lucide-react';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { hardwareTestTime, modeLabel, type PlaybackHealthRecord } from '@/lib/transcoding';

export function PlaybackHardware({
  data, busy, error, onRetest,
}: {
  data?: PlaybackHealthRecord;
  busy: boolean;
  error?: string;
  onRetest: () => void;
}) {
  return <Card className="mt-5">
    <CardHeader className="border-b">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <CardTitle className="flex items-center gap-2"><Cpu className="size-4" /> Hardware verification</CardTitle>
        <Button type="button" variant="outline" disabled={busy} onClick={onRetest}><RefreshCw className={busy ? 'animate-spin' : ''} />{busy ? 'Testing encoders…' : 'Retest hardware'}</Button>
      </div>
      <CardDescription>A listed GPU or encoder is not proof of support. Only a successful short encode using this installation&apos;s FFmpeg verifies an encoder. Tests and media remain local.</CardDescription>
    </CardHeader>
    <CardContent className="space-y-4">
      {error ? <Alert variant="destructive"><AlertTitle>Hardware status unavailable</AlertTitle><AlertDescription>{error} No current hardware availability claim can be made.</AlertDescription></Alert> : null}
      {!data && !error ? <Skeleton className="h-28" /> : null}
      {data ? <>
        <dl className="grid gap-4 text-sm sm:grid-cols-2 lg:grid-cols-3">
          <div><dt className="text-muted-foreground">Selected mode</dt><dd className="mt-1">{modeLabel(data.selected_mode)}</dd></div>
          <div><dt className="text-muted-foreground">Encoder for new transcodes</dt><dd className="mt-1 break-all">{data.selected_encoder || 'Unavailable'}</dd></div>
          <div><dt className="text-muted-foreground">Active encoder</dt><dd className="mt-1 break-all">{data.active_encoder || 'No active conversion'}</dd></div>
          <div><dt className="text-muted-foreground">Software fallback</dt><dd className="mt-1">{data.software_fallback === true ? 'In use for new transcodes' : data.software_fallback === false ? 'Not in use' : 'Not reported'}</dd></div>
          <div><dt className="text-muted-foreground">Detected GPUs</dt><dd className="mt-1">{data.detected_gpus?.length ? data.detected_gpus.join(', ') : 'None reported; test encoders to verify support'}</dd></div>
          <div><dt className="text-muted-foreground">Conversion slots</dt><dd className="mt-1">{data.conversions} / {data.max_conversions}</dd></div>
          <div><dt className="text-muted-foreground">Subtitle / probe processes</dt><dd className="mt-1">{data.auxiliary_processes}</dd></div>
        </dl>
        <p className="text-sm text-muted-foreground">Temporary output: {(data.temp_bytes / 1048576).toFixed(1)} / {(data.max_temp_bytes / 1048576).toFixed(0)} MiB. Current streams retain the settings with which they started.</p>
        {data.hardware_tests?.length ? <div className="grid gap-3 lg:grid-cols-3">{data.hardware_tests.map((test, index) => <section key={test.encoder + ':' + test.device + ':' + index} className="min-w-0 rounded-lg border p-4">
          <div className="flex flex-wrap items-center justify-between gap-2"><h3 className="break-all font-medium">{test.encoder}</h3><Badge variant={test.test_status === 'passed' ? 'secondary' : test.test_status === 'failed' ? 'destructive' : 'outline'}>{test.test_status.replaceAll('_', ' ')}</Badge></div>
          <dl className="mt-3 space-y-2 text-sm">
            <div><dt className="text-muted-foreground">GPU / device</dt><dd className="break-words">{test.gpu || 'GPU identity unavailable'} / {test.device}</dd></div>
            <div><dt className="text-muted-foreground">Last test</dt><dd>{hardwareTestTime(test.last_test_at)}</dd></div>
            <div><dt className="text-muted-foreground">Verified codecs</dt><dd>{test.available_codecs.length ? test.available_codecs.join(', ') : 'None verified'}</dd></div>
          </dl>
          {test.failure ? <p className="mt-3 text-sm text-destructive">{test.failure}</p> : null}
        </section>)}</div> : <p className="text-sm text-muted-foreground">No encoder test details are available. Retest hardware before relying on acceleration.</p>}
        {data.maintenance_error ? <Alert variant="destructive"><AlertTitle>Playback maintenance needs attention</AlertTitle><AlertDescription>{data.maintenance_error}</AlertDescription></Alert> : null}
      </> : null}
    </CardContent>
  </Card>;
}
