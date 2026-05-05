# Pipeline overview

Vue end-to-end du pipeline anti-hallucination de Shinobi no Sho.
Un tour de jeu suit ce flow, du moment ou le joueur tape sa commande
jusqu'a la narration affichee a l'ecran.

```mermaid
flowchart TD
    P[Joueur tape une commande] --> IC[Intent classifier - regex]
    IC -->|out_of_universe| RJ1[Reject in-character<br/>«Le ninja ne comprend pas»]
    IC -->|meta_command| MC[Bypass LLM<br/>save / load / options]
    IC -->|ambiguous / valid| RR[Reference resolver<br/>resolve_references on StateView]
    RR -->|toujours ambigu| CL[Demande clarification<br/>in-character]
    RR -->|resolu| QR[Query rewriter<br/>EnrichedQuery]
    QR --> HR[Hybrid retrieval<br/>BM25Adapter + ChromaDenseAdapter<br/>+ RRF + reranker<br/><i>pilier 8 - branche</i>]
    HR --> RAG[Top-K chunks RAG<br/>filtres temporellement<br/><i>pilier 5 - tagging en cours</i>]
    RAG --> SG[Structured generation<br/>Pydantic NarrativeOutput<br/>contraint sur enums canon<br/><i>pilier 6B - branche</i>]
    SG --> NO[NarrativeOutput<br/>Pydantic v2]
    NO --> V[Validator orchestrator]
    V --> A[Layer A : sherlock_rules]
    V --> B[Layer B : triplet_check<br/><i>pilier 6B - branche</i>]
    V --> CL3[Layer C : age_coherence]
    A & B & CL3 --> VR{is_valid ?}
    VR -->|non, regen 1-2| RGN[Regen loop<br/>feedback structure au LLM]
    RGN --> SG
    VR -->|oui| OF[Output filter<br/>scan meta-phrases]
    OF -->|leak detecte| LL[Log leakage<br/>pour patch blacklist]
    OF --> N[Narration affichee]
    N --> US[Update RuntimeState<br/>scene + dialogue + world]
    US --> P

    classDef done fill:#9f9,stroke:#393,stroke-width:2px,color:#000
    classDef todo fill:#fdb,stroke:#a73,stroke-width:1px,stroke-dasharray:5 5,color:#000
    classDef rj fill:#fcc,stroke:#a33,stroke-width:1px,color:#000

    class IC,RR,QR,V,A,B,CL3,OF,US,NO,SG,HR done
    class RAG todo
    class RJ1,CL,RGN,LL rj
```

Legende :
- vert plein : livre, teste (couches A+B+C, structured gen, hybrid retrieval branche)
- orange pointille : tagging temporel chunks RAG en cours (Phase 5)
- rouge clair : chemin de rejet ou de feedback

Le pipeline est concu pour echouer rapidement et fournir des messages
in-character au joueur quand un rejet a lieu, sans casser la 4e mur.
