"""
Adapters package — alt-data + OpenCLI runtime adapters (Step D).

Two sub-packages:
  - alt_data : direct adapters for free public sources (Wikipedia,
               SEC EDGAR, GitHub, Reddit). Each satisfies RULES.md §11.
  - opencli  : OpenCLI-mediated adapters (SEC 8-K full-text retrieval,
               GitHub commit-message text). Each satisfies RULES.md §17.

The packet generator stays the canonical entry point; adapters are
plumbed in via the manifest builder so block toggling, hashing, and
audit trail behavior remain in one place.
"""
