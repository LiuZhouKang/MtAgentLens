import { NextResponse, type NextRequest } from 'next/server';

/**
 * 开发模式优雅降级：/api/runs 与 /api/runs/active 是前端启动/轮询时探测
 * 后端 collector（7397）的端点。后端未运行时，next.config 的 dev rewrite 会
 * 返回 500 → 浏览器自动记录 console error（前端 JS 无法阻止）。
 *
 * 这里在后端不可达或返回非 2xx 时，返回 200 + `{ data: null }`（语义 = 「无
 * run 列表 / 无活跃 run」），彻底消除 500 报错；后端可达时透传真实响应。
 * 其余 /api/*（SSE、db、alerts 等）保持原 rewrite 行为不受影响。
 *
 * 生产环境为 Rust 内嵌静态导出，不经过此 middleware。
 */
export default async function middleware(req: NextRequest): Promise<NextResponse> {
  const { pathname } = req.nextUrl;
  if (pathname !== '/api/runs' && pathname !== '/api/runs/active') {
    return NextResponse.next();
  }
  try {
    const upstream = await fetch(`http://localhost:7397${pathname}`, {
      signal: AbortSignal.timeout(2500),
    });
    if (upstream.ok) {
      return new NextResponse(upstream.body, {
        status: 200,
        headers: {
          'Content-Type': upstream.headers.get('content-type') || 'application/json',
        },
      });
    }
    // 后端可达但返回非 2xx：视为「无活跃 run / 无 run 列表」，优雅降级
    return gracefulEmpty(pathname);
  } catch {
    // 后端不可达（collector 未运行 / 断连）：优雅降级为空数据，避免 500 报错
    return gracefulEmpty(pathname);
  }
}

function gracefulEmpty(pathname: string): NextResponse {
  // /api/runs 期待数组（空 = 无 run 列表）；/api/runs/active 期待 null 或
  // { data: null }（api.getActiveRun 会把 { data: null } 归一为 null）。
  return NextResponse.json(pathname === '/api/runs' ? [] : { data: null });
}

export const config = {
  matcher: ['/api/runs', '/api/runs/active'],
};
