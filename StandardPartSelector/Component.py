# -*- coding: utf-8 -*-
"""
Module: Component
Module universel pour tous les types de composants standardises.
S'appuie sur le JSON catalogue pour connaitre les parametres a passer au VarSet.
"""
import FreeCAD as App
import os

# ── Débogage ────────────────────────────────────────────────
# Trace de diagnostic réversible : activée si la variable d'environnement
# SPS_DEBUG=1 est définie. Silencieuse en utilisation normale.
DEBUG = os.environ.get("SPS_DEBUG", "0") == "1"

def _dbg(*args, **kwargs):
    """Affiche une trace de diagnostic uniquement si DEBUG est actif."""
    if DEBUG:
        print(*args, **kwargs)

# Mapping noms JSON → noms VarSet (après standardisation ils sont identiques)
_JSON_TO_VARSET = {
    # Les noms JSON et VarSet sont maintenant identiques — mapping vide
    # Conserve pour compatibilite future
}

# Clés universellement exclues du VarSet (indépendantes du composant)
# Les clés spécifiques à chaque composant sont déclarées dans son config.json
_EXCLUDED_BASE = {'ref'}                                    # 2.2



# ============================================================
# Propriétés Standards Components (5A.1)
# ============================================================
_SC_GROUP = 'StandardPartSelector'   # groupe visible dans le panneau Données

def _write_sc_properties(obj, comp_key, family, variant, ref, timestamp):
    """
    Ecrit les 5 propriétés SC_ sur l'objet FreeCAD inséré.
    Les propriétés sont regroupées sous 'StandardPartSelector' dans le panneau Données.
    Si une propriété existe déjà (réinsertion), sa valeur est simplement mise à jour.
    """
    fields = [
        ('SC_Standard',  'App::PropertyString', comp_key),
        ('SC_Family',    'App::PropertyString', family),
        ('SC_Category',  'App::PropertyString', variant),
        ('SC_Reference', 'App::PropertyString', ref),
        ('SC_TimeStamp', 'App::PropertyString', timestamp or ''),
    ]
    for prop_name, prop_type, value in fields:
        try:
            if not hasattr(obj, prop_name):
                obj.addProperty(prop_type, prop_name, _SC_GROUP)
            setattr(obj, prop_name, value)
        except Exception as e:
            _dbg(f"[SC_props] {prop_name}: {e}")


# ── Colonnes de données supplémentaires (Prix, Fournisseur, …) ──────────
_SC_DATA_GROUP = _SC_GROUP   # même groupe que les propriétés SC_ techniques

_TYPE_TO_PROP = {
    'float': 'App::PropertyFloat',
    'int':   'App::PropertyInteger',
    'text':  'App::PropertyString',
    'bool':  'App::PropertyBool',
}

# Noms techniques réservés : ne jamais écraser par une colonne de données
_SC_RESERVED = {'SC_Standard', 'SC_Family', 'SC_Category',
                'SC_Reference', 'SC_TimeStamp'}


def sc_data_prop_name(key):
    """Nom de propriété FreeCAD pour une colonne de données : SC_<clé sanitisée>."""
    import re
    name = re.sub(r'\W', '_', str(key)).strip('_')
    if not name or name[0].isdigit():
        name = 'd_' + name
    return 'SC_' + name


def coerce_value(value, ptype):
    """Convertit une valeur catalogue vers le type Python attendu."""
    try:
        if ptype == 'float': return float(value)
        if ptype == 'int':   return int(float(value))
        if ptype == 'bool':
            return str(value).strip().lower() in ('1', 'true', 'vrai', 'oui', 'yes', 'x')
        return str(value)                                   # text
    except (TypeError, ValueError):
        return str(value) if ptype == 'text' else None


def _write_sc_data_properties(obj, params, data_props):
    """
    Écrit les colonnes de données supplémentaires comme propriétés SC_ typées
    sur l'objet inséré (groupe 'StandardPartSelector — Données').
    data_props : {clé_catalogue: type}  avec type ∈ {float,int,text,bool}
    """
    if not data_props:
        return
    for key, ptype in data_props.items():
        prop_qt = _TYPE_TO_PROP.get(ptype)
        if not prop_qt or key not in params:
            continue
        prop_name = sc_data_prop_name(key)
        if prop_name in _SC_RESERVED:
            _dbg(f"[SC_data] {prop_name} réservé — ignoré")
            continue
        value = coerce_value(params[key], ptype)
        if value is None:
            continue
        try:
            if not hasattr(obj, prop_name):
                obj.addProperty(prop_qt, prop_name, _SC_DATA_GROUP)
            setattr(obj, prop_name, value)
        except Exception as e:
            _dbg(f"[SC_data] {prop_name}: {e}")


def _fit_view(doc_name, set_perspective=False):
    """Zoom fit all sur le document specifie (appele via QTimer)."""
    try:
        import FreeCADGui as Gui
        Gui.setActiveDocument(doc_name)
        Gui.updateGui()
        view = Gui.getDocument(doc_name).ActiveView
        if set_perspective:
            view.setCameraType('Perspective')
            cam = view.getCameraNode()
            try:
                from pivy import coin
                r = cam.orientation
                view.viewIsometric()
            except Exception:
                pass
        try:
            view.fitAll()
        except Exception:
            Gui.runCommand('Std_ViewFitAll')
    except Exception as e:
        _dbg(f"[fitAll] {e}")


def create_component(params: dict, fcstd_path: str, label: str = None,
                     excluded_keys: set = None,
                     comp_key: str = '', family: str = '', variant: str = '',
                     ref: str = '', geo_info: dict = None,
                     data_props: dict = None):
    """
    Insere un composant dans le document courant.

    params    : dictionnaire des parametres (issu du JSON catalogue)
    fcstd_path: chemin vers le FCStd template avec VarSet
    label     : label du Part dans FreeCAD (ex: '6205')
    """
    if not os.path.exists(fcstd_path):
        raise FileNotFoundError(f"FCStd template not found:\n{fcstd_path}")

    active_doc = App.ActiveDocument
    if active_doc is None:
        active_doc         = App.newDocument('Component')
        was_empty          = True
        active_doc_was_new = True
    else:
        was_empty          = len(active_doc.Objects) == 0
        active_doc_was_new = False

    # Copie temporaire DANS LE MÊME RÉPERTOIRE que le FCStd original
    # → FreeCAD résout les chemins relatifs correctement
    import shutil as _shutil
    _fcstd_dir  = os.path.dirname(fcstd_path)
    _fcstd_base = os.path.splitext(os.path.basename(fcstd_path))[0]
    tmp_fcstd_path = os.path.join(_fcstd_dir, f'_tmp_{_fcstd_base}.FCStd')
    _shutil.copy2(fcstd_path, tmp_fcstd_path)
    tmp_doc  = App.openDocument(tmp_fcstd_path)
    tmp_name = tmp_doc.Name

    # Rebascule immédiatement l'affichage sur le doc cible et gèle le rendu
    try:
        import FreeCADGui as Gui
        Gui.setActiveDocument(active_doc.Name)
        Gui.getDocument(active_doc.Name).ActiveView.setRedraw(False)
    except Exception:
        pass                                                # mode sans GUI (tests, CLI)

    try:
        # Chercher le VarSet quel que soit son nom (App::VarSet, params, etc.)
        def _find_varset(doc):
            vs = doc.getObject('VarSet')
            if vs: return vs
            for obj in doc.Objects:
                if obj.TypeId == 'App::VarSet': return obj
            for obj in doc.Objects:
                if obj.TypeId in ('App::FeaturePython','Part::FeaturePython'):
                    for p in (getattr(obj,'PropertiesList',None) or []):
                        try:
                            if obj.getGroupOfProperty(p) not in ('Base',''):
                                return obj
                        except Exception: pass
            return None
        varset = _find_varset(tmp_doc)
        if varset is None:
            raise RuntimeError(f"VarSet not found in {fcstd_path}")

        # 1. Mapper les noms JSON vers les noms VarSet
        excluded = _EXCLUDED_BASE | (excluded_keys or set())  # 2.2 — base + clés spécifiques
        mapped_params = {}
        for key, val in params.items():
            if key in excluded:
                continue
            varset_key = _JSON_TO_VARSET.get(key, key)
            mapped_params[varset_key] = val

        # 2. Appliquer au VarSet
        applied = []
        _builtin = frozenset({
            'Label','Label2','Visibility','ExpressionEngine',
            'Map','MapMode','MapPathParameter','MapReversed',
            'AttachmentOffset','Shape','Placement','Support',
        })
        for key, val in mapped_params.items():
            if key not in (varset.PropertiesList or []):
                continue
            if key in _builtin:
                continue
            current = getattr(varset, key)
            try:
                # Gérer App::PropertyLength et autres Quantity
                # Normaliser virgule décimale (locale FR) → point
                val_norm = str(val).replace(',', '.') if isinstance(val, str) else val
                if hasattr(current, 'Value'):
                    setattr(varset, key, float(val_norm))
                elif isinstance(current, int):
                    setattr(varset, key, int(float(val_norm)))
                else:
                    setattr(varset, key, float(val_norm))
                applied.append(key)
            except Exception as e:
                _dbg(f"[VarSet] {key}={val}: {e}")

        _dbg(f"VarSet: {len(applied)} params applied: {applied}")

        # 5A.1 — lire le pseudo-timestamp depuis le VarSet
        # Si absent, initialiser avec le timestamp courant et sauvegarder le FCStd
        sc_timestamp = ''
        try:
            props = varset.PropertiesList or []
            if 'SC_TimeStamp' in props and varset.SC_TimeStamp:
                sc_timestamp = str(varset.SC_TimeStamp)    # timestamp existant
            else:
                from datetime import datetime as _dt
                sc_timestamp = _dt.now().strftime('%Y%m%d-%H%M%S')
                if 'SC_TimeStamp' not in props:            # propriété absente → la créer
                    varset.addProperty('App::PropertyString',
                                       'SC_TimeStamp', 'StandardPartSelector')
                varset.SC_TimeStamp = sc_timestamp
                # Sauvegarder uniquement dans la copie tmp, pas dans le source
                if tmp_doc.FileName != fcstd_path:
                    tmp_doc.save()                          # persiste dans le FCStd
                _dbg(f'[SC_TimeStamp] Initialisé : {sc_timestamp}')
        except Exception as e:
            _dbg(f'[SC_TimeStamp] {e}')

        tmp_doc.recompute()

        # Détecter le type de racine : Part (multi-body) ou Body (mono-body)
        root_src = (
            tmp_doc.getObject('Part') or
            tmp_doc.getObject('Part001') or
            next((o for o in tmp_doc.Objects if o.TypeId == 'App::Part'), None)
        )
        if root_src is None:
            root_src = (
                tmp_doc.getObject('Body') or
                tmp_doc.getObject('Body001') or
                next((o for o in tmp_doc.Objects if o.TypeId == 'PartDesign::Body'), None)
            )
        if root_src is None:
            raise RuntimeError("Ni Part ni Body trouve dans le template")

        # Mode copie : mergeProject → active_doc
        objs_before = set(o.Name for o in active_doc.Objects)
        # Essayer mergeProject (préserve MultiTransform + références)
        try:
            tmp_doc.save()
            _tmp_path = tmp_doc.FileName
            App.closeDocument(tmp_name)
            tmp_name = None
            # mergeProject() émet des avertissements ElementMap bénins
            # ("Invalid element name string id" / "Missing element name
            # prefix id") liés au nommage topologique étendu — sans impact
            # sur le résultat (confirmé 24/08/2026). Coupure ciblée du
            # statut 'Wrn' de l'observateur 'ReportOutput' (panneau Rapport)
            # pendant ce seul appel, restaurée immédiatement après (y
            # compris en cas d'exception) pour ne pas masquer d'autres
            # avertissements FreeCAD par ailleurs.
            _wrn_was_on = App.Console.GetStatus('ReportOutput', 'Wrn')
            try:
                if _wrn_was_on:
                    App.Console.SetStatus('ReportOutput', 'Wrn', False)
                active_doc.mergeProject(_tmp_path)
            finally:
                if _wrn_was_on:
                    App.Console.SetStatus('ReportOutput', 'Wrn', True)
            # Corriger les expressions <<params>> → <<params003>>
            for _o in active_doc.Objects:
                if _o.TypeId != 'App::VarSet': continue
                if _o.Name in objs_before: continue
                _orig = _o.Label.rstrip('0123456789')
                if _orig == _o.Label: continue
                _new_lbl = _o.Label
                for _oo in active_doc.Objects:
                    if _oo.Name in objs_before: continue
                    for _path, _expr in list(
                            getattr(_oo, 'ExpressionEngine', [])):
                        if f'<<{_orig}>>' in _expr:
                            try:
                                _oo.setExpression(_path,
                                    _expr.replace(
                                        f'<<{_orig}>>',
                                        f'<<{_new_lbl}>>'))
                            except Exception: pass
            active_doc.recompute()
            _dbg('[Component] mergeProject OK')
        except Exception as _me:
            _dbg(f'[Component] mergeProject échoué ({_me}), fallback copyObject')
            if tmp_name is None:
                tmp_doc2 = App.openDocument(tmp_fcstd_path)
                _co = active_doc.copyObject(
                    next((o for o in tmp_doc2.Objects
                          if o.TypeId == 'App::Part'), None) or
                    next((o for o in tmp_doc2.Objects
                          if o.TypeId == 'PartDesign::Body'), None),
                    True)
                App.closeDocument(tmp_doc2.Name)
            else:
                _co = active_doc.copyObject(root_src, True)
            if not isinstance(_co, (list, tuple)):
                _co = [_co]
        new_objs = [o for o in active_doc.Objects
                    if o.Name not in objs_before]
        root = (
            next((o for o in new_objs if o.TypeId == 'App::Part'), None) or
            next((o for o in new_objs if o.TypeId == 'PartDesign::Body'), None) or
            (new_objs[-1] if new_objs else root_src)
        )
        _dbg(f'[Component] {len(new_objs)} objets, racine={root.TypeId}')

        # Diagnostic : TypeId + expressions éventuelles sur Placement
        exprs = getattr(root, 'ExpressionEngine', [])
        _dbg(f'[root] TypeId={root.TypeId}  ExpressionEngine={exprs}')

        # Nettoyer toute expression qui écraserait le Placement manuel
        try:
            for expr_path, _ in (exprs or []):
                if 'Placement' in str(expr_path):
                    root.clearExpression(expr_path)
                    _dbg(f'[root] Expression Placement effacée : {expr_path}')
        except Exception as _ex:
            _dbg(f'[root] clearExpression : {_ex}')

        if label:
            root.Label = label + ' '                       # espace : FreeCAD numérote
            # Appliquer aussi sur le Body à l'intérieur du Part
            for child in getattr(root, 'OutList', []):
                if child.TypeId == 'PartDesign::Body':
                    child.Label = label + ' '
                    break

        # 5A.1 — écrire les propriétés SC_ sur l'objet inséré
        _write_sc_properties(root, comp_key, family, variant,
                             ref or label or '', sc_timestamp)

        # Colonnes de données supplémentaires → propriétés SC_ typées
        _write_sc_data_properties(root, params, data_props)

        active_doc.recompute()

    finally:
        # Fermer tmp_doc si encore ouvert
        if tmp_name and tmp_name in [d.Name for d in App.listDocuments().values()]:
            App.closeDocument(tmp_name)
        # Supprimer la copie temporaire
        try:
            if os.path.exists(tmp_fcstd_path):
                os.remove(tmp_fcstd_path)
        except Exception:
            pass
        # Dégeler l'affichage et rafraîchir
        try:
            import FreeCADGui as Gui
            try:
                from PySide6.QtCore import QTimer
            except ImportError:
                from PySide2.QtCore import QTimer
            Gui.setActiveDocument(active_doc.Name)
            try:
                Gui.getDocument(active_doc.Name).ActiveView.setRedraw(True)
            except Exception:
                pass
            Gui.updateGui()
            if was_empty:
                delay = 500 if active_doc_was_new else 100
                QTimer.singleShot(
                    delay,
                    lambda: _fit_view(active_doc.Name, set_perspective=active_doc_was_new)
                )
        except Exception as e:
            _dbg(f"[display] {e}")

    print(f"{label or 'Component'} inserted into {active_doc.Name}")  # 1.3 — 'Bearing' → 'Component'
    return root, active_doc
