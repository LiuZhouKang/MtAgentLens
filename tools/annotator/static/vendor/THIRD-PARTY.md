# Third-party assets

All bundles below are loaded from this local workbench server only. The page
uses no CDN and no outbound request; raters do not need npm at runtime.

## dagre 0.8.5

- Upstream: <https://github.com/dagrejs/dagre>
- Package: <https://registry.npmjs.org/dagre/-/dagre-0.8.5.tgz>
- License: MIT (see `LICENSE.dagre`)
- Vendored file: `dagre.min.js` (SHA-256
  `62eb9787ccfdbdf4148d4d99d31dbf9ee4770eafee81e637d759b52aac22cd51`)
- Purpose: offline layered preset positions for the relations graph.

## Cytoscape.js 3.30.4

- Upstream: <https://github.com/cytoscape/cytoscape.js>
- Package: <https://registry.npmjs.org/cytoscape/-/cytoscape-3.30.4.tgz>
  (SHA-256 `ba7baf9d2232aaac0bf6621c8565bdc33ba40a1b55a7b2a3f3eb17e35bbc9e12`)
- License: MIT (see `LICENSE.cytoscape`)
- Vendored file: `cytoscape.min.js` from the package `dist/` build (SHA-256
  `1bb5340e549511e111b31e5684872c949ad33d40ea5dba0ad8e7d90c62c7b3b9`)
- Purpose: canvas relation graph with preset Dagre positions, selection and
  pan/zoom.

## Apache ECharts 5.5.1

- Upstream: <https://github.com/apache/echarts>
- Package: <https://registry.npmjs.org/echarts/-/echarts-5.5.1.tgz>
  (SHA-256 `1f0eae29c406496a77789a7d7f56b811cae034ac95c0b3808215998f4ba0fbf3`)
- License: Apache-2.0 (see `LICENSE.echarts`; NOTICE in `NOTICE.echarts`)
- Vendored file: `echarts.min.js` from the package `dist/` build (SHA-256
  `e84270bd0cd5bdf60fefc26d00c2a391cb2e81f4d26a7a9ee16185a54773a3cf`)
- Purpose: canvas timeline scatter with lane grouping, range data zoom and safe
  rich-text tooltips.
