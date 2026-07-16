"""Wrappers Claude Agent SDK : appels structurés et agentiques.

Politique d'erreurs :
- erreurs TRANSPORT (connexion, process CLI) → retry avec backoff ;
- erreurs SÉMANTIQUES (ResultMessage.is_error, ex. error_max_turns) → jamais retryées
  à l'identique ; error_max_turns déclenche UNE continuation de session (resume) ;
- timeout d'étape → non retryable (trop coûteux à rejouer aveuglément).
La conso des tentatives échouées est capturée et attachée aux exceptions (attribut .usage).
"""

import asyncio
import contextlib
import time
from dataclasses import dataclass

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ResultMessage,
    TextBlock,
    ToolUseBlock,
    query,
)

from usine.config import settings

try:  # les noms d'exceptions transport du SDK (présents en 0.2.x)
    from claude_agent_sdk import CLIConnectionError, CLIJSONDecodeError, ProcessError
    TRANSIENT_ERRORS: tuple[type[BaseException], ...] = (
        CLIConnectionError, CLIJSONDecodeError, ProcessError, ConnectionError, OSError,
    )
except ImportError:  # version du SDK sans ces classes : on reste prudent
    TRANSIENT_ERRORS = (ConnectionError, OSError)

_CONTINUATION_PROMPT = (
    "Tu as été interrompu par la limite de tours. Reprends exactement où tu t'étais "
    "arrêté et termine la mission initiale (relis spec.json et l'état des fichiers si besoin). "
    "Quand tu as terminé, résume en 3 lignes ce que tu as implémenté."
)


@dataclass
class StepUsage:
    cost_usd: float = 0.0
    input_tokens: int = 0  # input réel plein tarif
    cache_creation_tokens: int = 0
    cache_read_tokens: int = 0
    output_tokens: int = 0
    num_turns: int = 0
    session_id: str = ""
    summary: str = ""  # résumé final de l'agent (dev/fix)

    def merge(self, other: "StepUsage") -> None:
        self.cost_usd += other.cost_usd
        self.input_tokens += other.input_tokens
        self.cache_creation_tokens += other.cache_creation_tokens
        self.cache_read_tokens += other.cache_read_tokens
        self.output_tokens += other.output_tokens
        self.num_turns += other.num_turns
        if other.session_id:
            self.session_id = other.session_id


class SDKStepError(RuntimeError):
    """Échec définitif d'un appel SDK. `.usage` porte la conso des tentatives perdues."""

    def __init__(self, message: str, usage: StepUsage | None = None):
        super().__init__(message)
        self.usage = usage


class SDKTimeoutError(SDKStepError):
    """Timeout d'étape — non retryable."""


class SDKResultError(SDKStepError):
    """ResultMessage.is_error — échec sémantique définitif (jamais retryé à l'identique)."""

    def __init__(self, message: str, usage: StepUsage | None = None, subtype: str = ""):
        super().__init__(message, usage)
        self.subtype = subtype


def _usage_from(result: ResultMessage) -> StepUsage:
    usage = result.usage or {}
    return StepUsage(
        cost_usd=result.total_cost_usd or 0.0,
        input_tokens=usage.get("input_tokens", 0),
        cache_creation_tokens=usage.get("cache_creation_input_tokens", 0),
        cache_read_tokens=usage.get("cache_read_input_tokens", 0),
        output_tokens=usage.get("output_tokens", 0),
        num_turns=result.num_turns,
        session_id=result.session_id or "",
    )


def _tool_hint(block: ToolUseBlock) -> str:
    inp = block.input or {}
    for key in ("file_path", "path", "command", "pattern"):
        if key in inp:
            return str(inp[key])[:120]
    return ""


async def _heartbeat(label: str, logger, activity: dict):
    """Log périodique enrichi de la dernière activité outillée. Backoff 30s → 60s → 120s."""
    t0 = time.time()
    intervals = iter((30, 60))
    while True:
        await asyncio.sleep(next(intervals, 120))
        mins, secs = divmod(int(time.time() - t0), 60)
        if activity.get("tools"):
            logger.info("⏳ %s en cours... %dm%02ds — %d outils, dernier : %s",
                        label, mins, secs, activity["tools"], activity.get("last", "?"))
        else:
            logger.info("⏳ %s en cours... %dm%02ds", label, mins, secs)


async def _consume(prompt: str, options: ClaudeAgentOptions, logger, label: str,
                   activity: dict) -> tuple[ResultMessage, str, StepUsage]:
    """Itère la query ; retourne (ResultMessage, texte, usage). Lève SDKResultError si is_error."""
    text_parts: list[str] = []
    result: ResultMessage | None = None
    async for message in query(prompt=prompt, options=options):
        if isinstance(message, AssistantMessage):
            for block in message.content:
                if isinstance(block, ToolUseBlock):
                    activity["tools"] = activity.get("tools", 0) + 1
                    activity["last"] = f"{block.name} {_tool_hint(block)}".strip()
                    logger.debug("%s → %s", label, activity["last"])
                elif isinstance(block, TextBlock):
                    text_parts.append(block.text)
        elif isinstance(message, ResultMessage):
            result = message
    if result is None:
        raise SDKResultError(f"{label}: aucun ResultMessage reçu du SDK")
    usage = _usage_from(result)
    if result.is_error:
        subtype = getattr(result, "subtype", "") or ""
        detail = result.result or subtype or "erreur inconnue"
        raise SDKResultError(
            f"{label}: le SDK a signalé une erreur ({subtype}) — {str(detail)[:500]}",
            usage=usage, subtype=subtype,
        )
    return result, "".join(text_parts), usage


async def _call(prompt: str, options: ClaudeAgentOptions, *, timeout: int, logger,
                label: str) -> tuple[ResultMessage, str, StepUsage]:
    """Un appel SDK : heartbeat + timeout + retry/backoff sur les seules erreurs transport."""
    delay = settings.sdk_retry_backoff
    wasted = StepUsage()  # conso des tentatives perdues
    last_err: Exception | None = None
    for attempt in range(1, settings.sdk_retries + 2):
        activity: dict = {}
        hb = asyncio.ensure_future(_heartbeat(label, logger, activity))
        t0 = time.time()
        try:
            result, text, usage = await asyncio.wait_for(
                _consume(prompt, options, logger, label, activity), timeout
            )
            usage.merge(wasted)  # les tentatives perdues comptent dans la conso de l'étape
            logger.info("✅ %s terminé en %.0fs (%d turns)",
                        label, time.time() - t0, result.num_turns)
            return result, text, usage
        except TimeoutError:
            raise SDKTimeoutError(f"{label}: timeout après {timeout}s",
                                  usage=wasted if wasted.cost_usd else None) from None
        except SDKResultError as e:
            if e.usage:
                wasted.merge(e.usage)
            e.usage = wasted
            raise  # définitif — la continuation max_turns est gérée par run_agentic
        except TRANSIENT_ERRORS as e:
            last_err = e
            if attempt <= settings.sdk_retries:
                logger.warning("⚠️ %s tentative %d échouée (%s: %s) — retry dans %.0fs",
                               label, attempt, type(e).__name__, str(e)[:200], delay)
                await asyncio.sleep(delay)
                delay *= 2
        finally:
            hb.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await hb
    raise SDKStepError(
        f"{label}: échec après {settings.sdk_retries + 1} tentatives — {last_err}",
        usage=wasted if wasted.cost_usd else None,
    )


async def run_structured(prompt: str, schema: dict, *, model: str, timeout: int,
                         logger, label: str) -> tuple[dict, StepUsage]:
    """Appel single-shot avec structured output (json_schema) — zéro parsing manuel."""
    options = ClaudeAgentOptions(
        model=model,
        allowed_tools=[],
        max_turns=8,
        output_format={"type": "json_schema", "schema": schema},
    )
    result, _, usage = await _call(prompt, options, timeout=timeout, logger=logger, label=label)
    structured = getattr(result, "structured_output", None)
    if not isinstance(structured, dict):
        raise SDKStepError(f"{label}: pas de structured_output dans la réponse", usage=usage)
    return structured, usage


async def run_agentic(prompt: str, cwd, *, model: str, timeout: int, logger, label: str,
                      max_turns: int = 150, resume_session: str | None = None) -> StepUsage:
    """Appel agentique (bypassPermissions dans cwd) ; error_max_turns → UNE continuation."""

    def _options(resume: str | None) -> ClaudeAgentOptions:
        return ClaudeAgentOptions(
            model=model,
            cwd=str(cwd),
            permission_mode="bypassPermissions",
            max_turns=max_turns,
            resume=resume,
        )

    try:
        _, text, usage = await _call(prompt, _options(resume_session),
                                     timeout=timeout, logger=logger, label=label)
    except SDKResultError as e:
        if e.subtype != "error_max_turns" or not (e.usage and e.usage.session_id):
            raise
        logger.warning("⚠️ %s: max_turns (%d) atteint — continuation de la session %s",
                       label, max_turns, e.usage.session_id[:8])
        _, text, usage = await _call(
            _CONTINUATION_PROMPT, _options(e.usage.session_id),
            timeout=timeout, logger=logger, label=f"{label}+cont",
        )
        usage.merge(e.usage)
    usage.summary = text.strip()[:1000]
    return usage
