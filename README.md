# Usine v2 — AI App Factory

Usine automatisée qui génère des micro-SaaS complets **et réellement validés** : chaque app est buildée en Docker, lancée, et testée de bout en bout (HTTP + Playwright) avant d'être livrée. Fini le score subjectif de la v1 — une app livrée est une app qui tourne.

## Architecture

```
┌────────────────────────── conteneur usine (Python 3.12) ──────────────────────────┐
│                                                                                    │
│  CLI (python -m usine) ──► orchestrateur asyncio (N runs concurrents, sémaphore)   │
│                                    │                                               │
│   pipeline par run :               ▼                                               │
│   discover ─► product ─► dev ─► validate ⇄ fix (max 3) ─► business ─► package     │
│      │           │         │         │                        │                    │
│   Claude Agent SDK (abonnement) ; structured outputs Pydantic │                    │
│      │                     │         │                                             │
│      ▼                     ▼         ▼                                             │
│   SQLite usine        mode agentique docker CLI ──► socket Docker monté            │
│   (historique,        (écrit le code            (build + run des apps en          │
│    anti-dup, ports)    dans output/)             conteneurs frères, réseau         │
│                                                  usine-net, tests Playwright)      │
└────────────────────────────────────────────────────────────────────────────────────┘
```

- **Moteur** : [Claude Agent SDK Python](https://code.claude.com/docs/en/agent-sdk) — auth par abonnement (credentials `~/.claude/.credentials.json` montés), structured outputs (zéro parsing manuel), mode agentique pour dev/fix.
- **Validation réelle** : `docker build` → `docker run` + healthcheck → smoke HTTP génériques → tests des parcours critiques (`tests/smoke.py` écrit par l'agent dev, Playwright/httpx) → nettoyage systématique. Les erreurs concrètes (logs de build, stack traces, tests rouges) alimentent l'agent fix.
- **3 templates de stacks** (`templates/`) : `webapp` (FastAPI + SQLite + Jinja2), `api` (FastAPI pur), `static` (HTML/CSS/JS + nginx). L'agent product choisit ; la variété vient des idées.
- **Parallélisme** : asyncio, sémaphore configurable, un run échoué ne bloque pas les autres.
- **Reprise** : state.json par run + SQLite ; `resume` repart de la dernière étape complétée.

## Prérequis

- Docker Desktop en marche
- Un compte Claude (Pro/Max) authentifié sur la machine (`claude login` déjà fait)

## Quick start

```powershell
cd usine_v2
docker compose build                                  # première fois (~5 min)
docker compose run --rm usine sdk-check               # valide l'auth SDK
docker compose run --rm usine run --count 2 --parallel 2
```

Ou via le wrapper : `.\usine.ps1 run --count 2 --parallel 2`

### Commandes

```
run --count N --parallel P [--category X] [--stack webapp|api|static] [--budget 20] [--dry-run]
resume <run_id> [--force]   # reprend un run interrompu à l'étape où il s'est arrêté
resume --all                # reprend tous les runs interrompus/échoués
status                      # runs en cours : étape, durée, coût, dernière activité du log
report [--json]             # rapport markdown (agrégats par stack/catégorie) ou JSON
list                        # historique des runs (marque les runs interrompus resumables)
open <run_id> / stop <id>   # lance/arrête une app packagée sur son port hôte
clean                       # supprime les conteneurs/images usine-app-* orphelins
delete <run_id>             # supprime un run (dossier + base) [--keep-ideas]
doctor                      # diagnostic complet : Docker, disques, credentials, templates...
sdk-check                   # test réel de l'auth + structured output
```

Un préflight (daemon Docker, disques, credentials) tourne automatiquement avant `run` et
`resume` : si l'environnement est cassé, l'usine refuse de démarrer **avant** de dépenser
des tokens, avec le remède dans le message. Diagnostic détaillé : `doctor`.

### Qualité de l'usine (lint + tests)

```powershell
.\check.ps1          # ruff + pytest, le tout dans Docker
```

### Récupérer une app

```powershell
cd output\run_<id>\code
docker compose up --build
# → http://localhost:<port>   (le port est dans le README du run)
```

## Output d'un run

```
output/run_<id>/
├── code/                    ← l'app complète (Dockerfile + docker-compose inclus)
├── landing_page.html        ← page marketing standalone
├── business.json            ← tagline, pricing, SEO
├── validation_report.json   ← résultats détaillés des tests réels
├── ideas.json               ← les 5 idées du discover
├── state.json               ← état complet du pipeline (sert au resume)
├── run.log                  ← journal du run
├── README.md                ← fiche de l'app
└── RAPPORT_ECHEC.md         ← seulement si le run a échoué
```

## Configuration (`.env`)

| Variable | Défaut | Rôle |
|---|---|---|
| `CLAUDE_CREDENTIALS` | `~/.claude/.credentials.json` | chemin hôte des credentials |
| `USINE_PARALLEL` | 3 | runs concurrents max |
| `USINE_MODEL_DISCOVER/PRODUCT/DEV/FIX/BUSINESS` | sonnet/haiku | modèle par agent |
| `USINE_TIMEOUT_*` | 300–3600 s | timeout par étape |
| `USINE_MAX_TURNS_DEV/FIX` | 150/120 | plafond de tours agentiques (continuation auto de session si atteint) |
| `USINE_MAX_FIX_CYCLES` | 3 | cycles validate⇄fix max |
| `USINE_MAX_COST_PER_RUN` | 0 (off) | plafond de coût équiv. API par run |
| `USINE_HOST_DISK_WARN_GB` / `USINE_HOST_DISK_MIN_GB` | 5 / 1.5 | seuils disque hôte (C:) : alerte / blocage des runs |
| `USINE_VM_DISK_WARN_GB` / `USINE_VM_DISK_MIN_GB` | 10 / 3 | idem pour l'espace interne de la VM Docker |

## Notes techniques

- L'usine tourne **dans Docker** (pas de Python requis sur l'hôte). Le socket Docker est monté : les apps générées sont buildées/lancées en **conteneurs frères** sur le réseau `usine-net`, joignables par nom de conteneur — aucun port hôte n'est utilisé pendant la validation.
- Le source (`usine/`, `templates/`, `tests/`) est **monté dans le conteneur** : les modifs de code sont prises en compte immédiatement, sans rebuild. Rebuild (`docker compose build`) uniquement si `requirements.txt`, le `Dockerfile` ou les versions épinglées changent.
- Les CA d'inspection TLS de la machine (WatchGuard, Cloudflare Gateway) sont embarqués dans l'image (`certs/`) — nécessaires pour que le CLI Claude et Playwright passent le proxy d'entreprise.
- Si la console Windows affiche du mojibake sur `docker compose` : `chcp 65001` ou utiliser Windows Terminal.

## Exploitation (runbook)

### Rituel

```powershell
.\usine.ps1 doctor                        # santé de l'environnement
.\usine.ps1 run --count 3 --parallel 3    # production
.\usine.ps1 status                        # suivi des runs en cours (depuis un autre terminal)
.\usine.ps1 report                        # bilan
```

### Pannes courantes

| Symptôme | Cause probable | Remède |
|---|---|---|
| `préflight échoué : disque hôte` ou crash containerd (SIGBUS) pendant un build | C: saturé — le vhdx Docker grossit dessus | libérer C:, `docker builder prune`, puis compacter le vhdx (ci-dessous) |
| `sdk-check` échoue en auth | token OAuth expiré/corrompu dans le volume `data/claude` | `claude login` sur l'hôte, puis `Remove-Item data\claude\.credentials.json` — re-seed automatique au prochain lancement |
| `SELF_SIGNED_CERT_IN_CHAIN` dans les logs | CA du proxy TLS absents ou renouvelés | réexporter les CA (ci-dessous) puis `docker compose build` |
| `list` affiche 💀 interrompu | kill/crash pendant un run | `.\usine.ps1 resume <id>` (ou `resume --all`) |
| conteneurs/images `usine-app-*` qui traînent | kill dur pendant une validation | `.\usine.ps1 clean` (aussi balayé automatiquement au prochain run) |
| mojibake dans la console | code page Windows | `chcp 65001` ou Windows Terminal |

### Compacter le vhdx Docker (libère C: — PowerShell **administrateur**)

```powershell
wsl --shutdown
diskpart
# puis dans diskpart :
#   select vdisk file="C:\Users\<toi>\AppData\Local\Docker\wsl\disk\docker_data.vhdx"
#   attach vdisk readonly
#   compact vdisk
#   detach vdisk
#   exit
```

### Réexporter les CA d'entreprise (format PEM attendu dans `certs/`)

```powershell
Get-ChildItem Cert:\LocalMachine\Root |
  Where-Object { $_.Subject -match "WatchGuard|Cloudflare Managed" } |
  ForEach-Object {
    $der = "certs\$($_.Thumbprint).der"
    Export-Certificate -Cert $_ -FilePath $der -Type CERT | Out-Null
    certutil -encode $der "certs\$($_.Thumbprint).crt"   # DER → PEM
    Remove-Item $der
  }
docker compose build
```
