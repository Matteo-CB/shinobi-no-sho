# 09. Systeme RAG

Le RAG (Retrieval Augmented Generation) est ce qui permet au LLM local de rester strictement fidele au canon de Naruto malgre les limites de sa fenetre de contexte. A chaque tour, on injecte dans son prompt uniquement les informations canoniques pertinentes pour la situation courante.

## 1. Principes

### 1.1 Le LLM n'invente pas, il narre

Le LLM ne doit jamais inventer une technique, un personnage, ou un lieu. Toute information factuelle de la narration vient soit de l'etat du jeu (geree par le moteur), soit d'un chunk recupere par le RAG, soit du voice_profile d'un personnage canonique. Si une information n'est pas dans le contexte injecte, elle n'existe pas pour la narration.

### 1.2 Recherche hybride

Le retrieval est hybride :

- recherche semantique sur les embeddings pour les requetes ouvertes
- recherche structuree sur SQLite pour les requetes precises (par village, par rang, par age, etc.)
- combinaison des deux pour les cas complexes

### 1.3 Contexte calibre

Le budget de contexte injecte par tour est limite (typiquement 4000 a 6000 tokens sur les 16000 disponibles, le reste etant pour l'historique narratif compresse, le system prompt, et la generation). Le retrieval doit etre selectif et pertinent.

## 2. Strategies de chunking

### 2.1 Par type de donnee

Chaque type d'entite canonique a une strategie de chunking adaptee.

**Personnages.** Un seul chunk par personnage de moins de 1500 tokens, contenant id, noms, role, era, traits, voice_profile resume, techniques cles. Les details secondaires sont dans des chunks complementaires associes.

**Techniques.** Un chunk par technique avec id, noms, rang, prerequisites, description, premieres apparitions, utilisateurs canoniques, contre-mesures. Environ 200 a 500 tokens par chunk.

**Clans.** Un chunk par clan avec histoire, kekkei genkai, membres notables par ere, status par ere. 500 a 1500 tokens.

**Villages.** Un chunk de presentation generale, plus des sous-chunks par ere (Konoha pendant la guerre, Konoha post Yondaime, Konoha apres invasion, etc.).

**Evenements de timeline.** Un chunk par evenement, mais les chunks d'evenements relatifs au meme arc sont taggees pour etre recuperes ensemble.

**Lore et regles.** Chunks decoupes par theme (chakra, hand seals, examens, missions, etc.).

**Dialogues exemplaires.** Un chunk par sample dialogue, indexe par character_id, pour le few-shot injection.

### 2.2 Implementation

`src/shinobi/rag/chunker.py` expose une fonction par type de donnee :

```python
def chunk_character(character: Character, related_data: RelatedData) -> list[Chunk]
def chunk_technique(technique: Technique) -> list[Chunk]
def chunk_clan(clan: Clan, related_data: RelatedData) -> list[Chunk]
...
```

Chaque chunk porte des metadonnees :

```python
class Chunk(BaseModel):
    id: str
    text: str
    type: ChunkType  # character, technique, clan, village, event, lore, dialogue
    source_id: str   # id de l'entite source
    canonicity: str
    era_relevance: list[str] | None
    village_relevance: list[str] | None
    character_relevance: list[str] | None
    metadata: dict[str, Any]
```

Les metadonnees servent au filtering pendant le retrieval.

## 3. Embeddings

### 3.1 Modele

BGE-M3 multilingue, dimension 1024, execution CPU via sentence-transformers. Ce modele gere correctement le francais, l'anglais, et le japonais romaji.

### 3.2 Indexation

Au demarrage du projet et apres chaque modification d'un dataset canonique, un script `scripts/rebuild_embeddings.py` regenere les embeddings :

```
1. Charger tous les datasets canoniques.
2. Pour chaque entite, generer ses chunks.
3. Generer les embeddings en batch.
4. Stocker dans la collection chromadb appropriee avec metadonnees.
5. Generer aussi la collection crossdomain qui contient tout, pour recherches transversales.
```

Le script supporte une option `--only` pour reindexer un type specifique.

### 3.3 Cache

Les embeddings une fois calcules sont mis en cache local pour eviter le recompute. Le cache key est le hash du texte du chunk plus le nom du modele d'embedding.

## 4. Vector store

### 4.1 ChromaDB

Une instance ChromaDB en mode persistent local sous `data/embeddings/`. Plusieurs collections :

```
techniques            tous les chunks de techniques
characters            tous les chunks de personnages
clans
villages
events
lore
dialogue_examples     chunks de dialogues exemplaires par character_id
crossdomain           tout, pour recherche transversale
```

### 4.2 Distance et seuils

Distance cosine. Score de similarite calcule en `1 - cosine_distance`. Seuils types :

```
TRES_PERTINENT       > 0.75
PERTINENT            0.55 a 0.75
FAIBLEMENT_PERTINENT 0.40 a 0.55
NON_PERTINENT        < 0.40
```

Le retriever rejette les chunks sous 0.40 par defaut.

## 5. Retrieval

### 5.1 Architecture du retriever

`src/shinobi/rag/retriever.py` expose une classe `Retriever` qui encapsule la logique :

```python
class Retriever:
    def query_for_turn(self, context: TurnContext) -> RetrievedContext: ...
    def query_specific(self, query: str, type: ChunkType, top_k: int = 5) -> list[Chunk]: ...
    def query_by_filter(self, filters: dict) -> list[Chunk]: ...
    def query_dialogue_examples(self, character_id: str, situation: str, top_k: int = 3) -> list[Chunk]: ...
```

### 5.2 query_for_turn

C'est la methode la plus importante. Pour chaque tour, elle compile un contexte ciblé.

Etape 1 : analyser le `TurnContext`

```
- ou est le joueur ?
- avec qui parle-t-il ?
- quelle action vient-il de tenter ?
- quels sont ses objectifs actifs ?
- quels evenements canon sont imminents ?
```

Etape 2 : construire un ensemble de requetes

```
- requete principale sur la derniere action (semantique sur crossdomain)
- requete sur les PNJ presents (filter sur character chunks par id)
- requete sur le lieu (filter sur village ou location)
- requete sur les techniques tentees ou nommees (filter sur technique chunks)
- requete sur les rumeurs en circulation pertinentes
- requete sur les voice_profiles des PNJ presents
```

Etape 3 : agreger et deduplique les chunks recuperes

Etape 4 : trier par pertinence (combinant score semantique et priorites contextuelles)

Etape 5 : tronquer au budget de tokens disponible

Etape 6 : formater pour injection dans le prompt

### 5.3 Filtres structures

Les filtres ChromaDB permettent par exemple :

```python
retriever.query_specific(
    query="techniques de medecine",
    type=ChunkType.technique,
    metadata_filter={
        "category": "iryo_ninjutsu",
        "rank": {"$in": ["A", "B"]},
        "canonicity": {"$in": ["manga", "databook"]}
    },
    top_k=10
)
```

### 5.4 Recherche structuree complementaire

Pour des requetes precises ("liste tous les Hyuuga vivants en l'an 9 a Konoha"), on n'utilise pas les embeddings mais une requete SQL sur le mirror SQLite des datasets canoniques. Le retriever expose des methodes structurees :

```python
retriever.query_living_characters_at(year=9, village="konohagakure", clan="hyuuga")
retriever.query_techniques_for_jonin(natures=["katon"], max_rank="A")
retriever.query_npc_location_at(character_id="orochimaru", year=9)
```

## 6. Formattage du contexte injectee

### 6.1 Format

`src/shinobi/rag/formatter.py` produit une chaine structuree pour le prompt :

```
[CONTEXTE CANONIQUE]

Personnages presents :
  - Hatake Kakashi (jonin de Konoha, an 13)
    Voice profile : laconique, references aux livres de Jiraiya, lit Icha Icha
    Sample : "Yo. Desole, je me suis perdu sur le chemin de la vie."
    Etat actuel : sensei de l'equipe 7, recent decede son rival Maito Gai (en match amical)

Techniques pertinentes :
  - Chidori (raiton, A rang, prerequisites : Sharingan ou maitrise tres avancee de raiton)
    Description : Lance unique de chakra raiton concentre dans la main. Vitesse necessaire pour eviter contre-attaque.
    Utilisateurs canoniques : Kakashi (createur), Sasuke

Lieu :
  - Terrain d'entrainement numero 3 de Konoha
    Description : Trois poteaux marquant le lieu ou se sont initialement reunis les eleves de Sarutobi Hiruzen.

Evenements imminents :
  - Examen Chunin de Konoha, prevu dans 3 semaines.

[FIN CONTEXTE]
```

### 6.2 Compression

Si le budget de tokens est depasse, le formatter applique des reductions :

```
1. Couper les sample lines des voice_profiles
2. Couper les descriptions des techniques au resume
3. Couper les details des relations
4. Garder uniquement les noms et roles des PNJ secondaires
```

## 7. Few-shot dialogues

### 7.1 Pourquoi

Le LLM, meme avec le voice_profile en contexte, peut deriver vers un registre generique. Pour ancrer le rendu fidele, on lui injecte 2 a 3 sample lines effectifs du PNJ qui parle, choisies parmi celles qui matchent le mieux le ton de la situation.

### 7.2 Selection

`retriever.query_dialogue_examples` interroge la collection `dialogue_examples` en filtrant par character_id et en faisant une recherche semantique sur la situation courante. Top 3.

## 8. Cache

Les requetes de retrieval pour des contextes similaires peuvent etre identiques. Un cache LRU en memoire stocke les resultats par hash de la query. Invalide a la reindexation des embeddings.

## 9. Performance

Cibles :

```
embedding d'une query           < 50 ms
recherche dans une collection   < 100 ms
agregation pour un tour         < 500 ms
formattage final                < 50 ms
total par tour                  < 700 ms
```

Si le budget est depasse, prioriser :

- moins de queries (regrouper)
- top_k plus petit
- collections plus ciblees

## 10. Tests

Tests unitaires :

- chunking de personnages, techniques, clans (verifier la conformite des metadonnees)
- generation d'embeddings reproductible
- filtres metadonnees
- formattage du contexte avec et sans truncation

Tests d'integration :

- retrieval pour un tour type avec contexte realiste
- mesure du taux de pertinence sur un dataset de queries de reference

Tests qualitatifs :

- verifier que pour une requete sur Itachi, on recupere bien le voice_profile d'Itachi
- verifier que pour une requete sur Edo Tensei, on recupere bien la technique, son createur, et ses utilisateurs canoniques
- verifier qu'un filtre de canonicite restreint exclut effectivement les autres sources
