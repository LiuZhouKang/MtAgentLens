'use client';

import Link from 'next/link';
import { ArrowUpRight, Radio } from 'lucide-react';
import PrismPanel from '@/components/ui/prism-panel';

type HeroStat = {
  label: string;
  value: string;
  hint: string;
};

type HeroAction = {
  label: string;
  href: string;
  hint: string;
};

type ObservatoryPageHeroProps = {
  eyebrow: string;
  title: string;
  description: string;
  statusLabel: string;
  chips?: string[];
  stats: HeroStat[];
  actions?: HeroAction[];
};

export default function ObservatoryPageHero({
  eyebrow,
  title,
  description,
  statusLabel,
  chips = [],
  stats,
  actions = [],
}: ObservatoryPageHeroProps) {
  return (
    <PrismPanel size="lg" grid className="observatory-page-hero relative overflow-hidden">
      <div className="observatory-page-hero__hairline" />

      <div className="observatory-page-hero__layout relative z-[1] grid gap-5 xl:grid-cols-[minmax(0,1.05fr)_minmax(440px,0.95fr)]">
        <div className="observatory-page-hero__lead grid content-start gap-3">
          <div className="flex flex-wrap items-center gap-2">
            <span className="observatory-page-hero__eyebrow">{eyebrow}</span>
            <span className="observatory-page-hero__status">
              <Radio size={11} />
              {statusLabel}
            </span>
          </div>

          <div className="observatory-page-hero__copy grid gap-2">
            <h2 className="observatory-page-hero__title">{title}</h2>
            <p className="observatory-page-hero__description">{description}</p>
          </div>

          {chips.length ? (
            <div className="observatory-page-hero__chip-row flex flex-wrap gap-2">
              {chips.map((chip) => (
                <span key={chip} className="observatory-page-hero__chip">
                  {chip}
                </span>
              ))}
            </div>
          ) : null}
        </div>

        <div className="observatory-page-hero__side grid content-start gap-3">
          <div className="observatory-page-hero__stats grid gap-2 sm:grid-cols-3">
            {stats.map((item) => (
              <div key={item.label} className="observatory-page-hero__stat-card">
                <span className="observatory-page-hero__stat-label">{item.label}</span>
                <strong className="observatory-page-hero__stat-value">{item.value}</strong>
                <small className="observatory-page-hero__stat-hint">{item.hint}</small>
              </div>
            ))}
          </div>

          {actions.length ? (
            <div className="observatory-page-hero__actions grid gap-2 sm:grid-cols-2">
              {actions.map((item) => (
                <Link
                  key={item.label}
                  href={item.href}
                  className="observatory-page-hero__action-card group"
                >
                  <div className="flex items-center justify-between gap-3">
                    <strong className="observatory-page-hero__action-label">{item.label}</strong>
                    <ArrowUpRight
                      size={14}
                      className="observatory-page-hero__action-icon transition group-hover:translate-x-0.5 group-hover:-translate-y-0.5"
                    />
                  </div>
                  <small className="observatory-page-hero__action-hint">{item.hint}</small>
                </Link>
              ))}
            </div>
          ) : null}
        </div>
      </div>
    </PrismPanel>
  );
}
