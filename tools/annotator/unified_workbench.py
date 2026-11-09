"""Single local entry point for scene and runtime annotation workbenches."""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit


def create_unified_server(scene_url: str | None = None, runtime_url: str | None = None,
                          *, port: int = 7420):
    urls = {}
    if scene_url is not None and runtime_url is not None:
        urls = {"scene": _local_url(scene_url).geturl(),
                "runtime": _local_url(runtime_url).geturl()}

    class Handler(BaseHTTPRequestHandler):
        server_version = "MtAgentLensUnifiedWorkbench/1"
        sys_version = ""

        def log_message(self, format, *args):  # noqa: A002 - stdlib signature
            return

        def do_GET(self):  # noqa: N802 - stdlib signature
            if urlsplit(self.path).path not in ("/", "/index.html"):
                self.send_error(404)
                return
            urls = self.server.workbench_urls
            if set(urls) != {"scene", "runtime"}:
                self.send_error(503)
                return
            page = (_PAGE.replace("SCENE_WORKBENCH_URL", urls["scene"])
                    .replace("RUNTIME_WORKBENCH_URL", urls["runtime"])).encode("utf-8")
            frame_sources = " ".join(sorted({
                f"http://{_local_url(urls['scene']).netloc}",
                f"http://{_local_url(urls['runtime']).netloc}",
            }))
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(page)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", (
                "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; "
                f"frame-src {frame_sources}; base-uri 'none'; form-action 'none'; "
                "frame-ancestors 'none'"
            ))
            self.end_headers()
            self.wfile.write(page)

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.workbench_urls = urls
    return server


def _local_url(value: str):
    parsed = urlsplit(value)
    if (parsed.scheme != "http" or parsed.hostname not in ("127.0.0.1", "localhost")
            or not parsed.port or parsed.path not in ("", "/") or parsed.query):
        raise ValueError("workbench URLs must be local HTTP origins")
    query = parse_qs(parsed.fragment, strict_parsing=True) if parsed.fragment else {}
    if query and (set(query) != {"token"} or len(query["token"]) != 1 or not query["token"][0]):
        raise ValueError("runtime workbench URL has an invalid local capability")
    return parsed


_PAGE = """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>MtAgentLens 人工标注工作台</title>
  <style>
    :root { color-scheme: dark; font: 13px/1.45 system-ui, sans-serif; background: #0c1017; color: #e7edf6; }
    * { box-sizing: border-box; }
    body { margin: 0; min-height: 100vh; display: flex; flex-direction: column; }
    header { height: 64px; display: flex; align-items: center; justify-content: space-between;
             padding: 0 24px; gap: 20px; border-bottom: 1px solid #283142; background: #101620; }
    h1 { margin: 0; font-size: 14px; font-weight: 600; letter-spacing: .02em; }
    h1 small { display: block; margin-top: 2px; font-size: 10px; font-weight: 400;
               letter-spacing: .14em; color: #9eafc7; }
    nav { display: flex; gap: 4px; padding: 4px; border: 1px solid #2b3547; border-radius: 9px; background: #0b111a; }
    button { border: 1px solid transparent; border-radius: 6px; padding: 7px 16px;
             font: inherit; color: #aebdd2; background: transparent; cursor: pointer; white-space: nowrap; }
    button:hover { color: #edf4ff; background: #1c2736; }
    button:focus-visible { outline: 2px solid #91bcff; outline-offset: 3px; }
    button[aria-selected="true"] { background: #213954; color: #d0e6ff; border-color: #385b7d; }
    main { flex: 1; min-height: 0; }
    iframe { display: none; border: 0; width: 100%; height: calc(100vh - 64px); min-height: 400px; }
    iframe.active { display: block; }
    @media (max-width: 560px) { header { height: 98px; padding: 10px 14px; gap: 8px; align-items: flex-start; flex-direction: column; } nav { width: 100%; } button { flex: 1; padding: 6px 10px; } iframe { height: calc(100vh - 98px); } }
  </style>
</head>
<body>
  <header>
    <h1>MtAgentLens 人工标注工作台<small>ANNOTATION WORKSPACE</small></h1>
    <nav role="tablist" aria-label="标注任务类型">
      <button id="scene-tab" role="tab" aria-controls="scene-panel" aria-selected="true"
              tabindex="0">场景设计标注</button>
      <button id="runtime-tab" role="tab" aria-controls="runtime-panel" aria-selected="false"
              tabindex="-1">运行证据标注</button>
    </nav>
  </header>
  <main>
    <iframe id="scene-panel" class="active" title="FuxiBench 场景设计标注"
            src="SCENE_WORKBENCH_URL" sandbox="allow-scripts allow-same-origin allow-forms allow-downloads"></iframe>
    <iframe id="runtime-panel" title="运行证据与身份/归因标注"
            data-src="RUNTIME_WORKBENCH_URL" sandbox="allow-scripts allow-same-origin allow-forms allow-downloads"></iframe>
  </main>
  <script>
    const tabs = [...document.querySelectorAll('[role=tab]')];
    function activate(tab) {
      tabs.forEach((item, index) => {
        const selected = item === tab;
        item.setAttribute('aria-selected', String(selected));
        item.tabIndex = selected ? 0 : -1;
        const panel = document.getElementById(item.getAttribute('aria-controls'));
        if (selected && !panel.getAttribute('src')) panel.src = panel.dataset.src;
        panel.classList.toggle('active', selected);
      });
      tab.focus();
    }
    tabs.forEach((tab, index) => {
      tab.addEventListener('click', () => activate(tab));
      tab.addEventListener('keydown', event => {
        if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
        event.preventDefault();
        const next = event.key === 'Home' ? 0 : event.key === 'End' ? tabs.length - 1 :
          (index + (event.key === 'ArrowRight' ? 1 : tabs.length - 1)) % tabs.length;
        activate(tabs[next]);
      });
    });
  </script>
</body>
</html>
"""
