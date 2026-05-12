"""First-launch language picker + reset menu (Phase i18n.2).

Affiche un panneau Rich avec les 8 langues dans leur propre script natif.
L'utilisateur choisit par numero (1-8) ou code ISO. Le choix est persiste
durablement via `shinobi.i18n.preferences.set_language` ; le runtime est
mis a jour via `shinobi.i18n.set_active_language`.

Le panneau lui-meme est volontairement multi-lingue (chaque langue affiche
son nom natif a cote du numero) pour qu'un nouvel utilisateur puisse
identifier sa langue meme s'il ne parle aucune des autres.
"""

from __future__ import annotations

from collections.abc import Callable

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt
from rich.table import Table

from shinobi.i18n import (
    NATIVE_NAMES,
    SUPPORTED_LANGUAGES,
    set_active_language,
    set_language,
)

# Etiquette du picker dans chaque langue. Affichee comme titre du Panel.
PICKER_TITLE_BY_LANGUAGE: dict[str, str] = {
    "en": "Choose your language",
    "fr": "Choisis ta langue",
    "es": "Elige tu idioma",
    "ja": "言語を選んでください",
    "zh": "请选择您的语言",
    "ko": "언어를 선택하세요",
    "pt-BR": "Escolha seu idioma",
    "de": "Wähle deine Sprache",
}

# Ligne d'invite "Enter a number 1-8 or language code".
PICKER_PROMPT_BY_LANGUAGE: dict[str, str] = {
    "en": "Enter number (1-8) or code (en, fr, ...)",
    "fr": "Tape le numero (1-8) ou le code (en, fr, ...)",
    "es": "Numero (1-8) o codigo (en, fr, ...)",
    "ja": "番号 (1-8) またはコード (en, fr, ...)",
    "zh": "输入编号 (1-8) 或代码 (en, fr, ...)",
    "ko": "번호 (1-8) 또는 코드 (en, fr, ...)",
    "pt-BR": "Numero (1-8) ou codigo (en, fr, ...)",
    "de": "Nummer (1-8) oder Code (en, fr, ...)",
}

# Confirmation post-choix dans la langue choisie.
PICKER_CONFIRM_BY_LANGUAGE: dict[str, str] = {
    "en": "Language set to {name}.",
    "fr": "Langue definie sur {name}.",
    "es": "Idioma establecido en {name}.",
    "ja": "言語を{name}に設定しました。",
    "zh": "语言已设置为{name}。",
    "ko": "언어가 {name}(으)로 설정되었습니다.",
    "pt-BR": "Idioma definido como {name}.",
    "de": "Sprache auf {name} eingestellt.",
}


def _build_table() -> Table:
    """Construit la table des 8 langues avec numero + code + nom natif."""
    table = Table.grid(padding=(0, 2))
    table.add_column("idx", style="bold cyan", justify="right")
    table.add_column("code", style="dim")
    table.add_column("native_name", style="bold")
    for i, code in enumerate(SUPPORTED_LANGUAGES, start=1):
        table.add_row(str(i), code, NATIVE_NAMES[code])
    return table


def _build_panel_title() -> str:
    """Combine les 8 traductions du titre, separees par ' / '.

    Permet a chaque utilisateur de reconnaitre l'invite dans sa langue
    sans que le picker soit deja localise dans une langue choisie.
    """
    return " / ".join(
        PICKER_TITLE_BY_LANGUAGE[code] for code in SUPPORTED_LANGUAGES
    )


def _resolve_choice(raw: str) -> str | None:
    """Resout l'input utilisateur en code de langue.

    Accepte :
    - numero 1-8 (1-based)
    - code ISO (en, fr, ja, zh, ko, pt-BR, de, es) case-insensitive

    Retourne None si l'input est invalide.
    """
    raw = raw.strip()
    if not raw:
        return None
    # Numero ?
    if raw.isdigit():
        idx = int(raw)
        if 1 <= idx <= len(SUPPORTED_LANGUAGES):
            return SUPPORTED_LANGUAGES[idx - 1]
        return None
    # Code direct ?
    lower = raw.lower()
    for code in SUPPORTED_LANGUAGES:
        if code.lower() == lower:
            return code
    return None


def show_picker(
    console: Console | None = None,
    *,
    prompt_fn: Callable[..., str] | None = None,
    persist: bool = True,
) -> str:
    """Affiche le picker, retourne le code de langue choisi.

    Boucle jusqu'a ce que l'utilisateur entre une valeur valide. Apres choix :
    - Met a jour la langue runtime active via `set_active_language`.
    - Si `persist=True`, sauve durablement via `set_language`
      (preferences.json + first_launch_completed=True).

    Args:
        console: Rich Console. Si None, en cree une.
        prompt_fn: Fonction de prompt injectable (tests). Default Prompt.ask.
        persist: Sauve sur disque. False pour preview / tests.

    Returns:
        Code ISO de la langue choisie.
    """
    if console is None:
        console = Console()
    if prompt_fn is None:
        prompt_fn = Prompt.ask

    table = _build_table()
    title = _build_panel_title()
    console.print(Panel(table, title=title, border_style="cyan"))

    multi_prompt = " / ".join(
        PICKER_PROMPT_BY_LANGUAGE[code] for code in SUPPORTED_LANGUAGES
    )

    while True:
        raw = prompt_fn(f"[bold cyan]{multi_prompt}[/bold cyan]")
        choice = _resolve_choice(raw)
        if choice is not None:
            break
        console.print(
            "[yellow]Invalid input. "
            "Saisie invalide. "
            "Entrada invalida. "
            "無効な入力。 "
            "无效输入。 "
            "잘못된 입력。 "
            "Entrada invalida. "
            "Ungultige Eingabe.[/yellow]"
        )

    set_active_language(choice)
    if persist:
        set_language(choice)

    confirm_msg = PICKER_CONFIRM_BY_LANGUAGE[choice].format(
        name=NATIVE_NAMES[choice],
    )
    console.print(f"[green]{confirm_msg}[/green]")
    return choice


def maybe_show_first_launch_picker(
    console: Console | None = None,
    *,
    prompt_fn: Callable[..., str] | None = None,
) -> str | None:
    """Si `first_launch_completed` est False, affiche le picker. Sinon no-op.

    Initialise aussi la langue runtime depuis preferences.json.

    Returns:
        Code de langue choisi si le picker a ete affiche, sinon None.
    """
    from shinobi.i18n import (
        initialize_from_preferences,
        needs_first_launch_picker,
    )

    if not needs_first_launch_picker():
        # Initialise simplement la langue runtime depuis le fichier persiste
        initialize_from_preferences()
        return None

    return show_picker(console=console, prompt_fn=prompt_fn)


def run_language_reset_menu(
    console: Console | None = None,
    *,
    prompt_fn: Callable[..., str] | None = None,
) -> str:
    """Re-affiche le picker pour changer de langue (commande slash /language).

    Toujours persistante (set_language ecrase l'existant).
    """
    return show_picker(console=console, prompt_fn=prompt_fn, persist=True)
