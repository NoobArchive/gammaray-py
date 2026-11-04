"""兼容层:优先导入本仓库内的 DeltaXUtils。

旧的外置 ``Gamma引擎相关/gamma_formats.py`` 只作为回退,方便尚未迁移的
工作目录;新代码应统一用 ``deltaxutils``。
"""

import sys
from pathlib import Path

try:
    from deltaxutils import gamma_formats as gf
    from deltaxutils import gamma_models as gm

    LEGACY_DIR = Path(gf.__file__).resolve().parent
except ImportError:
    _THIS = Path(__file__).resolve()
    _LEGACY = None
    for _p in _THIS.parents:
        if (_p / "gamma_formats.py").exists() and (_p / "gamma_models.py").exists():
            _LEGACY = _p
            break

    if _LEGACY is None:
        raise ImportError("找不到 deltaxutils/gamma_formats.py;请从仓库内运行")

    if str(_LEGACY) not in sys.path:
        sys.path.insert(0, str(_LEGACY))

    import gamma_formats as gf      # noqa: E402
    import gamma_models as gm       # noqa: E402

    LEGACY_DIR = _LEGACY
