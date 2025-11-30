"""
Hybrid search and re-ranking for security reports.

Implements dual-vector search (dense + sparse BM25) with FlashRank re-ranking
for accurate duplicate detection.
"""

from typing import List, Optional

from fastembed import SparseTextEmbedding
from flashrank import Ranker, RerankRequest
from loguru import logger
from qdrant_client.models import Prefetch, FusionQuery, Fusion, SparseVector

from mnemosyne.config import get_settings
from mnemosyne.core.embeddings import get_embedding_generator
from mnemosyne.db.qdrant import get_qdrant_client
from mnemosyne.models.schema import NormalizedReport, SearchResult


class HybridSearcher:
    """
    Hybrid search engine combining dense vectors and sparse BM25.

    Uses Qdrant's prefetch + RRF (Reciprocal Rank Fusion) for optimal
    hybrid search performance.
    """

    def __init__(self):
        """Initialize the hybrid searcher with Qdrant and embeddings."""
        self.settings = get_settings()
        self.qdrant = get_qdrant_client()
        self.embedder = get_embedding_generator()
        self.sparse_embedder: Optional[SparseTextEmbedding] = None
        self.reranker: Optional[Ranker] = None

        logger.debug("HybridSearcher initialized")

    def _get_sparse_embedder(self) -> SparseTextEmbedding:
        """
        Lazy-load the sparse text embedder for BM25.

        Returns:
            SparseTextEmbedding instance
        """
        if self.sparse_embedder is None:
            logger.info("Loading sparse embedder: Qdrant/bm25")
            self.sparse_embedder = SparseTextEmbedding(model_name="Qdrant/bm25")
            logger.success("Sparse embedder loaded successfully")

        return self.sparse_embedder

    def _get_reranker(self) -> Ranker:
        """
        Lazy-load the FlashRank reranker.

        Returns:
            Ranker instance (ms-marco-TinyBERT-L-2-v2)
        """
        if self.reranker is None:
            logger.info(f"Loading reranker: {self.settings.rerank_model}")
            self.reranker = Ranker(
                model_name=self.settings.rerank_model,
                cache_dir=".cache/flashrank"
            )
            logger.success("Reranker loaded successfully")

        return self.reranker

    def hybrid_search(
        self, query_text: str, limit: int = 20
    ) -> List[SearchResult]:
        """
        Perform hybrid search using dense + sparse vectors with RRF fusion.

        Combines semantic similarity (dense) with keyword matching (sparse BM25)
        using Reciprocal Rank Fusion for optimal results.

        Args:
            query_text: The search query text
            limit: Maximum number of results to return

        Returns:
            List of SearchResult objects sorted by relevance
        """
        logger.info(f"Hybrid search (RRF): '{query_text[:100]}...' (limit={limit})")

        try:
            # Generate dense embedding for query
            dense_vector, _ = self.embedder.generate_embedding(query_text)
            logger.debug(f"Generated dense vector ({len(dense_vector)} dims)")

            # Generate sparse embedding for query (BM25)
            sparse_embedder = self._get_sparse_embedder()
            sparse_embeddings = list(sparse_embedder.embed([query_text]))
            sparse_embedding = sparse_embeddings[0]

            sparse_vector = SparseVector(
                indices=sparse_embedding.indices.tolist(),
                values=sparse_embedding.values.tolist()
            )
            logger.debug(f"Generated sparse vector ({len(sparse_embedding.indices)} non-zero entries)")

            # Perform hybrid search with RRF fusion
            results = self.qdrant.client.query_points(
                collection_name=self.settings.qdrant_collection_name,
                prefetch=[
                    # Dense vector search (semantic similarity)
                    Prefetch(
                        query=dense_vector,
                        using="dense",
                        limit=limit,
                    ),
                    # Sparse vector search (BM25 keyword matching)
                    Prefetch(
                        query=sparse_vector,
                        using="sparse",
                        limit=limit,
                    ),
                ],
                # Fuse results using Reciprocal Rank Fusion
                query=FusionQuery(fusion=Fusion.RRF),
                limit=limit,
                with_payload=True,
            )

            # Convert to SearchResult objects
            search_results = []
            for point in results.points:
                search_results.append(
                    SearchResult(
                        report_id=str(point.id),
                        score=point.score,
                        report=NormalizedReport(**point.payload),
                    )
                )

            logger.success(
                f"Hybrid search (RRF) found {len(search_results)} candidates "
                f"(top score: {search_results[0].score:.3f})" if search_results else "No results"
            )
            return search_results

        except Exception as e:
            logger.error(f"Hybrid search failed: {e}")
            # Fallback to dense-only search if hybrid fails
            logger.warning("Falling back to dense-only search")

            dense_vector, _ = self.embedder.generate_embedding(query_text)
            results = self.qdrant.client.query_points(
                collection_name=self.settings.qdrant_collection_name,
                query=dense_vector,
                using="dense",
                limit=limit,
                with_payload=True,
            )

            search_results = []
            for point in results.points:
                search_results.append(
                    SearchResult(
                        report_id=str(point.id),
                        score=point.score,
                        report=NormalizedReport(**point.payload),
                    )
                )

            logger.success(f"Dense-only search found {len(search_results)} candidates")
            return search_results

    def rerank_results(
        self, query: str, candidates: List[SearchResult], top_k: int = 10
    ) -> List[SearchResult]:
        """
        Re-rank search results using FlashRank cross-encoder.

        Args:
            query: The original search query
            candidates: List of SearchResult objects to re-rank
            top_k: Number of top results to return after re-ranking

        Returns:
            Re-ranked list of SearchResult objects
        """
        if not candidates:
            logger.warning("No candidates to re-rank")
            return []

        logger.info(f"Re-ranking {len(candidates)} candidates (top_k={top_k})")

        # Prepare passages for re-ranking
        # We create a rich text representation of each report for better matching
        passages = []
        for result in candidates:
            report = result.report
            # Combine key fields for re-ranking
            passage_text = (
                f"{report.title} | "
                f"{report.vulnerability_type} in {report.affected_component} | "
                f"{report.summary} | "
                f"Steps: {' '.join(report.reproduction_steps[:3])}"  # First 3 steps
            )
            passages.append({
                "id": result.report_id,
                "text": passage_text,
                "meta": {"original_score": result.score}
            })

        try:
            # Create rerank request
            reranker = self._get_reranker()
            rerank_request = RerankRequest(
                query=query,
                passages=passages
            )

            # Perform re-ranking
            reranked = reranker.rerank(rerank_request)

            # Map re-ranked results back to SearchResult objects
            reranked_results = []
            for ranked_passage in reranked[:top_k]:
                # Find original SearchResult by ID
                original = next(
                    (r for r in candidates if r.report_id == ranked_passage["id"]),
                    None
                )
                if original:
                    # Update with re-ranked score
                    reranked_result = SearchResult(
                        report_id=original.report_id,
                        score=ranked_passage["score"],  # New re-ranked score
                        report=original.report,
                    )
                    reranked_results.append(reranked_result)

            logger.success(
                f"Re-ranking complete: {len(reranked_results)} results "
                f"(top score: {reranked_results[0].score:.3f})"
            )

            return reranked_results

        except Exception as e:
            logger.error(f"Re-ranking failed: {e}")
            # Fallback: return original candidates if re-ranking fails
            logger.warning("Returning original candidates without re-ranking")
            return candidates[:top_k]


# Global instance (lazy-loaded)
_searcher: Optional[HybridSearcher] = None


def get_searcher() -> HybridSearcher:
    """
    Get the global HybridSearcher instance.

    Returns:
        HybridSearcher instance
    """
    global _searcher
    if _searcher is None:
        _searcher = HybridSearcher()
    return _searcher


def search(query: str, limit: int = 5) -> List[SearchResult]:
    """
    Convenience function to search for reports.

    Performs hybrid search + re-ranking in one call.

    Args:
        query: Search query text
        limit: Number of final results to return

    Returns:
        List of SearchResult objects, re-ranked by relevance
    """
    searcher = get_searcher()

    # Step 1: Hybrid search for candidates
    candidates = searcher.hybrid_search(
        query_text=query,
        limit=get_settings().top_k_candidates  # e.g., 20
    )

    # Step 2: Re-rank top candidates
    reranked = searcher.rerank_results(
        query=query,
        candidates=candidates,
        top_k=get_settings().rerank_top_k  # e.g., 10
    )

    # Step 3: Return final top results
    return reranked[:limit]


def search_for_duplicates(
    normalized_report: NormalizedReport, limit: int = 5
) -> List[SearchResult]:
    """
    Search for potential duplicates of a normalized report.

    Uses a multi-field query combining title, type, component, and summary.

    Args:
        normalized_report: The report to search duplicates for
        limit: Number of results to return

    Returns:
        List of SearchResult objects, ordered by relevance
    """
    # Build a rich query from the report
    query = (
        f"{normalized_report.title} "
        f"{normalized_report.vulnerability_type} "
        f"{normalized_report.affected_component} "
        f"{normalized_report.summary}"
    )

    logger.info(
        f"Searching for duplicates of: {normalized_report.title[:50]}..."
    )

    return search(query=query, limit=limit)
