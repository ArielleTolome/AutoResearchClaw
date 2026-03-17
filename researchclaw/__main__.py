"""Allow running as `python -m researchclaw`."""
from __future__ import annotations

import sys
from researchclaw.cli import main

sys.exit(main())
