"""État d'un run, persisté dans output/run_<id>/state.json à chaque étape (reprise sur échec)."""

import hashlib
import json
import secrets
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, Field

from usine.config import settings

STEP_ORDER = ["discover", "product", "dev", "validate", "business", "package"]


def utcnow() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def prompts_version() -> str:
    """Empreinte courte de prompts.py — trace quelle version des prompts a produit un run."""
    path = Path(__file__).parent / "prompts.py"
    return hashlib.sha256(path.read_bytes()).hexdigest()[:8]


class StepInfo(BaseModel):
    status: str = "pending"  # pending | running | done | failed
    started_at: str | None = None
    ended_at: str | None = None
    duration_s: float = 0.0
    cost_usd: float = 0.0
    input_tokens: int = 0  # input réel facturé plein tarif
    cache_creation_tokens: int = 0
    cache_read_tokens: int = 0
    output_tokens: int = 0
    error: str | None = None


class RunState(BaseModel):
    run_id: str
    label: str = ""  # slug humain choisi après discover
    created_at: str = Field(default_factory=utcnow)
    ended_at: str | None = None
    status: str = "created"  # created | running | completed | failed
    category: str | None = None
    stack: str | None = None  # imposé via CLI ou choisi par l'agent product
    idea: dict | None = None
    ideas: list[dict] = Field(default_factory=list)
    spec: dict | None = None
    business: dict | None = None
    fix_cycles: int = 0
    validation: dict | None = None  # dernier rapport de validation
    host_port: int | None = None  # port hôte réservé pour l'app packagée
    steps: dict[str, StepInfo] = Field(
        default_factory=lambda: {s: StepInfo() for s in STEP_ORDER}
    )
    session_ids: dict[str, str] = Field(default_factory=dict)  # étape -> session SDK (continuation)
    agent_summaries: dict[str, str] = Field(default_factory=dict)  # dev / fix#N -> résumé
    smoke_hash: str = ""  # sha256 de tests/smoke.py après dev (détection d'affaiblissement)
    smoke_checks: int = 0  # nb de checks observés au dernier passage (interdit de diminuer)
    prompts_version: str = ""  # empreinte de prompts.py au lancement
    models: dict[str, str] = Field(default_factory=dict)  # agent -> modèle utilisé

    # ── chemins ──────────────────────────────────────────────
    @property
    def run_dir(self) -> Path:
        return Path(settings.output_dir) / f"run_{self.run_id}"

    @property
    def code_dir(self) -> Path:
        return self.run_dir / "code"

    # ── cycle de vie ─────────────────────────────────────────
    @classmethod
    def create(cls, category: str | None = None, stack: str | None = None) -> "RunState":
        state = cls(
            run_id=secrets.token_hex(4),
            category=category,
            stack=stack,
            prompts_version=prompts_version(),
            models={
                "discover": settings.model_discover,
                "product": settings.model_product,
                "dev": settings.model_dev,
                "fix": settings.model_fix,
                "business": settings.model_business,
            },
        )
        state.run_dir.mkdir(parents=True, exist_ok=True)
        state.save()
        return state

    @classmethod
    def load(cls, run_id: str) -> "RunState":
        path = Path(settings.output_dir) / f"run_{run_id}" / "state.json"
        with open(path, encoding="utf-8") as f:
            return cls.model_validate_json(f.read())

    def save(self) -> None:
        self.run_dir.mkdir(parents=True, exist_ok=True)
        tmp = self.run_dir / "state.json.tmp"
        tmp.write_text(self.model_dump_json(indent=2), encoding="utf-8")
        tmp.replace(self.run_dir / "state.json")

    # ── étapes ───────────────────────────────────────────────
    def next_step(self) -> str | None:
        """Première étape non terminée (pour resume)."""
        for step in STEP_ORDER:
            if self.steps[step].status != "done":
                return step
        return None

    def step_start(self, step: str) -> None:
        info = self.steps[step]
        info.status = "running"
        info.started_at = utcnow()
        info.error = None
        self.status = "running"
        self.save()

    def step_done(self, step: str, *, cost_usd: float = 0.0, input_tokens: int = 0,
                  cache_creation_tokens: int = 0, cache_read_tokens: int = 0,
                  output_tokens: int = 0) -> None:
        info = self.steps[step]
        info.status = "done"
        info.ended_at = utcnow()
        if info.started_at:
            t0 = datetime.fromisoformat(info.started_at)
            info.duration_s = (datetime.fromisoformat(info.ended_at) - t0).total_seconds()
        info.cost_usd += cost_usd
        info.input_tokens += input_tokens
        info.cache_creation_tokens += cache_creation_tokens
        info.cache_read_tokens += cache_read_tokens
        info.output_tokens += output_tokens
        self.save()

    def step_failed(self, step: str, error: str) -> None:
        info = self.steps[step]
        info.status = "failed"
        info.ended_at = utcnow()
        info.error = error[:2000]
        self.status = "failed"
        self.ended_at = utcnow()
        self.save()

    def add_usage(self, step: str, *, cost_usd: float = 0.0, input_tokens: int = 0,
                  cache_creation_tokens: int = 0, cache_read_tokens: int = 0,
                  output_tokens: int = 0) -> None:
        """Cumule la conso d'un appel SDK intermédiaire (fix, tentative échouée...)."""
        info = self.steps[step]
        info.cost_usd += cost_usd
        info.input_tokens += input_tokens
        info.cache_creation_tokens += cache_creation_tokens
        info.cache_read_tokens += cache_read_tokens
        info.output_tokens += output_tokens
        self.save()

    # ── agrégats ─────────────────────────────────────────────
    @property
    def total_cost_usd(self) -> float:
        return sum(s.cost_usd for s in self.steps.values())

    @property
    def total_usage(self) -> dict[str, int]:
        return {
            "input": sum(s.input_tokens for s in self.steps.values()),
            "cache_creation": sum(s.cache_creation_tokens for s in self.steps.values()),
            "cache_read": sum(s.cache_read_tokens for s in self.steps.values()),
            "output": sum(s.output_tokens for s in self.steps.values()),
        }

    @property
    def duration_s(self) -> float:
        end = self.ended_at or utcnow()
        start = datetime.fromisoformat(self.created_at)
        return (datetime.fromisoformat(end) - start).total_seconds()


def dump_json(path: Path, data: dict | list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
