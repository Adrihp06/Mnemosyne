"""
ReAct agent for iterative duplicate detection.

Uses Claude Sonnet 4.5 with tool calling to search for duplicates
through multiple iterations of reasoning and searching.
"""

import json
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from anthropic import Anthropic
from loguru import logger

from mnemosyne.config import get_settings
from mnemosyne.core.search import get_searcher
from mnemosyne.models.schema import (
    DuplicateDetectionResult,
    NormalizedReport,
    SearchResult,
)


class ReactAgent:
    """
    ReAct (Reasoning + Acting) agent for duplicate detection.

    Uses Claude with tool calling to iteratively search for duplicates
    using different strategies until reaching a conclusive answer.
    """

    def __init__(self, api_key: Optional[str] = None):
        """
        Initialize the ReAct agent.

        Args:
            api_key: Optional Anthropic API key (uses settings if not provided)
        """
        settings = get_settings()
        self.api_key = api_key or settings.anthropic_api_key
        self.client = Anthropic(api_key=self.api_key)
        self.model = "claude-sonnet-4-5-20250929"
        self.max_iterations = settings.react_max_iterations
        self.searcher = get_searcher()

        # Load system prompt
        prompt_path = Path(__file__).parent.parent.parent.parent / "prompts" / "react_agent.md"
        with open(prompt_path, "r", encoding="utf-8") as f:
            self.system_prompt = f.read()

        logger.info(f"ReactAgent initialized with model: {self.model}")

    def _build_tool_definition(self) -> Dict:
        """
        Build the tool definition for hybrid_search.

        Returns:
            Tool definition dict for Claude API
        """
        return {
            "name": "hybrid_search",
            "description": (
                "Search for similar security reports using hybrid vector search "
                "(dense semantic + sparse BM25). Returns a list of candidate reports "
                "with similarity scores."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": (
                            "Search query text. Can be vulnerability description, "
                            "payloads, code snippets, component names, etc."
                        ),
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of results to return",
                        "default": 20,
                    },
                },
                "required": ["query"],
            },
        }

    def _execute_tool(self, tool_name: str, tool_input: Dict) -> Tuple[str, Optional[DuplicateDetectionResult]]:
        """
        Execute a tool and return the observation.

        Args:
            tool_name: Name of the tool to execute
            tool_input: Input parameters for the tool

        Returns:
            Tuple containing:
            - Formatted observation string
            - Optional DuplicateDetectionResult (if early stopping condition met)
        """
        if tool_name == "hybrid_search":
            query = tool_input.get("query", "")
            limit = tool_input.get("limit", 20)

            logger.info(f"Executing hybrid_search: '{query[:100]}...' (limit={limit})")

            # Perform hybrid search + re-ranking
            results = self.searcher.hybrid_search(query_text=query, limit=limit)

            # Re-rank top results
            if results:
                reranked = self.searcher.rerank_results(
                    query=query,
                    candidates=results,
                    top_k=min(10, len(results))  # Re-rank top 10
                )
            else:
                reranked = []

            # Format observation for Claude
            if not reranked:
                return "No results found.", None

            # Check for early stopping (high confidence match)
            top_result = reranked[0]
            if top_result.score > 0.9:
                logger.success(f"Early stopping: Found high confidence match (score={top_result.score:.3f})")
                early_result = DuplicateDetectionResult(
                    is_duplicate=True,
                    similarity_score=top_result.score,
                    matched_report_id=top_result.report_id,
                    matched_report=top_result.report,
                    status="duplicate",
                    candidates=[c.model_dump() for c in reranked],
                )
                return f"Found high confidence match: {top_result.report.title}", early_result

            observation = f"Found {len(reranked)} candidates:\n\n"
            for i, result in enumerate(reranked[:10], 1):  # Show top 10
                report = result.report
                observation += (
                    f"{i}. [Score: {result.score:.3f}] {report.title}\n"
                    f"   Type: {report.vulnerability_type}\n"
                    f"   Component: {report.affected_component}\n"
                    f"   Summary: {report.summary[:150]}...\n"
                    f"   Report ID: {result.report_id}\n\n"
                )

            return observation, None

        else:
            logger.error(f"Unknown tool: {tool_name}")
            return f"Error: Unknown tool '{tool_name}'", None

    def search_duplicates(
        self, new_report: NormalizedReport, raw_text: str
    ) -> DuplicateDetectionResult:
        """
        Search for duplicates of a new report using iterative ReAct pattern.

        Args:
            new_report: The normalized report to check for duplicates
            raw_text: Original raw text of the report

        Returns:
            DuplicateDetectionResult with detection outcome
        """
        logger.info(f"Starting duplicate detection for: {new_report.title[:50]}...")

        # Build initial message with report details
        initial_message = self._format_report_for_agent(new_report)

        # Conversation history for ReAct loop
        messages = [{"role": "user", "content": initial_message}]

        # Track all candidates found
        all_candidates: List[SearchResult] = []
        iterations = 0

        try:
            while iterations < self.max_iterations:
                iterations += 1
                logger.info(f"ReAct iteration {iterations}/{self.max_iterations}")

                # Call Claude with tool use
                response = self.client.messages.create(
                    model=self.model,
                    system=[
                        {
                            "type": "text",
                            "text": self.system_prompt,
                            "cache_control": {"type": "ephemeral"}  # Cache system prompt
                        }
                    ],
                    messages=messages,
                    tools=[self._build_tool_definition()],
                    max_tokens=4096,
                )

                logger.info(
                    f"API response - stop_reason: {response.stop_reason}, "
                    f"usage: {response.usage.model_dump()}"
                )

                # Process response
                assistant_message = {"role": "assistant", "content": response.content}
                messages.append(assistant_message)

                # Check if we have a tool use
                tool_use = None
                for block in response.content:
                    if block.type == "tool_use":
                        tool_use = block
                        break

                if tool_use:
                    # Execute the tool
                    observation, early_result = self._execute_tool(
                        tool_name=tool_use.name,
                        tool_input=tool_use.input
                    )

                    # Check for early stopping
                    if early_result:
                        logger.info("Early stopping triggered by tool execution")
                        return early_result

                    # Add tool result to conversation
                    messages.append({
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": tool_use.id,
                                "content": observation
                            }
                        ]
                    })

                elif response.stop_reason == "end_turn":
                    # Agent has finished - extract final answer
                    logger.info("Agent reached final answer")

                    # Extract text from response
                    final_text = ""
                    for block in response.content:
                        if block.type == "text":
                            final_text += block.text

                    # Parse final answer
                    result = self._parse_final_answer(final_text, new_report)
                    return result

                else:
                    logger.warning(f"Unexpected stop_reason: {response.stop_reason}")
                    break

            # Max iterations reached without final answer
            logger.warning(
                f"Max iterations ({self.max_iterations}) reached without conclusive answer"
            )

            return DuplicateDetectionResult(
                is_duplicate=False,
                similarity_score=0.0,
                matched_report=None,
                matched_report_id=None,
                status="similar",
                candidates=[],
            )

        except Exception as e:
            logger.error(f"ReAct agent failed: {e}")
            raise

    def _format_report_for_agent(self, report: NormalizedReport) -> str:
        """
        Format a normalized report for the agent's initial message.

        Args:
            report: The normalized report to format

        Returns:
            Formatted string for Claude
        """
        # Extract payloads if present
        payloads_text = ""
        if report.technical_artifacts:
            payloads_text = "\n\nTechnical Artifacts:\n"
            for i, artifact in enumerate(report.technical_artifacts[:3], 1):
                payloads_text += (
                    f"{i}. [{artifact.type}] {artifact.description}\n"
                    f"   Content: {artifact.content[:200]}...\n"
                )

        message = f"""Analiza este nuevo reporte de seguridad y determina si es un duplicado de reportes existentes en la base de datos.

**Nuevo Reporte:**

Título: {report.title}
Tipo de Vulnerabilidad: {report.vulnerability_type}
Severidad: {report.severity}
Componente Afectado: {report.affected_component}

Resumen:
{report.summary}

Pasos de Reproducción:
{self._format_steps(report.reproduction_steps)}

Tecnologías: {', '.join(report.technologies) if report.technologies else 'N/A'}

Impacto:
{report.impact}
{payloads_text}

---

Usa la herramienta `hybrid_search` para buscar reportes similares. Realiza múltiples búsquedas desde diferentes ángulos (descripción, componentes, payloads) antes de llegar a una conclusión.

Recuerda los criterios:
- **Duplicate** (score > 0.85): Mismo tipo + componente + payloads
- **Similar** (0.65-0.85): Relacionado pero diferente contexto
- **New** (< 0.65): Sin coincidencias relevantes

Comienza tu análisis."""

        return message

    def _format_steps(self, steps: List[str]) -> str:
        """Format reproduction steps as numbered list."""
        return "\n".join(f"{i}. {step}" for i, step in enumerate(steps, 1))

    def _parse_final_answer(
        self, response_text: str, new_report: NormalizedReport
    ) -> DuplicateDetectionResult:
        """
        Parse the agent's final answer into a DuplicateDetectionResult.

        Args:
            response_text: The agent's final text response
            new_report: The original report being analyzed

        Returns:
            DuplicateDetectionResult object
        """
        logger.info("Parsing final answer from agent")

        try:
            # Try to extract JSON from the response
            # Look for JSON between ```json and ``` or just raw JSON
            json_start = response_text.find("{")
            json_end = response_text.rfind("}") + 1

            if json_start >= 0 and json_end > json_start:
                json_str = response_text[json_start:json_end]
                answer = json.loads(json_str)

                # Extract fields
                is_duplicate = answer.get("is_duplicate", False)
                similarity_score = answer.get("similarity_score", 0.0)
                matched_id = answer.get("matched_report_id")
                status = answer.get("status", "new")

                logger.success(
                    f"Decision: {status.upper()} (is_duplicate={is_duplicate}, "
                    f"score={similarity_score:.3f})"
                )

                return DuplicateDetectionResult(
                    is_duplicate=is_duplicate,
                    similarity_score=similarity_score,
                    matched_report_id=matched_id,
                    matched_report=None,  # Will be populated by caller if needed
                    status=status,
                    candidates=[],  # Could be populated with search history
                )

            else:
                logger.warning("No JSON found in final answer, using default")
                return DuplicateDetectionResult(
                    is_duplicate=False,
                    similarity_score=0.0,
                    matched_report=None,
                    matched_report_id=None,
                    status="new",
                    candidates=[],
                )

        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse JSON from final answer: {e}")
            logger.debug(f"Response text: {response_text[:500]}")

            # Fallback: return safe default
            return DuplicateDetectionResult(
                is_duplicate=False,
                similarity_score=0.0,
                matched_report=None,
                matched_report_id=None,
                status="new",
                candidates=[],
            )


# Global instance (lazy-loaded)
_agent: Optional[ReactAgent] = None


def get_agent() -> ReactAgent:
    """
    Get the global ReactAgent instance.

    Returns:
        ReactAgent instance
    """
    global _agent
    if _agent is None:
        _agent = ReactAgent()
    return _agent


def detect_duplicates(
    new_report: NormalizedReport, raw_text: str
) -> DuplicateDetectionResult:
    """
    Convenience function to detect duplicates using the ReAct agent.

    Args:
        new_report: The normalized report to check
        raw_text: Original raw text of the report

    Returns:
        DuplicateDetectionResult with detection outcome
    """
    agent = get_agent()
    return agent.search_duplicates(new_report, raw_text)
