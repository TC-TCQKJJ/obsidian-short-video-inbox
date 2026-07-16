#!/usr/bin/env python3
"""
兼容入口：在项目根目录执行 python main.py 即可。
若当前 Python 缺依赖，会自动改用 .venv/bin/python 重新运行。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def _reexec_with_venv_if_needed() -> None:
    try:
        import zhconv  # noqa: F401
    except ImportError:
        venv_python = ROOT / ".venv" / "bin" / "python"
        if venv_python.is_file():
            os.execv(
                str(venv_python),
                [str(venv_python), str(Path(__file__).resolve()), *sys.argv[1:]],
            )
        print(
            "缺少依赖。请先执行：\n"
            "  python3 -m venv .venv\n"
            "  source .venv/bin/activate\n"
            "  pip install -r requirements.txt\n"
            "或运行：./run.sh \"分享链接\"",
            file=sys.stderr,
        )
        raise SystemExit(1)


if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

_reexec_with_venv_if_needed()

from script.main import main

if __name__ == "__main__":
    raise SystemExit(main())
