import { copyFile, mkdir, access } from 'node:fs/promises';
import path from 'node:path';

const root = path.resolve(process.cwd(), 'out');

const exportRoutes = [
  { html: 'overview.html', index: 'overview/index.html' },
  { html: 'traces.html', index: 'traces/index.html' },
  { html: 'clusters.html', index: 'clusters/index.html' },
  { html: 'agents.html', index: 'agents/index.html' },
  { html: 'handoff.html', index: 'handoff/index.html' },
  { html: 'investigations.html', index: 'investigations/index.html' },
];

async function fileExists(filePath) {
  try {
    await access(filePath);
    return true;
  } catch {
    return false;
  }
}

async function normalizeRoute(route) {
  const source = path.join(root, route.html);
  const target = path.join(root, route.index);

  if (!(await fileExists(source))) {
    return;
  }

  await mkdir(path.dirname(target), { recursive: true });
  await copyFile(source, target);
}

await Promise.all(exportRoutes.map(normalizeRoute));
