"""Entry point for the packaged app (PyInstaller needs a plain script, not `-m laqta`)."""

import sys

from laqta.__main__ import main

sys.exit(main())
