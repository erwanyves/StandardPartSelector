# -*- coding: utf-8 -*-
"""
editor_config — Configuration, Qt, chemins bibliothèque.
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
import os, json, shutil, ast, types, locale
from datetime import datetime

# MACRO_DIR peut être transmis par StandardPartSelector.py (via mod.MACRO_DIR)
# Sinon : calculé depuis __file__ (lancement direct de l'éditeur)
# __file__ = .../StandardPartSelector/StandardPartSelectorEditor/editor_config.py
# → remonter 2 niveaux pour atteindre StandardPartSelector/
if 'MACRO_DIR' not in dir():
    MACRO_DIR     = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MACROS_DIR    = os.path.dirname(MACRO_DIR)
STANDARDS_DIR = os.path.join(MACRO_DIR, 'Standards')
# Fichier RÉELLEMENT utilisé par la macro principale (config.py /
# category_labels.py) pour les libellés affichés de familles / variantes /
# composants. C'est ce fichier qu'il faut lire/écrire pour que le libellé de
# variante personnalisé apparaisse réellement dans le sélecteur.
CATEGORY_LABELS_FILE = os.path.join(MACRO_DIR, 'category_labels.json')


# ── Débogage ────────────────────────────────────────────────
# Même convention que config.py (macro principale) : trace de diagnostic
# réversible, activée si la variable d'environnement SPS_DEBUG=1 est
# définie. Silencieuse en utilisation normale. Utilisée par
# editor_catalog.py (lecture VarSet, lecture/écriture translations.json...).
DEBUG = os.environ.get("SPS_DEBUG", "0") == "1"

def _dbg(*args, **kwargs):
    """Affiche une trace de diagnostic uniquement si DEBUG est actif."""
    if DEBUG:
        print(*args, **kwargs)


# ============================================================
# Qt
# ============================================================
def _get_qt():
    try:
        from PySide2 import QtWidgets, QtCore, QtGui
        return QtWidgets, QtCore, QtGui
    except ImportError:
        from PySide import QtWidgets, QtCore, QtGui
        return QtWidgets, QtCore, QtGui


# ============================================================
# Traduction (réutilise le mécanisme principal — i18n.py / locales/*.json)
# ============================================================
# Historique : l'éditeur avait son propre mécanisme, basé sur
# translate.txt + translations.json, jamais réellement utilisé par
# editor_dialog.py ni editor_catalog.py (import mort) — fichiers et
# constantes (TRANSLATE_FILE / TRANSL_FILE) supprimés. L'éditeur est
# aligné sur celui du sélecteur principal (i18n.py + locales/*.json) —
# importable sans souci : StandardPartSelectorEditor.py insère toujours
# MACRO_DIR (dossier contenant i18n.py) en tête de sys.path avant de
# charger les modules de l'éditeur, que ce dernier soit lancé depuis le
# sélecteur ou en direct.
def _build_tr():
    """
    Retourne _t(key, default=None, **kwargs) — même signature que
    config.py::_t (macro principale) — construit au-dessus de i18n.tr().
    Repli sur `default` (ou la clé elle-même) si i18n.py est introuvable
    (ex. environnement de test isolé) plutôt que de lever une exception.
    """
    try:
        from i18n import tr as _i18n_tr
    except Exception:
        _i18n_tr = None

    def t(key, default=None, **kwargs):
        text = _i18n_tr(key) if _i18n_tr is not None else key
        if text == key and default is not None:
            text = default
        if kwargs:
            try: text = text.format(**kwargs)
            except Exception: pass
        return text
    return t

_t = _build_tr()


# ============================================================
# Bibliothèque externe
# ============================================================

LIBRARY_PATH_FILE = os.path.join(MACRO_DIR, 'library_path.txt')


def get_library_path():
    """Retourne le chemin de bibliotheque configure, ou None."""
    if os.path.exists(LIBRARY_PATH_FILE):
        with open(LIBRARY_PATH_FILE, encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#'):
                    return line if os.path.isdir(line) else None
    return None


def set_library_path(new_path):
    """Enregistre le nouveau chemin et retourne l ancien."""
    old_path = get_library_path()
    with open(LIBRARY_PATH_FILE, 'w', encoding='utf-8') as f:
        f.write('# Chemin de la bibliotheque\n')
        f.write(new_path + '\n')
    return old_path


def migrate_library(old_path, new_path):
    """
    Déplace l'intégralité des répertoires composants de old_path vers new_path.
    Ignore les dossiers commençant par '_' (templates, etc.).
    """
    moved, errors = [], []
    if not old_path or not os.path.isdir(old_path):
        return moved, errors
    os.makedirs(new_path, exist_ok=True)
    for entry in os.listdir(old_path):
        if entry.startswith('_'):
            continue
        src_dir = os.path.join(old_path, entry)
        if not os.path.isdir(src_dir):
            continue
        dst_dir = os.path.join(new_path, entry)
        try:
            if os.path.exists(dst_dir):
                # Fusionner : copier fichier par fichier
                for root, dirs, files in os.walk(src_dir):
                    rel   = os.path.relpath(root, src_dir)
                    d_root = os.path.join(dst_dir, rel)
                    os.makedirs(d_root, exist_ok=True)
                    for fname in files:
                        s = os.path.join(root, fname)
                        d = os.path.join(d_root, fname)
                        shutil.move(s, d)
                        moved.append(f'{entry}/{rel}/{fname}')
                # Supprimer les dossiers vides restants
                try:
                    shutil.rmtree(src_dir)
                except Exception:
                    pass
            else:
                shutil.move(src_dir, dst_dir)
                moved.append(f'{entry}/ (répertoire entier)')
        except Exception as e:
            errors.append(f'{entry}: {e}')
    return moved, errors


# ============================================================
# Utilitaires fichiers
# ============================================================

def sanitize_filename(name):
    """Remplace les caracteres interdits dans les noms de fichiers Windows."""
    result = name
    for c in r'\/:*?"<>|':
        result = result.replace(c, '_')
    # Remplacer aussi les espaces et points multiples
    result = result.strip('._')
    return result if result else 'fichier'

# ============================================================
# Utilitaires données
# ============================================================
