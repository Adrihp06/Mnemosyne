"""
ReAct agent for iterative duplicate detection using Claude Agent SDK.

Uses Claude Sonnet 4.5 with Claude Agent SDK to search for duplicates
through multiple iterations of reasoning and searching, with declarative
hooks for early stopping and query validation.
"""

import asyncio
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    HookContext,
    HookInput,
    HookJSONOutput,
    HookMatcher,
    ResultMessage,
    TextBlock,
    ToolUseBlock,
    create_sdk_mcp_server,
    tool,
)
from loguru import logger

from mnemosyne.config import get_settings
from mnemosyne.core.search import get_searcher
from mnemosyne.models.schema import (
    DuplicateDetectionResult,
    NormalizedReport,
)


# ============================================================================
# GLOBAL SEARCHER INSTANCE & METADATA STORE
# ============================================================================

_searcher = None

# Store for tool metadata (SDK doesn't pass _metadata to hooks)
_last_tool_metadata: Dict[str, Any] = {}


def get_tool_searcher():
    """Lazy-load searcher for tool usage."""
    global _searcher
    if _searcher is None:
        _searcher = get_searcher()
    return _searcher


def set_last_tool_metadata(metadata: Dict[str, Any]) -> None:
    """Store metadata from the last tool execution for hooks to access."""
    global _last_tool_metadata
    _last_tool_metadata = metadata


def get_last_tool_metadata() -> Dict[str, Any]:
    """Retrieve metadata from the last tool execution."""
    return _last_tool_metadata


# ============================================================================
# TOOL DEFINITION: hybrid_search
# ============================================================================


@tool(
    "hybrid_search",
    "Search for similar security reports using hybrid vector search (dense semantic + sparse BM25). Returns a list of candidate reports with similarity scores.",
    {"query": str, "limit": int},
)
async def hybrid_search_tool(args: Dict[str, Any]) -> Dict[str, Any]:
    """
    Herramienta de búsqueda híbrida para el agente ReAct.

    Args:
        args: Dict con 'query' (str) y 'limit' (int, opcional)

    Returns:
        Dict con formato compatible con Claude Agent SDK
    """
    query = args.get("query", "")
    limit = args.get("limit", 20)

    logger.info(f"[Tool] Executing hybrid_search: '{query[:100]}...' (limit={limit})")

    try:
        searcher = get_tool_searcher()

        # Paso 1: Hybrid search
        results = searcher.hybrid_search(query_text=query, limit=limit)

        # Paso 2: Re-ranking si hay resultados
        if results:
            reranked = searcher.rerank_results(
                query=query, candidates=results, top_k=min(10, len(results))
            )
        else:
            reranked = []

        # Formatear respuesta para Claude
        if not reranked:
            # Store metadata globally for hooks (SDK doesn't pass _metadata)
            set_last_tool_metadata({
                "result_count": 0,
                "top_score": 0.0,
                "candidates": [],
            })
            return {
                "content": [{"type": "text", "text": "No results found."}],
            }

        # Construir observación detallada
        observation_lines = [f"Found {len(reranked)} candidates:\n"]

        for i, result in enumerate(reranked[:10], 1):
            report = result.report
            observation_lines.append(
                f"{i}. [Score: {result.score:.3f}] {report.title}\n"
                f"   Type: {report.vulnerability_type}\n"
                f"   Component: {report.affected_component}\n"
                f"   Summary: {report.summary[:150]}...\n"
                f"   Report ID: {result.report_id}\n"
            )

        observation = "\n".join(observation_lines)

        # Store metadata globally for hooks (SDK doesn't pass _metadata)
        metadata = {
            "result_count": len(reranked),
            "top_score": reranked[0].score,
            "top_report_id": reranked[0].report_id,
            "candidates": [
                {
                    "report_id": r.report_id,
                    "score": r.score,
                    "title": r.report.title,
                    "type": r.report.vulnerability_type,
                }
                for r in reranked
            ],
        }
        set_last_tool_metadata(metadata)

        # Return content for Claude
        return {
            "content": [{"type": "text", "text": observation}],
        }

    except Exception as e:
        logger.error(f"[Tool] hybrid_search failed: {e}")
        set_last_tool_metadata({"result_count": 0, "top_score": 0.0, "error": str(e)})
        return {
            "content": [{"type": "text", "text": f"Error during search: {str(e)}"}],
            "is_error": True,
        }


# ============================================================================
# HOOKS: PreToolUse and PostToolUse
# ============================================================================


async def query_validation_hook(
    input_data: HookInput, tool_use_id: Optional[str], context: HookContext
) -> HookJSONOutput:
    """
    Hook opcional para validar queries antes de ejecutar hybrid_search.

    Útil para:
    - Detectar queries demasiado cortas
    - Prevenir búsquedas vacías
    """
    tool_name = input_data.get("tool_name", "")

    # Solo aplicar a hybrid_search
    if tool_name != "mcp__dedup__hybrid_search":
        return {}

    tool_input = input_data.get("tool_input", {})
    query = tool_input.get("query", "")

    # Validación: query muy corta
    if len(query.strip()) < 5:
        logger.warning(
            f"[PreToolUse Hook] Query too short: '{query}' (length={len(query)})"
        )

        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": (
                    "Query is too short (< 5 characters). "
                    "Please provide a more descriptive search query."
                ),
            }
        }

    # Query válida - permitir ejecución
    logger.debug(f"[PreToolUse Hook] Query validation passed: '{query[:50]}...'")
    return {}


async def early_stopping_hook(
    input_data: HookInput, tool_use_id: Optional[str], context: HookContext
) -> HookJSONOutput:
    """
    Hook de early stopping cuando score > 0.9.

    Este hook se ejecuta DESPUÉS de que hybrid_search retorna resultados.
    Si encuentra un candidato con score > 0.9, detiene la ejecución inmediatamente.
    """
    tool_name = input_data.get("tool_name", "")

    # Solo aplicar a hybrid_search
    if tool_name != "mcp__dedup__hybrid_search":
        return {}

    # Get metadata from global store (SDK doesn't pass _metadata to hooks)
    metadata = get_last_tool_metadata()

    top_score = metadata.get("top_score", 0.0)
    top_report_id = metadata.get("top_report_id")
    result_count = metadata.get("result_count", 0)

    logger.debug(
        f"[PostToolUse Hook] Checking early stopping: "
        f"top_score={top_score:.3f}, threshold=0.9"
    )

    # Early stopping condition
    if top_score > 0.9 and top_report_id:
        logger.success(
            f"[PostToolUse Hook] EARLY STOPPING triggered! "
            f"Found high confidence match (score={top_score:.3f})"
        )

        # Construir mensaje de resultado final
        candidates = metadata.get("candidates", [])
        top_candidate = candidates[0] if candidates else {}

        final_message = f"""
High confidence duplicate detected (score: {top_score:.3f})!

Matched Report ID: {top_report_id}
Title: {top_candidate.get('title', 'N/A')}
Type: {top_candidate.get('type', 'N/A')}

This is a DUPLICATE with very high confidence. No additional searches needed.

FINAL ANSWER:
{{
  "is_duplicate": true,
  "confidence": "high",
  "matched_report_id": "{top_report_id}",
  "similarity_score": {top_score},
  "status": "duplicate",
  "reasoning": "Early stopping triggered due to high confidence match (score > 0.9)",
  "search_iterations": 1,
  "key_findings": [
    "Found exact match with score {top_score:.3f}",
    "Same vulnerability type and affected component"
  ]
}}
"""

        return {
            "continue_": False,  # DETENER ejecución
            "stopReason": "early_stopping_high_confidence_match",
            "systemMessage": final_message,
            "hookSpecificOutput": {
                "hookEventName": "PostToolUse",
                "earlyStoppingTriggered": True,
                "matchScore": top_score,
                "matchedReportId": top_report_id,
            },
        }

    # No early stopping - continuar normalmente
    return {"continue_": True}


# ============================================================================
# REACT SDK AGENT CLASS
# ============================================================================


class ReactAgent:
    """
    ReAct Agent implementado con Claude Agent SDK.

    Migración de la implementación manual de react.py usando el SDK oficial.
    """

    def __init__(self, api_key: Optional[str] = None):
        """
        Initialize the ReAct SDK agent.

        Args:
            api_key: Optional Anthropic API key (uses settings if not provided)
        """
        self.settings = get_settings()
        self.api_key = api_key or self.settings.anthropic_api_key
        self.model = "claude-sonnet-4-5-20250929"

        # Cargar system prompt
        prompt_path = (
            Path(__file__).parent.parent.parent.parent / "prompts" / "react_agent.md"
        )
        with open(prompt_path, "r", encoding="utf-8") as f:
            self.system_prompt = f.read()

        # Crear MCP server in-process con nuestra herramienta
        self.mcp_server = create_sdk_mcp_server(
            name="dedup", version="1.0.0", tools=[hybrid_search_tool]
        )

        logger.info(f"ReactAgent initialized with Claude Agent SDK")

    def _build_agent_options(self) -> ClaudeAgentOptions:
        """
        Construir opciones de configuración para ClaudeSDKClient.

        Returns:
            ClaudeAgentOptions configurado
        """
        return ClaudeAgentOptions(
            # MCP Server con nuestra herramienta
            mcp_servers={"dedup": self.mcp_server},
            # Permitir solo hybrid_search
            allowed_tools=["mcp__dedup__hybrid_search"],
            # System prompt del agente ReAct
            system_prompt=self.system_prompt,
            # Modelo
            model=self.model,
            # Límites
            max_turns=self.settings.react_max_iterations,  # 5 iteraciones
            max_budget_usd=None,  # Sin límite de presupuesto
            # Hooks
            hooks={
                "PreToolUse": [
                    HookMatcher(
                        matcher="mcp__dedup__hybrid_search",
                        hooks=[query_validation_hook],
                    )
                ],
                "PostToolUse": [
                    HookMatcher(
                        matcher="mcp__dedup__hybrid_search",
                        hooks=[early_stopping_hook],
                    )
                ],
            },
            # Permisos (no necesitamos editar archivos)
            permission_mode="default",
            # Streaming
            include_partial_messages=False,  # No necesitamos mensajes parciales
        )

    async def search_duplicates(
        self, new_report: NormalizedReport, raw_text: str
    ) -> DuplicateDetectionResult:
        """
        Search for duplicates using Claude Agent SDK with ReAct pattern.

        Args:
            new_report: The normalized report to check for duplicates
            raw_text: Original raw text of the report (no usado actualmente)

        Returns:
            DuplicateDetectionResult with detection outcome
        """
        logger.info(
            f"Starting duplicate detection (SDK): {new_report.title[:50]}..."
        )

        # Construir mensaje inicial con detalles del reporte
        initial_message = self._format_report_for_agent(new_report)

        # Configurar opciones del agente
        options = self._build_agent_options()

        # Tracking
        last_assistant_text = ""
        all_candidates = []
        iterations = 0

        try:
            async with ClaudeSDKClient(options=options) as client:
                await client.query(initial_message)

                async for message in client.receive_response():
                    if isinstance(message, AssistantMessage):
                        iterations += 1
                        logger.info(f"[SDK] Iteration {iterations}")

                        # Extraer último texto para parsing
                        for block in message.content:
                            if isinstance(block, TextBlock):
                                last_assistant_text = block.text
                                logger.debug(
                                    f"[SDK] Text: {block.text[:200]}..."
                                )
                            elif isinstance(block, ToolUseBlock):
                                logger.info(
                                    f"[SDK] Tool use: {block.name} (id={block.id})"
                                )

                    elif isinstance(message, ResultMessage):
                        logger.success(
                            f"[SDK] Conversation finished: "
                            f"turns={message.num_turns}, "
                            f"duration={message.duration_ms}ms, "
                            f"cost=${message.total_cost_usd:.4f}, "
                            f"stop_reason={message.result}"
                        )

                        # Check for early stopping using metadata (SDK doesn't propagate stopReason)
                        metadata = get_last_tool_metadata()
                        if metadata.get("top_score", 0.0) > 0.9 and metadata.get("top_report_id"):
                            # Early stopping triggered - use metadata directly
                            logger.info("[SDK] Detected early stopping via high score in metadata")
                            candidates = metadata.get("candidates", [])
                            return DuplicateDetectionResult(
                                is_duplicate=True,
                                similarity_score=metadata.get("top_score", 0.95),
                                matched_report_id=metadata.get("top_report_id"),
                                matched_report=None,
                                status="duplicate",
                                candidates=candidates,
                            )

                        # Respuesta normal del agente
                        parsed = self._parse_json_from_text(last_assistant_text)
                        if parsed:
                            return DuplicateDetectionResult(
                                is_duplicate=parsed.get("is_duplicate", False),
                                similarity_score=parsed.get("similarity_score", 0.0),
                                matched_report_id=parsed.get("matched_report_id"),
                                matched_report=None,
                                status=parsed.get("status", "new"),
                                candidates=all_candidates,
                            )

                        # Fallback si no se puede parsear
                        return self._create_fallback_result()

            return self._create_fallback_result()

        except Exception as e:
            logger.error(f"[SDK] search_duplicates failed: {e}")
            raise

    def _format_report_for_agent(self, report: NormalizedReport) -> str:
        """
        Formatear reporte para el mensaje inicial del agente.

        MISMO formato que implementación actual en react.py
        """
        # Extraer payloads si existen
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

    def _parse_json_from_text(self, text: str) -> Optional[dict]:
        """
        Extraer JSON del texto de respuesta de Claude.

        Similar a _parse_final_answer() en react.py
        """
        try:
            # Buscar JSON en el texto
            json_start = text.find("{")
            json_end = text.rfind("}") + 1

            if json_start >= 0 and json_end > json_start:
                json_str = text[json_start:json_end]
                return json.loads(json_str)
        except json.JSONDecodeError as e:
            logger.error(f"[SDK] Failed to parse JSON: {e}")

        return None

    def _create_fallback_result(self) -> DuplicateDetectionResult:
        """Resultado fallback si no se puede parsear."""
        logger.warning("[SDK] Using fallback result (parsing failed)")
        return DuplicateDetectionResult(
            is_duplicate=False,
            similarity_score=0.0,
            matched_report=None,
            matched_report_id=None,
            status="new",
            candidates=[],
        )


# ============================================================================
# CONVENIENCE FUNCTIONS (Backward Compatibility)
# ============================================================================

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

    # The SDK is async, we need a sync wrapper
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    return loop.run_until_complete(agent.search_duplicates(new_report, raw_text))
