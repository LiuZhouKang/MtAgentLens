import type { Metadata } from 'next';
import './globals.css';

export const metadata: Metadata = {
  title: 'AgentSight — eBPF-Powered LLM Agent Observability',
  description: 'Near-Earth orbital command center for AI agent monitoring',
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className="dark">
      <head>
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link rel="preconnect" href="https://fonts.gstatic.com" crossOrigin="anonymous" />
        <link href="https://fonts.googleapis.com/css2?family=Inter:wght@350;400;500;600;700&display=swap" rel="stylesheet" />
      </head>
      <body style={{ margin: 0, padding: 0, overflow: 'hidden', width: '100vw', height: '100vh', background: '#000108', color: '#e8e8ed', fontFamily: 'Inter, system-ui' }}>
        {children}
      </body>
    </html>
  );
}
