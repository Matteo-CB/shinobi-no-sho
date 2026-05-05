"""Garde-fous I/O entre le joueur et le LLM (pilier 2 du plan anti-hallucination).

Trois etages :
- blacklist : termes hors-univers (programmation, tech moderne, IA, autres oeuvres)
- intent_classifier : pre-filter de la query joueur avec classification d'intent
- output_filter : post-filter de la sortie LLM (meta-phrases, casse 4e mur, etc.)

Aucun appel LLM dans ce module : tout est deterministe et sub-millisecond.
"""

from __future__ import annotations
