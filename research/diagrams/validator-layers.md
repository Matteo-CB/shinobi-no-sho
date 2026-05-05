# Validator layers

Structure du Validator central (pilier 3) avec ses 5 couches A->E.
Aujourd'hui les couches A et C sont livrees, B arrive avec le pilier 6B
ce soir, D et E sont reportees au pilier 7 et au-dela.

```mermaid
flowchart LR
    NO[NarrativeOutput<br/>+ RuntimeState<br/>+ CanonView] --> V[Validator]

    V --> A[Layer A<br/><b>sherlock_rules</b>]
    A --> A1[dead actor parle ?]
    A --> A2[location detruite ?]
    A --> A3[ubiquite PNJ ?]
    A1 & A2 & A3 --> RA{is_valid ?}

    V --> B[Layer B<br/><b>triplet_check</b><br/><i>pilier 6B</i>]
    B --> B1[actor in jutsu.canonical_users ?]
    B --> B2[location in canon ?]
    B1 & B2 --> RB{is_valid ?}

    V --> C[Layer C<br/><b>age_coherence</b>]
    C --> C1[vocabulaire adulte<br/>chez enfant < 8 ans ?]
    C --> C2[baby talk<br/>chez adulte > 25 ans ?]
    C1 & C2 --> RC{is_valid ?}

    V -.-> D[Layer D<br/><b>NLI factuel</b><br/><i>pilier 7 - reporte</i>]
    V -.-> E[Layer E<br/><b>LLM judge</b><br/><i>pilier 7 - reporte</i>]

    RA & RB & RC --> AGG[Aggregate ValidationResult<br/>short_circuit ou full-pass]
    AGG -->|valid| OK[Continue vers output_filter]
    AGG -->|invalid| RGN[Regen loop<br/>feedback structure]
    RGN -.->|max 2 regens| FB[Fallback in-character]

    classDef done fill:#9f9,stroke:#393,stroke-width:2px,color:#000
    classDef todo fill:#fdb,stroke:#a73,stroke-width:1px,stroke-dasharray:5 5,color:#000
    classDef defer fill:#ddd,stroke:#888,stroke-width:1px,stroke-dasharray:5 5,color:#000

    class A,A1,A2,A3,RA,C,C1,C2,RC,AGG,OK done
    class B,B1,B2,RB,RGN,FB todo
    class D,E defer
```

Mode d'execution :
- `short_circuit=True` (default) : s'arrete au premier reject, latence
  optimisee.
- `short_circuit=False` : execute toutes les couches, utile pour le
  logging et le feedback regen complet.

Latences mesurees (validator MVP, sans LLM) :
- Layer A : < 1 ms par output
- Layer C : < 5 ms par output (regex sur prose)
- Layer B (a venir) : O(jutsus_extraits * canonical_users), < 1 ms
  attendu sur les enums charges en memoire
