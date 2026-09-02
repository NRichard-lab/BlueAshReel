import { describe, expect, it } from 'vitest';

import {
  manualMediaPathProblem,
  nextAvailableFolderFocusIndex,
  nextFolderFocusIndex,
  WINDOWS_MEDIA_PATH_MESSAGE,
} from './media-folder-picker';

describe('manual media path validation', () => {
  it.each(['D:\\Media', 'd:/Media/Movies', '\\\\server\\share'])('rejects Windows host path %s with actionable guidance', (path) => {
    expect(manualMediaPathProblem(path)).toBe(WINDOWS_MEDIA_PATH_MESSAGE);
  });

  it('requires an approved internal absolute path', () => {
    expect(manualMediaPathProblem('../media')).toMatch(/internal mounted path/i);
    expect(manualMediaPathProblem('')).toMatch(/enter an internal/i);
    expect(manualMediaPathProblem('/media/Movies')).toBeUndefined();
  });
});

describe('folder-list keyboard navigation', () => {
  it('moves with arrows without leaving the list', () => {
    expect(nextFolderFocusIndex(0, 'ArrowUp', 3)).toBe(0);
    expect(nextFolderFocusIndex(0, 'ArrowDown', 3)).toBe(1);
    expect(nextFolderFocusIndex(2, 'ArrowDown', 3)).toBe(2);
  });

  it('supports Home and End and ignores unrelated keys', () => {
    expect(nextFolderFocusIndex(2, 'Home', 4)).toBe(0);
    expect(nextFolderFocusIndex(0, 'End', 4)).toBe(3);
    expect(nextFolderFocusIndex(1, 'Tab', 4)).toBe(1);
  });

  it('skips unavailable folders', () => {
    const available = [true, false, true, false];
    expect(nextAvailableFolderFocusIndex(0, 'ArrowDown', available)).toBe(2);
    expect(nextAvailableFolderFocusIndex(2, 'ArrowUp', available)).toBe(0);
    expect(nextAvailableFolderFocusIndex(0, 'End', available)).toBe(2);
  });
});
