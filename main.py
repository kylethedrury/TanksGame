"""Project entry point.

This file keeps ``python main.py`` working by adding ``src/`` to the import
path and then handing off to ``game.main()``.
"""

from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
# Keep imports simple for the rest of the project.
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from game import main


if __name__ == "__main__":
    main()
