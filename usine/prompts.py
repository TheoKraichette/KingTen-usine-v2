"""Prompts des agents. Les sorties structurées sont garanties par les schémas (pas de format à décrire)."""

import json

CATEGORIES = [
    "productivity", "ecommerce", "education", "health", "finance", "marketing",
    "hr", "real-estate", "food", "travel", "fitness", "legal", "events",
    "freelance", "content-creation", "customer-support", "logistics",
    "sustainability", "pets", "hobbies",
]

STACK_DESCRIPTIONS = """\
- `webapp` : application full-stack avec interface web, comptes utilisateurs et données persistées (FastAPI + SQLite + Jinja2/HTMX). Pour les outils avec dashboard, CRUD, multi-utilisateurs.
- `api` : service API pur sans interface (FastAPI + SQLite). Pour les services consommés par des développeurs (transformation, calcul, données).
- `static` : outil front 100 % navigateur, sans backend (HTML/CSS/JS vanilla). Pour les calculateurs, générateurs, convertisseurs, widgets."""

_STACK_NOTES = {
    "webapp": "- htmx est déjà fourni (`static/htmx.min.js`, référencé dans base.html) : n'ajoute AUCUNE ressource réseau (CDN interdit).",
    "api": "- Pas d'interface : GET / doit renvoyer la description JSON de l'API (le squelette le fait déjà, garde-le à jour).",
    "static": "- 100 % vanilla JS, aucun backend : toute la logique vit dans le navigateur. nginx renvoie 404 pour tout fichier absent — ne référence que des fichiers que tu crées réellement.",
}

# Le contrat exact que la validation mécanique appliquera — l'agent doit l'optimiser
_VALIDATION_CONTRACT = """\
CONTRAT DE VALIDATION MÉCANIQUE (l'app sera testée exactement ainsi, sur un conteneur FRAIS avec base vide) :
- `/health` doit répondre STRICTEMENT 200 (webapp/api).
- Chaque route GET publique sans paramètre déclarée dans la spec est appelée telle quelle sur base VIDE : elle doit répondre < 500 et ≠ 404 — gère explicitement le cas « aucune donnée ».
- Chaque route non-GET ou avec paramètre déclarée doit EXISTER : un appel avec corps vide ou id bidon peut répondre 400/401/403/422, mais jamais 404/405/500.
- Le conteneur est REDÉMARRÉ puis re-testé : l'init de la DB doit être idempotente (CREATE TABLE IF NOT EXISTS, jamais de DROP) et l'app doit survivre à un restart avec des données existantes.
- `tests/smoke.py` doit imprimer une ligne « OK  ... » ou « FAIL ... » par vérification (fonction check() fournie) et contenir AU MOINS 2 + <nb de parcours critiques> checks. Un nombre de checks en baisse entre deux validations est un échec automatique."""


def discover_prompt(category: str | None, stack: str | None, past: list[str]) -> str:
    past_block = "\n".join(f"- {p}" for p in past) if past else "(aucune — premier run)"
    category_line = (
        f"Catégorie imposée : **{category}**."
        if category else
        f"Choisis une catégorie porteuse parmi : {', '.join(CATEGORIES)}."
    )
    stack_line = (
        f"Contrainte : chaque idée doit être réalisable avec le template `{stack}`.\n{STACK_DESCRIPTIONS}"
        if stack else
        f"Chaque idée doit être réalisable avec l'un de ces 3 templates :\n{STACK_DESCRIPTIONS}"
    )
    return f"""Tu es un dénicheur d'idées de micro-SaaS rentables et simples.

{category_line}

Génère exactement 5 idées de micro-SaaS. Contraintes dures :
- MVP réalisable par un agent IA en moins d'une heure de développement.
- {stack_line}
- Aucun service externe payant, aucune clé API tierce, aucun envoi d'email : tout fonctionne en local (SQLite, calculs, génération).
- Public cible précis et problème douloureux réel.

Idées déjà générées lors des runs précédents — INTERDIT de les reproduire ou d'en proposer des variantes proches :
{past_block}

Score chaque idée honnêtement selon la grille : pain_intensity /20, feasibility /20, market_size /15, monetization_clarity /15, competition_gap /15, wow_factor /15. Le champ `score` est la somme exacte du breakdown."""


def product_prompt(idea: dict, stack: str | None) -> str:
    stack_line = (
        f"Le template est imposé : `{stack}` — la spec entière (routes, entities, pages, parcours) doit être conçue pour CE template.\n{STACK_DESCRIPTIONS}"
        if stack else
        f"Choisis le template le plus adapté :\n{STACK_DESCRIPTIONS}"
    )
    return f"""Tu es un product manager technique. Rédige la spécification MVP de cette idée de micro-SaaS :

{json.dumps(idea, ensure_ascii=False, indent=2)}

{stack_line}

Exigences :
- MVP strict : 3 à 6 features maximum, chacune réellement implémentable.
- `routes` : toutes les routes HTTP de l'app (pour `static`, uniquement GET / — les ancres ne comptent pas). Ne déclare QUE des routes qui seront réellement implémentées : chacune sera testée mécaniquement.
- `entities` : le schéma de données (vide pour `static` sans persistance).
- `pages` : les pages UI (vide pour `api`).
- `critical_paths` : 1 à 3 parcours utilisateur critiques, décrits en étapes CONCRÈTES et TESTABLES par un script Playwright ou HTTP. Chaque étape référence des sélecteurs CSS explicites (`#email`, `#btn-signup`, `[data-testid=...]`) ou des appels HTTP précis (méthode, chemin, payload, statut attendu). Le développeur DEVRA implémenter exactement ces sélecteurs.
  - `webapp` : typiquement signup → login → action principale.
  - `api` : séquence d'appels HTTP avec assertions sur les réponses.
  - `static` : l'interaction JavaScript principale (saisie → résultat visible).
- L'interface utilisateur et tous les textes de l'app sont en FRANÇAIS.
- `app_name` = le nom de l'idée."""


def dev_prompt(spec: dict, code_dir: str, existing_files: list[str]) -> str:
    files_block = "\n".join(f"- {f}" for f in existing_files)
    return f"""Tu es un développeur senior. Ton dossier de travail ABSOLU est `{code_dir}` (c'est ton cwd — utilise des chemins relatifs). Il contient le squelette du template `{spec["stack"]}` et `spec.json` (la spécification complète, reproduite ci-dessous — inutile de relire spec.json).

Fichiers déjà en place :
{files_block}

Si du code applicatif existe déjà au-delà du squelette, c'est une reprise : complète-le au lieu de repartir de zéro.

SPÉCIFICATION :
{json.dumps(spec, ensure_ascii=False, indent=2)}

Implémente le MVP complet selon la spec. Règles impératives :
1. NE CASSE PAS le contrat du template : l'app écoute sur 0.0.0.0:8000, la route `/health` répond 200, le `Dockerfile` fourni doit continuer à fonctionner tel quel (`docker build` puis `docker run` suffisent).
2. Implémente TOUTES les routes de la spec et TOUS les sélecteurs CSS mentionnés dans les `critical_paths` (ids / data-testid exacts).
3. Complète `tests/smoke.py` : il doit tester chaque parcours critique de la spec, de bout en bout, contre l'URL passée en `sys.argv[1]`. Contrat : exit code 0 si tout passe, 1 sinon, une ligne OK/FAIL par vérification via check(). Les bibliothèques `httpx` et `playwright` (chromium) sont disponibles. N'affaiblis jamais un test pour le faire passer.
4. Base de données : SQLite, fichier local `app.db`, initialisée automatiquement au démarrage si absente (init idempotente, jamais de DROP).
5. Toute dépendance ajoutée doit figurer dans `requirements.txt` et être installable par pip sans compilation exotique. Les dépendances du template sont déjà installées dans ton environnement : ne relance pas `pip install` pour elles.
6. Aucune ressource réseau externe (CDN compris) : tout est servi localement, l'app fonctionne hors ligne.
{_STACK_NOTES.get(spec["stack"], "")}
7. Interface et textes en FRANÇAIS. Soigne le CSS : l'app doit être présentable.
8. Avant de terminer : vérifie la syntaxe de chaque fichier Python avec `python -m py_compile`, et relis tests/smoke.py pour vérifier qu'il couvre bien les parcours critiques.

{_VALIDATION_CONTRACT}

Quand tu as terminé, résume en 3 lignes ce que tu as implémenté."""


def fix_prompt(report: dict, cycle: int, max_cycles: int, history: dict[str, str]) -> str:
    stages = [s for s in report.get("stages", []) if not s.get("passed")]
    failures = "\n\n".join(
        f"### Échec — {s['name']}\n{s.get('detail', '(pas de détail)')}" for s in stages
    )
    logs = report.get("docker_logs_tail", "")
    logs_block = f"\n\nLOGS DU CONTENEUR (fin) :\n```\n{logs}\n```" if logs else ""
    history_block = ""
    if history:
        lines = "\n".join(f"- {k} : {v}" for k, v in sorted(history.items()))
        history_block = f"""

CORRECTIONS DÉJÀ TENTÉES AUX CYCLES PRÉCÉDENTS (ne les défais pas, ne les répète pas à l'identique — cherche plus profond) :
{lines}"""
    return f"""Tu es un développeur senior en debugging. L'app de ce dossier (spec dans `spec.json`) a échoué à la validation mécanique (cycle {cycle}/{max_cycles}).

ERREURS CONCRÈTES À CORRIGER :

{failures}{logs_block}{history_block}

Instructions :
1. Diagnostique la cause racine de CHAQUE échec ci-dessus, puis corrige le code.
2. INTERDIT d'affaiblir ou de supprimer des tests pour les faire passer : corrige l'application. Toute baisse du nombre de checks dans tests/smoke.py est détectée mécaniquement et fait échouer la validation. Ne modifie `tests/smoke.py` que si le test lui-même est objectivement bogué (mauvais sélecteur par rapport au HTML réel, par exemple).
3. Le contrat du template reste inchangé : port 8000, `/health` → strictement 200, Dockerfile fonctionnel.
4. Vérifie la syntaxe des fichiers modifiés avec `python -m py_compile`.

{_VALIDATION_CONTRACT}

Résume en 3 lignes les corrections effectuées."""


def business_prompt(idea: dict, spec: dict) -> str:
    return f"""Tu es un marketeur produit. Voici un micro-SaaS qui vient d'être développé :

IDÉE : {json.dumps(idea, ensure_ascii=False, indent=2)}

FEATURES : {json.dumps(spec.get("features", []), ensure_ascii=False)}

Produis les assets business en FRANÇAIS :
- tagline percutante, description vendeuse, bénéfices clés
- grille de pricing réaliste pour un micro-SaaS (2-3 paliers, prix en euros/mois)
- mots-clés SEO
- une landing page HTML complète, standalone (CSS inline dans <style>, AUCUNE ressource externe, pas de JS requis) : hero avec tagline + CTA, section bénéfices, section pricing, footer. Design moderne et soigné (dégradés, ombres douces, responsive)."""
