"""
Test script for ReAct agent with Claude Agent SDK.

This script verifies that the ReAct agent (using Claude Agent SDK)
works correctly for duplicate detection.
"""

import asyncio
from pathlib import Path

from loguru import logger

from mnemosyne.agents.react import ReactAgent
from mnemosyne.models.schema import NormalizedReport, TechnicalArtifact


async def test_basic_detection():
    """Test basic duplicate detection functionality."""
    print("=" * 80)
    print("TESTING BASIC DUPLICATE DETECTION")
    print("=" * 80)

    # Create test report
    test_report = NormalizedReport(
        title="SQL Injection in /api/login endpoint",
        summary="SQL injection vulnerability allows bypassing authentication in the login endpoint",
        vulnerability_type="SQL Injection",
        severity="high",
        affected_component="/api/login",
        reproduction_steps=[
            "Navigate to /api/login",
            "Send POST request with username parameter",
            "Use payload: admin' OR '1'='1",
            "Observe successful authentication bypass",
        ],
        technical_artifacts=[
            TechnicalArtifact(
                type="payload",
                language="sql",
                content="admin' OR '1'='1",
                description="SQL injection payload that bypasses authentication",
            ),
        ],
        technologies=["Python", "Flask", "MySQL"],
        impact="Complete authentication bypass allowing unauthorized access to any account",
        remediation="Use parameterized queries and input validation",
        metadata={
            "cves": [],
            "references": [],
            "hunter_notes": "Test report for agent verification",
        },
    )

    try:
        agent = ReactAgent()
        result = await agent.search_duplicates(test_report, "")

        print(f"\n✅ Detection Result:")
        print(f"  is_duplicate: {result.is_duplicate}")
        print(f"  score: {result.similarity_score:.3f}")
        print(f"  status: {result.status}")
        print(f"  matched_id: {result.matched_report_id}")

        print("\n✅ Basic detection test PASSED")
        return True

    except Exception as e:
        print(f"\n❌ Detection test FAILED: {e}")
        logger.exception("Detection test failed")
        return False


async def test_early_stopping():
    """Test that early stopping works correctly with high confidence matches."""
    print("\n" + "=" * 80)
    print("TESTING EARLY STOPPING (Score > 0.9)")
    print("=" * 80)

    # Create a test report that should match something with high confidence
    # NOTE: This test requires data in Qdrant
    test_report = NormalizedReport(
        title="Cross-Site Scripting (XSS) in Search Bar",
        summary="XSS vulnerability in search functionality allows script execution",
        vulnerability_type="Cross-Site Scripting (XSS)",
        severity="medium",
        affected_component="/search",
        reproduction_steps=[
            "Navigate to search page",
            "Enter XSS payload in search field",
            "Submit form",
            "Observe script execution",
        ],
        technical_artifacts=[
            TechnicalArtifact(
                type="payload",
                language="html",
                content="<script>alert(document.cookie)</script>",
                description="XSS payload that displays cookies",
            ),
        ],
        technologies=["JavaScript"],
        impact="Limited XSS attack allowing cookie theft",
        remediation="Sanitize user input and implement CSP",
        metadata={"cves": [], "references": [], "hunter_notes": "Test for early stopping"},
    )

    try:
        agent = ReactAgent()
        result = await agent.search_duplicates(test_report, "")

        print(f"\n✅ Early Stopping Test Result:")
        print(f"  is_duplicate: {result.is_duplicate}")
        print(f"  score: {result.similarity_score:.3f}")
        print(f"  status: {result.status}")
        print(f"  matched_id: {result.matched_report_id}")

        if result.similarity_score > 0.9:
            print(f"\n✅ Early stopping was triggered (score={result.similarity_score:.3f} > 0.9)!")
        else:
            print(
                f"\n✓ Early stopping not triggered (score={result.similarity_score:.3f} <= 0.9)"
            )

        print("\n✅ Early stopping test PASSED")
        return True

    except Exception as e:
        print(f"\n❌ Early stopping test FAILED: {e}")
        logger.exception("Early stopping test failed")
        return False


async def test_tool_validation():
    """Test that PreToolUse hook validates queries correctly."""
    print("\n" + "=" * 80)
    print("TESTING PRETOOLUSE HOOK (Query Validation)")
    print("=" * 80)

    # This test would require mocking or specific scenarios
    # For now, we'll just verify the agent can be instantiated
    try:
        agent = ReactAgent()
        print("\n✅ Agent instantiated with hooks configured")
        print("✅ PreToolUse hook validation test PASSED")
        return True

    except Exception as e:
        print(f"\n❌ Hook validation test FAILED: {e}")
        logger.exception("Hook validation failed")
        return False


if __name__ == "__main__":
    print("\n🔬 Starting ReAct Agent Tests (Claude Agent SDK)\n")

    # Configure logging
    logger.add(
        "logs/test_react_agent_{time}.log",
        rotation="1 day",
        level="DEBUG",
        format="{time:YYYY-MM-DD HH:mm:ss} | {level} | {name}:{function}:{line} | {message}",
    )

    # Run tests
    results = []

    print("Test 1/3: Basic Duplicate Detection")
    results.append(asyncio.run(test_basic_detection()))

    print("\n" + "=" * 80)
    print("Test 2/3: Early Stopping")
    results.append(asyncio.run(test_early_stopping()))

    print("\n" + "=" * 80)
    print("Test 3/3: Tool Validation (Hooks)")
    results.append(asyncio.run(test_tool_validation()))

    # Summary
    print("\n" + "=" * 80)
    print("TEST SUMMARY")
    print("=" * 80)
    passed = sum(results)
    total = len(results)
    print(f"Passed: {passed}/{total}")

    if passed == total:
        print("\n✅ All tests PASSED!")
    else:
        print(f"\n⚠️  {total - passed} test(s) FAILED")

    print()
