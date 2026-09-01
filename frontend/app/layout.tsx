import type { Metadata } from 'next';

import { productConfig } from '@/lib/product-config';

import './globals.css';

export const metadata: Metadata = {
  metadataBase: new URL(process.env.PUBLIC_ORIGIN ?? 'http://localhost:8080'),
  title: {
    default: productConfig.name,
    template: `%s · ${productConfig.name}`,
  },
  description: `${productConfig.name} is a private, local-first home-media server.`,
  openGraph: {
    title: productConfig.name,
    description: productConfig.subtitle,
    images: [{ url: '/og.png', width: 1731, height: 909, alt: `${productConfig.name} local media illustration` }],
  },
  twitter: {
    card: 'summary_large_image',
    title: productConfig.name,
    description: productConfig.subtitle,
    images: ['/og.png'],
  },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
