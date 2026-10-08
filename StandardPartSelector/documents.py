# -*- coding: utf-8 -*-
"""StandardPartSelector.documents — garde-fou des documents chargés partiellement.

Quand un assemblage lie un document, FreeCAD peut ne le charger que
PARTIELLEMENT. Dans cet état il n'y enregistre **rien** et refuse de le
recalculer, et il le dit lui-même dans la vue Rapport :

    DocumentObject.cpp(910): Changes to partial loaded document will not be
                             saved: …#Objet.Propriete
    Document.cpp(2642):      Please reload partial document '…' for
                             recomputation

Mesuré le 13/09/2026 sur ORing : une modification rapportée réussie de bout en
bout par la macro, et le fichier — rouvert seul — inchangé. PERTE SILENCIEUSE.
Aucun contrôle interne ne peut la voir : la valeur EST posée, en mémoire. Seul
le disque sait.

Deux chemins de cette macro écrivent dans le document SOURCE d'un objet atteint
par une sélection **résolue** — c'est-à-dire précisément le document qu'un lien
charge à moitié :

- `integration.py::_apply_sc_properties()` — « sur l'objet original dans le
  document source », dit sa docstring ;
- `sc_props.py::_update_sc_varset()` — seul point de passage commun au
  remplacement et à la mise à jour en lot.

Le chemin d'INSERTION n'est pas concerné : `Component.py` insère dans
`App.ActiveDocument`, et un assemblage est chargé entièrement. C'est pourquoi
le garde-fou est posé **au point d'écriture** et non au lancement : une
complétion systématique ferait payer quelques secondes au geste le plus
courant, pour rien.

Ce module ne PARLE pas : il rend des couples `(clé i18n, arguments)` et reçoit
la fonction de traduction en argument. Il n'a donc aucune dépendance à
l'interface, et s'éprouve hors FreeCAD
(`_outils_claude/ESSAI_documents_partiels_sps.py`).
"""

import FreeCAD as App

# Clés i18n rendues par ce module — présentes dans les cinq catalogues.
MSG_COMPLETE = 'doc_partial_completed'
MSG_RESTE_PARTIEL = 'doc_partial_remains'
MSG_SANS_FICHIER = 'doc_partial_no_file'


def document_partiel(document):
    """Vrai si `document` est chargé partiellement — écritures NON conservées.

    `Partial` est un attribut de `App::Document`. `getattr` avec repli : une
    version de FreeCAD qui ne l'exposerait pas doit dégrader vers
    « complet », jamais bloquer l'écriture sur une propriété absente.
    """
    if document is None:
        return False
    try:
        return bool(getattr(document, 'Partial', False))
    except Exception:
        return False


def _rendre_actif(document):
    """Repose le document actif relevé avant la complétion. Ne lève jamais.

    `App.openDocument()` change le document actif, et l'aval de la macro est
    enraciné dessus. Rien n'est reposé si le document est déjà actif : un
    `setActiveDocument()` inutile fait clignoter l'onglet.
    """
    nom = getattr(document, 'Name', None)
    if not nom:
        return False
    try:
        if getattr(App.ActiveDocument, 'Name', None) == nom:
            return False
        App.setActiveDocument(nom)
    except Exception:
        return False
    return True


def completer_document_de(objet):
    """Complète le document de `objet` s'il est chargé PARTIELLEMENT.

    Rend `(ok, messages)`. `ok` est FAUX si le document reste partiel —
    l'appelant doit alors REFUSER d'écrire. `messages` est une liste de
    couples `(clé i18n, arguments)`.

    PÉRIMÈTRE : le document de `objet`, et lui seul. Contrairement à Swaging,
    la cible est déjà connue ici : inutile de remonter les liens du document
    actif pour la trouver. C'est la liste blanche la plus étroite possible.

    ⚠ Le succès se conclut d'une RELECTURE de l'état, jamais de l'absence
    d'exception : `App.openDocument()` peut rendre la main sans avoir
    complété. C'est le défaut planté n° 1 de la contre-épreuve.
    """
    document = getattr(objet, 'Document', None)
    if not document_partiel(document):
        # Repli vers « complet » : un objet sans document, ou une version de
        # FreeCAD sans `Partial`, ne doit pas bloquer une écriture licite.
        return True, []
    nom = getattr(document, 'Name', '?')
    chemin = getattr(document, 'FileName', '') or ''
    if not chemin:
        return False, [(MSG_SANS_FICHIER, {'document': nom})]
    actif_avant = getattr(App, 'ActiveDocument', None)
    erreur = None
    try:
        # `hidden=True` : compléter ne doit pas ouvrir la fenêtre du composant
        # sous les yeux de l'utilisateur, qui travaille dans son assemblage
        # (demande du 14/09/2026, portée sur SpringFull). L'argument n'existe
        # pas sur toutes les versions de FreeCAD : repli sur la forme
        # historique plutôt qu'un échec.
        try:
            App.openDocument(chemin, True)
        except TypeError:
            App.openDocument(chemin)
    except Exception as souci:
        # Jamais avalée : elle repart dans le message de refus.
        erreur = souci
    try:
        apres = App.getDocument(nom)
    except Exception:
        apres = None
    # Le document actif revient AUSSI quand la complétion a échoué : sinon
    # l'utilisateur se retrouve dans un autre onglet sans l'avoir demandé.
    _rendre_actif(actif_avant)
    if apres is not None and not document_partiel(apres):
        return True, [(MSG_COMPLETE, {'document': nom})]
    return False, [(MSG_RESTE_PARTIEL,
                    {'document': nom, 'erreur': erreur or ''})]


def garantir(objet, traduire):
    """Complète le document de `objet`, journalise, et LÈVE s'il reste partiel.

    `traduire` est la fonction de traduction de l'appelant (`config._t`),
    passée en argument plutôt qu'importée : ce module reste ainsi sans
    dépendance à l'interface, et éprouvable hors FreeCAD.

    LÈVE plutôt que rendre faux : les deux appelants rapportent déjà les
    exceptions — le bilan de la mise à jour en lot nomme le composant, et le
    wizard d'intégration affiche le message. **Refuser vaut mieux
    qu'avertir** : FreeCAD avertissait déjà, douze fois le 13/09, et la
    modification a été perdue quand même.
    """
    ok, messages = completer_document_de(objet)
    for cle, arguments in messages:
        texte = '[SPS] %s\n' % traduire(cle, **arguments)
        if ok:
            App.Console.PrintMessage(texte)
        else:
            App.Console.PrintWarning(texte)
    if not ok:
        raise RuntimeError(
            traduire(messages[0][0], **messages[0][1]) if messages
            else 'partial document')
    return True
