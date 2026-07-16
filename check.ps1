# Controle qualite de l'usine : lint (ruff) + tests unitaires (pytest), le tout dans Docker.
# Usage : .\check.ps1        (l'image doit exister : docker compose build la premiere fois)

Write-Host "--- ruff (lint) ---"
docker compose run --rm --entrypoint python usine -m ruff check usine tests
$lint = $LASTEXITCODE

Write-Host "--- pytest ---"
docker compose run --rm --entrypoint python usine -m pytest tests -q
$tests = $LASTEXITCODE

if ($lint -ne 0 -or $tests -ne 0) {
    Write-Host "[KO] check en echec (ruff=$lint, pytest=$tests)"
    exit 1
}
Write-Host "[OK] check reussi (lint + tests)"
exit 0
