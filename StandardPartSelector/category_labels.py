# -*- coding: utf-8 -*-
"""
category_labels.py — Traduction des libellés de catégories du catalogue
StandardPartSelector (familles, variantes, composants).

Distinct du module `i18n.py` générique (clé plate uniquement) : ces libellés
sont indexés par le nom natif du catalogue (toujours en français, ex.
« Billes rainurées »), pas par une clé symbolique. Conservé dans un module
séparé, propre à StandardPartSelector, pour ne pas complexifier `i18n.py`,
qui doit rester une copie strictement identique dans les quatre macros.

Données : category_labels.json (généré depuis l'ancien translations.json,
voir _legacy/).
"""

import json
import os

from i18n import active_lang

_DIR  = os.path.dirname(os.path.abspath(__file__))
_FILE = os.path.join(_DIR, "category_labels.json")

_cache = None


def _load() -> dict:
    global _cache
    if _cache is None:
        try:
            with open(_FILE, "r", encoding="utf-8") as fh:
                _cache = json.load(fh)
        except Exception:
            _cache = {}
    return _cache


def _lookup(section: str, key: str) -> str:
    data = _load()
    lang = active_lang()
    value = (data.get(lang, {}).get(section) or {}).get(key)
    if value is None:
        value = (data.get("en", {}).get(section) or {}).get(key)
    return value if value is not None else key


def family(key: str) -> str:
    return _lookup("families", key)


def variant(key: str) -> str:
    return _lookup("variants", key)


def component(key: str) -> str:
    return _lookup("components", key)
