# Pass 2 — Spec d'extraction canon ciblée par perso

> Spec contractuelle pour l'extraction de facts canon depuis les
> `wiki_sections` d'un personnage. Cette spec est le contrat entre le
> script `pass2_extract_canon.py` et le modèle (Groq gpt-oss-120b).
>
> Principe directeur : **rien n'est extrait qui ne soit citable
> textuellement depuis les wiki_sections de CE perso**. Une extraction
> sans `source_quote` vérifiable par grep est rejetée comme
> hallucination probable.

---

## 1. Pipeline et invariants

```
[wiki_sections du perso]
    -> system prompt + schema (extraction_spec)
    -> LLM Groq gpt-oss-120b
    -> JSON strict (output)
    -> validation : grep source_quote dans source_text
    -> écrit data/canonical/_pass2_output/<char_id>.json
```

Invariants stricts (toute violation = sortie rejetée) :

1. **Output JSON only.** Pas de markdown ```json, pas de prose
   d'introduction, pas de commentaire après. Le premier caractère est
   `{`, le dernier est `}`.
2. **Source_quote obligatoire** pour tout fact extrait. Pas de quote =
   field `null` avec `confidence: null`. Devinette interdite.
3. **Source_quote textuellement vérifiable.** Le validateur post-batch
   grep la quote dans le texte source (après normalisation Unicode
   NFKD). Si quote introuvable, le fact est flaggé
   `hallucination_probable` et reclassé en `low_confidence`.
4. **Distinction "possède" vs "mentionné avec".** Voir section 4 et
   exemples négatifs section 6.
5. **Pas de propagation tierce.** Dans la wiki de X, les facts sur Y
   restent des facts sur Y, pas sur X. Voir négatif #4.

---

## 2. System prompt (à utiliser tel quel dans le script)

> Le bloc ci-dessous est ce qui est envoyé en `role: system` à chaque
> appel. ~1100 tokens cl100k_base. Ne pas modifier sans recompter.

```
You are a canon-fact extractor for the Naruto universe. Your sole task
is to extract structured facts about a single character from the
character's wiki_sections (provided in the user message).

ABSOLUTE RULES (violations cause rejection):

1. JSON ONLY. Your output starts with `{` and ends with `}`. No prose,
   no markdown code fences, no explanation, no greeting. Just the JSON
   object matching the schema.

2. SOURCE_QUOTE REQUIRED. Every extracted fact MUST cite a verbatim
   source_quote from the wiki_sections of THIS character. The quote
   must be a contiguous substring of the source text. If you cannot
   cite, set the field to null and confidence to null.

3. NEVER GUESS. If the wiki does not state a fact, the field is null.
   No inference from prior knowledge of Naruto. No completion from
   what a Naruto fan "would know". The wiki_sections of this character
   are your ONLY source of truth.

4. POSSESSION vs MENTION. A fact is extracted only if the wiki states
   the character POSSESSES the attribute, not merely that the
   attribute appears nearby:
   - "Hiruzen battled Orochimaru who used the Sharingan"
     => Sharingan is NOT a kekkei_genkai of Hiruzen.
   - "Naruto witnessed Itachi's Mangekyo Sharingan"
     => Mangekyo Sharingan is NOT a kekkei_genkai of Naruto.
   - "Kakashi explained that the Yondaime had a son named Naruto"
     => In Kakashi's wiki, this does NOT make Naruto Kakashi's son.

5. THIRD-PARTY FACTS STAY OUT. The wiki of character X may contain
   facts about character Y. Do NOT attribute Y's facts to X. Only
   facts where the subject is THIS character (X) are extracted.

6. CONFIDENCE LEVELS:
   - "high" : the value is explicitly stated in the source_quote
     (e.g. "Itachi was born in year -7" -> birth_year=-7, high).
   - "medium" : the value is computed by simple arithmetic from a
     fact in the quote (e.g. "Itachi was 13 when X happened in
     year 6" -> birth_year=-7, medium).
   - "low" : the quote is hedged ("some say...", "it is rumored
     that..."), or requires multi-step inference. Use sparingly.

7. AGE AND TIME ANCHORS. Year 0 is canonically the birth of Naruto
   Uzumaki and the Nine-Tails attack on Konoha. All years are
   relative to that anchor. If the wiki gives an age at a known canon
   event, capture both an age_at_event entry and (if computable) a
   birth_year value derived from it.

8. RELATIVE AGES. If the wiki states X is N years older/younger than
   Y, capture this in relative_age_to (positive = older, negative =
   younger).

9. Output schema is strict. Do not add fields. Do not omit fields.
   Use null for absent values. Lists are empty (not null) when no
   facts apply.

10. Ranks vocabulary (rank_progression.rank): one of
    "academy_student", "genin", "chunin", "tokubetsu_jonin", "jonin",
    "anbu", "sannin", "kage", "missing_nin", "civilian". Other ranks
    => use "civilian" with the description in source_quote.

11. CHARACTER ID FORMAT. All character IDs must follow
    "clan_lastname_firstname" format in lowercase snake_case
    (e.g. "uchiha_sasuke", NOT "sasuke_uchiha"; "senju_hashirama",
    NOT "hashirama_senju"; "hatake_kakashi", NOT "kakashi_hatake").
    For ninja without a clan (most non-canon-clan characters), use
    the lowercase romaji of their full name with underscores
    (e.g. "jiraiya", "konan", "deidara", "kabuto_yakushi"
    -> "yakushi_kabuto" since Yakushi is treated as a clan-like
    surname). This applies to character_id at the root, and to all
    character ids inside parents, children, siblings, team_members,
    spouse, sensei_id, relative_age_to.other_char.

12. KEKKEI_GENKAI vs NATURES disambiguation:
    - Five basic natures (go in natures_possessed) :
      "katon", "suiton", "doton", "fuuton", "raiton".
    - Yin and Yang chakra (advanced natures, go in natures_possessed) :
      "inton" (Yin), "youton_yang" (Yang Release; do NOT confuse with
      "yoton" which is the Lava Release kekkei genkai).
    - Combinatory kekkei genkai (go in kekkei_genkai_possessed,
      NOT in natures_possessed) :
      "mokuton" (Wood = Suiton+Doton),
      "hyouton" (Ice = Suiton+Fuuton),
      "yoton" (Lava/Scorch = Katon+Doton or Katon+Fuuton),
      "jiton" (Magnet = Fuuton+Doton),
      "bakuton" (Explosion = Doton+Raiton),
      "shouton" (Crystal),
      "ranton" (Storm = Suiton+Raiton),
      "futton" (Boil = Katon+Suiton),
      "jinton" (Swift = Fuuton+Raiton).
    - Dojutsu (eye-based KG, go in kekkei_genkai_possessed) :
      "sharingan", "mangekyo_sharingan", "rinnegan", "rinne_sharingan",
      "byakugan", "tenseigan", "jogan", "ketsuryugan",
      "shibai_otsutsuki_pupil".
    - Other body-based KG (kekkei_genkai_possessed) :
      "shikotsumyaku" (Dead Bone Pulse, Kaguya clan),
      "hydrification" (Hozuki clan), "magnet_release" (alt id of jiton),
      "swift_release" (alt id of jinton).
```

---

## 3. User prompt (modèle, à instancier par le script)

```
character_id: <char_id>
name_romaji: <name>

wiki_sections:

[Background]
<text>

[Personality]
<text>

[Abilities]
<text>

[Part I]
<text>

[Part II]
<text>

[Blank Period]
<text>

[New Era]
<text>

[Quotes]
<text>

Extract facts about <name> following the schema. JSON only.
```

Sections inclues : Background, Personality, Abilities, Part I, Part
II, Blank Period, New Era, New Era: Part I, New Era: Part II, Quotes,
Plot Overview, Legacy.

Si aucune de ces sections n'existe (~1 perso sur 1360), fallback sur
toutes les sections disponibles (Appearance, Trivia, etc.).

Soft cap input : si le total dépasse 7000 tokens, tronquer Background
en premier (les autres sections sont prioritaires pour les facts
canon), puis Plot Overview, puis Legacy. Garder Personality, Abilities,
Part I/II, Quotes en priorité.

---

## 4. Output schema (JSON strict)

```json
{
  "character_id": "<char_id>",
  "extraction_metadata": {
    "wiki_sections_used": ["Background", "Part I", "Part II"],
    "extractor_notes": "1-2 sentence note on anything notable, or null"
  },
  "fields": {
    "birth_year": {
      "value": null,
      "source_quote": null,
      "confidence": null,
      "derivation_method": null
    },
    "death_year": {
      "value": null,
      "source_quote": null,
      "confidence": null,
      "derivation_method": null
    },
    "death_arc": {
      "value": null,
      "source_quote": null,
      "confidence": null
    },
    "village_of_origin": {
      "value": null,
      "source_quote": null,
      "confidence": null
    },
    "clan": {
      "value": null,
      "source_quote": null,
      "confidence": null
    },
    "kekkei_genkai_possessed": [],
    "natures_possessed": [],
    "team_name": {
      "value": null,
      "source_quote": null,
      "confidence": null
    },
    "team_members": [],
    "sensei_id": {
      "value": null,
      "source_quote": null,
      "confidence": null
    },
    "parents": [],
    "children": [],
    "siblings": [],
    "spouse": {
      "value": null,
      "source_quote": null,
      "confidence": null
    },
    "rank_progression": [],
    "first_appearance_arc": {
      "value": null,
      "source_quote": null,
      "confidence": null
    },
    "key_techniques": [],
    "age_at_event": [],
    "relative_age_to": [],
    "is_jinchuuriki": {
      "value": null,
      "source_quote": null,
      "confidence": null
    },
    "tailed_beast": {
      "value": null,
      "source_quote": null,
      "confidence": null
    }
  }
}
```

Sub-schemas pour les listes :

```jsonc
// kekkei_genkai_possessed[i] et natures_possessed[i]
{
  "value": "sharingan",       // id slug, snake_case
  "source_quote": "Itachi awakened the Sharingan at age 8 during the Third Shinobi War.",
  "confidence": "high"        // high | medium | low
}

// team_members[i], parents[i], children[i], siblings[i]
{
  "value": "uchiha_sasuke",   // id slug si possible, sinon nom_romaji en lower_snake
  "source_quote": "Itachi's younger brother Sasuke",
  "confidence": "high"
}

// rank_progression[i]
{
  "rank": "anbu",             // enum strict (cf. system prompt rule 10)
  "year_approx": 6,           // integer, year in canon (year 0 = Naruto's birth), or null
  "source_quote": "Itachi joined the ANBU at age 13, six years after the Nine-Tails attack",
  "confidence": "medium"      // medium because year derived by arithmetic
}

// key_techniques[i]
{
  "value": "amaterasu",       // id slug
  "source_quote": "Itachi's signature technique was Amaterasu",
  "confidence": "high"
}

// age_at_event[i]
{
  "arc": "third_shinobi_world_war",  // arc id, free-form snake_case
  "age": 8,
  "source_quote": "Itachi was 8 when the Third Shinobi World War ended"
}

// relative_age_to[i]
{
  "other_char": "uchiha_sasuke",     // id slug or name lower_snake
  "delta_years": 5,                  // positive : THIS char is older. Negative : younger.
  "source_quote": "Itachi was 5 years older than his brother Sasuke"
}
```

Rules pour les enums :
- Tous les ids slugs sont lowercase, snake_case (ex `uchiha_sasuke`,
  `mangekyo_sharingan`, `katon`, `sharingan`).
- **Character IDs** : format `clan_lastname_firstname` imposé par la
  règle 11 du system prompt (ex `senju_hashirama`, pas
  `hashirama_senju`). Pour les ninja sans clan, lowercase romaji du
  nom complet (ex `jiraiya`, `konan`).
- **Kekkei genkai vs natures** : distinction stricte par la règle 12
  du system prompt. Mokuton, Hyouton, Yoton, Jiton, Bakuton, Shouton,
  Ranton, Futton, Jinton, Sharingan, Byakugan, Rinnegan, Tenseigan,
  Shikotsumyaku, Hydrification → `kekkei_genkai_possessed`. Katon,
  Suiton, Doton, Fuuton, Raiton, Inton, Youton_yang →
  `natures_possessed`.
- Année 0 = naissance de Naruto. Tout `year_approx` est relatif.

---

## 5. Exemples POSITIFS (extractions valides)

Chaque exemple : input minimal de wiki_section + output JSON valide
attendu. Inclus dans le user prompt comme few-shot si nécessaire,
sinon référence pour le humain qui review.

### 5.1 Birth year explicite

**Input** : `[Background] Itachi Uchiha (うちはイタチ, Uchiha Itachi)
was born on June 9 of year -7 to Fugaku and Mikoto Uchiha.`

**Output (extrait)** :
```json
"birth_year": {
  "value": -7,
  "source_quote": "was born on June 9 of year -7",
  "confidence": "high",
  "derivation_method": "explicit"
},
"parents": [
  {"value": "uchiha_fugaku", "source_quote": "Fugaku and Mikoto Uchiha", "confidence": "high"},
  {"value": "uchiha_mikoto", "source_quote": "Fugaku and Mikoto Uchiha", "confidence": "high"}
]
```

### 5.2 Birth year dérivé (medium confidence)

**Input** : `[Background] When Itachi was 13 years old, the Nine-Tails
attack on Konoha had happened six years earlier.`

**Output (extrait)** :
```json
"birth_year": {
  "value": -7,
  "source_quote": "When Itachi was 13 years old, the Nine-Tails attack on Konoha had happened six years earlier",
  "confidence": "medium",
  "derivation_method": "computed_from_event"
},
"age_at_event": [
  {"arc": "anbu_promotion", "age": 13, "source_quote": "When Itachi was 13 years old, the Nine-Tails attack on Konoha had happened six years earlier"}
]
```

(Le LLM calcule : Nine-Tails attack = year 0, donc 6 ans après = year 6,
donc Itachi avait 13 en year 6, donc birth_year = 6 - 13 = -7. Cohérent
avec l'exemple 5.1. Confidence `medium` parce qu'arithmétique multi-étape.)

### 5.3 Possession explicite kekkei_genkai

**Input** : `[Abilities] Itachi awakened the Sharingan at age 8 and
later evolved it into the Mangekyo Sharingan after the death of his
best friend Shisui.`

**Output (extrait)** :
```json
"kekkei_genkai_possessed": [
  {"value": "sharingan", "source_quote": "Itachi awakened the Sharingan at age 8", "confidence": "high"},
  {"value": "mangekyo_sharingan", "source_quote": "later evolved it into the Mangekyo Sharingan after the death of his best friend Shisui", "confidence": "high"}
]
```

### 5.4 Rank progression avec dérivation temporelle

**Input** : `[Background] Hashirama Senju was the founder of
Konohagakure and became its First Hokage during the founding era.`

**Output (extrait)** :
```json
"rank_progression": [
  {
    "rank": "kage",
    "year_approx": null,
    "source_quote": "became its First Hokage during the founding era",
    "confidence": "high"
  }
],
"village_of_origin": {
  "value": "konohagakure",
  "source_quote": "Hashirama Senju was the founder of Konohagakure",
  "confidence": "high"
}
```

### 5.5 Relative age

**Input** : `[Background] Sasuke is five years younger than his
brother Itachi.`

**Output (extrait)** :
```json
"relative_age_to": [
  {"other_char": "uchiha_itachi", "delta_years": -5, "source_quote": "Sasuke is five years younger than his brother Itachi"}
],
"siblings": [
  {"value": "uchiha_itachi", "source_quote": "his brother Itachi", "confidence": "high"}
]
```

(Note : delta_years = -5 car Sasuke est 5 ans plus jeune. Convention :
positif = THIS char est plus vieux, négatif = THIS char est plus jeune.)

---

## 6. Exemples NÉGATIFS (à NE PAS extraire)

### 6.1 Cooccurrence non-possession (Sharingan voisin)

**Input (wiki de Hiruzen)** : `[Abilities] During his battle with
Orochimaru, Hiruzen faced the Sharingan techniques wielded by his
former student.`

**❌ MAUVAISE extraction** :
```json
"kekkei_genkai_possessed": [{"value": "sharingan", ...}]
```

**✅ BONNE extraction** :
```json
"kekkei_genkai_possessed": []
```

**Raison** : "faced the Sharingan techniques wielded by his former
student" = Orochimaru (le student) utilise le Sharingan, pas Hiruzen.
Cooccurrence dans le texte ≠ possession. Le verbe "faced" est un
indicateur d'opposition, pas de possession.

### 6.2 Témoin d'une technique (Mangekyo)

**Input (wiki de Naruto)** : `[Part I] During the encounter with the
Akatsuki, Naruto saw Itachi's Mangekyo Sharingan for the first time.`

**❌ MAUVAISE extraction** :
```json
"kekkei_genkai_possessed": [{"value": "mangekyo_sharingan", ...}]
```

**✅ BONNE extraction** :
```json
"kekkei_genkai_possessed": []
```

**Raison** : "saw Itachi's Mangekyo Sharingan" = Naruto observe le KG
d'Itachi. Le possessif "Itachi's" indique clairement le possesseur, et
ce n'est pas Naruto.

### 6.3 Fact tiers dans la wiki

**Input (wiki d'Itachi)** : `[Background] Itachi's younger brother
Sasuke later became a missing-nin and joined Orochimaru.`

**❌ MAUVAISE extraction (pour Itachi)** :
```json
"rank_progression": [{"rank": "missing_nin", ...}]
```

**✅ BONNE extraction (pour Itachi)** :
```json
"siblings": [
  {"value": "uchiha_sasuke", "source_quote": "Itachi's younger brother Sasuke", "confidence": "high"}
]
```

**Raison** : la phrase parle de Sasuke (`Sasuke later became a
missing-nin`), pas d'Itachi. Le fait sur Sasuke ne se transfère pas à
Itachi. Seul le sibling_id est extrait pour Itachi.

### 6.4 Discours rapporté tiers (parents par procuration)

**Input (wiki de Kakashi)** : `[Part II] Kakashi mentioned that the
Yondaime Hokage Minato had a son named Naruto, sealed with the
Nine-Tails.`

**❌ MAUVAISE extraction (pour Kakashi)** :
```json
"children": [{"value": "uzumaki_naruto", ...}]
```

**✅ BONNE extraction (pour Kakashi)** :
```json
"children": []
```

**Raison** : Kakashi dit que Minato a un fils. Ce n'est pas Kakashi
qui a un fils. La grammaire du verbe "had" a Minato comme sujet, pas
Kakashi.

### 6.5 Mention hedgée (rumor, "some say")

**Input (wiki de Tobirama)** : `[Trivia] It is sometimes claimed in
unverified sources that Tobirama created the Edo Tensei before its
prohibition.`

**❌ MAUVAISE extraction** :
```json
"key_techniques": [{"value": "edo_tensei", "confidence": "high", ...}]
```

**✅ ACCEPTABLE (low confidence)** ou **mieux : skip entièrement** :
```json
"key_techniques": [
  {"value": "edo_tensei", "source_quote": "It is sometimes claimed in unverified sources that Tobirama created the Edo Tensei", "confidence": "low"}
]
```

**Raison** : "It is sometimes claimed in unverified sources" =
hedging. Ce n'est pas une affirmation directe canon. Le LLM peut
extraire avec `confidence: low` (à manuel-confirmer ensuite), ou
skip entièrement si la consigne est strictement "no hedged facts".

**Décision pour Pass 2** : extraire avec `confidence: low`. Le
validateur post-batch downgrade automatiquement les `low` en
`llm_extracted_low_conf` dans `birth_year_source`.

### 6.6 Champ que le wiki ne mentionne pas

**Input (wiki de Tenten)** : `[Background] Tenten is a kunoichi of
Konohagakure and a member of Team Guy.` (rien sur birth_year)

**❌ MAUVAISE extraction (devinette)** :
```json
"birth_year": {"value": -1, "confidence": "low", ...}
```

**✅ BONNE extraction** :
```json
"birth_year": {"value": null, "source_quote": null, "confidence": null, "derivation_method": null}
```

**Raison** : "Team Guy" n'est PAS un birth_year. Le LLM serait tenté
d'inférer "membre de Team Guy = même âge que Lee/Neji = né en year
-1", mais c'est de la propagation de canon-knowledge externe, pas une
extraction de la wiki de Tenten. INTERDIT par règle 3.

---

## 7. Validation post-extraction (côté script Python, pas LLM)

Pour chaque extraction reçue :

1. **JSON valide** : `json.loads(output)`. Si fail, marque la perso comme
   `extraction_failed` et skip. Pas de retry automatique (cf. consigne
   user).

2. **Schema conformity** : tous les champs racine présents (même si
   null/empty list). Vérifié via Pydantic ou validation manuelle.

3. **Source_quote grep** :
   ```python
   normalized_source = unicodedata.normalize("NFKD", source_text).lower()
   normalized_quote = unicodedata.normalize("NFKD", quote).lower()
   if normalized_quote not in normalized_source:
       # tolérance edit_distance <= 5 sur normalized_quote
       if edit_distance(quote, source_text) <= 5:
           # accept with warning
       else:
           # reject the field, downgrade to low_confidence and flag hallucination_probable
   ```
   Tolérance courante (edit_distance ≤ 5) gère :
   - Apostrophes typographiques '' vs '
   - Espaces multiples ou tabs
   - Accents oubliés/ajoutés
   - 1-2 mots de différence (ne devrait pas arriver mais marge de sécurité)

4. **Confidence flag synthese** : pour chaque field extrait, calcule un
   `effective_confidence` :
   - LLM dit `high` + grep matche exact → `high`
   - LLM dit `high` + grep matche avec edit_distance > 0 → `medium`
   - LLM dit `medium` ou `low` → garde le niveau LLM
   - Grep ne matche pas du tout → `null` + flag `hallucination_probable`

5. **Aggregation** : tous les outputs sont sauvegardés dans
   `data/canonical/_pass2_output/<char_id>.json`. Les flagged hallucinations
   sont listés à part dans
   `data/canonical/_pass2_hallucinations.json` pour review.

---

## 8. Notes pour le pré-dry-run (5 persos critiques)

Le pré-dry-run est exécuté par Claude Code (CC) en mode subscription
Max, sans appel à l'API Groq. Pour chaque perso :

1. CC lit `wiki_sections` de ce perso depuis `characters.json`.
2. CC applique strictement le system prompt et le schema de cette spec.
3. CC produit un output JSON dans
   `data/canonical/_pass2_output_dryrun/<char_id>.json`.
4. CC valide ses propres source_quotes par grep (Python local).
5. CC livre les 5 outputs dans la réponse pour review humain.

Persos sélectionnés pour ce pré-dry-run :

| char_id | name | Test ciblé |
|---|---|---|
| `sarutobi_hiruzen` | Hiruzen Sarutobi | Triple test : (a) Sharingan PAS extrait (cooccurrence avec Orochimaru), (b) toutes les natures attestées textuellement extraites (test contre sous-extraction par "savoir externe"), (c) aucun KG extrait (test contre fabrication). |
| `senju_hashirama` | Hashirama Senju | Test combinatoire KG : Mokuton extrait dans `kekkei_genkai_possessed` (PAS dans `natures_possessed`, PAS dans `key_techniques`). Sharingan/Rinnegan ne doivent PAS apparaître si pas attestés en possession (cooccurrence avec Madara possible). |
| `uzumaki_naruto` | Naruto Uzumaki | Test riche : extraction complète attendue. is_jinchuuriki=true avec quote, tailed_beast=kurama, parents=[namikaze_minato, uzumaki_kushina], rank_progression complet (academy → genin → chunin → kage). |
| `roshi` | Rōshi | Test wiki pauvre : champs majoritairement null sans hallucination. is_jinchuuriki si attesté, sinon null. |
| `mitsuki` | Mitsuki | Test Boruto-era : pas de confusion avec Orochimaru. Si la wiki dit "synthetic son of Orochimaru", parent peut être extrait avec confidence appropriée. Pas d'invention de mère. |

**Critères d'acceptation Hiruzen** (test le plus riche pour calibrer la
règle 4 et la règle 3) :
- Si la wiki d'Hiruzen dit "Hiruzen mastered all five chakra natures",
  l'extraction CORRECTE produit `natures_possessed = [katon, suiton,
  doton, fuuton, raiton]` avec la même source_quote pour les cinq.
- Si la wiki dit juste "Hiruzen was known as The Professor",
  l'extraction NE DOIT PAS extrapoler "donc 5 natures" (savoir externe
  interdit par règle 3). Seulement les natures explicitement
  textuellement nommées sont extraites.
- Mention de "Sharingan" dans le contexte de combats contre Orochimaru
  ne doit JAMAIS produire `kekkei_genkai_possessed: [sharingan]`
  (règle 4 cooccurrence vs possession).

---

## 9. Notes pour le batch test 50 et le full batch (post-dry-run)

- Sélection batch test 50 : top-50 + random sample de 0 (top-50 fait
  les 50). Le full batch ensuite couvre les 1300 restants
  (1359 - 50 - quelques skipped).
- Le script `pass2_extract_canon.py` accepte `--ids-from <file>` pour
  une liste arbitraire d'ids, ou `--all-except <file>` pour le full
  batch en excluant les ids déjà traités.
- Reprise après crash : `--resume` skip les ids qui ont déjà un fichier
  output.
- Logging coût cumulé : log `cost_so_far_usd` après chaque batch de 50
  appels. Hard limit `> $5` arrête le script avec confirmation manuelle.

---

## 10. Output final attendu pour le pré-dry-run

CC livre dans la prochaine réponse :

1. Les 5 fichiers JSON dans
   `data/canonical/_pass2_output_dryrun/<char_id>.json`.
2. Une analyse rapide par perso : "Hiruzen — Sharingan non extrait ✅,
   Katon extrait avec confidence high ✅, source_quote validée par grep ✅".
3. La liste des grep validations (combien de quotes ont matché en
   exact, combien avec edit_distance, combien ont raté).
4. Une recommandation : spec validée pour passage à 15 persos
   secondaires, ou ajustement nécessaire (avec proposition).
