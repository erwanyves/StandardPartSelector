# -*- coding: utf-8 -*-
"""
StandardPartSelector.geometry — Détection géométrique et highlight.
"""
import math
from config import _dbg
from config import _t

# Types reconnus comme un repère (LCS) sélectionnable directement — voir
# le bloc de détection dédié dans _detect_cylindrical_geometry() ci-dessous.
# 'PartDesign::CoordinateSystem' est le TypeId utilisé par les LCS internes
# Local_Top/Local_Bottom créés par SpringFullModule.py::_find_or_create_lcs
# (et par la commande de repère local du workbench PartDesign) ;
# 'Part::LocalCoordinateSystem' est celui du LCS "classique" du workbench
# Part (menu Part > Repère local) — c'est celui qu'un utilisateur insère
# typiquement lui-même pour positionner un composant. Confirmé en 3D et en
# sélection dans l'arbre (23/08/2026) : un LCS Part::LocalCoordinateSystem
# non reconnu ici tombe silencieusement dans la détection face/arête
# ci-dessous (qui échoue faute de rayon), rendant le repère invisible pour
# le mécanisme d'insertion. 'PartDesign::LocalCoordinateSystem' est conservé
# par prudence pour d'éventuels documents/versions plus anciens.
_LCS_TYPES = (
    'PartDesign::CoordinateSystem',
    'PartDesign::LocalCoordinateSystem',
    'Part::LocalCoordinateSystem',
)

def _table_placements(entrees):
    """{id(objet résolu) : matrice monde}, depuis la sélection NON résolue.

    `entrees` : suite de couples `(tête, chemins)` telle que
    `getSelectionEx('', 0)` la fournit. Décision sortie du Qt pour être
    éprouvable hors FreeCAD (`ESSAI_placement_monde_sps.py`).

    Deux contrats d'API, **mesurés le 20/09/2026** et non supposés :
      - `tête.getSubObject(chemin, 1)` rend l'**objet résolu lui-même** ;
      - `tête.getSubObject(chemin, 4)` rend la **matrice accumulée**
        depuis la racine.

    La table est donc indexée par **identité d'objet**. On ne découpe
    jamais le chemin : un chemin FreeCAD se termine par un point, et le
    découper est l'erreur qui a coûté un défaut le 15/09/2026.

    Le **premier** chemin rencontré pour un objet gagne : deux
    sous-éléments du même objet partagent forcément la même matrice, et
    s'ils divergeaient, changer d'avis en cours de route serait pire que
    s'en tenir au premier.
    """
    table = {}
    for tete, chemins in entrees:
        if tete is None:
            continue
        for chemin in (chemins or ()):
            try:
                cible = tete.getSubObject(chemin, 1)
                matrice = tete.getSubObject(chemin, 4)
            except Exception:
                continue
            if cible is None or matrice is None:
                continue
            table.setdefault(id(cible), matrice)
    return table


def _placements_monde_de_la_selection():
    """{id(objet) : App.Placement} pour la sélection courante.

    Seule transformation juste en assemblage lié comme en mono-document.
    Mesurée : sans elle, 98,66 mm d'écart sur un composant lié dans un
    assemblage déplacé, et 0,00 mm en mono-document — d'où un défaut
    parfaitement invisible tant qu'on ne teste qu'un document simple.
    """
    try:
        import FreeCAD as _App
        import FreeCADGui as Gui
        brut = Gui.Selection.getSelectionEx('', 0)
    except Exception as erreur:
        _dbg('[geometry] sélection non résolue indisponible : %s' % erreur)
        return {}
    entrees = [(s.Object, s.SubElementNames) for s in (brut or [])]
    return dict((cle, _App.Placement(m))
                for cle, m in _table_placements(entrees).items())


def _detect_cylindrical_geometry():
    """
    Lit la sélection FreeCAD active.
    Détecte la géométrie depuis :
      - un repère (LCS) sélectionné directement — voir plus bas
      - une face cylindrique  (ShapeType='Face', Surface a Radius)
      - une arête circulaire  (ShapeType='Edge', Curve a Radius)
    Utilise SubObjects plutôt que getElement() : les deux rendent la
    sous-forme dans le repère LOCAL de l'objet résolu, mais SubObjects
    évite de rejouer la résolution du nom de sous-élément.

    ⚠ L'appel est `getSelectionEx()` SANS argument, donc RÉSOLU : ses
    `SubObjects` sont en coordonnées **locales**. C'est l'appel NON résolu,
    `getSelectionEx('', 0)`, dont les `SubObjects` sont déjà en coordonnées
    monde. Les deux formes n'ont pas le même contrat — confondre les deux a
    coûté une double transformation sur SpringFull le 15/09/2026, mesurée à
    exactement le double de la distance réelle.

    Le passage en coordonnées monde est donc fait explicitement plus bas,
    par le CHEMIN DEPUIS LA RACINE relevé sur la sélection non résolue
    (`_placements_monde_de_la_selection`) — et non par le placement de
    l'objet porteur, qui était la formulation de cette docstring jusqu'au
    20/09/2026 : elle décrivait le mécanisme d'AVANT le correctif, celui
    qui rendait 62,91 mm d'écart en assemblage lié.

    Retourne un dict {diameter, center, axis} ou None.
    """
    try:
        import FreeCADGui as Gui
        sel = Gui.Selection.getSelectionEx()
        # Le passage en coordonnées monde vient du chemin depuis la racine,
        # relevé une fois sur la sélection NON résolue (voir
        # `_placements_monde_de_la_selection`).
        _placements = _placements_monde_de_la_selection()
        for s in sel:
            # ── Repère (LCS) sélectionné directement, ou une de ses trois
            # branches d'axe cliquée en vue 3D ─────────────────────────────
            # Priorité sur la détection face/arête ci-dessous : un LCS
            # sélectionné donne à la fois la position ET l'orientation
            # complète (axe + roll, via 'radial') du composant à insérer —
            # sans notion de diamètre, donc aucun filtrage automatique de
            # variante dans ce cas (voir StandardPartSelector.py::main()).
            #
            # BUG CORRIGÉ (31/08/2026) : pour un LCS 'PartDesign::CoordinateSystem'
            # (confirmé par trace de diagnostic réelle), cliquer sur une
            # flèche d'axe en vue 3D NE sélectionne PAS un objet séparé —
            # `s.Object` reste le LCS lui-même, et l'axe cliqué est porté par
            # `s.SubElementNames` (simplement 'X', 'Y' ou 'Z'). L'ancien code
            # testait `s.Object.TypeId in _LCS_TYPES` en premier et ignorait
            # alors totalement SubElementNames, retombant systématiquement
            # sur l'axe Z par défaut quelle que soit la flèche cliquée. La
            # branche 'App::Line' ci-dessous reste nécessaire pour
            # Part::LocalCoordinateSystem (workbench Part), dont les axes
            # sont bien des objets séparés d'après un test antérieur
            # (23/08/2026) — les deux structures coexistent selon le type de
            # repère utilisé.
            #
            # REVERT (01/09/2026), CONFIRMÉ par test réel avec repère
            # correctement orienté : un décalage cyclique (X→Y, Y→Z, Z→X)
            # avait été introduit ici le 31/08 sur la base d'un test dont le
            # repère (LCS) de référence s'est révélé lui-même mal orienté —
            # le décalage observé venait du modèle de test, pas d'un
            # comportement réel de FreeCAD. Mapping direct et naturel
            # confirmé : le sous-élément 'X'/'Y'/'Z' correspond à son propre
            # vecteur local.
            from FreeCAD import Vector as _Vlcs
            _lcs_obj = None
            _local_axis, _local_radial = _Vlcs(0, 0, 1), _Vlcs(1, 0, 0)
            if getattr(s.Object, 'TypeId', None) in _LCS_TYPES:
                _lcs_obj  = s.Object
                _sub_list = list(s.SubElementNames or [])
                _sub      = _sub_list[0] if _sub_list else ''
                if _sub == 'X':
                    _local_axis, _local_radial = _Vlcs(1, 0, 0), _Vlcs(0, 1, 0)
                elif _sub == 'Y':
                    _local_axis, _local_radial = _Vlcs(0, 1, 0), _Vlcs(0, 0, 1)
                elif _sub == 'Z':
                    _local_axis, _local_radial = _Vlcs(0, 0, 1), _Vlcs(1, 0, 0)
                # _sub vide ou inattendu (sélection directe du repère depuis
                # l'arbre, ou clic sur sa croix en vue 3D sans viser une
                # flèche précise) : axe Z brut du repère par défaut (déjà
                # initialisé ci-dessus).
                _dbg(f'[geo-lcs] LCS direct : sub={_sub!r} → '
                      f'local_axis={tuple(_local_axis)}')
            elif getattr(s.Object, 'TypeId', None) == 'App::Line':
                _axis_name = getattr(s.Object, 'Name', '') or ''
                _cand = None
                if 'X_Axis' in _axis_name:
                    _cand = (_Vlcs(1, 0, 0), _Vlcs(0, 1, 0))
                elif 'Y_Axis' in _axis_name:
                    _cand = (_Vlcs(0, 1, 0), _Vlcs(0, 0, 1))
                elif 'Z_Axis' in _axis_name:
                    _cand = (_Vlcs(0, 0, 1), _Vlcs(1, 0, 0))
                if _cand is not None:
                    # Remonter depuis l'axe (App::Line) vers son LCS parent,
                    # via l'Origin intermédiaire — même technique de
                    # remontée par InList qu'ailleurs dans ce fichier.
                    _cur     = s.Object
                    _visited = set()
                    for _ in range(6):
                        _inlist = getattr(_cur, 'InList', [])
                        if not _inlist:
                            break
                        _nxt = _inlist[0]
                        if _nxt.Name in _visited:
                            break
                        _visited.add(_nxt.Name)
                        if getattr(_nxt, 'TypeId', None) in _LCS_TYPES:
                            _lcs_obj = _nxt
                            _local_axis, _local_radial = _cand
                            break
                        _cur = _nxt
            if _lcs_obj is None:
                # Ni LCS direct, ni axe résolu vers un LCS : sortir du bloc
                # LCS, poursuivre avec la détection face/arête ci-dessous
                # (comportement inchangé pour tout autre objet, y compris un
                # axe d'Origin d'un Body/Part qui ne mène à aucun LCS).
                pass
            else:
                try:
                    _gp = _lcs_obj.getGlobalPlacement()
                except Exception:
                    _gp = _lcs_obj.Placement
                world_center = _gp.Base
                world_axis   = _gp.Rotation.multVec(_local_axis)
                world_radial = _gp.Rotation.multVec(_local_radial)
                if world_axis.Length > 1e-10:
                    world_axis = world_axis.normalize()
                if world_radial.Length > 1e-10:
                    world_radial = world_radial.normalize()
                _dbg(f'[geo-lcs] lcs_obj={_lcs_obj.Name!r} '
                      f'local_axis={tuple(_local_axis)} '
                      f'world_axis=({world_axis.x:.3f},{world_axis.y:.3f},{world_axis.z:.3f})')
                return {
                    'diameter': None,
                    'center':   world_center,
                    'axis':     world_axis,
                    'radial':   world_radial,
                    'is_arc':   False,
                    'is_lcs':   True,
                }
            sub_names = s.SubElementNames or []
            sub_objs  = s.SubObjects or []       # repère LOCAL (cf. docstring)
            for i, sub_name in enumerate(sub_names):
                # SubObjects[i] = sous-forme dans le repère LOCAL de
                # l'objet résolu (appel getSelectionEx() sans argument).
                # Fallback sur getElement() si SubObjects non disponible
                if i < len(sub_objs):
                    shape = sub_objs[i]
                else:
                    shape = s.Object.Shape.getElement(sub_name)
                radius = center = axis = None
                if shape.ShapeType == 'Face':
                    surf = shape.Surface
                    if hasattr(surf, 'Radius'):
                        radius = surf.Radius
                        center = surf.Center
                        axis   = surf.Axis
                elif shape.ShapeType == 'Edge':
                    curve = shape.Curve
                    if hasattr(curve, 'Radius'):
                        radius = curve.Radius
                        center = curve.Center
                        # Détecter si c'est un cercle complet ou un arc
                        try:
                            _fp = shape.FirstParameter
                            _lp = shape.LastParameter
                            _is_full = abs(abs(_lp - _fp) - 2 * 3.141592653589793) < 1e-6
                        except Exception:
                            _is_full = False
                        # Méthode 3 points en coordonnées monde pour les deux cas
                        # (arc et cercle complet). gp.multVec() sur des POSITIONS
                        # est toujours fiable, même pour PartDesign::Mirrored où
                        # gp.Rotation.multVec() sur des directions est erroné.
                        if _is_full:
                            # Cercle : 3 points à 0, π/3, 2π/3 + fp
                            import math as _m
                            _t1 = curve.FirstParameter
                            _t2 = curve.FirstParameter + _m.pi * 2 / 3
                            _t3 = curve.FirstParameter + _m.pi * 4 / 3
                        else:
                            # Arc : début, milieu, fin
                            _t1 = _fp
                            _t2 = (_fp + _lp) / 2.0
                            _t3 = _lp
                        axis = curve.Axis  # fallback si 3 points échouent
                if radius is not None:
                    diameter = round(radius * 2)
                    # Les coordonnées sont LOCALES dans tous les cas (voir
                    # la docstring) : il faut donc toujours appliquer le
                    # placement de l'objet porteur, lien ou non.
                    # Remonter à l'objet porteur du placement monde
                    # PartDesign features n'ont pas de placement propre
                    # → chercher le App::Link ou App::Part parent
                    _obj = s.Object
                    _is_link = False
                    _gp_obj  = _obj
                    # Remonter dans InList : priorité App::Link
                    # (peut envelopper un App::Part)
                    _visited        = set()
                    _cur            = _obj
                    _part_candidate = None
                    while _cur is not None:
                        if _cur.Name in _visited: break
                        _visited.add(_cur.Name)
                        if _cur.TypeId in ('App::Link',
                                           'App::LinkElement'):
                            _gp_obj  = _cur
                            _is_link = True
                            break
                        if _cur.TypeId == 'App::Part':
                            _part_candidate = _cur
                        _in = getattr(_cur, 'InList', [])
                        _cur = _in[0] if _in else None
                    # Si pas de Link trouvé, utiliser le Part
                    if not _is_link and _part_candidate is not None:
                        _gp_obj = _part_candidate
                    # Un seul traitement : lien ou non, on applique le
                    # placement de `_gp_obj`. La distinction portée par
                    # `_is_link` sert UNIQUEMENT à choisir cet objet
                    # ci-dessus (le lien prime sur l'App::Part) ; les deux
                    # branches qui suivaient étaient identiques au
                    # commentaire près, et faisaient croire à deux
                    # traitements distincts.
                    #
                    # ⚠ `getGlobalPlacement()` rend le placement dans le
                    # document SOURCE d'un composant lié — mesuré faux de
                    # 132,5 mm sur SpringFull le 15/09/2026. C'est le
                    # défaut de placement du chantier D, geste 3 : il n'est
                    # PAS corrigé ici, et ce commentaire est là pour qu'on
                    # ne le croie pas réglé.
                    gp = _placements.get(id(s.Object))
                    if gp is None:
                        # Repli historique. Il est EXACT en mono-document et
                        # FAUX en assemblage lié (98,66 mm mesurés) : il ne
                        # sert donc que si le chemin depuis la racine est
                        # indisponible, et il le DIT au lieu de se taire.
                        _dbg('[geometry] chemin monde absent pour %s : repli '
                             'sur getGlobalPlacement() — exact en '
                             'mono-document, FAUX en assemblage lié'
                             % getattr(s.Object, 'Name', '?'))
                        try:
                            gp = _gp_obj.getGlobalPlacement()
                        except Exception:
                            gp = _gp_obj.Placement
                    world_center = gp.multVec(center)
                    world_axis   = gp.Rotation.multVec(axis)
                    if world_axis.Length > 1e-10:
                        world_axis = world_axis.normalize()
                    else:
                        from FreeCAD import Vector as _V
                        world_axis = _V(0, 0, 1)
                    # Méthode 3 points unifiée (arc et cercle complet) :
                    # gp.multVec() sur des positions est toujours fiable.
                    try:
                        from FreeCAD import Vector as _V2
                        _p1w = gp.multVec(_V2(curve.value(_t1)))
                        _p2w = gp.multVec(_V2(curve.value(_t2)))
                        _p3w = gp.multVec(_V2(curve.value(_t3)))
                        _v1  = _p2w - _p1w
                        _v2  = _p3w - _p1w
                        _nc  = _v1.cross(_v2)
                        if _nc.Length > 1e-10:
                            world_axis   = _nc.normalize()
                        _rad = _p1w - world_center
                        if _rad.Length > 1e-10:
                            world_radial = _rad.normalize()
                        else:
                            world_radial = None
                    except Exception as _ae:
                        _dbg(f'[geo] 3pts erreur : {_ae}')
                        # Fallback : radial depuis p0 local
                        try:
                            _p0_local    = curve.value(_t1)
                            _radial_local = _p0_local - center
                            if _radial_local.Length > 1e-10:
                                world_radial = gp.Rotation.multVec(
                                    _radial_local.normalize())
                            else:
                                world_radial = None
                        except Exception:
                            world_radial = None
                    return {
                        'diameter': str(diameter),
                        'center':   world_center,
                        'axis':     world_axis,
                        'radial':   world_radial,
                        'is_arc':   not _is_full,
                    }
    except Exception as e:
        _dbg(f'[5B.1] Détection ignorée : {e}')
    return None


def _detect_cylindrical_diameter():
    """Compatibilité — retourne uniquement le diamètre sous forme de chaîne."""
    geo = _detect_cylindrical_geometry()
    return geo['diameter'] if geo else None



# ============================================================
# Détection et traitement d'un composant SC sélectionné (5B.2)
# ============================================================


def _highlight(obj, on=True):
    """Met/enlève la surbrillance d'un objet dans la vue 3D."""
    try:
        import FreeCADGui as Gui
        if on:
            Gui.Selection.clearSelection()
            if obj and obj.Document:
                Gui.Selection.addSelection(
                    obj.Document.Name, obj.Name)
        else:
            Gui.Selection.clearSelection()
    except Exception:
        pass


