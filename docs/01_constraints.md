# 01. Contraintes et conventions

Ce document regroupe les regles inviolables du projet. Toute production, code ou narration, doit les respecter.

## 1. Regles de style absolues

### 1.1 Aucun tiret cadratin

Le caractere em dash (long tiret) et le caractere en dash (tiret moyen) sont interdits dans toute production du projet. Code, commentaires, documentation, narration generee par le LLM, sortie CLI, messages d'erreur, tout.

Substituts autorises selon le contexte :

```
em dash narratif        => virgule, point, parenthese, deux-points
em dash de liste        => virgule ou retour a la ligne
em dash de pause        => virgule ou point
en dash de plage        => mot "a" ou tiret simple "-" si neutre dans le contexte
```

Le tiret simple `-` reste autorise dans les identifiants techniques (noms de fichiers, slugs, noms de branches git).

### 1.2 Aucun emoji

Pas d'emoji unicode dans le code, les commentaires, la documentation, les commits, les messages CLI, la narration, ou l'interface joueur. Les caracteres japonais kanji et kana sont autorises uniquement dans les champs de donnees prevus pour eux dans les schemas JSON canoniques.

### 1.3 Aucun argot otaku dans la voix du narrateur

Le narrateur du jeu et le system prompt du LLM ne doivent jamais utiliser de formules comme "kyaa", "dattebayo" en dehors des dialogues du personnage qui les emploie canoniquement, "le ninja le plus stylé", "épique", "trop OP", ou tout vocable de fan. La narration est neutre, descriptive, et adaptee au registre des sources.

### 1.4 Personnages canoniques fideles

Quand un personnage canonique parle, il parle selon sa personnalite documentee. Naruto utilise "dattebayo" en VO et est expansif. Sasuke est laconique. Itachi est mesure et sentencieux. Orochimaru est sibyllin et theatral. Sakura est directe. Le LLM doit respecter ces voix. La fiche `voice_profile` de chaque personnage canonique dans `characters.json` precise leur registre.

### 1.5 Terminologie

Tout terme technique de l'univers est stocke et reference en romaji. Les descriptions et la narration sont en francais.

```
Correct   : "Tu lances un Katon Goukakyuu en direction de l'adversaire."
Incorrect : "Tu lances une Boule de feu supreme en direction de l'adversaire."
```

Exception : si une expression romaji rendrait la phrase incomprehensible pour un joueur novice, une apposition francaise courte est autorisee la premiere fois qu'elle apparait dans une partie. Exemple : "Tu actives ton Sharingan, le don visuel hereditaire des Uchiha."

## 2. Regles de moteur

### 2.1 Aucune action n'est jamais refusee a priori

Le moteur n'a pas de liste noire de verbes ou d'actions. Toute intention exprimee par le joueur est resolue par le pipeline de resolution d'action defini dans `06_game_engine.md`. Le resultat peut etre :

- succes complet
- succes partiel avec consequences
- echec sans consequence majeure
- echec catastrophique avec mort, infirmite, exil, etc.
- impossibilite contextuelle motivee narrativement (la cible n'est pas presente, l'objet n'existe pas a cette epoque, etc.) qui n'est pas un refus mais un fait du monde

Ce dernier cas est une consequence simulationniste, pas une censure. Le moteur ne dit jamais "tu n'as pas le droit". Il dit "tu tentes ceci, voici ce qui se passe".

### 2.2 La reussite vient du chemin

Pour les objectifs majeurs (apprendre une technique de rang S, ressusciter quelqu'un, devenir Hokage, maitriser un dojutsu), le joueur ne peut pas reussir par un seul jet de des. Il doit trouver le chemin canonique via le systeme d'objectifs decrit dans `07_goal_system.md`. Les jets de des servent a resoudre les actions ponctuelles le long de ce chemin, pas a remplacer le chemin.

### 2.3 Le canon est la verite par defaut

Au demarrage d'une partie, l'etat du monde est exactement l'etat canonique a l'annee choisie par le joueur. Tous les personnages canoniques sont a leur position canonique. Tous les evenements futurs sont planifies selon le canon. Le canon devient un baseline a partir duquel la partie peut diverger.

### 2.4 Hierarchie de canonicite

Les sources sont ordonnees du plus fiable au moins fiable :

```
manga          (Naruto + Naruto Shippuden, Kishimoto)
boruto_manga   (Boruto Naruto Next Generations)
tbv            (Two Blue Vortex)
databook       (databooks officiels)
movie_canon    (films consideres canon par la franchise)
movie_filler   (films non canon)
anime_filler   (arcs filler de l'anime)
novel          (romans officiels)
game           (jeux video, y compris Storm Connections, Hikari Uchiha, etc.)
```

En cas de contradiction entre deux sources, la source la plus haute dans la hierarchie prevaut. Le profil de canonicite actif dans la partie determine quelles sources sont prises en compte.

### 2.5 Aucun appel a une API payante

Aucun module du projet ne doit faire d'appel reseau vers une API payante, Anthropic, OpenAI, Mistral, Google, ou autre. Le seul appel reseau autorise pendant le gameplay est local, vers le serveur llama.cpp ou Ollama qui tourne sur la machine du joueur. Les phases de scraping initial sont une exception encadree, decrite dans `05_data_pipeline.md`, et ne touchent que des sources publiques gratuites.

## 3. Regles de code

### 3.1 Langage et version

Python 3.11 minimum. Type hints obligatoires sur toutes les signatures publiques. `from __future__ import annotations` en tete de chaque fichier qui utilise des annotations.

### 3.2 Style

Formattage : ruff format (config par defaut), 100 colonnes maximum.
Linting : ruff check avec les regles E, F, W, I, N, UP, B, C4, SIM, RUF.
Imports : tries par isort via ruff, groupes stdlib, third party, first party.
Naming : snake_case pour fonctions et variables, PascalCase pour classes et types, SCREAMING_SNAKE_CASE pour constantes module level.

### 3.3 Documentation du code

Docstrings au format Google sur toutes les fonctions publiques et toutes les classes. Commentaires en francais sans accents pour eviter les soucis d'encodage. Pas de commentaire qui paraphrase le code, uniquement des commentaires qui expliquent le pourquoi.

### 3.4 Tests

pytest pour tous les tests. Couverture minimale 70 pour cent sur les modules `engine`, `canon`, et `persistence`. Les tests d'integration qui necessitent le LLM sont marques avec un marker pytest `requires_llm` et ne sont executes que sur demande.

### 3.5 Gestion des erreurs

Pas de `except Exception` nu. Toujours capturer le type precis. Les erreurs metier ont leurs propres classes derivees de `ShinobiError` definie dans `src/shinobi/errors.py`. Logging structlog avec niveau approprie a chaque erreur.

### 3.6 Determinisme et seeds

Toute composante qui utilise du hasard doit accepter un seed en parametre, lu depuis l'etat de partie. Ainsi, pour une meme partie chargee au meme tour, les resolutions sont reproductibles, ce qui facilite le debug et permet une fonctionnalite future de "rejouer un tour".

## 4. Regles de donnees

### 4.1 JSON canoniques

Les fichiers sous `data/canonical/` sont la source de verite des donnees du jeu. Ils sont versionnes avec le code. Toute modification passe par une revue.

Format : JSON UTF-8, indentation 2 espaces, cle triees alphabetiquement, fin de fichier avec une ligne vide. Validation pydantic au chargement.

### 4.2 Donnees scrapees

Les donnees brutes scrapees sont sous `data/raw/` et ne sont pas committees. Elles sont reproductibles via les scripts de `05_data_pipeline.md`. Une trace de scraping (URL, date, hash) est conservee pour audit dans `data/raw/_trace.jsonl`.

### 4.3 Embeddings

Les embeddings sont sous `data/embeddings/` et ne sont pas committes. Reconstructibles via `scripts/rebuild_embeddings.py`.

### 4.4 Sauvegardes

Les sauvegardes sont sous `data/saves/` et ne sont pas committees. Format SQLite par partie avec metadonnees JSON adjacentes pour l'index.

## 5. Regles de versioning

### 5.1 Git

Branche principale : `main`. Branches de feature : `feature/short-name`. Branches de bug : `fix/short-name`. Branches de refacto : `refactor/short-name`.

Commits : format conventionnel court, en anglais, sans emoji, sans em dash. Exemple :
```
feat(engine): add chakra exhaustion resolution
fix(rag): correct cosine similarity threshold
docs(canon): clarify canonicity hierarchy
```

### 5.2 Authorship des commits

**Strictement interdit** d'ajouter un trailer `Co-Authored-By: Claude <noreply@anthropic.com>` ou toute mention de Claude, Claude Code, Anthropic, ou autre AI assistant dans les commits, peu importe la quantite de code generee. L'auteur officiel est l'utilisateur humain.

**Strictement interdit** egalement d'ajouter dans les messages de commit des phrases du genre "Generated with Claude Code" ou "AI assisted". Le commit doit ressembler a un commit humain standard.

Cette regle s'applique a tous les commits, y compris les commits faits par Claude Code en mode autonome. Claude Code doit etre configure pour ne pas inserer ces trailers.

### 5.3 Versioning du projet

Semver. Version actuelle : 0.0.0. Pas de release publique prevue.

## 6. Securite et vie privee

### 6.1 Donnees joueur

Les sauvegardes ne contiennent que les donnees du jeu. Aucun envoi vers un service externe. Aucune telemetry.

### 6.2 Modeles telecharges

Les modeles GGUF et embeddings sont telecharges depuis Hugging Face. Le hash de chaque fichier est verifie contre une valeur attendue dans `scripts/download_models.py` pour detecter une corruption ou un fichier compromis.

### 6.3 Scraping respectueux

Les scripts de scraping respectent un delai entre requetes (1.5 secondes minimum) et le `robots.txt` des sites cibles. User-Agent identifiant le projet et un email de contact.
