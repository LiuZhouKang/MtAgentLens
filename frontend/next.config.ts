import type { NextConfig } from 'next';
import { PHASE_DEVELOPMENT_SERVER } from 'next/constants';

export default function nextConfig(phase: string): NextConfig {
  const isDev = phase === PHASE_DEVELOPMENT_SERVER;
  return {
    allowedDevOrigins: ['127.0.0.1', 'localhost'],
    output: isDev ? undefined : 'export',
    poweredByHeader: false,
    reactStrictMode: true,
    transpilePackages: ['three'],
    distDir: isDev ? '.next' : 'out',
    images: {
      remotePatterns: [
        {
          protocol: 'https',
          hostname: 'img.example.com',
        },
      ],
    },
    experimental: {
      optimizePackageImports: ['lucide-react'],
    },
    turbopack: {
      root: __dirname,
    },
  };
}
