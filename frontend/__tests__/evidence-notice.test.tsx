// @vitest-environment jsdom
import { afterEach, describe, expect, it } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import EvidenceNotice from '@/components/investigation/evidence-notice';

afterEach(cleanup);

describe('EvidenceNotice', () => {
  it('renders a failed fetch as an error, not as zero evidence', () => {
    render(<EvidenceNotice loading={false} error="network down" />);
    expect(screen.getByRole('alert').textContent).toContain('证据加载失败');
    expect(screen.getByRole('alert').textContent).toContain('network down');
  });

  it('renders a loading hint', () => {
    render(<EvidenceNotice loading error={null} />);
    expect(screen.getByTestId('evidence-loading')).toBeDefined();
  });

  it('renders nothing when the evidence is loaded', () => {
    render(<EvidenceNotice loading={false} error={null} />);
    expect(screen.queryByRole('alert')).toBeNull();
  });
});
