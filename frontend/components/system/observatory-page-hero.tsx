'use client';

import Link from 'next/link';
import { ArrowUpRight, Radio, Sparkles } from 'lucide-react';
import { motion } from 'motion/react';
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
    <PrismPanel
      size="hero"
      grid
      shimmer
      className="relative overflow-hidden border border-cyan-300/10 bg-[linear-gradient(180deg,rgba(5,10,18,0.98)_0%,rgba(7,12,24,0.92)_48%,rgba(4,8,15,0.98)_100%)]"
    >
      <motion.div
        className="pointer-events-none absolute inset-x-10 top-0 h-px bg-gradient-to-r from-transparent via-cyan-200/80 to-transparent"
        animate={{ opacity: [0.3, 0.95, 0.3], scaleX: [0.84, 1, 0.84] }}
        transition={{ duration: 4.8, repeat: Infinity, ease: 'easeInOut' }}
      />
      <motion.div
        className="pointer-events-none absolute -right-16 top-[-72px] h-44 w-44 rounded-full bg-cyan-400/12 blur-3xl"
        animate={{ opacity: [0.14, 0.3, 0.14], scale: [0.9, 1.08, 0.9] }}
        transition={{ duration: 6.2, repeat: Infinity, ease: 'easeInOut' }}
      />

      <div className="relative z-[1] grid gap-6 lg:grid-cols-[minmax(0,1.45fr)_minmax(280px,0.9fr)]">
        <div className="grid gap-4">
          <div className="flex flex-wrap items-center gap-3">
            <span className="inline-flex items-center gap-2 text-[11px] uppercase tracking-[0.24em] text-cyan-100/58">
              <Sparkles size={12} className="text-cyan-200/72" />
              {eyebrow}
            </span>
            <span className="inline-flex items-center gap-2 rounded-full border border-emerald-400/20 bg-emerald-400/8 px-3 py-1.5 text-[10px] uppercase tracking-[0.18em] text-emerald-100/82">
              <Radio size={11} />
              {statusLabel}
            </span>
          </div>

          <div className="grid gap-3">
            <h2 className="text-[clamp(28px,4.4vw,52px)] font-semibold leading-[1.02] tracking-[-0.03em] text-white">
              {title}
            </h2>
            <p className="max-w-3xl text-[14px] leading-7 text-slate-300/82">
              {description}
            </p>
          </div>

          {chips.length ? (
            <div className="flex flex-wrap gap-2">
              {chips.map((chip) => (
                <span
                  key={chip}
                  className="inline-flex rounded-full border border-white/10 bg-white/[0.04] px-3 py-1.5 text-[11px] uppercase tracking-[0.14em] text-slate-200/72"
                >
                  {chip}
                </span>
              ))}
            </div>
          ) : null}
        </div>

        <div className="grid gap-3">
          <div className="grid gap-3 sm:grid-cols-3 lg:grid-cols-1 xl:grid-cols-3">
            {stats.map((item) => (
              <div
                key={item.label}
                className="rounded-[22px] border border-white/8 bg-[linear-gradient(180deg,rgba(255,255,255,0.045),rgba(255,255,255,0.02))] px-4 py-3"
              >
                <span className="block text-[10px] uppercase tracking-[0.18em] text-slate-400/72">{item.label}</span>
                <strong className="mt-2 block text-xl font-semibold text-white">{item.value}</strong>
                <small className="mt-1 block text-[12px] leading-5 text-slate-300/56">{item.hint}</small>
              </div>
            ))}
          </div>

          {actions.length ? (
            <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-2">
              {actions.map((item) => (
                <Link
                  key={item.label}
                  href={item.href}
                  className="group rounded-[20px] border border-cyan-300/14 bg-[linear-gradient(180deg,rgba(34,211,238,0.08),rgba(59,130,246,0.03))] px-4 py-3 transition hover:border-cyan-200/28 hover:bg-cyan-300/10"
                >
                  <div className="flex items-center justify-between gap-3">
                    <strong className="text-sm text-cyan-50">{item.label}</strong>
                    <ArrowUpRight size={14} className="text-cyan-100/72 transition group-hover:translate-x-0.5 group-hover:-translate-y-0.5" />
                  </div>
                  <small className="mt-1 block text-[12px] leading-5 text-slate-300/58">{item.hint}</small>
                </Link>
              ))}
            </div>
          ) : null}
        </div>
      </div>
    </PrismPanel>
  );
}
