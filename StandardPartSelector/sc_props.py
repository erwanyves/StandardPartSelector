# -*- coding: utf-8 -*-
"""
StandardPartSelector.sc_props — Gestion des propriétés SC_ et composants sélectionnés.
"""
import FreeCAD as App
from config import _dbg
from datetime import datetime as _dt
from config import MACRO_DIR, _t, _get_qt
from catalog import discover_components, load_catalog
from geometry import _highlight
import documents

# ── Propriétés SC_ de données (Prix, Fournisseur…) ──────────────────────
# Même groupe que les propriétés SC_ techniques.
SC_GROUP    = 'StandardPartSelector'
SC_RESERVED = {'SC_Standard', 'SC_Family', 'SC_Category',
               'SC_Reference', 'SC_TimeStamp'}
_TYPE_TO_PROP = {
    'float': 'App::PropertyFloat',
    'int':   'App::PropertyInteger',
    'text':  'App::PropertyString',
    'bool':  'App::PropertyBool',
}


def sc_data_prop_name(key):
    """Nom de propriété FreeCAD pour une colonne de données : SC_<clé sanitisée>."""
    import re
    name = re.sub(r'\W', '_', str(key)).strip('_')
    if not name or name[0].isdigit():
        name = 'd_' + name
    return 'SC_' + name


def coerce_value(value, ptype):
    try:
        if ptype == 'float': return float(value)
        if ptype == 'int':   return int(float(value))
        if ptype == 'bool':
            return str(value).strip().lower() in ('1', 'true', 'vrai', 'oui', 'yes', 'x')
        return str(value)
    except (TypeError, ValueError):
        return str(value) if ptype == 'text' else None


def _values_equal(a, b):
    try:
        return float(a) == float(b)
    except (TypeError, ValueError):
        return str(a) == str(b)


def diff_sc_data_properties(obj, params, data_props):
    """
    Compare les propriétés SC_ de données du composant aux valeurs catalogue.
    Retourne [(clé, valeur_actuelle, valeur_cible)] :
      - valeur_actuelle None  → propriété à créer
      - valeur_cible None     → propriété orpheline à supprimer
    """
    changed = []
    expected_names = set()
    for key, ptype in (data_props or {}).items():
        if key not in params:
            continue
        ref_val = coerce_value(params[key], ptype)
        if ref_val is None:
            continue
        name = sc_data_prop_name(key)
        if name in SC_RESERVED:
            continue
        expected_names.add(name)
        if not hasattr(obj, name):
            changed.append((key, None, ref_val))
        elif not _values_equal(getattr(obj, name), ref_val):
            changed.append((key, getattr(obj, name), ref_val))
    # Orphelines : propriétés SC_ de données qui ne sont plus au catalogue
    for name in getattr(obj, 'PropertiesList', []) or []:
        if (name.startswith('SC_') and name not in SC_RESERVED
                and name not in expected_names):
            changed.append((name, getattr(obj, name, None), None))
    return changed


def apply_sc_data_properties(obj, params, data_props):
    """
    Réconcilie les propriétés SC_ de données sur le composant :
    création, mise à jour, correction de groupe, suppression des orphelines.
    Idempotent. `params` doit être le dict catalogue COMPLET (clés muettes incluses).
    """
    expected = {}
    for key, ptype in (data_props or {}).items():
        prop_qt = _TYPE_TO_PROP.get(ptype)
        if not prop_qt or key not in params:
            continue
        name = sc_data_prop_name(key)
        if name in SC_RESERVED:
            continue
        val = coerce_value(params[key], ptype)
        if val is None:
            continue
        expected[name] = (prop_qt, val)

    # Création / mise à jour / correction de groupe
    for name, (prop_qt, val) in expected.items():
        try:
            if not hasattr(obj, name):
                obj.addProperty(prop_qt, name, SC_GROUP)
            try:
                obj.setGroupOfProperty(name, SC_GROUP)   # regrouper avec les SC_
            except Exception:
                pass
            setattr(obj, name, val)
        except Exception as e:
            _dbg(f'[SC_data] {name}: {e}')

    # Suppression des propriétés SC_ de données devenues orphelines
    for name in list(getattr(obj, 'PropertiesList', []) or []):
        if (name.startswith('SC_') and name not in SC_RESERVED
                and name not in expected):
            try:
                obj.removeProperty(name)
                _dbg(f'[SC_data] {name} supprimée (orpheline)')
            except Exception as e:
                _dbg(f'[SC_data] suppression {name}: {e}')


def _detect_selected_sc_component():
    """
    Lit la sélection FreeCAD active.
    Si un objet portant les propriétés SC_ est sélectionné, retourne un dict
    avec ses métadonnées. Retourne None sinon.
    """
    try:
        import FreeCADGui as Gui
        sel = Gui.Selection.getSelection()
        for obj in sel:
            props = getattr(obj, 'PropertiesList', None) or []
            if 'SC_Reference' not in props:
                continue
            return {
                'obj':        obj,
                'comp_key':   getattr(obj, 'SC_Standard',  ''),
                'family':     getattr(obj, 'SC_Family',    ''),
                'variant':    getattr(obj, 'SC_Category',  ''),
                'ref':        getattr(obj, 'SC_Reference', ''),
                'timestamp':  getattr(obj, 'SC_TimeStamp', ''),
            }
    except Exception as e:
        _dbg(f'[5B.2] Détection SC ignorée : {e}')
    return None


def _sc_info_from_obj(obj):
    """Construit le dict de métadonnées SC_ à partir d'un objet qui les porte."""
    return {
        'obj':        obj,
        'comp_key':   getattr(obj, 'SC_Standard',  ''),
        'family':     getattr(obj, 'SC_Family',    ''),
        'variant':    getattr(obj, 'SC_Category',  ''),
        'ref':        getattr(obj, 'SC_Reference', ''),
        'timestamp':  getattr(obj, 'SC_TimeStamp', ''),
    }


def _resolve_selected_sc_component(max_depth=8):
    """
    Remonte depuis un sous-élément sélectionné (face/arête/sommet dans la vue
    3D, ou sous-objet imbriqué dans l'arbre) jusqu'à un composant SC_ parent,
    pour les cas où `_detect_selected_sc_component` (correspondance directe
    uniquement) ne trouve rien.

    Copie locale, propre à StandardPartSelector, de la même logique de
    remontée que `SmartFinder/detector.py::resolve_selected_component`
    (contrainte d'indépendance entre macros : pas de module partagé).

    Retourne le même dict que `_detect_selected_sc_component`, ou None.
    """
    try:
        import FreeCADGui as Gui
        sel = Gui.Selection.getSelection()
    except Exception as e:
        _dbg(f'[5B.2] Résolution SC ignorée : {e}')
        return None

    def _has_sc(o):
        if o is None:
            return False
        props = getattr(o, 'PropertiesList', None) or []
        return 'SC_Reference' in props

    for start in sel:
        if _has_sc(start):
            continue  # déjà couvert par la détection directe
        current = start
        seen = set()
        for _ in range(max_depth):
            if current is None or id(current) in seen:
                break
            seen.add(id(current))

            body = getattr(current, '_Body', None)
            if _has_sc(body):
                return _sc_info_from_obj(body)

            for parent in getattr(current, 'InList', []) or []:
                if _has_sc(parent):
                    return _sc_info_from_obj(parent)

            try:
                grp = current.getParentGeoFeatureGroup()
            except Exception:
                grp = None
            if _has_sc(grp):
                return _sc_info_from_obj(grp)

            linked = getattr(current, 'LinkedObject', None)
            if _has_sc(linked):
                return _sc_info_from_obj(linked)

            # Remonter d'un niveau pour la prochaine itération : _Body en
            # priorité, sinon le premier parent InList.
            nxt = body
            if nxt is None:
                inlist = getattr(current, 'InList', []) or []
                nxt = inlist[0] if inlist else None
            current = nxt
    return None


def ask_edit_or_new_component(sc_info, QtWidgets):
    """
    Élément géométrique d'un composant standard existant sélectionné (pas
    l'objet racine lui-même) : demande à l'utilisateur s'il souhaite modifier
    ce composant ou en créer un nouveau, plutôt que de trancher silencieusement.

    Retourne 'edit', 'new', ou None (annulé — la macro doit alors s'arrêter).
    """
    box = QtWidgets.QMessageBox()
    box.setWindowTitle(_t('sc_edit_or_new_title'))
    box.setText(_t('sc_edit_or_new_prompt', ref=sc_info.get('ref', '')))
    box.setIcon(QtWidgets.QMessageBox.Question)
    btn_edit   = box.addButton(_t('sc_edit_or_new_btn_edit'),
                               QtWidgets.QMessageBox.AcceptRole)
    btn_new    = box.addButton(_t('sc_edit_or_new_btn_new'),
                               QtWidgets.QMessageBox.ActionRole)
    btn_cancel = box.addButton(_t('cancel_btn'),
                               QtWidgets.QMessageBox.RejectRole)
    box.setDefaultButton(btn_edit)
    box.exec()
    clicked = box.clickedButton()
    if clicked is btn_edit:
        return 'edit'
    if clicked is btn_new:
        return 'new'
    return None


def _find_varset_in_object(obj):
    """
    Cherche récursivement le VarSet dans l'arbre de l'objet inséré.
    Retourne le VarSet ou None.
    """
    try:
        to_visit = list(getattr(obj, 'OutList', []))
        visited  = set()
        while to_visit:
            child = to_visit.pop()
            if id(child) in visited:
                continue
            visited.add(id(child))
            props = getattr(child, 'PropertiesList', None) or []
            if 'SC_TimeStamp' in props:
                return child
            to_visit.extend(getattr(child, 'OutList', []))
    except Exception as e:
        _dbg(f'[5B.2] Recherche VarSet ignorée : {e}')
    return None


def _update_sc_varset(obj, varset, catalog_params, new_timestamp,
                      excluded_keys, QtWidgets, silent=False):
    """
    Met à jour uniquement le VarSet de l'objet existant.
    Recompute le document. Met à jour SC_TimeStamp sur l'objet.
    Préserve toutes les références dans l'assemblage.
    """
    try:
        # ⚠ `obj` vient d'une sélection RÉSOLUE : c'est l'objet du
        # document SOURCE, celui qu'un lien peut avoir chargé
        # partiellement. Cette fonction est le seul point de passage
        # commun au remplacement et à la mise à jour en lot : le
        # garde-fou posé ici couvre les deux sans se dupliquer.
        # Il LÈVE, et les deux appelants rapportent déjà l'exception.
        documents.garantir(obj, _t)
        doc   = obj.Document
        props = getattr(varset, 'PropertiesList', None) or []

        applied = []
        for key, val in catalog_params.items():
            if key in (excluded_keys or set()):
                continue
            varset_key = key   # mapping 1:1 (voir _JSON_TO_VARSET dans Component.py)
            if varset_key not in props:
                continue
            current = getattr(varset, varset_key)
            try:
                new_val = int(val) if isinstance(current, int) else float(val)
                setattr(varset, varset_key, new_val)
                applied.append(varset_key)
            except Exception as ex:
                _dbg(f'[5B.2] VarSet {varset_key}: {ex}')

        # Mettre à jour le timestamp sur le VarSet ET sur l'objet racine
        if 'SC_TimeStamp' in props:
            varset.SC_TimeStamp = new_timestamp
        obj_props = getattr(obj, 'PropertiesList', None) or []
        if 'SC_TimeStamp' in obj_props:
            obj.SC_TimeStamp = new_timestamp

        doc.recompute()
        _dbg(f'[5B.2] VarSet mis à jour : {applied}')
        _dbg(f'[5B.2] SC_TimeStamp → {new_timestamp}')

        if not silent:
            QtWidgets.QMessageBox.information(
                None, "StandardPartSelector",
                _t('sc_update_success', ref=getattr(obj, 'SC_Reference', ''),
                   count=len(applied))
            )
    except Exception as e:
        import traceback
        _dbg(f'[5B.2] Mise à jour VarSet échouée : {e}')
        print(traceback.format_exc())
        if not silent:
            QtWidgets.QMessageBox.critical(None, "StandardPartSelector", str(e))
        raise


def _find_matching_ref(varset, catalog, excluded_keys=None):
    """
    Cherche dans le catalogue la référence dont les paramètres
    correspondent EXACTEMENT (égalité stricte) au VarSet.
    Retourne le nom de la référence ou None.
    """
    excl = excluded_keys or set()
    try:
        props = getattr(varset, 'PropertiesList', None) or []
        for ref_name, ref_vals in catalog.items():
            if ref_name.startswith('_'):
                continue
            match = True
            for key, ref_val in ref_vals.items():
                if key in excl or key == 'ref':
                    continue
                if key not in props:
                    continue
                current = getattr(varset, key, None)
                if current is None:
                    continue
                try:
                    if float(current) != float(ref_val):
                        match = False
                        break
                except (TypeError, ValueError):
                    if str(current) != str(ref_val):
                        match = False
                        break
            if match:
                return ref_name
    except Exception as e:
        _dbg(f'[find_ref] {e}')
    return None


def _make_auto_ref_name(varset, catalog, excluded_keys=None):
    """
    Génère un nom de référence automatique basé sur les valeurs du VarSet.
    Exemple : 'd17_D35_B8' pour un roulement bore=17, outer=35, width=8
    """
    excl = excluded_keys or set()
    try:
        props = getattr(varset, 'PropertiesList', None) or []
        # Prendre les 3 premières clés non exclues
        parts = []
        for key in props:
            if key in excl or key in ('ref', 'Label', 'Label2',
                                      'Visibility', 'ExpressionEngine',
                                      'SC_TimeStamp'):
                continue
            val = getattr(varset, key, None)
            if val is None:
                continue
            try:
                v = float(val)
                # Formater sans .0 si entier
                s = str(int(v)) if v == int(v) else f'{v:.2f}'.rstrip('0')
                parts.append(f'{key[0]}{s}')
            except (TypeError, ValueError):
                pass
            if len(parts) >= 3:
                break
        base = '_'.join(parts) if parts else 'custom'
        # Éviter les doublons avec le catalogue existant
        existing = set(k for k in catalog if not k.startswith('_'))
        name = base
        i = 2
        while name in existing:
            name = f'{base}_{i}'
            i += 1
        return name
    except Exception as e:
        _dbg(f'[auto_ref] {e}')
    return 'custom'


def _open_editor_for_new_ref(varset, comp_key, family, variant,
                              auto_name, fcstd, QtWidgets):
    """
    Ouvre un dialogue pour saisir le nom de la nouvelle référence,
    puis lance l'éditeur de catalogues pré-rempli avec les valeurs
    actuelles du VarSet.
    """
    # ── Dialogue de saisie du nom ────────────────────────────────────────
    dlg = QtWidgets.QDialog()
    dlg.setWindowTitle(_t('sc_nonstandard_title'))
    dlg.setMinimumWidth(380)
    lay = QtWidgets.QVBoxLayout()

    lay.addWidget(QtWidgets.QLabel(_t('sc_new_ref_name_label')))
    edit = QtWidgets.QLineEdit(auto_name)
    edit.selectAll()
    lay.addWidget(edit)

    bbox = QtWidgets.QDialogButtonBox(
        QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
    bbox.accepted.connect(dlg.accept)
    bbox.rejected.connect(dlg.reject)
    lay.addWidget(bbox)
    dlg.setLayout(lay)

    if dlg.exec() != QtWidgets.QDialog.Accepted:
        return None
    new_ref_name = edit.text().strip() or auto_name

    # ── Construire les valeurs initiales depuis le VarSet ────────────────
    props = getattr(varset, 'PropertiesList', None) or []
    init_vals = {}
    for key in props:
        if key in ('Label', 'Label2', 'Visibility',
                   'ExpressionEngine', 'SC_TimeStamp'):
            continue
        val = getattr(varset, key, None)
        if val is not None:
            init_vals[key] = val

    # ── Ouvrir l'éditeur pré-rempli ──────────────────────────────────────
    try:
        import os, sys
        sc_dir = MACRO_DIR
        editor_path = os.path.join(sc_dir, 'StandardPartSelectorEditor.py')
        if not os.path.exists(editor_path):
            raise FileNotFoundError(f'Éditeur introuvable : {editor_path}')
        if sc_dir not in sys.path:
            sys.path.insert(0, sc_dir)
        import importlib.util as _ilu
        spec = _ilu.spec_from_file_location('StandardPartSelectorEditor',
                                             editor_path)
        mod  = _ilu.module_from_spec(spec)
        spec.loader.exec_module(mod)
        mod.open_editor(
            preselect_comp    = comp_key,
            preselect_family  = family,
            preselect_variant = variant,
            new_ref_name      = new_ref_name,
            new_ref_vals      = init_vals,
            restrict_fcstd    = fcstd,
            force_action      = 'edit',
        )
    except Exception as e:
        import traceback
        _dbg(f'[editor] Erreur ouverture éditeur : {e}')
        print(traceback.format_exc())
        QtWidgets.QMessageBox.warning(
            None, _t('sc_nonstandard_title'),
            f'{_t("editor_open_error")}: {e}')
    return new_ref_name

def _handle_sc_selection(sc_info, components, QtWidgets):
    """
    Traite un composant SC sélectionné avant d'ouvrir le dialogue.
    Retourne un dict 'preselect' pour pré-positionner le dialogue,
    ou None si la sélection doit être ignorée.
    """
    comp_key  = sc_info['comp_key']
    family    = sc_info['family']
    variant   = sc_info['variant']
    ref       = sc_info['ref']
    ts_obj    = sc_info['timestamp']
    obj       = sc_info['obj']

    # Vérifier que le composant existe encore dans les catalogues chargés
    comp_data = components.get(comp_key)
    if not comp_data:
        _dbg(f'[5B.2] Composant {comp_key!r} non trouvé dans les catalogues.')
        return None

    vcfg = comp_data['cfg'].get(family, {}).get(variant)
    if not vcfg:
        _dbg(f'[5B.2] Variante {family}/{variant} non trouvée.')
        return None

    # Charger le catalogue pour récupérer les paramètres de référence
    try:
        catalog = load_catalog(vcfg['json'])
    except Exception as e:
        _dbg(f'[5B.2] Catalogue non chargeable : {e}')
        return None

    catalog_params = catalog.get(ref)
    if catalog_params is None:
        _dbg(f'[5B.2] Référence {ref!r} absente du catalogue.')
        return None

    # Chercher le VarSet dans l'objet inséré
    varset = _find_varset_in_object(obj)
    _fcstd = vcfg.get('fcstd', '') if vcfg else ''
    if varset is None or not ts_obj:
        # Pas de VarSet trouvable ou pas de timestamp → dialogue normal
        return {'comp_key': comp_key, 'family': family,
                'variant': variant, 'ref': ref,
                'obj': obj, 'fcstd': _fcstd}

    # Lire le timestamp courant dans le FCStd
    try:
        tmp_doc    = App.openDocument(vcfg['fcstd'])
        tmp_vs     = tmp_doc.getObject('VarSet')
        ts_current = ''
        if tmp_vs:
            props_vs = getattr(tmp_vs, 'PropertiesList', None) or []
            if 'SC_TimeStamp' in props_vs:
                ts_current = str(tmp_vs.SC_TimeStamp)
        App.closeDocument(tmp_doc.Name)
    except Exception as e:
        _dbg(f'[5B.2] Lecture FCStd ignorée : {e}')
        ts_current = ts_obj   # en cas d'erreur → pas de mise à jour

    # ── Cas 1 : modèle FCStd plus récent → proposer mise à jour ──────────
    if ts_current and ts_obj and ts_current > ts_obj:
        reply = QtWidgets.QMessageBox.question(
            None,
            _t('sc_update_title'),
            _t('sc_update_prompt', ref=ref,
               ts_old=ts_obj, ts_new=ts_current),
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
            QtWidgets.QMessageBox.Yes
        )
        if reply == QtWidgets.QMessageBox.Yes:
            _update_sc_varset(obj, varset, catalog_params,
                              ts_current,
                              vcfg.get('excluded_keys', set()),
                              QtWidgets, silent=True)
            apply_sc_data_properties(obj, catalog.get(ref, {}),
                                     vcfg.get('data_props', {}))
        return {'comp_key': comp_key, 'family': family,
                'variant': variant, 'ref': ref,
                'obj': obj, 'fcstd': _fcstd}

    # ── Détecter si le VarSet correspond à une référence connue ──────────
    matching_ref = _find_matching_ref(
        varset, catalog,
        excluded_keys=set(vcfg.get('excluded_keys', [])))

    if matching_ref is None:
        # Composant non-standard : VarSet ne correspond à aucune référence
        auto_name = _make_auto_ref_name(
            varset, catalog,
            excluded_keys=set(vcfg.get('excluded_keys', [])))
        reply = QtWidgets.QMessageBox.question(
            None,
            _t('sc_nonstandard_title'),
            _t('sc_nonstandard_prompt', ref=ref, auto_name=auto_name),
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
            QtWidgets.QMessageBox.No
        )
        if reply == QtWidgets.QMessageBox.Yes:
            # Ouvrir l'éditeur pré-rempli avec les valeurs actuelles du VarSet
            saved_name = _open_editor_for_new_ref(
                varset, comp_key, family, variant,
                auto_name, _fcstd, QtWidgets)
            if saved_name:
                # Recharger le catalogue et remplacer directement
                _components = discover_components()
                _vcfg2 = _components.get(comp_key, {}).get(
                    'cfg', {}).get(family, {}).get(variant, {})
                try:
                    _cat2   = load_catalog(_vcfg2['json'])
                    _params = _cat2.get(saved_name)
                    if _params:
                        _cfg2 = {family: {variant: _vcfg2}}
                        _replace_component(
                            obj, comp_key, family, variant,
                            saved_name, _params, _cfg2, QtWidgets)
                        print(_t('success_msg', ref=saved_name))
                    else:
                        _dbg(f'[replace] Référence {saved_name!r} '
                              f'introuvable après sauvegarde')
                except Exception as _re:
                    _dbg(f'[replace] Erreur : {_re}')
            return None   # Fermer sans ouvrir le sélecteur
        # Non → continuer vers le dialogue de remplacement normal

    # Dans tous les cas → pré-positionner le dialogue
    # obj inclus pour permettre le remplacement in-place
    # fcstd inclus pour filtrer les choix possibles
    # Utiliser SC_Reference (ref) comme pré-sélection principale :
    # matching_ref peut être trompeur si le catalogue JSON est corrompu.
    _fcstd = vcfg.get('fcstd', '') if vcfg else ''
    return {'comp_key': comp_key, 'family': family,
            'variant': variant, 'ref': ref,
            'obj': obj, 'fcstd': _fcstd}

# ============================================================
# Interface — fonctions auxiliaires (4.1)
# ============================================================


def _replace_component(old_obj, comp_key, family, variant,
                       ref, params, cfg, QtWidgets):
    """
    Remplace un composant SC_ existant par un nouveau.
    Le filtre FCStd est déjà appliqué en amont dans l'UI.
    Toujours une mise à jour VarSet in-place.
    """
    import FreeCAD as App, os as _os
    from datetime import datetime as _dt
    old_sc_variant = getattr(old_obj, 'SC_Category', '')

    # ── Mise à jour VarSet in-place ───────────────────────────────────────
    varset = _find_varset_in_object(old_obj)
    if varset is None:
        raise RuntimeError(_t('no_varset_found'))
    new_ts = _dt.now().strftime('%Y%m%d-%H%M%S')
    vcfg   = cfg.get(family, {}).get(variant, {})
    excl   = set(vcfg.get('excluded_keys', []))
    catalog_params = {k: v for k, v in params.items()
                      if k not in excl and k != 'ref'}
    _update_sc_varset(old_obj, varset, catalog_params,
                      new_ts, excl, QtWidgets, silent=True)
    # Réconcilier les propriétés SC_ de données (création/màj/suppression).
    # params (complet) car les colonnes de données sont des clés exclues.
    apply_sc_data_properties(old_obj, params, vcfg.get('data_props', {}))
    # Mettre à jour label et propriétés SC_
    old_obj.SC_Reference = ref
    old_obj.SC_Family    = family
    old_obj.SC_Category  = variant
    try:
        old_obj.Label = ref
    except Exception:
        try: old_obj.Label = ref + '_'
        except Exception: pass
    # Constat du 26/09/2026 : on écrivait sur `old_obj` et on recalculait
    # `App.ActiveDocument`. En assemblage lié ce sont DEUX documents
    # différents — mémorandum §3, question 4.
    old_obj.Document.recompute()
    _highlight(old_obj, on=True)
    _dbg(f'[replace] Mise à jour in-place : {old_obj.Label} '
          f'({old_sc_variant} → {variant})')
