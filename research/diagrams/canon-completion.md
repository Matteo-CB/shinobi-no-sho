# Canon completion : Pass 2 -> Pass 2.5 -> Pass 3

Vue du sous-projet de completion canon ferme le 2026-05-04. Pipeline
en 3 passes, $2.30 brules au total, 1359 personnages couverts a 100%.

```mermaid
flowchart TD
    SCRAP[(Wikis Naruto<br/>1359 fiches)] --> CHARS[(characters.json<br/>scrape brut + corruptions)]
    CHARS --> P2I[Pass 2 - Input<br/>scripts/pass2_extract_canon.py<br/>+ pass2_batch.py]
    P2I -->|JSONL Groq Batch API| GROQ[Groq Llama-3.3-70b-versatile<br/>response_format json_object]
    GROQ -->|1359 outputs| P2O[scripts/pass2_normalize.py<br/>fallbacks slug + clan swap]
    P2O --> EXTR[(_pass2_output/*.json<br/>1359 fichiers extraits)]

    EXTR --> P25[Pass 2.5<br/>scripts/pass2_5_derive.py<br/>pure Python deterministe]
    P25 --> P25L[Lookup arc_temporal_anchors.json<br/>chunin_exam=12, war4=16...]
    P25 --> P25R[Resolve relative_age_to<br/>chains transitif]
    P25L & P25R -->|+ birth_year derives| EXTR2[(_pass2_output/*.json<br/>+ extraction_metadata.birth_year_source)]

    EXTR2 --> P3[Pass 3 - Aggregation<br/>scripts/pass2_aggregate.py]
    P3 --> COUNT[Pour chaque clan :<br/>compter membres attestant<br/>chaque KG / nature / technique]
    COUNT --> TIER{Classification 3-tier}
    TIER -->|>= 50% ET >= 3 membres| KEY[clan.key_*<br/>signature canon obligatoire]
    TIER -->|>= 30% ET >= 3 membres<br/>hors KEY| AVAIL[clan.available_*<br/>eligibilite]
    TIER -->|1-2 membres| INDIV[individual_mutation<br/>tagge per-character]

    KEY & AVAIL --> NEWCLAN[(clans.json<br/>regenerated)]
    KEY & AVAIL --> NEWKG[(kekkei_genkai.json<br/>regenerated)]
    INDIV --> FLAGS[(_pass2_output/*.flags.json)]

    P3 --> CORR[Detection corruptions :<br/>OLD - NEW set difference]
    CORR --> CR[(scraper-corruption-report.md<br/>13 corruptions detectees)]

    P3 --> RPT[(canon-completion-report.md<br/>couverture par source / confidence)]

    NEWCLAN --> NEXT[Utilise par piliers 5-8<br/>filtrage retrieval, enums canon,<br/>triplet check]

    classDef done fill:#9f9,stroke:#393,stroke-width:2px,color:#000
    classDef data fill:#cef,stroke:#36a,stroke-width:1px,color:#000
    classDef rep fill:#fec,stroke:#a73,stroke-width:1px,color:#000

    class P2I,P2O,P25,P25L,P25R,P3,COUNT,TIER,KEY,AVAIL,INDIV,CORR done
    class CHARS,EXTR,EXTR2,NEWCLAN,NEWKG,FLAGS data
    class CR,RPT rep
```

Stats finales :
- 1359 / 1359 personnages extraits (100%)
- 14 / 52 clans avec attestations canoniques (4 key, 12 available)
- 232 mutations individuelles taggees
- 13 corruptions scraper detectees
- $2.30 brules sur Groq (50% off Batch API), wall time ~6h

Voir `research/canon-cleanup-handoff.md` pour le detail.
