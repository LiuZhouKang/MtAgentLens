#!/usr/bin/env python3
"""Convert Claude stream-json logs to readable Markdown."""
import json, sys
from pathlib import Path
from datetime import datetime

def export_log(stream_json_path: Path, output_path: Path | None = None):
    """Convert a stream-json agent log to Markdown."""
    if output_path is None:
        output_path = stream_json_path.with_suffix('.md')
    
    lines = []
    lines.append(f"# Claude Agent Session Log\n")
    lines.append(f"**Source:** `{stream_json_path.name}`  \n")
    lines.append(f"**Exported:** {datetime.now().isoformat()}\n")
    
    msg_count = 0
    with open(stream_json_path) as f:
        for line in f:
            try:
                msg = json.loads(line.strip())
            except (json.JSONDecodeError, ValueError):
                continue
            
            msg_type = msg.get('type', '?')
            msg_count += 1
            
            if msg_type == 'system':
                subtype = msg.get('subtype', '')
                if subtype == 'init':
                    lines.append(f"\n## ⚙️ System Init\n")
            
            elif msg_type == 'assistant':
                content = msg.get('message', {}).get('content', [])
                for block in content:
                    if block.get('type') == 'text':
                        text = block.get('text', '')
                        if text.strip():
                            lines.append(f"\n### 💬 Assistant\n")
                            lines.append(f"{text}\n")
                    elif block.get('type') == 'tool_use':
                        name = block.get('name', '?')
                        inp = block.get('input', {})
                        lines.append(f"\n### 🔧 Tool: `{name}`\n")
                        lines.append(f"```json")
                        lines.append(json.dumps(inp, indent=2, ensure_ascii=False))
                        lines.append(f"```\n")
            
            elif msg_type == 'user':
                content = msg.get('message', {}).get('content', [])
                for block in content:
                    if block.get('type') == 'tool_result':
                        result = block.get('content', '')
                        is_error = block.get('is_error', False)
                        prefix = "❌ Error" if is_error else "📤 Result"
                        result_str = str(result)
                        if len(result_str) > 2000:
                            result_str = result_str[:2000] + "\n\n... (truncated)"
                        lines.append(f"\n### {prefix}\n")
                        lines.append(f"```\n{result_str}\n```\n")
            
            elif msg_type == 'result':
                lines.append(f"\n---\n✅ **Session ended**\n")
    
    lines.append(f"\n---\n*{msg_count} messages total*")
    output_path.write_text('\n'.join(lines))
    return output_path

if __name__ == '__main__':
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument('logfile', help='agent_N.log file to convert')
    p.add_argument('-o', '--output', help='Output markdown file')
    args = p.parse_args()
    out = export_log(Path(args.logfile), Path(args.output) if args.output else None)
    print(f"Exported: {out}")
