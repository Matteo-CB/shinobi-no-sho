# 05. Pipeline d'acquisition des donnees

Construction des datasets canoniques depuis les sources brutes. C'est la phase 1 du projet et la plus longue.

## 1. Strategie globale

L'objectif est d'aboutir a des JSON canoniques exhaustifs, valides, et coherents, couvrant l'ensemble des sources actives (manga, boruto, two blue vortex, databooks, films canon, films non canon, fillers, novels, jeux video pertinents).

L'approche se fait en quatre etapes sequentielles :

```
1. SCRAPING
   recolte automatisee des donnees brutes depuis les sources publiques

2. PARSING
   extraction structuree depuis les pages brutes vers du JSON intermediaire

3. ENRICHISSEMENT
   completion des champs manquants par croisement de sources et generation LLM

4. VALIDATION
   verification de coherence et qualite, correction manuelle des cas ambigus
```

Chaque etape est idempotente. On peut relancer une etape sans casser les precedentes. Le pipeline complet est orchestrable via `scripts/build_canonical_jsons.py` qui execute toutes les etapes dans l'ordre.

## 2. Sources

### 2.1 Narutopedia (en.naruto.fandom.com)

Source principale pour la couverture exhaustive. Contient la quasi totalite des entites nommees du franchise. Qualite variable, parfois imprecise sur les details, mais excellente couverture.

Acces : scraping HTTP avec respect du `robots.txt` et delai de 1.5 seconde minimum entre requetes. User-Agent identifiant le projet.

Structure exploitee :
- pages de personnages : `/wiki/[Name]`
- pages de techniques : `/wiki/[Technique_Name]`
- pages de clans : `/wiki/[Clan]_Clan`
- pages de villages : `/wiki/[Village]`
- categories : `/wiki/Category:[Name]` pour iterer

Strategie : commencer par les pages de categories pour obtenir les listes exhaustives, puis scraper chaque page individuelle.

### 2.2 Databooks officiels

Quatre databooks officiels publies par Shueisha. Donnent les stats numeriques officielles, les classifications, et des informations exclusives. Pas accessibles en scraping direct. Plusieurs options :

- recuperer les pages databook synthetisees sur Narutopedia (champ `Databook` souvent present)
- utiliser des dumps communautaires de fans qui ont consolide les databooks
- scanner manuellement les exemplaires si possedes

Le projet privilegie la premiere et la deuxieme option. Toute donnee databook est marquee `canonicity: databook` et le numero du databook est dans `sources`.

### 2.3 Sources annexes

- pages dediees au Boruto manga sur Narutopedia
- pages dediees a Two Blue Vortex (recentes, parfois incompletes)
- pages dediees aux films
- transcripts d'episodes pour les fillers
- listings dedies aux jeux video (CyberConnect2 wikis, fan databases) pour Storm Connections et autres

## 3. Etape 1 : scraping

### 3.1 Architecture du scraper

`scripts/scrape_narutopedia.py` utilise httpx async avec un semaphore pour limiter la concurrence a 3 requetes simultanees, et un delai aleatoire de 1.5 a 2.5 secondes entre requetes.

Cache local agressif : chaque page est sauvegardee dans `data/raw/narutopedia/pages/[hash_url].html`. Le scraper verifie le cache avant toute requete et ne refetche que si flag `--force` ou si le cache a plus de 30 jours.

### 3.2 Flux

```
1. Charger les listes de categories cibles
2. Pour chaque categorie, parcourir les pages de listing
3. Extraire les URLs d'entites individuelles
4. Pour chaque entite, scraper la page complete
5. Sauvegarder le HTML brut + une trace dans _trace.jsonl
```

### 3.3 Categories cibles minimales

```
Category:Characters
Category:Jutsu
Category:Clans
Category:Villages
Category:Tailed_Beasts
Category:Kekkei_Genkai
Category:Kekkei_Mora
Category:Hiden
Category:Tools
Category:Weapons
Category:Locations
Category:Organizations
Category:Events
```

Sous-categories explorees recursivement avec une profondeur limite pour eviter les boucles.

### 3.4 Trace de scraping

Chaque page scrapee genere une ligne dans `data/raw/_trace.jsonl` :

```json
{"url": "https://en.naruto.fandom.com/wiki/Naruto_Uzumaki", "fetched_at": "2026-05-01T14:23:11Z", "status": 200, "size_bytes": 142883, "sha256": "..."}
```

## 4. Etape 2 : parsing

### 4.1 Architecture

`scripts/parse_narutopedia.py` lit les pages HTML brutes et produit du JSON intermediaire dans `data/raw/narutopedia/parsed/`. Le format intermediaire est plus libre que les schemas canoniques finaux : il accepte des champs en l'etat tels qu'ils sont presents sur la wiki.

### 4.2 Strategie d'extraction

Trois extracteurs complementaires :

```
1. Extracteur d'infobox
   Les wikis Fandom utilisent des templates d'infobox structurees.
   Extraction par parsing du HTML de la table d'infobox (selecteurs CSS connus).
   Donne les champs structures : nom, age, rang, debut, statut, affiliations, etc.

2. Extracteur de prose
   Sections "Background", "Personality", "Abilities", "Trivia", etc.
   Extraction du texte brut nettoye via trafilatura.
   Donne les champs descriptifs.

3. Extracteur de listes
   Listes de techniques connues, listes de membres de clan, etc.
   Extraction par parsing des balises ul/ol et liens internes.
   Donne les references croisees.
```

### 4.3 Mapping vers les schemas canoniques

Le parser produit un JSON intermediaire dont la structure suit la wiki, pas notre schema cible. La conversion vers le schema canonique se fait a l'etape 3 (enrichissement), parce qu'elle requiert des decisions de mapping qui peuvent necessiter le LLM.

Exemples de mapping non triviaux :

- "Affiliations" sur Narutopedia est une liste d'ids de villages et organisations a mapper sur nos `current_village_by_era` et organisations
- "Jutsu" est une liste de techniques connues a mapper sur `techniques_known_by_era` (mais sans info temporelle, l'enrichissement doit deviner)
- "Stats" du databook est une table 8 valeurs a mapper sur nos stats etendues

## 5. Etape 3 : enrichissement

### 5.1 Generation par LLM augmente

Pour chaque entite parsee, le pipeline appelle le LLM local avec :

- la donnee parsee brute
- le schema cible pydantic en JSON Schema
- des extraits de la prose source pour contexte
- des references croisees deja resolues (pour eviter les ids inventes)

Le LLM produit un JSON conforme au schema, qu'on valide pydantic. Les erreurs declenchent un retry avec feedback.

### 5.2 Resolution des references croisees

Avant d'enrichir une entite avec des references (par exemple, lister les techniques connues d'un perso par leurs ids), on doit avoir un index global des ids deja attribues. Le pipeline fonctionne en deux passes :

```
Passe 1 : creer les ids canoniques de toutes les entites (slug deterministe depuis le nom canonique)
Passe 2 : enrichir chaque entite en utilisant les ids de la passe 1
```

L'id canonique est un slug deterministe de la forme `[clan_or_prefix]_[name]`, par exemple `uchiha_sasuke`, `katon_goukakyuu_no_jutsu`. La fonction de slug est dans `src/shinobi/utils/slug.py` et est utilisee a chaque etape du pipeline pour garantir la coherence.

### 5.3 Generation des stats par ere

Pour les personnages, les stats databook donnent souvent une seule valeur "actuelle" sans precision d'ere. Le LLM, augmente par la prose biographique, doit :

- detecter les eres pertinentes pour ce perso (academie, debut Shippuden, post-guerre, etc.)
- extrapoler des valeurs raisonnables pour chaque ere en se basant sur l'evolution decrite
- conserver les stats databook officielles a l'ere de leur publication

Cette extrapolation est marquee avec `confidence: extrapolated` sur les snapshots qui ne viennent pas directement d'une source.

### 5.4 Voice profiles

Pour chaque personnage avec presence dialoguee, le LLM extrait :

- des sample lines tirees des transcripts ou citations dans les sections "Personality" ou "Trivia"
- les patterns syntaxiques recurrents
- les verbal tics
- le registre

Stocke dans `voice_profiles.json`.

### 5.5 Timeline events

Les evenements de timeline ne sont pas tous explicites sur la wiki. Le pipeline genere un draft de timeline events depuis :

- pages de la wiki listant les arcs et leurs episodes/chapitres
- pages des batailles
- pages des morts notables
- pages des promotions de rang

Pour chaque evenement, le LLM produit le format `timeline_events.json` avec preconditions et outcomes deduits de la prose. Ces drafts requièrent ensuite une validation manuelle parce que les preconditions structurees demandent une comprehension causale fine.

## 6. Etape 4 : validation

### 6.1 Validation pydantic

`scripts/validate_canon.py` charge tous les JSON via les modeles pydantic. Toute erreur de structure est rejetee.

### 6.2 Validation de coherence

Apres validation pydantic, des regles metier sont appliquees :

```
- Tout id reference dans une liste de cross-references doit exister
- Tout perso a une birth_year compatible avec ses apparitions canon
- Tout perso decede a une death_year > birth_year
- Tout perso jinchuuriki a un tailed_beast existant et la reciproque dans tailed_beasts.json
- Tout evenement de timeline a des preconditions referencant des perso vivants a la date prevue
- Toute technique enseignable par un perso est dans la liste de ses techniques connues
- Tout chef de village dans kage_lineage existe et a des dates compatibles
- Toute date d'evenement est posterieure aux dates de naissance des participants
```

### 6.3 Rapport de couverture

`scripts/audit_canonicity.py` produit un rapport markdown :

```
Couverture par source :
  manga              characters: 487  techniques: 312  events: 124
  boruto_manga       characters: 156  techniques: 88   events: 47
  databook           stats fournies pour 234 characters
  movie_canon        characters: 23   events: 11
  game               characters: 18 (Storm Connections), techniques: 12

Manques detectes :
  characters sans birth_year : 23
  techniques sans canonical_users : 47
  events sans preconditions structurees : 12

Reference brisees :
  characters.uzumaki_boruto.key_relationships[3].with = "uchiha_kawaki" non trouve
  ...
```

Ce rapport guide les sessions de correction manuelle.

### 6.4 Correction manuelle

Les cas ambigus, contradictions inter-sources, et trous de donnees sont fixes a la main dans une session de revue. Le commit des corrections cite le rapport d'audit.

## 7. Ordre de construction recommande

Pour gerer les dependances entre datasets, la construction se fait dans l'ordre suivant :

```
1. world_rules.json         (ne depend de rien, ecrit manuellement, calibre)
2. natures.json              (statique, ecrit manuellement)
3. ranks.json                (statique, ecrit manuellement)
4. eras.json                 (statique, ecrit manuellement)
5. jutsu_categories.json     (statique, ecrit manuellement)
6. villages.json             (depend de futurs character ids, mais skeleton possible)
7. clans.json                (skeleton possible)
8. organizations.json        (skeleton possible)
9. characters.json           (gros morceau, scraping + enrichissement)
10. tailed_beasts.json       (depend de characters.json pour jinchuuriki)
11. kekkei_genkai.json       (depend de clans.json et characters.json)
12. kekkei_mora.json
13. hiden.json
14. techniques.json          (depend de characters.json pour canonical_users)
15. weapons_tools.json       (depend de characters.json pour wielders)
16. locations.json
17. timeline_events.json     (depend de tout le reste pour preconditions)
18. voice_profiles.json      (depend de characters.json)
```

Le script `build_canonical_jsons.py` orchestre cet ordre et permet de reconstruire un dataset specifique avec toutes ses dependances.

## 8. Estimation d'effort

Sur la base de l'experience accumulee sur des wikis comparables :

```
Scraping initial complet narutopedia       8 a 12 heures (selon delai)
Parsing automatique                        1 a 2 heures
Enrichissement LLM (14B local)             10 a 30 heures de generation
Validation et correction manuelle          40 a 80 heures de revue humaine
```

Le poste de revue humaine est de loin le plus couteux. Une strategie viable est de prioriser les entites par importance narrative : tous les perso jouables canon majeurs et toutes les techniques de rang B+ sont reverifiees a la main, le reste peut rester dans un etat moins poli avec un flag `quality: auto`.

## 9. Idempotence et reproductibilite

Toutes les etapes sont conçues pour etre relancables :

```
scrape       cache local, ne refetche que si force ou cache vieux
parse        regenere le parse depuis la cache
enrich       prend en input le parse, regenere le canonique
validate     pure verification, ne modifie rien
```

Une session typique de mise a jour ressemble a :

```
$ python scripts/scrape_narutopedia.py --refresh-categories
$ python scripts/parse_narutopedia.py
$ python scripts/build_canonical_jsons.py --only characters,techniques
$ python scripts/validate_canon.py
$ python scripts/audit_canonicity.py > audit.md
$ python scripts/rebuild_embeddings.py --only characters,techniques
```

## 10. Politique de respect des sources

Le scraper respecte strictement :

- le `robots.txt` du domaine
- un User-Agent identifiant le projet et un email de contact
- un delai minimum entre requetes
- une limitation de concurrence a 3 requetes simultanees
- pas de bypass d'eventuels Cloudflare ou captchas

Le projet ne redistribue pas les pages scrapees. Les datasets canoniques produits sont une oeuvre derivee transformee a usage personnel et ne sont pas publies.
