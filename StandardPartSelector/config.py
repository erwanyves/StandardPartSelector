# -*- coding: utf-8 -*-
"""
StandardPartSelector.config — Configuration, constantes, chemins.

Traduction : déléguée à i18n.py (clés plates) et category_labels.py
(libellés de familles/variantes/composants) — voir ces modules.
"""
import os

from i18n import tr as _i18n_tr
import category_labels

# ── Débogage ────────────────────────────────────────────────
# Trace de diagnostic réversible : activée si la variable d'environnement
# SPS_DEBUG=1 est définie. Silencieuse en utilisation normale.
DEBUG = os.environ.get("SPS_DEBUG", "0") == "1"

def _dbg(*args, **kwargs):
    """Affiche une trace de diagnostic uniquement si DEBUG est actif."""
    if DEBUG:
        print(*args, **kwargs)

MACROS_DIR         = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MACRO_DIR            = os.path.join(MACROS_DIR, 'StandardPartSelector')
STANDARDS_DIR        = os.path.join(MACRO_DIR, 'Standards')
LIBRARY_PATH_FILE = os.path.join(MACRO_DIR, 'library_path.txt')

_UI = {
    'dlg_min_w': 720, 'dlg_min_h': 560,
    'dlg_def_w': 860, 'dlg_def_h': 580,
    'combo_min_w': 200,
    'thumb_w': 190,   'thumb_h': 160,
    'thumb_max_w': 180, 'thumb_max_h': 160,
}


def _t(key, default=None, **kwargs):
    """
    Traduction d'une clé plate (via i18n.py), avec repli optionnel sur
    `default` si la clé est absente des deux catalogues (en/fr), puis
    interpolation `**kwargs`. Conserve la signature de l'ancien `_build_tr()`
    pour ne pas casser les sites d'appel existants.
    """
    text = _i18n_tr(key)
    if text == key and default is not None:
        text = default
    if kwargs:
        try:
            text = text.format(**kwargs)
        except Exception:
            pass
    return text


_t.family    = category_labels.family
_t.variant   = category_labels.variant
_t.component = category_labels.component


def _build_tr():
    """Conservé pour compatibilité des sites d'appel existants (`from config import _build_tr`) ; retourne directement `_t`."""
    return _t


def _get_library_path():
    """Retourne le chemin de bibliothèque configuré, ou None."""
    if os.path.exists(LIBRARY_PATH_FILE):
        with open(LIBRARY_PATH_FILE, encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#'):
                    return line if os.path.isdir(line) else None
    return None


def _get_qt():
    """Importe et retourne (QtWidgets, QtCore)."""
    try:
        from PySide6 import QtWidgets, QtCore
    except ImportError:
        from PySide2 import QtWidgets, QtCore
    return QtWidgets, QtCore


def _status(message, timeout=4000):
    """
    Affiche un message transitoire dans la barre de statut de FreeCAD.
    Complète les print() (vue Rapport, souvent fermée) par un retour
    visible dans la fenêtre principale, sans bloquer l'utilisateur.
    Silencieux si aucune interface graphique n'est disponible (tests, CLI).
    """
    try:
        import FreeCADGui as Gui
        mw = Gui.getMainWindow()
        if mw is not None:
            mw.statusBar().showMessage(message, timeout)
    except Exception:
        pass
