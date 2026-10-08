# -*- coding: utf-8 -*-
"""
StandardPartSelector.update — Mise à jour en masse des composants SC_.

Trois types de modification sont distingués par composant :

  * varset_changed : les valeurs du catalogue (JSON) diffèrent du VarSet du
                     composant. Mise à jour EN PLACE possible (le VarSet est
                     réécrit, la géométrie se recalcule, l'assemblage tient).
  * fcstd_changed  : le modèle FCStd a changé (géométrie), ou sa structure de
                     paramètres a évolué. La mise à jour en place est impossible
                     → le composant doit être RECRÉÉ avec le nouveau FCStd.
  * missing_ref    : la référence n'existe plus dans le catalogue.

Le cas « les deux ont changé » est couvert par fcstd_changed : dès que le
modèle change, le composant doit être recréé, quelle que soit l'évolution
des valeurs.
"""
from config   import _t, _dbg
from catalog  import load_catalog
from sc_props import (_find_varset_in_object, _update_sc_varset,
                      diff_sc_data_properties, apply_sc_data_properties)
from manifest import get_source_timestamp, read_varset_timestamp


def _fmt_val(v):
    """Formate une valeur numérique de façon lisible (sans .0 superflu)."""
    try:
        f = float(v)
        return str(int(f)) if f == int(f) else f'{f:g}'
    except (TypeError, ValueError):
        return str(v)


def _values_equal(a, b):
    """Égalité tolérante : numérique si possible, sinon textuelle."""
    try:
        return float(a) == float(b)
    except (TypeError, ValueError):
        return str(a) == str(b)


def _diff_varset(varset, params, excl):
    """
    Compare les valeurs du catalogue (params) au VarSet réel du composant.

    Retourne (changed, structural) :
      changed    : liste de (clé, valeur_actuelle, valeur_catalogue) qui diffèrent
      structural : True si une clé non-muette du catalogue est absente du VarSet
                   (paramètre nouveau → changement de modèle, pas de MAJ en place)
    """
    changed    = []
    structural = False
    props = getattr(varset, 'PropertiesList', None) or [] if varset is not None else []

    for key, ref_val in params.items():
        if key.startswith('_') or key == 'ref' or key in excl:
            continue
        if varset is None or key not in props:
            structural = True
            continue
        current = getattr(varset, key, None)
        if current is None:
            continue
        if not _values_equal(current, ref_val):
            changed.append((key, current, ref_val))

    return changed, structural


def _scan_sc_components(doc, components):
    """
    Scanne tous les objets SC_ du document actif.
    Retourne une liste de dicts :
      { obj, comp_key, family, variant, ref, ts_obj, ts_src,
        catalog, vcfg, varset, status, changed_params, error }
    status : 'ok' | 'varset_changed' | 'fcstd_changed' | 'missing_ref'
    """
    import os as _os

    results = []
    if doc is None:
        return results

    for obj in doc.Objects:
        if not hasattr(obj, 'SC_Standard') or not obj.SC_Standard:
            continue
        comp_key = getattr(obj, 'SC_Standard',  '')
        family   = getattr(obj, 'SC_Family',    '')
        variant  = getattr(obj, 'SC_Category',  '')
        ref      = getattr(obj, 'SC_Reference', '')
        ts_obj   = getattr(obj, 'SC_TimeStamp', '')
        if not comp_key or not ref:
            continue

        base = {
            'obj': obj, 'comp_key': comp_key, 'family': family,
            'variant': variant, 'ref': ref, 'ts_obj': ts_obj,
            'ts_src': None, 'catalog': {}, 'vcfg': {}, 'varset': None,
            'changed_params': [], 'changed_data': [], 'data_props': {},
            'error': None,
        }

        # Config de la variante
        vcfg = (components.get(comp_key, {})
                          .get('cfg', {})
                          .get(family, {})
                          .get(variant, {}))
        if not vcfg:
            base.update(status='missing_ref', error=_t('update_no_config'))
            results.append(base)
            continue

        # Catalogue
        try:
            catalog = load_catalog(vcfg['json'])
        except Exception as e:
            base.update(status='missing_ref', error=str(e))
            results.append(base)
            continue

        # Référence encore présente ?
        if ref not in catalog:
            base.update(status='missing_ref', catalog=catalog, vcfg=vcfg,
                        varset=_find_varset_in_object(obj),
                        error=_t('update_ref_missing', ref=ref))
            results.append(base)
            continue

        # Version du modèle FCStd : jeton SC_TimeStamp via le manifest
        # (manifest.py — insensible aux copies/sync/restaurations qui ne
        # touchent que le mtime sans modifier le contenu du modèle).
        # reconcile_all() est appelé au lancement de la macro ; le manifest
        # est donc normalement à jour. Repli sur une lecture directe du
        # VarSet si l'entrée manque (manifest jamais réconcilié).
        fcstd_path = vcfg.get('fcstd') or ''
        ts_src = None
        if fcstd_path and _os.path.exists(fcstd_path):
            ts_src = get_source_timestamp(comp_key, fcstd_path)
            if ts_src is None:
                ts_src = read_varset_timestamp(fcstd_path)

        varset = _find_varset_in_object(obj)
        excl   = set(vcfg.get('excluded_keys', set()))
        params = catalog.get(ref, {})
        data_props = vcfg.get('data_props', {})

        changed, structural = _diff_varset(varset, params, excl)
        data_changed = diff_sc_data_properties(obj, params, data_props)

        # Modèle plus récent que le composant ?
        model_newer = bool(ts_src) and bool(ts_obj) and ts_src > ts_obj

        # Affichage groupé : params VarSet + données supplémentaires
        base.update(catalog=catalog, vcfg=vcfg, varset=varset, ts_src=ts_src,
                    data_props=data_props, changed_data=data_changed,
                    changed_params=changed + data_changed)

        # ── Décision de statut (le modèle l'emporte → couvre « les deux ») ──
        if varset is None:
            base.update(status='fcstd_changed',
                        error=_t('update_no_varset',
                                 default='VarSet introuvable — recréer le composant'))
        elif model_newer or structural:
            reason = (_t('update_reason_structural',
                         default='structure de paramètres modifiée')
                      if structural and not model_newer
                      else _t('update_reason_geometry',
                              default='géométrie du modèle modifiée'))
            base.update(status='fcstd_changed', error=reason)
        elif changed or data_changed:
            base.update(status='varset_changed')
        else:
            base.update(status='ok')

        results.append(base)

    return results


def _format_changed(changed):
    """Résumé lisible : 'clé : actuel → cible' (création, modification, suppression)."""
    def _fmt_pair(cur, ref):
        if ref is None:
            return f'{_fmt_val(cur)} → (supprimée)'
        if cur is None:
            return f'(absent) → {_fmt_val(ref)}'
        return f'{_fmt_val(cur)} → {_fmt_val(ref)}'
    return ' ; '.join(f'{k} : {_fmt_pair(cur, ref)}' for k, cur, ref in changed)


def _show_update_dialog(scan_results, QtWidgets, QtCore):
    """
    Affiche le dialogue de mise à jour.
    Retourne la liste des items 'varset_changed' cochés (MAJ en place),
    ou None si annulé.
    """
    try:
        from PySide2 import QtWidgets as Qw, QtCore as Qc
    except ImportError:
        from PySide  import QtGui as Qw, QtCore as Qc

    varset_ch = [r for r in scan_results if r['status'] == 'varset_changed']
    fcstd_ch  = [r for r in scan_results if r['status'] == 'fcstd_changed']
    missing   = [r for r in scan_results if r['status'] == 'missing_ref']
    ok        = [r for r in scan_results if r['status'] == 'ok']

    dlg = Qw.QDialog()
    dlg.setWindowTitle(_t('update_title'))
    dlg.setMinimumWidth(720)
    dlg.setMinimumHeight(460)
    root = Qw.QVBoxLayout()

    # ── Résumé ──────────────────────────────────────────────────────────
    parts = []
    if varset_ch:
        parts.append(f'🔧 {len(varset_ch)} '
                     + _t('update_sum_varset', default='valeur(s) à mettre à jour'))
    if fcstd_ch:
        parts.append(f'⚠ {len(fcstd_ch)} '
                     + _t('update_sum_fcstd', default='à recréer (modèle FCStd modifié)'))
    if missing:
        parts.append(f'❌ {len(missing)} '
                     + _t('update_sum_missing', default='référence(s) introuvable(s)'))
    if ok:
        parts.append(f'✅ {len(ok)} '
                     + _t('update_sum_ok', default='à jour'))
    summary = Qw.QLabel('<br>'.join(parts) if parts
                        else _t('update_nothing_to_do'))
    summary.setTextFormat(Qc.Qt.RichText)
    root.addWidget(summary)

    # ── Section 1 : VarSet modifié → MAJ en place ───────────────────────
    checkboxes = []
    if varset_ch:
        msg = Qw.QLabel(
            '<b style="color:#61afef;">'
            + _t('update_varset_alert',
                 default='Les valeurs catalogue de ces composants ont changé '
                         '(paramètres et/ou données supplémentaires). La mise à '
                         'jour s\'applique en place, sans déplacer la pièce ni '
                         'casser l\'assemblage.')
            + '</b>')
        msg.setTextFormat(Qc.Qt.RichText)
        msg.setWordWrap(True)
        root.addWidget(msg)

        table = Qw.QTableWidget(len(varset_ch), 4)
        table.setHorizontalHeaderLabels([
            '', _t('update_col_name'), _t('update_col_ref'),
            _t('update_col_params', default='Paramètres modifiés')])
        table.horizontalHeader().setStretchLastSection(True)
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(Qw.QAbstractItemView.NoEditTriggers)
        table.setSelectionMode(Qw.QAbstractItemView.NoSelection)
        table.setWordWrap(True)

        for row, r in enumerate(varset_ch):
            cb = Qw.QCheckBox()
            cb.setChecked(True)
            cell_w = Qw.QWidget()
            cell_l = Qw.QHBoxLayout(cell_w)
            cell_l.addWidget(cb)
            cell_l.setAlignment(Qc.Qt.AlignCenter)
            cell_l.setContentsMargins(0, 0, 0, 0)
            table.setCellWidget(row, 0, cell_w)
            table.setItem(row, 1, Qw.QTableWidgetItem(r['obj'].Label))
            table.setItem(row, 2, Qw.QTableWidgetItem(r['ref']))
            table.setItem(row, 3, Qw.QTableWidgetItem(_format_changed(r['changed_params'])))
            checkboxes.append(cb)

        table.resizeColumnsToContents()
        table.resizeRowsToContents()
        root.addWidget(table)

        sel_bar = Qw.QHBoxLayout()
        btn_all  = Qw.QPushButton(_t('update_select_all'))
        btn_none = Qw.QPushButton(_t('update_select_none'))
        btn_all.clicked.connect(lambda: [cb.setChecked(True)  for cb in checkboxes])
        btn_none.clicked.connect(lambda: [cb.setChecked(False) for cb in checkboxes])
        sel_bar.addWidget(btn_all)
        sel_bar.addWidget(btn_none)
        sel_bar.addStretch()
        root.addLayout(sel_bar)

    # ── Section 2 : modèle FCStd modifié → à recréer (couvre « les deux ») ─
    if fcstd_ch:
        alert = Qw.QLabel(
            '<b style="color:#e5c07b;">'
            + _t('update_fcstd_alert',
                 default='Le modèle FCStd de ces composants a changé. La mise à '
                         'jour en place est impossible : recréez-les avec le '
                         'nouveau FCStd (supprimez puis réinsérez, ou utilisez '
                         'Remplacer).')
            + '</b>')
        alert.setTextFormat(Qc.Qt.RichText)
        alert.setWordWrap(True)
        root.addWidget(alert)

        lst = Qw.QListWidget()
        for r in fcstd_ch:
            detail = r.get('error') or ''
            lst.addItem(f'{r["obj"].Label}  —  {r["ref"]}'
                        + (f'  ({detail})' if detail else ''))
        lst.setMaximumHeight(110)
        root.addWidget(lst)

    # ── Section 3 : références introuvables ─────────────────────────────
    if missing:
        alert = Qw.QLabel(
            f'<b style="color:#e06c75;">{_t("update_missing_alert")}</b>')
        alert.setTextFormat(Qc.Qt.RichText)
        alert.setWordWrap(True)
        root.addWidget(alert)
        miss_list = Qw.QListWidget()
        for r in missing:
            miss_list.addItem(f'{r["obj"].Label}  —  {r["error"]}')
        miss_list.setMaximumHeight(90)
        root.addWidget(miss_list)

    if not (varset_ch or fcstd_ch or missing):
        root.addWidget(Qw.QLabel(_t('update_nothing_to_do')))

    # ── Boutons ─────────────────────────────────────────────────────────
    bbox = Qw.QDialogButtonBox(
        Qw.QDialogButtonBox.Ok | Qw.QDialogButtonBox.Cancel)
    btn_ok = bbox.button(Qw.QDialogButtonBox.Ok)
    btn_ok.setText(_t('update_apply'))
    btn_ok.setEnabled(bool(varset_ch))          # MAJ en place uniquement
    bbox.button(Qw.QDialogButtonBox.Cancel).setText(_t('cancel_btn'))
    bbox.accepted.connect(dlg.accept)
    bbox.rejected.connect(dlg.reject)
    root.addWidget(bbox)
    dlg.setLayout(root)

    if dlg.exec() != Qw.QDialog.Accepted:
        return None

    if not varset_ch:
        return []
    return [r for r, cb in zip(varset_ch, checkboxes) if cb.isChecked()]


def _apply_bulk_update(to_update, QtWidgets):
    """
    Applique la mise à jour EN PLACE du VarSet sur les composants sélectionnés
    (statut 'varset_changed' uniquement). Bilan unique à la fin.
    """
    import FreeCAD as App
    from datetime import datetime as _dt
    doc = App.ActiveDocument
    results_ok  = []   # [(label, ref, nb_params)]
    results_err = []   # [(label, erreur)]

    for r in to_update:
        obj     = r['obj']
        ref     = r['ref']
        catalog = r['catalog']
        vcfg    = r.get('vcfg', {})
        varset  = r['varset']

        if varset is None:
            results_err.append((obj.Label, _t('no_varset_found')))
            continue

        params = catalog.get(ref, {})
        excl   = set(vcfg.get('excluded_keys', []))
        # Recaler la version du composant sur celle du modèle (et non sur now())
        # pour qu'une future modification réelle du FCStd reste détectable.
        new_ts = r.get('ts_src') or r.get('ts_obj') \
                 or _dt.now().strftime('%Y%m%d-%H%M%S')
        try:
            _update_sc_varset(obj, varset, params, new_ts, excl, QtWidgets,
                              silent=True)
            # Rafraîchir les propriétés SC_ de données (Prix, Fournisseur…)
            apply_sc_data_properties(obj, params, r.get('data_props', {}))
            nb = len([k for k in params
                      if k not in excl
                      and k in (varset.PropertiesList or [])])
            results_ok.append((obj.Label, ref, nb))
            _dbg(f'[update] {obj.Label} → {ref} ✓')
        except Exception as e:
            results_err.append((obj.Label, str(e)))
            _dbg(f'[update] {obj.Label}: {e}')

    if doc:
        doc.recompute()

    # Bilan
    lines = []
    if results_ok:
        lines.append(f'✅ {len(results_ok)} '
                     + _t('update_done', default='composant(s) mis à jour :'))
        for label, ref, nb in results_ok:
            lines.append(f'   • {label}  →  {ref}  ({nb} '
                         + _t('update_params_word', default='paramètre(s)') + ')')
    if results_err:
        if lines:
            lines.append('')
        lines.append(f'❌ {len(results_err)} '
                     + _t('update_errors', default='erreur(s) :'))
        for label, err in results_err:
            lines.append(f'   • {label} : {err}')

    msg = '\n'.join(lines) if lines else _t('update_nothing_to_do')
    _dbg(f'[update] {len(results_ok)} ok, {len(results_err)} erreur(s)')
    if results_err:
        QtWidgets.QMessageBox.warning(None, _t('update_title'), msg)
    else:
        QtWidgets.QMessageBox.information(None, _t('update_title'), msg)
