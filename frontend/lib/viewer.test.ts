import { describe, expect, it } from 'vitest';
import { clockTime } from './viewer';

describe('playback time labels', () => {
  it('handles unknown or negative media duration honestly', () => {
    expect(clockTime(null)).toBe('0:00');
    expect(clockTime(-10)).toBe('0:00');
  });
  it('formats seconds and multi-hour media', () => {
    expect(clockTime(90.8)).toBe('1:30');
    expect(clockTime(7261)).toBe('2:01:01');
  });
});
