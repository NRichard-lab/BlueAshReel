import type { Metadata } from 'next';

import { LoginForm } from '@/components/login-form';

export const metadata: Metadata = { title: 'Owner sign in' };

export default function LoginPage() {
  return <LoginForm />;
}
