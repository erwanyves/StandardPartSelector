# -*- coding: utf-8 -*-
"""
StandardPartSelectorEditor — Point d'entrée.
Charge les modules depuis StandardPartSelector/StandardPartSelectorEditor/.
"""
import sys, os

# Dossier de la macro (config.py, catalog.py, manifest.py…) : nécessaire même
# en lancement direct de l'éditeur (hors bouton du sélecteur), pour que
# reconcile_all() (manifest.py) soit disponible dans les deux cas.
_macro_dir = os.path.dirname(os.path.abspath(__file__))
if _macro_dir not in sys.path:
    sys.path.insert(0, _macro_dir)

# Dossier des modules de l'éditeur
_ed_dir = os.path.join(_macro_dir, 'StandardPartSelectorEditor')
if _ed_dir not in sys.path:
    sys.path.insert(0, _ed_dir)

# Forcer le rechargement à chaque exécution
for _m in ('editor_config', 'editor_catalog', 'editor_dialog', 'i18n', 'category_labels'):
    sys.modules.pop(_m, None)

from editor_config  import *   # noqa: F401,F403
from editor_catalog import *   # noqa: F401,F403
from editor_dialog  import open_editor, main  # noqa: F401

if __name__ == '__main__':
    main()
