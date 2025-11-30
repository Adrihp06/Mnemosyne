"""Core functionality: search, re-ranking, and truncation."""

from mnemosyne.core.search import (
    HybridSearcher,
    get_searcher,
    search,
    search_for_duplicates,
)

__all__ = [
    "HybridSearcher",
    "get_searcher",
    "search",
    "search_for_duplicates",
]
