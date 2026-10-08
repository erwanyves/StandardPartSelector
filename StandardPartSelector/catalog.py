# -*- coding: utf-8 -*-
"""
StandardPartSelector.catalog — Découverte des composants et chargement des catalogues.
"""
import os, json, re as _re
from config import _dbg
from config import MACRO_DIR, STANDARDS_DIR, _t, _get_library_path


def _resolve_fcstd(comp_key, fcstd_name):
    # Priorité : STANDARDS_DIR local (évite les problèmes d'accès Dropbox)
    p_local = os.path.join(STANDARDS_DIR, comp_key, 'models', fcstd_name)
    if os.path.exists(p_local):
        return p_local
    # Fallback : bibliothèque externe
    lib = _get_library_path()
    if lib:
        p_lib = os.path.join(lib, comp_key, 'models', fcstd_name)
        if os.path.exists(p_lib):
            return p_lib
    return p_local  # retourner le chemin local même si absent


def load_comp_config(family):
    """
    Charge et retourne le config.json d'une famille.
    Retourne {} si absent ou invalide.
    Mutualisé entre catalog.py et integration.py.
    """
    cfg_path = os.path.join(STANDARDS_DIR, family, 'config.json')
    if not os.path.exists(cfg_path):
        return {}
    try:
        with open(cfg_path, encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        _dbg(f'[catalog] config.json illisible ({family}) : {e}')
        return {}


def discover_components():
    """Scanne STANDARDS_DIR et la bibliothèque externe."""
    components = {}
    if not os.path.isdir(STANDARDS_DIR):
        raise FileNotFoundError(f"Dossier introuvable:\n{STANDARDS_DIR}")
    lib = _get_library_path()
    scan_dirs = [STANDARDS_DIR]
    if lib and lib != STANDARDS_DIR and os.path.isdir(lib):
        scan_dirs.append(lib)
    all_entries = {}
    for scan_dir in reversed(scan_dirs):   # lib d'abord, STANDARDS_DIR ensuite
        for entry in sorted(os.listdir(scan_dir)):
            all_entries[entry] = scan_dir
    for entry, scan_dir in sorted(all_entries.items()):
        if entry.startswith('_'): continue
        comp_dir    = os.path.join(scan_dir, entry)
        config_file = os.path.join(comp_dir, 'config.json')
        if not (os.path.isdir(comp_dir) and os.path.exists(config_file)):
            continue
        try:
            raw = load_comp_config(entry)
            datas_dir     = os.path.join(comp_dir, 'datas')
            excluded_keys = set(raw.get('_excluded_keys', []))
            cfg = {}
            for family, variants in raw.items():
                if family.startswith('_') or not isinstance(variants, dict):
                    continue
                cfg[family] = {}
                for variant, entry_data in variants.items():
                    if variant.startswith('_'): continue
                    _fn = entry_data.get('fcstd')
                    cfg[family][variant] = {
                        'json':          os.path.join(datas_dir, entry_data['json']),
                        'fcstd':         _resolve_fcstd(entry, os.path.basename(_fn))
                                         if _fn else None,
                        'columns':       [tuple(c[:6]) if len(c) > 5
                                          else (tuple(c[:4]) if len(c) > 3
                                          else (tuple(c[:3]) if len(c) > 2
                                          else (c[0], c[1])))
                                          for c in entry_data['columns']],
                        'excluded_keys': excluded_keys,
                        # Colonnes de données propagées : {clé: type}
                        # (mute=True ET prop=True ET type renseigné)
                        'data_props':    {c[1]: c[4] for c in entry_data['columns']
                                          if len(c) > 5 and c[2] and c[5] and c[4]},
                    }
            components[entry] = {'dir': comp_dir, 'config': config_file, 'cfg': cfg}
        except Exception as e:
            _dbg(f'[StandardPartSelector] Composant ignoré "{entry}": {e}')
    return components


def load_catalog(json_path):
    """Charge et normalise un catalogue JSON."""
    if not os.path.exists(json_path):
        raise FileNotFoundError(_t("error_catalog") + json_path)
    with open(json_path, 'r', encoding='utf-8') as f:
        raw = f.read()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        _dbg(f'[load_catalog] JSON corrompu : {json_path} — {e}')
        raise
    _upat = _re.compile(
        r'^\s*([+-]?\d+(?:\.\d+)?)\s*(?:mm|deg|°|rad|kg|N|Pa|MPa)?\s*$')
    result = {}
    for ref, vals in data.items():
        if ref.startswith('_') or not isinstance(vals, dict): continue
        clean = {}
        for k, v in vals.items():
            if isinstance(v, str):
                m = _upat.match(v)
                if m:
                    num = float(m.group(1))
                    v   = int(num) if num == int(num) else num
            elif isinstance(v, float) and v == int(v):
                v = int(v)
            clean[k] = v
        result[ref] = clean
    return result
