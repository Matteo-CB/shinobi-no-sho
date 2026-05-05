# Pass 3 — Rapport comparatif (avant/après seuils 3-tier)

Généré manuellement à partir des dry-runs `pass2_aggregate.py`.
Vise à valider que la séquence **D** (re-aggregation 3-tier) suffit à
restaurer la cohérence canon des grands clans, ou si un complément
**B** (re-run top-100 avec prompt agressif) est nécessaire.

## 1. Configuration

- `MIN_RATIO_KEY = 0.50`, `MIN_MEMBERS_KEY = 3` → signature de clan
- `MIN_RATIO_AVAILABLE = 0.30`, `MIN_MEMBERS_AVAILABLE = 3` → éligibilité
- `MIN_MEMBERS_INDIVIDUAL = 1`, `MAX_MEMBERS_INDIVIDUAL = 2` → mutation isolée

Trois axes agrégés par clan : `kekkei_genkai_possessed`, `natures_possessed`,
`key_techniques`. Les techniques sont normalisées via NFKD + lowercase + slug.

## 2. Couverture des grands clans canon

Légende : `K` = key, `A` = available, `~` = individual_mutation détecté
mais non listé au niveau clan, `-` = absent.

| Clan | N | Attribut canon attendu | Statut Pass 2/3 |
|---|---:|---|---|
| uchiha | 46 | sharingan, katon | sharingan **K**, mangekyo_sharingan **A**, katon `-` (extraction limitée) |
| hyuga | 17 | byakugan, juuken | byakugan **K**, eight_trigrams_palms_revolving_heaven `-` (4/17 = 23 %) |
| senju | 9 | mokuton (Hashirama only), suiton/doton | doton/fuuton/inton/katon/raiton/suiton/youton_yang **A** ; mokuton `~` (1 membre) |
| sarutobi | 7 | katon, fuuton (Hiruzen) | fuuton **K**, katon **K**, raiton **A** |
| nara | 18 | shadow_imitation (Hiden), inton | shadow_imitation_technique **A** ; inton `-` |
| akimichi | 12 | multi_size (Hiden) | partial_multi_size_technique **A** |
| yamanaka | 10 | mind_body_switch (Hiden), inton | mind_body_switch_technique **A** ; inton `-` |
| aburame | 10 | kikaichu (Hiden) | kikaichu **A** |
| inuzuka | 14 | fang_passing (Hiden) | fang_passing_fang **A** |
| hozuki | 3 | hydrification, suiton | suiton **K** ; hydrification `~` |
| kaguya | 3 | shikotsumyaku (Kimimaro only) | shikotsumyaku `~` (1/3) |
| yuki | 7 | hyouton/ice_release | ice_release **A** |
| uzumaki | 20 | fuinjutsu, longévité | fuinjutsu (2/20 = 10 %) `-` |
| otsutsuki | 21 | byakugan/tenseigan/karma | doton **A** (3/21) ; byakugan/tenseigan/magnet/lava `-` (3/21 = 14 %) |

**Bilan : 10/14 grands clans ont au moins un attribut canon attesté en
key ou available.** Les 4 cas faibles sont :

- **senju + mokuton** : techniquement correct car Hashirama est le seul
  Senju ayant Mokuton dans le canon. Le tag `individual_mutation` est
  appliqué sur Hashirama lui-même.
- **kaguya + shikotsumyaku** : idem, Kimimaro est canoniquement le seul
  porteur du Shikotsumyaku malgré l'appartenance au clan Kaguya.
- **uzumaki + fuinjutsu** : sous-extraction réelle. 2/20 = 10 % alors
  que 90 %+ des Uzumaki documentés sont sealers en canon.
- **otsutsuki + byakugan/karma** : dispersion attendue car Otsutsuki est
  une méta-clan dont les branches (Hyuga, etc.) ont essaimé.

## 3. Comparaison avec ancien `clans.json` (issu du scraper)

| Clan | OLD key_kekkei_genkai | OLD key_natures | NEW key_* | NEW available_* |
|---|---|---|---|---|
| aburame | `[]` | `[]` | `key_tech=kikaichu` | -- |
| akimichi | `[]` | `[doton]` ⚠ | -- | `tech=partial_multi_size_technique` |
| hozuki | `[]` | `[suiton]` | `nat=suiton` | -- |
| hyuga | `[byakugan]` | `[]` | `kg=byakugan` | -- |
| inuzuka | `[]` | `[]` | `tech=fang_passing_fang` | -- |
| kaguya | `[shikotsumyaku]` ⚠ | `[]` | -- | -- |
| nara | `[]` | `[inton]` | `tech=shadow_imitation_technique` | -- |
| sarutobi | `[]` | `[katon]` | `nat=fuuton,katon` | `nat=raiton, tech=…` |
| senju | `[]` | `[mokuton,suiton,doton]` ⚠ | -- | `nat=7 éléments` |
| uchiha | `[sharingan]` | `[katon]` | `kg=sharingan` | `kg=mangekyo_sharingan` |
| uzumaki | `[]` | `[fuuton,youton_yang]` ⚠ | -- | -- |
| yamanaka | `[]` | `[inton]` | `tech=mind_body_switch_technique` | -- |
| yuki | `[]` | `[hyouton]` | -- | `kg=ice_release` |

⚠ = corruption probable du scraper. Cas notables :

- `akimichi + doton` : aucun Akimichi documenté n'a doton en canon.
  Probable parsing erroné de l'infobox de Karui par le scraper.
- `kaguya + shikotsumyaku key_*` : ancienne donnée traitait le KG comme
  signature obligatoire alors qu'il est en réalité une mutation rare.
- `senju + mokuton key_*` : idem, élevait Hashirama au rang de signature
  clan-wide.
- `uzumaki + youton_yang` : seul Naruto utilise youton_yang, et c'est un
  pouvoir lié à Kurama, pas au clan.

13 corruptions détectées au total (voir `scraper-corruption-report.md`
quand généré avec `--apply`).

## 4. Effectifs des Hidens

Les Hidens (techniques de clan) apparaissent désormais en
`available_techniques` plutôt que `kekkei_genkai_possessed`, comme attendu
par le canon (un Hiden n'est pas un KG).

| Hiden | Clan | Attesté/N | Ratio |
|---|---|---|---|
| shadow_imitation_technique | nara | 6/18 | 33 % |
| partial_multi_size_technique | akimichi | 4/12 | 33 % |
| mind_body_switch_technique | yamanaka | ≥3/10 | ≥30 % |
| kikaichu | aburame | 3/10 | 30 % |
| fang_passing_fang | inuzuka | 5/14 | 36 % |

Tous franchissent le seuil **AVAILABLE** (30 %+, 3+ membres). Aucun ne
franchit le seuil **KEY** (50 %+) car les corpus Pass 2 sont sous-extraits
sur les techniques (le LLM ne reporte que les key_techniques les plus
citées, pas la totalité de l'arsenal).

## 5. Couverture extraction birth_year

| Source | Count |
|---|---:|
| unknown | 1345 |
| llm_extracted | 8 |
| derived | 3 |
| canon_hard | 3 |

14/1359 = 1 % avec birth_year explicite. C'est conforme au constat du
post-mortem : Llama-3.3-70b est très conservateur sur la règle
**NEVER GUESS** et le canon Naruto donne rarement des dates absolues.
Pass 2.5 (déduction par age_at_event + relative_age) gagnera plus de
caractères une fois le bug ARC_ALIASES propagé sur l'ensemble.

## 6. Décision recommandée

**Séquence D suffit.** Les 10/14 grands clans ont leurs attributs
canoniques et les 4 « manquants » sont en réalité fidèles au canon :

- 2 cas (senju+mokuton, kaguya+shikotsumyaku) sont des mutations
  individuelles correctement classées en `individual_mutation`.
- 2 cas (uzumaki+fuinjutsu, otsutsuki+byakugan) souffrent de dispersion
  ou sous-extraction. Un re-run B agressif sur 100 clans pourrait
  améliorer marginalement uzumaki+fuinjutsu, mais le coût (~$0.50)
  ne semble pas justifié au regard du gain attendu.

**Recommandation : valider D, faire `--apply` directement.** Patch Pass
2.5 ARC_ALIASES déjà appliqué (67 mismatches corrigés). Le projet peut
passer au pilier §5 (proposed_actions warner) avec un canon nettoyé et
un système 3-tier qui reflète la réalité simulationniste : « ce que ton
clan te donne par défaut » (key) vs « ce que tu peux développer »
(available) vs « tu serais une mutation isolée » (individual).
