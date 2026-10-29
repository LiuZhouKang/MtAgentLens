import { copyFile, mkdir, access } from 'node:fs/promises';
import path from 'node:path';

const root = path.resolve(process.cwd(), 'out');

const exportRoutes = [
  { html: 'console.html', index: 'console/index.html' },
  { html: 'overview.html', index: 'overview/index.html' },
  { html: 'investigations.html', index: 'investigations/index.html' },
  { html: 'traces.html', index: 'traces/index.html' },
  { html: 'research/fuxibench.html', index: 'research/fuxibench/index.html' },
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
