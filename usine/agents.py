"""Agents du pipeline : discover, product, dev, fix, business — tous via le Claude Agent SDK."""

import hashlib
import re
import shutil
from pathlib import Path

from usine import db, prompts
from usine.config import settings
from usine.schemas import BusinessOutput, DiscoverOutput, ProductSpec
from usine.sdk import StepUsage, run_agentic, run_structured
from usine.state import RunState, dump_json


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:30] or "app"


def smoke_file_hash(code_dir: Path) -> str:
    smoke = code_dir / "tests" / "smoke.py"
    return hashlib.sha256(smoke.read_bytes()).hexdigest() if smoke.is_file() else ""


async def agent_discover(state: RunState, logger) -> StepUsage:
    """Génère 5 idées scorées (anti-duplication via l'historique), sélectionne la meilleure."""
    past = db.past_ideas(state.category)
    logger.info("🔎 discover — catégorie: %s, %d idées passées exclues",
                state.category or "libre", len(past))
    data, usage = await run_structured(
        prompts.discover_prompt(state.category, state.stack, past),
        DiscoverOutput.model_json_schema(),
        model=settings.model_discover,
        timeout=settings.timeout_discover,
        logger=logger,
        label="discover",
    )
    output = DiscoverOutput.model_validate(data)
    ideas = [i.model_dump() for i in output.ideas]
    # Recalcul serveur du score (anti-inflation)
    for idea in ideas:
        idea["score"] = sum(idea["score_breakdown"].values())
    ideas.sort(key=lambda i: i["score"], reverse=True)
    # Anti-duplication mécanique : écarte les idées dont le slug existe déjà en base
    known = db.known_slugs()
    fresh = [i for i in ideas if _slug(i["name"]) not in known]
    if not fresh:
        raise RuntimeError("discover: les 5 idées générées dupliquent l'historique — relancer")
    if len(fresh) < len(ideas):
        logger.warning("⚠️ %d idée(s) dupliquant l'historique écartée(s)", len(ideas) - len(fresh))
    state.ideas = ideas
    state.idea = fresh[0]
    state.label = _slug(state.idea["name"])
    if not state.category:
        state.category = state.idea.get("category")
    state.save()
    db.save_ideas(state.run_id, ideas, state.idea["name"])
    dump_json(state.run_dir / "ideas.json", {"ideas": ideas})
    logger.info("🏆 Idée retenue : %s (%d/100) — %s",
                state.idea["name"], state.idea["score"], state.idea["one_liner"])
    return usage


async def agent_product(state: RunState, logger) -> StepUsage:
    """Spec produit structurée. Un stack forcé au CLI est imposé PAR LE SCHÉMA."""
    logger.info("📋 product — spec de «%s»", state.idea["name"])
    schema = ProductSpec.model_json_schema()
    if state.stack:
        schema.setdefault("properties", {})["stack"] = {"type": "string", "enum": [state.stack]}
    data, usage = await run_structured(
        prompts.product_prompt(state.idea, state.stack),
        schema,
        model=settings.model_product,
        timeout=settings.timeout_product,
        logger=logger,
        label="product",
    )
    spec = ProductSpec.model_validate(data)
    state.spec = spec.model_dump()
    state.stack = spec.stack
    state.save()
    logger.info("📐 Stack: %s — %d features, %d routes, %d parcours critiques",
                spec.stack, len(spec.features), len(spec.routes), len(spec.critical_paths))
    return usage


async def agent_dev(state: RunState, logger) -> StepUsage:
    """Implémente la spec en mode agentique. Ne recopie JAMAIS le template sur du code existant."""
    template_dir = Path(settings.templates_dir) / state.stack
    if not template_dir.is_dir():
        raise RuntimeError(f"template introuvable: {template_dir}")
    first_time = not (state.code_dir / "spec.json").exists()
    if first_time:
        state.code_dir.mkdir(parents=True, exist_ok=True)
        shutil.copytree(template_dir, state.code_dir, dirs_exist_ok=True)
        logger.info("💻 dev — template %s copié, implémentation agentique...", state.stack)
    else:
        logger.info("💻 dev — reprise : code existant conservé (template non recopié)")
    dump_json(state.code_dir / "spec.json", state.spec)
    files = sorted(
        str(p.relative_to(state.code_dir)).replace("\\", "/")
        for p in state.code_dir.rglob("*")
        if p.is_file() and "__pycache__" not in p.parts
    )
    usage = await run_agentic(
        prompts.dev_prompt(state.spec, str(state.code_dir), files),
        state.code_dir,
        model=settings.model_dev,
        timeout=settings.timeout_dev,
        logger=logger,
        label="dev",
        max_turns=settings.max_turns_dev,
        resume_session=state.session_ids.get("dev"),
    )
    state.session_ids["dev"] = usage.session_id
    state.agent_summaries["dev"] = usage.summary
    state.smoke_hash = smoke_file_hash(state.code_dir)
    state.save()
    return usage


async def agent_fix(state: RunState, report: dict, cycle: int, logger) -> StepUsage:
    """Corrige les erreurs concrètes remontées par la validation, informé des cycles précédents."""
    logger.info("🔧 fix — cycle %d/%d", cycle, settings.max_fix_cycles)
    history = {k: v for k, v in state.agent_summaries.items() if k.startswith("fix#") and v}
    usage = await run_agentic(
        prompts.fix_prompt(report, cycle, settings.max_fix_cycles, history),
        state.code_dir,
        model=settings.model_fix,
        timeout=settings.timeout_fix,
        logger=logger,
        label=f"fix#{cycle}",
        max_turns=settings.max_turns_fix,
    )
    state.agent_summaries[f"fix#{cycle}"] = usage.summary
    state.save()
    return usage


async def agent_business(state: RunState, logger) -> StepUsage:
    """Assets marketing structurés (tagline, pricing, landing page, SEO)."""
    logger.info("💼 business — assets marketing...")
    data, usage = await run_structured(
        prompts.business_prompt(state.idea, state.spec),
        BusinessOutput.model_json_schema(),
        model=settings.model_business,
        timeout=settings.timeout_business,
        logger=logger,
        label="business",
    )
    state.business = BusinessOutput.model_validate(data).model_dump()
    state.save()
    logger.info("💬 Tagline : %s", state.business["tagline"])
    return usage
