# Maintenance i18n long terme

Phase i18n.12 fournit l'outillage pour ajouter / maintenir des chaines
traduites sans casser la parite entre les 8 catalogues. Ce document
explique le workflow et les conventions.

## TLDR : ajouter une nouvelle chaine

Le dev tape en **francais** dans `data/i18n/fr.json` puis lance le
traducteur incrementiel :

```bash
# 1. Ajouter la nouvelle cle FR dans data/i18n/fr.json :
#    "my.new.key": "Texte source FR"

# 2. Utiliser dans le code Python :
#    from shinobi.i18n import t
#    t("my.new.key")

# 3. Detecter + traduire vers les 7 autres langues :
python scripts/i18n_translate_new.py --backend qwen   # gratuit (local)
# ou
python scripts/i18n_translate_new.py                  # Anthropic Sonnet (paye)

# 4. Verifier la parite :
python scripts/i18n_lint.py
# -> [i18n_lint] OK : tous les catalogues sont alignes.
```

Le pre-commit hook lance automatiquement `i18n_lint` et
`i18n_extract_new_strings` avant chaque `git commit`. Si la parite
catalog est cassee ou qu'une cle est utilisee dans le code sans figurer
au catalogue, le commit est refuse.

## Architecture i18n

### Langues supportees

8 langues, source canonique = anglais (`en`) :

| Code | Langue | Native |
|------|--------|--------|
| `en` | English (canonical) | English |
| `fr` | French | Français |
| `es` | Spanish | Español |
| `ja` | Japanese | 日本語 |
| `zh` | Chinese (Simplified) | 中文 |
| `ko` | Korean | 한국어 |
| `pt-BR` | Brazilian Portuguese | Português (Brasil) |
| `de` | German | Deutsch |

### Catalogue plat

`data/i18n/<lang>.json` est un dict flat `cle_pointee -> traduction` :

```json
{
  "_schema": "i18n_v1",
  "cli.menu.welcome": "Welcome to Shinobi no Sho",
  "engine.actions.outcome.full_success": "Success. {summary}",
  "api.canon.character_not_found": "canon character not found: {canon_id}"
}
```

Les meta-cles (commencent par `_`) sont filtrees par le loader.

### Glossary preserve

`data/i18n/glossary.json` liste les termes Naruto qui **ne doivent
jamais etre traduits** (chakra, Sharingan, Hokage, Konohagakure, etc.).
Le footer LLM auto-injecte ce glossary dans tout prompt systeme via
`shinobi.i18n.glossary.llm_prompt_footer()`. Le traducteur batch
respecte aussi cette liste.

### Resolution runtime

```python
from shinobi.i18n import t, set_language, get_language

set_language("ja")               # persiste dans preferences.json
get_language()                   # -> "ja"
t("cli.menu.welcome")            # -> "シノビの書へようこそ"
t("engine.actions.outcome.full_success", summary="x")
                                  # -> "成功。x"
```

Si une cle manque dans la lang active, fallback automatique sur EN.
Si elle manque aussi en EN, retourne la cle elle-meme + warning logger.

### API Accept-Language

Le middleware `AcceptLanguageMiddleware` (Phase 9) parse
`Accept-Language: ja, en;q=0.9` et active la langue pour la duree de la
requete via ContextVar. Pas de leak inter-requete.

## Outils Phase 12

### `scripts/i18n_lint.py`

Verifie la parite : tous les catalogues doivent avoir les memes cles
(les `test.*` sont exclues, elles servent aux tests de fallback).

```bash
python scripts/i18n_lint.py            # rapport console
python scripts/i18n_lint.py --json     # rapport JSON
python scripts/i18n_lint.py --quiet    # exit code uniquement
```

Exit code : 0 si aligne, 1 sinon.

### `scripts/i18n_extract_new_strings.py`

Scan recursif de `src/shinobi/**/*.py` pour les call-sites `t("key")`,
compare a `en.json`. Signale les cles utilisees dans le code mais
absentes du catalogue (= probable nouvelle chaine non encore traduite).

```bash
python scripts/i18n_extract_new_strings.py
```

Exit code : 0 si pas de nouvelle cle, 1 sinon.

### `scripts/i18n_translate_new.py`

Pour chaque cle presente dans `fr.json` mais absente dans `en.json`,
demande au LLM (Anthropic Sonnet ou Qwen3-4B local) de traduire vers
les 8 langues + ajoute aux catalogues. Glossary preserve.

```bash
python scripts/i18n_translate_new.py --dry-run        # preview
python scripts/i18n_translate_new.py --backend qwen   # gratuit (local)
python scripts/i18n_translate_new.py                  # Sonnet (~$0.10/run)
```

Workflow recommande : Qwen local pour les chaines simples (UI labels),
Sonnet pour les chaines longues / nuancees (narration).

### Pre-commit hook

Installation :

```bash
bash scripts/install-precommit-hook.sh
```

Le hook ecrit `.git/hooks/pre-commit` qui lance avant chaque commit :
1. `i18n_lint.py --quiet` : refuse si catalogues divergent.
2. `i18n_extract_new_strings.py --quiet` : refuse si cles orphelines.

Bypass possible (a eviter) : `git commit --no-verify`.

Alternative : `pre-commit` framework via `.pre-commit-config.yaml`
(requiert `pip install pre-commit` puis `pre-commit install`).

## Conventions de cle

### Namespacing

Utiliser des prefixes hierarchiques :

- `cli.<screen>.<element>` : strings CLI (menus, prompts, status views)
- `engine.<module>.<concept>` : strings du moteur (actions, missions,
  outcomes, etc.)
- `api.<route_group>.<error>` : messages HTTPException des routes API
- `narrator.<context>` : prompts systeme LLM (non utilise — gere via
  `data/i18n/prompts/<lang>/narrator.txt`)
- `test.*` : reserve aux tests de fallback (exclus du lint)

### Placeholders

Utiliser `{name}` (str.format style). Le placeholder doit etre identique
dans toutes les langues :

```json
// en.json
"api.canon.character_not_found": "canon character not found: {canon_id}",
// ja.json
"api.canon.character_not_found": "canon キャラクターが見つかりません: {canon_id}",
```

Ne PAS utiliser `{0}` positional (sauf pour les tests dedies).

### Termes preserves

Si la cle contient un terme Naruto a preserver (chakra, Hokage, etc.),
le terme **doit rester en romaji dans toutes les langues** :

```json
// fr.json
"engine.combat.chakra_low": "Chakra faible ({current}/{max})",
// ja.json (chakra reste en romaji)
"engine.combat.chakra_low": "chakra 不足 ({current}/{max})",
```

Le glossary `data/i18n/glossary.json` est la source de verite. Ajouter
un terme :

```json
{
  "techniques": [..., "new_jutsu"],
  ...
}
```

Le glossary est versionne avec le repo, pas par langue.

## Tests cross-langue

Phase i18n.11 fournit la fixture `lang` dans `tests/conftest.py` qui
parametrise tout test sur les 8 langues :

```python
def test_my_feature_works_in_all_langs(lang: str) -> None:
    # `lang` est set via `set_active_language(...)` automatiquement
    out = my_feature()
    assert ...
```

9 fichiers existants couvrent CLI, engine, canon, api, llm, no-leak,
glossary, goals, missions.

## Workflow en cas de regression

```bash
# Symptome : un commit fait `i18n_lint` echouer
python scripts/i18n_lint.py
# [i18n_lint] JA: 5 missing, 0 extra
#     missing: my.new.key1, my.new.key2, ...

# Cas 1 : nouvelle cle ajoutee uniquement en FR
python scripts/i18n_translate_new.py --backend qwen

# Cas 2 : cle ajoutee en EN sans propagation
# -> manuel : copier l'entree dans fr.json, puis translate_new

# Cas 3 : cle extra dans une lang (typo, etc.)
# -> manuel : retirer la cle du catalogue divergent
```

## Cost et performance

- Qwen3-4B local (llama.cpp) : gratuit, ~30s pour 10 cles x 7 langs.
- Anthropic Sonnet 4.6 : ~$0.10 par run typique (1-20 nouvelles cles
  vers 7 langs).

Le pre-commit hook lance `i18n_lint.py` qui prend < 1s — pas d'impact
perf perceptible.
