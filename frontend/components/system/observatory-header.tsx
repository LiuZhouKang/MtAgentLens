'use client';

import type { ReactNode } from 'react';
import Link from 'next/link';
import ThemeToggle from '@/components/ui/theme-toggle';
import { ArrowLeft, Radio, Search, ShieldCheck } from 'lucide-react';

type ObservatorySection = 'overview' | 'traces' | 'clusters' | 'agents' | 'investigations' | 'handoff';

const NAV_ITEMS: Array<{ id: ObservatorySection; label: string; href: string }> = [
  { id: 'overview', label: '总览', href: '/overview' },
  { id: 'traces', label: 'Trace', href: '/traces' },
  { id: 'clusters', label: 'Cluster', href: '/clusters' },
  { id: 'agents', label: 'Agent', href: '/agents' },
  { id: 'investigations', label: '调查', href: '/investigations' },
  { id: 'handoff', label: 'Handoff', href: '/handoff' },
];

type ObservatoryHeaderProps = {
  eyebrow: string;
  title: string;
  description: string;
  activeSection: ObservatorySection;
  backHref: string;
  backLabel: string;
  stats?: string[];
  sticky?: boolean;
  rightSlot?: ReactNode;
};

export default function ObservatoryHeader({
  eyebrow,
  title,
  description,
  activeSection,
  backHref,
  backLabel,
  stats = [],
  sticky = false,
  rightSlot,
}: ObservatoryHeaderProps) {
  return (
    <header
      className="observatory-header-surface"
      style={sticky ? { position: 'sticky', top: 0, zIndex: 20 } : undefined}
    >
      <div className="observatory-command-bar">
        <Link href="/" className="observatory-brand-lockup" aria-label="MtAgentLens 主控制台">
          <span className="observatory-brand-mark">
            <ShieldCheck size={15} />
          </span>
          <span>
            <strong>MtAgentLens</strong>
            <small>eBPF Agent Observatory</small>
          </span>
        </Link>

        <nav className="observatory-header-nav" aria-label="观测层级导航">
          {NAV_ITEMS.map((item) => (
            <Link
              key={item.id}
              href={item.href}
              className={[
                'observatory-nav-pill',
                item.id === activeSection ? 'observatory-nav-pill--active' : '',
              ]
                .filter(Boolean)
                .join(' ')}
              aria-current={item.id === activeSection ? 'page' : undefined}
            >
              {item.label}
            </Link>
          ))}
        </nav>

        <div className="observatory-command-actions">
          <div className="observatory-command-search" aria-label="全局命令入口">
            <Search size={13} />
            <span>⌘K</span>
          </div>
          <div className="observatory-live-status">
            <Radio size={13} />
            Collector ready
          </div>
          <ThemeToggle />
          {rightSlot}
        </div>
      </div>

      <div className="observatory-header-main">
        <div className="observatory-header-copy">
          <div className="observatory-header-topline">
            <Link href={backHref} className="observatory-header-link">
              <ArrowLeft size={14} />
              {backLabel}
            </Link>
            <span className="observatory-header-chip">{eyebrow}</span>
          </div>

          <div className="observatory-header-text">
            <h1 className="observatory-header-title">{title}</h1>
            <p className="observatory-header-description">{description}</p>
          </div>
        </div>

        <div className="observatory-header-side">
          {stats.length ? (
            <div className="observatory-header-stats">
              {stats.map((value) => (
                <div key={value} className="observatory-header-pill">
                  {value}
                </div>
              ))}
            </div>
          ) : null}
        </div>
      </div>
    </header>
  );
}
