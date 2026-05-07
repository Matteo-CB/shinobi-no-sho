"""Phase H : pipeline d'extraction LLM offline pour enrichir le canon.

Spec doc 02 §9 : 5 datasets a produire one-shot via Anthropic Sonnet 4.6
Batch API (50% off) :

- 9.1 Timeline events enrichis (preconditions/outcomes structures + alternative_seeds)
- 9.2 Motivations profondes top-50 PNJ
- 9.3 Forces politiques (factions, alliances, tensions)
- 9.4 Moments charnieres (divergence points)
- 9.5 Patterns Kishimoto (style guide narrator)

Budget cible : <$25 sur les $30 credits Anthropic.

Strategie :
- Batch API pour bulk (50% off : input $1.5/M, output $7.5/M)
- Synchronous API pour pilots/dev
- Cost tracker strict avec hard cap a $25
- Pydantic validation outputs (refuse hallucinations)
"""
