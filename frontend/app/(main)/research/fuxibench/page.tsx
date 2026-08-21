'use client';

import Link from 'next/link';
import { useEffect } from 'react';
import { useAppStore } from '@/lib/store';

export default function FuxiBenchResearchPage() {
  // 静态研究页：内容即时渲染 → 立即报告就绪（供布局层协调加载层，防 loader 滞留）
  const setPageReady = useAppStore((s) => s.setPageReady);
  useEffect(() => {
    setPageReady(true);
  }, [setPageReady]);

  return (
    <main
      style={{
        minHeight: '100vh',
        display: 'grid',
        placeItems: 'center',
        padding: '40px 20px',
        background:
          'radial-gradient(circle at 50% 12%, rgba(96, 165, 250, 0.08), transparent 28%), linear-gradient(180deg, #02040b 0%, #06101c 100%)',
        color: '#e8e8ed',
      }}
    >
      <section
        style={{
          width: 'min(720px, 100%)',
          display: 'grid',
          gap: 16,
          padding: 28,
          borderRadius: 20,
          border: '1px solid rgba(148, 163, 184, 0.12)',
          background: 'rgba(8, 14, 28, 0.82)',
          boxShadow: '0 24px 72px rgba(0, 0, 0, 0.28)',
        }}
      >
        <div style={{ display: 'grid', gap: 10 }}>
          <span
            style={{
              fontSize: 11,
              lineHeight: 1.2,
              letterSpacing: '0.12em',
              textTransform: 'uppercase',
              color: 'rgba(226, 232, 240, 0.48)',
            }}
          >
            Research Mode
          </span>
          <h1 style={{ margin: 0, fontSize: 32, lineHeight: 1.04 }}>
            FuxiBench benchmark assets live here
          </h1>
          <p
            style={{
              margin: 0,
              fontSize: 14,
              lineHeight: 1.75,
              color: 'rgba(203, 213, 225, 0.68)',
            }}
          >
            首页已经切换为面向实际运行的 Runtime Operations Console。FuxiBench 相关实验、研究和
            benchmark 视图作为独立入口保留在 Research 模式下，避免继续干扰生产首页叙事。
          </p>
        </div>

        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 12 }}>
          {[
            { href: '/', label: '返回运行控制台' },
            { href: '/overview', label: '打开仪表盘' },
            { href: '/investigations', label: '打开调查工作台' },
          ].map((item) => (
            <Link
              key={item.href}
              href={item.href}
              style={{
                display: 'inline-flex',
                alignItems: 'center',
                justifyContent: 'center',
                minHeight: 40,
                padding: '0 14px',
                borderRadius: 999,
                border: '1px solid rgba(148, 163, 184, 0.14)',
                background: 'rgba(255, 255, 255, 0.04)',
                color: '#f8fafc',
                textDecoration: 'none',
                fontSize: 13,
              }}
            >
              {item.label}
            </Link>
          ))}
        </div>
      </section>
    </main>
  );
}
