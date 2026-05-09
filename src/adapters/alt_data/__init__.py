"""
Alt-data adapters package (Step D).

Importing this package triggers each adapter module's @register_adapter
side effect so the registry is populated.
"""
from __future__ import annotations

# Eager imports so registry is populated when callers `from
# adapters.alt_data import REGISTRY`.
#
# Note: Reddit was scoped initially but excluded 2026-04-28 (registration
# friction + anti-pollution rationale). The 3 sources below span Tier 1-3
# of the credibility hierarchy in RULES.md §10.13.
from . import wikipedia_pageviews  # noqa: F401
from . import sec_edgar  # noqa: F401
from . import github_public  # noqa: F401
from . import fmp_sentiment  # noqa: F401  (Step D8 — disabled by default in regression baseline)
from . import sec_ownership  # noqa: F401  (Step D9 — sec_13f, sec_form4, sec_def14a; disabled by default)
from . import polygon_news  # noqa: F401  (D5 Option C — historical news; disabled by default)

from .base import AltDataAdapter, AltDataResult  # noqa: F401
from .registry import REGISTRY, get_adapter, list_adapters  # noqa: F401
