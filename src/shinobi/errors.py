"""Classes d'exception du projet.

Toutes les erreurs metier derivent de ShinobiError pour permettre la capture
selective sans recourir a un except Exception nu.
"""

from __future__ import annotations


class ShinobiError(Exception):
    """Erreur racine du projet."""


class ConfigError(ShinobiError):
    """Configuration manquante ou invalide."""


class CanonError(ShinobiError):
    """Erreur liee aux donnees canoniques."""


class CanonValidationError(CanonError):
    """Une regle de coherence sur les donnees canoniques a ete violee."""


class CanonReferenceError(CanonError):
    """Reference vers un id qui n'existe pas dans le dataset canonique."""


class CanonLoadError(CanonError):
    """Impossible de charger un dataset canonique."""


class RagError(ShinobiError):
    """Erreur dans le pipeline RAG."""


class EmbeddingError(RagError):
    """Erreur d'embedding (modele indisponible, dimension incorrecte, etc.)."""


class RetrievalError(RagError):
    """Erreur de retrieval ChromaDB."""


class LLMError(ShinobiError):
    """Erreur dans le client LLM ou la generation."""


class LLMTimeoutError(LLMError):
    """Le serveur LLM n'a pas repondu dans le delai imparti."""


class LLMUnavailableError(LLMError):
    """Le serveur LLM n'est pas joignable."""


class LLMResponseError(LLMError):
    """Le LLM a repondu mais la reponse est inutilisable."""


class LLMSchemaError(LLMResponseError):
    """La sortie du LLM ne respecte pas le schema attendu."""


class LLMStyleError(LLMResponseError):
    """La sortie du LLM contient un pattern interdit (em dash, emoji, argot)."""


class EngineError(ShinobiError):
    """Erreur dans le moteur deterministe."""


class ImpossibleActionError(EngineError):
    """Une action est physiquement irrealisable et ne peut pas etre resolue."""


class StateInconsistencyError(EngineError):
    """L'etat du moteur est dans un etat impossible."""


class GoalError(ShinobiError):
    """Erreur dans le systeme d'objectifs."""


class PriceUnaffordableError(GoalError):
    """Le joueur ne peut pas payer le prix demande pour un breadcrumb."""


class StateError(ShinobiError):
    """Erreur dans le state tracker runtime (pilier 4 du plan anti-hallucination)."""


class CharacterNotFoundError(StateError):
    """Le personnage demande n'existe pas dans le canon."""


class CharacterNotYetBornError(StateError):
    """Le personnage existe mais n'est pas encore ne a la date demandee."""


class CharacterDeadError(StateError):
    """Le personnage est deja mort a la date demandee."""


class PersistenceError(ShinobiError):
    """Erreur lors d'une operation de sauvegarde ou chargement."""


class SaveNotFoundError(PersistenceError):
    """Le save_id demande n'existe pas."""


class SaveCorruptError(PersistenceError):
    """Le contenu d'une save est incoherent ou illisible."""


class SchemaMigrationError(PersistenceError):
    """Une migration de schema a echoue."""


class CLIError(ShinobiError):
    """Erreur d'usage de la CLI."""


class ScrapingError(ShinobiError):
    """Erreur dans le pipeline de scraping."""
