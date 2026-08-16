import { readFileSync } from 'node:fs';
import path from 'node:path';
import { describe, expect, test } from 'vitest';

const root = path.resolve(__dirname, '..');

function read(relativePath: string) {
  return readFileSync(path.join(root, relativePath), 'utf8');
}

describe('frontend quality baseline', () => {
  test('manifest exposes installability and shortcut metadata', () => {
    const manifest = JSON.parse(read('public/manifest.webmanifest'));

    expect(manifest.display).toBe('standalone');
    expect(manifest.display_override).toContain('standalone');
    expect(manifest.icons[0].purpose).toContain('maskable');
    expect(manifest.shortcuts.length).toBeGreaterThan(0);
    expect(manifest.categories).toContain('security');
  });

  test('headers define hardened transport and CSP policy', () => {
    const headers = read('public/_headers');

    expect(headers).toContain('Strict-Transport-Security');
    expect(headers).toContain("frame-ancestors 'self'");
    expect(headers).toContain('upgrade-insecure-requests');
    expect(headers).toContain('Origin-Agent-Cluster');
  });

  test('lighthouse thresholds encode core web vitals targets', () => {
    const lighthouse = JSON.parse(read('lighthouserc.json'));
    const assertions = lighthouse.ci.assert.assertions;

    expect(assertions['categories:performance'][1].minScore).toBeGreaterThanOrEqual(0.95);
    expect(assertions['largest-contentful-paint'][1].maxNumericValue).toBeLessThanOrEqual(2000);
    expect(assertions['cumulative-layout-shift'][1].maxNumericValue).toBeLessThanOrEqual(0.1);
    expect(assertions['interaction-to-next-paint'][1].maxNumericValue).toBeLessThanOrEqual(200);
    expect(assertions['first-contentful-paint'][1].maxNumericValue).toBeLessThanOrEqual(1500);
  });

  test('package scripts expose compatibility and reporting workflows', () => {
    const pkg = JSON.parse(read('package.json'));

    expect(pkg.scripts['audit:compat']).toContain('compatibility-smoke.spec.ts');
    expect(pkg.scripts['report:quality']).toContain('generate-quality-report');
    expect(pkg.scripts['ci:quality']).toContain('audit:responsive');
  });

});
