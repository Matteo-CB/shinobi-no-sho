# 10. Integration LLM

Comment le LLM local est utilise, ce qu'on lui envoie, ce qu'on attend de lui, et comment on parse ses reponses.

## 1. Roles du LLM

Le LLM intervient dans plusieurs roles distincts. Chaque role a son propre system prompt et son propre schema de sortie.

```
ROLE_NARRATOR             narrer un tour de jeu, faire parler les PNJ, decrire les consequences
ROLE_DIALOGUE             produire des repliques de PNJ specifiques sans narration
ROLE_GOAL_PATHFINDER      proposer un chemin canonique vers un objectif declare
ROLE_BREADCRUMB_GENERATOR generer un breadcrumb pour un objectif et son prix
ROLE_RUMOR_GENERATOR      generer une rumeur a propos d'un evenement diffuse
ROLE_WORLD_RESOLVER       resoudre une divergence canonique complexe
ROLE_CHARACTER_INTERPRETER  interpreter une action libre du joueur en intention structuree
ROLE_DIVERGENCE_NARRATOR    raconter ce qui se passe a la place d'un evenement annule
```

Chaque role a un module dedie sous `src/shinobi/llm/`.

## 2. Client LLM

`src/shinobi/llm/client.py` expose un client HTTP compatible OpenAI :

```python
class LLMClient:
    async def generate(
        self,
        messages: list[Message],
        schema: dict | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        stream: bool = False,
    ) -> LLMResponse: ...
    
    async def generate_streaming(
        self,
        messages: list[Message],
        schema: dict | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> AsyncIterator[StreamChunk]: ...
```

Le client gere les retries (3 tentatives avec backoff), les timeouts (60 secondes par defaut, 180 pour generation longue), et la validation des sorties JSON contre le schema fourni.

## 3. Format des prompts

### 3.1 Structure generale

Tous les prompts suivent une structure :

```
SYSTEM
[role-specific system prompt]

USER
[contexte structure]

[contexte rag]

[etat de jeu pertinent]

[instruction concise]

ASSISTANT
[reponse generee, conforme au schema]
```

### 3.2 System prompt narrator (le plus important)

```
Tu es le narrateur omniscient d'un simulateur de vie dans l'univers de Naruto. Tu narres des
tours de jeu en respectant strictement les regles suivantes.

REGLES DE STYLE :
- Aucun tiret cadratin ou em dash dans la sortie. Utilise des virgules, points, parentheses.
- Aucun emoji.
- Aucun argot otaku ou expression de fan dans la voix narrative. Pas de "epique", pas de
  "trop stylé", pas de "OP". Le narrateur reste sobre et descriptif.
- La narration est en francais, mais les noms de techniques, lieux, villages, clans,
  personnages, et concepts sont en romaji.

REGLES DE FIDELITE :
- Tu n'inventes jamais une technique, un personnage, ou un lieu. Tout ce que tu nommes
  doit apparaitre dans le CONTEXTE CANONIQUE injecte ou dans l'etat de jeu.
- Quand un personnage canonique parle, il parle exactement selon son voice_profile fourni
  en contexte. Le sample line est la reference de ton.
- Si tu ne trouves pas l'information dans le contexte, tu n'inventes pas. Tu ecris une
  description neutre qui ne nomme pas l'element manquant.

REGLES DE NARRATION :
- Tu decris ce qui se passe en consequence de l'action du joueur, en t'appuyant sur le
  resultat mecanique fourni par le moteur (succes, echec, degats, etc.).
- Tu ne resous jamais une action toi-meme : c'est le moteur qui decide. Tu narres ce que
  le moteur a decide.
- Tu ne refuses jamais une action. Si une action a ete jugee impossible contextuellement
  par le moteur, tu narres simplement le constat (la cible n'est pas la, l'objet n'existe
  pas, etc.).
- Tu n'avances pas le temps de toi-meme : tu narres uniquement la duree fournie par le
  moteur.

REGLES DE SORTIE :
- Tu reponds toujours en JSON conforme au schema fourni.
- Le champ narrative contient la description du tour, en francais propre.
- Le champ npc_dialogue contient les eventuelles repliques de PNJ presents.
- Le champ proposed_actions contient 4 a 7 propositions d'actions pour le tour suivant.
  Chacune est une formulation en francais, plus un type structure pour le moteur.
- Le champ world_observations contient les observations du joueur sur le monde (rumeurs
  entendues, indices remarques).

Si le joueur a ecrit une action libre dont l'intention n'est pas claire, tu peux dans le
champ clarification_request demander une precision avant de narrer.
```

### 3.3 Schema de sortie narrator

```json
{
  "type": "object",
  "required": ["narrative", "proposed_actions"],
  "properties": {
    "narrative": {"type": "string", "minLength": 50},
    "npc_dialogue": {
      "type": "array",
      "items": {
        "type": "object",
        "required": ["character_id", "line"],
        "properties": {
          "character_id": {"type": "string"},
          "line": {"type": "string"},
          "tone": {"type": "string"}
        }
      }
    },
    "proposed_actions": {
      "type": "array",
      "minItems": 4,
      "maxItems": 7,
      "items": {
        "type": "object",
        "required": ["label_fr", "action_type"],
        "properties": {
          "label_fr": {"type": "string"},
          "action_type": {"type": "string"},
          "parameters": {"type": "object"},
          "estimated_difficulty": {"type": "string"},
          "estimated_duration": {"type": "string"}
        }
      }
    },
    "world_observations": {
      "type": "array",
      "items": {"type": "string"}
    },
    "clarification_request": {"type": "string"}
  }
}
```

### 3.4 System prompt goal pathfinder

```
Tu es un strategiste de l'univers de Naruto. On te donne :
- un objectif declare par un personnage
- l'etat actuel de ce personnage
- le contexte canonique recupere par RAG (techniques, personnages, evenements pertinents)
- la date courante en in-game

Ta tache est de proposer le PROCHAIN PAS du chemin canonique vers cet objectif. Pas
le chemin entier. Juste l'indice immediat.

REGLES :
- L'indice doit etre canoniquement coherent. Si l'objectif a deja ete atteint dans le
  canon, le chemin doit s'inspirer de comment cela s'est fait canoniquement.
- L'indice doit etre actionnable. Pas de generalite philosophique.
- L'indice doit avoir un PRIX. Personne ne donne d'information importante gratuitement.
- L'indice doit etre PARTIEL. Il pointe vers la prochaine etape, pas vers le succes.
- Tu peux proposer 1 a 3 sources d'information distinctes (PNJ, lieux, methodes), avec
  un prix different pour chacune.
- Le prix doit etre coherent avec qui detient l'information. Un Anbu demande des faveurs,
  pas de l'argent. Un yakuza demande de l'intimidation. Orochimaru demande des cobayes.

Tu reponds en JSON conforme au schema.
```

Schema correspondant :

```json
{
  "type": "object",
  "required": ["sources_of_information"],
  "properties": {
    "interpretation": {"type": "string"},
    "sources_of_information": {
      "type": "array",
      "minItems": 1,
      "maxItems": 3,
      "items": {
        "type": "object",
        "required": ["source_type", "source_description", "price", "indice_unlocked"],
        "properties": {
          "source_type": {"enum": ["npc", "location", "scroll", "rumor_mill", "self_research"]},
          "source_id": {"type": "string"},
          "source_description": {"type": "string"},
          "price": {
            "type": "object",
            "required": ["type", "description"],
            "properties": {
              "type": {"enum": ["money", "favor", "sub_mission", "reputation", "secret", "physical", "moral", "political", "none"]},
              "amount": {"type": "number"},
              "description": {"type": "string"}
            }
          },
          "indice_unlocked": {
            "type": "object",
            "required": ["description", "completion_conditions"],
            "properties": {
              "description": {"type": "string"},
              "completion_conditions": {
                "type": "array",
                "items": {
                  "type": "object",
                  "required": ["type", "parameters"],
                  "properties": {
                    "type": {"type": "string"},
                    "parameters": {"type": "object"}
                  }
                }
              }
            }
          }
        }
      }
    }
  }
}
```

### 3.5 System prompt character interpreter

Quand le joueur ecrit une action libre en langage naturel, le LLM la traduit en intention structuree :

```
Tu es un interpreteur d'actions. Le joueur ecrit une intention en francais. Tu la
classifies dans un type d'action structuree, tu en extrais les parametres pertinents,
et tu signales les ambiguites.

Tu reponds en JSON. Si l'action est claire, tu remplis tous les champs. Si elle est
ambigue, tu remplis un champ clarification_questions avec 1 ou 2 questions courtes.
```

## 4. Voice profiling en pratique

### 4.1 Injection du voice_profile

Avant chaque appel au narrator, pour chaque PNJ present qui pourrait parler, le retriever recupere son voice_profile et 2 sample lines pertinentes a la situation. Le formatter les inclut dans le contexte sous une section dediee :

```
PNJ presents et leur voix :
  - Hatake Kakashi (jonin, Konoha)
    Registre : laconique, references litteraires, ironie sobre
    Tic verbal : aucune signature forte, mais utilise frequemment "Saa..." pour temporiser
    Vocabulaire a utiliser : neutre, expressions militaires, references a Icha Icha quand l'ambiance le permet
    Sample 1 : "Yo. Desole, je me suis perdu sur le chemin de la vie."
    Sample 2 : "Au sein des shinobi, ceux qui violent les regles sont consideres comme des dechets, mais ceux qui abandonnent leurs camarades sont pires que des dechets."
    A eviter : ton enjoue, vocabulaire de fan, references inadaptees a l'ere
```

### 4.2 Garde-fous

Si malgre le voice_profile le LLM produit une replique hors registre, deux mecanismes :

- post-validation : un detecteur de patterns interdits (tirets, emoji, argot) verifie la sortie. Si une violation est detectee, retry avec un message corrigeant explicitement.
- regen avec correction : le client peut regenerer une replique specifique avec un prompt court "reformule cette replique de [character_id] sans utiliser X, Y, Z".

## 5. Streaming

Pour la CLI, la generation est streamee. Le module `cli/streaming_display.py` affiche les tokens au fur et a mesure qu'ils arrivent. La validation JSON ne peut se faire qu'a la fin du stream, donc l'affichage progressif n'est applique qu'au champ `narrative`. Les autres champs (proposed_actions, etc.) apparaissent une fois le stream fini.

## 6. Gestion d'erreurs

### 6.1 JSON invalide

Si la sortie n'est pas du JSON valide ou ne respecte pas le schema, le client retry une fois avec un message system additionnel "Ta derniere sortie n'etait pas conforme. Le schema attendu est : ... Reformule.". Si le second essai echoue, le client renvoie une erreur que le module narration gere en proposant au joueur de re-tenter le tour.

### 6.2 Timeout

Sur timeout, le client retry une fois. Si echec, il bascule vers le modele de secours (8B) si configure.

### 6.3 Censure modele

Qwen3 a quelques garde-fous internes. Pour les sujets que le moteur soumet legitimement (violence, mort, manipulation politique), un system prompt ferme l'evite generalement. Pour les rares cas ou le modele refuse, le client capte la reponse de refus et la transforme en narration neutre via un fallback. Le projet n'utilise pas de jailbreak. Si le sujet est legitimement bloque (contenu sexuel impliquant des mineurs notamment, qui est une limite ethique non negotiable), la narration reste sobre.

## 7. Resume narratif compresse

L'historique narratif d'une partie peut depasser le contexte. Strategie :

```
1. Garder les 3 derniers tours en clair dans le contexte.
2. Au-dela, generer un resume compresse (200 a 400 tokens par segment de 5 tours).
3. Resumes des resumes pour les longues parties (compression hierarchique).
```

Un module `llm/summarization.py` gere ces compressions avec un prompt dedie.

## 8. Tests

Tests unitaires :

- parsing JSON strict
- detection de patterns interdits dans la sortie
- compression d'historique

Tests d'integration (marker requires_llm) :

- narration d'un tour simple, verifier conformite schema
- pathfinder pour un objectif simple, verifier conformite et coherence canon
- voice profiling sur 3 personnages canon connus

Tests qualitatifs (non automatises mais documentes) :

- comparer 10 sorties narrator a la baseline canon attendue
- verifier l'absence de tirets, emoji, argot sur 100 generations consecutives
