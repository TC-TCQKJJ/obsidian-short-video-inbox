#!/usr/bin/env python3
"""命令行入口：抖音分享链接提取文案。"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# 保证从项目根目录可导入 src
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from script.config import Settings
from script.paths import OUTPUT_DIR
from script.pipeline import process_douyin_share


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="抖音分享链接：视频转写文案，图文直接提取配文字案（本地 Whisper，无需 API）。"
    )
    p.add_argument(
        "url",
        nargs="?",
        help="抖音分享链接或整段分享文案（含 v.douyin.com 短链）",
    )
    p.add_argument(
        "-o",
        "--output",
        type=Path,
        default=OUTPUT_DIR,
        help=f"输出根目录（默认: {OUTPUT_DIR}）",
    )
    p.add_argument(
        "--model",
        default="small",
        choices=["tiny", "base", "small", "medium", "large-v2", "large-v3"],
        help="faster-whisper 模型大小（默认: small）",
    )
    p.add_argument(
        "--device",
        default="auto",
        help="推理设备: auto / cpu / cuda（默认: auto）",
    )
    p.add_argument(
        "--compute-type",
        default="default",
        help="计算精度，如 int8、float16（默认: default）",
    )
    return p.parse_args()


def main() -> int:
    args = parse_args()
    share = args.url
    if not share:
        print("请粘贴抖音分享链接或分享文案，回车结束输入：", file=sys.stderr)
        share = sys.stdin.read().strip()
    if not share:
        print("错误: 未提供链接", file=sys.stderr)
        return 1

    settings = Settings(
        output_dir=args.output,
        whisper_model=args.model,
        whisper_device=args.device,
        whisper_compute_type=args.compute_type,
    )

    print("开始处理…")
    try:
        out_dir = process_douyin_share(share, settings=settings)
    except Exception as e:
        print(f"处理失败: {e}", file=sys.stderr)
        return 1

    print(f"完成。输出目录: {out_dir.resolve()}")
    print(f"文案文件: {(out_dir / 'transcript.txt').resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
