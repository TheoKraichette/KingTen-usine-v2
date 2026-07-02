"""Test réel du SDK : auth par abonnement + structured output.

À lancer tôt et à chaque doute : `docker compose run --rm usine sdk-check`.
"""

import asyncio
import logging
import time

logger = logging.getLogger(__name__)


async def _check() -> int:
    from claude_agent_sdk import (
        AssistantMessage,
        ClaudeAgentOptions,
        ResultMessage,
        TextBlock,
        query,
    )

    # 1. Appel texte minimal — valide l'auth
    logger.info("Test 1/2 — appel texte simple (auth)...")
    t0 = time.time()
    options = ClaudeAgentOptions(max_turns=1, allowed_tools=[], model="haiku")
    text, result = "", None
    async for message in query(prompt="Réponds exactement: OK", options=options):
        if isinstance(message, AssistantMessage):
            for block in message.content:
                if isinstance(block, TextBlock):
                    text += block.text
        elif isinstance(message, ResultMessage):
            result = message
    if result is None or result.is_error:
        logger.error("Échec test 1 : %s", result)
        return 1
    logger.info(
        "OK en %.1fs — réponse: %r, coût: $%.4f, session: %s",
        time.time() - t0, text.strip(), result.total_cost_usd or 0, result.session_id,
    )

    # 2. Structured output — mécanisme clé des agents discover/product/business
    logger.info("Test 2/2 — structured output (json_schema)...")
    t0 = time.time()
    schema = {
        "type": "object",
        "properties": {
            "ville": {"type": "string"},
            "population": {"type": "integer"},
        },
        "required": ["ville", "population"],
        "additionalProperties": False,
    }
    options = ClaudeAgentOptions(
        max_turns=1,
        allowed_tools=[],
        model="haiku",
        output_format={"type": "json_schema", "schema": schema},
    )
    structured, result = None, None
    async for message in query(
        prompt="Donne la ville la plus peuplée de France et sa population approximative.",
        options=options,
    ):
        if isinstance(message, ResultMessage):
            result = message
            structured = getattr(message, "structured_output", None)
    if result is None or result.is_error or not isinstance(structured, dict):
        logger.error("Échec test 2 : result=%s structured=%r", result, structured)
        return 1
    logger.info(
        "OK en %.1fs — structured_output: %s, coût: $%.4f",
        time.time() - t0, structured, result.total_cost_usd or 0,
    )

    logger.info("SDK opérationnel : auth abonnement + structured output validés.")
    return 0


def sdk_check() -> int:
    return asyncio.run(_check())
