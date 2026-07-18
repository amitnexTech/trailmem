"""`python -m trailmem` — CLI fallback for when the `trailmem` launcher is
blocked (Windows Smart App Control rejects unsigned per-install .exes) or
simply off PATH."""

import sys

from .cli import main

sys.exit(main())
