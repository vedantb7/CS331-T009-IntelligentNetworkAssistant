"""Entry point so the assistant runs as ``python -m assistant``."""

from __future__ import annotations

import sys

from assistant.client import main

if __name__ == "__main__":
    sys.exit(main())
