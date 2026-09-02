export interface NetworkEnforcement {
  platform: 'windows' | 'docker';
  status: 'enforced' | 'not_enforced' | 'unknown' | 'not_managed';
  checked_at: string | null;
  detail: string;
}

export function networkEnforcementLabel(status?: string): string {
  if (status === 'enforced') return 'Enforced';
  if (status === 'not_enforced') return 'Not enforced';
  if (status === 'not_managed') return 'Managed by deployment';
  return 'Not verified';
}
