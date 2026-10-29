import { execFileSync } from 'node:child_process';
import { existsSync, mkdtempSync, mkdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { describe, expect, it } from 'vitest';
import { buildAgentHref, parseAgentId } from '@/lib/agent-route';
import { buildResponsibilityUnitHref, parseUnitId } from '@/lib/responsibility-unit-route';

describe('repointed route builders', () => {
  it('routes agent deep links to the overview dashboard', () => {
    expect(buildAgentHref('agent-security')).toBe('/overview#agentId=agent-security');
    expect(buildAgentHref()).toBe('/overview');
    expect(parseAgentId(new URLSearchParams(), '#agentId=agent-security')).toBe('agent-security');
  });

  it('routes responsibility-unit deep links to the overview dashboard', () => {
    expect(buildResponsibilityUnitHref('unit-security-anchor')).toBe(
      '/overview#unitId=unit-security-anchor',
    );
    expect(buildResponsibilityUnitHref()).toBe('/overview');
    expect(parseUnitId(new URLSearchParams(), '#unitId=unit-security-anchor')).toBe(
      'unit-security-anchor',
    );
  });
});

describe('static export routes', () => {
  it('normalizes current routes without generating retired route indexes', () => {
    const workspace = mkdtempSync(path.join(tmpdir(), 'mtagentlens-export-'));
    const output = path.join(workspace, 'out');
    const currentRoutes = ['console', 'overview', 'investigations', 'traces', 'research/fuxibench'];
    const retiredRoutes = ['clusters', 'agents', 'handoff'];
    try {
      mkdirSync(path.join(output, 'research'), { recursive: true });
      writeFileSync(path.join(output, 'index.html'), '<main>root</main>');
      for (const route of [...currentRoutes, ...retiredRoutes]) {
        writeFileSync(path.join(output, `${route}.html`), `<main>${route}</main>`);
      }

      execFileSync(process.execPath, [path.resolve('scripts/normalize-static-export.mjs')], {
        cwd: workspace,
      });

      for (const route of currentRoutes) {
        expect(readFileSync(path.join(output, route, 'index.html'), 'utf8')).toBe(
          `<main>${route}</main>`,
        );
      }
      for (const route of retiredRoutes) {
        expect(existsSync(path.join(output, route, 'index.html'))).toBe(false);
      }
      expect(readFileSync(path.join(output, 'index.html'), 'utf8')).toBe('<main>root</main>');
      expect(existsSync(path.join(output, 'index', 'index.html'))).toBe(false);
    } finally {
      rmSync(workspace, { recursive: true, force: true });
    }
  });
});
