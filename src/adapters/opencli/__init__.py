"""
OpenCLI runtime adapters (Step D6).

Two use cases:
  - sec_8k_fulltext       : retrieves the full text of an 8-K filing
                            via OpenCLI; complements src/adapters/alt_data/
                            sec_edgar.py (which returns metadata only).
  - github_commit_messages: retrieves recent commit messages for a
                            mapped repo; complements
                            src/adapters/alt_data/github_public.py.

Both satisfy `RULES.md` §17 (OpenCLI Subsystem Rules — 27 ACTIVE rules,
INTEGRATION_STATUS PENDING until this code lands).
"""
from __future__ import annotations

from . import sec_8k_fulltext  # noqa: F401
from . import github_commit_messages  # noqa: F401
from .base import OpenCLIAdapter, OpenCLIResult, OPENCLI_OUTPUT_FIELDS  # noqa: F401
