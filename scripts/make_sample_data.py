"""Generate the synthetic Absheron demo field (UTM 39N) into sample_data/.

Usage: uv run python scripts/make_sample_data.py [--out DIR] [--seed N]

The generator lives in ``petrogis.sample_data`` so that tests (and a future "load demo data"
action) can use it too.
"""

import sys

from petrogis.sample_data import main

if __name__ == "__main__":
    sys.exit(main())
