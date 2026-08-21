from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from compare.tools.runner import run_comparison


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run local offline tool comparison on MtAgentLens datasets")
    parser.add_argument("--dataset", default="FuxiBench")
    parser.add_argument("--tools", default="all", help="Comma-separated tool ids or all")
    parser.add_argument("--dataset-root", default=str(Path("compare/Dataset")))
    parser.add_argument("--lockfile", default=str(Path("compare/tool-lock.json")))
    parser.add_argument("--output", default="")
    parser.add_argument("--experimental", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    tools = [tool.strip() for tool in args.tools.split(",") if tool.strip()]
    if not tools:
        raise SystemExit(2)

    dataset_root = _project_path(args.dataset_root)
    lockfile = _project_path(args.lockfile)
    output_dir = Path(args.output) if args.output else PROJECT_ROOT / "compare" / "results" / "tool-comparison" / args.dataset

    return run_comparison(
        dataset_name=args.dataset,
        tools=tools,
        dataset_root=dataset_root,
        lockfile=lockfile,
        output_dir=output_dir,
        experimental=args.experimental,
    )


def _project_path(value: str) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return PROJECT_ROOT / path


if __name__ == "__main__":
    raise SystemExit(main())
