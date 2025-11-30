"""
Intelligent truncation for long security reports.

Implements section prioritization strategy to handle reports that exceed
Claude's context window (~200k tokens).
"""

import re
from dataclasses import dataclass
from typing import Optional

from loguru import logger


@dataclass
class Section:
    """Represents a section of a report."""

    name: str
    content: str
    priority: int  # Lower number = higher priority (1 = highest)
    preserve_complete: bool  # If True, never truncate this section


class ReportTruncator:
    """
    Truncates long reports using section prioritization.

    Strategy:
    1. Identify sections by markdown headers or common patterns
    2. Prioritize critical sections (title, summary, steps, payloads)
    3. Truncate low-priority sections first (logs, metadata, context)
    4. Never truncate technical artifacts (code/payloads)
    """

    # Section patterns and their priorities
    SECTION_PATTERNS = {
        # Priority 1: Critical - NEVER truncate
        r"^#+ ?(title|vulnerability|summary|description|overview)": (
            "Title/Summary",
            1,
            True,
        ),
        r"^#+ ?(reproduction|steps|reproduce|how to|poc|proof)": (
            "Reproduction Steps",
            1,
            True,
        ),
        r"^#+ ?(payload|exploit|code|request|response|technical)": (
            "Technical Artifacts",
            1,
            True,
        ),
        # Priority 2: Important - Preserve if possible
        r"^#+ ?(impact|severity|affected|vulnerable)": ("Impact/Severity", 2, False),
        r"^#+ ?(remediation|fix|mitigation|recommendation)": (
            "Remediation",
            2,
            False,
        ),
        r"^#+ ?(affected|technologies|stack|environment)": (
            "Technologies",
            2,
            False,
        ),
        # Priority 3: Context - Can be summarized
        r"^#+ ?(background|context|introduction|about)": ("Background", 3, False),
        r"^#+ ?(timeline|discovery|disclosure)": ("Timeline", 3, False),
        # Priority 4: Expendable - Truncate first
        r"^#+ ?(logs?|output|debug|trace)": ("Logs", 4, False),
        r"^#+ ?(screenshot|image|video|attachment)": ("Media", 4, False),
        r"^#+ ?(metadata|info|details|additional)": ("Metadata", 4, False),
    }

    def __init__(self, max_tokens: int = 180000, token_buffer: int = 20000):
        """
        Initialize the truncator.

        Args:
            max_tokens: Maximum tokens before truncation (default: 180k)
            token_buffer: Safety buffer below context window (default: 20k)
        """
        self.max_tokens = max_tokens
        self.token_buffer = token_buffer
        self.effective_max = max_tokens - token_buffer

    def estimate_tokens(self, text: str) -> int:
        """
        Estimate token count using a simple heuristic.

        Claude's tokenizer is ~4 characters per token on average.
        This is a rough estimate - actual count may vary.

        Args:
            text: The text to estimate

        Returns:
            Estimated token count
        """
        return len(text) // 4

    def identify_sections(self, text: str) -> list[Section]:
        """
        Split report into sections and assign priorities.

        Args:
            text: The raw report text

        Returns:
            List of Section objects
        """
        sections = []
        lines = text.split("\n")
        current_section = None
        current_content = []

        for line in lines:
            # Check if this line is a header
            is_header = False
            section_info = None

            for pattern, (name, priority, preserve) in self.SECTION_PATTERNS.items():
                if re.match(pattern, line.strip(), re.IGNORECASE):
                    is_header = True
                    section_info = (name, priority, preserve)
                    break

            if is_header and section_info:
                # Save previous section if exists
                if current_section:
                    sections.append(
                        Section(
                            name=current_section[0],
                            content="\n".join(current_content),
                            priority=current_section[1],
                            preserve_complete=current_section[2],
                        )
                    )

                # Start new section
                current_section = section_info
                current_content = [line]
            else:
                # Add to current section or create default section
                if current_section is None:
                    current_section = ("Introduction", 3, False)
                    current_content = [line]
                else:
                    current_content.append(line)

        # Add final section
        if current_section:
            sections.append(
                Section(
                    name=current_section[0],
                    content="\n".join(current_content),
                    priority=current_section[1],
                    preserve_complete=current_section[2],
                )
            )

        return sections

    def preserve_code_blocks(self, text: str) -> tuple[str, list[str]]:
        """
        Extract code blocks to preserve them completely.

        Args:
            text: Text containing code blocks

        Returns:
            Tuple of (text with placeholders, list of code blocks)
        """
        code_blocks = []
        placeholder_pattern = "<<<CODE_BLOCK_{}>>>"

        # Find markdown code blocks
        def replace_code(match):
            code_blocks.append(match.group(0))
            return placeholder_pattern.format(len(code_blocks) - 1)

        # Match ```...``` blocks
        text = re.sub(
            r"```[\s\S]*?```", replace_code, text, flags=re.MULTILINE
        )

        return text, code_blocks

    def restore_code_blocks(self, text: str, code_blocks: list[str]) -> str:
        """
        Restore code blocks after truncation.

        Args:
            text: Text with placeholders
            code_blocks: List of code blocks to restore

        Returns:
            Text with code blocks restored
        """
        for i, block in enumerate(code_blocks):
            text = text.replace(f"<<<CODE_BLOCK_{i}>>>", block)
        return text

    def truncate_section(self, section: Section, target_tokens: int) -> str:
        """
        Truncate a single section to target token count.

        Args:
            section: The section to truncate
            target_tokens: Target token count

        Returns:
            Truncated section content
        """
        # Preserve code blocks
        content, code_blocks = self.preserve_code_blocks(section.content)

        estimated = self.estimate_tokens(content)

        if estimated <= target_tokens:
            # No truncation needed
            return self.restore_code_blocks(content, code_blocks)

        # Calculate how much to keep (keep first and last portions)
        lines = content.split("\n")
        keep_ratio = target_tokens / estimated
        keep_lines = int(len(lines) * keep_ratio)

        if keep_lines < 10:
            keep_lines = min(10, len(lines))

        # Keep first 70% and last 30% of the kept lines
        first_count = int(keep_lines * 0.7)
        last_count = keep_lines - first_count

        truncated_lines = (
            lines[:first_count]
            + [f"\n... [truncated {len(lines) - keep_lines} lines] ...\n"]
            + lines[-last_count:]
        )

        truncated = "\n".join(truncated_lines)
        return self.restore_code_blocks(truncated, code_blocks)

    def truncate(self, text: str) -> tuple[str, bool]:
        """
        Truncate report if it exceeds token limits.

        Strategy:
        1. Parse into sections with priorities
        2. Calculate total tokens
        3. If over limit, truncate low-priority sections first
        4. Always preserve code blocks and critical sections

        Args:
            text: The raw report text

        Returns:
            Tuple of (truncated text, was_truncated bool)
        """
        estimated_tokens = self.estimate_tokens(text)

        if estimated_tokens <= self.effective_max:
            logger.debug(
                f"Report size OK: {estimated_tokens:,} tokens (limit: {self.effective_max:,})"
            )
            return text, False

        logger.warning(
            f"Report exceeds limit: {estimated_tokens:,} tokens > {self.effective_max:,}. Applying truncation..."
        )

        # Parse into sections
        sections = self.identify_sections(text)

        if not sections:
            # No sections found, use simple truncation
            logger.warning("No sections detected, using simple truncation")
            target_chars = self.effective_max * 4  # Rough conversion
            return text[:target_chars] + "\n\n... [truncated]", True

        # Sort by priority (preserve critical sections)
        sections.sort(key=lambda s: (s.priority, -self.estimate_tokens(s.content)))

        # Calculate tokens per section
        section_tokens = [
            (s, self.estimate_tokens(s.content)) for s in sections
        ]

        # Start with all preserved sections
        preserved_sections = [
            (s, tokens) for s, tokens in section_tokens if s.preserve_complete
        ]
        preserved_total = sum(tokens for _, tokens in preserved_sections)

        logger.info(
            f"Preserving {len(preserved_sections)} critical sections ({preserved_total:,} tokens)"
        )

        # Allocate remaining budget to other sections
        remaining_budget = self.effective_max - preserved_total
        other_sections = [
            (s, tokens) for s, tokens in section_tokens if not s.preserve_complete
        ]

        truncated_sections = []

        for section, tokens in other_sections:
            if remaining_budget <= 0:
                logger.debug(f"Skipping section '{section.name}' (no budget left)")
                continue

            if tokens <= remaining_budget:
                # Can keep full section
                truncated_sections.append((section, section.content))
                remaining_budget -= tokens
                logger.debug(
                    f"Keeping full section '{section.name}' ({tokens:,} tokens)"
                )
            else:
                # Need to truncate this section
                truncated_content = self.truncate_section(section, remaining_budget)
                truncated_tokens = self.estimate_tokens(truncated_content)
                truncated_sections.append((section, truncated_content))
                remaining_budget -= truncated_tokens
                logger.debug(
                    f"Truncated section '{section.name}' ({tokens:,} -> {truncated_tokens:,} tokens)"
                )

        # Combine all sections
        all_sections = [(s, s.content) for s, _ in preserved_sections] + truncated_sections
        # Sort back to original order (roughly)
        all_sections.sort(key=lambda x: x[0].priority)

        final_text = "\n\n".join(content for _, content in all_sections)

        final_tokens = self.estimate_tokens(final_text)
        logger.info(
            f"Truncation complete: {estimated_tokens:,} -> {final_tokens:,} tokens"
        )

        return final_text, True


def truncate_report(
    text: str, max_tokens: Optional[int] = None
) -> tuple[str, bool]:
    """
    Convenience function to truncate a report.

    Args:
        text: The report text
        max_tokens: Optional custom token limit

    Returns:
        Tuple of (truncated text, was_truncated bool)
    """
    truncator = ReportTruncator(max_tokens=max_tokens) if max_tokens else ReportTruncator()
    return truncator.truncate(text)
