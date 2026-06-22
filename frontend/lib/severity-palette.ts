import type { ResponsibilityAlert } from '@/lib/types';

export type SeverityPalette = {
  color: string;
  bg: string;
  border: string;
  glow: string;
};

const severityMap: Record<ResponsibilityAlert['severity'], SeverityPalette> = {
  Critical: {
    color: '#fb7185',
    bg: 'rgba(251,113,133,0.1)',
    border: 'rgba(251,113,133,0.26)',
    glow: 'rgba(251,113,133,0.42)',
  },
  Warning: {
    color: '#fbbf24',
    bg: 'rgba(251,191,36,0.1)',
    border: 'rgba(251,191,36,0.24)',
    glow: 'rgba(251,191,36,0.32)',
  },
  Info: {
    color: '#7dd3fc',
    bg: 'rgba(125,211,252,0.1)',
    border: 'rgba(125,211,252,0.24)',
    glow: 'rgba(125,211,252,0.34)',
  },
};

export function getSeverityPalette(severity: ResponsibilityAlert['severity']): SeverityPalette {
  return severityMap[severity] ?? severityMap.Info;
}
