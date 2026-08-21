import { http, HttpResponse } from 'msw';
import { demoAlerts, demoEvidence, demoGraph } from '@/lib/demo-data';

const demoRun = {
  run_id: 'demo-run-1',
  started_at_ns: Date.parse('2026-06-20T09:30:00.000Z') * 1_000_000,
  state: 'demo',
  scenario_ids: ['demo'],
};

export const handlers = [
  http.get('/api/demo', () => {
    return HttpResponse.json({
      data: {
        graph: demoGraph,
        alerts: demoAlerts,
      },
    });
  }),

  http.get('/api/runs', () => {
    return HttpResponse.json({ data: [demoRun] });
  }),

  http.get('/api/runs/active', () => {
    return HttpResponse.json({ data: demoRun });
  }),

  http.get('/api/runs/:id', ({ params }) => {
    return HttpResponse.json({
      data: {
        ...demoRun,
        run_id: String(params.id),
      },
    });
  }),

  http.get('/api/runs/:id/graph', () => {
    return HttpResponse.json({ data: demoGraph });
  }),

  http.get('/api/runs/:id/alerts', () => {
    return HttpResponse.json({ data: demoAlerts });
  }),

  http.get('/api/runs/:id/evidence', () => {
    return HttpResponse.json({
      data: {
        evidence: demoEvidence,
        intents: [],
        actions: [],
        non_links: [],
      },
    });
  }),

  http.get('/api/runs/:id/snapshots', () => {
    return HttpResponse.json({ data: [] });
  }),

  http.get('/api/runs/:id/events', () => {
    return HttpResponse.json({ data: [] });
  }),
];
