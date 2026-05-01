# 04. Bases de connaissances canoniques

Ce document specifie le schema de chaque dataset JSON sous `data/canonical/`. Chaque schema est implemente sous forme de modele pydantic dans `src/shinobi/canon/models.py`, et valide au chargement.

Conventions communes :

- Tout objet a un champ `id` unique au sein de son fichier, en snake_case ascii pur (pas d'accent, pas d'espace).
- Tout objet a un champ `canonicity` parmi les valeurs de la hierarchie definie dans `01_constraints.md`.
- Tout objet a un champ `sources` qui est une liste de references vers les sources documentees.
- Tout objet a un champ `updated_at` au format `YYYY-MM-DD`.
- Les annees sont des entiers signed, an 1 = annee de naissance de Naruto.
- Les noms canoniques en romaji et en kanji sont stockes ensemble. Les descriptions sont en francais.
- Les enums sont des chaines en snake_case ascii.

## 1. ranks.json

Reference des rangs ninja.

```json
[
  {
    "id": "academy_student",
    "name_romaji": "Akademi gakusei",
    "name_fr": "Eleve de l'academie",
    "level": 0,
    "min_age": 6,
    "typical_max_age": 12,
    "description_fr": "Statut des enfants en formation a l'academie ninja.",
    "canonicity": "manga",
    "sources": ["narutopedia:Academy_Student"],
    "updated_at": "2026-05-01"
  },
  {
    "id": "genin",
    "name_romaji": "Genin",
    "name_kanji": "下忍",
    "name_fr": "Ninja debutant",
    "level": 1,
    "min_age": 6,
    "typical_max_age": null,
    "description_fr": "Premier rang officiel apres validation de l'examen de fin d'academie.",
    "canonicity": "manga",
    "sources": ["narutopedia:Genin"],
    "updated_at": "2026-05-01"
  }
]
```

Liste minimale a inclure : `academy_student`, `genin`, `chunin`, `tokubetsu_jonin`, `jonin`, `anbu`, `kage`, `sannin`, `missing_nin`, `civilian`, `monk`, `samurai`, `daimyo`.

## 2. eras.json

Decoupage des grandes ères de l'univers, utile pour la creation de personnage et pour le scheduler d'evenements.

```json
[
  {
    "id": "warring_states",
    "name_romaji": "Sengoku Jidai",
    "name_fr": "Periode des Royaumes Combattants",
    "year_start": -100,
    "year_end": -55,
    "description_fr": "Ere precedant la fondation des villages cachees, marquee par les guerres entre clans.",
    "key_figures": ["senju_hashirama", "uchiha_madara", "uzumaki_mito"],
    "canonicity": "manga",
    "updated_at": "2026-05-01"
  }
]
```

Liste minimale : `warring_states`, `villages_founding`, `first_great_ninja_war`, `second_great_ninja_war`, `third_great_ninja_war`, `pre_naruto_era`, `naruto_academy_era`, `naruto_part1`, `naruto_part2_shippuden`, `fourth_great_ninja_war`, `post_war_era`, `boruto_era`, `tbv_era`.

Les annees exactes de debut et fin sont approximatives pour les eres anciennes. Le champ `year_start` peut avoir un sous-champ `confidence` (`exact`, `approximate`, `estimated`).

## 3. natures.json

Les cinq natures elementaires plus les natures avancees et speciales.

```json
[
  {
    "id": "katon",
    "name_romaji": "Katon",
    "name_kanji": "火遁",
    "name_fr": "Feu",
    "type": "basic",
    "strong_against": ["fuuton"],
    "weak_against": ["suiton"],
    "common_clans": ["uchiha", "sarutobi"],
    "common_villages": ["konohagakure", "sunagakure"],
    "description_fr": "Nature elementaire associee au feu, dominante chez de nombreux clans de Konoha.",
    "canonicity": "manga",
    "updated_at": "2026-05-01"
  }
]
```

Liste minimale : `katon`, `suiton`, `fuuton`, `doton`, `raiton`, `mokuton`, `hyouton`, `youton`, `jinton`, `bakuton`, `shouton`, `ranton`, `jiton`, `inton`, `youton_yang`, `inton_yin`, `meiton`, `senpou` (chakra naturel).

## 4. world_rules.json

Regles abstraites de l'univers utilisees par le moteur pour resolution.

```json
{
  "chakra": {
    "definition": "Energie spirituelle et physique combinee, ressource principale pour les techniques.",
    "baseline_pools": {
      "civilian_adult": 30,
      "academy_student": 50,
      "genin": 100,
      "chunin": 200,
      "jonin": 400,
      "kage": 800,
      "uzumaki_modifier": 2.0,
      "jinchuuriki_modifier": 5.0,
      "senju_modifier": 1.5
    },
    "regeneration": {
      "rest_per_hour": 5,
      "sleep_per_hour": 15,
      "meditation_per_hour": 25
    },
    "exhaustion_thresholds": {
      "fatigue": 0.5,
      "danger": 0.2,
      "death_risk": 0.05
    }
  },
  "learning": {
    "difficulty_to_hours_baseline": {
      "1": 5,
      "2": 20,
      "3": 60,
      "4": 200,
      "5": 600,
      "6": 1500,
      "7": 4000,
      "8": 10000,
      "9": 25000,
      "10": 60000
    },
    "stat_modifiers": {
      "intelligence_per_point": -0.05,
      "chakra_control_per_point": -0.04,
      "talent_genius_per_point": -0.06
    },
    "mentor_quality_modifiers": {
      "absent": 1.5,
      "self_taught_with_scroll": 1.2,
      "regular_teacher": 1.0,
      "expert_user": 0.7,
      "creator_of_technique": 0.5
    }
  },
  "combat": {
    "initiative_formula": "speed + d20 + reflexes_modifier",
    "hit_formula": "attacker_skill + d20 vs defender_skill + 10",
    "damage_formula": "technique_power * (1 + attacker_strength_or_chakra / 10) - defender_resistance"
  },
  "social": {
    "reputation_decay_per_year": 0.1,
    "village_loyalty_default": 50,
    "missing_nin_threshold": -100,
    "village_kekkei_genkai_persecution_modifier": -50
  },
  "economy": {
    "ryo_to_jutsu_scroll_multiplier_by_rank": {
      "E": 100,
      "D": 1000,
      "C": 10000,
      "B": 100000,
      "A": 1000000,
      "S": 10000000,
      "forbidden": null
    },
    "mission_pay_by_rank": {
      "D": 5000,
      "C": 50000,
      "B": 200000,
      "A": 800000,
      "S": 3000000
    }
  },
  "time": {
    "year_one_anchor": "1990-10-10",
    "month_names_jp": ["mutsuki", "kisaragi", "yayoi", "uzuki", "satsuki", "minazuki", "fumizuki", "hazuki", "nagatsuki", "kannazuki", "shimotsuki", "shiwasu"]
  }
}
```

Le fichier est unique, pas une liste. Les valeurs ci-dessus sont des baselines indicatives, calibrables.

## 5. clans.json

```json
[
  {
    "id": "uchiha",
    "name_romaji": "Uchiha",
    "name_kanji": "うちは一族",
    "village_of_origin": "konohagakure",
    "founder": "uchiha_tajima",
    "key_kekkei_genkai": ["sharingan", "mangekyou_sharingan"],
    "key_natures": ["katon"],
    "key_techniques": ["katon_goukakyuu_no_jutsu", "amaterasu", "tsukuyomi", "susanoo"],
    "exclusive_techniques": ["amaterasu", "tsukuyomi", "susanoo", "kotoamatsukami"],
    "history_summary_fr": "Clan descendant d'Indra Otsutsuki, cofondateur de Konohagakure aux cotes des Senju, decime lors du massacre de l'an 4 par Itachi.",
    "status_by_era": [
      {"from_year": -55, "to_year": 4, "status": "active", "notes": "Membre fondateur de Konoha"},
      {"from_year": 4, "to_year": null, "status": "near_extinct", "notes": "Survivants connus apres massacre"}
    ],
    "notable_members_by_era": [
      {"era": "warring_states", "members": ["uchiha_madara", "uchiha_izuna"]},
      {"era": "naruto_part1", "members": ["uchiha_sasuke", "uchiha_itachi", "uchiha_obito"]}
    ],
    "social_structure_fr": "Clan militariste a forte cohesion interne, traditionnellement responsable de la police militaire de Konoha.",
    "canonicity": "manga",
    "sources": ["narutopedia:Uchiha_Clan"],
    "updated_at": "2026-05-01"
  }
]
```

Liste minimale a couvrir : tous les clans nommes dans le manga + databooks. Les clans mineurs avec peu d'info auront moins de champs remplis mais doivent quand meme exister pour permettre au joueur d'en etre membre.

## 6. kekkei_genkai.json et kekkei_mora.json

```json
[
  {
    "id": "sharingan",
    "name_romaji": "Sharingan",
    "name_kanji": "写輪眼",
    "type": "dojutsu",
    "category": "kekkei_genkai",
    "carrier_clans": ["uchiha"],
    "activation_conditions_fr": "Activation initiale generalement declenchee par un choc emotionnel intense, le plus souvent la perte d'un etre cher.",
    "stages": [
      {"stage": 1, "tomoe": 1, "abilities_fr": "Vision accrue, lecture des mouvements et des hand seals."},
      {"stage": 2, "tomoe": 2, "abilities_fr": "Anticipation amelioree, copie de techniques observees."},
      {"stage": 3, "tomoe": 3, "abilities_fr": "Maitrise complete du Sharingan basique, hypnose, techniques de genjutsu de base."}
    ],
    "evolution_paths": ["mangekyou_sharingan"],
    "weaknesses_fr": "Consommation de chakra accrue, fatigue oculaire, vulnerable aux genjutsu de niveau superieur.",
    "canonicity": "manga",
    "sources": ["narutopedia:Sharingan", "databook_3"],
    "updated_at": "2026-05-01"
  }
]
```

Tous les dojutsu, kekkei genkai elementaires (Mokuton, Hyouton, Youton, Jinton, Bakuton, Shouton, Ranton), kekkei genkai non elementaires (Shikotsumyaku, etc.), et kekkei mora (Jinton de Muu, Storm release, etc.) doivent etre listes. Les kekkei mora vont dans `kekkei_mora.json` avec les memes champs.

## 7. hiden.json

Techniques secretes transmises uniquement au sein d'un clan ou village, mais qui ne sont pas des kekkei genkai (donc apprenables sans mutation genetique, juste avec acces a la formation).

```json
[
  {
    "id": "shintenshin_no_jutsu",
    "name_romaji": "Shintenshin no Jutsu",
    "name_fr": "Transposition mentale",
    "owning_clan": "yamanaka",
    "owning_village": "konohagakure",
    "shareable_outside_clan": false,
    "shareable_with_authorization": true,
    "description_fr": "Permet a l'utilisateur d'envoyer son esprit dans le corps d'un adversaire pour en prendre temporairement le controle.",
    "canonicity": "manga",
    "sources": ["narutopedia:Mind_Body_Switch_Technique"],
    "updated_at": "2026-05-01"
  }
]
```

## 8. villages.json

```json
[
  {
    "id": "konohagakure",
    "name_romaji": "Konohagakure no Sato",
    "name_kanji": "木ノ葉隠れの里",
    "name_fr": "Village cache des feuilles",
    "country": "hi_no_kuni",
    "country_name_fr": "Pays du feu",
    "founded_year": -55,
    "founded_by": ["senju_hashirama", "uchiha_madara"],
    "kage_title": "hokage",
    "kage_lineage": [
      {"order": 1, "character_id": "senju_hashirama", "from_year": -55, "to_year": -45},
      {"order": 2, "character_id": "senju_tobirama", "from_year": -45, "to_year": -38},
      {"order": 3, "character_id": "sarutobi_hiruzen", "from_year": -38, "to_year": -7},
      {"order": 4, "character_id": "namikaze_minato", "from_year": -7, "to_year": 1},
      {"order": 3, "character_id": "sarutobi_hiruzen", "from_year": 1, "to_year": 12, "second_term": true},
      {"order": 5, "character_id": "tsunade", "from_year": 12, "to_year": 17},
      {"order": 6, "character_id": "hatake_kakashi", "from_year": 17, "to_year": 22},
      {"order": 7, "character_id": "uzumaki_naruto", "from_year": 33, "to_year": null}
    ],
    "main_clans": ["uchiha", "senju", "hyuuga", "nara", "akimichi", "yamanaka", "inuzuka", "aburame", "sarutobi", "uzumaki", "hatake"],
    "specialties": ["balanced_ninjutsu", "diverse_clans", "diplomacy"],
    "geography_fr": "Vallee montagneuse boisee, entouree de falaises naturelles. Le visage des Hokages successifs est sculpte dans une falaise dominant le village.",
    "districts": [
      {"id": "uchiha_district", "name_fr": "Quartier Uchiha", "active_until_year": 4},
      {"id": "hyuuga_compound", "name_fr": "Domaine Hyuuga"},
      {"id": "academy_district"},
      {"id": "merchant_quarter"},
      {"id": "hokage_tower_area"}
    ],
    "diplomatic_relations_by_era": [
      {"year": 12, "kumogakure": "neutral_tense", "iwagakure": "cold", "kirigakure": "thawing", "sunagakure": "alliance"}
    ],
    "canonicity": "manga",
    "sources": ["narutopedia:Konohagakure"],
    "updated_at": "2026-05-01"
  }
]
```

Liste minimale : les 5 grands villages, plus les villages mineurs nommes (Amegakure, Kusagakure, Yugakure, Takigakure, Otogakure, Tanigakure, Yukigakure, Hoshigakure, Getsugakure, Shimogakure, etc.).

## 9. organizations.json

Organisations transversales (Akatsuki, Anbu, Roto, Kara, etc.).

```json
[
  {
    "id": "akatsuki",
    "name_romaji": "Akatsuki",
    "name_fr": "Aube",
    "active_period": [{"from_year": -20, "to_year": 16, "phase": "yahiko_era"}, {"from_year": -10, "to_year": 16, "phase": "obito_era"}],
    "founders": ["yahiko", "nagato", "konan"],
    "leaders_by_era": [
      {"from_year": -20, "to_year": -15, "leader": "yahiko"},
      {"from_year": -15, "to_year": 0, "leader": "nagato_puppet_pain"},
      {"from_year": 0, "to_year": 16, "leader": "uchiha_obito_as_madara"}
    ],
    "members_by_era": [
      {"year": 12, "members": ["nagato", "konan", "uchiha_itachi", "hoshigaki_kisame", "deidara", "sasori", "hidan", "kakuzu", "uchiha_obito", "zetsu"]}
    ],
    "ideology_fr": "Premier objectif : capturer les neuf bijuu pour invoquer le Gedo Mazo et lancer le Tsuki no Me, plan d'illusion mondiale.",
    "headquarters": ["amegakure_tower", "hidden_caves"],
    "canonicity": "manga",
    "sources": ["narutopedia:Akatsuki"],
    "updated_at": "2026-05-01"
  }
]
```

## 10. characters.json

Le dataset le plus volumineux. Chaque personnage canonique nomme dans une source active.

```json
{
  "id": "uzumaki_naruto",
  "name_romaji": "Uzumaki Naruto",
  "name_kanji": "うずまきナルト",
  "name_fr": "Naruto Uzumaki",
  "aliases": ["konoha no orenji hokage", "child of the prophecy"],
  "gender": "male",
  "birth_year": 1,
  "birth_date": "10-10",
  "death_year": null,
  "death_circumstances_fr": null,
  "village_of_origin": "konohagakure",
  "current_village_by_era": [
    {"from_year": 1, "to_year": null, "village": "konohagakure"}
  ],
  "clan": "uzumaki",
  "secondary_clan": "namikaze",
  "kekkei_genkai": [],
  "kekkei_mora": [],
  "tailed_beast": "kurama",
  "rank_progression": [
    {"year": 12, "rank": "genin"},
    {"year": 16, "rank": "skipped_chunin", "notes": "Promu directement plus tard pour merites de guerre"},
    {"year": 33, "rank": "hokage"}
  ],
  "stats_by_era": [
    {
      "era_label": "academy_end",
      "year": 12,
      "ninjutsu": 1.0,
      "taijutsu": 1.5,
      "genjutsu": 1.0,
      "intelligence": 1.5,
      "strength": 2.0,
      "speed": 2.0,
      "stamina": 4.0,
      "hand_seals": 1.0,
      "chakra_pool": 600,
      "chakra_control": 1.0,
      "total_databook": 12.0,
      "social_charisma": 3.0,
      "learning_genius": 2.0,
      "luck": 4.0,
      "beauty": 2.5,
      "lineage_value": 5.0
    },
    {
      "era_label": "shippuden_end",
      "year": 17,
      "ninjutsu": 5.0,
      "taijutsu": 5.0,
      "genjutsu": 1.0,
      "intelligence": 3.5,
      "strength": 5.0,
      "speed": 5.0,
      "stamina": 5.0,
      "hand_seals": 4.5,
      "chakra_pool": 5000,
      "chakra_control": 4.0,
      "total_databook": 39.0,
      "social_charisma": 5.0,
      "learning_genius": 3.5,
      "luck": 5.0,
      "beauty": 3.5,
      "lineage_value": 5.0
    }
  ],
  "techniques_known_by_era": [
    {"year": 12, "techniques": ["kage_bunshin_no_jutsu", "henge_no_jutsu", "kawarimi_no_jutsu", "harem_no_jutsu", "rasengan_incomplete"]},
    {"year": 17, "techniques": ["kage_bunshin_no_jutsu", "rasengan", "oodama_rasengan", "rasenshuriken", "fuuton_rasenshuriken", "senjutsu_mode", "kurama_chakra_mode", "tailed_beast_mode"]}
  ],
  "natures": ["fuuton", "inton", "youton_yang"],
  "personality_fr": "Optimiste pugnace, loyal a l'extreme, refuse d'abandonner ses amis, sensible a l'isolement par experience personnelle.",
  "voice_profile_id": "naruto_voice",
  "speech_patterns": {
    "verbal_tic": "dattebayo",
    "tic_frequency": "fin_de_phrase_emphatique",
    "register": "familier_chaleureux",
    "vocabulary_traits": ["repetitif", "ramen", "amitie", "depasser_les_limites"]
  },
  "key_relationships": [
    {"with": "uchiha_sasuke", "type": "rival_then_brother", "since_year": 12},
    {"with": "haruno_sakura", "type": "teammate", "since_year": 12},
    {"with": "hatake_kakashi", "type": "sensei_then_friend", "since_year": 12},
    {"with": "jiraiya", "type": "godfather_master", "since_year": 13},
    {"with": "hyuuga_hinata", "type": "love_interest_then_wife", "since_year": 12},
    {"with": "namikaze_minato", "type": "father", "since_year": 1},
    {"with": "uzumaki_kushina", "type": "mother", "since_year": 1}
  ],
  "location_by_year": [
    {"year": 1, "location": "konohagakure"},
    {"year": 13, "location": "training_journey_with_jiraiya"},
    {"year": 16, "location": "konohagakure"}
  ],
  "teachable_techniques": ["kage_bunshin_no_jutsu", "rasengan"],
  "teaching_conditions_fr": "N'enseigne pas avant l'an 17. Apres, accepte d'enseigner a quiconque a prouve sa loyaute envers ses amis ou Konoha.",
  "knowledge_domains": ["kuchiyose_toad", "fuuton_techniques", "senjutsu_basics", "uzumaki_seal_basics"],
  "canonicity": "manga",
  "sources": ["narutopedia:Naruto_Uzumaki", "databook_1", "databook_2", "databook_3"],
  "updated_at": "2026-05-01"
}
```

Champs cles a expliquer :

- `stats_by_era` : plusieurs snapshots a differentes annees pour permettre au moteur de simuler le personnage au moment ou le joueur l'interagit.
- `teachable_techniques` : techniques que ce personnage peut enseigner a un eleve, parmi celles qu'il connait.
- `teaching_conditions_fr` : conditions narratives auxquelles il accepte d'enseigner. Le LLM les lit pour negocier.
- `knowledge_domains` : champs de competence sur lesquels il peut donner de l'information moyennant un prix.
- `voice_profile_id` : reference vers `voice_profiles.json` pour le rendu de dialogue.
- `location_by_year` : utilise par le moteur pour determiner ou se trouve canoniquement un PNJ a une date donnee. Indispensable pour les rencontres orchestrees.

Les personnages mineurs auront beaucoup moins de champs remplis, mais le minimum requis est : `id`, `name_romaji`, `gender`, `birth_year`, `village_of_origin`, `canonicity`, `sources`, `updated_at`.

## 11. techniques.json

```json
{
  "id": "katon_goukakyuu_no_jutsu",
  "name_romaji": "Katon Goukakyuu no Jutsu",
  "name_kanji": "火遁豪火球の術",
  "name_fr": "Boule de feu supreme",
  "category": "ninjutsu",
  "subcategory": "elemental",
  "natures": ["katon"],
  "rank": "C",
  "classification": ["offensive"],
  "range": "short_to_mid",
  "hand_seals": ["snake", "ram", "monkey", "boar", "horse", "tiger"],
  "chakra_cost": 25,
  "stamina_cost": 10,
  "learning_difficulty": 3,
  "prerequisites": {
    "min_chakra_pool": 30,
    "min_chakra_control": 1.5,
    "required_natures": ["katon"],
    "required_techniques": [],
    "min_age": 7,
    "clan_restriction": null,
    "kekkei_genkai_restriction": null,
    "village_restriction": null,
    "rank_restriction": null,
    "notes_fr": "Souvent enseignee comme rite de passage chez les Uchiha."
  },
  "effects": {
    "damage": "moderate",
    "area_type": "cone",
    "area_size_meters": 10,
    "duration_turns": 1,
    "side_effects_fr": []
  },
  "counters": ["suiton_techniques", "doton_walls"],
  "synergies": [],
  "canonical_users": ["uchiha_sasuke", "uchiha_itachi", "uchiha_madara", "uchiha_obito"],
  "first_appearance": {
    "year": 0,
    "context_fr": "Sasuke utilise la technique en flashback dans son entrainement enfance."
  },
  "description_fr": "L'utilisateur insuffle du chakra dans ses poumons et l'expulse sous forme d'une grande boule de feu apres la sequence de signes appropriee.",
  "canonicity": "manga",
  "sources": ["narutopedia:Fire_Release_Great_Fireball_Technique", "databook_1"],
  "updated_at": "2026-05-01"
}
```

Champs supplementaires possibles selon la technique :

- `requires_summon` : pour les techniques de Kuchiyose, l'id de l'invocation necessaire
- `requires_weapon` : pour les techniques d'arme, l'id de l'arme
- `requires_partner` : pour les techniques de duo (genre Inuzuka + chien)
- `forbidden_reason_fr` : pour les kinjutsu, raison de l'interdiction
- `creator_id` : qui a invente la technique
- `taught_secretly_at` : si la technique est gardee secrete par un groupe

Categories complete : `ninjutsu`, `taijutsu`, `genjutsu`, `kenjutsu`, `bukijutsu`, `fuinjutsu`, `juinjutsu`, `senjutsu`, `iryo_ninjutsu`, `kinjutsu`, `hijutsu`, `kekkei_genkai`, `kekkei_mora`, `dojutsu_ability`, `unique_ability`, `summoning`, `barrier`.

## 12. tailed_beasts.json

```json
{
  "id": "kurama",
  "name_romaji": "Kurama",
  "tails": 9,
  "epithets": ["kyuubi no kitsune"],
  "current_jinchuuriki_by_era": [
    {"from_year": -55, "to_year": -45, "jinchuuriki": "uzumaki_mito"},
    {"from_year": -45, "to_year": -7, "jinchuuriki": "uzumaki_kushina"},
    {"from_year": 1, "to_year": null, "jinchuuriki": "uzumaki_naruto"}
  ],
  "personality_fr": "Initialement haineux et rancunier suite aux abus de Madara, evolue vers une relation de partenariat avec Naruto.",
  "abilities_fr": "Chakra demoniaque dense, regeneration acceleree, telepathie partielle, manipulation de la haine.",
  "chakra_signature_color": "orange_red",
  "canonicity": "manga",
  "sources": ["narutopedia:Kurama"],
  "updated_at": "2026-05-01"
}
```

Les neuf bijuu, plus le Juubi.

## 13. timeline_events.json

Evenements canoniques planifies. Le scheduler du moteur les lit pour determiner ce qui se passe automatiquement dans le monde.

```json
[
  {
    "id": "kyuubi_attack_konoha",
    "name_fr": "Attaque du Kyuubi sur Konoha",
    "year": 1,
    "date": "10-10",
    "location": "konohagakure",
    "involved_characters": ["uchiha_obito", "namikaze_minato", "uzumaki_kushina", "uzumaki_naruto"],
    "preconditions": [
      {"type": "character_alive", "character_id": "uchiha_obito", "as_of_year": 1},
      {"type": "character_alive", "character_id": "namikaze_minato", "as_of_year": 1},
      {"type": "kyuubi_held_by", "jinchuuriki_id": "uzumaki_kushina", "as_of_year": 1}
    ],
    "outcomes": [
      {"type": "character_death", "character_id": "namikaze_minato"},
      {"type": "character_death", "character_id": "uzumaki_kushina"},
      {"type": "jinchuuriki_transfer", "from": "uzumaki_kushina", "to": "uzumaki_naruto", "beast": "kurama"}
    ],
    "narrative_summary_fr": "Obito Uchiha manipule le Kyuubi pour attaquer Konoha le soir de la naissance de Naruto. Minato et Kushina se sacrifient pour sceller le bijuu dans leur fils.",
    "canonicity": "manga",
    "sources": ["narutopedia:Nine-Tails_Attack_on_Konoha"],
    "updated_at": "2026-05-01"
  }
]
```

Le moteur evalue les preconditions a chaque tour et declenche l'evenement quand toutes sont validees ET que la date est atteinte. Si une precondition est rompue (par exemple Obito est mort avant l'an 1), l'evenement est annule ou modifie selon une logique definie dans `08_world_simulation.md`.

Couverture minimale : tous les evenements majeurs des manga, plus les evenements canon des films, plus les evenements specifiques des Storm games marques avec `canonicity: game`.

## 14. weapons_tools.json

Armes et outils ninja nommes.

```json
[
  {
    "id": "kusanagi_no_tsurugi",
    "name_romaji": "Kusanagi no Tsurugi",
    "name_fr": "Lame de l'herbe",
    "type": "sword",
    "subcategory": "legendary_blade",
    "wielders_canonical": ["orochimaru", "uchiha_sasuke"],
    "abilities_fr": "Lame extensible et incassable, peut etre invoquee depuis l'estomac de l'utilisateur.",
    "rarity": "legendary",
    "canonicity": "manga",
    "sources": ["narutopedia:Sword_of_Kusanagi"],
    "updated_at": "2026-05-01"
  }
]
```

Inclure les sept lames de la Brume, les armes uniques (Samehada, Kubikiribocho, Helmet Splitter, etc.), les outils communs (kunai, shuriken, makibishi, fuma shuriken, etc.) avec stats abstraites.

## 15. locations.json

Lieux remarquables qui ne sont pas des villages mais qui ont une importance canon (Vallee de la Fin, Mont Myoboku, Repaires d'Orochimaru, Iles tortue, Pays des Vagues, etc.).

```json
[
  {
    "id": "valley_of_the_end",
    "name_romaji": "Shuumatsu no Tani",
    "name_fr": "Vallee de la Fin",
    "country": "hi_no_kuni",
    "near_village": "konohagakure",
    "geography_fr": "Vallee creusee par le combat entre Hashirama Senju et Madara Uchiha, marquee par leurs deux statues.",
    "canonical_events": ["kage_bunshin_battle_part1_end"],
    "canonicity": "manga",
    "sources": ["narutopedia:Valley_of_the_End"],
    "updated_at": "2026-05-01"
  }
]
```

## 16. voice_profiles.json

Profils de voix pour le rendu fidele des personnages canoniques par le LLM.

```json
[
  {
    "id": "naruto_voice",
    "character_id": "uzumaki_naruto",
    "register_fr": "familier, chaleureux, exuberant",
    "verbal_tics": ["dattebayo en fin de phrase emphatique"],
    "vocabulary_themes": ["ramen", "amitie", "ne jamais abandonner", "pari", "promesse"],
    "syntactic_patterns": ["phrases courtes", "exclamations frequentes", "repetitions emphatiques"],
    "sample_lines": [
      "Je deviendrai Hokage, dattebayo.",
      "Sasuke, je vais te ramener au village quoi qu'il en coute.",
      "Un ramen au porc supplementaire pour moi."
    ],
    "do_not_use": ["vocabulaire soutenu", "tournures formelles", "registre litteraire"],
    "updated_at": "2026-05-01"
  }
]
```

Tous les personnages avec une presence dialoguee notable doivent avoir un voice profile.

## 17. jutsu_categories.json

Reference des categories et sous-categories de techniques, avec leurs regles d'apprentissage de base.

## 18. Conventions de validation

Au chargement, `validate_canon.py` execute :

- validation pydantic stricte de chaque fichier
- verification que toutes les references croisees existent (un id de personnage cite dans une technique existe dans `characters.json`)
- verification de coherence temporelle (un personnage mort en l'an X ne peut pas etre acteur d'un evenement en l'an X+1)
- verification que les preconditions des `timeline_events.json` referent a des champs interrogeables
- rapport de couverture par canonicite

Si une erreur est detectee, le chargement echoue avec un message precis.
