import { describe, expect, it } from 'vitest';
import { networkEnforcementLabel } from './privacy';

describe('network enforcement reporting', () => {
  it('shows enforced only for an affirmative runtime result', () => {
    expect(networkEnforcementLabel('enforced')).toBe('Enforced');
    expect(networkEnforcementLabel('not_enforced')).toBe('Not enforced');
    expect(networkEnforcementLabel('not_managed')).toBe('Managed by deployment');
    expect(networkEnforcementLabel('unknown')).toBe('Not verified');
    expect(networkEnforcementLabel()).toBe('Not verified');
    expect(networkEnforcementLabel('configured')).toBe('Not verified');
  });
});
