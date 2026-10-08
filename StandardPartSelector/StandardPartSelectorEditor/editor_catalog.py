# -*- coding: utf-8 -*-
"""
editor_catalog — Lecture/écriture des données catalogue.
"""
# -*- coding: utf-8 -*-
"""
Macro: StandardPartSelectorEditor
Editeur de catalogues pour StandardPartSelector.
Wizard en 5 étapes :
  0. Choix de l'action
  1. Source (FCStd ou famille existante)
  2. Métadonnées (composant / famille / variante)
  3. Colonnes (paramètres VarSet + paramètres muets)
  4. Références (tableau éditable)
  5. Aperçu et sauvegarde
"""
import FreeCAD as App
from editor_config import _dbg
import os, json, shutil, ast, types, locale
from datetime import datetime



# ============================================================
# Qt
# ============================================================
from editor_config import (_get_qt, get_library_path,
                           set_library_path, sanitize_filename,
                           MACRO_DIR, MACROS_DIR, STANDARDS_DIR,
                           CATEGORY_LABELS_FILE)

def discover_components():
    """Scanne STANDARDS_DIR + bibliothèque externe."""
    components = {}
    if not os.path.isdir(MACRO_DIR):
        return components
    lib = get_library_path()
    scan_dirs = [STANDARDS_DIR]
    if lib and lib != STANDARDS_DIR and os.path.isdir(lib):
        scan_dirs.append(lib)
    # STANDARDS_DIR prioritaire : scanner lib EN PREMIER, puis STANDARDS_DIR
    # → STANDARDS_DIR écrase lib pour les composants présents aux deux endroits
    all_entries = {}
    for scan_dir in reversed(scan_dirs):   # lib d'abord, STANDARDS_DIR ensuite
        for entry in sorted(os.listdir(scan_dir)):
            all_entries[entry] = scan_dir
    for entry, scan_dir in sorted(all_entries.items()):
        if entry.startswith('_'):
            continue
        comp_dir    = os.path.join(scan_dir, entry)
        config_file = os.path.join(comp_dir, 'config.json')
        if not (os.path.isdir(comp_dir) and os.path.exists(config_file)):
            continue
        try:
            with open(config_file, encoding='utf-8') as f:
                raw = json.load(f)
            datas_dir  = os.path.join(comp_dir, 'datas')
            models_dir = os.path.join(comp_dir, 'models')
            # Bibliothèque externe si configurée
            lib = get_library_path()
            if lib:
                lib_models = os.path.join(lib, entry, 'models')
            else:
                lib_models = models_dir
            cfg = {}
            for family, variants in raw.items():
                if family.startswith('_') or not isinstance(variants, dict):
                    continue
                cfg[family] = {}
                for variant, vdata in variants.items():
                    if variant.startswith('_'):
                        continue
                    _fn = vdata.get('fcstd', '')
                    # Résoudre le chemin FCStd
                    if _fn:
                        _bn = os.path.basename(_fn)
                        _p_lib = os.path.join(lib_models, _bn)
                        _p_loc = os.path.join(models_dir, _bn)
                        fcstd_abs = _p_lib if os.path.exists(_p_lib) else _p_loc
                    else:
                        fcstd_abs = None
                    cfg[family][variant] = {
                        'json':    os.path.join(datas_dir,
                                                os.path.basename(vdata.get('json',''))),
                        'fcstd':   fcstd_abs,
                        'columns': vdata.get('columns', []),
                    }
            components[entry] = {
                'dir':    comp_dir,
                'config': config_file,
                'raw':    raw,
                'cfg':    cfg,
            }
            n_var = sum(len(v) for v in cfg.values())
            _dbg(f'[Editor] Chargé : {entry} ({n_var} variante(s))')
        except Exception as e:
            _dbg(f'[Editor] Catégorie ignorée "{entry}": {e}')
    return components


def find_varset(doc):
    """
    Cherche le VarSet dans un document FreeCAD.
    Ordre de priorité :
    1. Objet nommé 'VarSet'
    2. Premier objet de TypeId 'App::VarSet'
    3. Tout FeaturePython ayant des propriétés utilisateur (hors groupe Base)
    """
    # 1. Nom exact 'VarSet'
    vs = doc.getObject('VarSet')
    if vs is not None:
        return vs
    # 2. TypeId App::VarSet (FreeCAD 1.0+)
    for obj in doc.Objects:
        if obj.TypeId == 'App::VarSet':
            return obj
    # 3. Tout FeaturePython avec des propriétés hors groupe Base
    for obj in doc.Objects:
        if obj.TypeId in ('App::FeaturePython', 'Part::FeaturePython'):
            props = getattr(obj, 'PropertiesList', None) or []
            for p in props:
                try:
                    if obj.getGroupOfProperty(p) not in ('Base', ''):
                        return obj
                except Exception:
                    pass
    return None


# Propriétés built-in FreeCAD à exclure du VarSet
_VARSET_BUILTIN = frozenset({
    'Label', 'Label2', 'Visibility', 'ExpressionEngine',
    'Map', 'MapMode', 'MapPathParameter', 'MapReversed',
    'AttachmentOffset', 'Shape', 'Placement', 'Support',
    '_ChildCache', '_LinkOwner', '_LinkTouched',
})

def _varset_val(vs, prop):
    """
    Lit la valeur d'un paramètre VarSet.
    Gère les types Python natifs ET les FreeCAD.Units.Quantity
    (retournés par App::PropertyLength, PropertyAngle, etc.)
    """
    try:
        val = getattr(vs, prop)
        # FreeCAD.Units.Quantity → extraire la valeur numérique en unité de base
        if hasattr(val, 'Value'):
            return float(val.Value)
        if isinstance(val, bool):
            return None    # exclure les booléens
        if isinstance(val, (int, float)):
            return val
        if isinstance(val, str):
            return val
    except Exception:
        pass
    return None


def read_varset_params(vs):
    """
    Lit les paramètres utilisateur d'un VarSet.
    Filtre par noms connus (fiable quelle que soit la version FreeCAD).
    """
    result = {}
    if vs is None:
        return result
    for prop in (vs.PropertiesList or []):
        if prop in _VARSET_BUILTIN:
            continue
        if prop.startswith('_') or prop == 'SC_TimeStamp':
            continue
        val = _varset_val(vs, prop)
        if val is not None:
            result[prop] = val
    return result


def read_varset_from_fcstd(fcstd_path):
    """
    Ouvre le FCStd, cherche le VarSet (quel que soit son nom)
    et retourne un dict {nom: valeur} des paramètres utilisateur.
    """
    result = {}
    try:
        tmp    = App.openDocument(fcstd_path)
        vs     = find_varset(tmp)
        result = read_varset_params(vs)
        _dbg(f'[Editor] VarSet trouvé : {vs.Name if vs else "None"}')
        _dbg(f'[Editor] Paramètres : {list(result.keys())}')
        App.closeDocument(tmp.Name)
    except Exception as e:
        _dbg(f'[Editor] Lecture VarSet: {e}')
    return result


def write_json_aligned(data, path):
    """
    Écrit un JSON avec alignement vertical des valeurs par clé.
    Les références (clés sans _) ont leurs valeurs alignées en colonnes.
    Exemple :
        "6203": {"bore_dia":  10, "outer_dia":  30, "total_width":  9},
        "6210": {"bore_dia":  50, "outer_dia":  90, "total_width": 20}
    """
    # Séparer métadonnées et références
    meta_keys = [k for k in data if k.startswith('_')]
    ref_keys  = [k for k in data if not k.startswith('_')]

    if not ref_keys:
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return

    # Collecter toutes les clés de colonnes et leurs valeurs
    all_col_keys = []
    seen = set()
    for rk in ref_keys:
        for ck in data[rk]:
            if ck not in seen:
                all_col_keys.append(ck)
                seen.add(ck)

    # Largeur max par colonne (clé + valeur)
    key_w   = max(len(ck) for ck in all_col_keys)
    val_w   = {}
    for ck in all_col_keys:
        max_v = 0
        for rk in ref_keys:
            v = data[rk].get(ck, '')
            max_v = max(max_v, len(_fmt_val(v)))
        val_w[ck] = max_v

    # Largeur max des clés de référence
    ref_w = max(len(json.dumps(rk)) for rk in ref_keys)

    lines = ['{']

    # Métadonnées
    for mk in meta_keys:
        v = json.dumps(data[mk], ensure_ascii=False)
        lines.append(f'  {json.dumps(mk)}: {v},')

    # Références alignées
    for i, rk in enumerate(ref_keys):
        row     = data[rk]
        rk_str  = json.dumps(rk).ljust(ref_w)
        parts   = []
        for ck in all_col_keys:
            v       = row.get(ck, '')
            ck_str  = json.dumps(ck).ljust(key_w + 2)   # +2 pour les guillemets
            val_str = _fmt_val(v).rjust(val_w[ck])
            parts.append(f'{ck_str}: {val_str}')
        sep  = ',' if i < len(ref_keys) - 1 else ''
        body = ', '.join(parts)
        lines.append(f'  {rk_str}: {{{body}}}{sep}')

    lines.append('}')

    with open(path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines) + '\n')


def _fmt_val(v):
    """Formate une valeur JSON pour l'alignement."""
    if isinstance(v, float):
        s = f'{v:g}'
        return s
    return json.dumps(v, ensure_ascii=False)


def backup_file(path):
    """Crée une sauvegarde horodatée d'un fichier existant."""
    if os.path.exists(path):
        ts  = datetime.now().strftime('%Y%m%d_%H%M%S')
        bak = path + f'.bak_{ts}'
        shutil.copy2(path, bak)
        return bak
    return None


# ============================================================
# Libellé d'affichage de variante (5 — renommage « cosmétique »)
# ============================================================
# Le sélecteur (config.py / category_labels.py) affiche les noms de
# familles/variantes/composants via category_labels.json — PAS via
# l'ancien translations.json (système remplacé, cf. l'en-tête de
# category_labels.py : « généré depuis l'ancien translations.json »).
# On écrit donc directement dans category_labels.json, avec le même
# schéma {lang: {families/variants/components: {clé: libellé}}}, pour
# que le libellé apparaisse réellement dans le sélecteur. La clé interne
# de la variante (utilisée dans config.json, le nom du JSON, et
# potentiellement dans les propriétés des composants déjà insérés)
# n'est JAMAIS modifiée — seul le texte affiché dans le sélecteur change.
# Les composants déjà placés référençant l'ancienne variante par sa clé
# continuent donc de fonctionner sans aucune pénalité.
#
# NB : category_labels.py met en cache le contenu du fichier en mémoire
# (variable module `_cache`, chargée une seule fois). Après un
# write_variant_label(), le sélecteur doit donc invalider ce cache
# (`category_labels._cache = None`) pour relire le fichier à jour — c'est
# fait côté ui_selector.py après la fermeture de l'éditeur.

def read_variant_label(variant_key):
    """
    Retourne le libellé d'affichage personnalisé d'une variante (ou ''
    si aucun n'est défini — le sélecteur affiche alors la clé brute).
    """
    if not variant_key or not os.path.exists(CATEGORY_LABELS_FILE):
        return ''
    try:
        with open(CATEGORY_LABELS_FILE, encoding='utf-8') as f:
            all_labels = json.load(f)
    except Exception as e:
        _dbg(f'[Editor] Lecture category_labels.json : {e}')
        return ''
    # Toutes les langues devraient porter le même libellé personnalisé
    # (ce n'est pas une traduction, juste un texte d'affichage) — on
    # prend la première trouvée, en préférant 'fr'.
    for lang in ('fr', 'en', *all_labels.keys()):
        variants = all_labels.get(lang, {}).get('variants', {})
        label = variants.get(variant_key, '')
        if label and label != variant_key:
            return label
    return ''


def write_variant_label(variant_key, label):
    """
    Enregistre (ou efface, si label est vide) le libellé d'affichage
    d'une variante dans category_labels.json, pour toutes les langues
    déjà présentes dans le fichier (ou 'fr'/'en' si le fichier est encore
    vide/absent). N'affecte jamais la clé interne de la variante.
    """
    if not variant_key:
        return
    all_labels = {}
    if os.path.exists(CATEGORY_LABELS_FILE):
        try:
            with open(CATEGORY_LABELS_FILE, encoding='utf-8') as f:
                all_labels = json.load(f)
        except Exception as e:
            _dbg(f'[Editor] Lecture category_labels.json : {e}')
            return
    label = (label or '').strip()
    # Fichier absent/vide : au moins fr/en pour que le libellé soit pris
    # en compte (category_labels._lookup replie sinon sur 'en' puis la clé).
    if label and not all_labels:
        all_labels = {'fr': {}, 'en': {}}
    changed = False
    for lang_key, lang_data in list(all_labels.items()):
        if lang_key.startswith('_') or not isinstance(lang_data, dict):
            continue
        variants = lang_data.setdefault('variants', {})
        if label:
            if variants.get(variant_key) != label:
                variants[variant_key] = label
                changed = True
        elif variant_key in variants:
            del variants[variant_key]
            changed = True
    if not changed:
        return
    try:
        bak = backup_file(CATEGORY_LABELS_FILE)
        with open(CATEGORY_LABELS_FILE, 'w', encoding='utf-8') as f:
            json.dump(all_labels, f, ensure_ascii=False, indent=2)
        _dbg(f'[Editor] Libellé variante {variant_key!r} → {label!r} '
             f'(sauvegarde : {os.path.basename(bak) if bak else "aucune"})')
    except Exception as e:
        _dbg(f'[Editor] Écriture category_labels.json : {e}')
