"""源码目录直接运行入口: python gammaray.py ..."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))

from gammaray.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
