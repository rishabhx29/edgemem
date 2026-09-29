"""Make the repository's own directories importable from the test suite.

``pythonpath`` in the project configuration covers the engine. The schema packs
live in ``fixtures/`` beside it rather than inside it, because a vertical that
lived in the engine's package would be a vertical the engine imports — which is
the arrangement this repository is arguing against.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
for _path in (ROOT, ROOT / "src"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))
