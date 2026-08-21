import type { Metadata } from 'next';

export const metadata: Metadata = {
  title: 'MtAgentLens — 零插桩观测',
  description: '零插桩，看见 Agent 的责任流动',
};

/**
 * Landing route group — a full-screen deep-space canvas that owns the whole
 * viewport. Deliberately has NO (main) layout: no topbar, no data-source
 * selector, no event rail. The root layout's <main id="app-main"> still wraps
 * children, so skip-link and a11y landmarks keep working.
 */
export default function LandingLayout({ children }: { children: React.ReactNode }) {
  return <div className="landing-root">{children}</div>;
}
