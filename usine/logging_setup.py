"""Logging UTF-8 : console globale + un fichier par run."""

import logging
import sys
from pathlib import Path

_FORMAT = "%(asctime)s %(levelname)-7s %(message)s"
_DATEFMT = "%H:%M:%S"


def setup_logging(level: int = logging.INFO) -> None:
    """Configure la console globale (stdout, UTF-8). Le fichier par run reçoit aussi le DEBUG."""
    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(level)
    handler.setFormatter(logging.Formatter(_FORMAT, datefmt=_DATEFMT))
    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()
    root.addHandler(handler)
    # Bruit des libs tierces
    for noisy in ("httpx", "asyncio", "claude_agent_sdk"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def run_logger(run_id: str, run_dir: Path) -> "_PrefixAdapter":
    """Logger d'un run : préfixe [run_id] en console + fichier run.log dédié."""
    logger = logging.getLogger(f"usine.run.{run_id}")
    logger.setLevel(logging.DEBUG)  # le fichier capte tout, la console (root) filtre à INFO
    if not logger.handlers:
        run_dir.mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(run_dir / "run.log", encoding="utf-8")
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(logging.Formatter(_FORMAT, datefmt="%Y-%m-%d %H:%M:%S"))
        logger.addHandler(fh)
    return _PrefixAdapter(logger, run_id)


class _PrefixAdapter(logging.LoggerAdapter):
    """Préfixe chaque message avec le run_id pour la console partagée."""

    def __init__(self, logger: logging.Logger, run_id: str):
        super().__init__(logger, {})
        self.run_id = run_id

    def process(self, msg, kwargs):
        return f"[{self.run_id}] {msg}", kwargs
