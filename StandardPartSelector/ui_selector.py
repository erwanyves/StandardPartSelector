# -*- coding: utf-8 -*-
"""
StandardPartSelector.ui_selector — Interface de sélection des composants.
"""
import os
import json
import FreeCAD as App
from config import MACRO_DIR, _t, _get_qt, _UI, _get_library_path, _dbg
import category_labels
from catalog import discover_components, load_catalog
from geometry import _highlight
from sc_props import (_find_varset_in_object, _update_sc_varset,
                       _find_matching_ref, _make_auto_ref_name,
                       _handle_sc_selection, _open_editor_for_new_ref)
from sync import _sync_library_to_sc_dir
from update import _scan_sc_components, _show_update_dialog, _apply_bulk_update
from insertion import generate_component, _apply_placement, _show_orientation_dialog

# Colonne « Préféré » ajoutée en TOUTE PREMIÈRE position du tableau, en
# lecture seule — même libellé littéral que FAVORI_LABEL dans
# editor_dialog.py (l'éditeur). Champ dédié '_favori' (booléen) dans le
# JSON, jamais une colonne configurable : absente de vcfg['columns'] et de
# _legende, donc ajoutée manuellement ici plutôt que via _resolve_col_labels.
FAVORI_COL_LABEL = 'Préféré'
FAVORI_COL_KEY   = '_favori'

# Fond de mise en évidence des lignes préférées : ambre semi-transparent
# (alpha), pour rester lisible quel que soit le thème Qt/FreeCAD actif
# (clair ou sombre) — la transparence se fond avec la couleur de fond
# réelle de la cellule plutôt que d'imposer une teinte fixe qui jurerait
# dans l'autre thème. Même couleur que editor_dialog.py (FAVORI_HIGHLIGHT_
# RGBA), pour une cohérence visuelle éditeur/sélecteur.
FAVORI_HIGHLIGHT_RGBA = (255, 193, 7, 60)

def _apply_favori_highlight(item, is_fav, QtGui):
    """Applique (ou retire) le gras + fond ambré d'un item selon '_favori'."""
    font = item.font()
    if font.bold() != is_fav:
        font.setBold(is_fav)
        item.setFont(font)
    if is_fav:
        item.setBackground(QtGui.QBrush(QtGui.QColor(*FAVORI_HIGHLIGHT_RGBA)))
    else:
        item.setBackground(QtGui.QBrush())   # fond par défaut du thème

def _get_thumbnail_path(comp_dir, fcstd_path):
    """
    Retourne le chemin du PNG de miniature correspondant au FCStd.
    Cherche dans comp_dir/thumbnails/ ET dans la bibliothèque externe.
    Si le PNG n'existe pas encore, tente de l'extraire du FCStd (archive ZIP).
    """
    # Chercher d'abord en local (STANDARDS_DIR), ensuite en bibliothèque
    # uniquement si le répertoire local n'existe pas
    lib = _get_library_path()
    if lib:
        comp_name    = os.path.basename(comp_dir)
        lib_comp_dir = os.path.join(lib, comp_name)
        # N'utiliser la bibliothèque que si comp_dir n'existe PAS localement
        if not os.path.isdir(comp_dir) and os.path.isdir(lib_comp_dir):
            comp_dir = lib_comp_dir
    _dbg(f'[thumbnail] fcstd_path={fcstd_path!r}')
    _dbg(f'[thumbnail] comp_dir={comp_dir!r}')
    if not fcstd_path or not os.path.exists(fcstd_path):
        return None
    stem      = os.path.splitext(os.path.basename(fcstd_path))[0]
    thumb_dir = os.path.join(comp_dir, 'thumbnails')
    thumb_png = os.path.join(thumb_dir, stem + '.png')
    _dbg(f'[thumbnail] thumb_png={thumb_png!r} exists={os.path.exists(thumb_png)}')
    if os.path.exists(thumb_png):
        if os.path.getsize(thumb_png) > 0:
            return thumb_png
        os.remove(thumb_png)  # vide → réextraire
    # Extraction depuis le FCStd (fichier ZIP)
    try:
        import zipfile
        with zipfile.ZipFile(fcstd_path, 'r') as z:
            all_files = z.namelist()
            _dbg(f'[thumbnail] Contenu ZIP : {all_files[:10]}')
            candidates = [n for n in all_files
                          if n.lower().endswith('.png')
                          and 'thumbnail' in n.lower()]
            if not candidates:
                candidates = [n for n in all_files if n.lower().endswith('.png')]
            _dbg(f'[thumbnail] Candidats PNG : {candidates}')
            if candidates:
                os.makedirs(thumb_dir, exist_ok=True)
                data = z.read(candidates[0])
                with open(thumb_png, 'wb') as f:
                    f.write(data)
                print(f"[StandardPartSelector] Vignette extraite : {stem}.png")
                return thumb_png
    except Exception as e:
        print(f"[thumbnail] {os.path.basename(fcstd_path)}: {e}")
    return None


def _update_thumbnail(lbl, thumb_path, QtGui, QtCore, max_w=_UI['thumb_max_w'], max_h=_UI['thumb_max_h']):
    """Met à jour le QLabel miniature avec l'image donnée."""
    if thumb_path and os.path.exists(thumb_path):
        # Normaliser les séparateurs (Windows mixte \ et /)
        thumb_path = thumb_path.replace('\\', '/').replace('\\\\', '/')
        pix = QtGui.QPixmap(thumb_path)
        if not pix.isNull():
            pix = pix.scaled(max_w, max_h,
                             QtCore.Qt.KeepAspectRatio,
                             QtCore.Qt.SmoothTransformation)
            lbl.setPixmap(pix)
            return
    lbl.clear()


# ============================================================
# Interface
# ============================================================
def _get_qt():
    try:
        from PySide2 import QtWidgets, QtCore, QtGui
        return QtWidgets, QtCore, QtGui
    except ImportError:
        from PySide import QtWidgets, QtCore, QtGui
        return QtWidgets, QtCore, QtGui




# ============================================================
# Détection de face cylindrique (5B.1)
# ============================================================

def _build_ui_maps(components, QtWidgets, QtCore, dlg, replace_fcstd=None):
    """
    Construit les onglets famille/variante et les tableaux pour chaque composant.
    Retourne (component_stack, component_combo, thumb_label, ui_state).
    ui_state regroupe tous les widgets et maps nécessaires aux autres fonctions.
    """
    _, _, QtGui = _get_qt()
    component_stack = QtWidgets.QStackedWidget()

    # Miniature (à droite du stack)
    thumb_label = QtWidgets.QLabel()
    thumb_label.setFixedWidth(_UI['thumb_w'])
    thumb_label.setMinimumHeight(_UI['thumb_h'])
    thumb_label.setAlignment(QtCore.Qt.AlignCenter)
    thumb_label.setStyleSheet('background: transparent;')

    # Combo composant
    component_combo = QtWidgets.QComboBox()
    component_combo.setMinimumWidth(_UI['combo_min_w'])

    # Maps de navigation et de données
    component_keys  = []
    family_tabs_map  = {}   # comp_key → QTabWidget
    variant_tabs_map = {}   # comp_key → { family_key → QTabWidget }
    tables_map       = {}   # (comp_key, family_key, variant_key) → QTableWidget
    catalogs_map     = {}   # (comp_key, family_key, variant_key) → catalog
    family_keys_map  = {}   # comp_key → [family_key, ...]
    variant_keys_map = {}   # (comp_key, family_key) → [variant_key, ...]
    cfgs_map         = {}   # (comp_key, family_key, variant_key) → cfg

    # Normaliser replace_fcstd pour comparaison robuste
    import os as _os
    _rfcstd = _os.path.normcase(_os.path.normpath(replace_fcstd)) \
              if replace_fcstd else None
    for comp_key, comp_data in components.items():
        if _rfcstd and not any(
                _os.path.normcase(_os.path.normpath(vcfg.get('fcstd', ''))) == _rfcstd
                for fam in comp_data['cfg'].values()
                for vcfg in fam.values()):
            continue
        component_keys.append(comp_key)
        component_combo.addItem(_t.component(comp_key))

        cfg = comp_data['cfg']
        family_keys_map[comp_key]  = []
        variant_tabs_map[comp_key] = {}

        comp_widget = QtWidgets.QWidget()
        comp_layout = QtWidgets.QVBoxLayout()
        family_tabs = QtWidgets.QTabWidget()
        family_tabs_map[comp_key] = family_tabs

        for family, variants in cfg.items():
            if _rfcstd and not any(
                    _os.path.normcase(_os.path.normpath(v.get('fcstd',''))) == _rfcstd
                    for v in variants.values()):
                continue
            family_keys_map[comp_key].append(family)
            variant_keys_map[(comp_key, family)] = []

            family_widget = QtWidgets.QWidget()
            family_layout = QtWidgets.QVBoxLayout()
            variant_tabs  = QtWidgets.QTabWidget()
            variant_tabs_map[comp_key][family] = variant_tabs

            for variant, vcfg in variants.items():
                if _rfcstd and \
                        _os.path.normcase(
                            _os.path.normpath(vcfg.get('fcstd',''))) != _rfcstd:
                    continue
                variant_keys_map[(comp_key, family)].append(variant)

                try:
                    catalog = load_catalog(vcfg['json'])
                except FileNotFoundError as e:
                    print(f'[selector] FICHIER MANQUANT : {vcfg["json"]}')
                    tab = QtWidgets.QWidget()
                    tl  = QtWidgets.QVBoxLayout()
                    tl.addWidget(QtWidgets.QLabel(_t("file_missing") + str(e)))
                    tab.setLayout(tl)
                    variant_tabs.addTab(tab, _t.variant(variant))
                    catalogs_map[(comp_key, family, variant)] = {}
                    tables_map[(comp_key, family, variant)]   = None
                    cfgs_map[(comp_key, family, variant)]     = vcfg
                    continue

                catalogs_map[(comp_key, family, variant)] = catalog
                cfgs_map[(comp_key, family, variant)]     = vcfg
                cols = _resolve_col_labels(
                    vcfg['columns'], vcfg.get('json', ''))

                fav_col = 0   # toujours en TOUTE PREMIÈRE position

                table = QtWidgets.QTableWidget()
                table.setColumnCount(len(cols) + 1)
                table.setHorizontalHeaderLabels(
                    [FAVORI_COL_LABEL] + [_t(c[0]) for c in cols])
                table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
                table.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
                table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
                table.horizontalHeader().setStretchLastSection(False)
                table.verticalHeader().setVisible(False)

                refs = list(catalog.keys())
                table.setRowCount(len(refs))
                for row, ref in enumerate(refs):
                    p = catalog[ref]
                    is_fav = bool(p.get('_favori'))
                    # Colonne Préféré (col. 0) — affichage seul (✓/vide),
                    # non éditable depuis le sélecteur : le statut se
                    # change dans l'éditeur.
                    fav_item = QtWidgets.QTableWidgetItem('✓' if is_fav else '')
                    fav_item.setTextAlignment(QtCore.Qt.AlignCenter)
                    _apply_favori_highlight(fav_item, is_fav, QtGui)
                    table.setItem(row, fav_col, fav_item)
                    for col, _c in enumerate(cols, start=1):
                        col_key = _c[1]
                        if col_key == 'ref':
                            val = ref
                        elif isinstance(p.get(col_key), float):
                            val = f"{p[col_key]:.2f}".rstrip('0').rstrip('.')
                        else:
                            val = str(p.get(col_key, '-'))
                        item = QtWidgets.QTableWidgetItem(val)
                        item.setTextAlignment(QtCore.Qt.AlignCenter)
                        _apply_favori_highlight(item, is_fav, QtGui)
                        table.setItem(row, col, item)

                table.selectRow(0)
                _fit_table_columns(table)
                table.setSortingEnabled(True)
                table.itemDoubleClicked.connect(lambda _, d=dlg: d.accept())
                tables_map[(comp_key, family, variant)] = table

                tab = QtWidgets.QWidget()
                tl  = QtWidgets.QVBoxLayout()
                tl.addWidget(table)
                tab.setLayout(tl)
                variant_tabs.addTab(tab, _t.variant(variant))

            family_layout.addWidget(variant_tabs)
            family_widget.setLayout(family_layout)
            family_tabs.addTab(family_widget, _t.family(family))

        comp_layout.addWidget(family_tabs)
        comp_widget.setLayout(comp_layout)
        component_stack.addWidget(comp_widget)

    ui_state = {
        'component_combo':  component_combo,
        'component_stack':  component_stack,   # sync manuel pendant blockSignals
        'thumb_label':      thumb_label,
        'component_keys':   component_keys,
        'family_tabs_map':  family_tabs_map,
        'variant_tabs_map': variant_tabs_map,
        'tables_map':       tables_map,
        'catalogs_map':     catalogs_map,
        'family_keys_map':  family_keys_map,
        'variant_keys_map': variant_keys_map,
        'cfgs_map':         cfgs_map,
    }
    # Stocker Qt pour _reload_catalogs
    ui_state['_QtWidgets'] = QtWidgets
    ui_state['_QtCore']    = QtCore

    return component_stack, component_combo, thumb_label, ui_state



def _restore_selection_from(ui_state, sel):
    """Positionne l'UI sur la sélection fournie (5B.2)."""
    _restore_ui_silent(ui_state, None, restore_from_param=False, sel=sel)


def _set_signals_blocked(ui_state, blocked):
    """Bloque ou débloque les signaux des widgets de navigation."""
    try:
        ui_state['component_combo'].blockSignals(blocked)
        for ft in ui_state['family_tabs_map'].values():
            ft.blockSignals(blocked)
        for (ck, fam) in ui_state['variant_keys_map']:
            ui_state['variant_tabs_map'][ck][fam].blockSignals(blocked)
    except Exception:
        pass


def _restore_last_selection(ui_state):
    """Repositionne l'UI sur la dernière sélection sauvegardée (3.2)."""
    _restore_ui_silent(ui_state,
                       ui_state['component_combo'].property('_last_ck') or '',
                       restore_from_param=True)


def _restore_ui_silent(ui_state, _, restore_from_param=False, sel=None):
    """
    Repositionne l'UI sans déclencher les signaux de filtrage.
    restore_from_param=True : lit depuis App.ParamGet
    sel : dict {'comp_key', 'family', 'variant', 'ref'}
    """
    try:
        if restore_from_param:
            param    = App.ParamGet('User parameter:Plugins/StandardPartSelector')
            last_ck  = param.GetString('LastComponent', '')
            last_fam = param.GetString('LastFamily',    '')
            last_var = param.GetString('LastVariant',   '')
            last_ref = param.GetString('LastRef',       '')
        else:
            last_ck  = (sel or {}).get('comp_key', '')
            last_fam = (sel or {}).get('family',   '')
            last_var = (sel or {}).get('variant',  '')
            last_ref = (sel or {}).get('ref',      '')

        ck_list = ui_state['component_keys']
        if last_ck not in ck_list:
            return

        # Bloquer tous les signaux pendant le repositionnement
        _set_signals_blocked(ui_state, True)
        try:
            ci = ck_list.index(last_ck)
            ui_state['component_combo'].setCurrentIndex(ci)
            # Synchroniser le stack manuellement (signal currentIndexChanged bloqué)
            if 'component_stack' in ui_state:
                ui_state['component_stack'].setCurrentIndex(ci)
            fkeys = ui_state['family_keys_map'].get(last_ck, [])
            if last_fam not in fkeys:
                return
            ui_state['family_tabs_map'][last_ck].setCurrentIndex(
                fkeys.index(last_fam))
            vkeys = ui_state['variant_keys_map'].get((last_ck, last_fam), [])
            if last_var not in vkeys:
                return
            ui_state['variant_tabs_map'][last_ck][last_fam].setCurrentIndex(
                vkeys.index(last_var))
            table = ui_state['tables_map'].get((last_ck, last_fam, last_var))
            if table is None or not last_ref:
                # Table manquante (données JSON absentes) ou pas de ref :
                # combo/famille/variante déjà positionnés — on s'arrête là
                return
            cfg_r     = ui_state['cfgs_map'][(last_ck, last_fam, last_var)]
            ref_col_r = _display_ref_col(cfg_r)
            for r in range(table.rowCount()):
                item = table.item(r, ref_col_r)
                if item and item.text() == last_ref:
                    table.selectRow(r)
                    table.scrollTo(table.model().index(r, 0))
                    break
        finally:
            _set_signals_blocked(ui_state, False)
    except Exception as e:
        print(f'[StandardPartSelector] Restauration sélection ignorée: {e}')


def _save_window_size(dlg):
    """Sauvegarde la taille de la fenêtre dans les préférences FreeCAD (3.1)."""
    try:
        _p = App.ParamGet('User parameter:Plugins/StandardPartSelector')
        _p.SetInt('WindowWidth',  dlg.width())
        _p.SetInt('WindowHeight', dlg.height())
    except Exception:
        pass


def _get_selection(ui_state):
    """Lit la sélection courante dans l'UI et retourne le tuple résultat."""
    combo   = ui_state['component_combo']
    ck_list = ui_state['component_keys']
    ci = combo.currentIndex()
    if ci < 0 or ci >= len(ck_list): return None
    comp_key = ck_list[ci]

    ft    = ui_state['family_tabs_map'].get(comp_key)
    fkeys = ui_state['family_keys_map'].get(comp_key, [])
    fi    = ft.currentIndex() if ft else -1
    if fi < 0 or fi >= len(fkeys): return None
    family = fkeys[fi]

    vt    = ui_state['variant_tabs_map'][comp_key].get(family)
    vkeys = ui_state['variant_keys_map'].get((comp_key, family), [])
    vi    = vt.currentIndex() if vt else -1
    if vi < 0 or vi >= len(vkeys): return None
    variant = vkeys[vi]

    table = ui_state['tables_map'].get((comp_key, family, variant))
    if table is None: return None
    row = table.currentRow()
    if row < 0: return None

    cfg     = ui_state['cfgs_map'][(comp_key, family, variant)]
    catalog = ui_state['catalogs_map'][(comp_key, family, variant)]
    ref_col = _display_ref_col(cfg)
    ref_item = table.item(row, ref_col) if ref_col >= 0 else None
    if ref_item is None: return None
    ref = ref_item.text()
    if ref not in catalog: return None
    return comp_key, family, variant, ref, catalog[ref], cfg


# ============================================================
# Interface — orchestrateur (4.1)
# ============================================================



def _resolve_col_labels(cols, json_path):
    """
    Retourne les colonnes avec libellés depuis _legende du JSON si disponible,
    sinon depuis config.json.
    cols = [(label, key), ...]
    """
    legende = {}
    if json_path and os.path.exists(json_path):
        try:
            with open(json_path, encoding='utf-8') as f:
                legende = json.load(f).get('_legende', {})
        except Exception as _e_legende:
            # Ne pas taire : c'est un except muet ici qui a masqué pendant
            # longtemps un `json` non importé, et donc un _legende jamais lu.
            App.Console.PrintWarning(
                '[selector] _legende illisible dans %s : %s\n'
                % (json_path, _e_legende))
    # Filtrer les colonnes avec show=False (4e élément = False)
    visible = [c for c in cols if not (len(c) > 3 and not c[3])]
    return [(legende.get(col[1], col[0]), col[1]) for col in visible]

def _display_ref_col(cfg):
    """
    Index RÉEL de la colonne Référence dans la table du sélecteur telle
    qu'affichée : après filtrage des colonnes masquées par
    _resolve_col_labels() (même liste, même ordre que celle utilisée pour
    construire la table), puis décalé de +1 pour la colonne Préféré,
    toujours en tête (position 0). Ne JAMAIS chercher 'ref' directement dans
    cfg['columns'] brut — cette liste non filtrée ne correspond plus à la
    position réelle dans la table depuis l'ajout de la colonne Préféré.
    Retourne -1 si la colonne référence est introuvable.
    """
    cols = _resolve_col_labels(cfg.get('columns', []), cfg.get('json', ''))
    idx = next((i for i, c in enumerate(cols) if c[1] == 'ref'), -1)
    return idx + 1 if idx >= 0 else -1

def _fit_table_columns(table):
    """
    Ajuste les colonnes à leur contenu (header + données).
    Centrage différé via resizeEvent pour ne pas bloquer l'affichage.
    """
    hh = table.horizontalHeader()
    hh.setStretchLastSection(False)
    table.resizeColumnsToContents()
    # Padding par colonne
    for ci in range(table.columnCount()):
        table.setColumnWidth(ci, table.columnWidth(ci) + 16)

    # Centrage différé : connecter au resizeEvent pour avoir
    # la vraie largeur disponible une fois le widget affiché
    def _center_cols(event=None):
        try:
            total_w = sum(table.columnWidth(ci)
                          for ci in range(table.columnCount()))
            avail_w = table.viewport().width()
            if avail_w > 0 and total_w < avail_w:
                margin = (avail_w - total_w) // 2
                table.setContentsMargins(margin, 0, margin, 0)
            else:
                table.setContentsMargins(0, 0, 0, 0)
        except RuntimeError:
            pass  # Widget Qt déjà détruit — ignorer

    # Appliquer immédiatement si déjà visible, sinon différer
    _center_cols()
    try:
        from PySide2.QtCore import QTimer
    except ImportError:
        from PySide.QtCore import QTimer
    QTimer.singleShot(50, _center_cols)

def _reload_catalogs(ui_state, components):
    """Recharge colonnes + données JSON dans les tableaux existants."""
    QtWidgets = ui_state.get('_QtWidgets')
    QtCore    = ui_state.get('_QtCore')
    _, _, QtGui = _get_qt()
    tables_map = ui_state.get('tables_map', {})
    for (ck, fam, var), table in tables_map.items():
        if table is None: continue
        vcfg  = components.get(ck, {}).get('cfg', {}).get(fam, {}).get(var, {})
        if not vcfg: continue
        # Mettre à jour cfgs_map avec la config fraîche
        ui_state['cfgs_map'][(ck, fam, var)] = vcfg
        jpath = vcfg.get('json', '')
        if not jpath or not os.path.exists(jpath):
            continue
        # Désactiver le tri le temps du repeuplement ligne par ligne : avec
        # setSortingEnabled(True) resté actif depuis la construction
        # initiale, écrire une cellule (ex : la Référence, souvent la
        # colonne triée) peut déclencher un re-tri IMMÉDIAT de la ligne —
        # les colonnes suivantes de la même itération (table.item(row, col))
        # pointent alors sur une ligne différente de celle visée, ce qui
        # mélange/efface silencieusement des références entre les lignes.
        # C'est le comportement documenté de QTableWidget : toujours
        # désactiver le tri avant un peuplement par setItem/setText, le
        # réactiver après (cf. _build_ui_maps qui ne l'active QU'APRÈS
        # avoir rempli toutes les lignes, à la construction initiale).
        was_sorting = table.isSortingEnabled()
        table.setSortingEnabled(False)
        try:
            catalog = load_catalog(jpath)
            cols = _resolve_col_labels(
                vcfg.get('columns', []), jpath)
            fav_col = 0   # toujours en TOUTE PREMIÈRE position
            # Reconstruire colonnes + données
            table.setColumnCount(len(cols) + 1)
            table.setHorizontalHeaderLabels(
                [FAVORI_COL_LABEL] + [_t(c[0]) for c in cols])
            refs = list(catalog.keys())
            table.setRowCount(len(refs))
            for row, ref in enumerate(refs):
                p = catalog[ref]
                favori = bool(p.get('_favori'))
                # Colonne Préféré (col. 0, affichage seul ✓/vide)
                fav_val  = '✓' if favori else ''
                fav_item = table.item(row, fav_col)
                if fav_item is None:
                    if QtWidgets:
                        fav_item = QtWidgets.QTableWidgetItem(fav_val)
                        if QtCore:
                            fav_item.setTextAlignment(QtCore.Qt.AlignCenter)
                        table.setItem(row, fav_col, fav_item)
                else:
                    fav_item.setText(fav_val)
                if fav_item is not None:
                    _apply_favori_highlight(fav_item, favori, QtGui)
                for col, _c in enumerate(cols, start=1):
                    col_key = _c[1]
                    if col_key == 'ref':
                        val = ref
                    elif isinstance(p.get(col_key), float):
                        val = f"{p[col_key]:.2f}".rstrip('0').rstrip('.')
                    else:
                        val = str(p.get(col_key, '-'))
                    item = table.item(row, col)
                    if item is None:
                        if QtWidgets:
                            item = QtWidgets.QTableWidgetItem(val)
                            if QtCore:
                                item.setTextAlignment(QtCore.Qt.AlignCenter)
                            table.setItem(row, col, item)
                    else:
                        item.setText(val)
                    if item is not None:
                        # Préféré : gras + fond ambré si coché dans le
                        # catalogue, sinon normal — recalculé à chaque
                        # rechargement (l'état a pu changer entre-temps).
                        _apply_favori_highlight(item, favori, QtGui)
            _fit_table_columns(table)
        except Exception as e:
            print(f'[reload] {ck}/{fam}/{var}: {e}')
        finally:
            table.setSortingEnabled(was_sorting)


def _rebuild_ui(components, component_stack, component_combo,
                ui_state, QtWidgets, QtCore, dlg,
                _reapply_filter, _refresh_thumb, replace_fcstd=None):
    """Reconstruit entièrement le stack et le combo composant."""
    try:
        # Sauvegarder la sélection courante
        ci_old  = component_combo.currentIndex()
        ck_old  = ui_state['component_keys'][ci_old]                   if 0 <= ci_old < len(ui_state['component_keys']) else ''

        # Vider le stack
        while component_stack.count():
            w = component_stack.widget(0)
            component_stack.removeWidget(w)
            w.deleteLater()

        # Reconstruire
        _build_ui_maps(components, QtWidgets, QtCore, dlg, replace_fcstd=replace_fcstd)

        # Re-connecter les signaux
        component_combo.blockSignals(True)
        component_combo.clear()
        for ck in components:
            component_combo.addItem(_t.component(ck))
        component_combo.blockSignals(False)

        # Restaurer la sélection
        ci_new = next((i for i, k in enumerate(components)
                       if k == ck_old), 0)
        component_combo.setCurrentIndex(ci_new)
        component_stack.setCurrentIndex(ci_new)

        _dbg('[reload] UI reconstruite')
    except Exception as e:
        import traceback
        print(f'[rebuild_ui] {e}\n{traceback.format_exc()}')


# ============================================================
# Bannière de contexte (5C)
# ============================================================

def _build_context_banner(layout, ctx, QtWidgets):
    """
    Insère une bannière compacte de contexte dans le layout.
    ctx = {'type', 'icon', 'color', 'title', 'detail'}
    """
    color  = ctx.get('color', '#3B82F6')
    icon   = ctx.get('icon',  '•')
    title  = ctx.get('title', '')
    detail = ctx.get('detail', '')

    # Convertir la couleur hex en composantes pour le fond translucide
    try:
        r = int(color[1:3], 16)
        g = int(color[3:5], 16)
        b = int(color[5:7], 16)
        bg = f'rgba({r},{g},{b},30)'
    except Exception:
        bg = 'rgba(80,130,255,30)'

    frame = QtWidgets.QFrame()
    frame.setFixedHeight(46)
    frame.setStyleSheet(f"""
        QFrame {{
            background: {bg};
            border: none;
            border-left: 4px solid {color};
            border-radius: 3px;
        }}
    """)

    row = QtWidgets.QHBoxLayout(frame)
    row.setContentsMargins(12, 0, 10, 0)
    row.setSpacing(10)

    # Pastille colorée (cercle CSS — rendu fiable sur toutes plateformes)
    dot = QtWidgets.QFrame()
    dot.setFixedSize(10, 10)
    dot.setStyleSheet(f'background: {color}; border-radius: 5px; border: none;')
    row.addWidget(dot)

    # Textes
    txt = QtWidgets.QVBoxLayout()
    txt.setSpacing(0)
    t_lbl = QtWidgets.QLabel(title)
    t_lbl.setStyleSheet('font-weight: bold; font-size: 12px;')
    d_lbl = QtWidgets.QLabel(detail)
    d_lbl.setStyleSheet('font-size: 10px; color: #888;')
    txt.addWidget(t_lbl)
    txt.addWidget(d_lbl)
    row.addLayout(txt)
    row.addStretch()

    frame.setToolTip(
        f"<b>{_t('ctx_tooltip_title')}</b><br><br>"
        f"<b>● {_t('ctx_free_title')}</b><br>"
        f"&nbsp;&nbsp;{_t('ctx_tooltip_free_detail')}<br><br>"
        f"<b>● {_t('ctx_geometry_label')}</b><br>"
        f"&nbsp;&nbsp;{_t('ctx_tooltip_geo_detail')}<br><br>"
        f"<b>● {_t('ctx_replace_label')}</b><br>"
        f"&nbsp;&nbsp;{_t('ctx_tooltip_replace_detail')}<br><br>"
        f"<b>● {_t('ctx_nonsc_label')}</b><br>"
        f"&nbsp;&nbsp;{_t('ctx_tooltip_nonsc_detail')}"
    )
    frame.setToolTipDuration(15000)
    layout.addWidget(frame)


# ============================================================
def select_component(components, prefilter=None, preselect=None, geo_info=None,
                     replace_fcstd=None, context_info=None):
    QtWidgets, QtCore, QtGui = _get_qt()

    # ── Dialogue ──────────────────────────────────────────────
    dlg = QtWidgets.QDialog()
    dlg.setWindowTitle(_t("window_title"))
    dlg.setMinimumWidth(_UI['dlg_min_w'])
    dlg.setMinimumHeight(_UI['dlg_min_h'])
    try:
        _p = App.ParamGet('User parameter:Plugins/StandardPartSelector')
        dlg.resize(_p.GetInt('WindowWidth', _UI['dlg_def_w']),
                   _p.GetInt('WindowHeight', _UI['dlg_def_h']))
    except Exception:
        pass

    # ── Construction de l'UI ──────────────────────────────────
    component_stack, component_combo, thumb_label, ui_state = (
        _build_ui_maps(components, QtWidgets, QtCore, dlg, replace_fcstd=replace_fcstd)
    )
    component_combo.currentIndexChanged.connect(component_stack.setCurrentIndex)
    component_stack.setCurrentIndex(component_combo.currentIndex())  # sync initiale

    main_layout = QtWidgets.QVBoxLayout()

    top_layout = QtWidgets.QHBoxLayout()
    top_layout.addWidget(QtWidgets.QLabel(_t("choose_label")))
    top_layout.addWidget(component_combo)
    top_layout.addStretch()
    main_layout.addLayout(top_layout)

    # ── Bannière de contexte ───────────────────────────────────
    if context_info:
        _build_context_banner(main_layout, context_info, QtWidgets)

    content_layout = QtWidgets.QHBoxLayout()
    content_layout.addWidget(component_stack, stretch=1)

    # ── Panneau vignette + actions ─────────────────────────────
    thumb_panel = QtWidgets.QVBoxLayout()
    thumb_panel.setSpacing(4)
    thumb_panel.addWidget(thumb_label)

    btn_edit_fcstd = QtWidgets.QPushButton(_t("edit_model_btn"))
    btn_edit_fcstd.setToolTip(
        _t("edit_model_tip"))
    btn_edit_fcstd.setFixedWidth(_UI['thumb_w'])

    btn_refresh_thumb = QtWidgets.QPushButton(_t("refresh_thumb_btn"))
    btn_refresh_thumb.setToolTip(
        _t("refresh_thumb_tip"))
    btn_refresh_thumb.setFixedWidth(_UI['thumb_w'])

    thumb_panel.addWidget(btn_edit_fcstd)
    thumb_panel.addWidget(btn_refresh_thumb)
    thumb_panel.addStretch()
    content_layout.addLayout(thumb_panel)

    main_layout.addLayout(content_layout)

    # ── Filtre de recherche (3.3 / 5B.1) ────────────────────
    filter_layout = QtWidgets.QHBoxLayout()
    filter_layout.addWidget(QtWidgets.QLabel(_t('filter_label')))
    filter_edit = QtWidgets.QLineEdit()
    filter_edit.setPlaceholderText(_t('filter_placeholder'))
    filter_edit.setClearButtonEnabled(True)
    filter_layout.addWidget(filter_edit)
    tol_btn = QtWidgets.QPushButton('±')
    tol_btn.setCheckable(True)
    tol_btn.setChecked(bool(prefilter))         # auto-activé si préfiltre face
    tol_btn.setToolTip(_t('filter_tolerance_tip'))
    tol_btn.setFixedWidth(28)
    tol_spin = QtWidgets.QDoubleSpinBox()
    tol_spin.setRange(0.1, 50.0)
    tol_spin.setValue(5.0)
    tol_spin.setSingleStep(0.5)
    tol_spin.setSuffix(' %')
    tol_spin.setFixedWidth(68)
    tol_spin.setToolTip(_t('filter_tolerance_tip'))
    filter_layout.addWidget(tol_btn)
    filter_layout.addWidget(tol_spin)
    main_layout.addLayout(filter_layout)

    def _filter_table(table, text, num_target, ref_col):
        """Applique le filtre sur une table. Retourne l'index de la 1ère ligne visible."""
        first = -1
        for r in range(table.rowCount()):
            if not text:
                match = True
            elif num_target is not None:
                tol    = tol_spin.value() / 100.0
                lo, hi = num_target * (1 - tol), num_target * (1 + tol)
                match  = False
                for c in range(table.columnCount()):
                    if c == ref_col: continue
                    cell = table.item(r, c)
                    if cell is None: continue
                    try:
                        if lo <= float(cell.text().replace(',', '.')) <= hi:
                            match = True; break
                    except ValueError:
                        pass
            else:
                match = any(
                    text in (table.item(r, c).text().lower()
                             if table.item(r, c) else '')
                    for c in range(table.columnCount())
                )
            table.setRowHidden(r, not match)
            if match and first < 0:
                first = r
        return first

    def _apply_filter(text):
        """Filtre TOUTES les tables — persistance cross-onglets."""
        s    = ui_state
        text = text.strip().lower()

        # Mode numérique ±5% si le bouton bascule est actif
        num_target = None
        if text and tol_btn.isChecked():
            try:
                num_target = float(text.replace(',', '.'))
            except ValueError:
                pass

        # Identifier la table active (pour scroll/sélection)
        ci = s['component_combo'].currentIndex()
        active_ck  = s['component_keys'][ci] if 0 <= ci < len(s['component_keys']) else None
        active_fam = None
        active_var = None
        if active_ck:
            ft    = s['family_tabs_map'].get(active_ck)
            fkeys = s['family_keys_map'].get(active_ck, [])
            fi    = ft.currentIndex() if ft else -1
            if 0 <= fi < len(fkeys):
                active_fam = fkeys[fi]
                vt    = s['variant_tabs_map'][active_ck].get(active_fam)
                vkeys = s['variant_keys_map'].get((active_ck, active_fam), [])
                vi    = vt.currentIndex() if vt else -1
                if 0 <= vi < len(vkeys):
                    active_var = vkeys[vi]

        # Filtrer TOUTES les tables de TOUS les composants
        for (ck, fam, var), table in s['tables_map'].items():
            if table is None: continue
            cfg     = s['cfgs_map'].get((ck, fam, var), {})
            ref_col = _display_ref_col(cfg)
            first = _filter_table(table, text, num_target, ref_col)
            # Scroll + sélection sur la table active uniquement
            if ck == active_ck and fam == active_fam and var == active_var:
                if first >= 0:
                    table.selectRow(first)
                    table.scrollTo(table.model().index(first, 0))

    # Ré-appliquer le filtre courant à chaque changement d'onglet
    # (plus de _reset_filter : le filtre persiste sur tous les composants)
    def _reapply_filter(*_): _apply_filter(filter_edit.text())

    filter_edit.textChanged.connect(_apply_filter)
    tol_btn.toggled.connect(_reapply_filter)           # bascule ±
    tol_spin.valueChanged.connect(_reapply_filter)     # valeur tolérance
    component_combo.currentIndexChanged.connect(_reapply_filter)
    for _fft in ui_state['family_tabs_map'].values():
        _fft.currentChanged.connect(_reapply_filter)
    for (_ck, _fam) in ui_state['variant_keys_map']:
        ui_state['variant_tabs_map'][_ck][_fam].currentChanged.connect(_reapply_filter)

    # 5B.1 — pré-remplissage APRÈS connexion : setText déclenche textChanged
    if prefilter:
        filter_edit.setText(prefilter)

    # ── Miniature (3.x) ───────────────────────────────────────
    def _refresh_thumb(*_):
        s     = ui_state
        ci    = s['component_combo'].currentIndex()
        ck_l  = s['component_keys']
        if ci < 0 or ci >= len(ck_l): thumb_label.clear(); return
        ck    = ck_l[ci]
        cdir  = components[ck]['dir']
        fkeys = s['family_keys_map'].get(ck, [])
        ft    = s['family_tabs_map'].get(ck)
        fi    = ft.currentIndex() if ft else -1
        if fi < 0 or fi >= len(fkeys): thumb_label.clear(); return
        fam   = fkeys[fi]
        vkeys = s['variant_keys_map'].get((ck, fam), [])
        vt    = s['variant_tabs_map'][ck].get(fam)
        vi    = vt.currentIndex() if vt else -1
        if vi < 0 or vi >= len(vkeys): thumb_label.clear(); return
        vcfg  = s['cfgs_map'].get((ck, fam, vkeys[vi]), {})
        path  = _get_thumbnail_path(cdir, vcfg.get('fcstd'))
        _update_thumbnail(thumb_label, path, QtGui, QtCore)

    component_combo.currentIndexChanged.connect(_refresh_thumb)
    for _fft in ui_state['family_tabs_map'].values():
        _fft.currentChanged.connect(_refresh_thumb)
    for (_ck, _fam) in ui_state['variant_keys_map']:
        ui_state['variant_tabs_map'][_ck][_fam].currentChanged.connect(_refresh_thumb)
    # _refresh_thumb() initial appelé APRÈS restore pour éviter
    # la désynchronisation vignette/combo à l'ouverture

    # ── Actions vignette ────────────────────────────────────────
    def _get_current_fcstd_and_thumb():
        """Retourne (fcstd_path, thumb_png_path) de la sélection courante."""
        s    = ui_state
        ci   = s['component_combo'].currentIndex()
        ck_l = s['component_keys']
        if ci < 0 or ci >= len(ck_l): return None, None
        ck   = ck_l[ci]
        cdir = components[ck]['dir']
        fkeys = s['family_keys_map'].get(ck, [])
        ft    = s['family_tabs_map'].get(ck)
        fi    = ft.currentIndex() if ft else -1
        if fi < 0 or fi >= len(fkeys): return None, None
        fam  = fkeys[fi]
        vkeys = s['variant_keys_map'].get((ck, fam), [])
        vt    = s['variant_tabs_map'][ck].get(fam)
        vi    = vt.currentIndex() if vt else -1
        if vi < 0 or vi >= len(vkeys): return None, None
        vcfg = s['cfgs_map'].get((ck, fam, vkeys[vi]), {})
        fcstd = vcfg.get('fcstd')
        if not fcstd or not os.path.exists(fcstd): return None, None
        stem  = os.path.splitext(os.path.basename(fcstd))[0]
        thumb = os.path.join(cdir, 'thumbnails', stem + '.png')
        return fcstd, thumb

    def _on_edit_fcstd():
        fcstd, _ = _get_current_fcstd_and_thumb()
        if not fcstd:
            QtWidgets.QMessageBox.warning(
                dlg, _t("model_not_found"),
                _t("no_fcstd_for_variant"))
            return
        try:
            App.openDocument(fcstd)
            dlg.reject()   # ferme le sélecteur (dialog modal → libère FreeCAD)
        except Exception as _e:
            QtWidgets.QMessageBox.critical(
                dlg, _t("err_open_model"), str(_e))

    def _on_refresh_thumb():
        """Supprime la vignette en cache et extrait la nouvelle depuis le FCStd."""
        s    = ui_state
        ci   = s['component_combo'].currentIndex()
        ck_l = s['component_keys']
        if ci < 0 or ci >= len(ck_l): return
        ck   = ck_l[ci]
        cdir = components[ck]['dir']
        fkeys = s['family_keys_map'].get(ck, [])
        ft    = s['family_tabs_map'].get(ck)
        fi    = ft.currentIndex() if ft else -1
        if fi < 0 or fi >= len(fkeys): return
        fam  = fkeys[fi]
        vkeys = s['variant_keys_map'].get((ck, fam), [])
        vt    = s['variant_tabs_map'][ck].get(fam)
        vi    = vt.currentIndex() if vt else -1
        if vi < 0 or vi >= len(vkeys): return
        vcfg  = s['cfgs_map'].get((ck, fam, vkeys[vi]), {})
        fcstd = vcfg.get('fcstd')
        if not fcstd: return

        # Supprimer la vignette en cache (même logique de chemin que _get_thumbnail_path)
        stem      = os.path.splitext(os.path.basename(fcstd))[0]
        thumb_dir = os.path.join(cdir, 'thumbnails')
        thumb_png = os.path.join(thumb_dir, stem + '.png')
        if os.path.exists(thumb_png):
            try:
                os.remove(thumb_png)
            except Exception as _e:
                QtWidgets.QMessageBox.warning(
                    dlg, _t("err_delete_thumb"), str(_e))
                return

        # Ré-extraire depuis le FCStd et afficher immédiatement
        new_path = _get_thumbnail_path(cdir, fcstd)
        _update_thumbnail(thumb_label, new_path, QtGui, QtCore)

    btn_edit_fcstd.clicked.connect(_on_edit_fcstd)
    btn_refresh_thumb.clicked.connect(_on_refresh_thumb)

    # ── Boutons ───────────────────────────────────────────────
    bbox = QtWidgets.QDialogButtonBox(
        QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
    bbox.button(QtWidgets.QDialogButtonBox.Ok).setText(_t("generate_btn"))
    bbox.button(QtWidgets.QDialogButtonBox.Cancel).setText(_t("cancel_btn"))
    bbox.accepted.connect(dlg.accept)
    bbox.rejected.connect(dlg.reject)

    # Bouton Remplacer — visible seulement si un composant SC_ est sélectionné
    _replace_obj = (preselect or {}).get('obj')
    btn_replace  = QtWidgets.QPushButton(_t('replace_btn'))
    btn_replace.setToolTip(_t('replace_tip'))
    _replace_mode = [False]   # flag mutable pour la closure
    def _on_replace():
        _replace_mode[0] = True
        dlg.accept()
    btn_replace.clicked.connect(_on_replace)

    # En mode remplacement : cacher Générer, montrer seulement Remplacer
    _is_replace_mode = _replace_obj is not None
    btn_replace.setVisible(_is_replace_mode)
    bbox.button(QtWidgets.QDialogButtonBox.Ok).setVisible(not _is_replace_mode)

    # Bouton éditeur de catalogues (5C)
    btn_editor = QtWidgets.QPushButton(_t('catalog_editor_btn'))
    btn_editor.setToolTip(_t('catalog_editor_tip'))
    def _open_editor():
        try:
            import importlib.util
            import sys
            # Forcer le rechargement pour éviter le cache module
            if 'StandardPartSelectorEditor' in sys.modules:
                del sys.modules['StandardPartSelectorEditor']
            import time
            _mod_name = f'StandardPartSelectorEditor_{int(time.time())}'
            spec = importlib.util.spec_from_file_location(
                _mod_name,
                os.path.join(MACRO_DIR, 'StandardPartSelectorEditor.py'))
            mod = importlib.util.module_from_spec(spec)
            mod.App    = App
            mod.MACRO_DIR = MACRO_DIR
            sys.modules[_mod_name] = mod
            spec.loader.exec_module(mod)
            # Passer le composant/famille courants pour pré-sélection
            ci = component_combo.currentIndex()
            _pre_ck  = ui_state['component_keys'][ci] \
                       if 0 <= ci < len(ui_state['component_keys']) else ''
            _fkeys   = ui_state['family_keys_map'].get(_pre_ck, [])
            _ft      = ui_state['family_tabs_map'].get(_pre_ck)
            _fi      = _ft.currentIndex() if _ft else -1
            _pre_fam = _fkeys[_fi] if 0 <= _fi < len(_fkeys) else ''
            # Variante courante
            _vt      = ui_state['variant_tabs_map'].get(_pre_ck, {}).get(_pre_fam)
            _vkeys   = ui_state['variant_keys_map'].get((_pre_ck, _pre_fam), [])
            _vi      = _vt.currentIndex() if _vt else -1
            _pre_var = _vkeys[_vi] if 0 <= _vi < len(_vkeys) else ''
            mod.open_editor(preselect_comp=_pre_ck,
                            preselect_family=_pre_fam,
                            preselect_variant=_pre_var)

            # Nettoyer les modules de l'éditeur pour éviter les références
            # à d'anciens objets Qt qui corrompent le prochain lancement
            for _mk in list(sys.modules.keys()):
                if 'StandardPartSelectorEditor' in _mk:
                    try: del sys.modules[_mk]
                    except Exception: pass
            # Après fermeture éditeur — recharger
            new_components = discover_components()

            # Invalider le cache de category_labels.py : l'éditeur peut y
            # avoir écrit un libellé d'affichage de variante (category_
            # labels.json), mais ce module ne recharge son contenu qu'une
            # fois, à la première lecture (variable module `_cache`) — sans
            # cette invalidation, le nouveau libellé resterait invisible
            # pour le reste de la session FreeCAD. _t.family/variant/
            # component (config.py) délèguent directement à category_labels,
            # donc cette seule ligne suffit à les rafraîchir.
            category_labels._cache = None

            def _variants_sig(comps):
                sig = set()
                for ck, cd in comps.items():
                    for fam, variants in cd.get('cfg', {}).items():
                        for var in variants:
                            sig.add((ck, fam, var))
                return sig

            old_sig = _variants_sig(components)
            new_sig = _variants_sig(new_components)
            components.clear()
            components.update(new_components)

            if new_sig != old_sig:
                _dbg(f'[selector] Rechargement complet : {len(new_sig) - len(old_sig):+d} variante(s)')
                # Nouvelles variantes → reconstruire le stack en place
                try:
                    from PySide2.QtCore import QTimer
                except ImportError:
                    from PySide.QtCore import QTimer

                # Vider le stack existant
                while component_stack.count():
                    w = component_stack.widget(0)
                    component_stack.removeWidget(w)
                    w.setParent(None)

                # Reconstruire avec les nouveaux composants
                new_stack, new_combo, new_thumb, new_ui = \
                    _build_ui_maps(new_components, QtWidgets, QtCore, dlg, replace_fcstd=replace_fcstd)

                # Transférer les widgets du nouveau stack vers l'ancien
                while new_stack.count():
                    w = new_stack.widget(0)
                    new_stack.removeWidget(w)
                    component_stack.addWidget(w)

                # Mettre à jour le combo
                component_combo.blockSignals(True)
                component_combo.clear()
                for k in new_components:
                    component_combo.addItem(_t.component(k))
                component_combo.blockSignals(False)

                # Mettre à jour ui_state
                for key, val in new_ui.items():
                    ui_state[key] = val
                ui_state['component_combo'] = component_combo

                # Reconnecter les signaux de filtrage
                for _fft in ui_state['family_tabs_map'].values():
                    _fft.currentChanged.connect(_reapply_filter)
                for (_ck2, _fam2) in ui_state['variant_keys_map']:
                    ui_state['variant_tabs_map'][_ck2][_fam2]\
                        .currentChanged.connect(_reapply_filter)

                component_combo.setCurrentIndex(0)
                component_stack.setCurrentIndex(0)
                QTimer.singleShot(0, _refresh_thumb)
            else:
                # Même structure → juste recharger les données JSON, et
                # les libellés d'onglets famille/variante (traduction
                # potentiellement mise à jour par l'éditeur).
                _dbg('[selector] Rechargement données catalogue (même structure)')
                _reload_catalogs(ui_state, new_components)
                for ck, ft in ui_state['family_tabs_map'].items():
                    for fi, fam in enumerate(ui_state['family_keys_map'].get(ck, [])):
                        ft.setTabText(fi, _t.family(fam))
                        vt = ui_state['variant_tabs_map'].get(ck, {}).get(fam)
                        if not vt:
                            continue
                        for vi, var in enumerate(
                                ui_state['variant_keys_map'].get((ck, fam), [])):
                            vt.setTabText(vi, _t.variant(var))
        except Exception as _e:
            import traceback
            QtWidgets.QMessageBox.critical(
                dlg, _t('editor_error_title'), str(_e) + '\n' + traceback.format_exc())
        finally:
            # Ramener le sélecteur au premier plan après fermeture de l'éditeur
            try:
                dlg.show()
                dlg.raise_()
                dlg.activateWindow()
            except Exception:
                pass
            # Forcer le rafraîchissement visuel des tables
            for tbl in ui_state.get('tables_map', {}).values():
                if tbl is not None:
                    try:
                        tbl.viewport().update()
                    except Exception:
                        pass
    btn_editor.clicked.connect(_open_editor)

    # ── Barre de boutons bas ──────────────────────────────────────────────
    btn_sync = QtWidgets.QPushButton(_t('sync_btn'))
    btn_sync.setToolTip(_t('sync_tip'))
    # Sync disponible seulement si bibliothèque configurée
    _lib_path = _get_library_path()
    btn_sync.setVisible(bool(_lib_path and os.path.isdir(_lib_path)))
    _sync_done = [False]  # flag pour éviter double sync à la fermeture
    def _on_sync():
        _sync_library_to_sc_dir(interactive=True, QtWidgets=QtWidgets)
        _sync_done[0] = True
        # Recharger les composants après sync
        new_components = discover_components()
        _build_ui_maps(new_components, QtWidgets, QtCore, dlg,
                       replace_fcstd=replace_fcstd)
    btn_sync.clicked.connect(_on_sync)

    btn_update = QtWidgets.QPushButton(_t('update_btn'))
    btn_update.setToolTip(_t('update_tip'))
    # Visible seulement si des composants SC_ sont présents dans le document
    try:
        import FreeCAD as _App
        _has_sc = any(
            hasattr(o, 'SC_Standard') and o.SC_Standard
            for o in (_App.ActiveDocument.Objects
                      if _App.ActiveDocument else []))
    except Exception:
        _has_sc = False
    btn_update.setVisible(_has_sc and not _is_replace_mode)
    def _on_update():
        import FreeCAD as _App2
        _results = _scan_sc_components(_App2.ActiveDocument, components)
        _to_upd  = _show_update_dialog(_results, QtWidgets, QtCore)
        if _to_upd is None: return  # annulé
        if _to_upd:
            _apply_bulk_update(_to_upd, QtWidgets)
    btn_update.clicked.connect(_on_update)

    btn_bar = QtWidgets.QHBoxLayout()
    btn_bar.addWidget(btn_editor)
    btn_bar.addWidget(btn_sync)
    btn_bar.addWidget(btn_update)
    btn_bar.addStretch()
    btn_bar.addWidget(btn_replace)
    btn_bar.addWidget(bbox)
    main_layout.addLayout(btn_bar)
    dlg.setLayout(main_layout)

    # ── Exécution ─────────────────────────────────────────────
    try:
        from PySide2.QtCore import QTimer
    except ImportError:
        from PySide.QtCore import QTimer

    # ── Restauration de la sélection (synchrone, avant dlg.exec)
    # Hors boucle événements : les signaux bloqués dans _restore_ui_silent
    # ne peuvent pas être rejoués, aucun _reapply_filter ne peut interférer.
    # Pas de timer → la sélection n'est jamais écrasée après une action utilisateur.
    if preselect:
        _restore_selection_from(ui_state, preselect)
    else:
        _restore_last_selection(ui_state)
    # Focus sur le tableau actif pour que la sélection soit visible
    sel_init = _get_selection(ui_state)
    if sel_init:
        t_init = ui_state['tables_map'].get((sel_init[0], sel_init[1], sel_init[2]))
        if t_init:
            t_init.setFocus()
    QTimer.singleShot(0, _refresh_thumb)
    dlg.exec()
    _save_window_size(dlg)
    if not dlg.result():
        print(_t("cancelled"))
        return None

    sel = _get_selection(ui_state)
    if sel is None: return None
    return sel + (geo_info, _replace_mode[0],  # tuple étendu
                  _sync_done[0])               # +sync_done


# ============================================================
# Persistance de la sélection (3.2)
# ============================================================
def _save_last_selection(comp_key, family, variant, ref):
    try:
        param = App.ParamGet('User parameter:Plugins/StandardPartSelector')
        param.SetString('LastComponent', comp_key)
        param.SetString('LastFamily',    family)
        param.SetString('LastVariant',   variant)
        param.SetString('LastRef',       ref)
    except Exception as e:
        print(f'[StandardPartSelector] Sauvegarde sélection ignorée: {e}')


# ============================================================
# Génération
# ============================================================
