"""
Configuration management for Mnemosyne.

Loads settings from environment variables with sensible defaults.
"""

import os
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from pydantic import Field
from pydantic_settings import BaseSettings

# Load .env file if it exists
load_dotenv()

# Project root directory
PROJECT_ROOT = Path(__file__).parent.parent.parent


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # Anthropic API Configuration
    anthropic_api_key: str = Field(
        ..., description="Anthropic API key for Claude"
    )

    # Qdrant Configuration
    qdrant_url: str = Field(
        default="http://localhost:6333", description="Qdrant server URL"
    )
    qdrant_api_key: Optional[str] = Field(
        default=None, description="Qdrant API key (optional)"
    )
    qdrant_collection_name: str = Field(
        default="security_reports", description="Name of the Qdrant collection"
    )

    # Model Configuration
    embedding_model: str = Field(
        default="BAAI/bge-large-en-v1.5",
        description="FastEmbed model for dense embeddings (1024 dims)",
    )
    embedding_dimension: int = Field(
        default=1024, description="Dimension of dense embedding vectors (BGE-M3)"
    )
    rerank_model: str = Field(
        default="ms-marco-TinyBERT-L-2-v2",
        description="FlashRank model for re-ranking",
    )

    # Search Configuration
    dense_weight: float = Field(
        default=0.7, description="Weight for dense vector search", ge=0.0, le=1.0
    )
    sparse_weight: float = Field(
        default=0.3, description="Weight for sparse (BM25) search", ge=0.0, le=1.0
    )
    top_k_candidates: int = Field(
        default=20, description="Number of candidates to retrieve before re-ranking"
    )
    rerank_top_k: int = Field(
        default=10, description="Number of candidates to re-rank"
    )
    final_top_k: int = Field(
        default=5, description="Number of final results to return"
    )

    # Duplicate Detection Thresholds
    duplicate_threshold: float = Field(
        default=0.85, description="Score threshold for duplicates (🔴)", ge=0.0, le=1.0
    )
    similar_threshold: float = Field(
        default=0.65, description="Score threshold for similar reports (🟡)", ge=0.0, le=1.0
    )

    # Report Processing
    max_tokens: int = Field(
        default=180000, description="Maximum tokens before truncation"
    )
    token_buffer: int = Field(
        default=20000, description="Buffer to keep below context window limit"
    )

    # ReAct Agent Configuration
    react_max_iterations: int = Field(
        default=5, description="Maximum iterations for ReAct agent"
    )
    react_verbose: bool = Field(
        default=False, description="Enable verbose agent logging"
    )

    # Logging Configuration
    log_level: str = Field(
        default="INFO", description="Logging level (DEBUG, INFO, WARNING, ERROR)"
    )
    log_file: Optional[str] = Field(
        default=None, description="Path to log file (None = no file logging)"
    )

    # Batch Processing
    batch_concurrency: int = Field(
        default=3, description="Number of reports to process concurrently"
    )
    batch_delay: float = Field(
        default=1.0, description="Delay between batches in seconds"
    )

    # Claude System Prompt Paths
    prompts_dir: Path = Field(
        default=PROJECT_ROOT / "prompts",
        description="Directory containing system prompts",
    )
    normalization_prompt_path: Path = Field(
        default=PROJECT_ROOT / "prompts" / "normalization.md",
        description="Path to the normalization system prompt",
    )
    react_agent_prompt_path: Path = Field(
        default=PROJECT_ROOT / "prompts" / "react_agent.md",
        description="Path to the ReAct agent system prompt",
    )
    similarity_prompt_path: Path = Field(
        default=PROJECT_ROOT / "prompts" / "similarity_analysis.md",
        description="Path to the similarity analysis system prompt",
    )

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = False
        extra = "ignore"  # Ignore extra fields in .env


# Global settings instance
_settings: Optional[Settings] = None


def get_settings() -> Settings:
    """
    Get the global settings instance.

    Returns:
        Settings: The application settings.

    Raises:
        ValueError: If required environment variables are missing.
    """
    global _settings
    if _settings is None:
        try:
            _settings = Settings()
        except Exception as e:
            raise ValueError(
                f"Failed to load settings. Make sure you have a .env file with required variables. Error: {e}"
            ) from e
    return _settings


def reload_settings() -> Settings:
    """
    Reload settings from environment variables.

    Useful for testing or when environment variables change.

    Returns:
        Settings: The reloaded settings.
    """
    global _settings
    _settings = None
    return get_settings()
