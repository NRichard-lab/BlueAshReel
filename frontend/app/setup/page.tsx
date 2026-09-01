import type { Metadata } from 'next';

import { SetupWizard } from '@/components/setup-wizard';
import { productConfig } from '@/lib/product-config';

export const metadata: Metadata = {
  title: 'First-run setup',
  description: `Create the Owner account and prepare ${productConfig.name}.`,
};

export default function SetupPage() {
  return <SetupWizard />;
}
