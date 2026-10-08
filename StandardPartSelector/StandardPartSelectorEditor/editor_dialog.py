# -*- coding: utf-8 -*-
"""
editor_dialog — Dialogue éditeur de catalogues.
"""
import FreeCAD as App
import os, json, shutil, ast, types, locale
from datetime import datetime
from editor_config  import (_get_qt, _build_tr, _t, get_library_path,
                            set_library_path, migrate_library,
                            sanitize_filename, MACRO_DIR, MACROS_DIR,
                            STANDARDS_DIR, LIBRARY_PATH_FILE)
from editor_catalog import (discover_components, find_varset,
                            read_varset_params, read_varset_from_fcstd,
                            write_json_aligned, backup_file, _fmt_val,
                            read_variant_label, write_variant_label)

# Valeurs spéciales des combos famille/catégorie/variante déclenchant la
# création d'un nouvel élément plutôt que la sélection d'un existant.
# Définies UNE SEULE FOIS ici et référencées partout (addItem ET
# comparaisons) — jamais par un littéral répété — pour que la détection
# "nouvel élément" reste correcte quelle que soit la langue active.
_SENTINEL_NEW_FAMILY   = _t('ed_sentinel_new_family',   default='+ Nouvelle famille…')
_SENTINEL_NEW_CATEGORY = _t('ed_sentinel_new_category', default='+ Nouvelle catégorie…')
_SENTINEL_NEW_VARIANT  = _t('ed_sentinel_new_variant',  default='+ Nouvelle variante…')
_SENTINEL_NEW_GENERIC  = _t('ed_sentinel_new_generic',  default='+ Nouveau…')

# Marqueur de version : affiché dans la vue rapport FreeCAD au chargement du
# module. Permet de vérifier à coup sûr que c'est bien CE fichier (et pas une
# copie restée dans un autre dossier / une ancienne version en cache) qui est
# réellement exécuté — indispensable pour diagnostiquer un correctif qui
# "ne prend pas effet".
_EDITOR_DIALOG_VERSION = '2026-08-31-fix9 (suppression bibliothèque + local)'
print(f'[Editor] editor_dialog.py chargé — version {_EDITOR_DIALOG_VERSION}')

class EditorDialog:
    """Wizard d'édition de catalogues StandardPartSelector."""

    PAGE_META     = 0
    PAGE_COLUMNS  = 1
    PAGE_DATA     = 2
    PAGE_PREVIEW  = 3

    # Colonne « Préféré » : toujours en TOUTE PREMIÈRE position (colonne 0)
    # de data_table — jamais un champ configurable comme les autres (pas de
    # ligne dans col_table, absente de _legende). Valeur persistée par
    # référence sous la clé JSON '_favori' (booléen), créée à False pour
    # toute référence qui ne l'avait pas encore (anciens catalogues).
    # Comme elle est fixée en position 0, un import CSV (qui ajoute ses
    # colonnes à la fin) ne peut jamais la faire glisser — _favori_col()
    # garde néanmoins un repli par libellé par sécurité.
    FAVORI_LABEL = _t('ed_col_favori')
    FAVORI_KEY   = '_favori'
    FAVORI_COL   = 0
    # Référence : juste après Préféré, elle aussi fixe.
    REF_COL      = 1

    def __init__(self, preselect_comp='', preselect_family='',
                 preselect_variant='', new_ref_name=None,
                 new_ref_vals=None, restrict_fcstd=None,
                 force_action=None):
        QtWidgets, QtCore, QtGui = _get_qt()
        self.Qt       = QtCore.Qt
        self.QtWidgets = QtWidgets
        self.QtCore    = QtCore
        self.QtGui     = QtGui

        self.components = discover_components()

        # État interne
        self.action       = 'new_fcstd'   # 'new_fcstd' | 'edit' | 'from_family'
        self.fcstd_path   = ''
        self.varset       = {}            # {prop: val} depuis FCStd
        # État transitoire pour le renommage / remplacement du FCStd d'une
        # variante existante (page Métadonnées) — jamais persisté avant un
        # _save() explicite, réinitialisé à chaque changement de variante.
        self._fcstd_target_name  = ''     # nom de fichier à utiliser à la sauvegarde
        self._pending_fcstd_rename = None # (ancien_nom, nouveau_nom) ou None
        self._fcstd_replaced     = False  # True si un AUTRE fichier a été choisi
        self._fcstd_replaced_old_name = '' # nom du fichier remplacé (pour l'avertissement bibliothèque)
        self._pending_library_cleanup = [] # [(old_name, chemin_bibliothèque), ...] proposés après _save()
        self.comp_key     = preselect_comp
        self.family       = preselect_family
        self.variant      = ''
        self.variant_label = ''           # libellé d'affichage cosmétique (optionnel)
        self.columns      = []            # [{'key': str, 'label': str, 'mute': bool}]
        self.refs         = {}            # {ref_name: {key: val}}
        self.source_comp    = preselect_comp
        self.source_family  = preselect_family
        self.source_variant = preselect_variant
        self.variant        = preselect_variant
        # Mode création nouvelle référence depuis composant non-standard
        self.new_ref_name   = new_ref_name    # nom suggéré
        self.new_ref_vals   = new_ref_vals or {}  # valeurs VarSet actuelles
        self.restrict_fcstd = restrict_fcstd  # filtrer FCStd
        # Forcer l'action (ex: 'edit' pour modifier une variante existante)
        if force_action:
            self.action = force_action

        # Signatures utilisées pour ne reconstruire col_table / data_table
        # depuis leur source "froide" (VarSet, variante source, disque) que
        # lors de la PREMIÈRE visite de la page pour une variante donnée.
        # Sans cela, un simple aller-retour Colonnes ↔ Données (ou
        # Métadonnées ↔ Données pour une variante existante) efface les
        # lignes déjà saisies ou importées depuis un CSV.
        self._columns_page_sig      = None
        self._data_page_sig         = None
        self._data_table_initialized = False

        self._build()

    # ──────────────────────────────────────────────────────────
    # Construction du dialogue
    # ──────────────────────────────────────────────────────────
    def _build(self):
        Qt = self.Qt
        QtWidgets = self.QtWidgets

        self.dlg = QtWidgets.QDialog()
        self.dlg.setWindowTitle(_t('ed_win_title'))
        self.dlg.setMinimumSize(820, 580)
        self.dlg.setWindowFlags(
            self.dlg.windowFlags() | self.QtCore.Qt.WindowStaysOnTopHint)

        main_layout = QtWidgets.QVBoxLayout()

        # Titre de l'étape courante
        self.step_label = QtWidgets.QLabel()
        self.step_label.setStyleSheet(
            'font-size: 14px; font-weight: bold; padding: 6px 0;')
        main_layout.addWidget(self.step_label)

        # QStackedWidget
        self.stack = QtWidgets.QStackedWidget()
        self._build_page_meta()
        self._build_page_columns()
        self._build_page_data()
        self._build_page_preview()
        main_layout.addWidget(self.stack, stretch=1)

        # Boutons de navigation
        nav = QtWidgets.QHBoxLayout()
        self.btn_back    = QtWidgets.QPushButton(_t('ed_btn_prev'))
        self.btn_next    = QtWidgets.QPushButton(_t('ed_btn_next'))
        self.btn_finish  = QtWidgets.QPushButton(_t('ed_btn_save'))
        self.btn_cancel  = QtWidgets.QPushButton(_t('ed_btn_cancel'))
        self.btn_library = QtWidgets.QPushButton(_t('ed_btn_library'))
        self.btn_library.setToolTip(
            _t('ed_tooltip_library_btn'))

        nav.addWidget(self.btn_cancel)
        nav.addWidget(self.btn_library)
        nav.addStretch()
        nav.addWidget(self.btn_back)
        nav.addWidget(self.btn_next)
        nav.addWidget(self.btn_finish)
        main_layout.addLayout(nav)

        self.dlg.setLayout(main_layout)

        # Connexions
        self.btn_back.clicked.connect(self._go_back)
        self.btn_next.clicked.connect(self._go_next)
        self.btn_finish.clicked.connect(self._finish)
        self.btn_cancel.clicked.connect(self.dlg.reject)
        self.btn_library.clicked.connect(self._configure_library)

        self._update_nav(self.PAGE_META)

    # ── Page 0 : Famille / Catégorie / Variante (adaptatif) ─
    def _build_page_meta(self):
        QtWidgets = self.QtWidgets
        page   = QtWidgets.QWidget()
        layout = QtWidgets.QFormLayout()
        layout.setSpacing(8)

        # Famille
        self.meta_family_combo = QtWidgets.QComboBox()
        self.meta_family_edit  = QtWidgets.QLineEdit()
        self.meta_family_edit.setPlaceholderText(_t('ed_ph_new_family'))
        layout.addRow(_t('ed_row_family'),       self.meta_family_combo)
        layout.addRow(_t('ed_row_family_name'),   self.meta_family_edit)

        # Catégorie
        self.meta_comp_combo = QtWidgets.QComboBox()
        self.meta_comp_edit  = QtWidgets.QLineEdit()
        self.meta_comp_edit.setPlaceholderText(_t('ed_ph_new_category'))
        layout.addRow(_t('ed_row_category'),     self.meta_comp_combo)
        layout.addRow(_t('ed_row_category_name'), self.meta_comp_edit)

        # Variante
        self.meta_variant_combo = QtWidgets.QComboBox()
        self.meta_variant_edit  = QtWidgets.QLineEdit()
        self.meta_variant_edit.setPlaceholderText(_t('ed_ph_new_variant'))
        layout.addRow(_t('ed_row_variant'),      self.meta_variant_combo)
        layout.addRow(_t('ed_row_variant_name'),  self.meta_variant_edit)

        # Libellé affiché de la variante — purement cosmétique : n'affecte
        # que le texte montré dans le sélecteur, jamais la clé interne de la
        # variante (utilisée dans config.json et les composants déjà
        # insérés), pour ne jamais les pénaliser.
        self.meta_variant_label_edit = QtWidgets.QLineEdit()
        self.meta_variant_label_edit.setPlaceholderText(
            _t('ed_ph_variant_label'))
        layout.addRow(_t('ed_row_display_label'), self.meta_variant_label_edit)

        # Source FCStd (visible si variante nouvelle)
        src_row = QtWidgets.QHBoxLayout()
        self.meta_fcstd_edit = QtWidgets.QLineEdit()
        self.meta_fcstd_edit.setReadOnly(True)
        self.meta_fcstd_edit.setPlaceholderText(_t('ed_ph_fcstd_source'))
        self.meta_fcstd_btn  = QtWidgets.QPushButton(_t('ed_btn_browse'))
        self.meta_fcstd_btn.clicked.connect(self._browse_meta_fcstd)
        # Renommer le fichier FCStd d'une variante existante (contenu
        # inchangé) — n'a de sens que pour une variante déjà sauvegardée,
        # masqué pour une nouvelle variante (cf. _on_variant_changed).
        self.meta_fcstd_rename_btn = QtWidgets.QPushButton(_t('ed_btn_rename'))
        self.meta_fcstd_rename_btn.clicked.connect(self._rename_meta_fcstd)
        src_row.addWidget(self.meta_fcstd_edit)
        src_row.addWidget(self.meta_fcstd_rename_btn)
        src_row.addWidget(self.meta_fcstd_btn)
        self.meta_src_label = QtWidgets.QLabel(_t('ed_label_fcstd_source'))
        layout.addRow(self.meta_src_label, src_row)

        # JSON (visible si variante nouvelle)
        self.meta_json_edit  = QtWidgets.QLineEdit()
        self.meta_json_edit.setPlaceholderText(_t('ed_ph_json_filename'))
        self.meta_json_label = QtWidgets.QLabel(_t('ed_label_json_file'))
        layout.addRow(self.meta_json_label, self.meta_json_edit)

        # Connexions cascade
        self.meta_family_combo.currentIndexChanged.connect(self._on_family_changed)
        self.meta_comp_combo.currentIndexChanged.connect(self._on_category_changed)
        self.meta_variant_combo.currentIndexChanged.connect(self._on_variant_changed)

        page.setLayout(layout)
        self.stack.addWidget(page)        # index PAGE_META = 0

    # ── Page 3 : Colonnes ────────────────────────────────────
    def _build_page_columns(self):
        QtWidgets = self.QtWidgets
        page   = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout()

        lbl = QtWidgets.QLabel(
            _t('ed_label_columns_help'))
        lbl.setWordWrap(True)
        layout.addWidget(lbl)

        # Tableau de configuration des colonnes
        self.col_table = QtWidgets.QTableWidget()
        self.col_table.setColumnCount(5)
        self.col_table.setHorizontalHeaderLabels(
            [_t('ed_header_show'), _t('ed_header_json_key'), _t('ed_header_display_label'), _t('ed_header_mute'), _t('ed_header_required')])
        self.col_table.horizontalHeader().setStretchLastSection(False)
        self.col_table.horizontalHeader().setSectionResizeMode(
            2, QtWidgets.QHeaderView.Stretch)
        self.col_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        layout.addWidget(self.col_table)

        # Boutons gestion colonnes
        btn_row = QtWidgets.QHBoxLayout()
        btn_add_mute = QtWidgets.QPushButton(_t('ed_btn_add_mute_param'))
        btn_del      = QtWidgets.QPushButton(_t('ed_btn_delete'))
        btn_up       = QtWidgets.QPushButton(_t('ed_btn_arrow_up'))
        btn_down     = QtWidgets.QPushButton(_t('ed_btn_arrow_down'))
        btn_add_mute.clicked.connect(self._add_mute_param)
        btn_del.clicked.connect(self._del_column)
        btn_up.clicked.connect(self._col_up)
        btn_down.clicked.connect(self._col_down)
        for b in (btn_add_mute, btn_del, btn_up, btn_down):
            btn_row.addWidget(b)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        page.setLayout(layout)
        self.stack.addWidget(page)        # index PAGE_COLUMNS = 1

    # ── Page 4 : Données ─────────────────────────────────────
    def _build_page_data(self):
        QtWidgets = self.QtWidgets
        page   = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout()

        lbl = QtWidgets.QLabel(
            _t('ed_label_refs_help'))
        lbl.setWordWrap(True)
        layout.addWidget(lbl)

        self.data_table = QtWidgets.QTableWidget()
        self.data_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.data_table.setSelectionMode(
            QtWidgets.QAbstractItemView.ExtendedSelection)
        _hdr = self.data_table.horizontalHeader()
        _hdr.sectionClicked.connect(self._on_data_col_header_clicked)
        _hdr.setHighlightSections(False)   # Qt ne gère pas le gras lui-même
        # Clic sur cellule → plus de colonne sélectionnée
        self.data_table.cellClicked.connect(
            lambda r, c: self._clear_col_highlight())
        # Coche « Préféré » togglée → gras immédiat sur toute la ligne
        self.data_table.itemChanged.connect(self._on_data_item_changed)
        layout.addWidget(self.data_table)

        btn_row = QtWidgets.QHBoxLayout()
        btn_add = QtWidgets.QPushButton(_t('ed_btn_add_row'))
        btn_dup = QtWidgets.QPushButton(_t('ed_btn_duplicate'))
        btn_del = QtWidgets.QPushButton(_t('ed_btn_delete'))
        btn_csv = QtWidgets.QPushButton(_t('ed_btn_csv_export'))
        btn_up  = QtWidgets.QPushButton(_t('ed_btn_arrow_up'))
        btn_dn  = QtWidgets.QPushButton(_t('ed_btn_arrow_down'))
        btn_cl  = QtWidgets.QPushButton(_t('ed_btn_arrow_left'))
        btn_cr  = QtWidgets.QPushButton(_t('ed_btn_arrow_right'))
        btn_up.setFixedWidth(30)
        btn_dn.setFixedWidth(30)
        btn_cl.setFixedWidth(30)
        btn_cr.setFixedWidth(30)
        btn_csv.setToolTip(_t('ed_tooltip_import_csv'))
        btn_cl.setToolTip(_t('ed_tooltip_move_col_left'))
        btn_cr.setToolTip(_t('ed_tooltip_move_col_right'))
        btn_add.clicked.connect(self._data_add_row)
        btn_dup.clicked.connect(self._data_dup_row)
        btn_del.clicked.connect(self._data_del_row)
        btn_csv.clicked.connect(self._import_csv)
        btn_up.clicked.connect(self._data_row_up)
        btn_dn.clicked.connect(self._data_row_down)
        btn_cl.clicked.connect(self._data_col_left)
        btn_cr.clicked.connect(self._data_col_right)
        for b in (btn_add, btn_dup, btn_del, btn_csv, btn_up, btn_dn,
                  btn_cl, btn_cr):
            btn_row.addWidget(b)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        page.setLayout(layout)
        self.stack.addWidget(page)        # index PAGE_DATA = 2

    # ── Page 5 : Aperçu ──────────────────────────────────────
    def _build_page_preview(self):
        QtWidgets = self.QtWidgets
        page   = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout()

        lbl = QtWidgets.QLabel(_t('ed_label_json_preview'))
        lbl.setStyleSheet('font-weight: bold;')
        layout.addWidget(lbl)

        self.preview_text = QtWidgets.QPlainTextEdit()
        self.preview_text.setReadOnly(True)
        self.preview_text.setFont(self.QtGui.QFont("Courier New", 9))
        layout.addWidget(self.preview_text)

        self.save_status = QtWidgets.QLabel("")
        self.save_status.setWordWrap(True)
        layout.addWidget(self.save_status)

        page.setLayout(layout)
        self.stack.addWidget(page)        # index PAGE_PREVIEW = 3

    # ──────────────────────────────────────────────────────────
    # Navigation
    # ──────────────────────────────────────────────────────────
    _TITLES = [
        _t('ed_step1_title'),
        _t('ed_step2_title'),
        _t('ed_step3_title'),
        _t('ed_step4_title'),
        _t('ed_step5_title'),
        _t('ed_step_preview_title'),
    ]

    def _update_nav(self, page_idx):
        self.stack.setCurrentIndex(page_idx)
        self.step_label.setText(self._TITLES[page_idx])
        self.btn_back.setEnabled(page_idx > 0)
        is_last    = page_idx == self.PAGE_PREVIEW
        is_data    = page_idx == self.PAGE_DATA
        self.btn_next.setVisible(not is_last)
        # Sauvegarder visible sur page Aperçu ET page Données
        self.btn_finish.setVisible(is_last or is_data)
        self.btn_finish.setText(
            _t('ed_btn_save') if is_last else _t('ed_btn_save_noprev'))

    def _go_back(self):
        idx = self.stack.currentIndex()
        if idx <= 0:
            return
        prev_idx = idx - 1
        # NB : la page Colonnes n'est plus sautée pour une variante existante
        # — elle permet de renommer, réordonner et surtout SUPPRIMER une
        # colonne (bouton « Supprimer », déjà existant) pour un catalogue
        # déjà créé, pas seulement de la masquer.
        self._update_nav(prev_idx)

    def _go_next(self):
        idx = self.stack.currentIndex()
        if not self._validate_page(idx):
            return
        next_idx = idx + 1
        self._prepare_page(next_idx)
        self._update_nav(next_idx)

    # ──────────────────────────────────────────────────────────
    # Validation par page
    # ──────────────────────────────────────────────────────────
    def _validate_page(self, idx):
        QtWidgets = self.QtWidgets

        if idx == self.PAGE_META:
            # Famille = comp_key (dossier)
            fam_txt = self.meta_family_combo.currentText()
            self.comp_key = self.meta_family_edit.text().strip() \
                if fam_txt == _SENTINEL_NEW_FAMILY else fam_txt
            if not self.comp_key:
                QtWidgets.QMessageBox.warning(
                    self.dlg, _t('ed_err_missing_field_title'), _t('ed_err_family_name_required'))
                return False

            # Catégorie = famille dans config.json
            cat_txt = self.meta_comp_combo.currentText()
            self.family = self.meta_comp_edit.text().strip() \
                if cat_txt == _SENTINEL_NEW_CATEGORY else cat_txt
            if not self.family:
                QtWidgets.QMessageBox.warning(
                    self.dlg, _t('ed_err_missing_field_title'), _t('ed_err_category_name_required'))
                return False

            # Variante
            var_txt = self.meta_variant_combo.currentText()
            is_new_var = (var_txt == _SENTINEL_NEW_VARIANT)
            self.variant = self.meta_variant_edit.text().strip() \
                if is_new_var else var_txt
            if not self.variant:
                QtWidgets.QMessageBox.warning(
                    self.dlg, _t('ed_err_missing_field_title'), _t('ed_err_variant_name_required'))
                return False
            self.variant_label = self.meta_variant_label_edit.text().strip() \
                if hasattr(self, 'meta_variant_label_edit') else ''

            if is_new_var:
                # Nouvelle variante : FCStd + JSON requis
                if not self.fcstd_path or not os.path.exists(self.fcstd_path):
                    QtWidgets.QMessageBox.warning(
                        self.dlg, _t('ed_err_file_missing_title'),
                        _t('ed_err_fcstd_required'))
                    return False
                jf = self.meta_json_edit.text().strip()
                if not jf:
                    # Auto-générer depuis FCStd
                    jf = os.path.splitext(
                        os.path.basename(self.fcstd_path))[0]
                self.json_filename = sanitize_filename(
                    jf.replace('.json','')) + '.json'
                # Mettre à jour le VarSet
                self.varset = read_varset_from_fcstd(self.fcstd_path)
            # Variante existante : fcstd_path + json_filename
            # déjà récupérés dans _on_variant_changed
            return True

        if idx == self.PAGE_COLUMNS:
            self._read_columns_from_table()
            if not any(c for c in self.columns if not c.get('mute')):
                QtWidgets.QMessageBox.warning(
                    self.dlg, _t('ed_err_columns_missing_title'),
                    _t('ed_err_columns_min_required'))
                return False
            return True

        if idx == self.PAGE_DATA:
            # Resynchroniser self.columns depuis col_table AVANT de lire les
            # données (comme le fait déjà _save()) : sans cela, un ordre de
            # colonnes ou un renommage fait juste avant ne serait pas pris
            # en compte pour l'appariement en-tête → colonne ci-dessous.
            self._read_columns_from_table()
            self._read_data_from_table()
            print(f'[validate] PAGE_DATA: refs={len(self.refs)} colonnes={[c["key"] for c in self.columns]}')
            if self.refs:
                first_ref = next(iter(self.refs))
                print(f'[validate] première ref: {first_ref!r} → keys={list(self.refs[first_ref].keys())}')
            if not self.refs:
                QtWidgets.QMessageBox.warning(
                    self.dlg, _t('ed_err_data_missing_title'),
                    _t('ed_err_ref_min_required'))
                return False
            return True

        return True

    # ──────────────────────────────────────────────────────────
    # Préparation des pages
    # ──────────────────────────────────────────────────────────
    def _prepare_page(self, idx):
        if idx == self.PAGE_META:
            self._prepare_meta_page()
        elif idx == self.PAGE_COLUMNS:
            self._prepare_columns_page()
        elif idx == self.PAGE_DATA:
            self._prepare_data_page()
        elif idx == self.PAGE_PREVIEW:
            self._prepare_preview_page()

    def _on_family_changed_silent(self):
        """
        Peuple la cascade sans signaux.
        Famille (1er combo)  = comp_key = dossier (circlips, bearings...)
        Catégorie (2e combo) = famille dans config.json (Billes rainurées...)
        """
        comp_key = self.meta_family_combo.currentText()  # dossier
        is_new_comp = (comp_key == _SENTINEL_NEW_FAMILY)
        _pre_cat = self.source_family or self.family     # famille config.json
        _pre_var = self.source_variant or self.variant

        self.meta_family_edit.setVisible(is_new_comp)

        # Catégorie = famille dans config.json du comp_key sélectionné
        self.meta_comp_combo.blockSignals(True)
        self.meta_comp_combo.clear()
        if not is_new_comp and comp_key in self.components:
            for fam in sorted(
                    self.components[comp_key].get('cfg', {}).keys()):
                self.meta_comp_combo.addItem(fam)
        self.meta_comp_combo.addItem(_SENTINEL_NEW_CATEGORY)
        if is_new_comp:
            self.meta_comp_combo.setCurrentIndex(
                self.meta_comp_combo.count() - 1)
        elif _pre_cat:
            ci = self.meta_comp_combo.findText(_pre_cat)
            if ci >= 0: self.meta_comp_combo.setCurrentIndex(ci)
        self.meta_comp_combo.blockSignals(False)
        self.meta_comp_edit.setVisible(
            self.meta_comp_combo.currentText() == _SENTINEL_NEW_CATEGORY
            and not is_new_comp)

        # Variante
        cat = self.meta_comp_combo.currentText()   # famille config.json
        self.meta_variant_combo.blockSignals(True)
        self.meta_variant_combo.clear()
        if not is_new_comp and comp_key in self.components and \
                cat != _SENTINEL_NEW_CATEGORY:
            for var in sorted(
                    self.components[comp_key].get('cfg',{}).get(cat,{}).keys()):
                self.meta_variant_combo.addItem(var)
        self.meta_variant_combo.addItem(_SENTINEL_NEW_VARIANT)
        if is_new_comp or cat == _SENTINEL_NEW_CATEGORY:
            self.meta_variant_combo.setCurrentIndex(
                self.meta_variant_combo.count() - 1)
        elif _pre_var:
            vi = self.meta_variant_combo.findText(_pre_var)
            if vi >= 0: self.meta_variant_combo.setCurrentIndex(vi)
        self.meta_variant_combo.blockSignals(False)

        is_new_var = (
            self.meta_variant_combo.currentText() == _SENTINEL_NEW_VARIANT)
        self.meta_variant_edit.setVisible(is_new_var)
        self.meta_json_label.setVisible(is_new_var)
        self.meta_json_edit.setVisible(is_new_var)

        # Un renommage/remplacement de FCStd non sauvegardé concernait la
        # variante précédemment affichée : abandonné en changeant de variante.
        self._pending_fcstd_rename = None
        self._fcstd_replaced = False
        self._fcstd_replaced_old_name = ''

        if not is_new_var:
            vcfg = self.components.get(comp_key, {}).get(
                'cfg', {}).get(cat, {}).get(
                self.meta_variant_combo.currentText(), {})
            self.fcstd_path    = vcfg.get('fcstd', '') or ''
            self.json_filename = os.path.basename(vcfg.get('json', ''))

        self._fcstd_target_name = os.path.basename(self.fcstd_path) \
            if self.fcstd_path else ''

        # Source FCStd : toujours visible (lecture seule + « Renommer… »/
        # « Choisir un autre… » pour une variante existante, « Parcourir… »
        # pour une nouvelle variante).
        self.meta_src_label.setVisible(True)
        self.meta_fcstd_edit.setVisible(True)
        self.meta_fcstd_edit.setText(self.fcstd_path)
        self.meta_fcstd_btn.setVisible(True)
        self.meta_fcstd_btn.setText(_t('ed_btn_browse') if is_new_var else _t('ed_btn_choose_other'))
        self.meta_fcstd_rename_btn.setVisible(not is_new_var and bool(self.fcstd_path))

    def _on_family_changed(self):
        """
        Famille (combo 1) = comp_key (dossier).
        Catégorie (combo 2) = clés cfg du comp_key sélectionné.
        """
        comp_key   = self.meta_family_combo.currentText()
        is_new_fam = (comp_key == _SENTINEL_NEW_FAMILY)
        self.meta_family_edit.setVisible(is_new_fam)

        # Catégorie = familles du comp_key sélectionné
        self.meta_comp_combo.blockSignals(True)
        self.meta_comp_combo.clear()
        if not is_new_fam and comp_key in self.components:
            for fam in sorted(
                    self.components[comp_key].get('cfg', {}).keys()):
                self.meta_comp_combo.addItem(fam)
        self.meta_comp_combo.addItem(_SENTINEL_NEW_CATEGORY)
        if is_new_fam:
            self.meta_comp_combo.setCurrentIndex(
                self.meta_comp_combo.count() - 1)
        self.meta_comp_combo.blockSignals(False)
        self.meta_comp_edit.setVisible(
            self.meta_comp_combo.currentText() == _SENTINEL_NEW_CATEGORY
            and not is_new_fam)
        self._on_category_changed()

    def _on_category_changed(self):
        """Catégorie changée → mise à jour variante."""
        comp_key   = self.meta_family_combo.currentText()
        cat        = self.meta_comp_combo.currentText()   # famille config.json
        is_new_fam = (comp_key == _SENTINEL_NEW_FAMILY)
        is_new_cat = (cat == _SENTINEL_NEW_CATEGORY)
        self.meta_comp_edit.setVisible(is_new_cat and not is_new_fam)

        self.meta_variant_combo.blockSignals(True)
        self.meta_variant_combo.clear()
        if not is_new_cat and not is_new_fam and comp_key in self.components:
            for var in sorted(
                    self.components[comp_key].get('cfg',{}).get(cat,{}).keys()):
                self.meta_variant_combo.addItem(var)
        self.meta_variant_combo.addItem(_SENTINEL_NEW_VARIANT)
        if is_new_cat or is_new_fam:
            self.meta_variant_combo.setCurrentIndex(
                self.meta_variant_combo.count() - 1)
        self.meta_variant_combo.blockSignals(False)
        self.meta_variant_edit.setVisible(
            self.meta_variant_combo.currentText() == _SENTINEL_NEW_VARIANT)
        self._on_variant_changed()

    def _on_variant_changed(self):
        """Variante changée → afficher/masquer source + JSON."""
        comp_key   = self.meta_family_combo.currentText()
        cat        = self.meta_comp_combo.currentText()
        var        = self.meta_variant_combo.currentText()
        is_new_var = (var == _SENTINEL_NEW_VARIANT)
        self.meta_variant_edit.setVisible(is_new_var)
        self.meta_json_label.setVisible(is_new_var)
        self.meta_json_edit.setVisible(is_new_var)

        # Un renommage/remplacement de FCStd non sauvegardé concernait la
        # variante précédemment affichée : abandonné en changeant de variante.
        self._pending_fcstd_rename = None
        self._fcstd_replaced = False
        self._fcstd_replaced_old_name = ''

        if not is_new_var and comp_key in self.components:
            vcfg = self.components[comp_key].get(
                'cfg',{}).get(cat,{}).get(var,{})
            self.fcstd_path    = vcfg.get('fcstd','') or ''
            jf = vcfg.get('json','')
            if jf: self.json_filename = os.path.basename(jf)
            # Libellé d'affichage déjà personnalisé pour cette variante,
            # le cas échéant (texte purement cosmétique, cf. translations.json)
            if hasattr(self, 'meta_variant_label_edit'):
                self.meta_variant_label_edit.setText(read_variant_label(var))
        elif hasattr(self, 'meta_variant_label_edit'):
            self.meta_variant_label_edit.clear()

        self._fcstd_target_name = os.path.basename(self.fcstd_path) \
            if self.fcstd_path else ''

        # Source FCStd : toujours visible (lecture seule + « Renommer… »/
        # « Choisir un autre… » pour une variante existante, « Parcourir… »
        # pour une nouvelle variante).
        self.meta_src_label.setVisible(True)
        self.meta_fcstd_edit.setVisible(True)
        self.meta_fcstd_edit.setText(self.fcstd_path)
        self.meta_fcstd_btn.setVisible(True)
        self.meta_fcstd_btn.setText(_t('ed_btn_browse') if is_new_var else _t('ed_btn_choose_other'))
        self.meta_fcstd_rename_btn.setVisible(not is_new_var and bool(self.fcstd_path))

    def _prepare_meta_page(self):
        """Peuple les combos famille et déclenche la cascade."""
        _pre_fam = self.source_family or self.family

        # Famille
        self.meta_family_combo.blockSignals(True)
        self.meta_family_combo.clear()
        # Famille = comp_key (dossiers : circlips, bearings, profilés)
        _pre_comp = self.source_comp or self.comp_key
        for k in sorted(self.components.keys()):
            # Si restrict_fcstd : ne proposer que les composants
            # ayant au moins une variante utilisant ce FCStd
            if self.restrict_fcstd:
                import os as _os
                _rf = _os.path.normcase(_os.path.normpath(self.restrict_fcstd))
                _has = any(
                    _os.path.normcase(_os.path.normpath(
                        v.get('fcstd', ''))) == _rf
                    for fam in self.components[k].get('cfg', {}).values()
                    for v in fam.values()
                    if isinstance(v, dict)
                )
                if not _has:
                    continue
            self.meta_family_combo.addItem(k)
        if not self.restrict_fcstd:
            self.meta_family_combo.addItem(_SENTINEL_NEW_FAMILY)
        if _pre_comp:
            fi = self.meta_family_combo.findText(_pre_comp)
            if fi >= 0: self.meta_family_combo.setCurrentIndex(fi)
        self.meta_family_combo.blockSignals(False)

        # Déclencher la cascade manuellement SANS signaux
        self._on_family_changed_silent()

    def _prepare_source_page(self):
        # new_ref : sélection catalogue existant
        # autres  : sélection FCStd (nouveau modèle)
        is_catalog = (self.action == 'new_ref')
        self.src_fcstd_widget.setVisible(not is_catalog)
        self.src_catalog_widget.setVisible(is_catalog)
        if is_catalog:
            self._populate_src_combo()


    def _browse_meta_fcstd(self):
        """Parcourir pour sélectionner un fichier FCStd : nouveau modèle pour
        une variante nouvelle, ou remplacement du FCStd d'une variante
        existante (_t('ed_btn_choose_other'))."""
        QtWidgets = self.QtWidgets
        # Ouvrir le navigateur dans le dossier du FCStd actuellement
        # sélectionné (variante existante) plutôt que toujours MACRO_DIR —
        # bien plus pratique quand les modèles sont rangés par sous-dossiers.
        start_dir = MACRO_DIR
        if self.fcstd_path:
            _d = os.path.dirname(self.fcstd_path)
            if _d and os.path.isdir(_d):
                start_dir = _d
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self.dlg, _t('ed_dlg_select_fcstd_title'), start_dir,
            "FreeCAD Files (*.FCStd *.fcstd)")
        if not path:
            return
        is_new_var = (
            self.meta_variant_combo.currentText() == _SENTINEL_NEW_VARIANT)
        old_basename = os.path.basename(self.fcstd_path) if self.fcstd_path else ''
        self.fcstd_path = path
        self.meta_fcstd_edit.setText(path)
        self._fcstd_target_name = os.path.basename(path)
        self._pending_fcstd_rename = None
        self.varset = read_varset_from_fcstd(path)
        self._refresh_varset_display()
        if not is_new_var:
            if not getattr(self, '_fcstd_replaced', False) and old_basename:
                # Mémoriser le nom D'ORIGINE (avant tout remplacement dans
                # cette session d'édition) pour pouvoir avertir à la
                # sauvegarde s'il existe aussi dans la bibliothèque externe.
                self._fcstd_replaced_old_name = old_basename
            self._fcstd_replaced = True
            self.meta_fcstd_rename_btn.setVisible(True)
            missing = [c['key'] for c in getattr(self, 'columns', [])
                       if not c.get('mute', False)
                       and c['key'] not in (self.varset or {})]
            if missing:
                QtWidgets.QMessageBox.warning(
                    self.dlg, _t('ed_err_params_missing_title'),
                    _t('ed_err_fcstd_missing_params_msg', missing=(", ".join(missing))))

    def _refresh_varset_display(self):
        """Affiche les paramètres VarSet détectés."""
        pass   # pas de liste dédiée dans ce layout compact

    def _fcstd_usages(self, comp_key, basename, exclude=None):
        """Liste les (famille, variante) de config.json référençant ce
        fichier FCStd (par nom), hors le couple `exclude` (famille, variante).
        Sert à avertir avant un renommage qui affecterait d'autres variantes
        partageant le même fichier physique."""
        usages = []
        raw = self.components.get(comp_key, {}).get('raw', {})
        for fam, variants in raw.items():
            if fam.startswith('_') or not isinstance(variants, dict):
                continue
            for var, vdata in variants.items():
                if var.startswith('_') or not isinstance(vdata, dict):
                    continue
                if exclude and (fam, var) == exclude:
                    continue
                if os.path.basename(vdata.get('fcstd', '') or '') == basename:
                    usages.append((fam, var))
        return usages

    def _fcstd_usages_on_disk(self, comp_key, basename):
        """Comme `_fcstd_usages`, mais relit config.json directement depuis
        le disque au lieu de `self.components` (qui date de l'ouverture de
        l'éditeur et n'est pas encore rafraîchi juste après un `_save()` —
        il montrerait encore l'ancien nom pour la variante qu'on vient de
        renommer/remplacer, faussant le diagnostic « encore utilisé »)."""
        usages = []
        config_path = os.path.join(STANDARDS_DIR, comp_key, 'config.json')
        if not os.path.exists(config_path):
            return usages
        try:
            with open(config_path, encoding='utf-8') as f:
                raw = json.load(f)
        except Exception:
            return usages
        for fam, variants in raw.items():
            if fam.startswith('_') or not isinstance(variants, dict):
                continue
            for var, vdata in variants.items():
                if var.startswith('_') or not isinstance(vdata, dict):
                    continue
                if os.path.basename(vdata.get('fcstd', '') or '') == basename:
                    usages.append((fam, var))
        return usages

    def _library_fcstd_path(self, comp_key, basename):
        """Chemin du fichier `basename` dans la bibliothèque externe pour ce
        composant, si une bibliothèque est configurée et qu'il y existe
        réellement — None sinon. Sert à avertir : un fichier renommé/remplacé
        localement mais toujours présent dans la bibliothèque sera
        automatiquement re-copié dans STANDARDS_DIR par la synchronisation
        silencieuse (sync._sync_library_to_sc_dir) au prochain lancement de
        la macro, tant qu'il n'est pas aussi traité côté bibliothèque
        (lecture seule depuis l'éditeur, jamais modifiée automatiquement)."""
        if not basename:
            return None
        lib = get_library_path()
        if not lib:
            return None
        p = os.path.join(lib, comp_key, 'models', basename)
        return p if os.path.exists(p) else None

    def _register_fcstd_leftover(self, saved, old_name, lib_path, local_path):
        """Note un fichier FCStd devenu orphelin (renommé/remplacé) mais
        encore présent quelque part — bibliothèque et/ou copie locale non
        nettoyée — et l'ajoute à la liste proposée en nettoyage juste après
        la sauvegarde (cf. `_offer_library_cleanup`). N'ajoute rien si le
        fichier n'existe plus nulle part."""
        if not lib_path and not local_path:
            return
        locations = []
        if lib_path:
            locations.append(f'la bibliothèque ({lib_path})')
        if local_path:
            locations.append(f'le dossier local ({local_path})')
        where = ' et '.join(locations)
        if lib_path:
            note = (_t('ed_note_lib_still_exists', old_name=(old_name), where=(where)))
        else:
            note = (_t('ed_note_lib_orphan', old_name=(old_name), where=(where)))
        saved.append(note)
        self._pending_library_cleanup.append((old_name, lib_path, local_path))

    def _offer_library_cleanup(self, QtWidgets):
        """Après une sauvegarde ayant renommé/remplacé un FCStd resté présent
        quelque part (bibliothèque externe et/ou copie locale orpheline —
        cf. avertissement dans `saved`), propose — via un bouton dédié,
        jamais automatiquement — de le supprimer, dans les DEUX emplacements
        concernés en une seule confirmation si les deux existent. C'est la
        seule façon d'empêcher un fichier encore en bibliothèque d'être
        recopié en local à la prochaine synchronisation. Avant suppression,
        re-vérifie qu'aucune variante (même une autre famille du composant)
        ne référence encore ce nom dans config.json — sécurité en cas de
        modifications supplémentaires entre le renommage/remplacement et ce
        moment."""
        pending = getattr(self, '_pending_library_cleanup', [])
        self._pending_library_cleanup = []
        comp_key = self.comp_key
        for old_name, lib_path, local_path in pending:
            lib_path   = lib_path   if lib_path   and os.path.exists(lib_path)   else None
            local_path = local_path if local_path and os.path.exists(local_path) else None
            if not lib_path and not local_path:
                continue   # déjà supprimé(s) entre-temps
            # Relire config.json depuis le disque plutôt que d'utiliser
            # self.components : celui-ci vient de discover_components() à
            # l'ouverture de l'éditeur et n'est pas encore rechargé juste
            # après un _save() — il montrerait encore l'ANCIEN nom pour la
            # variante qu'on vient précisément de renommer/remplacer.
            still_used = self._fcstd_usages_on_disk(comp_key, old_name)
            locations = []
            if lib_path:   locations.append(f'la bibliothèque ({lib_path})')
            if local_path: locations.append(f'le dossier local ({local_path})')
            where = ' et '.join(locations)

            box = QtWidgets.QMessageBox(self.dlg)
            box.setIcon(QtWidgets.QMessageBox.Warning)
            box.setWindowTitle(_t('ed_win_fcstd_obsolete_title'))
            if still_used:
                detail = "\n".join(f" • {f} / {v}" for f, v in still_used)
                box.setText(
                    _t('ed_msg_fcstd_still_used', old_name=(old_name), where=(where), detail=(detail)))
                box.setStandardButtons(QtWidgets.QMessageBox.Ok)
                box.exec()
                continue
            box.setText(
                _t('ed_msg_fcstd_orphan_offer', old_name=(old_name), where=(where)))
            if lib_path and local_path:
                delete_label = _t('ed_btn_delete_both')
            elif lib_path:
                delete_label = _t('ed_btn_delete_lib')
            else:
                delete_label = _t('ed_btn_delete_local')
            btn_delete = box.addButton(
                delete_label, QtWidgets.QMessageBox.DestructiveRole)
            btn_keep = box.addButton(
                _t('ed_btn_keep'), QtWidgets.QMessageBox.RejectRole)
            box.setDefaultButton(btn_keep)
            box.exec()
            if box.clickedButton() is btn_delete:
                removed, errors = [], []
                for p in (lib_path, local_path):
                    if not p:
                        continue
                    try:
                        os.remove(p)
                        removed.append(p)
                    except Exception as e:
                        errors.append(f'{p} : {e}')
                if removed:
                    self.save_status.setText(
                        self.save_status.text()
                        + _t('ed_status_removed', removed=(" ; ".join(removed))))
                if errors:
                    QtWidgets.QMessageBox.warning(
                        self.dlg, _t('ed_err_generic_title'),
                        "Suppression impossible pour :\n" + "\n".join(errors))

    def _rename_meta_fcstd(self):
        """Renomme le fichier FCStd de la variante courante, sans en changer
        le contenu. Si d'autres variantes du même composant référencent le
        même fichier, leur référence est mise à jour à la sauvegarde pour ne
        pas les laisser pointer vers un nom qui n'existe plus."""
        QtWidgets = self.QtWidgets
        if not self.fcstd_path:
            return
        current_name = getattr(self, '_fcstd_target_name', '') \
            or os.path.basename(self.fcstd_path)
        base, ext = os.path.splitext(current_name)
        new_base, ok = QtWidgets.QInputDialog.getText(
            self.dlg, _t('ed_dlg_rename_fcstd_title'),
            _t('ed_dlg_rename_fcstd_label'),
            QtWidgets.QLineEdit.Normal, base)
        if not ok:
            return
        new_base = new_base.strip()
        if not new_base:
            return
        if any(sep and sep in new_base for sep in (os.sep, os.altsep, '/')):
            QtWidgets.QMessageBox.warning(
                self.dlg, _t('ed_err_invalid_name_title'),
                _t('ed_err_invalid_name_path_sep'))
            return
        new_name = new_base + ext
        if new_name == current_name:
            return

        comp_key = self.meta_family_combo.currentText()
        dest = os.path.join(STANDARDS_DIR, comp_key, 'models', new_name)
        if os.path.exists(dest):
            QtWidgets.QMessageBox.warning(
                self.dlg, _t('ed_err_name_taken_title'),
                _t('ed_err_name_taken_msg', new_name=(new_name)))
            return

        shared = self._fcstd_usages(
            comp_key, current_name, exclude=(self.family, self.variant))
        if shared:
            detail = "\n".join(f" • {f} / {v}" for f, v in shared)
            resp = QtWidgets.QMessageBox.question(
                self.dlg, _t('ed_q_fcstd_shared_title'),
                _t('ed_q_fcstd_shared_msg', current_name=(current_name), count=(len(shared)), detail=(detail)),
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No)
            if resp != QtWidgets.QMessageBox.Yes:
                return

        # Si un renommage était déjà en attente depuis l'ouverture de cette
        # variante, on rebase sur le nom d'ORIGINE (celui de config.json),
        # pas sur le nom intermédiaire déjà proposé — sinon un double
        # renommage successif perdrait la trace du nom réellement sur disque.
        pending = getattr(self, '_pending_fcstd_rename', None)
        old_name = pending[0] if pending else current_name
        self._pending_fcstd_rename = (old_name, new_name)
        self._fcstd_target_name = new_name
        self.meta_fcstd_edit.setText(
            os.path.join(os.path.dirname(self.fcstd_path), new_name))
        self.save_status.setStyleSheet('color: #b8860b;')
        self.save_status.setText(
            _t('ed_status_rename_planned', old_name=(old_name), new_name=(new_name)))


    def _prepare_columns_page(self):
        QtWidgets = self.QtWidgets

        # Ne reconstruire col_table depuis la source (VarSet / variante
        # source) que la première fois qu'on arrive sur cette page pour
        # cette variante. Sinon, un aller-retour Métadonnées ↔ Colonnes
        # écrase silencieusement les libellés que l'utilisateur vient de
        # personnaliser (ex : renommer "DClou" en "Diamètre de la tige").
        _sig = (self.comp_key, self.family, self.variant, self.fcstd_path)
        if self._columns_page_sig == _sig:
            return
        self._columns_page_sig = _sig

        self.col_table.setRowCount(0)

        is_new_var = (
            self.meta_variant_combo.currentText() == _SENTINEL_NEW_VARIANT
            if hasattr(self, 'meta_variant_combo') else True)

        # Variante EXISTANTE (quel que soit self.action — 'edit', 'new_ref',
        # etc.) : self.family/self.variant pointent déjà sur elle-même → on
        # réutilise _load_columns_from_config() (seule source qui relit aussi
        # _legende pour les libellés déjà personnalisés, et porte
        # show/mute/requis) plutôt que dupliquer cette logique ici.
        if not is_new_var and self.family and self.variant:
            self._load_columns_from_config()
            col_defs = self.columns
        # 'from_family' : on démarre une NOUVELLE variante (self.variant est
        # encore vide à ce stade) en copiant la structure de colonnes d'une
        # variante source différente.
        elif self.action in ('from_family', 'edit') and self.source_comp:
            comp_data = self.components.get(self.source_comp, {})
            src_cfg   = comp_data.get('cfg', {}).get(
                self.source_family, {}).get(self.source_variant, {})
            src_cols  = src_cfg.get('columns', [])
            # Format stocké par _save() : (label, key, mute, show, requis) —
            # pas un simple couple (label, key). Un dépliage à 2 valeurs
            # plantait (ValueError) dès qu'une variante avait déjà été
            # sauvegardée.
            col_defs = []
            for col_entry in src_cols:
                cl = col_entry[0]
                ck = col_entry[1]
                if ck == 'ref':
                    continue
                col_mute = bool(col_entry[2]) if len(col_entry) > 2 else False
                col_show = bool(col_entry[3]) if len(col_entry) > 3 else True
                col_req  = bool(col_entry[4]) if len(col_entry) > 4 else (not col_mute)
                col_defs.append({'key': ck, 'label': cl, 'mute': col_mute,
                                  'show': col_show, 'required': col_req})
            # Ajouter les params VarSet non encore présents (non-muets : ce sont des params réels)
            for k in self.varset:
                if not any(c['key'] == k for c in col_defs):
                    col_defs.append({'key': k, 'label': k, 'mute': False,
                                      'show': True, 'required': True})
        else:
            # Depuis VarSet brut
            col_defs = [
                {'key': k, 'label': k, 'mute': False, 'show': True, 'required': True}
                for k in self.varset
            ]

        for cd in col_defs:
            self._add_col_row(cd['key'], cd['label'],
                               show=cd.get('show', True), mute=cd['mute'],
                               required=cd.get('required'))

    def _prepare_data_page(self):
        QtWidgets = self.QtWidgets

        is_new_var = (
            self.meta_variant_combo.currentText() == _SENTINEL_NEW_VARIANT
            if hasattr(self, 'meta_variant_combo') else True)

        # Signature de la variante actuellement affichée. Tant qu'elle ne
        # change pas, on est en train de REVISITER cette page (ex : retour
        # à Colonnes/Métadonnées puis avance à nouveau) — il ne faut alors
        # ni recharger les colonnes depuis leur source froide (disque ou
        # VarSet), ni effacer les lignes déjà saisies/importées : c'était
        # la cause du tableau qui se retrouve vide après un simple
        # aller-retour dans le wizard (ex : après un import CSV).
        _sig = (self.comp_key, self.family, self.variant,
                self.fcstd_path, is_new_var)
        _first_visit = (self._data_page_sig != _sig)

        _preserved_rows = None
        if not _first_visit and self._data_table_initialized:
            _preserved_rows = self._snapshot_data_rows()

        # La page Colonnes (PAGE_COLUMNS) est désormais TOUJOURS visitée avant
        # cette page, pour une variante nouvelle comme existante — elle est la
        # source unique de self.columns (y compris une suppression de colonne
        # faite là-bas). On se contente donc de la relire depuis col_table.
        # Filet de sécurité : si col_table est vide (page Colonnes non
        # visitée — ne devrait plus arriver en usage normal), on retombe sur
        # un chargement depuis la config pour une variante existante.
        if not is_new_var and self.col_table.rowCount() == 0:
            self._load_columns_from_config()
            self.col_table.setRowCount(0)
            for col in self.columns:
                self._add_col_row(col['key'], col['label'],
                                  show=col.get('show', True),
                                  mute=col.get('mute', False),
                                  required=col.get('required'))
        else:
            self._read_columns_from_table()

        self._data_page_sig = _sig

        # En-têtes — TOUTES les colonnes (visible ou non) pour pouvoir les réactiver
        # La visibilité dans le sélecteur est contrôlée par les coches, pas par le filtre ici
        # 'Préféré' toujours en TOUTE PREMIÈRE colonne (position fixe 0),
        # 'Référence' juste après (position fixe 1) — cf. FAVORI_COL/REF_COL.
        headers  = [self.FAVORI_LABEL, _t('editor_col_reference')] + [c['label'] for c in self.columns]
        self.data_table.setColumnCount(len(headers))
        self.data_table.setHorizontalHeaderLabels(headers)
        self.data_table.horizontalHeader().setStretchLastSection(False)

        # Ajuster chaque colonne au max(libellé, contenu le plus large)
        fm  = self.data_table.horizontalHeader().fontMetrics()
        fm2 = self.data_table.fontMetrics()
        self.data_table.horizontalHeader().setStretchLastSection(False)
        for ci in range(self.data_table.columnCount()):
            # Largeur du libellé
            hdr = self.data_table.horizontalHeaderItem(ci)
            w   = fm.horizontalAdvance(hdr.text()) + 24 if hdr else 80
            # Largeur max du contenu des cellules
            for row in range(self.data_table.rowCount()):
                cell = self.data_table.item(row, ci)
                if cell:
                    cw = fm2.horizontalAdvance(cell.text()) + 16
                    w  = max(w, cw)
            self.data_table.setColumnWidth(ci, max(w, 60))
        self.data_table.horizontalHeader().setStretchLastSection(True)

        # ── Charger les données D'ABORD, puis ajouter ligne coches ──
        if _preserved_rows is not None:
            # Revisite avec données déjà en session (saisies ou importées
            # d'un CSV) — on les restaure telles quelles, par clé de colonne.
            self.data_table.setRowCount(0)
            self._restore_data_rows(_preserved_rows)
        elif not is_new_var:
            self._load_existing_data()
            # En mode ajout de référence : ajouter la nouvelle ligne
            if self.new_ref_name:
                if self.new_ref_vals:
                    self.varset.update(self.new_ref_vals)
                self._data_add_row(prefill=True)
                item = self.data_table.item(
                    self.data_table.rowCount() - 1, self.REF_COL)
                if item:
                    item.setText(self.new_ref_name)
        else:
            self.data_table.setRowCount(0)
            if self.new_ref_vals:
                self.varset.update(self.new_ref_vals)
            self._data_add_row(prefill=bool(self.new_ref_vals) or True)
            if self.new_ref_name:
                item = self.data_table.item(
                    self.data_table.rowCount() - 1, self.REF_COL)
                if item:
                    item.setText(self.new_ref_name)

        # ── Ligne 0 : coches de visibilité (insérée après les données)
        # Checked = visible dans le sélecteur (show=True)
        # Unchecked = caché dans le sélecteur (show=False)
        # NB : mute (non-appliqué au VarSet) est indépendant de la visibilité
        self.data_table.insertRow(0)
        # Cellule de garde pour la colonne Préféré (col. 0) — pas de coche
        # de visibilité ici (elle n'est jamais une colonne du sélecteur).
        lbl_fav = self.QtWidgets.QTableWidgetItem('')
        lbl_fav.setFlags(self.QtCore.Qt.ItemIsEnabled)
        lbl_fav.setBackground(
            self.QtGui.QBrush(self.QtGui.QColor(60, 60, 80)))
        self.data_table.setItem(0, self.FAVORI_COL, lbl_fav)
        lbl_vis = self.QtWidgets.QTableWidgetItem('Visible')
        lbl_vis.setFlags(self.QtCore.Qt.ItemIsEnabled)
        lbl_vis.setBackground(
            self.QtGui.QBrush(self.QtGui.QColor(60, 60, 80)))
        self.data_table.setItem(0, self.REF_COL, lbl_vis)
        for ci, col in enumerate(self.columns, start=self.REF_COL + 1):
            chk = self.QtWidgets.QTableWidgetItem()
            chk.setFlags(
                self.QtCore.Qt.ItemIsUserCheckable |
                self.QtCore.Qt.ItemIsEnabled)
            # show=True (défaut) → Checked ; show=False → Unchecked
            chk.setCheckState(
                self.QtCore.Qt.Checked if col.get('show', True)
                else self.QtCore.Qt.Unchecked)
            chk.setBackground(
                self.QtGui.QBrush(self.QtGui.QColor(60, 60, 80)))
            self.data_table.setItem(0, ci, chk)
        self.data_table.setRowHeight(0, 28)

        # En-têtes éditables par double-clic (option B)
        self.data_table.resizeColumnsToContents()
        for ci in range(self.data_table.columnCount()):
            self.data_table.setColumnWidth(
                ci, self.data_table.columnWidth(ci) + 16)
        def _on_header_double_click(col):
            if col == self.REF_COL or col == self._favori_col():
                return   # Référence et Préféré non renommables
            current = self.data_table.horizontalHeaderItem(col)
            current_text = current.text() if current else ''
            new_label, ok = self.QtWidgets.QInputDialog.getText(
                self.data_table,
                _t('ed_dlg_rename_column_title'),
                _t('ed_dlg_rename_column_label', col=(col)),
                text=current_text)
            if ok and new_label.strip():
                new_label = new_label.strip()
                self.data_table.horizontalHeaderItem(col).setText(new_label)
                # Mettre à jour self.columns
                idx = col - (self.REF_COL + 1)
                if 0 <= idx < len(self.columns):
                    self.columns[idx]['label'] = new_label
                # Répercuter sur col_table (page Colonnes) — sinon le rename
                # est écrasé au prochain _read_columns_from_table() (aperçu,
                # sauvegarde, ou retour/avance dans le wizard), qui régénère
                # self.columns à partir de col_table et non de data_table.
                ct_row = idx
                if 0 <= ct_row < self.col_table.rowCount():
                    lbl_item = self.col_table.item(ct_row, 2)
                    if lbl_item:
                        lbl_item.setText(new_label)
                print(f'[Editor] Label colonne {col} → {new_label!r}')

        hdr = self.data_table.horizontalHeader()
        import warnings as _w
        with _w.catch_warnings():
            _w.simplefilter('ignore', RuntimeWarning)
            try:
                hdr.sectionDoubleClicked.disconnect()
            except Exception:
                pass
        hdr.sectionDoubleClicked.connect(_on_header_double_click)

        # Chargement déplacé avant la ligne de coches

        self._data_table_initialized = True

    def _snapshot_data_rows(self):
        """
        Capture les lignes de data_table (hors ligne 0 = coches de
        visibilité) par référence + valeurs indexées par CLÉ de colonne
        (jamais par position ni par libellé) — pour pouvoir les restaurer
        sans perte après une reconstruction des colonnes, par exemple
        lors d'un retour en arrière dans le wizard suivi d'une ré-avance.
        """
        label_to_key = {c['label']: c['key'] for c in self.columns}
        header_to_key = {}
        fav_ci = self._favori_col()
        for ci in range(self.REF_COL + 1, self.data_table.columnCount()):
            if ci == fav_ci:
                continue   # Préféré : traité à part (case à cocher, pas texte)
            h = self.data_table.horizontalHeaderItem(ci)
            text = h.text() if h else ''
            # Repli sur le texte lui-même si jamais l'en-tête est déjà
            # la clé brute (colonne pas encore renommée).
            header_to_key[ci] = label_to_key.get(text, text)

        rows = []
        for r in range(1, self.data_table.rowCount()):   # ligne 0 = coches
            ref_item = self.data_table.item(r, self.REF_COL)
            ref = ref_item.text() if ref_item else ''
            if not ref.strip():
                continue
            vals = {}
            for ci, key in header_to_key.items():
                if not key:
                    continue
                item = self.data_table.item(r, ci)
                vals[key] = item.text() if item else ''
            if fav_ci >= 0:
                fav_item = self.data_table.item(r, fav_ci)
                vals[self.FAVORI_KEY] = bool(
                    fav_item and
                    fav_item.checkState() == self.QtCore.Qt.Checked)
            rows.append((ref, vals))
        return rows

    def _restore_data_rows(self, rows):
        """
        Réinjecte dans data_table les lignes capturées par
        _snapshot_data_rows, en réaffectant chaque valeur par clé de
        colonne — robuste à un renommage de libellé ou à un
        réordonnancement des colonnes survenu entre-temps.
        """
        QtWidgets = self.QtWidgets
        key_to_ci = {col['key']: ci
                     for ci, col in enumerate(self.columns, start=self.REF_COL + 1)}
        fav_ci = self._favori_col()
        for ref, vals in rows:
            r = self.data_table.rowCount()
            self.data_table.insertRow(r)
            self.data_table.setItem(r, self.REF_COL, QtWidgets.QTableWidgetItem(ref))
            for key, txt in vals.items():
                if key == self.FAVORI_KEY:
                    continue   # traité séparément ci-dessous
                ci = key_to_ci.get(key)
                if ci is not None:
                    self.data_table.setItem(
                        r, ci, QtWidgets.QTableWidgetItem(txt))
            if fav_ci >= 0:
                is_fav = bool(vals.get(self.FAVORI_KEY, False))
                self.data_table.setItem(
                    r, fav_ci, self._make_favori_item(is_fav))
                self._set_row_bold(r, is_fav)

    def _prepare_preview_page(self):
        # Synchroniser self.columns depuis col_table (ordre + show + mute)
        # avant de lire les données — garantit que la légende et l'ordre
        # du preview reflètent l'état visuel actuel (après éventuels ← →)
        self._read_columns_from_table()
        self._read_data_from_table()
        json_str = self._build_json_preview()
        self.preview_text.setPlainText(json_str)
        self.save_status.setText("")

    # ──────────────────────────────────────────────────────────
    # Gestion page Source
    # ──────────────────────────────────────────────────────────
    def _browse_fcstd(self):
        QtWidgets = self.QtWidgets
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self.dlg, _t('ed_dlg_select_fcstd_title'), MACRO_DIR,
            "FreeCAD Files (*.FCStd *.fcstd)")
        if path:
            self.fcstd_path = path
            self.fcstd_edit.setText(path)
            self.varset = read_varset_from_fcstd(path)
            self._refresh_varset_list()

    def _refresh_varset_list(self):
        self.varset_list.clear()
        for k, v in self.varset.items():
            self.varset_list.addItem(f"{k} = {v}")

    def _populate_src_combo(self):
        self.src_comp_combo.blockSignals(True)
        self.src_comp_combo.clear()
        for k in sorted(self.components.keys()):
            self.src_comp_combo.addItem(k)
        # Pré-sélectionner la catégorie — signaux bloqués
        _pre = self.source_comp or self.comp_key
        if _pre:
            idx = self.src_comp_combo.findText(_pre)
            if idx >= 0:
                self.src_comp_combo.setCurrentIndex(idx)
        self.src_comp_combo.blockSignals(False)
        # Appel unique avec source_family encore valide
        self._src_comp_changed_preselect()

    def _src_comp_changed_preselect(self):
        """Peuple famille + pré-sélectionne depuis source_family/family."""
        ck = self.src_comp_combo.currentText()
        self.src_family_combo.blockSignals(True)
        self.src_family_combo.clear()
        comp = self.components.get(ck, {})
        for fam in sorted(comp.get('cfg', {}).keys()):
            self.src_family_combo.addItem(fam)
        # Pré-sélectionner la famille — signaux bloqués
        _pre_f = self.source_family or self.family
        if _pre_f:
            fi = self.src_family_combo.findText(_pre_f)
            if fi >= 0:
                self.src_family_combo.setCurrentIndex(fi)
        self.src_family_combo.blockSignals(False)
        self._src_family_changed()

    def _src_comp_changed(self):
        """Appelée par signal currentIndexChanged — pas de pré-sélection."""
        ck = self.src_comp_combo.currentText()
        self.src_family_combo.blockSignals(True)
        self.src_family_combo.clear()
        comp = self.components.get(ck, {})
        for fam in sorted(comp.get('cfg', {}).keys()):
            self.src_family_combo.addItem(fam)
        if self.src_family_combo.count() > 0:
            self.src_family_combo.setCurrentIndex(0)
        self.src_family_combo.blockSignals(False)
        self._src_family_changed()

    def _src_family_changed(self):
        ck  = self.src_comp_combo.currentText()
        fam = self.src_family_combo.currentText()
        self.src_variant_combo.clear()
        variants = self.components.get(ck, {}).get('cfg', {}).get(fam, {})
        for var in sorted(variants.keys()):
            self.src_variant_combo.addItem(var)
        # Charger le VarSet si mode 'from_family'
        if self.action == 'from_family':
            var = self.src_variant_combo.currentText()
            if var:
                vcfg = variants.get(var, {})
                fcstd = vcfg.get('fcstd', '')
                if fcstd and os.path.exists(fcstd):
                    self.fcstd_path = fcstd
                    self.varset     = read_varset_from_fcstd(fcstd)
                    self._refresh_varset_list()

    # ──────────────────────────────────────────────────────────
    # Gestion page Métadonnées
    # ──────────────────────────────────────────────────────────
    def _meta_comp_changed(self):
        ck = self.meta_comp_combo.currentText()
        is_new = ck in (_SENTINEL_NEW_GENERIC, _SENTINEL_NEW_CATEGORY)
        self.meta_comp_edit.setVisible(is_new)
        self.meta_family_combo.blockSignals(True)
        self.meta_family_combo.clear()
        if not is_new and ck in self.components:
            for fam in sorted(self.components[ck]['cfg'].keys()):
                self.meta_family_combo.addItem(fam)
        self.meta_family_combo.addItem(_SENTINEL_NEW_GENERIC)
        # Pré-sélectionner la famille source ou family courante
        _pre_f = self.source_family or self.family
        if _pre_f:
            fi = self.meta_family_combo.findText(_pre_f)
            if fi >= 0:
                self.meta_family_combo.setCurrentIndex(fi)
        self.meta_family_combo.blockSignals(False)
        self._meta_family_changed()

    def _meta_family_changed(self):
        fam = self.meta_family_combo.currentText()
        self.meta_family_edit.setVisible(fam == _SENTINEL_NEW_GENERIC)

    # ──────────────────────────────────────────────────────────
    # Gestion page Colonnes
    # ──────────────────────────────────────────────────────────
    def _add_col_row(self, key, label, show=True, mute=False, required=None):
        QtWidgets = self.QtWidgets
        QtCore    = self.QtCore
        row = self.col_table.rowCount()
        self.col_table.insertRow(row)
        # Par défaut (si non précisé) : un paramètre muet est optionnel,
        # un paramètre réel (VarSet) est requis — reproduit le comportement
        # historique (une valeur vide n'était omise que pour les muets).
        if required is None:
            required = not mute

        # Colonne 0 : case à cocher Afficher
        chk_show = QtWidgets.QTableWidgetItem()
        chk_show.setFlags(QtCore.Qt.ItemIsUserCheckable | QtCore.Qt.ItemIsEnabled)
        chk_show.setCheckState(
            QtCore.Qt.Checked if show else QtCore.Qt.Unchecked)
        self.col_table.setItem(row, 0, chk_show)

        # Colonne 1 : clé JSON (non éditable)
        key_item = QtWidgets.QTableWidgetItem(key)
        key_item.setFlags(QtCore.Qt.ItemIsEnabled)
        self.col_table.setItem(row, 1, key_item)

        # Colonne 2 : label (éditable)
        self.col_table.setItem(row, 2, QtWidgets.QTableWidgetItem(label))

        # Colonne 3 : muet (case à cocher)
        chk_mute = QtWidgets.QTableWidgetItem()
        chk_mute.setFlags(QtCore.Qt.ItemIsUserCheckable | QtCore.Qt.ItemIsEnabled)
        chk_mute.setCheckState(
            QtCore.Qt.Checked if mute else QtCore.Qt.Unchecked)
        self.col_table.setItem(row, 3, chk_mute)

        # Colonne 4 : requis (case à cocher) — si décoché, une valeur vide
        # pour ce paramètre est omise du JSON plutôt qu'enregistrée comme "".
        chk_req = QtWidgets.QTableWidgetItem()
        chk_req.setFlags(QtCore.Qt.ItemIsUserCheckable | QtCore.Qt.ItemIsEnabled)
        chk_req.setCheckState(
            QtCore.Qt.Checked if required else QtCore.Qt.Unchecked)
        self.col_table.setItem(row, 4, chk_req)

    def _add_mute_param(self):
        QtWidgets = self.QtWidgets
        name, ok = QtWidgets.QInputDialog.getText(
            self.dlg, _t('ed_dlg_mute_param_title'), _t('ed_dlg_mute_param_label'))
        if ok and name.strip():
            self._add_col_row(name.strip(), name.strip(), show=False, mute=True)

    def _del_column(self):
        row = self.col_table.currentRow()
        if row >= 0:
            self.col_table.removeRow(row)

    def _col_up(self):
        row = self.col_table.currentRow()
        if row > 0:
            self._swap_col_rows(row, row - 1)
            self.col_table.selectRow(row - 1)

    def _col_down(self):
        row = self.col_table.currentRow()
        if row < self.col_table.rowCount() - 1:
            self._swap_col_rows(row, row + 1)
            self.col_table.selectRow(row + 1)

    def _swap_col_rows(self, a, b):
        QtCore = self.QtCore
        for col in range(self.col_table.columnCount()):
            ia = self.col_table.item(a, col)
            ib = self.col_table.item(b, col)
            if ia and ib:
                ta, tb = ia.text(), ib.text()
                ca = ia.checkState() if ia.flags() & QtCore.Qt.ItemIsUserCheckable \
                    else None
                cb = ib.checkState() if ib.flags() & QtCore.Qt.ItemIsUserCheckable \
                    else None
                ia.setText(tb); ib.setText(ta)
                if ca is not None and cb is not None:
                    ia.setCheckState(cb); ib.setCheckState(ca)

    def _read_columns_from_table(self):
        QtCore = self.QtCore
        self.columns = []
        for row in range(self.col_table.rowCount()):
            show = self.col_table.item(row, 0)
            key  = self.col_table.item(row, 1)
            lbl  = self.col_table.item(row, 2)
            mute = self.col_table.item(row, 3)
            req  = self.col_table.item(row, 4)
            if key:
                is_mute = mute.checkState() == QtCore.Qt.Checked if mute else False
                self.columns.append({
                    'key':   key.text(),
                    'label': lbl.text() if lbl else key.text(),
                    'show':  show.checkState() == QtCore.Qt.Checked if show else True,
                    'mute':  is_mute,
                    'required':
                        (req.checkState() == QtCore.Qt.Checked) if req
                        else (not is_mute),
                })

    # ──────────────────────────────────────────────────────────
    # Gestion page Données
    # ──────────────────────────────────────────────────────────

    def _prepare_from_family_data(self):
        """
        Pour 'from_family' : charge colonnes et données existantes
        de toutes les variantes de la famille source.
        L'utilisateur voit le tableau complet et ajoute de nouvelles lignes.
        """
        QtWidgets = self.QtWidgets
        comp_data = self.components.get(self.source_comp, {})
        family_cfg = comp_data.get('cfg', {}).get(self.source_family, {})

        # Récupérer les colonnes depuis la première variante disponible
        self.columns = []
        first_vcfg = None
        for var, vcfg in family_cfg.items():
            if first_vcfg is None:
                first_vcfg = vcfg
            for col_entry in vcfg.get('columns', []):
                col_label = col_entry[0]
                col_key   = col_entry[1]
                col_mute  = bool(col_entry[2]) if len(col_entry) > 2 else False
                col_show  = bool(col_entry[3]) if len(col_entry) > 3 else True
                if col_key != 'ref' and not any(
                        c['key'] == col_key for c in self.columns):
                    self.columns.append({
                        'key':   col_key,
                        'label': col_label,
                        'show':  col_show,
                        'mute':  col_mute,
                    })

        # Charger le VarSet depuis le FCStd de la famille
        if first_vcfg and first_vcfg.get('fcstd'):
            self.fcstd_path = first_vcfg['fcstd']
            self.varset     = read_varset_from_fcstd(self.fcstd_path)

        # Sécurité : si TOUTES les colonnes ont show=False (config corrompue),
        # les remettre à True pour ne pas cacher toutes les données
        if self.columns and not any(c.get('show', True) for c in self.columns):
            for c in self.columns:
                c['show'] = True

        # Métadonnées : même catégorie/famille, variante à saisir
        self.comp_key      = self.source_comp
        self.family        = self.source_family
        self.variant       = ''   # à renseigner par l'utilisateur
        self.json_filename = ''

        # Construire le tableau : Référence + paramètres (sans colonne Variante)
        # La variante = ref automatiquement à la sauvegarde
        headers = [_t('editor_col_reference')] + [c['label'] for c in self.columns]
        self.data_table.setColumnCount(len(headers))
        self.data_table.setHorizontalHeaderLabels(headers)
        self.data_table.horizontalHeader().setStretchLastSection(True)
        self.data_table.setColumnWidth(0, 160)

        # Charger toutes les variantes existantes (lecture seule, grisées)
        self.data_table.setRowCount(0)
        self._from_family_existing_rows = 0
        _shown_refs = set()   # éviter les doublons inter-variantes
        for var_name, vcfg in family_cfg.items():
            json_path = vcfg.get('json', '')
            if not json_path or not os.path.exists(json_path):
                continue
            try:
                with open(json_path, encoding='utf-8') as f:
                    data = json.load(f)
                for ref_key, vals in data.items():
                    if ref_key.startswith('_'):
                        continue
                    if ref_key in _shown_refs:
                        continue   # doublon inter-variantes → ignorer
                    _shown_refs.add(ref_key)
                    row = self.data_table.rowCount()
                    self.data_table.insertRow(row)
                    # Référence
                    item_ref = QtWidgets.QTableWidgetItem(ref_key)
                    item_ref.setFlags(self.QtCore.Qt.ItemIsEnabled)   # lecture seule
                    item_ref.setForeground(
                        self.QtGui.QBrush(self.QtGui.QColor(128, 128, 128)))
                    self.data_table.setItem(row, 0, item_ref)
                    # Valeurs des paramètres
                    for col_idx, col in enumerate(self.columns, start=1):
                        v = str(vals.get(col['key'], ''))
                        item_v = QtWidgets.QTableWidgetItem(v)
                        item_v.setFlags(self.QtCore.Qt.ItemIsEnabled)
                        item_v.setForeground(
                            self.QtGui.QBrush(self.QtGui.QColor(128, 128, 128)))
                        self.data_table.setItem(row, col_idx, item_v)
                    self._from_family_existing_rows += 1
            except Exception as e:
                print(f'[Editor] Chargement {var_name}: {e}')

        # Bannière d'instruction — la variante = ref (automatique)
        lbl_info = QtWidgets.QLabel(
            _t('ed_label_from_family_header', family=(self.source_family), count=(self._from_family_existing_rows)))
        lbl_info.setWordWrap(True)
        lbl_info.setStyleSheet('color: #aaa; font-style: italic; padding: 4px;')
        data_page = self.stack.widget(self.PAGE_DATA)
        if data_page and data_page.layout():
            data_page.layout().insertWidget(0, lbl_info)


    def _read_new_rows_from_family_table(self):
        """Lit uniquement les lignes éditables (nouvelles) du tableau from_family."""
        self.refs = {}
        existing = getattr(self, '_from_family_existing_rows', 0)
        # Index header → colonne (robuste aux réordonnements)
        header_to_ci = {}
        for ci in range(self.data_table.columnCount()):
            h = self.data_table.horizontalHeaderItem(ci)
            if h:
                header_to_ci[h.text()] = ci
        for row in range(existing, self.data_table.rowCount()):
            ref_item = self.data_table.item(row, 0)
            if not ref_item or not ref_item.text().strip():
                continue
            ref  = ref_item.text().strip()
            vals = {}
            for col in self.columns:
                ci = header_to_ci.get(col['label'], header_to_ci.get(col['key'], -1))
                if ci < 0:
                    continue
                item = self.data_table.item(row, ci)
                raw  = item.text().strip() if item else ''
                import re as _re
                raw_c = _re.sub(r'\s*(mm|deg|°|rad|kg|N|Pa|MPa)\s*$',
                                '', raw, flags=_re.IGNORECASE).strip()
                if not raw and not col.get('required', True):
                    continue
                try:
                    v = float(raw_c) if raw_c else float(raw)
                    vals[col['key']] = int(v) if v == int(v) else v
                except (ValueError, OverflowError):
                    vals[col['key']] = raw_c or raw
            self.refs[ref] = vals


    def _data_row_up(self):
        row = self.data_table.currentRow()
        existing = max(getattr(self, '_from_family_existing_rows', 0), 1)
        if row > existing:   # ne pas monter au-dessus des lignes existantes/coches
            self._swap_data_rows(row, row - 1)
            self.data_table.selectRow(row - 1)

    def _data_row_down(self):
        row = self.data_table.currentRow()
        if row >= 0 and row < self.data_table.rowCount() - 1:
            self._swap_data_rows(row, row + 1)
            self.data_table.selectRow(row + 1)

    def _on_data_col_header_clicked(self, col):
        """Met l'en-tête de la colonne cliquée en gras (sauf Référence/Préféré)."""
        if col == self.REF_COL or col == self._favori_col():
            return   # Référence et Préféré : non réordonnables
        self._clear_col_highlight()
        self._highlighted_col = col
        item = self.data_table.horizontalHeaderItem(col)
        if item:
            font = item.font()
            font.setBold(True)
            item.setFont(font)
        if self.data_table.rowCount() > 0:
            self.data_table.setCurrentCell(0, col)

    def _clear_col_highlight(self):
        """Remet l'en-tête de la colonne précédente en style normal."""
        col = getattr(self, '_highlighted_col', None)
        if col is None:
            return
        item = self.data_table.horizontalHeaderItem(col)
        if item:
            font = item.font()
            font.setBold(False)
            item.setFont(font)
        self._highlighted_col = None

    def _data_col_left(self):
        """Déplace la colonne sélectionnée vers la gauche (Préféré/Référence protégées)."""
        col = getattr(self, '_highlighted_col', None)
        if col is None or col <= self.REF_COL + 1:
            return
        self._clear_col_highlight()          # retire le gras AVANT le swap
        self._swap_data_cols(col, col - 1)
        self._highlighted_col = col - 1
        self._apply_col_bold(col - 1)        # remet le gras sur la nouvelle position
        if self.data_table.rowCount() > 0:
            self.data_table.setCurrentCell(0, col - 1)

    def _data_col_right(self):
        """Déplace la colonne sélectionnée vers la droite."""
        col = getattr(self, '_highlighted_col', None)
        if (col is None or col <= self.REF_COL
                or col >= self.data_table.columnCount() - 1):
            return
        self._clear_col_highlight()          # retire le gras AVANT le swap
        self._swap_data_cols(col, col + 1)
        self._highlighted_col = col + 1
        self._apply_col_bold(col + 1)        # remet le gras sur la nouvelle position
        if self.data_table.rowCount() > 0:
            self.data_table.setCurrentCell(0, col + 1)


    # ── Import CSV ────────────────────────────────────────────────────────

    def _import_csv(self):
        """Lance le wizard d'import CSV : sélection → mapping → import."""
        QtWidgets = self.QtWidgets
        try:
            path, _ = QtWidgets.QFileDialog.getOpenFileName(
                self.dlg, _t('ed_dlg_import_csv_title'), MACRO_DIR,
                "CSV (*.csv *.txt);;Tous (*.*)")
            if not path:
                return
            rows, headers, _ = self._parse_csv(path)
            if not rows:
                QtWidgets.QMessageBox.warning(
                    self.dlg, _t('ed_err_csv_empty_title'), _t('ed_err_csv_empty_msg', path=(path)))
                return
            mapping = self._csv_mapping_dialog(headers)
            if mapping is None:
                return
            added, updated = self._apply_csv_import(rows, mapping)
            QtWidgets.QMessageBox.information(
                self.dlg, _t('ed_info_csv_import_done_title'),
                _t('ed_info_csv_import_done_msg', added=(added), updated=(updated)))
        except Exception as _e:
            import traceback as _tb
            print(f'[CSV] Erreur import : {_e}\n{_tb.format_exc()}')
            QtWidgets.QMessageBox.critical(
                self.dlg, _t('ed_err_csv_import_title'),
                str(_e) + _t('ed_err_see_console'))

    def _parse_csv(self, path):
        """Lit un CSV : détection encodage + séparateur décimal."""
        import csv as _csv
        enc_ok = 'utf-8'
        sample = ''
        for enc in ('utf-8-sig', 'utf-8', 'latin-1', 'cp1252'):
            try:
                with open(path, encoding=enc, newline='') as f:
                    sample = f.read(2048)
                enc_ok = enc
                break
            except UnicodeDecodeError:
                pass
        try:
            dialect = _csv.Sniffer().sniff(sample, delimiters=',;\t|')
        except _csv.Error:
            dialect = _csv.excel
        rows, headers = [], []
        with open(path, encoding=enc_ok, newline='') as f:
            reader = _csv.DictReader(f, dialect=dialect)
            headers = list(reader.fieldnames or [])
            for row in reader:
                rows.append(dict(row))
        return rows, headers, enc_ok

    def _csv_mapping_dialog(self, csv_headers):
        """
        Dialog de mapping colonnes CSV → rôles catalogue.
        - Référence (clé primaire)
        - VarSet: <param>   → key=param VarSet, label=nom CSV
        - Extra             → key=label=nom personnalisable
        - Ignorer
        Retourne {csv_col: role} où role ∈ {'ref','varset:NOM','extra:NOM',None}
        Retourne None si annulé.
        """
        QtWidgets = self.QtWidgets
        varset_params = list(self.varset.keys()) if self.varset else []
        vset_lower    = {p.lower(): p for p in varset_params}

        dlg = QtWidgets.QDialog(self.dlg)
        dlg.setWindowTitle(_t('ed_win_csv_mapping_title'))
        dlg.setMinimumWidth(620)
        vlay = QtWidgets.QVBoxLayout(dlg)
        vlay.addWidget(QtWidgets.QLabel(
            _t('ed_label_csv_mapping_help')))

        # ── Tableau ─────────────────────────────────────────────────────────
        tbl = QtWidgets.QTableWidget(len(csv_headers), 3)
        tbl.setHorizontalHeaderLabels(
            [_t('ed_header_csv_column'), _t('ed_header_csv_role'), _t('ed_header_csv_varset_name')])
        tbl.horizontalHeader().setStretchLastSection(True)
        tbl.verticalHeader().setVisible(False)

        # Entrées du combo de rôle
        ROLE_REF   = _t('ed_role_ref')
        ROLE_EXTRA = "→ Champ extra"
        ROLE_SKIP  = "Ignorer"
        role_opts  = ([ROLE_REF]
                      + [f"→ VarSet : {p}" for p in varset_params]
                      + [ROLE_EXTRA, ROLE_SKIP])

        combos, name_edits = [], []

        for i, col in enumerate(csv_headers):
            # Col 1 : nom CSV (lecture seule)
            lbl_item = QtWidgets.QTableWidgetItem(col)
            try:
                lbl_item.setFlags(lbl_item.flags() & ~self.QtCore.Qt.ItemIsEditable)
            except Exception:
                pass
            tbl.setItem(i, 0, lbl_item)

            # Col 2 : combo rôle
            cmb = QtWidgets.QComboBox()
            for r in role_opts:
                cmb.addItem(r)
            col_l = col.strip().lower()

            # Auto-détection
            if col_l in ('reference', 'ref', 'nom', 'name'):
                cmb.setCurrentText(ROLE_REF)
            elif col_l in vset_lower:
                cmb.setCurrentText(f"→ VarSet : {vset_lower[col_l]}")
            else:
                cmb.setCurrentText(ROLE_EXTRA)

            combos.append(cmb)
            tbl.setCellWidget(i, 1, cmb)

            # Col 3 : paramètre VarSet (lecture seule) ou nom extra (éditable)
            edt = QtWidgets.QLineEdit()
            name_edits.append(edt)
            tbl.setCellWidget(i, 2, edt)

            def _update_name_field(_idx, _edt=edt, _cmb=cmb, _col=col):
                txt = _cmb.currentText()
                if txt.startswith('→ VarSet : '):
                    # Affiche le nom du param VarSet (lecture seule, informatif)
                    _edt.setText(txt[len('→ VarSet : '):])
                    _edt.setEnabled(False)
                    _edt.setPlaceholderText('')
                elif txt == ROLE_EXTRA:
                    # Nom affiché = nom CSV par défaut, modifiable
                    if not _edt.isEnabled():
                        _edt.setText(_col.strip())
                    _edt.setEnabled(True)
                    _edt.setPlaceholderText(_col.strip())
                else:
                    _edt.setText('')
                    _edt.setEnabled(False)
                    _edt.setPlaceholderText('')

            cmb.currentIndexChanged.connect(_update_name_field)
            _update_name_field(0)   # état initial

        vlay.addWidget(tbl)

        # Ajuster chaque colonne à son contenu (puis fixer un minimum raisonnable)
        tbl.resizeColumnsToContents()
        for c in range(tbl.columnCount()):
            tbl.setColumnWidth(c, max(tbl.columnWidth(c), 140))

        vlay.addWidget(QtWidgets.QLabel(
            _t('ed_label_varset_help')))

        bbox = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        bbox.accepted.connect(dlg.accept)
        bbox.rejected.connect(dlg.reject)
        vlay.addWidget(bbox)

        if dlg.exec_() != QtWidgets.QDialog.Accepted:
            return None

        # ── Construire le mapping ────────────────────────────────────────────
        mapping = {}
        for i, col in enumerate(csv_headers):
            role_text = combos[i].currentText()
            if role_text == ROLE_REF:
                mapping[col] = 'ref'
            elif role_text.startswith('→ VarSet : '):
                param_name   = role_text[len('→ VarSet : '):]
                mapping[col] = f'varset:{param_name}'
            elif role_text == ROLE_EXTRA:
                cat_name     = name_edits[i].text().strip() or col.strip()
                mapping[col] = f'extra:{cat_name}'
            else:
                mapping[col] = None   # Ignorer
        return mapping

    def _apply_csv_import(self, rows, mapping):
        """
        Insère les lignes CSV dans data_table.
        Si une référence existe déjà (même clé), ses valeurs mappées sont
        MISES À JOUR plutôt qu'ignorées — permet de ré-importer un CSV pour
        corriger/compléter un catalogue déjà créé, sans devoir tout ressaisir.
        Retourne (added, updated).
        """
        QtWidgets = self.QtWidgets

        def _norm(v):
            return v.strip().replace(',', '.') if v else ''

        # Lignes existantes indexées par RÉFÉRENCE (clé primaire) → n° de ligne.
        # Une référence déjà présente n'est plus un « doublon » ignoré : ses
        # valeurs sont MISES À JOUR par l'import (ex : ré-importer le même
        # CSV pour corriger un catalogue existant, où les colonnes VarSet
        # avaient été mal enregistrées — sinon le ré-import ne faisait rien).
        # Deux entrées avec même dimension mais références différentes
        # (ex: W-05-G et W-05-N) restent bien deux lignes distinctes.
        existing_rows = {}
        for ri in range(self.data_table.rowCount()):
            ref_item = self.data_table.item(ri, self.REF_COL)
            if ref_item and ref_item.text().strip():
                existing_rows[ref_item.text().strip().lower()] = ri

        # En-têtes actuels du tableau (pour détecter les colonnes manquantes)
        col_headers = []
        for ci in range(self.data_table.columnCount()):
            h = self.data_table.horizontalHeaderItem(ci)
            col_headers.append(h.text() if h else '')

        # Ajouter les colonnes manquantes
        # Règle key/label :
        #   VarSet → key = nom param VarSet (stable, utilisé pour setattr)
        #            label = nom colonne CSV (affiché dans sélecteur, modifiable)
        #   Extra  → key = label = nom personnalisé
        #
        # On détecte les doublons par KEY (pas par label) pour éviter d'ajouter
        # une colonne si le param VarSet est déjà présent sous un libellé différent.
        existing_keys = {c['key']: c['label'] for c in self.columns}

        for csv_col, role in mapping.items():
            if not role or role == 'ref':
                continue
            is_vs      = role.startswith('varset:')
            param_name = role.split(':', 1)[1]   # key pour les deux types
            key        = param_name
            label      = csv_col.strip() if is_vs else param_name  # label = nom CSV

            if key in existing_keys:
                # Colonne déjà présente (peut avoir un libellé différent) → skip
                continue

            ci = self.data_table.columnCount()
            self.data_table.setColumnCount(ci + 1)
            self.data_table.setHorizontalHeaderItem(
                ci, QtWidgets.QTableWidgetItem(label))
            col_headers.append(label)
            self._add_col_row(key, label, show=True, mute=not is_vs, required=is_vs)
            self.columns.append({
                'key':   key,
                'label': label,
                'show':  True,
                'mute':  not is_vs,
                'required': is_vs,
            })
            existing_keys[key] = label
            # Checkbox de visibilité en ligne 0
            chk = QtWidgets.QTableWidgetItem()
            chk.setFlags(self.QtCore.Qt.ItemIsUserCheckable |
                         self.QtCore.Qt.ItemIsEnabled)
            chk.setCheckState(self.QtCore.Qt.Checked)
            self.data_table.setItem(0, ci, chk)

        added = updated = 0
        for row in rows:
            # Normaliser valeurs ET clés (espaces parasites éventuels)
            row_n = {k.strip(): _norm(v) for k, v in row.items()}

            # Référence = clé primaire
            ref_val = next(
                (row_n[c] for c, r in mapping.items()
                 if r == 'ref' and c in row_n),
                f'ref_{added + 1}')

            # Référence déjà présente → mise à jour de la ligne existante
            # (et non plus un doublon ignoré, cf. commentaire ci-dessus).
            ri = existing_rows.get(ref_val.lower())
            is_update = ri is not None
            if not is_update:
                ri = self.data_table.rowCount()
                self.data_table.insertRow(ri)
                self.data_table.setItem(ri, self.REF_COL, QtWidgets.QTableWidgetItem(ref_val))
                # Préféré : décoché par défaut sur une ligne nouvellement
                # importée (le CSV ne porte pas cette information).
                fav_ci = self._favori_col()
                if fav_ci >= 0:
                    self.data_table.setItem(
                        ri, fav_ci, self._make_favori_item(False))

            for csv_col, role in mapping.items():
                if not role or role == 'ref':
                    continue
                is_vs      = role.startswith('varset:')
                param_name = role.split(':', 1)[1]
                key        = param_name
                # Retrouver le label effectif depuis existing_keys (déjà mis à jour)
                label      = existing_keys.get(key, csv_col.strip() if is_vs else param_name)
                if label in col_headers:
                    ci = col_headers.index(label)
                    self.data_table.setItem(
                        ri, ci, QtWidgets.QTableWidgetItem(row_n.get(csv_col, '')))

            existing_rows[ref_val.lower()] = ri
            if is_update:
                updated += 1
            else:
                added += 1

        # Scroller pour rendre la dernière colonne visible
        last_col = self.data_table.columnCount() - 1
        if last_col > 0:
            self.data_table.scrollToItem(
                self.data_table.item(
                    max(0, self.data_table.rowCount() - 1), last_col) or
                self.data_table.horizontalHeaderItem(last_col),
                self.QtWidgets.QAbstractItemView.EnsureVisible)

        return added, updated

    def _apply_col_bold(self, col):
        """Met l'en-tête de la colonne en gras."""
        item = self.data_table.horizontalHeaderItem(col)
        if item:
            font = item.font()
            font.setBold(True)
            item.setFont(font)

    # ──────────────────────────────────────────────────────────
    # Colonne « Préféré » (référence en gras)
    # ──────────────────────────────────────────────────────────
    def _favori_col(self):
        """
        Index de la colonne « Préféré » dans data_table — toujours la
        colonne 0 (position fixe, jamais déplacée par un import CSV
        puisque celui-ci ajoute ses colonnes en FIN de tableau). Repli par
        libellé si la structure attendue n'est pas (encore) en place.
        """
        if self.data_table.columnCount() > self.FAVORI_COL:
            h = self.data_table.horizontalHeaderItem(self.FAVORI_COL)
            if h and h.text() == self.FAVORI_LABEL:
                return self.FAVORI_COL
        for ci in range(self.data_table.columnCount()):
            h = self.data_table.horizontalHeaderItem(ci)
            if h and h.text() == self.FAVORI_LABEL:
                return ci
        return -1

    def _make_favori_item(self, checked=False):
        """Crée une cellule à cocher pour la colonne Préféré."""
        chk = self.QtWidgets.QTableWidgetItem()
        chk.setFlags(self.QtCore.Qt.ItemIsUserCheckable |
                     self.QtCore.Qt.ItemIsEnabled)
        chk.setCheckState(
            self.QtCore.Qt.Checked if checked else self.QtCore.Qt.Unchecked)
        return chk

    # Fond de mise en évidence « Préféré » : ambre semi-transparent (alpha),
    # pour rester lisible quel que soit le thème Qt/FreeCAD actif (clair ou
    # sombre) — la transparence se fond avec la couleur de fond réelle du
    # tableau plutôt que d'imposer une teinte fixe qui jurerait dans l'autre
    # thème. Même couleur que ui_selector.py (FAVORI_HIGHLIGHT_RGBA), pour
    # une cohérence visuelle éditeur/sélecteur.
    FAVORI_HIGHLIGHT_RGBA = (255, 193, 7, 60)

    def _set_row_bold(self, row, bold):
        """Applique (ou retire) la mise en évidence d'une ligne de données
        selon son statut « Préféré » : gras + léger fond ambré — reflet
        visuel immédiat, plus visible qu'un simple gras."""
        highlight = self.QtGui.QBrush(self.QtGui.QColor(*self.FAVORI_HIGHLIGHT_RGBA))
        no_brush  = self.QtGui.QBrush()   # rétablit le fond par défaut du thème
        for ci in range(self.data_table.columnCount()):
            item = self.data_table.item(row, ci)
            if item is None:
                continue
            font = item.font()
            if font.bold() != bold:
                font.setBold(bold)
                item.setFont(font)
            item.setBackground(highlight if bold else no_brush)

    def _on_data_item_changed(self, item):
        """Bascule le gras de la ligne dès que sa case Préféré change d'état."""
        if item.row() <= 0:   # ligne 0 = coches de visibilité, pas une donnée
            return
        if item.column() != self._favori_col():
            return
        self._set_row_bold(
            item.row(), item.checkState() == self.QtCore.Qt.Checked)

    def _swap_data_cols(self, a, b):
        """Échange deux colonnes dans data_table ET dans col_table (synchronisation)."""
        dt = self.data_table
        # 1. Échanger les largeurs de colonnes
        w_a = dt.columnWidth(a)
        w_b = dt.columnWidth(b)
        dt.setColumnWidth(a, w_b)
        dt.setColumnWidth(b, w_a)
        # 2. Échanger le contenu de toutes les lignes
        for row in range(dt.rowCount()):
            ia = dt.item(row, a)
            ib = dt.item(row, b)
            if ia and ib:
                ta, tb = ia.text(), ib.text()
                ia.setText(tb)
                ib.setText(ta)
        # 2. Échanger les en-têtes de colonnes
        hdr = dt.horizontalHeader()
        lbl_a = dt.horizontalHeaderItem(a)
        lbl_b = dt.horizontalHeaderItem(b)
        if lbl_a and lbl_b:
            la, lb = lbl_a.text(), lbl_b.text()
            lbl_a.setText(lb)
            lbl_b.setText(la)
        # 3. Synchroniser col_table (les lignes y = colonnes dans data_table)
        # col_table row 0 = data_table col REF_COL+1 (Préféré et Référence
        # ne sont jamais des lignes de col_table).
        ct_a = a - (self.REF_COL + 1)
        ct_b = b - (self.REF_COL + 1)
        if (ct_a >= 0 and ct_b >= 0 and
                ct_a < self.col_table.rowCount() and
                ct_b < self.col_table.rowCount()):
            self._swap_col_rows(ct_a, ct_b)

    def _swap_data_rows(self, a, b):
        fav_ci = self._favori_col()
        for col in range(self.data_table.columnCount()):
            ia = self.data_table.item(a, col)
            ib = self.data_table.item(b, col)
            if not (ia and ib):
                continue
            if col == fav_ci:
                # Case à cocher : échanger l'état, pas le texte (toujours vide).
                sa, sb = ia.checkState(), ib.checkState()
                ia.setCheckState(sb)
                ib.setCheckState(sa)
            else:
                ta, tb = ia.text(), ib.text()
                ia.setText(tb)
                ib.setText(ta)
        if fav_ci >= 0:
            for r in (a, b):
                item = self.data_table.item(r, fav_ci)
                if item:
                    self._set_row_bold(
                        r, item.checkState() == self.QtCore.Qt.Checked)

    def _data_add_row(self, prefill=False):
        QtWidgets = self.QtWidgets
        row = self.data_table.rowCount()
        self.data_table.insertRow(row)
        n_cols = self.data_table.columnCount()
        fav_ci = self._favori_col()
        for col in range(n_cols):
            if col == self.REF_COL:
                # Colonne référence — toujours vide, à saisir par l'utilisateur
                val = ''
                self.data_table.setItem(row, col, QtWidgets.QTableWidgetItem(val))
            elif col == fav_ci:
                # Préféré — toujours décoché par défaut sur une nouvelle ligne.
                self.data_table.setItem(row, col, self._make_favori_item(False))
            else:
                # Valeur depuis VarSet si préfill
                idx = col - (self.REF_COL + 1)
                if prefill and 0 <= idx < len(self.columns):
                    k   = self.columns[idx]['key']
                    raw = self.varset.get(k, '')
                    # Normaliser : float entier → int, Quantity → valeur numérique
                    if hasattr(raw, 'Value'):
                        raw = float(raw.Value)
                    if isinstance(raw, float) and raw == int(raw):
                        raw = int(raw)
                    val = str(raw) if raw != '' else ''
                else:
                    val = ''
                self.data_table.setItem(row, col, QtWidgets.QTableWidgetItem(val))

    def _data_dup_row(self):
        QtWidgets = self.QtWidgets
        src = self.data_table.currentRow()
        if src < 0:
            return
        new_row = self.data_table.rowCount()
        self.data_table.insertRow(new_row)
        fav_ci = self._favori_col()
        for col in range(self.data_table.columnCount()):
            item = self.data_table.item(src, col)
            if col == fav_ci:
                # Préféré — case à cocher : on reprend l'état de la ligne
                # source (une variante dupliquée depuis une référence
                # préférée reste préférée, à l'utilisateur de décocher).
                is_fav = bool(item and item.checkState() == self.QtCore.Qt.Checked)
                self.data_table.setItem(
                    new_row, col, self._make_favori_item(is_fav))
            else:
                self.data_table.setItem(
                    new_row, col,
                    QtWidgets.QTableWidgetItem(item.text() if item else ''))
        if fav_ci >= 0:
            fav_item = self.data_table.item(new_row, fav_ci)
            self._set_row_bold(
                new_row,
                bool(fav_item and fav_item.checkState() == self.QtCore.Qt.Checked))

    def _data_del_row(self):
        """Supprime toutes les lignes sélectionnées (sauf la ligne 0 de coches)."""
        selected = sorted(
            {idx.row() for idx in self.data_table.selectedIndexes()
             if idx.row() > 0},   # ignorer ligne 0 (coches)
            reverse=True)         # supprimer de bas en haut pour préserver les indices
        if not selected:
            # Fallback : ligne courante
            row = self.data_table.currentRow()
            if row > 0:
                self.data_table.removeRow(row)
            return
        for row in selected:
            self.data_table.removeRow(row)


    def _load_columns_from_config(self):
        """
        Charge les colonnes pour une variante existante.
        Inclut TOUS les paramètres VarSet — même ceux non affichés
        dans le dialogue de sélection — car ils sont nécessaires
        à la construction du composant.
        """
        vcfg = self.components.get(self.comp_key, {}).get(
            'cfg', {}).get(self.family, {}).get(self.variant, {})
        # Charger _legende depuis le JSON de données
        legende = {}
        json_path = vcfg.get('json', '')
        if json_path and os.path.exists(json_path):
            try:
                with open(json_path, encoding='utf-8') as _f:
                    legende = json.load(_f).get('_legende', {})
            except Exception as _e:
                print(f'[Editor] _legende erreur : {_e}')

        # Colonnes visibles depuis config.json avec labels depuis _legende
        visible_keys = set()
        self.columns = []
        for col_entry in vcfg.get('columns', []):
            ck       = col_entry[1]
            cl       = col_entry[0]
            col_mute = bool(col_entry[2]) if len(col_entry) > 2 else False
            col_show = bool(col_entry[3]) if len(col_entry) > 3 else True
            # Requis : absent des anciens catalogues (format à 4 éléments) →
            # repli sur l'ancien comportement implicite (muet = optionnel).
            col_req  = bool(col_entry[4]) if len(col_entry) > 4 else (not col_mute)
            if ck == 'ref': continue
            label = legende.get(ck, cl)   # _legende prioritaire
            self.columns.append(
                {'key': ck, 'label': label, 'show': col_show, 'mute': col_mute,
                 'required': col_req})
            visible_keys.add(ck)
        # Params VarSet absents du config → ajoutés comme NON-muets
        # (ce sont de vrais paramètres VarSet à appliquer, pas des champs extra)
        if self.fcstd_path and not self.varset:
            self.varset = read_varset_from_fcstd(self.fcstd_path)
        for k in self.varset:
            if k not in visible_keys:
                label = legende.get(k, k)
                self.columns.append(
                    {'key': k, 'label': label, 'show': True, 'mute': False,
                     'required': True})

        # Sécurité : si TOUTES les colonnes ont show=False (config corrompue),
        # les remettre à True pour ne pas cacher toutes les données
        if self.columns and not any(c.get('show', True) for c in self.columns):
            for c in self.columns:
                c['show'] = True

    def _load_existing_data(self):
        """Charge les données d'une variante existante dans le tableau."""
        QtWidgets = self.QtWidgets
        comp = self.components.get(self.comp_key, {})
        vcfg = comp.get('cfg', {}).get(
            self.family, {}).get(self.variant, {})
        json_path = vcfg.get('json', '')
        if not json_path or not os.path.exists(json_path):
            print(f'[Editor] JSON introuvable : {json_path}')
            return
        try:
            with open(json_path, encoding='utf-8') as f:
                data = json.load(f)
            refs = {k: v for k, v in data.items() if not k.startswith('_')}
            self.data_table.setRowCount(0)
            for ref, vals in refs.items():
                row = self.data_table.rowCount()
                self.data_table.insertRow(row)
                self.data_table.setItem(
                    row, self.REF_COL, QtWidgets.QTableWidgetItem(str(ref)))
                for col_idx, col in enumerate(self.columns, start=self.REF_COL + 1):
                    # NB : si la référence n'a pas cette clé dans le JSON, la
                    # cellule est laissée VIDE. Ne surtout pas retomber sur
                    # self.varset ici : ce dict est un snapshot UNIQUE de
                    # l'état courant du VarSet du FCStd (celui actuellement
                    # ouvert dans FreeCAD), pas une valeur par référence —
                    # l'utiliser comme repli faisait apparaître la même
                    # valeur pour TOUTES les références dont la donnée
                    # manquait (bug : « tous les items ont la même valeur »).
                    raw = vals.get(col['key'], '')
                    # Normaliser : "150.0 mm" → "150", 150.0 → "150"
                    import re as _re
                    if isinstance(raw, str):
                        m = _re.match(
                            r'^\s*([+-]?\d+(?:\.\d+)?)\s*'
                            r'(?:mm|deg|°|rad|kg|N|Pa|MPa)?\s*$', raw)
                        if m:
                            num = float(m.group(1))
                            raw = int(num) if num == int(num) else num
                    if isinstance(raw, float) and raw == int(raw):
                        v = str(int(raw))
                    else:
                        v = str(raw) if raw != '' else ''
                    self.data_table.setItem(
                        row, col_idx, QtWidgets.QTableWidgetItem(v))
                # Préféré — absent du JSON (anciens catalogues) → décoché
                # par défaut ; sera créé explicitement à False au prochain
                # enregistrement (cf. _read_data_from_table).
                is_fav = bool(vals.get(self.FAVORI_KEY, False))
                fav_ci = self._favori_col()
                if fav_ci >= 0:
                    self.data_table.setItem(
                        row, fav_ci, self._make_favori_item(is_fav))
                    self._set_row_bold(row, is_fav)
        except Exception as e:
            import traceback
            print(f'[Editor] Chargement {self.variant}: {e}')
            print(traceback.format_exc())

    def _read_data_from_table(self):
        self.refs = {}
        print(f'[read_data] rowCount={self.data_table.rowCount()} colCount={self.data_table.columnCount()} columns={[c["key"] for c in self.columns]}')

        # Construire un index header → colonne dans data_table
        # (indépendant de self.columns qui peut avoir un ordre différent)
        header_to_ci = {}
        for ci in range(self.data_table.columnCount()):
            h = self.data_table.horizontalHeaderItem(ci)
            if h:
                header_to_ci[h.text()] = ci

        # Lire les coches de visibilité (ligne 0) par header
        for col in self.columns:
            ci = header_to_ci.get(col['label'], header_to_ci.get(col['key'], -1))
            if ci >= 0:
                chk = self.data_table.item(0, ci)
                if chk:
                    col['show'] = (chk.checkState() == self.QtCore.Qt.Checked)

        # Lire les données par header (pas par position self.columns)
        for row in range(1, self.data_table.rowCount()):
            ref_item = self.data_table.item(row, self.REF_COL)
            if not ref_item or not ref_item.text().strip():
                continue
            ref  = ref_item.text().strip()
            vals = {}
            for col in self.columns:
                ci = header_to_ci.get(col['label'], header_to_ci.get(col['key'], -1))
                if ci < 0:
                    continue
                item = self.data_table.item(row, ci)
                raw  = item.text().strip() if item else ''
                import re as _re
                raw_c = _re.sub(r'\s*(mm|deg|°|rad|kg|N|Pa|MPa)\s*$',
                                '', raw, flags=_re.IGNORECASE).strip()
                # Ne pas sauvegarder les champs extra (mute) vides
                if not raw and not col.get('required', True):
                    continue
                try:
                    v = float(raw_c) if raw_c else float(raw)
                    vals[col['key']] = int(v) if v == int(v) else v
                except (ValueError, OverflowError):
                    vals[col['key']] = raw_c or raw
            # Préféré — toujours écrit explicitement (même False), afin que
            # le champ soit créé dans le JSON dès le premier enregistrement
            # pour les catalogues antérieurs qui ne l'avaient pas encore.
            fav_ci = header_to_ci.get(self.FAVORI_LABEL, -1)
            is_fav = False
            if fav_ci >= 0:
                fav_item = self.data_table.item(row, fav_ci)
                is_fav = bool(fav_item and
                              fav_item.checkState() == self.QtCore.Qt.Checked)
            vals[self.FAVORI_KEY] = is_fav
            self.refs[ref] = vals

    # ──────────────────────────────────────────────────────────
    # Aperçu JSON
    # ──────────────────────────────────────────────────────────
    def _build_json_data(self):
        """Construit le dict JSON final à partir de l'état courant."""
        fcstd_name = os.path.basename(self.fcstd_path) if self.fcstd_path else ''
        data = {
            '_parametres': {
                'type':  f'{self.family} {self.variant}',
                'fcstd': fcstd_name,
            },
            '_legende': {
                c['key']: c['label']
                for c in self.columns
            },
        }
        data.update(self.refs)
        return data

    def _build_json_preview(self):
        """Retourne la chaîne JSON formatée (aperçu et écriture)."""
        import io
        data = self._build_json_data()

        meta_keys = [k for k in data if k.startswith('_')]
        ref_keys  = [k for k in data if not k.startswith('_')]

        if not ref_keys:
            return json.dumps(data, ensure_ascii=False, indent=2)

        all_col_keys = list(dict.fromkeys(
            k for ref in ref_keys for k in data[ref]))
        key_w   = max((len(k) for k in all_col_keys), default=1) + 2  # +2 guillemets
        val_w   = {}
        for ck in all_col_keys:
            max_v = max(
                (len(_fmt_val(data[rk].get(ck, ''))) for rk in ref_keys),
                default=1)
            val_w[ck] = max_v
        ref_w = max((len(json.dumps(rk)) for rk in ref_keys), default=1)

        lines = ['{']
        for mk in meta_keys:
            lines.append(
                f'  {json.dumps(mk)}: {json.dumps(data[mk], ensure_ascii=False)},')
        for i, rk in enumerate(ref_keys):
            row    = data[rk]
            rk_str = json.dumps(rk).ljust(ref_w)
            parts  = []
            for ck in all_col_keys:
                ck_str  = json.dumps(ck).ljust(key_w)
                val_str = _fmt_val(row.get(ck, '')).rjust(val_w[ck])
                parts.append(f'{ck_str}: {val_str}')
            sep  = ',' if i < len(ref_keys) - 1 else ''
            body = ', '.join(parts)
            lines.append(f'  {rk_str}: {{{body}}}{sep}')
        lines.append('}')
        return '\n'.join(lines)

    # ──────────────────────────────────────────────────────────
    # Sauvegarde
    # ──────────────────────────────────────────────────────────
    def _finish(self):
        if not self._validate_page(self.PAGE_DATA):
            return
        try:
            saved = self._save()
            self.save_status.setStyleSheet('color: green;')
            self.save_status.setText(
                _t('ed_status_save_success') + '\n'.join(saved))
            self.btn_finish.setEnabled(False)
            QtWidgets = self.QtWidgets
            # Si un FCStd renommé/remplacé reste présent dans la bibliothèque
            # externe (cf. avertissements dans `saved`), proposer de le
            # supprimer là-bas — action explicite, jamais automatique.
            self._offer_library_cleanup(QtWidgets)
            # Proposer de fermer l'éditeur
            reply = QtWidgets.QMessageBox.question(
                self.dlg, _t('ed_q_save_success_title'),
                _t('ed_q_save_success_msg', details=('\n'.join(saved))),
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
                QtWidgets.QMessageBox.Yes)
            if reply == QtWidgets.QMessageBox.Yes:
                self.dlg.accept()
            else:
                # Rester dans l'éditeur → recharger les composants
                self.components = discover_components()
                self._prepare_meta_page()
        except Exception as e:
            import traceback
            self.save_status.setStyleSheet('color: red;')
            self.save_status.setText(_t('ed_status_error', err=(e), tb=(traceback.format_exc())))


    def _save(self):
        """Sauvegarde JSON + mise à jour config.json. Retourne la liste des fichiers écrits."""
        saved = []

        # 1. Synchroniser l'état "show" de data_table ligne 0 → col_table
        #    par POSITION (pas par self.columns) car les colonnes peuvent
        #    avoir été réordonnées — data_table et col_table restent
        #    synchrones entre eux, mais self.columns date de l'ancienne position.
        #    Décalage RÉF_COL+1 (= 2) pour sauter Préféré (colonne 0, jamais
        #    affichée dans col_table) ET Référence (colonne REF_COL) — la
        #    première colonne configurable (self.columns[0]) commence donc à
        #    data_table[REF_COL + 1], pas à data_table[1].
        for i in range(self.col_table.rowCount()):
            chk_data = self.data_table.item(0, i + self.REF_COL + 1)
            chk_col  = self.col_table.item(i, 0)
            if chk_data and chk_col:
                chk_col.setCheckState(chk_data.checkState())

        # 2. Resynchroniser self.columns depuis col_table (ordre + show + mute)
        self._read_columns_from_table()

        # Toujours sauvegarder dans STANDARDS_DIR (source de vérité locale)
        # La bibliothèque externe est en lecture seule depuis l'éditeur
        comp_dir = os.path.join(STANDARDS_DIR, self.comp_key)
        print(f'[Editor._save] comp_key={self.comp_key!r}')
        print(f'[Editor._save] family={self.family!r} variant={self.variant!r}')
        print(f'[Editor._save] json_filename={self.json_filename!r}')
        print(f'[Editor._save] comp_dir={comp_dir!r}')
        os.makedirs(os.path.join(comp_dir, 'datas'),  exist_ok=True)
        os.makedirs(os.path.join(comp_dir, 'models'), exist_ok=True)
        os.makedirs(os.path.join(comp_dir, 'thumbnails'), exist_ok=True)

        # Fichier FCStd : nom cible (celui choisi via 'Renommer…' s'il y en a
        # un, sinon celui du fichier source actuellement sélectionné) et
        # copie/renommage/remplacement selon le cas.
        fcstd_name = getattr(self, '_fcstd_target_name', '') or \
            (os.path.basename(self.fcstd_path) if self.fcstd_path else '')
        dest_fcstd = os.path.join(comp_dir, 'models', fcstd_name) \
            if fcstd_name else ''
        pending_rename = getattr(self, '_pending_fcstd_rename', None)
        self._pending_library_cleanup = []

        if pending_rename and self.fcstd_path:
            # Renommage demandé (page Métadonnées → 'Renommer…') : même
            # contenu, nouveau nom de fichier.
            old_name, new_name = pending_rename
            old_local = os.path.join(comp_dir, 'models', old_name)
            if os.path.exists(old_local) and not os.path.exists(dest_fcstd):
                os.rename(old_local, dest_fcstd)
                saved.append(_t('ed_status_fcstd_renamed', old_name=(old_name), new_name=(new_name)))
            elif not os.path.exists(dest_fcstd):
                # Le fichier actif venait de la bibliothèque externe (lecture
                # seule depuis l'éditeur) : copie locale sous le nouveau nom,
                # la bibliothèque n'est pas modifiée.
                shutil.copy2(self.fcstd_path, dest_fcstd)
                saved.append(
                    _t('ed_status_fcstd_copied_lib_renamed', new_name=(new_name)))
            # Avertir si l'ANCIEN nom existe encore quelque part (bibliothèque
            # et/ou — normalement jamais après un renommage, mais vérifié par
            # sécurité — une copie locale non déplacée) : tant qu'il reste
            # dans la bibliothèque, la synchronisation silencieuse (lancement
            # suivant de la macro) le recopiera automatiquement.
            lib_old = self._library_fcstd_path(self.comp_key, old_name)
            local_leftover = old_local if os.path.exists(old_local) else None
            self._register_fcstd_leftover(saved, old_name, lib_old, local_leftover)
        elif getattr(self, '_fcstd_replaced', False) and self.fcstd_path:
            # Un autre fichier FCStd a été choisi pour cette variante
            # (page Métadonnées → 'Choisir un autre…') : remplacement
            # volontaire, y compris si un fichier du même nom existe déjà.
            if fcstd_name and os.path.abspath(self.fcstd_path) != os.path.abspath(dest_fcstd):
                shutil.copy2(self.fcstd_path, dest_fcstd)
                saved.append(_t('ed_status_fcstd_replaced', dest_fcstd=(dest_fcstd)))
            old_replaced_name = getattr(self, '_fcstd_replaced_old_name', '')
            if old_replaced_name:
                lib_old2 = self._library_fcstd_path(self.comp_key, old_replaced_name)
                local_old2 = os.path.join(comp_dir, 'models', old_replaced_name)
                local_old2 = local_old2 if os.path.exists(local_old2) else None
                self._register_fcstd_leftover(
                    saved, old_replaced_name, lib_old2, local_old2)
        elif self.fcstd_path and fcstd_name and not os.path.exists(dest_fcstd):
            # Cas normal (nouvelle variante, ou variante existante inchangée
            # dont le fichier n'est pas encore déployé localement).
            shutil.copy2(self.fcstd_path, dest_fcstd)
            saved.append(_t('ed_status_fcstd_copied', dest_fcstd=(dest_fcstd)))

        # Écrire le JSON de données
        json_path = os.path.join(comp_dir, 'datas', self.json_filename)
        bak = backup_file(json_path)
        if bak:
            saved.append(_t('ed_status_backup', name=(os.path.basename(bak))))
        data = self._build_json_data()
        # Diagnostic : vérifier les colonnes et les premières refs
        print(f'[Editor._save] columns={[c["key"] for c in self.columns]}')
        sample = list(data.items())[:3]
        for k, v in sample:
            if not str(k).startswith('_'):
                print(f'[Editor._save] ref={k!r} keys={list(v.keys()) if isinstance(v,dict) else v}')
        write_json_aligned(data, json_path)
        saved.append(_t('ed_status_json_written', path=(json_path)))

        # Mettre à jour config.json
        config_path = os.path.join(comp_dir, 'config.json')
        if os.path.exists(config_path):
            with open(config_path, encoding='utf-8') as f:
                cfg = json.load(f)
        else:
            cfg = {'_description': f'Catalogue {self.comp_key}', '_version': '1.0'}

        # Si le FCStd a été renommé et qu'il est partagé par d'autres
        # variantes (même composant), répercuter le nouveau nom pour ne pas
        # les laisser référencer un fichier qui n'existe plus sous l'ancien
        # nom (cf. avertissement affiché dans _rename_meta_fcstd).
        if pending_rename:
            old_name, new_name = pending_rename
            for fam, variants in cfg.items():
                if fam.startswith('_') or not isinstance(variants, dict):
                    continue
                for var, vdata in variants.items():
                    if fam == self.family and var == self.variant:
                        continue
                    if isinstance(vdata, dict) and vdata.get('fcstd') == old_name:
                        vdata['fcstd'] = new_name
                        saved.append(
                            _t('ed_status_ref_updated', fam=(fam), var=(var)))

        # Colonnes pour config.json : format (label, key, mute, show, requis)
        # mute=True   → non appliqué au VarSet (champ extra)
        # show=False  → non affiché dans le tableau du sélecteur
        # requis=False → une valeur vide pour ce paramètre est omise du JSON
        #                 (au lieu d'y être enregistrée comme "")
        all_cols = [('Reference', 'ref', False, True, True)] + [
            (c['label'], c['key'], c.get('mute', False), c.get('show', True),
             c.get('required', not c.get('mute', False)))
            for c in self.columns]

        # Reconstruire _excluded_keys comme UNION de toutes les variantes du composant.
        # Utiliser uniquement les colonnes explicitement muettes (mute=True).
        # Les params VarSet ne doivent JAMAIS être muets (fix précédent garantit cela).
        # Note : _excluded_keys est global au composant → couvre toutes ses variantes.
        new_excl = set()
        # Colonnes de la variante courante
        new_excl.update(c['key'] for c in self.columns if c.get('mute', False))
        # Colonnes des autres variantes déjà dans config.json
        for fam_data in cfg.values():
            if not isinstance(fam_data, dict):
                continue
            for var_data in fam_data.values():
                if not isinstance(var_data, dict):
                    continue
                for col in var_data.get('columns', []):
                    if len(col) > 2 and col[2]:   # mute = 3e élément
                        new_excl.add(col[1])      # key = 2e élément
        if new_excl:
            cfg['_excluded_keys'] = sorted(new_excl)
        else:
            cfg.pop('_excluded_keys', None)

        if self.family not in cfg:
            cfg[self.family] = {}
        cfg[self.family][self.variant] = {
            'fcstd':   fcstd_name,
            'json':    self.json_filename,
            'columns': all_cols,
        }
        bak2 = backup_file(config_path)
        if bak2:
            saved.append(_t('ed_status_config_backup', name=(os.path.basename(bak2))))
        with open(config_path, 'w', encoding='utf-8') as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        saved.append(_t('ed_status_config_written', path=(config_path)))

        # Réinitialiser l'état transitoire de renommage/remplacement FCStd :
        # une fois sauvegardé, fcstd_name/dest_fcstd deviennent la référence
        # "normale" (utile si l'utilisateur enchaîne une autre sauvegarde
        # sans rouvrir la variante).
        self._pending_fcstd_rename = None
        self._fcstd_replaced = False
        self._fcstd_replaced_old_name = ''
        self._fcstd_target_name = fcstd_name
        if dest_fcstd and os.path.exists(dest_fcstd):
            self.fcstd_path = dest_fcstd

        # Libellé d'affichage de la variante — purement cosmétique
        # (translations.json), n'affecte jamais la clé interne self.variant.
        try:
            write_variant_label(self.variant, getattr(self, 'variant_label', ''))
            if getattr(self, 'variant_label', ''):
                saved.append(_t('ed_status_variant_label', label=(self.variant_label)))
        except Exception as _e:
            print(f'[Editor] Libellé variante non sauvegardé : {_e}')

        return saved

    # ──────────────────────────────────────────────────────────
    # Lancement
    # ──────────────────────────────────────────────────────────

    def _configure_library(self):
        """Dialogue de configuration du chemin de bibliothèque."""
        QtWidgets = self.QtWidgets
        dlg = QtWidgets.QDialog(self.dlg)
        dlg.setWindowTitle(_t('ed_win_library_title'))
        dlg.setMinimumWidth(580)
        dlg.setWindowFlags(
            dlg.windowFlags() | self.QtCore.Qt.WindowStaysOnTopHint)
        layout = QtWidgets.QVBoxLayout()

        current = get_library_path() or ''
        lbl = QtWidgets.QLabel(
            _t('ed_label_library_help'))
        lbl.setWordWrap(True)
        layout.addWidget(lbl)

        path_row = QtWidgets.QHBoxLayout()
        path_edit = QtWidgets.QLineEdit(current)
        path_edit.setPlaceholderText(_t('ed_ph_library_path'))
        btn_browse = QtWidgets.QPushButton(_t('ed_btn_browse'))
        path_row.addWidget(path_edit)
        path_row.addWidget(btn_browse)
        layout.addLayout(path_row)

        status_lbl = QtWidgets.QLabel("")
        status_lbl.setWordWrap(True)
        layout.addWidget(status_lbl)

        btn_row = QtWidgets.QHBoxLayout()
        btn_ok    = QtWidgets.QPushButton(_t('ed_btn_validate'))
        btn_clear = QtWidgets.QPushButton(_t('ed_btn_delete_config'))
        btn_cancel= QtWidgets.QPushButton(_t('ed_btn_cancel'))
        btn_row.addWidget(btn_clear)
        btn_row.addStretch()
        btn_row.addWidget(btn_cancel)
        btn_row.addWidget(btn_ok)
        layout.addLayout(btn_row)
        dlg.setLayout(layout)

        def _browse():
            p = QtWidgets.QFileDialog.getExistingDirectory(
                dlg, _t('ed_dlg_select_library_dir_title'), current)
            if p:
                path_edit.setText(p)

        def _validate():
            new_path = path_edit.text().strip()
            if not new_path:
                status_lbl.setStyleSheet('color: red;')
                status_lbl.setText(
                    _t('ed_status_empty_path'))
                return
            if not os.path.isdir(new_path):
                try:
                    os.makedirs(new_path, exist_ok=True)
                except Exception as e:
                    status_lbl.setStyleSheet('color: red;')
                    status_lbl.setText(_t('ed_status_create_error', err=(e)))
                    return
            old_path = set_library_path(new_path)
            # Source : ancienne bibliothèque ou MACRO_DIR (première config)
            src_path = (old_path
                        if old_path and old_path != new_path
                           and os.path.isdir(old_path)
                        else MACRO_DIR)
            reply = QtWidgets.QMessageBox.question(
                dlg, _t('ed_q_migration_title'),
                _t('ed_q_migration_msg', src=(src_path), dst=(new_path)),
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
                QtWidgets.QMessageBox.Yes)
            if reply == QtWidgets.QMessageBox.Yes:
                moved, errors = migrate_library(src_path, new_path)
                msg = (_t('ed_status_files_moved', count=(len(moved)), list=("\n".join(moved)))
                       ) if moved else _t('ed_status_no_fcstd_found')
                if errors:
                    msg += _t('ed_status_moved_errors', count=(len(errors)), errors=(', '.join(errors)))
                status_lbl.setStyleSheet(
                    'color: green;' if not errors else 'color: orange;')
                status_lbl.setText(msg)
                if not errors:
                    dlg.accept()
            else:
                status_lbl.setStyleSheet('color: green;')
                status_lbl.setText(_t('ed_status_library_configured', path=(new_path)))
                dlg.accept()

        def _clear():
            if os.path.exists(LIBRARY_PATH_FILE):
                os.remove(LIBRARY_PATH_FILE)
            status_lbl.setStyleSheet('color: gray;')
            status_lbl.setText(
                _t('ed_status_config_removed'))
            path_edit.clear()

        btn_browse.clicked.connect(_browse)
        btn_ok.clicked.connect(_validate)
        btn_cancel.clicked.connect(dlg.reject)
        btn_clear.clicked.connect(_clear)
        dlg.exec()

    def run(self):
        if not self.components:
            self.components = discover_components()
        self._prepare_meta_page()
        # Capturer le code de retour depuis exec() lui-même —
        # WA_DeleteOnClose supprime l'objet C++ avant que result() soit appelable
        exit_code = self.dlg.exec()
        try:
            self.dlg.deleteLater()
        except Exception:
            pass
        return exit_code


# ============================================================
# Intégration dans le dialogue principal de StandardPartSelector
# ============================================================
def open_editor(preselect_comp='', preselect_family='', preselect_variant='',
                new_ref_name=None, new_ref_vals=None, restrict_fcstd=None,
                force_action=None):
    """Point d'entrée appelé par le bouton 'Gérer les catalogues'."""
    ed = EditorDialog(
        preselect_comp=preselect_comp,
        preselect_family=preselect_family,
        preselect_variant=preselect_variant,
        new_ref_name=new_ref_name,
        new_ref_vals=new_ref_vals,
        restrict_fcstd=restrict_fcstd,
        force_action=force_action)
    ed.run()


# ============================================================
# Main (lancement direct comme macro FreeCAD)
# ============================================================
def main():
    open_editor()


# Exécution directe uniquement (pas quand importé via importlib)
if __name__ == '__main__' or globals().get('App') is None:
    try:
        main()
    except Exception as _e:
        import traceback as _tb
        _msg = f"Erreur démarrage éditeur : {_e}\n\n{_tb.format_exc()}"
        print(_msg)
        try:
            from PySide2 import QtWidgets as _Qt
        except ImportError:
            from PySide import QtWidgets as _Qt
        _Qt.QMessageBox.critical(None, "StandardPartSelectorEditor", _msg)
