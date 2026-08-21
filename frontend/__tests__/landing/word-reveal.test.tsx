// @vitest-environment jsdom
import { afterEach, describe, expect, it } from 'vitest';
import { cleanup, render } from '@testing-library/react';
import { WordReveal } from '@/components/landing/word-reveal';
import { splitIntoWords } from '@/lib/landing/landing-copy';

function setReducedMotion(matches: boolean) {
  window.matchMedia = ((query: string) => ({
    matches,
    media: query,
    onchange: null,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => false,
  })) as unknown as typeof window.matchMedia;
}

function wordTexts(container: HTMLElement): string[] {
  return Array.from(container.querySelectorAll<HTMLElement>('.word')).map(
    (w) => w.textContent?.trim() ?? '',
  );
}

afterEach(() => cleanup());

describe('WordReveal — 逐词文字动画', () => {
  it('renders one .word span per split unit (latin)', () => {
    const { container } = render(<WordReveal text="OBSERVE THE FLOW" />);
    expect(container.querySelectorAll('.word')).toHaveLength(3);
    expect(wordTexts(container)).toEqual(['OBSERVE', 'THE', 'FLOW']);
  });

  it('splits CJK text per character', () => {
    const { container } = render(<WordReveal text="零插桩" />);
    expect(wordTexts(container)).toEqual(['零', '插', '桩']);
  });

  it('renders mixed CJK + latin segments correctly', () => {
    const { container } = render(<WordReveal text="零插桩 OBSERVE" />);
    expect(wordTexts(container)).toEqual(['零', '插', '桩', 'OBSERVE']);
  });

  it('exposes the full text via sr-only copy while the animated words stay aria-hidden', () => {
    const { container } = render(<WordReveal text="看见责任流动" as="h1" />);
    const el = container.querySelector('h1.word-reveal');
    expect(el).not.toBeNull();
    // No aria-label on the wrapper (prohibited on spans) — the intact text is
    // provided as an sr-only sibling instead.
    expect(el!.getAttribute('aria-label')).toBeNull();
    expect(el!.querySelector('.sr-only')?.textContent).toBe('看见责任流动');
    const words = container.querySelectorAll('.word');
    expect(words.length).toBeGreaterThan(0);
    for (const w of Array.from(words)) {
      expect(w.getAttribute('aria-hidden')).toBe('true');
    }
  });

  it('renders statically (all words visible) under prefers-reduced-motion', () => {
    setReducedMotion(true);
    const { container } = render(<WordReveal text="OBSERVE THE FLOW" />);
    expect(wordTexts(container)).toEqual(['OBSERVE', 'THE', 'FLOW']);
  });

  it('matches splitIntoWords exactly', () => {
    const { container } = render(<WordReveal text="零插桩，看见" />);
    expect(wordTexts(container)).toEqual(splitIntoWords('零插桩，看见'));
  });

  it('adds a proper-space class to a CJK char right before a latin word (防「看见Agent」挤在一起)', () => {
    const { container } = render(<WordReveal text="看见 Agent 的责任流动" />);
    const words = Array.from(container.querySelectorAll<HTMLElement>('.word'));
    // 「见」紧跟 latin「Agent」→ 加 word--cjk-gap（0.3em 正常空格）
    const jian = words.find((w) => w.textContent === '见');
    expect(jian?.className).toContain('word--cjk-gap');
    // CJK 标点后的 latin 不加（如「，」后不是 CJK 字），latin 自身不加
    const agent = words.find((w) => w.textContent === 'Agent');
    expect(agent?.className).not.toContain('word--cjk-gap');
    // 「的」后跟 CJK 字「责」→ 不加 gap
    const de = words.find((w) => w.textContent === '的');
    expect(de?.className).not.toContain('word--cjk-gap');
  });

  it('marks accent words with .word--accent (amber 强调，其他词不加)', () => {
    const { container } = render(
      <WordReveal text="看见 Agent 的责任流动" accent={['责', '任', '流', '动']} />,
    );
    const words = Array.from(container.querySelectorAll<HTMLElement>('.word'));
    const accentWords = words.filter((w) => w.className.includes('word--accent')).map((w) => w.textContent);
    expect(accentWords).toEqual(['责', '任', '流', '动']);
    // 非强调词（看/见/Agent/的）不加 accent
    for (const w of words) {
      if (!['责', '任', '流', '动'].includes(w.textContent ?? '')) {
        expect(w.className).not.toContain('word--accent');
      }
    }
  });

  it('does not add a space class at CJK→CJK 标点边界', () => {
    const { container } = render(<WordReveal text="零插桩，看见" />);
    const words = Array.from(container.querySelectorAll<HTMLElement>('.word'));
    // 「桩」后是「，」（CJK 标点，无 a-z）→ 不加 gap
    const zhuang = words.find((w) => w.textContent === '桩');
    expect(zhuang?.className).not.toContain('word--cjk-gap');
  });

  it('forwards an id to the root tag (aria-labelledby anchor)', () => {
    const { container } = render(<WordReveal as="h2" id="pipeline-title" text="一条管线" />);
    const el = container.querySelector('h2.word-reveal');
    expect(el?.getAttribute('id')).toBe('pipeline-title');
  });
});
