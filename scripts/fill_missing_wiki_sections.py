"""Comble manuellement les wiki_sections des entrees qui n'ont pas matche.

Pour les entrees dont les pages Narutopedia ne portent pas le meme nom que
le canon manuel (ex: shintenshin_no_jutsu vs Mind Body Switch Technique),
on ecrit directement les sections de reference.

Format des entrees : { id: { Section_Title: text, ... } }

Usage : python scripts/fill_missing_wiki_sections.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

CANON_DIR = ROOT / "data" / "canonical"


# Tailed beasts ----------------------------------------------------------------
TAILED_BEASTS_FILL: dict[str, dict[str, str]] = {
    "juubi": {
        "Background": (
            "Le Juubi (Bete a dix queues, Shinju) est l'incarnation originelle de tous les bijuus. "
            "Le Sage des Six Chemins (Hagoromo Otsutsuki) l'a scinde en neuf bijuus distincts pour "
            "empecher sa puissance destructrice. Son corps original (Shinju, l'arbre divin) etait nourri "
            "par le fruit du chakra que Kaguya Otsutsuki a consomme."
        ),
        "Personality": (
            "Le Juubi n'a pas de personnalite consciente comme les bijuus separes. Pure force destructrice "
            "lorsque revele en mode incomplet. Devient sentient lors de sa pleine reformation."
        ),
        "Abilities": (
            "Pouvoir absolu : capable de detruire des continents avec ses Tailed Beast Balls (Bijudama). "
            "Maitre de toutes les natures de chakra. Son chakra est noir et corrompu. "
            "Une fois reforme, il transcende les bijuus individuels."
        ),
        "Trivia": (
            "Reapparait lors de la 4e Guerre Mondiale Shinobi (an 16) reforme par Obito Uchiha puis "
            "Madara Uchiha qui devient son jinchuuriki. Eventuellement scelle a nouveau."
        ),
    },
}


# Hidens (techniques secretes de clan) ----------------------------------------
HIDEN_FILL: dict[str, dict[str, str]] = {
    "shintenshin_no_jutsu": {
        "Description": (
            "Technique de transposition mentale du clan Yamanaka (Konoha). L'utilisateur projette son "
            "esprit hors de son corps pour prendre temporairement le controle d'une cible. Le corps "
            "du lanceur reste inanime pendant la duree de l'echange et est extremement vulnerable."
        ),
        "Limitations": (
            "Ne fonctionne que sur cible immobile ou peu mobile (l'esprit voyage en ligne droite et lente). "
            "L'utilisateur perd les sensations de son propre corps. Si la cible meurt avec l'esprit dedans, "
            "l'utilisateur meurt aussi."
        ),
        "Trivia": (
            "Ino Yamanaka apprend cette technique d'enfance. Inoichi Yamanaka, son pere, est l'un des "
            "maitres les plus accomplis de la technique."
        ),
    },
    "mushi_yose_no_jutsu": {
        "Description": (
            "Technique d'appel des insectes du clan Aburame (Konoha). Permet a l'utilisateur de "
            "convoquer ses kikaichu (insectes parasites) pour les faire emerger de son corps ou les "
            "rappeler depuis une distance."
        ),
        "Limitations": (
            "Necessite que l'utilisateur ait deja une colonie de kikaichu vivant en symbiose avec lui "
            "(implant des la naissance dans le clan Aburame)."
        ),
    },
    "juuken": {
        "Description": (
            "Le Poing Souple (Gentle Fist) est le style de combat exclusif du clan Hyuga (Konoha). "
            "Combine avec le Byakugan, il permet de viser et fermer les tenketsus (canaux de chakra) "
            "de l'adversaire d'une simple frappe, paralysant les organes internes."
        ),
        "Background": (
            "Maitrise par tous les Hyuga des leur enfance. Necessite un controle ultra-precis du "
            "chakra dans les paumes et un Byakugan actif pour voir le reseau de chakra de la cible."
        ),
        "Trivia": (
            "Hiashi Hyuga, Hizashi Hyuga, Neji Hyuga et Hinata Hyuga sont des praticiens notables. "
            "Permet l'utilisation des techniques signatures comme les Soixante-Quatre Paumes."
        ),
    },
    "ougon_no_kuro_kogane_no_seishin": {
        "Description": (
            "Esprit d'or noir (Ougon no Kuro Kogane) : technique secrete tres rare utilisant des "
            "metaux specifiques pour creer des protections / armes spirituelles. Affiliation clanique "
            "incertaine, considere comme un kinjutsu mineur."
        ),
        "Trivia": (
            "Apparait dans des contextes filler/anime, peu document dans le canon manga principal."
        ),
    },
    "irezumi_fuin": {
        "Description": (
            "Sceau-tatouage : technique de fuinjutsu permettant d'apposer un sceau qui s'integre "
            "comme un tatouage sur la peau. Utilise pour stocker des objets, du chakra, ou pour "
            "marquer un porteur (ex: sceau de la marque maudite)."
        ),
        "Limitations": (
            "Necessite des connaissances avancees en fuinjutsu. Les sceaux complexes (comme la "
            "marque maudite d'Orochimaru) requierent un rituel et un sacrifice."
        ),
    },
    "kurama_genjutsu": {
        "Description": (
            "Genjutsu du clan Kurama (Konoha) : illusions extremement puissantes, capables de tuer la "
            "cible si elle croit assez fort a l'illusion. Le clan Kurama possede une ligne de chakra "
            "specialisee dans la perception et la manipulation de l'esprit."
        ),
        "Background": (
            "Le clan Kurama est presque eteint au moment de Naruto. Yakumo Kurama est l'une des "
            "dernieres heritieres. Le clan a ete decime par sa propre instabilite mentale."
        ),
        "Trivia": (
            "A ne pas confondre avec Kurama, le Kyuubi (Bete a Neuf Queues) - aucun lien direct."
        ),
    },
    "doku_kakou": {
        "Description": (
            "Synthese de poison : ensemble de techniques chimiques permettant de fabriquer, stocker "
            "et appliquer des poisons sur des armes (kunai, senbon) ou de les vaporiser. Specialite "
            "des shinobis de Sunagakure et de certains medic-nin."
        ),
        "Background": (
            "Sasori du Sable est le maitre incontesté. Ses poisons paralysants ou letaux sont actifs "
            "en quelques secondes. Sakura Haruno apprend les antidotes correspondants."
        ),
    },
    "shouten_no_jutsu": {
        "Description": (
            "Transfert d'ascension (Shouten no Jutsu) : technique de Pain et de l'Akatsuki permettant "
            "a un utilisateur de prendre le controle a distance d'un autre corps via un fragment de "
            "chakra. Le 'corps' devient une marionnette vivante du contrologue."
        ),
        "Limitations": (
            "Necessite un fragment du corps cible (cheveu, sang) et un sceau prealable. Si la connexion "
            "est rompue, le corps controle peut mourir ou tomber inconscient."
        ),
        "Trivia": (
            "Pain a six corps via cette technique (les Six Chemins de Pain). Yahiko, son corps "
            "principal, etait deja mort et reanime par cette technique."
        ),
    },
}


def fill_dataset(json_filename: str, fill: dict[str, dict[str, str]]) -> None:
    path = CANON_DIR / json_filename
    data = json.loads(path.read_text(encoding="utf-8"))
    filled = 0
    for entry in data:
        eid = entry.get("id")
        if eid in fill and not entry.get("wiki_sections"):
            entry["wiki_sections"] = fill[eid]
            filled += 1
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    print(f"  {json_filename}: {filled} entrees remplies manuellement")


def main() -> None:
    print("Remplissage manuel des entrees orphelines...")
    fill_dataset("tailed_beasts.json", TAILED_BEASTS_FILL)
    fill_dataset("hiden.json", HIDEN_FILL)
    print("Fait.")


if __name__ == "__main__":
    main()
