'use client';

import { Fragment, useCallback, useEffect, useRef, useState } from 'react';
import { ChevronDown, ChevronRight, Folder, FolderCheck, FolderOpen, LoaderCircle, RotateCcw } from 'lucide-react';

import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Breadcrumb, BreadcrumbItem, BreadcrumbList, BreadcrumbPage, BreadcrumbSeparator } from '@/components/ui/breadcrumb';
import { Button } from '@/components/ui/button';
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Empty, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from '@/components/ui/empty';
import { Field, FieldDescription, FieldLabel } from '@/components/ui/field';
import { Input } from '@/components/ui/input';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Skeleton } from '@/components/ui/skeleton';
import { ApiError, apiRequest, jsonBody, type MediaFolderPage, type MediaFolderSelection, type MediaRootSummary } from '@/lib/api';

export const WINDOWS_MEDIA_PATH_MESSAGE =
  'Windows folders must first be configured as approved media roots. Use the local bootstrap workflow, then browse the mounted folder here.';

export function manualMediaPathProblem(value: string): string | undefined {
  const trimmed = value.trim();
  if (!trimmed) return 'Enter an internal mounted path to validate.';
  if (/^[a-zA-Z]:[\\/]/.test(trimmed) || trimmed.startsWith('\\\\')) return WINDOWS_MEDIA_PATH_MESSAGE;
  if (!trimmed.startsWith('/')) return 'Manual paths must use an internal mounted path inside an approved media root.';
  return undefined;
}

export function nextFolderFocusIndex(current: number, key: string, count: number): number {
  if (count <= 0) return current;
  if (key === 'ArrowDown') return Math.min(count - 1, current + 1);
  if (key === 'ArrowUp') return Math.max(0, current - 1);
  if (key === 'Home') return 0;
  if (key === 'End') return count - 1;
  return current;
}

export function nextAvailableFolderFocusIndex(current: number, key: string, available: boolean[]): number {
  const indices = available.flatMap((value, index) => value ? [index] : []);
  if (!indices.length) return current;
  if (key === 'Home') return indices[0];
  if (key === 'End') return indices.at(-1) ?? current;
  if (key === 'ArrowDown') return indices.find((index) => index > current) ?? current;
  if (key === 'ArrowUp') return indices.findLast((index) => index < current) ?? current;
  return current;
}

function errorMessage(caught: unknown): string {
  if (caught instanceof ApiError && caught.status === 401) return 'This setup or sign-in session expired. Refresh the page and try again.';
  return caught instanceof Error ? caught.message : 'The folder list is unavailable right now.';
}

export function FolderBrowserDialog({
  open,
  onOpenChange,
  onSelect,
  initialRootId,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onSelect: (selection: MediaFolderSelection) => void;
  initialRootId?: string;
}) {
  const [roots, setRoots] = useState<MediaRootSummary[]>([]);
  const [rootId, setRootId] = useState('');
  const [page, setPage] = useState<MediaFolderPage>();
  const [items, setItems] = useState<MediaFolderSelection[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string>();
  const [reloadKey, setReloadKey] = useState(0);
  const [focusedIndex, setFocusedIndex] = useState(0);
  const requestId = useRef(0);
  const attemptedSelectionId = useRef<string | undefined>(undefined);
  const navigationFocusPending = useRef<{ selectionId?: string } | undefined>(undefined);
  const rowRefs = useRef<Array<HTMLButtonElement | null>>([]);
  const folderListRef = useRef<HTMLDivElement | null>(null);
  const selectFolderRef = useRef<HTMLButtonElement | null>(null);

  const focusFolderRow = useCallback((index: number) => {
    const row = rowRefs.current[index];
    const list = folderListRef.current;
    if (!row || !list) return;
    row.focus({ preventScroll: true });
    const rowBounds = row.getBoundingClientRect();
    const listBounds = list.getBoundingClientRect();
    if (rowBounds.top < listBounds.top) list.scrollTop -= listBounds.top - rowBounds.top;
    else if (rowBounds.bottom > listBounds.bottom) list.scrollTop += rowBounds.bottom - listBounds.bottom;
  }, []);

  const browse = useCallback(async (selectionId: string, cursor?: string, append = false) => {
    const currentRequest = ++requestId.current;
    attemptedSelectionId.current = selectionId;
    setLoading(true); setError(undefined);
    try {
      const result = await apiRequest<MediaFolderPage>('/media-folders/browse', {
        method: 'POST',
        body: jsonBody({ selection_id: selectionId, cursor: cursor ?? null, page_size: 100 }),
      });
      if (currentRequest !== requestId.current) return;
      setPage(result);
      setRootId(result.root.id);
      setItems((current) => append ? [...current, ...result.items] : result.items);
      navigationFocusPending.current = {
        selectionId: result.items.find((item) => item.available && item.readable)?.selection_id,
      };
    } catch (caught: unknown) {
      if (currentRequest === requestId.current) setError(errorMessage(caught));
    } finally {
      if (currentRequest === requestId.current) setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!open || loading || !navigationFocusPending.current) return;
    // Wait until new rows and the dialog's next-frame focus restoration finish.
    // Loading another page focuses its first new row, even when Load more disappears.
    const currentRequest = requestId.current;
    let focusFrame: number | undefined;
    const frame = requestAnimationFrame(() => {
      focusFrame = requestAnimationFrame(() => {
        if (currentRequest !== requestId.current) return;
        const pending = navigationFocusPending.current;
        navigationFocusPending.current = undefined;
        const index = items.findIndex((item) => item.selection_id === pending?.selectionId);
        if (index >= 0) {
          setFocusedIndex(index);
          focusFolderRow(index);
        } else {
          selectFolderRef.current?.focus({ preventScroll: true });
        }
      });
    });
    return () => { cancelAnimationFrame(frame); if (focusFrame !== undefined) cancelAnimationFrame(focusFrame); };
  }, [focusFolderRow, items, loading, open]);

  useEffect(() => {
    if (!open) return;
    const currentRequest = ++requestId.current;
    attemptedSelectionId.current = undefined;
    navigationFocusPending.current = undefined;
    setLoading(true); setError(undefined); setPage(undefined); setItems([]);
    apiRequest<{ items: MediaRootSummary[] }>('/media-roots')
      .then((result) => {
        if (currentRequest !== requestId.current) return;
        setRoots(result.items);
        const initial = result.items.find((root) => root.id === initialRootId && root.available && root.readable && root.read_only_enforced !== false)
          ?? result.items.find((root) => root.available && root.readable && root.read_only_enforced !== false);
        if (initial) void browse(initial.selection_id);
      })
      .catch((caught: unknown) => { if (currentRequest === requestId.current) setError(errorMessage(caught)); })
      .finally(() => { if (currentRequest === requestId.current) setLoading(false); });
    return () => { requestId.current += 1; };
  }, [browse, initialRootId, open, reloadKey]);

  const chooseRoot = (nextRootId: string | null) => {
    if (!nextRootId) return;
    const root = roots.find((item) => item.id === nextRootId);
    setRootId(nextRootId); setPage(undefined); setItems([]);
    if (root?.available && root.readable && root.read_only_enforced !== false) void browse(root.selection_id);
  };

  const moveFocus = (index: number, key: string) => {
    if (!items.length) return;
    const next = nextAvailableFolderFocusIndex(
      index,
      key,
      items.map((item) => item.available && item.readable),
    );
    setFocusedIndex(next);
    focusFolderRow(next);
  };

  const currentRoot = roots.find((root) => root.id === rootId);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="flex h-[calc(100dvh-1rem)] max-h-[calc(100dvh-1rem)] grid-rows-none flex-col overflow-clip sm:h-auto sm:max-h-[calc(100dvh-2rem)] sm:max-w-2xl">
        <DialogHeader className="shrink-0">
          <DialogTitle>Browse media folders</DialogTitle>
          <DialogDescription>Choose a folder inside storage that was approved on this computer. Only folders are shown.</DialogDescription>
        </DialogHeader>

        {roots.length > 1 ? (
          <Field className="shrink-0">
            <FieldLabel htmlFor="approved-media-root">Approved media storage</FieldLabel>
            <Select value={rootId} onValueChange={chooseRoot}>
              <SelectTrigger id="approved-media-root" className="w-full"><SelectValue placeholder="Choose storage">{currentRoot?.display_name}</SelectValue></SelectTrigger>
              <SelectContent>{roots.map((root) => <SelectItem key={root.id} value={root.id} disabled={!root.available || !root.readable || root.read_only_enforced === false}>{root.display_name}{!root.available || !root.readable ? ' · unavailable' : root.read_only_enforced === false ? ' · not read-only' : ''}</SelectItem>)}</SelectContent>
            </Select>
            {currentRoot ? <FieldDescription>{currentRoot.read_only_enforced === true ? 'Read-only enforced' : currentRoot.read_only_enforced === false ? 'Not read-only — selection blocked' : 'Read-only configured'}</FieldDescription> : null}
          </Field>
        ) : currentRoot ? (
          <div className="flex shrink-0 items-center gap-2 rounded-lg border bg-muted/25 px-3 py-2 text-sm"><FolderCheck className="size-4 text-primary" /><span className="font-medium">{currentRoot.display_name}</span><span className="ml-auto text-xs text-muted-foreground">{currentRoot.read_only_enforced === true ? 'Read-only enforced' : currentRoot.read_only_enforced === false ? 'Not read-only' : 'Read-only configured'}</span></div>
        ) : null}

        {page ? (
          <div className="shrink-0 overflow-x-auto pb-1">
            <Breadcrumb>
              <BreadcrumbList className="flex-nowrap whitespace-nowrap">
                {page.breadcrumbs.map((crumb, index) => <Fragment key={crumb.selection_id}>
                  {index ? <BreadcrumbSeparator /> : null}
                  <BreadcrumbItem>{index === page.breadcrumbs.length - 1
                    ? <BreadcrumbPage>{crumb.name}</BreadcrumbPage>
                    : <Button variant="link" className="h-auto p-0 text-muted-foreground" onClick={() => void browse(crumb.selection_id)}>{crumb.name}</Button>}
                  </BreadcrumbItem>
                </Fragment>)}
              </BreadcrumbList>
            </Breadcrumb>
          </div>
        ) : null}

        <div ref={folderListRef} className="min-h-0 flex-1 overflow-y-auto sm:max-h-[min(48dvh,22rem)]" aria-busy={loading}>
          {error ? <Alert variant="destructive"><AlertTitle>Folders unavailable</AlertTitle><AlertDescription>{error}</AlertDescription><Button className="mt-3" size="sm" variant="outline" onClick={() => attemptedSelectionId.current ? void browse(attemptedSelectionId.current) : setReloadKey((value) => value + 1)}><RotateCcw /> Retry</Button></Alert> : null}
          {!error && loading && !page ? <output className="block space-y-2" aria-live="polite"><span className="sr-only">Loading folders</span><Skeleton className="h-12" /><Skeleton className="h-12" /><Skeleton className="h-12" /></output> : null}
          {!error && !loading && roots.length === 0 ? <Empty><EmptyHeader><EmptyMedia variant="icon"><FolderOpen /></EmptyMedia><EmptyTitle>No approved media storage</EmptyTitle><EmptyDescription>Run the local bootstrap workflow to approve a host folder, then recreate the containers.</EmptyDescription></EmptyHeader></Empty> : null}
          {!error && !loading && roots.length > 0 && !page ? <Empty><EmptyHeader><EmptyMedia variant="icon"><FolderOpen /></EmptyMedia><EmptyTitle>Approved storage unavailable</EmptyTitle><EmptyDescription>The configured folders are missing or cannot be read. Reconnect the storage or check the local mount configuration.</EmptyDescription></EmptyHeader></Empty> : null}
          {!error && page && items.length === 0 ? <Empty><EmptyHeader><EmptyMedia variant="icon"><FolderOpen /></EmptyMedia><EmptyTitle>This folder has no subfolders</EmptyTitle><EmptyDescription>You can still select the current folder.</EmptyDescription></EmptyHeader></Empty> : null}
          {!error && items.length ? <ul aria-label={`Folders in ${page?.current.display_path ?? 'media storage'}`} className="divide-y rounded-lg border p-1">
              {items.map((folder, index) => <li key={folder.selection_id}><button
                ref={(node) => { rowRefs.current[index] = node; }}
                type="button"
                tabIndex={index === focusedIndex ? 0 : -1}
                className="flex min-h-11 w-full items-center gap-3 rounded-md px-3 py-2 text-left hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                disabled={!folder.available || !folder.readable}
                onFocus={() => setFocusedIndex(index)}
                onClick={() => void browse(folder.selection_id)}
                onKeyDown={(event) => { if (['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) { event.preventDefault(); moveFocus(index, event.key); } }}
              ><Folder className="size-4 shrink-0 text-primary" /><span className="min-w-0 flex-1 truncate font-medium">{folder.name}</span>{!folder.available || !folder.readable ? <span className="text-xs text-destructive">{folder.status === 'permission_denied' ? 'Permission denied' : 'Unavailable'}</span> : <ChevronRight className="size-4 text-muted-foreground" />}<span className="sr-only">{folder.available && folder.readable ? 'Open folder' : 'Folder cannot be opened'}</span></button></li>)}
            </ul> : null}
          {!error && page?.next_cursor ? <Button variant="outline" className="mt-2 w-full" disabled={loading} onClick={() => void browse(page.current.selection_id, page.next_cursor ?? undefined, true)}>{loading ? <LoaderCircle className="animate-spin" /> : null} Load more folders</Button> : null}
        </div>

        <p className="sr-only" aria-live="polite">{page ? `Opened ${page.current.display_path}. ${items.length} folders shown.` : ''}</p>
        <DialogFooter className="shrink-0">
          <Button className="min-h-11 sm:min-h-8" variant="outline" onClick={() => onOpenChange(false)}>Cancel</Button>
          <Button className="min-h-11 sm:min-h-8" variant="outline" disabled={!page || loading || Boolean(error) || page.breadcrumbs.length < 2} onClick={() => page && void browse(page.breadcrumbs.at(-2)?.selection_id ?? page.current.selection_id)}>Back</Button>
          <Button ref={selectFolderRef} className="min-h-11 sm:min-h-8" disabled={!page || loading || Boolean(error) || !page.current.available || !page.current.readable || page.root.read_only_enforced === false} onClick={() => { if (page) { onSelect(page.current); onOpenChange(false); } }}><FolderCheck /> Select Folder</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export function MediaFolderField({
  id,
  selection,
  onSelectionChange,
  initialRootId,
}: {
  id: string;
  selection?: MediaFolderSelection;
  onSelectionChange: (selection: MediaFolderSelection | undefined) => void;
  initialRootId?: string;
}) {
  const [open, setOpen] = useState(false);
  const [advanced, setAdvanced] = useState(false);
  const [manualPath, setManualPath] = useState('');
  const [validating, setValidating] = useState(false);
  const [manualError, setManualError] = useState<string>();
  const [approvedRoots, setApprovedRoots] = useState<MediaRootSummary[]>([]);
  const validationRequestId = useRef(0);

  useEffect(() => {
    if (!advanced || approvedRoots.length) return;
    apiRequest<{ items: MediaRootSummary[] }>('/media-roots')
      .then((result) => setApprovedRoots(result.items))
      .catch(() => undefined);
  }, [advanced, approvedRoots.length]);

  const validateManual = async () => {
    const candidate = manualPath.trim();
    const currentRequest = ++validationRequestId.current;
    const localProblem = manualMediaPathProblem(candidate);
    if (localProblem) { setManualError(localProblem); return; }
    setValidating(true); setManualError(undefined);
    try {
      const result = await apiRequest<MediaFolderSelection>('/media-folders/validate', { method: 'POST', body: jsonBody({ path: candidate }) });
      if (currentRequest === validationRequestId.current) onSelectionChange(result);
    } catch (caught: unknown) { if (currentRequest === validationRequestId.current) setManualError(errorMessage(caught)); }
    finally { if (currentRequest === validationRequestId.current) setValidating(false); }
  };

  const acceptBrowsedSelection = (nextSelection: MediaFolderSelection) => {
    validationRequestId.current += 1;
    setValidating(false);
    setManualPath('');
    setManualError(undefined);
    onSelectionChange(nextSelection);
  };

  return <Field>
    <FieldLabel htmlFor={id}>Library folder</FieldLabel>
    <p className="text-sm leading-relaxed text-muted-foreground">Choose the folder that contains this library. BlueReel can read your media but cannot change or delete the source files.</p>
    <div className="grid gap-2 sm:grid-cols-[minmax(0,1fr)_auto]">
      <Input id={id} readOnly value={selection?.display_path ?? ''} placeholder="No folder selected" aria-describedby={`${id}-description`} />
      <Button type="button" size="lg" onClick={() => setOpen(true)}><FolderOpen /> Browse folders</Button>
    </div>
    <FieldDescription id={`${id}-description`}>{selection ? 'Selected from approved read-only media storage.' : 'Browse folders to choose from approved media storage.'}</FieldDescription>
    <FolderBrowserDialog open={open} onOpenChange={setOpen} onSelect={acceptBrowsedSelection} initialRootId={initialRootId ?? selection?.root_id} />
    <Collapsible open={advanced} onOpenChange={setAdvanced}>
      <CollapsibleTrigger render={<Button type="button" variant="ghost" size="sm" className="px-0 text-muted-foreground" />}><ChevronDown className={`transition-transform ${advanced ? 'rotate-180' : ''}`} /> Advanced manual entry</CollapsibleTrigger>
      <CollapsibleContent className="mt-2 space-y-2 rounded-lg border bg-muted/20 p-3">
        {selection?.internal_path ? <p className="break-all text-xs text-muted-foreground">Current internal mounted path: <code>{selection.internal_path}</code></p> : null}
        <FieldLabel htmlFor={`${id}-manual`}>Internal mounted path</FieldLabel>
        <div className="grid gap-2 sm:grid-cols-[minmax(0,1fr)_auto]"><Input id={`${id}-manual`} value={manualPath} onChange={(event) => { const value = event.target.value; validationRequestId.current += 1; setValidating(false); setManualPath(value); setManualError(value ? manualMediaPathProblem(value) : undefined); onSelectionChange(undefined); }} onBlur={() => { if (manualPath && !manualMediaPathProblem(manualPath)) void validateManual(); }} placeholder="/media/approved-root/folder" /><Button type="button" variant="outline" disabled={validating} onPointerDown={(event) => event.preventDefault()} onClick={() => void validateManual()}>{validating ? <LoaderCircle className="animate-spin" /> : null} Validate</Button></div>
        <p className="text-xs text-muted-foreground">Only internal paths within an approved root are accepted. Windows host paths are never guessed or rewritten.</p>
        {approvedRoots.length ? <p className="text-xs text-muted-foreground">Allowed {approvedRoots.length === 1 ? 'root' : 'roots'}: {approvedRoots.map((root) => `${root.display_name}${root.internal_path ? ` (${root.internal_path})` : ''}`).join(', ')}</p> : null}
        {manualError ? <p role="alert" className="text-sm text-destructive">{manualError}</p> : null}
      </CollapsibleContent>
    </Collapsible>
  </Field>;
}
