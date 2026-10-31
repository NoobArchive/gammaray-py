"""兼容层:定位并导入 Gamma引擎相关/ 下的现有工具模块。

迁移期这样做可以避免一次性搬动 140KB 代码;后续按 docs/ARCHITECTURE.md
把模块逐个搬进 gammaray 包。
"""

import sys
from pathlib import Path

_THIS = Path(__file__).resolve()
_LEGACY = None
for _p in _THIS.parents:
    if (_p / "gamma_formats.py").exists() and (_p / "gamma_models.py").exists():
        _LEGACY = _p
        break

if _LEGACY is None:
    raise ImportError("找不到 Gamma引擎相关/gamma_formats.py;请从仓库内运行")

if str(_LEGACY) not in sys.path:
    sys.path.insert(0, str(_LEGACY))

import gamma_formats as gf          # noqa: E402
import gamma_models as gm           # noqa: E402

LEGACY_DIR = _LEGACY
