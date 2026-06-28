#!/usr/bin/env python
"""Janus user-facing CLI entry point.

Run ``python janus.py --help`` for the command surface. The core path is:

    python janus.py import WTI data/WTI.csv
    python janus.py run WTI --window 2024Q4

See ``cli/`` for the implementation and the README quick start for examples.
"""

import sys

from cli.main import main

if __name__ == "__main__":
    sys.exit(main())
