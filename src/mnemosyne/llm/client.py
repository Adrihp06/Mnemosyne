"""
Claude API client for report normalization.

Uses Claude Sonnet 4.5 with Prompt Caching to efficiently normalize
security reports into structured JSON.
"""

from pathlib import Path
from typing import Optional

from anthropic import Anthropic
from loguru import logger
from pydantic import ValidationError

from mnemosyne.config import get_settings
from mnemosyne.core.truncation import truncate_report
from mnemosyne.models.schema import NormalizedReport


class ClaudeClient:
    """
    Client for interacting with Claude API for report normalization.

    Features:
    - Prompt Caching to reduce costs
    - Structured output using Tool Use
    - Automatic truncation for long reports
    """

    MODEL = "claude-sonnet-4-5-20250929"

    def __init__(self, api_key: Optional[str] = None):
        """
        Initialize the Claude client.

        Args:
            api_key: Optional Anthropic API key (uses settings if not provided)
        """
        settings = get_settings()
        self.api_key = api_key or settings.anthropic_api_key
        self.client = Anthropic(api_key=self.api_key)
        self.system_prompt = self._load_system_prompt(settings.normalization_prompt_path)
        self.max_tokens = settings.max_tokens

        logger.info(f"Initialized Claude client with model: {self.MODEL}")

    def _load_system_prompt(self, prompt_path: Path) -> str:
        """
        Load the system prompt from claude.md file.

        Args:
            prompt_path: Path to the claude.md file

        Returns:
            System prompt content

        Raises:
            FileNotFoundError: If claude.md doesn't exist
        """
        if not prompt_path.exists():
            raise FileNotFoundError(
                f"System prompt file not found: {prompt_path}. "
                "Make sure claude.md exists in the project root."
            )

        with open(prompt_path, "r", encoding="utf-8") as f:
            content = f.read()

        logger.debug(f"Loaded system prompt from {prompt_path} ({len(content)} chars)")
        return content

    def _create_normalization_tool(self) -> dict:
        """
        Create the Tool Use schema for normalized reports.

        This forces Claude to return structured JSON matching our NormalizedReport model.

        Returns:
            Tool definition dict
        """
        return {
            "name": "submit_normalized_report",
            "description": "Submit the normalized security report in structured format",
            "input_schema": {
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "Short, descriptive title of the vulnerability",
                    },
                    "summary": {
                        "type": "string",
                        "description": "Executive summary (2-3 sentences) explaining the vulnerability and impact",
                    },
                    "vulnerability_type": {
                        "type": "string",
                        "description": "Category using OWASP/CWE nomenclature (e.g., SQL Injection, XSS, SSRF)",
                    },
                    "severity": {
                        "type": "string",
                        "enum": ["critical", "high", "medium", "low", "info"],
                        "description": "Security severity level",
                    },
                    "affected_component": {
                        "type": "string",
                        "description": "Specific component, endpoint, or module affected",
                    },
                    "reproduction_steps": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Ordered list of actionable steps to reproduce",
                    },
                    "technical_artifacts": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "type": {
                                    "type": "string",
                                    "enum": [
                                        "payload",
                                        "request",
                                        "response",
                                        "code",
                                        "exploit",
                                        "log",
                                        "other",
                                    ],
                                },
                                "language": {
                                    "type": "string",
                                    "description": "Programming/markup language",
                                },
                                "content": {
                                    "type": "string",
                                    "description": "Complete, unmodified artifact content",
                                },
                                "description": {
                                    "type": "string",
                                    "description": "Brief explanation of this artifact",
                                },
                            },
                            "required": ["type", "content", "description"],
                        },
                        "description": "Code snippets, payloads, requests, exploits",
                    },
                    "technologies": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Technologies mentioned (frameworks, languages, databases)",
                    },
                    "impact": {
                        "type": "string",
                        "description": "Description of the business/security impact",
                    },
                    "remediation": {
                        "type": "string",
                        "description": "Recommended fix or mitigation",
                    },
                    "metadata": {
                        "type": "object",
                        "properties": {
                            "cves": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "CVE identifiers if mentioned",
                            },
                            "references": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "External URLs or references",
                            },
                            "hunter_notes": {
                                "type": "string",
                                "description": "Additional notes from the researcher",
                            },
                        },
                    },
                },
                "required": [
                    "title",
                    "summary",
                    "vulnerability_type",
                    "severity",
                    "affected_component",
                    "reproduction_steps",
                    "impact",
                ],
            },
        }

    def normalize_report(
        self, raw_text: str, apply_truncation: bool = True
    ) -> NormalizedReport:
        """
        Normalize a raw security report into structured format.

        This is the main function that:
        1. Optionally truncates long reports
        2. Calls Claude with prompt caching
        3. Parses the response into NormalizedReport

        Args:
            raw_text: The raw report text (markdown, txt, etc.)
            apply_truncation: Whether to apply truncation for long reports

        Returns:
            NormalizedReport: The normalized report

        Raises:
            ValueError: If normalization fails or API returns invalid data
        """
        # Step 1: Truncate if needed
        processed_text = raw_text
        was_truncated = False

        if apply_truncation:
            processed_text, was_truncated = truncate_report(
                raw_text, max_tokens=self.max_tokens
            )
            if was_truncated:
                logger.warning("Report was truncated due to length")

        # Step 2: Call Claude API with prompt caching
        logger.info("Calling Claude API for normalization...")

        try:
            response = self.client.messages.create(
                model=self.MODEL,
                max_tokens=4096,
                system=[
                    {
                        "type": "text",
                        "text": self.system_prompt,
                        # Enable prompt caching on the system prompt
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
                messages=[
                    {
                        "role": "user",
                        "content": f"Please normalize the following security report:\n\n{processed_text}",
                    }
                ],
                tools=[self._create_normalization_tool()],
                tool_choice={"type": "tool", "name": "submit_normalized_report"},
            )

            # Log cache performance
            usage = response.usage
            logger.info(
                f"API call complete. Tokens: input={usage.input_tokens}, "
                f"output={usage.output_tokens}"
            )

            # Check for cache hits
            if hasattr(usage, "cache_creation_input_tokens"):
                logger.info(
                    f"Cache performance: "
                    f"created={usage.cache_creation_input_tokens}, "
                    f"read={getattr(usage, 'cache_read_input_tokens', 0)}"
                )

            # Step 3: Extract tool use from response
            if not response.content:
                raise ValueError("Empty response from Claude")

            tool_use = None
            for block in response.content:
                if block.type == "tool_use":
                    tool_use = block
                    break

            if not tool_use:
                raise ValueError(
                    "No tool use found in response. Claude did not return structured data."
                )

            # Step 4: Parse into Pydantic model
            try:
                normalized = NormalizedReport(**tool_use.input)
                logger.success("Report normalized successfully")

                # Add note if it was truncated
                if was_truncated:
                    normalized.metadata.hunter_notes = (
                        f"[Note: Original report was truncated due to length] "
                        f"{normalized.metadata.hunter_notes}"
                    )

                return normalized

            except ValidationError as e:
                logger.error(f"Failed to parse Claude's response into NormalizedReport: {e}")
                raise ValueError(f"Invalid response structure from Claude: {e}") from e

        except Exception as e:
            logger.error(f"Normalization failed: {e}")
            raise


def normalize_report(raw_text: str) -> NormalizedReport:
    """
    Convenience function to normalize a report using default settings.

    Args:
        raw_text: The raw report text

    Returns:
        NormalizedReport: The normalized report
    """
    client = ClaudeClient()
    return client.normalize_report(raw_text)
