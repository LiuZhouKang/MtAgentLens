// @vitest-environment jsdom
import { describe, expect, it } from 'vitest';
import { render } from '@testing-library/react';
import MtlLogo from '@/components/ui/mtl-logo';

/**
 * MtlLogo 渐变 ID 唯一性 —— 防跨实例 url(#id) 串扰。
 *
 * 回归背景：页面多个 MtlLogo 实例曾共用同一个 <linearGradient id="mtl-grad">，
 * url(#mtl-grad) 按 DOM 顺序解析到第一个实例（开场 reveal，collapse 后
 * display:none）。SVG 规范下引用 display:none 子树里的 paint server 失效，
 * 导致后续实例（品牌区块）的轨道与核心渐变不渲染、logo 只剩模糊光斑。
 */
describe('MtlLogo — 每个实例的渐变 ID 唯一且自我引用', () => {
  it('两个实例的 linearGradient id 互不相同', () => {
    const { container } = render(
      <>
        <MtlLogo size={240} />
        <MtlLogo size={260} />
      </>,
    );
    const svgs = container.querySelectorAll('svg');
    expect(svgs.length).toBe(2);
    const idA = svgs[0]!.querySelector('linearGradient')!.getAttribute('id');
    const idB = svgs[1]!.querySelector('linearGradient')!.getAttribute('id');
    expect(idA).toBeTruthy();
    expect(idB).toBeTruthy();
    expect(idA).not.toBe(idB);
  });

  it('核心与轨道各自引用自己实例的渐变，不引用对方/共享 id', () => {
    const { container } = render(
      <>
        <MtlLogo size={240} />
        <MtlLogo size={260} />
      </>,
    );
    const [svgA, svgB] = container.querySelectorAll('svg');
    const idA = svgA!.querySelector('linearGradient')!.getAttribute('id')!;
    const idB = svgB!.querySelector('linearGradient')!.getAttribute('id')!;

    // 实例 A：核心 fill + 轨道 stroke 引用自己的渐变
    expect(svgA!.querySelector('.mtl-core circle')!.getAttribute('fill')).toBe(`url(#${idA})`);
    expect(svgA!.querySelector('.mtl-orbit')!.getAttribute('stroke')).toBe(`url(#${idA})`);
    // 实例 B：同样自我引用
    expect(svgB!.querySelector('.mtl-core circle')!.getAttribute('fill')).toBe(`url(#${idB})`);
    expect(svgB!.querySelector('.mtl-orbit')!.getAttribute('stroke')).toBe(`url(#${idB})`);
    // 绝不出现旧共享 id（回归守卫）
    expect(container.querySelectorAll('[id="mtl-grad"]').length).toBe(0);
  });

  it('mono 模式不需要渐变但仍渲染单色线条', () => {
    const { container } = render(<MtlLogo mono size={20} />);
    const svg = container.querySelector('svg');
    expect(svg!.querySelector('linearGradient')).toBeNull();
    expect(svg!.querySelectorAll('ellipse').length).toBeGreaterThan(0);
  });
});
