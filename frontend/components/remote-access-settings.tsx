'use client';

import { useEffect, useState } from 'react';
import { ArrowUpRight, Fingerprint, Link2, LoaderCircle, RefreshCw, ShieldCheck, Unplug } from 'lucide-react';
import Link from 'next/link';

import { PageHeader } from '@/components/page-header';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Checkbox } from '@/components/ui/checkbox';
import { Field, FieldDescription, FieldGroup, FieldLabel } from '@/components/ui/field';
import { Input } from '@/components/ui/input';
import { ApiError, apiRequest, jsonBody } from '@/lib/api';
import { productConfig } from '@/lib/product-config';

interface RemoteState {
  available: boolean;
  enabled: boolean;
  paired: boolean;
  state: string;
  account_email: string | null;
  agent_id: string | null;
  name: string | null;
  fingerprint: string | null;
  last_heartbeat: string | null;
  relay_endpoint: string;
  central_revocation_pending: boolean;
  shared_fields: string[];
}

const states: Record<string, string> = {
  disabled: 'Remote access disabled', connected_through_relay: 'Connected through relay',
  agent_offline: 'Agent offline', reconnecting: 'Reconnecting', pairing: 'Pairing',
  pairing_failed: 'Pairing failed', connection_failed: 'Connection failed', revoked: 'Credentials revoked',
  action_pending: 'Action pending',
};

export function RemoteAccessSettings() {
  const [remote, setRemote] = useState<RemoteState>();
  const [code, setCode] = useState('');
  const [name, setName] = useState('My private server');
  const [consent, setConsent] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();
  const [notice, setNotice] = useState<string>();

  useEffect(() => {
    let active = true;
    const refresh = () => apiRequest<RemoteState>('/remote-access').then((state) => {
      if (active) setRemote(state);
    }).catch((caught: unknown) => {
      if (caught instanceof ApiError && caught.status === 401) { window.location.replace('/login'); return; }
      if (active) setError(caught instanceof Error ? caught.message : 'Remote settings could not be loaded.');
    });
    void refresh();
    const interval = window.setInterval(() => { void refresh(); }, 5000);
    return () => { active = false; window.clearInterval(interval); };
  }, []);

  const act = async (action: 'pair' | 'reconnect' | 'unpair' | 'revoke') => {
    setBusy(true); setError(undefined); setNotice(undefined);
    try {
      const state = await apiRequest<RemoteState>(`/remote-access/${action}`, {
        method: 'POST',
        body: jsonBody(action === 'pair' ? { code, name, confirm_enable: consent } : {}),
      });
      setRemote(state);
      if (action === 'pair') { setCode(''); setConsent(false); }
      setNotice(action === 'unpair' || action === 'revoke'
        ? 'Disablement requested. The connector will close the tunnel, remove its local identity, and confirm central revocation.'
        : action === 'pair' ? 'Pairing requested. Compare the fingerprint here with your portal when pairing completes.' : 'Reconnection requested.');
    } catch (caught: unknown) { setError(caught instanceof Error ? caught.message : 'Remote-access action failed.'); }
    finally { setBusy(false); }
  };

  const pending = remote?.state === 'action_pending' || remote?.state === 'pairing';
  const unavailable = !remote?.available;

  return <>
    <PageHeader eyebrow="Owner settings" title="Remote Access" description="Pair your server with your account for an encrypted connection check. Your collection and playback remain local." />
    {error ? <Alert variant="destructive" className="mt-6"><AlertTitle>Action could not be completed</AlertTitle><AlertDescription>{error}</AlertDescription></Alert> : null}
    {notice ? <Alert className="mt-6"><AlertTitle>Request received</AlertTitle><AlertDescription>{notice}</AlertDescription></Alert> : null}
    <div className="mt-6 grid gap-5 xl:grid-cols-[1.15fr_1fr]">
      <Card>
        <CardHeader className="border-b"><CardTitle className="flex items-center gap-2"><Link2 className="size-4" /> Your connection</CardTitle><CardDescription>Disabled by default. Only a local Owner can enable remote access.</CardDescription></CardHeader>
        <CardContent className="space-y-5">
          <Badge variant={remote?.enabled ? 'secondary' : 'outline'}>{remote ? states[remote.state] ?? 'Connection unavailable' : 'Checking connection'}</Badge>
          {unavailable && remote ? <Alert><AlertTitle>Connector unavailable</AlertTitle><AlertDescription>The isolated outbound connector must be installed and running before pairing. Local playback remains available. Docker users can enable the optional remote connector deployment; native installations require the optional AppContainer connector setup.</AlertDescription></Alert> : null}
          {remote?.state === 'pairing_failed' ? <Alert variant="destructive"><AlertTitle>Pairing did not complete</AlertTitle><AlertDescription>Check that the portal is reachable and request a fresh code. If a server card appeared in the portal during an interrupted attempt, revoke that registration before pairing again.</AlertDescription></Alert> : null}
          {remote?.central_revocation_pending ? <Alert><AlertTitle>Central revocation pending</AlertTitle><AlertDescription>The local private key has been removed and this Agent cannot reconnect. A signed revoke-only request will retry when the portal is reachable. You can also revoke this server in your portal.</AlertDescription></Alert> : null}
          {remote?.paired ? <>
            <dl className="grid gap-4 text-sm">
              {[['Account', remote.account_email], ['Agent name', remote.name], ['Last heartbeat', remote.last_heartbeat ? new Date(remote.last_heartbeat).toLocaleString() : 'Not yet connected'], ['Relay endpoint', remote.relay_endpoint]].map(([label, value]) => <div key={label}><dt className="text-xs text-muted-foreground">{label}</dt><dd className="mt-1 break-all font-medium">{value}</dd></div>)}
              <div><dt className="flex items-center gap-2 text-xs text-muted-foreground"><Fingerprint className="size-4" /> Agent fingerprint · SHA-256</dt><dd className="mt-2 break-all rounded-lg border bg-muted/30 p-3 font-mono text-xs">{remote.fingerprint}</dd></div>
            </dl>
            <p className="text-xs leading-relaxed text-muted-foreground">Compare this fingerprint with the server card at {productConfig.domain}. Ownership transfer is unavailable. To rotate the identity, unpair, then pair again with a fresh code.</p>
            <div className="flex flex-wrap gap-2">
              <Button variant="outline" disabled={busy || pending || unavailable} onClick={() => void act('reconnect')}><RefreshCw /> Reconnect</Button>
              <Button variant="outline" disabled={busy || pending} onClick={() => void act('unpair')}><Unplug /> Unpair</Button>
              <Button variant="destructive" disabled={busy || pending} onClick={() => void act('revoke')}>Revoke local credentials</Button>
            </div>
            <p className="text-xs text-muted-foreground">Unpairing and revoking local credentials close the tunnel and remove the local remote identity. Media, local users, history and databases stay in place.</p>
          </> : <FieldGroup>
            <p className="text-sm leading-relaxed text-muted-foreground">Sign in to your portal, choose Pair New Server, then enter its five-minute, single-use code here. Choose a friendly name that does not reveal private paths or media.</p>
            <Button variant="outline" render={<Link href={`https://${productConfig.domain}/portal/pair`} target="_blank" rel="noopener noreferrer" />}>Open {productConfig.domain}<ArrowUpRight /></Button>
            <Field><FieldLabel htmlFor="agent-name">Friendly Agent name</FieldLabel><Input id="agent-name" maxLength={80} value={name} onChange={(event) => setName(event.target.value)} /></Field>
            <Field><FieldLabel htmlFor="pairing-code">Pairing code</FieldLabel><Input id="pairing-code" autoComplete="off" spellCheck={false} maxLength={64} value={code} onChange={(event) => setCode(event.target.value)} placeholder="Enter the code from your portal" /><FieldDescription>The code is used once and is removed from this server after the attempt.</FieldDescription></Field>
            <div className="flex items-start gap-3 text-sm leading-relaxed"><Checkbox id="remote-enable-consent" checked={consent} onCheckedChange={setConsent} className="mt-1" /><label htmlFor="remote-enable-consent" className="cursor-pointer">Enable the outbound connection and share the account, Agent identity and diagnostic information described on this page.</label></div>
            <Button disabled={busy || pending || unavailable || !consent || !name.trim() || code.replace(/[ -]/g, '').length !== 26 || remote?.central_revocation_pending} onClick={() => void act('pair')}>{busy ? <LoaderCircle className="animate-spin" /> : <Link2 />} Pair and enable remote access</Button>
          </FieldGroup>}
        </CardContent>
      </Card>
      <div className="space-y-5">
        <Card><CardHeader><CardTitle className="flex items-center gap-2"><ShieldCheck className="size-4" /> What leaves this server</CardTitle><CardDescription>An outbound TLS connection to {productConfig.domain}. No router changes or inbound ports.</CardDescription></CardHeader><CardContent className="space-y-4"><ul className="space-y-2 text-sm">{remote?.shared_fields.map((field) => <li key={field} className="flex gap-2"><span aria-hidden="true" className="text-primary">✓</span>{field}</li>)}</ul><p className="border-t pt-4 text-sm leading-relaxed text-muted-foreground">Media, titles, filenames, paths, local usernames, library contents, searches, watch history, playback progress, artwork and transcodes remain on your server. The connector has no media or database access.</p></CardContent></Card>
        <Card><CardHeader><CardTitle>Connection checks only</CardTitle></CardHeader><CardContent className="space-y-3 text-sm leading-relaxed text-muted-foreground"><p>This phase supports encrypted synthetic echo and a limited read-only status result. Remote library browsing and video playback are not available yet.</p><p>The relay forwards encrypted diagnostics without storing their contents. Your browser verifies the Agent identity before sending data. A compromised public website could serve malicious browser code; this is not an absolute privacy guarantee against the website operator.</p><p>Your local server continues working when the Internet or public portal is unavailable.</p></CardContent></Card>
      </div>
    </div>
  </>;
}
