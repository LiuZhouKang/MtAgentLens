import type { NextConfig } from 'next';

const nextConfig: NextConfig = {
  output: 'export',
  transpilePackages: ['three'],
  distDir: 'out',
  turbopack: {
    root: __dirname,
  },
};

export default nextConfig;
