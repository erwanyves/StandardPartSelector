# -*- coding: utf-8 -*-
"""
StandardPartSelector.transaction — frontière d'annulation (04/11/2026).

CINQUIÈME copie de ce motif dans la suite de macros : ORing l'a introduit le
11/09/2026 (modules/dialogue.py), Swaging en utilise la forme mono-document,
SpringFull le porte depuis le 20/09/2026 (SpringFull.FCMacro). Il est copié
ici TEL QUEL, sur décision de l'auteur du 04/11/2026 (« pour l'annulation,
procède comme pour les autres macros »). Ce couple de fonctions a vocation à
rejoindre un module commun (mecanismes_partages.md).

Constat qui l'a motivé — mesuré au banc FreeCAD 26.3 le 04/11 : après une
insertion, `doc.UndoNames` était VIDE. Aucun Ctrl+Z ne défaisait une
insertion ; aucune transaction n'existait dans la macro.

POURQUOI App.setActiveTransaction() ET NON doc.openTransaction() :
`doc.openTransaction()` n'engage qu'UN document. Un remplacement ou une mise
à jour écrit dans le document de l'objet, qui n'est pas le document actif
quand le composant vient d'un fichier lié. Seul `App.setActiveTransaction()`
relie tous les documents touchés par un identifiant commun : une seule
entrée d'annulation, et un abandon qui défait tout.

L'ouverture est PARESSEUSE : rien n'est engagé tant qu'aucun document n'est
modifié. `mergeProject()`, par lequel l'insertion copie le modèle, est
couvert (mesuré le 04/11 : un Ctrl+Z retire les 13 objets d'un composant,
un abandon n'en laisse aucun).

UNE TRANSACTION EST UN FILET : elle ne doit jamais empêcher le geste
d'aboutir. Si l'ouverture échoue, on le dit et le geste se déroule comme
avant ce correctif.
"""
import FreeCAD as App
from config import _t

_ETAT = {'ouverte': False, 'id': None}


def _active():
    """(nom, id) de la transaction active, None s'il n'y en a pas.

    'inconnu' quand FreeCAD n'expose pas `getActiveTransaction()` (versions
    anciennes) ou qu'elle échoue : on ne peut alors rien affirmer.
    """
    lire = getattr(App, 'getActiveTransaction', None)
    if lire is None:
        return 'inconnu'
    try:
        return lire()
    except Exception:
        return 'inconnu'


def _est_la_notre():
    """La transaction active est-elle celle qu'a ouverte `ouvrir()` ?

    Mesuré sous FreeCAD 26.3 (04/11/2026) : une seconde
    `setActiveTransaction()` — un panneau de tâche, une commande, une autre
    macro lancée pendant la fenêtre d'orientation, qui n'est pas modale —
    VALIDE la nôtre et ouvre la sienne ; un Ctrl+Z la valide puis la défait.
    Dans les deux cas, `closeActiveTransaction()` fermerait la transaction
    d'un AUTRE outil, ou rien. Sans moyen de le savoir, on ferme comme avant.
    """
    if _ETAT.get('id') is None:
        return True
    active = _active()
    if active == 'inconnu':
        return True
    return bool(active) and len(active) > 1 and active[1] == _ETAT['id']


def ouvrir(intitule):
    """Ouvre la transaction qui couvrira tous les documents touchés.

    Retourne True si elle est ouverte, False sinon — après l'avoir dit.
    """
    _ETAT['ouverte'] = False
    _ETAT['id'] = None
    try:
        App.setActiveTransaction(intitule)
        _ETAT['ouverte'] = True
        active = _active()
        if isinstance(active, tuple) and len(active) > 1:
            _ETAT['id'] = active[1]
        return True
    except Exception as erreur:
        App.Console.PrintWarning(
            '[SPS] %s\n' % _t('transaction_unavailable', error=erreur))
        return False


def fermer(abandon=False):
    """Valide — ou abandonne — la transaction ouverte par `ouvrir()`.

    Ne ferme que NOTRE transaction (voir `_est_la_notre`).

    IDEMPOTENTE : le drapeau est baissé AVANT l'appel à FreeCAD. Une
    fermeture anticipée (validation dès la fin du geste, abandon avant la
    boîte d'erreur) peut donc être suivie de celle du `finally` : la seconde
    passe dans le vide, sans double abandon.
    """
    if not _ETAT.get('ouverte'):
        return
    _ETAT['ouverte'] = False
    if not _est_la_notre():
        # Déjà refermée par FreeCAD : on ne touche pas à celle d'un autre.
        if abandon:
            App.Console.PrintWarning(
                '[SPS] %s\n' % _t('transaction_abort_impossible'))
        else:
            App.Console.PrintMessage(
                '[SPS] %s\n' % _t('transaction_already_closed'))
        return
    try:
        App.closeActiveTransaction(abandon)
    except Exception as erreur:
        App.Console.PrintWarning(
            '[SPS] %s\n' % _t('transaction_close_failed', error=erreur))
