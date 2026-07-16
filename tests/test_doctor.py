"""Tests des checks purs du doctor — sans Docker ni réseau."""

import json
from pathlib import Path

from usine.config import settings
from usine.doctor import (
    FAIL,
    OK,
    WARN,
    check_cli_config,
    check_credentials,
    check_disk,
    check_templates,
)


def test_disk_ok(tmp_path):
    assert check_disk(str(tmp_path), "disque", warn_gb=0.0, fail_gb=0.0, remedy="r").level == OK


def test_disk_warn(tmp_path):
    assert check_disk(str(tmp_path), "disque", warn_gb=10**9, fail_gb=0.0, remedy="r").level == WARN


def test_disk_fail_porte_le_remede(tmp_path):
    c = check_disk(str(tmp_path), "disque", warn_gb=10**9, fail_gb=10**9, remedy="libérer")
    assert c.level == FAIL
    assert c.remedy == "libérer"


def test_disk_chemin_illisible():
    assert check_disk("/chemin/inexistant", "disque", 1, 1, "r").level == FAIL


def test_credentials_absents(tmp_path):
    assert check_credentials(tmp_path / "absent.json").level == FAIL


def test_credentials_corrompus(tmp_path):
    path = tmp_path / "creds.json"
    path.write_text("{pas du json", encoding="utf-8")
    assert check_credentials(path).level == FAIL


def test_credentials_expires_avec_refresh_token(tmp_path):
    path = tmp_path / "creds.json"
    path.write_text(json.dumps({"claudeAiOauth": {"expiresAt": 1000, "refreshToken": "r"}}))
    assert check_credentials(path, now_ms=2000).level == WARN  # le CLI rafraîchira tout seul


def test_credentials_expires_sans_refresh_token(tmp_path):
    path = tmp_path / "creds.json"
    path.write_text(json.dumps({"claudeAiOauth": {"expiresAt": 1000}}))
    assert check_credentials(path, now_ms=2000).level == FAIL


def test_credentials_valides_affichent_l_echeance(tmp_path):
    path = tmp_path / "creds.json"
    path.write_text(json.dumps({"claudeAiOauth": {"expiresAt": 8 * 3_600_000}}))
    c = check_credentials(path, now_ms=0)
    assert c.level == OK
    assert "8 h" in c.detail


def test_cli_config_presente(tmp_path):
    (tmp_path / ".claude.json").write_text("{}", encoding="utf-8")
    assert check_cli_config(tmp_path).level == OK


def test_cli_config_absente_sans_backup(tmp_path):
    assert check_cli_config(tmp_path).level == WARN  # créée au premier appel du CLI


def test_cli_config_absente_avec_backup_bloque(tmp_path):
    # Cas réel du 2026-07-16 : le CLI refuse de démarrer si un backup existe sans config
    (tmp_path / "backups").mkdir()
    (tmp_path / "backups" / ".claude.json.backup.123").write_text("{}", encoding="utf-8")
    assert check_cli_config(tmp_path).level == FAIL


def test_templates_du_repo_respectent_le_contrat():
    assert check_templates(Path(settings.templates_dir)).level == OK


def test_templates_incomplets(tmp_path):
    c = check_templates(tmp_path)
    assert c.level == FAIL
    assert "webapp/Dockerfile" in c.detail
