import type { NextConfig } from 'next';

const nextConfig: NextConfig = process.env.BLUEREEL_NATIVE_BUILD === '1'
  ? { output: 'standalone' }
  : {};

export default nextConfig;
