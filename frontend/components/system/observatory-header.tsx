'use client';

import type { ReactNode } from 'react';
import Image from 'next/image';
import Link from 'next/link';
import ThemeToggle from '@/components/ui/theme-toggle';
import { ArrowLeft, Command, Radio, Search, Waypoints } from 'lucide-react';

type ObservatorySection = 'command' | 'overview' | 'traces' | 'clusters' | 'agents' | 'investigations' | 'handoff';

const NAV_ITEMS: Array<{ id: ObservatorySection; label: string; href: string }> = [
  { id: 'command', label: '指挥台', href: '/' },
  { id: 'overview', label: '总览', href: '/overview' },
  { id: 'traces', label: '运行链路', href: '/traces' },
  { id: 'clusters', label: '责任集群', href: '/clusters' },
  { id: 'agents', label: '主体画像', href: '/agents' },
  { id: 'investigations', label: '调查', href: '/investigations' },
  { id: 'handoff', label: '委托扩散', href: '/handoff' },
];

type ObservatoryHeaderProps = {
  eyebrow?: string;
  title?: string;
  description?: string;
  activeSection: ObservatorySection;
  backHref?: string;
  backLabel?: string;
  stats?: string[];
  sticky?: boolean;
  rightSlot?: ReactNode;
  compact?: boolean;
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
  compact = false,
}: ObservatoryHeaderProps) {
  return (
    <header
      className="observatory-header-surface"
      style={sticky ? { position: 'sticky', top: 0, zIndex: 20 } : undefined}
    >
      <div className="observatory-command-bar">
        <Link href="/" className="observatory-brand-lockup" aria-label="MtAgentLens 主控制台">
          <Image
            src="/brand/mtagentlens-lockup.svg"
            alt=""
            aria-hidden="true"
            className="observatory-brand-lockup__image"
            width={1280}
            height={300}
            priority
          />
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
            <span>全局检索</span>
            <kbd>
              <Command size={11} />
              K
            </kbd>
          </div>
          <div className="observatory-live-status">
            <Radio size={13} />
            Collector Online
          </div>
          <ThemeToggle />
          {rightSlot}
        </div>
      </div>

      {!compact ? (
        <div className="observatory-header-main">
          <div className="observatory-header-copy">
            <div className="observatory-header-topline">
              {backHref && backLabel ? (
                <Link href={backHref} className="observatory-header-link">
                  <ArrowLeft size={14} />
                  {backLabel}
                </Link>
              ) : null}
              {eyebrow ? <span className="observatory-header-chip">{eyebrow}</span> : null}
              <span className="observatory-header-subchip">
                <Waypoints size={12} />
                Runtime Graph
              </span>
            </div>

            <div className="observatory-header-text">
              {title ? <h1 className="observatory-header-title">{title}</h1> : null}
              {description ? <p className="observatory-header-description">{description}</p> : null}
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
      ) : null}
    </header>
  );
}
