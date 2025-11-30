"""
Pydantic models for normalized security reports.

This module defines the canonical data structure for bug bounty reports
after normalization by Claude Sonnet 4.5.
"""

from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


class SeverityLevel(str, Enum):
    """Security severity levels following industry standards."""

    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


class ArtifactType(str, Enum):
    """Types of technical artifacts found in security reports."""

    PAYLOAD = "payload"
    REQUEST = "request"
    RESPONSE = "response"
    CODE = "code"
    EXPLOIT = "exploit"
    LOG = "log"
    OTHER = "other"


class TechnicalArtifact(BaseModel):
    """
    Represents a technical artifact (code, payload, request, etc.) from a report.

    CRITICAL: The content field must NEVER be modified, summarized, or truncated.
    This is essential for exact matching in sparse search.
    """

    type: ArtifactType = Field(
        description="Type of artifact (payload, request, exploit, etc.)"
    )
    language: Optional[str] = Field(
        default=None,
        description="Programming/markup language (python, javascript, http, graphql, etc.)",
    )
    content: str = Field(
        description="The complete, unmodified content of the artifact"
    )
    description: str = Field(
        description="Brief explanation of what this artifact does or represents"
    )

    class Config:
        use_enum_values = True


class ReportMetadata(BaseModel):
    """Additional metadata extracted from the report."""

    cves: list[str] = Field(
        default_factory=list, description="CVE identifiers if mentioned"
    )
    references: list[str] = Field(
        default_factory=list, description="External URLs or references"
    )
    hunter_notes: str = Field(
        default="", description="Additional notes from the security researcher"
    )
    custom: dict[str, Any] = Field(
        default_factory=dict,
        description="Flexible field for any additional metadata",
    )


class NormalizedReport(BaseModel):
    """
    Canonical structure for a normalized security report.

    This model represents the output of Claude's normalization process
    and is used for both storage in Qdrant and duplicate detection.
    """

    title: str = Field(description="Short, descriptive title of the vulnerability")
    summary: str = Field(
        description="Executive summary (2-3 sentences) explaining what the vulnerability is and its impact"
    )
    vulnerability_type: str = Field(
        description="Category using OWASP/CWE nomenclature (e.g., SQL Injection, XSS, SSRF)"
    )
    severity: SeverityLevel = Field(
        description="Security severity level (critical, high, medium, low, info)"
    )
    affected_component: str = Field(
        description="Specific component, endpoint, or module affected (e.g., /api/graphql, auth service)"
    )
    reproduction_steps: list[str] = Field(
        description="Ordered list of actionable steps to reproduce the vulnerability"
    )
    technical_artifacts: list[TechnicalArtifact] = Field(
        default_factory=list,
        description="Code snippets, payloads, requests, exploits, etc.",
    )
    technologies: list[str] = Field(
        default_factory=list,
        description="Technologies mentioned (frameworks, languages, databases, etc.)",
    )
    impact: str = Field(
        description="Description of the business/security impact of this vulnerability"
    )
    remediation: str = Field(
        default="",
        description="Recommended fix or mitigation (if available in the report)",
    )
    metadata: ReportMetadata = Field(
        default_factory=ReportMetadata, description="Additional metadata and notes"
    )

    class Config:
        use_enum_values = True


class DuplicateDetectionResult(BaseModel):
    """Result of a duplicate detection scan."""

    is_duplicate: bool = Field(
        description="Whether this report is considered a duplicate"
    )
    similarity_score: float = Field(
        description="Similarity score from re-ranking (0.0 to 1.0)", ge=0.0, le=1.0
    )
    matched_report: Optional[NormalizedReport] = Field(
        default=None, description="The most similar report found (if any)"
    )
    matched_report_id: Optional[str] = Field(
        default=None, description="ID (hash) of the matched report"
    )
    status: str = Field(
        description="Status: 'duplicate' (🔴), 'similar' (🟡), or 'new' (🟢)"
    )
    candidates: list[dict[str, Any]] = Field(
        default_factory=list,
        description="List of all candidates considered (with scores)",
    )


class SearchResult(BaseModel):
    """Result from hybrid search in Qdrant."""

    report_id: str = Field(description="Unique ID (SHA-256 hash) of the report")
    score: float = Field(description="Initial similarity score from vector search")
    report: NormalizedReport = Field(description="The normalized report data")


class IngestionResult(BaseModel):
    """Result of ingesting a report into the database."""

    success: bool = Field(description="Whether ingestion was successful")
    report_id: str = Field(description="Unique ID (SHA-256 hash) of the report")
    message: str = Field(description="Status message or error description")
    already_exists: bool = Field(
        default=False, description="Whether this report was already in the database"
    )
