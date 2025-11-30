"""
Qdrant client and operations for security reports storage.

Handles collection management, report ingestion, and hybrid search.
"""

import hashlib
import uuid
from typing import Optional

from loguru import logger
from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    PointStruct,
    VectorParams,
    SparseVectorParams,
)

from mnemosyne.config import get_settings
from mnemosyne.core.embeddings import embed_report
from mnemosyne.models.schema import (
    IngestionResult,
    NormalizedReport,
    SearchResult,
)


class QdrantDB:
    """
    Qdrant database client for security reports.

    Manages the security_reports collection with hybrid search
    (dense + sparse vectors) for duplicate detection.
    """

    def __init__(self, url: Optional[str] = None, api_key: Optional[str] = None):
        """
        Initialize Qdrant client.

        Args:
            url: Optional Qdrant URL (uses settings if not provided)
            api_key: Optional API key (uses settings if not provided)
        """
        settings = get_settings()
        self.url = url or settings.qdrant_url
        self.api_key = api_key or settings.qdrant_api_key
        self.collection_name = settings.qdrant_collection_name
        self.embedding_dim = settings.embedding_dimension

        logger.info(f"Connecting to Qdrant at {self.url}")
        self.client = QdrantClient(url=self.url, api_key=self.api_key)
        logger.success("Connected to Qdrant successfully")

    def check_connection(self) -> bool:
        """
        Check if Qdrant is accessible.

        Returns:
            True if connection is successful
        """
        try:
            collections = self.client.get_collections()
            logger.debug(f"Qdrant accessible. Collections: {len(collections.collections)}")
            return True
        except Exception as e:
            logger.error(f"Failed to connect to Qdrant: {e}")
            return False

    def collection_exists(self) -> bool:
        """
        Check if the security_reports collection exists.

        Returns:
            True if collection exists
        """
        try:
            collections = self.client.get_collections().collections
            exists = any(c.name == self.collection_name for c in collections)
            logger.debug(f"Collection '{self.collection_name}' exists: {exists}")
            return exists
        except Exception as e:
            logger.error(f"Error checking collection existence: {e}")
            return False

    def create_collection(self) -> bool:
        """
        Create the security_reports collection with hybrid search config.

        Creates a collection with:
        - Dense vectors: 1024 dimensions (BGE-M3)
        - Sparse vectors: BM25/learned sparse (Qdrant native)

        Returns:
            True if collection was created successfully
        """
        if self.collection_exists():
            logger.info(f"Collection '{self.collection_name}' already exists")
            return True

        try:
            logger.info(
                f"Creating collection '{self.collection_name}' "
                f"with {self.embedding_dim} dims"
            )

            self.client.create_collection(
                collection_name=self.collection_name,
                vectors_config={
                    "dense": VectorParams(
                        size=self.embedding_dim, distance=Distance.COSINE
                    )
                },
                sparse_vectors_config={
                    "sparse": SparseVectorParams()  # BM25-like sparse indexing
                },
            )

            logger.success(f"Collection '{self.collection_name}' created successfully")
            return True

        except Exception as e:
            logger.error(f"Failed to create collection: {e}")
            return False

    def calculate_report_id(self, raw_text: str) -> str:
        """
        Calculate unique ID for a report using SHA-256 hash converted to UUID.

        Args:
            raw_text: The original raw report text

        Returns:
            UUID string derived from SHA-256 hash (first 16 bytes)
        """
        # Generate SHA-256 hash
        hash_bytes = hashlib.sha256(raw_text.encode()).digest()
        # Use first 16 bytes to create a deterministic UUID
        report_uuid = uuid.UUID(bytes=hash_bytes[:16])
        return str(report_uuid)

    def report_exists(self, report_id: str) -> bool:
        """
        Check if a report with this ID already exists.

        Args:
            report_id: The SHA-256 hash of the report

        Returns:
            True if report exists
        """
        try:
            result = self.client.retrieve(
                collection_name=self.collection_name, ids=[report_id]
            )
            exists = len(result) > 0
            if exists:
                logger.debug(f"Report {report_id[:8]}... already exists")
            return exists
        except Exception as e:
            logger.debug(f"Error checking report existence: {e}")
            return False

    def ingest_report(
        self, raw_text: str, normalized: NormalizedReport
    ) -> IngestionResult:
        """
        Ingest a normalized report into Qdrant.

        Process:
        1. Calculate unique ID (SHA-256 of raw text)
        2. Check if report already exists
        3. Generate embeddings (dense + sparse)
        4. Upsert to Qdrant with full JSON payload

        Args:
            raw_text: Original raw report text
            normalized: Normalized report structure

        Returns:
            IngestionResult with success status and message
        """
        # Step 1: Calculate ID
        report_id = self.calculate_report_id(raw_text)
        logger.info(f"Ingesting report with ID: {report_id[:16]}...")

        # Step 2: Check if exists
        if self.report_exists(report_id):
            logger.warning(f"Report {report_id[:8]}... already exists in database")
            return IngestionResult(
                success=False,
                report_id=report_id,
                message="Report already exists in database",
                already_exists=True,
            )

        try:
            # Step 3: Generate embeddings
            logger.info("Generating embeddings...")
            embedding_text, dense_vector, sparse_vector = embed_report(normalized)

            # Step 4: Prepare point
            point = PointStruct(
                id=report_id,
                vector={
                    "dense": dense_vector,
                    # Sparse vector: for now using empty, Qdrant will auto-generate BM25
                    # When FastEmbed supports BGE-M3 sparse, we'll use it
                },
                payload=normalized.model_dump(),
            )

            # Step 5: Upsert to Qdrant
            logger.info("Upserting to Qdrant...")
            self.client.upsert(
                collection_name=self.collection_name, points=[point], wait=True
            )

            logger.success(
                f"Report ingested successfully: {normalized.title[:50]}..."
            )

            return IngestionResult(
                success=True,
                report_id=report_id,
                message=f"Report ingested successfully ({report_id[:8]}...)",
                already_exists=False,
            )

        except Exception as e:
            logger.error(f"Failed to ingest report: {e}")
            return IngestionResult(
                success=False,
                report_id=report_id,
                message=f"Ingestion failed: {str(e)}",
                already_exists=False,
            )

    def get_collection_info(self) -> dict:
        """
        Get information about the collection.

        Returns:
            Dict with collection statistics
        """
        try:
            if not self.collection_exists():
                return {
                    "exists": False,
                    "message": f"Collection '{self.collection_name}' does not exist",
                }

            info = self.client.get_collection(collection_name=self.collection_name)

            # Get vectors count from config if available
            vectors_count = info.points_count  # Each point has vectors
            if hasattr(info, 'vectors_count'):
                vectors_count = info.vectors_count

            return {
                "exists": True,
                "name": self.collection_name,
                "vectors_count": vectors_count,
                "points_count": info.points_count,
                "status": info.status.value if hasattr(info.status, 'value') else str(info.status),
                "config": {
                    "vector_size": self.embedding_dim,
                    "distance": "Cosine",
                },
            }

        except Exception as e:
            logger.error(f"Failed to get collection info: {e}")
            return {"exists": False, "error": str(e)}


# Global instance (lazy-loaded)
_qdrant: Optional[QdrantDB] = None


def get_qdrant_client() -> QdrantDB:
    """
    Get the global Qdrant client instance.

    Returns:
        QdrantDB instance
    """
    global _qdrant
    if _qdrant is None:
        _qdrant = QdrantDB()
    return _qdrant


def initialize_collection() -> bool:
    """
    Initialize the Qdrant collection.

    Returns:
        True if collection exists or was created successfully
    """
    client = get_qdrant_client()
    return client.create_collection()


def ingest_report(raw_text: str, normalized: NormalizedReport) -> IngestionResult:
    """
    Convenience function to ingest a report.

    Args:
        raw_text: Original raw report text
        normalized: Normalized report structure

    Returns:
        IngestionResult
    """
    client = get_qdrant_client()
    return client.ingest_report(raw_text, normalized)


def get_collection_info() -> dict:
    """
    Convenience function to get collection info.

    Returns:
        Dict with collection statistics
    """
    client = get_qdrant_client()
    return client.get_collection_info()
