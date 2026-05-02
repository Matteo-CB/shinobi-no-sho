"""Configuration centrale via pydantic-settings.

Lit les variables depuis le .env a la racine du projet. Tous les chemins relatifs
sont resolus par rapport a la racine du projet (PROJECT_ROOT).
"""

from __future__ import annotations

from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _project_root() -> Path:
    """Racine du projet, situee a deux niveaux au-dessus de ce fichier."""
    return Path(__file__).resolve().parents[2]


PROJECT_ROOT: Path = _project_root()


class Settings(BaseSettings):
    """Settings runtime du projet."""

    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # LLM
    llm_backend_url: str = "http://127.0.0.1:8080"
    llm_model_name: str = "qwen3-4b-instruct"
    llm_model_path: str = "models/llm/Qwen3-4B-UD-Q4_K_XL.gguf"
    llm_temperature: float = 0.7
    llm_temperature_structured: float = 0.3
    llm_max_tokens: int = 800
    llm_context_size: int = 8192
    llm_timeout_seconds: int = 120
    llm_gpu_layers: int = 99
    llm_disable_thinking: bool = True

    # Embeddings
    embeddings_model_name: str = "BAAI/bge-m3"
    embeddings_device: str = "cpu"

    # Stockage (chemins relatifs au projet)
    chroma_persist_path: str = "./data/embeddings"
    saves_path: str = "./data/saves"
    canonical_data_path: str = "./data/canonical"
    raw_data_path: str = "./data/raw"
    models_path: str = "./data/models"

    # Sauvegardes
    saves_compress_payloads: bool = True
    saves_prune_old_snapshots: bool = False
    saves_snapshot_interval: int = 50

    # Logs
    log_level: str = "INFO"
    log_file_path: str = "./logs/shinobi.log"
    log_console_pretty: bool = True

    # Profil de canonicite par defaut (csv)
    canonicity_profile_sources: str = "manga,boruto_manga,tbv,databook,movie_canon"

    # Scraping (Phase 1)
    scraper_user_agent: str = "ShinobiNoSho/0.1 (private project, contact@example.local)"
    scraper_delay_seconds: float = 1.5
    scraper_concurrency: int = 3

    @field_validator("log_level")
    @classmethod
    def _normalize_log_level(cls, value: str) -> str:
        return value.upper()

    @property
    def chroma_persist_dir(self) -> Path:
        """Repertoire absolu pour la persistence ChromaDB."""
        return self._abs_path(self.chroma_persist_path)

    @property
    def saves_dir(self) -> Path:
        """Repertoire absolu des saves."""
        return self._abs_path(self.saves_path)

    @property
    def canonical_data_dir(self) -> Path:
        """Repertoire absolu des datasets canoniques."""
        return self._abs_path(self.canonical_data_path)

    @property
    def raw_data_dir(self) -> Path:
        """Repertoire absolu des donnees scrapees."""
        return self._abs_path(self.raw_data_path)

    @property
    def models_dir(self) -> Path:
        """Repertoire absolu des modeles auxiliaires."""
        return self._abs_path(self.models_path)

    @property
    def llm_model_full_path(self) -> Path:
        """Chemin absolu vers le fichier GGUF du modele primaire."""
        return self._abs_path(self.llm_model_path)

    @property
    def log_file_full_path(self) -> Path:
        """Chemin absolu du fichier de log."""
        return self._abs_path(self.log_file_path)

    @property
    def canonicity_profile_list(self) -> list[str]:
        """Liste des sources autorisees dans le profil par defaut."""
        return [s.strip() for s in self.canonicity_profile_sources.split(",") if s.strip()]

    def _abs_path(self, raw: str) -> Path:
        """Resout un chemin relatif depuis la racine du projet."""
        path = Path(raw)
        if path.is_absolute():
            return path
        return (PROJECT_ROOT / path).resolve()

    def ensure_directories(self) -> None:
        """Cree les repertoires de runtime s'ils n'existent pas."""
        for directory in (
            self.chroma_persist_dir,
            self.saves_dir,
            self.canonical_data_dir,
            self.raw_data_dir,
            self.models_dir,
            self.log_file_full_path.parent,
        ):
            directory.mkdir(parents=True, exist_ok=True)


settings = Settings()
