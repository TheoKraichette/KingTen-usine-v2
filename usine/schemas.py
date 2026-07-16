"""Schémas Pydantic des sorties structurées des agents (discover / product / business)."""

from typing import Literal

from pydantic import BaseModel, Field


# ── discover ─────────────────────────────────────────────────
class ScoreBreakdown(BaseModel):
    pain_intensity: int = Field(ge=0, le=20)
    feasibility: int = Field(ge=0, le=20)
    market_size: int = Field(ge=0, le=15)
    monetization_clarity: int = Field(ge=0, le=15)
    competition_gap: int = Field(ge=0, le=15)
    wow_factor: int = Field(ge=0, le=15)


class Idea(BaseModel):
    name: str = Field(description="Nom court du produit, un seul mot de marque")
    one_liner: str = Field(description="Pitch en une phrase")
    description: str = Field(description="Description du problème et de la solution, 3-4 phrases")
    category: str
    target_audience: str
    monetization: str
    score: int = Field(ge=0, le=100)
    score_breakdown: ScoreBreakdown


class DiscoverOutput(BaseModel):
    ideas: list[Idea] = Field(min_length=5, max_length=5)


# ── product ──────────────────────────────────────────────────
class Route(BaseModel):
    method: Literal["GET", "POST", "PUT", "PATCH", "DELETE"]
    path: str
    description: str
    auth_required: bool = False


class Entity(BaseModel):
    name: str
    fields: list[str] = Field(description="Champs au format 'nom type contrainte'")


class CriticalPath(BaseModel):
    name: str = Field(description="Nom du parcours, ex. 'signup-login-create'")
    steps: list[str] = Field(
        description="Étapes UI concrètes et testables, ex. 'remplir #email avec "
                    "test@x.fr et cliquer sur #btn-signup'"
    )


class ProductSpec(BaseModel):
    app_name: str
    stack: Literal["webapp", "api", "static"]
    summary: str
    features: list[str] = Field(min_length=3, max_length=8)
    routes: list[Route]
    entities: list[Entity] = Field(default_factory=list)
    pages: list[str] = Field(default_factory=list,
                             description="Pages UI (chemins), vide pour stack api")
    critical_paths: list[CriticalPath] = Field(min_length=1, max_length=3)
    notes: str = Field(default="", description="Contraintes techniques particulières")


# ── business ─────────────────────────────────────────────────
class PricingTier(BaseModel):
    name: str
    price_month_eur: float
    features: list[str]


class BusinessOutput(BaseModel):
    tagline: str
    description: str
    key_benefits: list[str] = Field(min_length=3, max_length=6)
    pricing: list[PricingTier] = Field(min_length=2, max_length=3)
    seo_keywords: list[str] = Field(min_length=5, max_length=15)
    landing_page_html: str = Field(
        description="Page HTML standalone complète (CSS inline), en français, prête à publier"
    )
