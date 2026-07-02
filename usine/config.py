"""Configuration centralisée de l'usine (variables d'env USINE_*, injectées par compose)."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="USINE_", extra="ignore")

    # Parallélisme (sémaphore)
    parallel: int = 3

    # Modèle par agent (alias CLI : sonnet / opus / haiku, ou ID complet)
    model_discover: str = "sonnet"
    model_product: str = "sonnet"
    model_dev: str = "sonnet"
    model_fix: str = "sonnet"
    model_business: str = "haiku"

    # Timeouts par étape (secondes) — hambook a atteint 2047s en dev, marge large
    timeout_discover: int = 300
    timeout_product: int = 600
    timeout_dev: int = 3600
    timeout_fix: int = 1800
    timeout_business: int = 600
    timeout_validate: int = 900

    # Plafonds de tours agentiques (hambook a atteint 98 turns)
    max_turns_dev: int = 150
    max_turns_fix: int = 120

    # Retry SDK — uniquement sur les erreurs transport (voir sdk.TRANSIENT_ERRORS)
    sdk_retries: int = 2
    sdk_retry_backoff: float = 20.0

    # Validation
    max_fix_cycles: int = 3
    healthcheck_timeout: int = 90

    # Garde-fous coût (0 = désactivé) — coût équivalent API, décompté de l'abonnement
    max_cost_per_run: float = 0.0

    # Chemins & infra (dans le conteneur)
    output_dir: str = "/app/output"
    data_dir: str = "/app/data"
    templates_dir: str = "/app/templates"
    docker_network: str = "usine-net"

    # Port interne des apps générées (dans leur conteneur)
    app_port: int = 8000


settings = Settings()
