#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0 OR MIT
"""将当前场景、fixture、GT 冻结为有内容校验的比较输入；不生成标注意见。"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from compare.tools.dataset_release import build_release


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenarios", type=Path, default=ROOT / "env/scenarios")
    parser.add_argument("--ground-truth", type=Path, default=ROOT / "datasets/FuxiBench/ground_truth")
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--sink-host", default=None,
                        help="rewrite fixture loopback collection endpoints to this address "
                             "(required for external-egress corpus validity; see experimental-design §85)")
    parser.add_argument("--allow-legacy-type-c", action="store_true",
                        help="accept historical Type C records; current taxonomy is A/B/benign")
    args = parser.parse_args()
    release = build_release(args.scenarios, args.ground_truth, args.out,
                            sink_host=args.sink_host,
                            allow_legacy_type_c=args.allow_legacy_type_c)
    print(json.dumps({key: value for key, value in release.items() if key != "files"}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
