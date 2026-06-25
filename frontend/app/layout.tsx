import type { Metadata, Viewport } from 'next';
import { Inter } from 'next/font/google';
import Script from 'next/script';
import AppProviders from '@/components/system/app-providers';
import SkipLink from '@/components/system/skip-link';
import ServiceWorkerRegister from '@/components/system/service-worker-register';
import { getThemeBootstrapScript } from '@/lib/theme';
import './globals.css';

const inter = Inter({
  subsets: ['latin'],
  display: 'swap',
  variable: '--font-inter',
});

export const metadata: Metadata = {
  title: 'MtAgentLens — 多 Agent 责任归因界面',
  description: '零插桩、多 Agent、责任边界与执行证据统一呈现的旗舰观测界面',
  manifest: '/manifest.webmanifest',
  applicationName: 'MtAgentLens',
  keywords: ['MtAgentLens', 'Agent observability', 'eBPF', 'responsibility graph', 'investigation workbench'],
  icons: {
    icon: '/icon.svg',
    shortcut: '/icon.svg',
    apple: '/icon.svg',
  },
  openGraph: {
    title: 'MtAgentLens — 多 Agent 责任归因界面',
    description: '零插桩、多 Agent、责任边界与执行证据统一呈现的旗舰观测界面',
    type: 'website',
  },
};

export const viewport: Viewport = {
  width: 'device-width',
  initialScale: 1,
  viewportFit: 'cover',
  themeColor: '#000108',
  colorScheme: 'dark light',
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="zh-CN" className={`dark ${inter.variable}`} suppressHydrationWarning>
      <body className="app-body">
        <Script id="theme-bootstrap" strategy="beforeInteractive">
          {getThemeBootstrapScript()}
        </Script>
        <SkipLink />
        <AppProviders>
          <main id="app-main" tabIndex={-1}>
            {children}
          </main>
          <ServiceWorkerRegister />
        </AppProviders>
      </body>
    </html>
  );
}
