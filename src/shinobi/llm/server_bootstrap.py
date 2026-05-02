"""Auto-start du serveur llama.cpp en arriere-plan.

Au boot du jeu :
1. Si le serveur repond deja sur llm_backend_url -> rien a faire.
2. Sinon, cherche llama-server.exe (PATH ou %USERPROFILE%/llama.cpp/) et le modele.
3. Lance llama-server en background (fenetre detachee sur Windows pour que
   l'utilisateur puisse l'arreter manuellement).
4. Attend que /health reponde (timeout configurable, defaut 60s).

Le processus serveur survit a la fermeture de l'app (CREATE_NEW_PROCESS_GROUP).
Le user peut le tuer avec Ctrl+C dans la fenetre dediee, ou via Task Manager.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from shinobi.config import settings
from shinobi.logging_setup import get_logger

logger = get_logger(__name__)

HEALTH_TIMEOUT_SECONDS = 90
POLL_INTERVAL_SECONDS = 1.5
EXE_NAME = "llama-server.exe" if os.name == "nt" else "llama-server"


def _health_url() -> str:
    """URL /health du serveur LLM, derivee de la config."""
    return settings.llm_backend_url.rstrip("/") + "/health"


def is_server_running(timeout: float = 1.0) -> bool:
    """Ping rapide /health. True si le serveur repond."""
    try:
        with urllib.request.urlopen(_health_url(), timeout=timeout) as resp:
            return resp.status == 200
    except (urllib.error.URLError, OSError, TimeoutError):
        return False


def find_llama_server() -> Path | None:
    """Cherche llama-server dans le PATH puis aux emplacements connus."""
    in_path = shutil.which(EXE_NAME)
    if in_path:
        return Path(in_path)
    candidates = [
        Path(os.environ.get("USERPROFILE", "")) / "llama.cpp" / EXE_NAME,
        Path("C:/llama.cpp") / EXE_NAME,
        Path.cwd() / "llama.cpp" / EXE_NAME,
        Path.home() / "llama.cpp" / EXE_NAME,
        Path("/usr/local/bin") / EXE_NAME,
        Path("/usr/bin") / EXE_NAME,
    ]
    for c in candidates:
        if c.exists():
            return c
    return None


def find_model_path() -> Path | None:
    """Resout le chemin du modele GGUF depuis settings.llm_model_full_path."""
    p = settings.llm_model_full_path
    return p if p.exists() else None


def start_llama_server_background(
    *, llama_path: Path, model_path: Path
) -> subprocess.Popen | None:
    """Lance llama-server en background. Retourne le Popen (ou None si echec)."""
    port = _port_from_url(settings.llm_backend_url)
    args = [
        str(llama_path),
        "-m",
        str(model_path),
        "-ngl",
        str(settings.llm_gpu_layers),
        "-c",
        str(settings.llm_context_size),
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        "--jinja",
    ]
    logger.info("llm_server_starting", args=args)
    try:
        if os.name == "nt":
            # CREATE_NEW_CONSOLE : ouvre une fenetre dediee, l'utilisateur la voit
            # et peut Ctrl+C dedans pour l'arreter. Survit a la fermeture du jeu.
            creationflags = subprocess.CREATE_NEW_CONSOLE
            return subprocess.Popen(args, creationflags=creationflags)
        # Unix : detache via start_new_session
        return subprocess.Popen(args, start_new_session=True)
    except (OSError, FileNotFoundError) as exc:
        logger.error("llm_server_spawn_failed", error=str(exc))
        return None


def wait_for_server(timeout: int = HEALTH_TIMEOUT_SECONDS, *, console=None) -> bool:
    """Poll /health jusqu'a reponse OK ou timeout. True si OK."""
    deadline = time.time() + timeout
    last_print = 0.0
    while time.time() < deadline:
        if is_server_running(timeout=1.0):
            return True
        # Update affichage toutes les 5 sec si console fournie
        now = time.time()
        if console is not None and now - last_print >= 5.0:
            remaining = int(deadline - now)
            console.print(
                f"[dim]Serveur LLM en cours de chargement... ({remaining}s restantes)[/dim]"
            )
            last_print = now
        time.sleep(POLL_INTERVAL_SECONDS)
    return False


def ensure_llm_server(*, console=None, auto_start: bool = True) -> str:
    """Verifie que le serveur LLM est dispo. Tente de le demarrer si non.

    Retourne :
      - 'already_running' : repondait deja
      - 'started' : demarre avec succes
      - 'started_no_health' : process lance mais health timeout
      - 'no_executable' : llama-server introuvable
      - 'no_model' : modele GGUF introuvable
      - 'disabled' : auto_start=False et pas dispo
      - 'spawn_failed' : Popen a echoue
    """

    def _say(msg: str) -> None:
        if console is not None:
            console.print(msg)
        else:
            print(msg, file=sys.stderr)

    if is_server_running():
        return "already_running"

    if not auto_start:
        _say("[yellow]Serveur LLM non disponible (auto_start desactive).[/yellow]")
        return "disabled"

    llama_path = find_llama_server()
    if llama_path is None:
        _say(
            "[yellow]llama-server introuvable. Pour activer la narration LLM :\n"
            "  - Installe via setup.bat OU\n"
            "  - Telecharge llama-server depuis https://github.com/ggml-org/llama.cpp/releases\n"
            "Le jeu continue en mode mecanique (sans narration).[/yellow]"
        )
        return "no_executable"

    model_path = find_model_path()
    if model_path is None:
        _say(
            f"[yellow]Modele LLM introuvable a {settings.llm_model_full_path}.\n"
            "Telecharge Qwen3-8B-UD-Q5_K_XL.gguf depuis "
            "https://huggingface.co/unsloth/Qwen3-8B-GGUF\n"
            "Le jeu continue en mode mecanique.[/yellow]"
        )
        return "no_model"

    _say(
        f"[dim]Demarrage du serveur LLM en arriere-plan ({llama_path.name})...[/dim]"
    )
    proc = start_llama_server_background(llama_path=llama_path, model_path=model_path)
    if proc is None:
        _say("[red]Impossible de lancer llama-server. Verifie les logs.[/red]")
        return "spawn_failed"

    _say(
        "[dim]Une fenetre dediee s'est ouverte pour le serveur LLM. "
        "Chargement du modele en cours (15-45 sec selon GPU)...[/dim]"
    )
    if wait_for_server(console=console):
        _say("[green]Serveur LLM pret.[/green]")
        return "started"
    _say(
        "[yellow]Le serveur LLM est lance mais ne repond pas encore. "
        "Le jeu va commencer en mode mecanique ; la narration apparaitra "
        "des que le serveur sera pret.[/yellow]"
    )
    return "started_no_health"


def _port_from_url(url: str) -> int:
    """Extrait le port depuis une URL http://host:port. Defaut 8080."""
    try:
        from urllib.parse import urlparse

        parsed = urlparse(url)
        return parsed.port or 8080
    except Exception:
        return 8080
