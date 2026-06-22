'use client';

import { useEffect, useState } from 'react';

export interface Home3DStoryChapter {
  id: string;
  stage: string;
  title: string;
  summary: string;
  metricLabel: string;
  metricValue: string;
  accent: string;
  edgeIds: string[];
}

interface Home3DStoryDirectorProps {
  chapters: Home3DStoryChapter[];
  activeChapterId: string;
  sceneStats?: {
    nodes: number;
    links: number;
    boundaryCrossings: number;
  };
  onSelectChapter: (chapter: Home3DStoryChapter) => void;
}

export default function Home3DStoryDirector({
  chapters,
  activeChapterId,
  sceneStats,
  onSelectChapter,
}: Home3DStoryDirectorProps) {
  const [mounted, setMounted] = useState(false);

  useEffect(() => {
    setMounted(true);
  }, []);

  if (!mounted || !chapters.length) return null;

  return (
    <div
      className="home-story-director prism-dossier-card"
      role="region"
      aria-label="3D 场景导演器"
      style={{
        position: 'absolute',
        right: 24,
        top: 'clamp(88px, 12vh, 112px)',
        bottom: 'auto',
        zIndex: 9,
        width: 'min(286px, calc(100vw - 48px))',
        maxHeight: 'calc(100vh - 128px)',
        overflowY: 'auto',
        pointerEvents: 'auto',
      }}
    >
      <div style={{ display: 'grid', gap: 6 }}>
        <div className="prism-kicker-rule" style={{ gap: 0 }}>
          Scene Director
        </div>
        <div className="prism-display" style={{ fontSize: 17, lineHeight: 1.12, color: 'var(--text-primary)' }}>
          用真实责任图谱驱动 3D 镜头，而不是把地球当背景视频
        </div>
      </div>

      {sceneStats ? (
        <div
          aria-label={`Runtime Mesh：${sceneStats.nodes} 个节点，${sceneStats.links} 条关系，${sceneStats.boundaryCrossings} 次边界穿越`}
          style={{
            marginTop: 12,
            display: 'grid',
            gridTemplateColumns: 'repeat(3, minmax(0, 1fr))',
            gap: 6,
          }}
        >
          {[
            ['Nodes', sceneStats.nodes],
            ['Links', sceneStats.links],
            ['Cross', sceneStats.boundaryCrossings],
          ].map(([label, value]) => (
            <div
              key={label}
              style={{
                minWidth: 0,
                padding: '8px 8px 7px',
                borderRadius: 12,
                border: '1px solid var(--border-soft)',
                background: 'rgba(7, 12, 22, 0.32)',
              }}
            >
              <div style={{ fontSize: 9, letterSpacing: '0.12em', textTransform: 'uppercase', color: 'var(--text-quaternary)' }}>
                {label}
              </div>
              <div className="prism-display" style={{ marginTop: 3, fontSize: 16, lineHeight: 1, color: 'var(--text-primary)' }}>
                {value}
              </div>
            </div>
          ))}
        </div>
      ) : null}

      <div style={{ marginTop: 12, display: 'grid', gap: 8 }}>
        {chapters.map((chapter, index) => {
          const active = chapter.id === activeChapterId;

          return (
            <button
              key={chapter.id}
              type="button"
              onClick={() => onSelectChapter(chapter)}
              aria-label={`${chapter.stage}：${chapter.title}。${chapter.metricLabel} ${chapter.metricValue}。${active ? '当前镜头已选中。' : '按回车切换到这个镜头。'}`}
              aria-pressed={active}
              style={{
                width: '100%',
                display: 'grid',
                gap: 8,
                textAlign: 'left',
                padding: '11px 12px',
                borderRadius: 18,
                border: active
                  ? `1px solid color-mix(in srgb, ${chapter.accent} 34%, transparent)`
                  : '1px solid var(--border-soft)',
                background: active
                  ? `linear-gradient(180deg, color-mix(in srgb, ${chapter.accent} 14%, transparent) 0%, rgba(8, 13, 25, 0.6) 100%)`
                  : 'rgba(7, 12, 22, 0.28)',
                color: 'var(--text-primary)',
                cursor: 'pointer',
                boxShadow: active ? `0 14px 28px color-mix(in srgb, ${chapter.accent} 12%, transparent)` : 'none',
                transition: 'transform 180ms ease, border-color 180ms ease, background 180ms ease, box-shadow 180ms ease',
              }}
            >
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 10 }}>
                <div style={{ display: 'inline-flex', alignItems: 'center', gap: 8 }}>
                  <span
                    aria-hidden="true"
                    style={{
                      width: 24,
                      height: 24,
                      borderRadius: 999,
                      display: 'inline-flex',
                      alignItems: 'center',
                      justifyContent: 'center',
                      fontSize: 9,
                      fontWeight: 700,
                      letterSpacing: '0.08em',
                      color: 'var(--text-primary)',
                      background: `color-mix(in srgb, ${chapter.accent} 18%, transparent)`,
                      border: `1px solid color-mix(in srgb, ${chapter.accent} 30%, transparent)`,
                    }}
                  >
                    0{index + 1}
                  </span>
                  <div style={{ fontSize: 10, letterSpacing: '0.14em', textTransform: 'uppercase', color: 'var(--text-quaternary)' }}>
                    {chapter.stage}
                  </div>
                </div>
                <div aria-hidden="true" style={{ fontSize: 10, color: active ? chapter.accent : 'var(--text-tertiary)' }}>
                  {chapter.metricLabel} {chapter.metricValue}
                </div>
              </div>

              <div className="prism-display" style={{ fontSize: 14, lineHeight: 1.16, color: 'var(--text-primary)' }}>
                {chapter.title}
              </div>

              <div style={{ fontSize: 10.5, lineHeight: 1.55, color: 'var(--text-secondary)' }}>
                {chapter.summary}
              </div>
            </button>
          );
        })}
      </div>
    </div>
  );
}
