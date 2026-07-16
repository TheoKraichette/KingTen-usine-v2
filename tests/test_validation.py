"""Tests des helpers purs de la validation (comptage des checks, détection infra)."""

from usine.validation import _is_infra_failure, _tail, container_name, count_checks, image_tag


def test_count_checks_ne_compte_que_les_debuts_de_ligne():
    out = "OK inscription\nFAIL login → 500\nblabla OK au milieu\nOK /health\n"
    assert count_checks(out) == 3


def test_count_checks_sortie_sans_check():
    assert count_checks("Traceback (most recent call last):\n  ...") == 0


def test_tail_borne_la_taille():
    assert _tail("x" * 10_000, limit=100) == "x" * 100
    assert _tail("court") == "court"


def test_echecs_infra_detectes():
    assert _is_infra_failure(124, "")  # timeout docker
    assert _is_infra_failure(1, "Cannot connect to the Docker daemon at unix:///var/run/docker.sock")
    assert _is_infra_failure(1, "write /var/lib/docker/tmp: no space left on device")


def test_bug_d_app_n_est_pas_un_echec_infra():
    assert not _is_infra_failure(1, "SyntaxError: invalid syntax (main.py, line 12)")


def test_noms_conteneur_et_image():
    assert container_name("abc") == "usine-app-abc"
    assert image_tag("abc") == "usine-app-abc:latest"
