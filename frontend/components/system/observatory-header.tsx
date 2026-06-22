'use client';

import type { ReactNode } from 'react';
import Link from 'next/link';
import ThemeToggle from '@/components/ui/theme-toggle';
import { ArrowLeft } from 'lucide-react';

type ObservatorySection = 'overview' | 'clusters' | 'agents' | 'investigations' | 'handoff';

const NAV_ITEMS: Array<{ id: ObservatorySection; label: string; href: string }> = [
  { id: 'overview', label: 'Overview', href: '/overview' },
  { id: 'clusters', label: 'Clusters', href: '/clusters' },
  { id: 'agents', label: 'Agents', href: '/agents' },
  { id: 'investigations', label: 'Investigations', href: '/investigations' },
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
        </div>

        <div className="observatory-header-side">
          <div className="observatory-header-controls">
            <ThemeToggle />
            {rightSlot}
          </div>
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
