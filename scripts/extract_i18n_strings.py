"""Phase i18n.3 : extraction AST des chaines FR a localiser dans la CLI.

Scanne les modules CLI pour identifier les literals FR a remplacer par
des appels `t(key, ...)`. Detecte :
- Strings literals dans : `console.print()`, `Panel()`, `Prompt.ask()`,
  `typer.echo()`, `typer.confirm()`, `Confirm.ask()`, `Table(title=...)`.
- F-strings : capture les segments litteraux + variables interpolees.

Sortie : `data/i18n/_extracted.json` avec entrees :
{
  "key": "cli.module.heading.context",
  "module": "cli/menu.py",
  "lineno": 38,
  "col_offset": 4,
  "type": "Constant" | "JoinedStr",
  "fr": "Nouvelle partie",
  "vars": ["..."]   // pour f-strings
}

Heuristique cle : `<scope>.<area>.<key_snake>` derive du contexte.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from collections.abc import Iterator
from pathlib import Path

# Modules CLI a scanner.
CLI_MODULES = [
    "src/shinobi/cli/display.py",
    "src/shinobi/cli/streaming_display.py",
    "src/shinobi/cli/menu.py",
    "src/shinobi/cli/canon_incarnation.py",
    "src/shinobi/cli/app.py",
    "src/shinobi/cli/character_creation.py",
    "src/shinobi/cli/play.py",
]

# Detection : la chaine contient au moins un caractere accentue francais
# OU c'est un mot/phrase clairement francais (mots-cles).
ACCENT_RE = re.compile(r"[éèêëàâäîïôöùûüç]", re.IGNORECASE)

# Mots-cles FR sans accent (pour les chaines comme "Nom", "Lieu", "Confirmer").
FR_KEYWORDS = frozenset({
    "Nom", "Age", "Rang", "Date", "Lieu", "Choix", "Confirmer", "Quitter",
    "Continuer", "Retour", "Aucune", "Aucun", "Erreur", "Genie", "Maitrise",
    "Annee", "Tours", "Nouvelle", "Charger", "Creer", "Supprimer", "Dupliquer",
    "Exporter", "Importer", "Lister", "Gerer", "Personnage", "Saves",
    "Configuration", "Mode", "Selection", "Numero", "Recherche", "Apercu",
    "Stats", "Techniques", "Objectifs", "Journal", "Action", "Actions",
    "Difficulte", "Duree", "Etudes", "Branche", "Ouvrir", "Vendre", "Acheter",
    "Promotion", "Trauma", "Famille", "Volonte",
    # Extension Phase i18n.3
    "Genre", "Inventaire", "Recap", "Periode", "Description", "Titre",
    "Recompense", "Succes", "Echec", "Pistes", "Rumeurs", "Reputation",
    "Biographie", "Naissance", "Naturel", "Fierte", "Honneur", "Piege",
    "Ere", "Ress", "Effet", "Niveau", "Bouclier", "Foudre", "Feu", "Eau",
    "Vent", "Terre", "Glace", "Bois", "Arc", "Phase", "Tableau", "Rangs",
    "Ressources", "Mission", "Missions", "Inventaire", "Vide", "Equipee",
    "Equipees", "Possedee", "Possede", "Consommables", "Outils", "Armes",
    "Statut", "Sante", "Mort", "Vivant", "Endurance", "Patience",
    "Intervention", "Decompte", "Resultat", "Evenement", "Evenements",
    "Sanglant", "Nukenin", "Deserteur", "Desertion", "Bingo", "Trahison",
    "Allie", "Ennemi", "Initiation", "Bibliotheque", "Marchand", "Vendeur",
    "Sage", "Sannin", "Disciple", "Apprenti", "Maitre", "Sensei",
})

# Functions whose string args are user-visible.
USER_VISIBLE_FUNCS = frozenset({
    "print", "ask", "confirm", "echo",
    "add_column", "add_row",
})


def is_french_text(s: str) -> bool:
    """True si `s` ressemble a du francais user-visible."""
    if not s or not isinstance(s, str):
        return False
    if len(s) < 2:
        return False
    if ACCENT_RE.search(s):
        return True
    # Detection sans accent : un mot du keyword set
    words = re.findall(r"\b\w+\b", s)
    return any(w in FR_KEYWORDS for w in words)


def slugify_for_key(text: str, max_words: int = 4) -> str:
    """Genere un suffixe snake_case court a partir de `text`."""
    # Strip Rich markup [...]
    cleaned = re.sub(r"\[/?[^\]]*\]", "", text)
    # Strip f-string vars {...}
    cleaned = re.sub(r"\{[^}]*\}", "", cleaned)
    # Lower + replace non-alnum
    cleaned = re.sub(r"[^a-zA-Z0-9 ]", "", cleaned)
    cleaned = cleaned.lower().strip()
    words = [w for w in cleaned.split() if w][:max_words]
    if not words:
        return "msg"
    return "_".join(words)


def call_func_name(node: ast.Call) -> str | None:
    """Retourne le nom du callable invoque, ou None."""
    func = node.func
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return None


class StringExtractor(ast.NodeVisitor):
    def __init__(self, module_path: str, source: str):
        self.module_path = module_path
        self.module_short = Path(module_path).stem  # ex: "menu"
        self.source = source
        self.entries: list[dict] = []
        self._counter = 0

    def _entry_key(self, suffix: str) -> str:
        self._counter += 1
        return f"cli.{self.module_short}.{suffix}__{self._counter:03d}"

    def _emit_constant(self, node: ast.Constant) -> None:
        if not isinstance(node.value, str):
            return
        if not is_french_text(node.value):
            return
        suffix = slugify_for_key(node.value)
        self.entries.append({
            "key": self._entry_key(suffix),
            "module": self.module_path,
            "lineno": node.lineno,
            "col_offset": node.col_offset,
            "end_lineno": getattr(node, "end_lineno", node.lineno),
            "end_col_offset": getattr(node, "end_col_offset", None),
            "type": "Constant",
            "fr": node.value,
            "vars": [],
        })

    def _emit_joined(self, node: ast.JoinedStr) -> None:
        # Reconstruit le template f-string : segments litteraux + {var}
        parts: list[str] = []
        var_names: list[str] = []
        for v in node.values:
            if isinstance(v, ast.Constant) and isinstance(v.value, str):
                parts.append(v.value)
            elif isinstance(v, ast.FormattedValue):
                # Recompose le placeholder name a partir de l'expression
                if isinstance(v.value, ast.Name):
                    var_names.append(v.value.id)
                    parts.append("{" + v.value.id + "}")
                elif isinstance(v.value, ast.Attribute):
                    chain = []
                    cur = v.value
                    while isinstance(cur, ast.Attribute):
                        chain.append(cur.attr)
                        cur = cur.value
                    if isinstance(cur, ast.Name):
                        chain.append(cur.id)
                    name = "_".join(reversed(chain))
                    var_names.append(name)
                    parts.append("{" + name + "}")
                else:
                    # Expression complexe : on met un placeholder generique
                    var_names.append("v" + str(len(var_names)))
                    parts.append("{" + var_names[-1] + "}")
            else:
                # Ignore les noeuds inattendus
                return
        template = "".join(parts)
        if not is_french_text(template):
            return
        suffix = slugify_for_key(template)
        self.entries.append({
            "key": self._entry_key(suffix),
            "module": self.module_path,
            "lineno": node.lineno,
            "col_offset": node.col_offset,
            "end_lineno": getattr(node, "end_lineno", node.lineno),
            "end_col_offset": getattr(node, "end_col_offset", None),
            "type": "JoinedStr",
            "fr": template,
            "vars": var_names,
        })

    def visit_Call(self, node: ast.Call) -> None:
        fname = call_func_name(node)
        if fname in USER_VISIBLE_FUNCS or fname == "Panel" or fname == "Confirm":
            for arg in node.args:
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    self._emit_constant(arg)
                elif isinstance(arg, ast.JoinedStr):
                    self._emit_joined(arg)
            for kw in node.keywords:
                if kw.arg in ("border_style", "header_style", "style", "default"):
                    continue  # styles / defaults techniques, pas a localiser
                if isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
                    if is_french_text(kw.value.value):
                        self._emit_constant(kw.value)
                elif isinstance(kw.value, ast.JoinedStr):
                    self._emit_joined(kw.value)
        self.generic_visit(node)


def extract_module(path: Path) -> list[dict]:
    src = path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(src, filename=str(path))
    except SyntaxError as exc:
        print(f"[!] SyntaxError in {path}: {exc}", file=sys.stderr)
        return []
    extractor = StringExtractor(str(path).replace("\\", "/"), src)
    extractor.visit(tree)
    return extractor.entries


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--out",
        default="data/i18n/_extracted.json",
        help="Chemin de sortie pour le catalogue extrait.",
    )
    parser.add_argument(
        "--root",
        default=".",
        help="Racine du projet (default: cwd).",
    )
    args = parser.parse_args()

    root = Path(args.root).resolve()
    all_entries: list[dict] = []
    for rel in CLI_MODULES:
        path = root / rel
        if not path.exists():
            print(f"[!] Skip missing: {rel}", file=sys.stderr)
            continue
        entries = extract_module(path)
        all_entries.extend(entries)
        print(f"[+] {rel}: {len(entries)} strings", file=sys.stderr)

    out_path = root / args.out
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(all_entries, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"[OK] {len(all_entries)} entries -> {out_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
