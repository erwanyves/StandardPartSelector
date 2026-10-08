# -*- coding: utf-8 -*-
"""
i18n.py — Internationalisation de StandardPartSelector.

Résolution de la langue (ordre de priorité) :
  1. Fichier lang.txt    (StandardPartSelector/lang.txt) — override manuel
  2. Préférence FreeCAD  (BaseApp/Preferences/General → Language)
  3. Locale système      (module `locale`)
  4. Fallback            → anglais ("en")

Module identique (à l'adaptation du nom de macro près dans les commentaires)
à celui des trois autres macros (ORing, SpringFull, SmartFinder) — copie
locale et indépendante, sans import croisé entre macros. Toute correction
future doit être reportée manuellement dans chacune des quatre copies.

Ne couvre que les clés plates. Les libellés de catégories du catalogue
(familles, variantes, composants — indexés par nom natif du catalogue plutôt
que par clé symbolique) sont traduits séparément par `category_labels.py`,
propre à StandardPartSelector, afin de ne pas complexifier ce module.
"""

from __future__ import annotations

import json
import locale
import os
import sys


# ─────────────────────────────────────────────────────────────────────────────
#  Résolution robuste du dossier StandardPartSelector
# ─────────────────────────────────────────────────────────────────────────────

def _find_macro_dir() -> str:
    candidates = []

    try:
        candidates.append(os.path.abspath(os.path.dirname(__file__)))
    except Exception:
        pass

    try:
        import FreeCAD
        candidates.append(os.path.join(FreeCAD.getUserMacroDir(True), "StandardPartSelector"))
    except Exception:
        pass

    for p in sys.path:
        if p and os.path.isdir(p):
            candidates.append(p)

    for c in candidates:
        if c and os.path.isdir(os.path.join(c, "locales")):
            return c

    for c in candidates:
        if c:
            return c

    return ""


_SPS_DIR     = _find_macro_dir()
_LOCALES_DIR = os.path.join(_SPS_DIR, "locales")
_LANG_FILE   = os.path.join(_SPS_DIR, "lang.txt")

_catalogs    = {}
_active_lang = None


# ─────────────────────────────────────────────────────────────────────────────
#  Détection de la langue
# ─────────────────────────────────────────────────────────────────────────────

def _has_catalog(code: str) -> bool:
    return os.path.isfile(os.path.join(_LOCALES_DIR, f"{code}.json"))


def _detect_lang() -> str:

    # 1. lang.txt
    try:
        with open(_LANG_FILE, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line and not line.startswith("#"):
                    code = line.split()[0].lower()
                    if code and _has_catalog(code):
                        return code
                    break
    except (FileNotFoundError, IndexError, OSError):
        pass

    # 2. Préférence FreeCAD
    try:
        import FreeCAD
        fc_lang = (
            FreeCAD.ParamGet("User parameter:BaseApp/Preferences/General")
            .GetString("Language", "").strip()
        )
        if fc_lang:
            code = fc_lang.split("_")[0].lower()
            if _has_catalog(code):
                return code
    except Exception:
        pass

    # 3. Locale système
    try:
        sys_locale = locale.getdefaultlocale()[0] or ""
        code = sys_locale.split("_")[0].lower()
        if _has_catalog(code):
            return code
    except Exception:
        pass

    # 4. Fallback
    return "en"


# ─────────────────────────────────────────────────────────────────────────────
#  Chargement des catalogues
# ─────────────────────────────────────────────────────────────────────────────

def _load_catalog(code: str) -> dict:
    if code in _catalogs:
        return _catalogs[code]

    path = os.path.join(_LOCALES_DIR, f"{code}.json")
    try:
        with open(path, "r", encoding="utf-8") as fh:
            catalog = json.load(fh)
        _catalogs[code] = catalog
        return catalog
    except Exception:
        pass

    _catalogs[code] = {}
    return {}


def _ensure_loaded() -> None:
    global _active_lang
    if _active_lang is not None:
        return
    _active_lang = _detect_lang()
    _load_catalog(_active_lang)
    if _active_lang != "en":
        _load_catalog("en")


# ─────────────────────────────────────────────────────────────────────────────
#  API publique
# ─────────────────────────────────────────────────────────────────────────────

def tr(key: str, **kwargs) -> str:
    _ensure_loaded()
    value = (
        _catalogs.get(_active_lang, {}).get(key)
        or _catalogs.get("en", {}).get(key)
        or key
    )
    if kwargs:
        try:
            value = value.format(**kwargs)
        except (KeyError, ValueError):
            pass
    return value


def set_lang(code: str) -> None:
    global _active_lang
    _active_lang = code.lower()
    _load_catalog(_active_lang)
    if _active_lang != "en":
        _load_catalog("en")


def active_lang() -> str:
    _ensure_loaded()
    return _active_lang or "en"


def available_languages() -> list:
    """Codes de langue disponibles, déduits des catalogues présents dans locales/."""
    try:
        codes = [
            fn[:-5] for fn in os.listdir(_LOCALES_DIR)
            if fn.endswith(".json")
        ]
        return sorted(codes)
    except Exception:
        return []
