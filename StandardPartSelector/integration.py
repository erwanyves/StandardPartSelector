# -*- coding: utf-8 -*-
"""
StandardPartSelector.integration — Wizard d'intégration de composants hors-standard.

Flux :
  0. Question préliminaire
  1. Analyse du Body/Part sélectionné (VarSet, params)
  2. Classement : famille, variante, référence, nom FCStd
  3. Placement : réinitialiser origine et orientation avant export
  4. Confirmation + sauvegarde dans Standards/
     → conversion de l'objet original en standard (propriétés SC_)
"""
import os, json
from config import _dbg
import FreeCAD as App
from datetime import datetime
from config  import MACRO_DIR, STANDARDS_DIR, _t, _get_qt
from catalog import load_comp_config
import documents

# ── Constantes ──────────────────────────────────────────────────────────────
_INTEGRABLE_TYPES = ('PartDesign::Body', 'App::Part')


# ══════════════════════════════════════════════════════════════════════════════
# Analyse
# ══════════════════════════════════════════════════════════════════════════════

def _remonter_vers_integrable(objet, max_depth=8):
    """Premier Body ou Part en remontant depuis `objet`, ou None.

    Désigner un objet dans l'arbre du modèle rend cet objet, pas son
    corps : cliquer « Pad » ou « Fillet » dans l'arbre doit pouvoir servir
    à intégrer le corps qui les porte.

    ⚠ Ce n'est PAS le chemin d'un clic en vue 3D : celui-ci porte un
    sous-élément et est écarté en amont par `_cible_integrable()`, parce
    qu'il est le geste d'insertion de la macro.

    Ordre d'essai : l'objet lui-même, `_Body` (feature interne à un
    `PartDesign::Body`), le premier parent `InList` intégrable, puis
    `getParentGeoFeatureGroup()`.

    ⚠ **`LinkedObject` n'est délibérément PAS suivi.** Remonter dans un
    composant lié pour l'intégrer serait une capacité nouvelle — aujourd'hui
    un `App::Link` n'est pas intégrable et ne l'était pas davantage avant ce
    correctif. C'est une décision de conception, pas un oubli.

    ⚠ C'est la **troisième** instance de ce parcours dans cette macro
    (`sc_props.py::_resolve_selected_sc_component`, et le calcul de
    `_gp_obj` dans `geometry.py`), et il en existe une quatrième dans
    `SmartFinder/detector.py`. Leur unification est le chantier C du plan ;
    elle n'est pas faite ici, et cette note est là pour que la quatrième
    copie ne s'écrive pas sans qu'on le sache.

    Fonction **pure** : elle ne lit que des attributs, et s'éprouve donc
    hors FreeCAD (`_outils_claude/ESSAI_detection_body_sps.py`).
    """
    vu = set()
    courant = objet
    profondeur = 0
    while courant is not None and profondeur < max_depth:
        if id(courant) in vu:
            break
        vu.add(id(courant))
        profondeur += 1

        if getattr(courant, 'TypeId', None) in _INTEGRABLE_TYPES:
            return courant

        suivant = getattr(courant, '_Body', None)

        if suivant is None:
            for parent in getattr(courant, 'InList', None) or []:
                if getattr(parent, 'TypeId', None) in _INTEGRABLE_TYPES:
                    return parent
                if suivant is None:
                    suivant = parent

        if suivant is None:
            try:
                suivant = courant.getParentGeoFeatureGroup()
            except Exception:
                suivant = None

        if suivant is courant:
            break
        courant = suivant
    return None


def _cible_integrable(entrees, max_depth=8):
    """Premier corps intégrable désigné par `entrees`, ou None.

    `entrees` : suite de couples `(objet, noms_sous_éléments)`, telle que
    `getSelectionEx()` la fournit. La décision est sortie du Qt pour être
    éprouvable hors FreeCAD (`ESSAI_detection_body_sps.py`).

    **Une entrée portant un sous-élément est ignorée.** Un clic en vue 3D
    sur une face ou une arête est le geste d'**insertion** de la macro ;
    faire coïncider les deux mettait une question sans rapport devant le
    geste principal, et un refus fermait la macro — constat de
    l'utilisateur, 20/09/2026. Seule une désignation dans l'arbre du
    modèle ouvre la question de l'intégration.

    Un nom de sous-élément **vide** ne compte pas : selon la version de
    FreeCAD, une sélection d'arbre rend `()` ou `('',)`. Les deux formes
    sont acceptées plutôt que l'une supposée.
    """
    for objet, noms in entrees:
        if [n for n in (noms or ()) if n]:
            continue
        cible = _remonter_vers_integrable(objet, max_depth)
        if cible is None:
            continue
        props = getattr(cible, 'PropertiesList', None) or []
        if 'SC_Reference' not in props:
            return cible
    return None


def _detect_non_sc_body():
    """
    Retourne le premier Body ou Part sélectionné sans propriétés SC_.
    Retourne None si rien de pertinent n'est sélectionné.

    Ne fait qu'adapter la sélection FreeCAD à `_cible_integrable()`, qui
    porte toute la décision.
    """
    try:
        import FreeCADGui as Gui
        entrees = [(s.Object, s.SubElementNames)
                   for s in Gui.Selection.getSelectionEx()]
    except Exception as e:
        _dbg(f'[integration] détection : {e}')
        return None
    return _cible_integrable(entrees)


def _is_varset_owned_by_other(vs):
    """
    Retourne True si ce VarSet appartient déjà à un autre composant standard
    (il a une propriété SC_Reference → il est la propriété d'un Body SC_).
    """
    return 'SC_Reference' in getattr(vs, 'PropertiesList', [])


def _find_varset_for_object(obj):
    """
    Trouve le VarSet "propre" d'un Body ou Part.
    Règles :
      - Un VarSet avec SC_Reference appartient à un autre composant → exclu
      - Priorité 1 : VarSet dans obj.Group (lien hiérarchique direct)
      - Priorité 2 : VarSet dont le Label contient ou est contenu dans
                     le Label du Body (heuristique de nommage)
      - Priorité 3 : un seul VarSet non-SC dans le document
      - Sinon : None (pas de VarSet propre détectable)
    La stratégie par expressions est abandonnée : elle trouve des VarSets
    de composants voisins dans un assemblage (références croisées).
    """
    doc = obj.Document
    body_label = obj.Label.lower()

    # ── Priorité 1 : VarSet enfant direct ────────────────────────────────
    for child in getattr(obj, 'Group', []):
        if child.TypeId == 'App::VarSet' and not _is_varset_owned_by_other(child):
            return child

    # ── Collecter les VarSets non-SC du document ─────────────────────────
    candidates = [
        o for o in doc.Objects
        if o.TypeId == 'App::VarSet' and not _is_varset_owned_by_other(o)
    ]

    if not candidates:
        return None

    # ── Priorité 2 : Label du VarSet proche du Label du Body ─────────────
    for vs in candidates:
        vs_label = vs.Label.lower()
        if (body_label in vs_label or vs_label in body_label
                or body_label.split('_')[0] in vs_label):
            return vs

    # ── Priorité 3 : unique VarSet non-SC dans le document ───────────────
    if len(candidates) == 1:
        return candidates[0]

    # Plusieurs VarSets non identifiables → retourner None
    # Le wizard affichera un avertissement
    return None



def _analyze_body(obj):
    """
    Analyse un Body ou Part FreeCAD :
      - utilise _find_varset_for_object pour cibler le bon VarSet
      - extrait tous ses paramètres non-internes
    Retourne un dict d'analyse.
    """
    doc    = obj.Document
    params = {}

    varset = _find_varset_for_object(obj)

    if varset:
        _INTERNAL = {'ExpressionEngine', 'Label', 'Label2', 'Visibility',
                     'MapMode', 'MapReversed', 'MapPathParameter',
                     'AttachmentOffset'}
        for prop in varset.PropertiesList:
            if (prop.startswith('_') or prop.startswith('SC_')
                    or prop in _INTERNAL):
                continue
            try:
                params[prop] = getattr(varset, prop)
            except Exception:
                pass

    return {
        'obj':        obj,
        'label':      obj.Label,
        'doc_name':   doc.Name,
        'doc_path':   doc.FileName,
        'has_varset': varset is not None,
        'varset':     varset,
        'params':     params,
        'placement':  obj.Placement,
    }


# ══════════════════════════════════════════════════════════════════════════════
# SC_ properties
# ══════════════════════════════════════════════════════════════════════════════

def _apply_sc_properties(obj, family_key, family, variant, ref):
    """
    Applique les propriétés SC_ sur l'objet original dans le document source.
    Convertit ainsi le modèle existant en composant standard reconnu.
    """
    # ⚠ Le document de `obj` peut être chargé PARTIELLEMENT : la
    # docstring ci-dessus le dit, on écrit « dans le document source »,
    # et c'est justement ce document qu'un lien charge à moitié. FreeCAD
    # n'y enregistre rien et refuse de le recalculer. Complété — ou
    # REFUSÉ — avant la première écriture (26/09/2026).
    documents.garantir(obj, _t)
    props = getattr(obj, 'PropertiesList', [])
    sc_data = {
        'SC_Standard':  family_key,
        'SC_Family':    family,
        'SC_Category':  variant,
        'SC_Reference': ref,
        'SC_TimeStamp': datetime.now().strftime('%Y%m%d_%H%M%S'),
    }
    for prop, val in sc_data.items():
        if prop not in props:
            obj.addProperty('App::PropertyString', prop,
                            'StandardPart', prop)
        setattr(obj, prop, val)
    obj.Document.recompute()
    _dbg(f'[integration] SC_ appliqués sur {obj.Label}')


# ══════════════════════════════════════════════════════════════════════════════
# Export FCStd
# ══════════════════════════════════════════════════════════════════════════════

def _extract_body_to_fcstd(obj, dest_path, varset=None,
                           reset_position=False, reset_orientation=False):
    """
    Extrait le Body/Part dans un FCStd autonome.

    Stratégie :
      1. Si le document source est sauvegardé : mergeProject pour préserver
         toutes les expressions, puis suppression des objets étrangers.
      2. Sinon (document non sauvegardé) : copyObject en fallback.
    Dans les deux cas, un VarSet vide est ajouté si absent.
    """
    import shutil as _shutil

    # Sauvegarder les noms AVANT toute opération (mergeProject peut invalider
    # les références Python aux objets FreeCAD du document source)
    obj_name    = obj.Name
    varset_name = varset.Name if varset is not None else None
    src_doc     = obj.Document
    src_path    = src_doc.FileName
    tmp_name    = 'SPS_Integration_Tmp'
    tmp_doc     = App.newDocument(tmp_name)

    try:
        if src_path and os.path.exists(src_path):
            # ── Stratégie 1 : mergeProject ────────────────────────────────
            # Créer une copie temporaire du doc source dans le même dossier
            # pour que FreeCAD résolve correctement les chemins relatifs
            tmp_src = os.path.join(os.path.dirname(src_path),
                                   '_sps_int_tmp.FCStd')
            _shutil.copy2(src_path, tmp_src)
            try:
                tmp_doc.mergeProject(tmp_src)
            finally:
                try: os.remove(tmp_src)
                except Exception: pass

            tmp_doc.recompute()

            # Identifier les objets à conserver : Body + VarSet + dépendances
            body_in_tmp = tmp_doc.getObject(obj_name)
            vs_in_tmp   = (tmp_doc.getObject(varset_name)
                           if varset_name is not None else None)

            # Supprimer tout ce qui n'est ni le Body ni son VarSet
            # (prudence : ne supprimer que les objets de haut niveau)
            to_keep = set()
            if body_in_tmp:
                to_keep.add(body_in_tmp.Name)
                for dep in getattr(body_in_tmp, 'Group', []):
                    to_keep.add(dep.Name)
            if vs_in_tmp:
                to_keep.add(vs_in_tmp.Name)

            for o in list(tmp_doc.Objects):
                try:
                    o_name = o.Name   # peut lever ReferenceError si déjà supprimé
                except ReferenceError:
                    continue          # dépendance supprimée en cascade, ignorer
                if o_name not in to_keep:
                    try: tmp_doc.removeObject(o_name)
                    except Exception: pass

            tmp_doc.recompute()
            _dbg(f'[integration] mergeProject OK : {len(tmp_doc.Objects)} objets')

        else:
            # ── Stratégie 2 : copyObject (fallback) ──────────────────────
            _dbg('[integration] Document non sauvegardé → copyObject')
            if varset is not None:
                tmp_doc.copyObject(varset, False)
            tmp_doc.copyObject(obj, True)
            tmp_doc.recompute()

        # ── Réinitialisations de placement ────────────────────────────────
        copied = tmp_doc.getObject(obj_name)
        if copied:
            pl = copied.Placement
            if reset_position and reset_orientation:
                copied.Placement = App.Placement()
            elif reset_position:
                copied.Placement = App.Placement(
                    App.Vector(0, 0, 0), pl.Rotation)
            elif reset_orientation:
                copied.Placement = App.Placement(
                    pl.Base, App.Rotation())
            tmp_doc.recompute()

        # ── VarSet minimal si absent ──────────────────────────────────────
        if not any(o.TypeId == 'App::VarSet' for o in tmp_doc.Objects):
            vs = tmp_doc.addObject('App::VarSet', 'VarSet')
            vs.Label = 'VarSet'
            tmp_doc.recompute()
            _dbg('[integration] VarSet vide créé (composant non paramétrique)')

        # ── Sauvegarde ────────────────────────────────────────────────────
        os.makedirs(os.path.dirname(dest_path), exist_ok=True)
        tmp_doc.saveAs(dest_path)
        if not os.path.exists(dest_path):
            raise RuntimeError(f'FCStd non créé : {dest_path}')
        _dbg(f'[integration] FCStd sauvegardé : {dest_path}')
    finally:
        try: App.closeDocument(tmp_name)
        except Exception: pass


# ══════════════════════════════════════════════════════════════════════════════
# JSON
# ══════════════════════════════════════════════════════════════════════════════

def _generate_json_entry(ref_name, params, fcstd_name):
    """Génère une entrée JSON — tous les params VarSet deviennent des colonnes."""
    entry = {'_fcstd': fcstd_name}
    for k, v in params.items():
        if hasattr(v, 'Value'):
            v = v.Value
        elif hasattr(v, '__iter__') and not isinstance(v, str):
            v = list(v)
        entry[k] = v
    return {ref_name: entry}


def _write_or_update_json(dest_dir, family_dir, type_name, variant_name,
                          ref_name, params, fcstd_name):
    """
    Écrit la référence dans datas/{fichier}.json (format plat)
    et met à jour config.json avec l'entrée famille/variante.
    Crée les fichiers et répertoires si absents.
    """
    datas_dir  = os.path.join(dest_dir, 'datas')
    cfg_path   = os.path.join(dest_dir, 'config.json')
    os.makedirs(datas_dir, exist_ok=True)
    os.makedirs(os.path.join(dest_dir, 'models'), exist_ok=True)

    # ── Nom du fichier JSON de données ───────────────────────────────────
    safe_type    = type_name.replace(' ', '_').replace('/', '-')
    safe_variant = variant_name.replace(' ', '_').replace('/', '-')
    json_name    = f'{safe_type}_{safe_variant}.json'
    json_path    = os.path.join(datas_dir, json_name)

    # ── Charger ou créer le fichier de données ────────────────────────────
    data = {}
    if os.path.exists(json_path):
        try:
            with open(json_path, encoding='utf-8') as f:
                data = json.load(f)
        except Exception:
            pass

    # Mettre à jour _parametres
    param_keys = list(params.keys())
    existing   = data.get('_parametres', [])
    for k in param_keys:
        if k not in existing:
            existing.append(k)
    if existing:
        data['_parametres'] = existing

    # Ajouter la référence
    entry = {'fcstd': os.path.join('models', fcstd_name)}
    for k, v in params.items():
        if hasattr(v, 'Value'):
            v = v.Value
        elif hasattr(v, '__iter__') and not isinstance(v, str):
            v = list(v)
        entry[k] = v
    data[ref_name] = entry

    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    _dbg(f'[integration] Données : {json_path}')

    # ── Mettre à jour config.json ─────────────────────────────────────────
    cfg = {}
    if os.path.exists(cfg_path):
        try:
            with open(cfg_path, encoding='utf-8') as f:
                cfg = json.load(f)
        except Exception:
            pass

    # Toujours inclure la colonne 'ref' en première position
    # (requis par _get_selection dans ui_selector.py)
    columns = [['ref', 'ref']] + [[k, k] for k in param_keys]

    # Ajouter _excluded_keys au niveau racine de config.json
    # pour que create_component n'essaie pas d'appliquer 'fcstd' au VarSet
    existing_excluded = set(cfg.get('_excluded_keys', []))
    existing_excluded.add('fcstd')
    cfg['_excluded_keys'] = sorted(existing_excluded)

    cfg.setdefault(type_name, {})[variant_name] = {
        'json':    json_name,
        'fcstd':   fcstd_name,
        'columns': columns,
    }

    with open(cfg_path, 'w', encoding='utf-8') as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    _dbg(f'[integration] Config  : {cfg_path}')


# ══════════════════════════════════════════════════════════════════════════════
# Wizard Qt
# ══════════════════════════════════════════════════════════════════════════════

class IntegrationWizard:
    """
    Wizard d'intégration en 4 pages :
      Page 0 — Analyse du Body/Part (info + alerte VarSet)
      Page 1 — Classement (famille, variante, référence, nom FCStd)
      Page 2 — Placement (réinitialiser origine / orientation)
      Page 3 — Confirmation et sauvegarde
    """

    PAGE_ANALYSIS  = 0
    PAGE_CLASS     = 1
    PAGE_PLACEMENT = 2
    PAGE_CONFIRM   = 3

    def __init__(self, analysis):
        self.analysis          = analysis
        self._family           = ''
        self._variant          = ''
        self._ref              = ''
        self._fcstd            = ''
        self._reset_position   = False
        self._reset_orientation = False
        self._saved            = False

    # ── Construction UI ──────────────────────────────────────────────────────

    def _build(self):
        QtWidgets, QtCore = _get_qt()

        self.dlg = QtWidgets.QDialog()
        self.dlg.setWindowTitle(_t('integration_title',
                                   default='Intégrer un composant standard'))
        self.dlg.setMinimumWidth(540)

        root = QtWidgets.QVBoxLayout(self.dlg)

        self.lbl_step = QtWidgets.QLabel()
        font = self.lbl_step.font()
        font.setBold(True)
        self.lbl_step.setFont(font)
        root.addWidget(self.lbl_step)

        self.stack = QtWidgets.QStackedWidget()
        root.addWidget(self.stack)
        self.stack.addWidget(self._build_page_analysis(QtWidgets))
        self.stack.addWidget(self._build_page_class(QtWidgets))
        self.stack.addWidget(self._build_page_placement(QtWidgets))
        self.stack.addWidget(self._build_page_confirm(QtWidgets))

        nav = QtWidgets.QHBoxLayout()
        self.btn_back   = QtWidgets.QPushButton(_t('btn_back',   default='← Retour'))
        self.btn_next   = QtWidgets.QPushButton(_t('btn_next',   default='Suivant →'))
        self.btn_save   = QtWidgets.QPushButton(_t('btn_save',   default='✔ Sauvegarder'))
        self.btn_cancel = QtWidgets.QPushButton(_t('btn_cancel', default='Annuler'))
        self.btn_save.setVisible(False)
        nav.addWidget(self.btn_cancel)
        nav.addStretch()
        nav.addWidget(self.btn_back)
        nav.addWidget(self.btn_next)
        nav.addWidget(self.btn_save)
        root.addLayout(nav)

        self.btn_back.clicked.connect(self._go_back)
        self.btn_next.clicked.connect(self._go_next)
        self.btn_save.clicked.connect(self._save)
        self.btn_cancel.clicked.connect(self.dlg.reject)

        self._update_nav()

    def _build_page_analysis(self, QtWidgets):
        """Page 0 : résumé du Body/Part analysé."""
        w   = QtWidgets.QWidget()
        lay = QtWidgets.QVBoxLayout(w)

        grp = QtWidgets.QGroupBox(_t('integration_analysis',
                                     default='Composant détecté'))
        gl  = QtWidgets.QFormLayout(grp)
        gl.addRow(_t('lbl_name', default='Nom :'),
                  QtWidgets.QLabel(self.analysis['label']))
        gl.addRow(_t('lbl_type', default='Type :'),
                  QtWidgets.QLabel(self.analysis['obj'].TypeId))
        gl.addRow(_t('lbl_doc',  default='Document :'),
                  QtWidgets.QLabel(self.analysis['doc_name']))

        vs_text = (_t('varset_found',   default='✔ VarSet détecté')
                   if self.analysis['has_varset']
                   else _t('varset_missing', default='⚠ Pas de VarSet'))
        if self.analysis['has_varset']:
            vs_text += f"  ({self.analysis['varset'].Label})"
        lbl_vs = QtWidgets.QLabel(vs_text)
        if not self.analysis['has_varset']:
            lbl_vs.setStyleSheet('color: orange; font-weight: bold;')
        gl.addRow(_t('lbl_varset', default='VarSet :'), lbl_vs)
        lay.addWidget(grp)

        if self.analysis['params']:
            grp2 = QtWidgets.QGroupBox(
                _t('integration_params', default='Paramètres détectés'))
            gl2  = QtWidgets.QFormLayout(grp2)
            for k, v in self.analysis['params'].items():
                val = v.Value if hasattr(v, 'Value') else v
                gl2.addRow(f'{k} :', QtWidgets.QLabel(str(val)))
            lay.addWidget(grp2)
        else:
            lay.addWidget(QtWidgets.QLabel(
                _t('no_params', default='Aucun paramètre VarSet trouvé.')))

        if not self.analysis['has_varset']:
            lbl_warn = QtWidgets.QLabel(
                _t('integration_warn_no_varset',
                   default='⚠ Ce composant n\'a pas de VarSet.\n'
                           'Il sera intégré comme forme figée (non paramétrique).\n'
                           'Vous pourrez ajouter un VarSet plus tard dans FreeCAD.'))
            lbl_warn.setWordWrap(True)
            lbl_warn.setStyleSheet(
                'background:#fff3cd;color:#856404;padding:8px;border-radius:4px;')
            lay.addWidget(lbl_warn)

        lay.addStretch()
        return w

    def _build_page_class(self, QtWidgets):
        """Page 1 : Famille / Type / Variante / nom FCStd avec cascades."""
        w   = QtWidgets.QWidget()
        lay = QtWidgets.QFormLayout(w)

        # Famille — combo éditable, familles existantes + nouvelle possible
        self.cmb_family = QtWidgets.QComboBox()
        self.cmb_family.setEditable(True)
        self.cmb_family.setInsertPolicy(QtWidgets.QComboBox.NoInsert)
        self.cmb_family.setPlaceholderText(
            _t('ph_family', default='Sélectionner ou saisir une nouvelle famille...'))
        self._populate_families()
        lay.addRow(_t('lbl_family', default='Famille :'), self.cmb_family)

        # Type — combo éditable, alimenté depuis le JSON de la famille
        self.cmb_type = QtWidgets.QComboBox()
        self.cmb_type.setEditable(True)
        self.cmb_type.setInsertPolicy(QtWidgets.QComboBox.NoInsert)
        self.cmb_type.setPlaceholderText(
            _t('ph_type', default='Sélectionner ou saisir un nouveau type...'))
        lay.addRow(_t('lbl_type', default='Type :'), self.cmb_type)

        # Variante — combo éditable, alimentée depuis le JSON famille/type
        self.cmb_ref = QtWidgets.QComboBox()
        self.cmb_ref.setEditable(True)
        self.cmb_ref.setInsertPolicy(QtWidgets.QComboBox.NoInsert)
        self.cmb_ref.setPlaceholderText(
            _t('ph_ref', default='Sélectionner ou saisir une nouvelle variante...'))
        # Pré-remplir avec le label du composant
        self.cmb_ref.addItem(self.analysis['label'])
        lay.addRow(_t('lbl_ref', default='Variante :'), self.cmb_ref)

        # Nom FCStd
        self.edt_fcstd = QtWidgets.QLineEdit()
        self._update_fcstd_suggestion()
        lay.addRow(_t('lbl_fcstd', default='Nom FCStd :'), self.edt_fcstd)

        # Cascades
        self.cmb_family.currentTextChanged.connect(self._on_family_changed)
        self.cmb_type.currentTextChanged.connect(self._on_type_changed)
        self.cmb_family.currentTextChanged.connect(self._update_fcstd_suggestion)

        # Déclencher la cascade initiale si une famille est déjà sélectionnée
        if self.cmb_family.currentText():
            self._on_family_changed(self.cmb_family.currentText())

        return w

    def _build_page_placement(self, QtWidgets):
        """
        Page 2 : placement actuel + options de réinitialisation.
        Le composant est possiblement issu d'un assemblage et possède
        un placement non nul. L'utilisateur peut choisir de le réinitialiser
        avant l'export en standard.
        """
        w   = QtWidgets.QWidget()
        lay = QtWidgets.QVBoxLayout(w)

        # Affichage placement actuel
        pl  = self.analysis['placement']
        b   = pl.Base
        rpy = pl.Rotation.toEuler()   # (yaw, pitch, roll) en degrés

        grp = QtWidgets.QGroupBox(
            _t('placement_current', default='Placement actuel dans le document'))
        gl  = QtWidgets.QFormLayout(grp)
        gl.addRow('X :', QtWidgets.QLabel(f'{b.x:.3f} mm'))
        gl.addRow('Y :', QtWidgets.QLabel(f'{b.y:.3f} mm'))
        gl.addRow('Z :', QtWidgets.QLabel(f'{b.z:.3f} mm'))
        gl.addRow(_t('lbl_yaw',   default='Yaw :'),
                  QtWidgets.QLabel(f'{rpy[0]:.2f}°'))
        gl.addRow(_t('lbl_pitch', default='Pitch :'),
                  QtWidgets.QLabel(f'{rpy[1]:.2f}°'))
        gl.addRow(_t('lbl_roll',  default='Roll :'),
                  QtWidgets.QLabel(f'{rpy[2]:.2f}°'))
        lay.addWidget(grp)

        # Options de réinitialisation
        grp2 = QtWidgets.QGroupBox(
            _t('placement_reset_title',
               default='Réinitialiser dans le FCStd standard'))
        gl2  = QtWidgets.QVBoxLayout(grp2)

        self.chk_pos = QtWidgets.QCheckBox(
            _t('chk_reset_position',
               default='Réinitialiser la position à l\'origine (0, 0, 0)'))
        self.chk_rot = QtWidgets.QCheckBox(
            _t('chk_reset_orientation',
               default='Réinitialiser l\'orientation (rotation = identité)'))

        # Pré-cocher si le placement est non nul
        if b.Length > 0.01:
            self.chk_pos.setChecked(True)
        if abs(rpy[0]) > 0.01 or abs(rpy[1]) > 0.01 or abs(rpy[2]) > 0.01:
            self.chk_rot.setChecked(True)

        gl2.addWidget(self.chk_pos)
        gl2.addWidget(self.chk_rot)

        lbl_note = QtWidgets.QLabel(
            _t('placement_note',
               default='Note : ces réinitialisations s\'appliquent uniquement\n'
                       'au FCStd exporté. Le document source n\'est pas modifié.'))
        lbl_note.setStyleSheet('color:#555; font-style:italic;')
        lbl_note.setWordWrap(True)
        gl2.addWidget(lbl_note)
        lay.addWidget(grp2)

        lay.addStretch()
        return w

    def _build_page_confirm(self, QtWidgets):
        """Page 3 : aperçu JSON + chemin de destination."""
        w   = QtWidgets.QWidget()
        lay = QtWidgets.QVBoxLayout(w)

        lay.addWidget(QtWidgets.QLabel(
            _t('integration_confirm_info',
               default='Vérifiez les informations avant de sauvegarder :')))

        self.txt_preview = QtWidgets.QTextEdit()
        self.txt_preview.setReadOnly(True)
        self.txt_preview.setFontFamily('Courier New')
        self.txt_preview.setMinimumHeight(180)
        lay.addWidget(self.txt_preview)

        self.lbl_dest = QtWidgets.QLabel()
        self.lbl_dest.setWordWrap(True)
        self.lbl_dest.setStyleSheet('color:#555; font-style:italic;')
        lay.addWidget(self.lbl_dest)

        # Résumé des réinitialisations choisies
        self.lbl_placement_summary = QtWidgets.QLabel()
        self.lbl_placement_summary.setStyleSheet('color:#2a6496;')
        lay.addWidget(self.lbl_placement_summary)

        lay.addStretch()
        return w

    # ── Helpers ──────────────────────────────────────────────────────────────

    def _populate_families(self):
        """Remplit le combo avec les familles existantes + option nouvelle."""
        self.cmb_family.clear()
        self.cmb_family.addItem(_t('new_family', default='+ Nouvelle famille'))
        if not os.path.isdir(STANDARDS_DIR):
            return
        for entry in sorted(os.listdir(STANDARDS_DIR)):
            if os.path.isdir(os.path.join(STANDARDS_DIR, entry)):
                self.cmb_family.addItem(entry)

    # Structure catalogue :
    #   Famille  = répertoire dans Standards/ (contient config.json)
    #   Type     = clé famille dans config.json (ex: "Profilés aluminium")
    #   Variante = clé variante sous le type   (ex: "standard", "DIN471")

    def _load_comp_config(self, family):
        """Délègue à catalog.load_comp_config (fonction mutualisée)."""
        return load_comp_config(family)

    def _get_types_for_family(self, family):
        """Retourne les Types (clés famille dans config.json)."""
        cfg = self._load_comp_config(family)
        return sorted(k for k in cfg.keys() if not k.startswith('_'))

    def _get_variants_for_type(self, family, type_name):
        """Retourne les Variantes (clés variante sous le Type)."""
        cfg       = self._load_comp_config(family)
        type_data = cfg.get(type_name, {})
        if isinstance(type_data, dict):
            return sorted(k for k in type_data.keys()
                          if not k.startswith('_'))
        return []

    def _on_family_changed(self, family):
        """Cascade : recharge les Types depuis config.json de la famille."""
        family = family.strip()
        if family == _t('new_family', default='+ Nouvelle famille'):
            self.cmb_type.clear()
            self.cmb_ref.clear()
            return
        types = self._get_types_for_family(family)
        self.cmb_type.clear()
        self.cmb_type.addItem(_t('new_type', default='+ Nouveau type'))
        for t in types:
            self.cmb_type.addItem(t)

    def _on_type_changed(self, type_val):
        """Cascade : recharge les Variantes depuis config.json famille/type."""
        family   = self.cmb_family.currentText().strip()
        type_val = type_val.strip()
        label    = self.analysis['label']
        self.cmb_ref.clear()
        self.cmb_ref.addItem(_t('new_variant', default='+ Nouvelle variante'))
        if type_val and type_val != _t('new_type', default='+ Nouveau type'):
            for v in self._get_variants_for_type(family, type_val):
                self.cmb_ref.addItem(v)
        # Toujours proposer le label du Body/Part (pré-sélectionné)
        idx = self.cmb_ref.findText(label)
        if idx >= 0:
            self.cmb_ref.setCurrentIndex(idx)
        else:
            self.cmb_ref.insertItem(1, label)   # après "+ Nouvelle variante"
            self.cmb_ref.setCurrentIndex(1)

    def _update_fcstd_suggestion(self):
        fam   = self.cmb_family.currentText().strip()
        label = self.analysis['label']
        safe_label = label.replace(' ', '_').replace('/', '-')
        safe_fam   = fam.replace(' ', '_') if fam else 'MaFamille'
        # Proposition : Label_du_Body_NomFamille.FCStd
        self.edt_fcstd.setPlaceholderText(f'{safe_label}_{safe_fam}.FCStd')

    def _collect_inputs(self):
        def _clean(val, prefix_key):
            v = val.strip()
            p = _t(prefix_key, default='+ ')
            return '' if v == p or v.startswith('+ ') and len(v) < 25 else v
        self._family  = _clean(self.cmb_family.currentText(),
                               'new_family')
        self._variant = _clean(self.cmb_type.currentText(),
                               'new_type')
        self._ref     = _clean(self.cmb_ref.currentText(),
                               'new_variant')
        fcstd_val     = self.edt_fcstd.text().strip() or \
                        self.edt_fcstd.placeholderText()
        if not fcstd_val.endswith('.FCStd'):
            fcstd_val += '.FCStd'
        self._fcstd = fcstd_val

    def _collect_placement(self):
        self._reset_position    = self.chk_pos.isChecked()
        self._reset_orientation = self.chk_rot.isChecked()

    def _update_confirm_preview(self):
        self._collect_inputs()
        self._collect_placement()

        entry   = _generate_json_entry(
            self._ref, self.analysis['params'], self._fcstd)
        preview = {self._family: {self._variant: entry}}
        self.txt_preview.setPlainText(
            json.dumps(preview, ensure_ascii=False, indent=2))

        dest_dir   = os.path.join(STANDARDS_DIR, self._family)
        json_path  = os.path.join(dest_dir, f'{self._family}.json')
        fcstd_path = os.path.join(dest_dir, 'models', self._fcstd)
        self.lbl_dest.setText(f'JSON  : {json_path}\nFCStd : {fcstd_path}')

        # Résumé placement
        parts = []
        if self._reset_position:
            parts.append(_t('reset_pos_applied',
                            default='Position réinitialisée à (0,0,0)'))
        if self._reset_orientation:
            parts.append(_t('reset_rot_applied',
                            default='Orientation réinitialisée'))
        self.lbl_placement_summary.setText(
            '  •  '.join(parts) if parts
            else _t('placement_kept', default='Placement conservé tel quel'))

    # ── Navigation ───────────────────────────────────────────────────────────

    def _current_page(self):
        return self.stack.currentIndex()

    def _go_back(self):
        page = self._current_page()
        if page > 0:
            self.stack.setCurrentIndex(page - 1)
            self._update_nav()

    def _go_next(self):
        page = self._current_page()
        if not self._validate(page):
            return
        if page == self.PAGE_PLACEMENT:
            self._update_confirm_preview()
        self.stack.setCurrentIndex(page + 1)
        self._update_nav()

    def _validate(self, page):
        QtWidgets, _ = _get_qt()
        if page == self.PAGE_CLASS:
            self._collect_inputs()
            missing = []
            # rstrip : lbl_family/lbl_type/lbl_ref sont aussi utilisés comme
            # libellés de champ (avec ' :') — ici insérés dans une phrase.
            if not self._family:
                missing.append(_t('lbl_family', default='Famille :').rstrip(' :'))
            if not self._variant:
                missing.append(_t('lbl_type',   default='Type :').rstrip(' :'))
            if not self._ref:
                missing.append(_t('lbl_ref',    default='Variante :').rstrip(' :'))
            if missing:
                QtWidgets.QMessageBox.warning(
                    self.dlg,
                    _t('validation_error', default='Champs manquants'),
                    _t('fill_required',
                       default='Veuillez remplir : ') + ', '.join(missing))
                return False
        return True

    def _update_nav(self):
        page  = self._current_page()
        steps = [
            _t('step_analysis',  default='Étape 1/4 — Analyse du composant'),
            _t('step_class',     default='Étape 2/4 — Classement'),
            _t('step_placement', default='Étape 3/4 — Placement'),
            _t('step_confirm',   default='Étape 4/4 — Confirmation'),
        ]
        self.lbl_step.setText(steps[page])
        self.btn_back.setEnabled(page > 0)
        self.btn_next.setVisible(page < self.PAGE_CONFIRM)
        self.btn_save.setVisible(page == self.PAGE_CONFIRM)

    # ── Sauvegarde ───────────────────────────────────────────────────────────

    def _save(self):
        QtWidgets, _ = _get_qt()
        try:
            dest_dir   = os.path.join(STANDARDS_DIR, self._family)
            fcstd_path = os.path.join(dest_dir, 'models', self._fcstd)
            family_key = self._family
            # Créer les répertoires nécessaires
            for _sub in ('models', 'datas', 'thumbnails'):
                os.makedirs(os.path.join(dest_dir, _sub), exist_ok=True)

            # Sauvegarder nom + doc AVANT _extract_body_to_fcstd
            # (mergeProject peut invalider les références Python du doc source)
            _obj         = self.analysis['obj']
            _obj_name    = _obj.Name
            _src_doc_name = _obj.Document.Name

            # 1. Extraire le Body/Part dans un nouveau FCStd
            _extract_body_to_fcstd(
                _obj, fcstd_path,
                varset=self.analysis['varset'],
                reset_position=self._reset_position,
                reset_orientation=self._reset_orientation)

            # 2. Créer/mettre à jour datas/*.json et config.json
            _write_or_update_json(
                dest_dir, self._family,
                self._variant, self._ref,
                self._ref, self.analysis['params'], self._fcstd)

            # 3. Récupérer une référence fraîche (obj peut être invalidé)
            try:
                fresh_obj = App.getDocument(_src_doc_name).getObject(_obj_name)
            except Exception:
                fresh_obj = None

            # 4. Convertir l'objet original en standard (SC_ properties)
            if fresh_obj is not None:
                _apply_sc_properties(
                    fresh_obj,
                    family_key, self._family, self._variant, self._ref)
            else:
                _dbg(f'[integration] avertissement : objet source introuvable '
                      f'après extraction, SC_ non appliqué')

            self._saved = True
            QtWidgets.QMessageBox.information(
                self.dlg,
                _t('integration_success_title', default='Composant intégré'),
                _t('integration_success_msg',
                   default='Composant sauvegardé dans Standards/.\n'
                           'Le modèle original a été marqué comme standard (SC_).\n'
                           'Vous pouvez l\'affiner dans l\'éditeur de catalogues.'))
            self.dlg.accept()

        except Exception as e:
            import traceback as _tb
            QtWidgets.QMessageBox.critical(
                self.dlg,
                _t('integration_error_title', default='Erreur'),
                str(e))
            _dbg(f'[integration] erreur sauvegarde : {e}')
            print(_tb.format_exc())

    # ── Lancement ────────────────────────────────────────────────────────────

    def run(self):
        self._build()
        self.dlg.exec_()
        return self._saved


# ══════════════════════════════════════════════════════════════════════════════
# Point d'entrée
# ══════════════════════════════════════════════════════════════════════════════

def open_integration_wizard():
    """
    Détecte le Body/Part sélectionné, pose la question préliminaire,
    puis ouvre le wizard si l'utilisateur confirme.
    Retourne True si un composant a été intégré, False sinon.
    """
    QtWidgets, _ = _get_qt()

    obj = _detect_non_sc_body()
    if obj is None:
        return False

    rep = QtWidgets.QMessageBox.question(
        None,
        _t('integration_ask_title', default='Intégrer comme standard ?'),
        _t('integration_ask_msg', label=obj.Label,
           default='Le composant "{label}" n\'est pas un standard enregistré.\n\n'
                   'Souhaitez-vous le sauvegarder en tant que standard privé ?'),
        QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
        QtWidgets.QMessageBox.No)

    if rep != QtWidgets.QMessageBox.Yes:
        return False

    analysis = _analyze_body(obj)
    wizard   = IntegrationWizard(analysis)
    return wizard.run()
