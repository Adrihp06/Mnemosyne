"""
Embedding generation using BGE-M3 for dual-vector (dense + sparse) embeddings.

Uses FastEmbed for local, efficient embedding generation without API costs.
"""

from typing import Optional

from fastembed import TextEmbedding
from loguru import logger

from mnemosyne.config import get_settings
from mnemosyne.models.schema import NormalizedReport


class EmbeddingGenerator:
    """
    Generates dual-vector embeddings using BGE-M3.

    BGE-M3 produces both dense (1024 dims) and sparse (learned) vectors
    in a single forward pass, optimized for hybrid search.
    """

    def __init__(self, model_name: Optional[str] = None):
        """
        Initialize the embedding generator.

        Args:
            model_name: Optional model name (uses settings if not provided)
        """
        settings = get_settings()
        self.model_name = model_name or settings.embedding_model
        self.embedding_dim = settings.embedding_dimension

        logger.info(f"Loading embedding model: {self.model_name}")
        self.model = TextEmbedding(model_name=self.model_name)
        logger.success(f"Embedding model loaded successfully ({self.embedding_dim} dims)")

    def create_embedding_text(self, normalized: NormalizedReport) -> str:
        """
        Create the text to embed from a normalized report.

        Combines all relevant fields into a single text that captures
        the full context of the security report.

        Args:
            normalized: The normalized security report

        Returns:
            Combined text for embedding
        """
        # Combine technical artifacts
        artifacts_text = ""
        if normalized.technical_artifacts:
            artifacts_text = "\n".join(
                [
                    f"{artifact.type}: {artifact.content}"
                    for artifact in normalized.technical_artifacts
                ]
            )

        # Build comprehensive text
        text_parts = [
            f"Title: {normalized.title}",
            f"Summary: {normalized.summary}",
            f"Vulnerability Type: {normalized.vulnerability_type}",
            f"Severity: {normalized.severity}",
            f"Affected Component: {normalized.affected_component}",
            f"Reproduction Steps: {' | '.join(normalized.reproduction_steps)}",
        ]

        if artifacts_text:
            text_parts.append(f"Technical Artifacts:\n{artifacts_text}")

        if normalized.technologies:
            text_parts.append(f"Technologies: {', '.join(normalized.technologies)}")

        text_parts.append(f"Impact: {normalized.impact}")

        if normalized.remediation:
            text_parts.append(f"Remediation: {normalized.remediation}")

        embedding_text = "\n\n".join(text_parts)

        logger.debug(
            f"Created embedding text: {len(embedding_text)} chars, "
            f"~{len(embedding_text) // 4} tokens"
        )

        return embedding_text

    def generate_embedding(self, text: str) -> tuple[list[float], dict]:
        """
        Generate dual-vector embedding (dense + sparse) from text.

        Args:
            text: The text to embed

        Returns:
            Tuple of (dense_vector, sparse_vector)
            - dense_vector: List of 1024 floats
            - sparse_vector: Dict with 'indices' and 'values' keys
        """
        logger.debug(f"Generating embedding for text ({len(text)} chars)")

        # BGE-M3 generates both dense and sparse in one call
        # Note: FastEmbed API might vary, adjust if needed
        embeddings = list(self.model.embed([text]))

        if not embeddings:
            raise ValueError("Failed to generate embeddings")

        dense_vector = embeddings[0].tolist()

        # TODO: FastEmbed might not expose sparse directly
        # For now, we'll return empty sparse (Qdrant can generate BM25)
        # When FastEmbed supports BGE-M3 sparse, update this
        sparse_vector = {"indices": [], "values": []}

        logger.debug(
            f"Generated dense vector: {len(dense_vector)} dims, "
            f"sparse: {len(sparse_vector['indices'])} terms"
        )

        return dense_vector, sparse_vector

    def embed_report(
        self, normalized: NormalizedReport
    ) -> tuple[str, list[float], dict]:
        """
        Generate embeddings for a normalized report.

        Args:
            normalized: The normalized security report

        Returns:
            Tuple of (embedding_text, dense_vector, sparse_vector)
        """
        embedding_text = self.create_embedding_text(normalized)
        dense_vector, sparse_vector = self.generate_embedding(embedding_text)

        return embedding_text, dense_vector, sparse_vector


# Global instance (lazy-loaded)
_generator: Optional[EmbeddingGenerator] = None


def get_embedding_generator() -> EmbeddingGenerator:
    """
    Get the global embedding generator instance.

    Loads the model once and reuses it for all embeddings.

    Returns:
        EmbeddingGenerator instance
    """
    global _generator
    if _generator is None:
        _generator = EmbeddingGenerator()
    return _generator


def generate_embedding(text: str) -> tuple[list[float], dict]:
    """
    Convenience function to generate embeddings.

    Args:
        text: The text to embed

    Returns:
        Tuple of (dense_vector, sparse_vector)
    """
    generator = get_embedding_generator()
    return generator.generate_embedding(text)


def embed_report(normalized: NormalizedReport) -> tuple[str, list[float], dict]:
    """
    Convenience function to embed a report.

    Args:
        normalized: The normalized security report

    Returns:
        Tuple of (embedding_text, dense_vector, sparse_vector)
    """
    generator = get_embedding_generator()
    return generator.embed_report(normalized)
