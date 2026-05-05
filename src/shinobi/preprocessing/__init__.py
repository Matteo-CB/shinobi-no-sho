"""Preprocessing de la query joueur : resolution referentielle et enrichissement.

`reference_resolver` : resout pronoms et ellipses via un StateView (Protocol).
`query_rewriter` : pipeline complet (intent classification -> resolution -> enriched query).

Le StateView est un Protocol minimal pour decoupler du pilier 4 (state tracker).
Implementation par defaut : NullStateView (renvoie None partout). Quand le pilier
4 sera implemente, le tracker exposera un StateView reel.
"""

from __future__ import annotations
