# -*- coding: utf-8 -*-
"""
StandardPartSelector.sync — Synchronisation bibliothèque ↔ Standards.
"""
import os, json, glob
from config import _dbg
from config import MACRO_DIR, STANDARDS_DIR, _t, _get_library_path

def _json_content_equal(path_a, path_b):
    """
    Compare deux fichiers JSON sémantiquement (indépendant de l'ordre des clés
    et de la mise en forme). Retourne True si le contenu est équivalent.
    """
    try:
        with open(path_a, encoding='utf-8') as f: a = json.load(f)
        with open(path_b, encoding='utf-8') as f: b = json.load(f)
        return a == b
    except Exception:
        return False


def _file_equal(path_a, path_b):
    """Comparaison adaptée selon le type de fichier."""
    if path_a.endswith('.json'):
        return _json_content_equal(path_a, path_b)
    # Autres fichiers : comparaison par taille + mtime
    try:
        sa, sb = os.stat(path_a), os.stat(path_b)
        return sa.st_size == sb.st_size and abs(sa.st_mtime - sb.st_mtime) < 2
    except Exception:
        return False


def _collect_sync_actions(lib, sc_dir):
    """
    Analyse les différences entre lib et sc_dir.
    Retourne une liste de dicts décrivant chaque action à effectuer :
      {'action': 'lib→sc' | 'sc→lib' | 'conflit' | 'identique',
       'rel':    chemin relatif depuis la racine comp,
       'comp':   nom du composant,
       'lib':    chemin absolu côté bibliothèque (ou None),
       'sc':     chemin absolu côté MACRO_DIR (ou None)}
    """
    import shutil as _sh
    actions = []

    # Collecter tous les fichiers des deux côtés
    def _scan(base):
        result = {}
        if not os.path.isdir(base):
            return result
        for root, dirs, files in os.walk(base):
            # Ignorer les fichiers backup
            files = [f for f in files
                     if '.bak_' not in f
                     and not f.endswith('.FCBak')
                     and not f.startswith('_')]
            dirs[:] = [d for d in dirs if not d.startswith('_')]
            for fname in files:
                full = os.path.join(root, fname)
                rel  = os.path.relpath(full, base)
                # Ignorer les fichiers à la racine (non-composants :
                # .py, .txt, .json de config, etc.)
                if os.sep not in rel and '/' not in rel:
                    continue
                result[rel] = full
        return result

    lib_files = _scan(lib)
    sc_files  = _scan(sc_dir)
    all_rels  = set(lib_files) | set(sc_files)

    for rel in sorted(all_rels):
        # Extraire le comp_key (premier segment du chemin)
        parts = rel.replace('\\', '/').split('/')
        comp  = parts[0]
        lib_p = lib_files.get(rel)
        sc_p  = sc_files.get(rel)

        if lib_p and not sc_p:
            actions.append({'action': 'lib→sc', 'rel': rel, 'comp': comp,
                            'lib': lib_p, 'sc': os.path.join(sc_dir, rel)})
        elif sc_p and not lib_p:
            actions.append({'action': 'sc→lib', 'rel': rel, 'comp': comp,
                            'lib': os.path.join(lib, rel), 'sc': sc_p})
        elif lib_p and sc_p:
            if _file_equal(lib_p, sc_p):
                actions.append({'action': 'identique', 'rel': rel, 'comp': comp,
                                'lib': lib_p, 'sc': sc_p})
            else:
                mtime_lib = os.path.getmtime(lib_p)
                mtime_sc  = os.path.getmtime(sc_p)
                if mtime_lib > mtime_sc + 2:
                    actions.append({'action': 'lib→sc', 'rel': rel, 'comp': comp,
                                    'lib': lib_p, 'sc': sc_p})
                elif mtime_sc > mtime_lib + 2:
                    actions.append({'action': 'sc→lib', 'rel': rel, 'comp': comp,
                                    'lib': lib_p, 'sc': sc_p})
                else:
                    actions.append({'action': 'conflit', 'rel': rel, 'comp': comp,
                                    'lib': lib_p, 'sc': sc_p})
    return actions


def _apply_sync_actions(actions, conflict_resolution):
    """
    Applique la liste d'actions.
    conflict_resolution : dict {rel: 'lib→sc'|'sc→lib'|'ignorer'}
    Retourne (synced, errors).
    """
    import shutil as _sh
    synced, errors = 0, 0
    for a in actions:
        act = a['action']
        if act == 'identique': continue
        if act == 'conflit':
            act = conflict_resolution.get(a['rel'], 'ignorer')
            if act == 'ignorer': continue
        src = a['lib'] if act == 'lib→sc' else a['sc']
        dst = a['sc']  if act == 'lib→sc' else a['lib']
        try:
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            _sh.copy2(src, dst)
            synced += 1
        except Exception as e:
            errors += 1
            _dbg(f'[sync] Erreur {a["rel"]}: {e}')
    return synced, errors


def _show_sync_dialog(actions, QtWidgets):
    """
    Affiche le rapport de synchronisation et permet la résolution des conflits.
    Retourne (confirmed, conflict_resolution) ou (False, {}) si annulé.
    """
    try:
        from PySide2 import QtCore, QtWidgets as Qw
    except ImportError:
        from PySide  import QtCore, QtGui as Qw

    # Compter les actions
    counts = {'lib→sc': 0, 'sc→lib': 0, 'conflit': 0, 'identique': 0}
    for a in actions:
        counts[a['action']] = counts.get(a['action'], 0) + 1

    # Si rien à faire : message court et retour
    if counts['lib→sc'] == 0 and counts['sc→lib'] == 0 and counts['conflit'] == 0:
        _dbg(f'[sync] {counts["identique"]} fichier(s) identique(s) — rien à faire')
        return False, {}

    dlg = Qw.QDialog()
    dlg.setWindowTitle(_t('sync_title'))
    dlg.setMinimumWidth(680)
    dlg.setMinimumHeight(480)
    lay = Qw.QVBoxLayout()

    # Résumé
    summary = Qw.QLabel(
        f'<b>{_t("sync_summary")}</b><br>')
    parts = []
    if counts['lib→sc']:
        parts.append(f'⬇ {counts["lib→sc"]} {_t("sync_lib_to_sc")}')
    if counts['sc→lib']:
        parts.append(f'⬆ {counts["sc→lib"]} {_t("sync_sc_to_lib")}')
    if counts['conflit']:
        parts.append(f'⚠ {counts["conflit"]} {_t("sync_conflicts")}')
    if counts['identique']:
        parts.append(f'✅ {counts["identique"]} {_t("sync_identical")}')
    summary.setText('<br>'.join(parts))
    summary.setTextFormat(QtCore.Qt.RichText)
    lay.addWidget(summary)

    # Tableau détaillé
    table = Qw.QTableWidget()
    table.setColumnCount(3)
    table.setHorizontalHeaderLabels([
        _t('sync_col_file'), _t('sync_col_action'), _t('sync_col_resolve')])
    table.horizontalHeader().setStretchLastSection(True)
    table.setEditTriggers(Qw.QAbstractItemView.NoEditTriggers)
    table.setSelectionBehavior(Qw.QAbstractItemView.SelectRows)

    conflict_combos = {}   # rel → QComboBox
    display_actions = [a for a in actions if a['action'] != 'identique']
    table.setRowCount(len(display_actions))

    action_labels = {
        'lib→sc': f'⬇ {_t("sync_lib_to_sc")}',
        'sc→lib': f'⬆ {_t("sync_sc_to_lib")}',
        'conflit': f'⚠ {_t("sync_conflict_label")}',
    }
    for row, a in enumerate(display_actions):
        table.setItem(row, 0, Qw.QTableWidgetItem(a['rel'].replace('\\', '/')))
        table.setItem(row, 1, Qw.QTableWidgetItem(
            action_labels.get(a['action'], a['action'])))
        if a['action'] == 'conflit':
            combo = Qw.QComboBox()
            combo.addItems([
                _t('sync_keep_lib'),
                _t('sync_keep_sc'),
                _t('sync_ignore')])
            table.setCellWidget(row, 2, combo)
            conflict_combos[a['rel']] = combo
        else:
            table.setItem(row, 2, Qw.QTableWidgetItem(''))

    table.resizeColumnsToContents()
    lay.addWidget(table)

    # Boutons
    bbox = Qw.QDialogButtonBox(
        Qw.QDialogButtonBox.Ok | Qw.QDialogButtonBox.Cancel)
    bbox.button(Qw.QDialogButtonBox.Ok).setText(_t('sync_apply'))
    bbox.button(Qw.QDialogButtonBox.Cancel).setText(_t('cancel_btn'))
    bbox.accepted.connect(dlg.accept)
    bbox.rejected.connect(dlg.reject)
    lay.addWidget(bbox)
    dlg.setLayout(lay)

    if dlg.exec() != Qw.QDialog.Accepted:
        return False, {}

    # Résoudre les conflits
    resolution_map = {'lib→sc': 'lib→sc', 'sc→lib': 'sc→lib'}
    conflict_resolution = {}
    for rel, combo in conflict_combos.items():
        idx = combo.currentIndex()
        conflict_resolution[rel] = ['lib→sc', 'sc→lib', 'ignorer'][idx]

    return True, conflict_resolution


def _sync_library_to_sc_dir(interactive=False, QtWidgets=None):
    """
    Synchronisation bidirectionnelle bibliothèque ↔ Standards.
    - interactive=False : sync silencieuse lib→Standards (comportement historique)
    - interactive=True  : analyse complète + dialogue de rapport
    """
    lib = _get_library_path()
    if not lib or not os.path.isdir(lib):
        return

    if not interactive:
        # Mode silencieux : lib→Standards uniquement (comportement d'origine)
        import shutil as _sh
        synced, skipped, errors = 0, 0, 0
        for comp_key in os.listdir(lib):
            src_comp = os.path.join(lib, comp_key)
            if not os.path.isdir(src_comp) or comp_key.startswith('_'):
                continue
            dst_comp = os.path.join(STANDARDS_DIR, comp_key)
            for root, dirs, files in os.walk(src_comp):
                dirs[:] = [d for d in dirs if not d.startswith('_')]
                files = [f for f in files if '.bak_' not in f
                         and not f.endswith('.FCBak')
                         and not f.startswith('_')]
                rel      = os.path.relpath(root, src_comp)
                dst_root = os.path.join(dst_comp, rel)
                os.makedirs(dst_root, exist_ok=True)
                for fname in files:
                    src_file = os.path.join(root, fname)
                    dst_file = os.path.join(dst_root, fname)
                    try:
                        # config.json suit la même règle que les autres fichiers :
                        # comparaison sémantique (_file_equal → _json_content_equal)
                        # puis copie si la bibliothèque est plus récente.
                        if not os.path.exists(dst_file):
                            # Nouveau fichier en bibliothèque → copier
                            _sh.copy2(src_file, dst_file)
                            synced += 1
                        elif not _file_equal(src_file, dst_file):
                            # Fichiers différents : copier seulement si
                            # la bibliothèque est plus récente (local protégé)
                            mtime_src = os.path.getmtime(src_file)
                            mtime_dst = os.path.getmtime(dst_file)
                            if mtime_src > mtime_dst + 2:
                                _sh.copy2(src_file, dst_file)
                                synced += 1
                            else:
                                # Local plus récent → édition locale, ne pas écraser
                                skipped += 1
                        else:
                            skipped += 1
                    except Exception as e:
                        errors += 1
                        _dbg(f'[sync] {fname}: {e}')
        if synced or errors:
            _dbg(f'[sync] Bibliothèque → Standards : ' 
                  f'{synced} copié(s), {skipped} identique(s), {errors} erreur(s)')
        return

    # Mode interactif : analyse complète + dialogue
    actions = _collect_sync_actions(lib, STANDARDS_DIR)
    confirmed, conflict_resolution = _show_sync_dialog(actions, QtWidgets)
    if not confirmed:
        return
    synced, errors = _apply_sync_actions(actions, conflict_resolution)
    msg = f'[sync] {synced} fichier(s) synchronisé(s)'
    if errors: msg += f', {errors} erreur(s)'
    print(msg)
    if QtWidgets:
        QtWidgets.QMessageBox.information(
            None, _t('sync_title'), msg)


def _cleanup_json_backups(max_age_days=7, max_per_file=3):
    """
    Supprime les fichiers de sauvegarde JSON (.bak_YYYYMMDD_HHMMSS)
    et les sauvegardes FreeCAD (.FCBak) dans la bibliothèque, Standards,
    et à la racine du dossier macro (ex. category_labels.json.bak_*).
    Conserve au maximum max_per_file backups par fichier JSON,
    et supprime ceux plus anciens que max_age_days jours.
    """
    import glob, time
    from datetime import datetime, timedelta
    cutoff = time.time() - max_age_days * 86400
    cleaned = 0

    def _cleanup_bak_files(bak_files):
        nonlocal cleaned
        # Grouper par fichier source
        groups = {}
        for bak in bak_files:
            # Extraire le fichier source (avant .bak_)
            base = bak[:bak.index('.bak_')]
            groups.setdefault(base, []).append(bak)

        for base, baks in groups.items():
            # Trier par date (plus récent en premier)
            baks.sort(reverse=True)
            for i, bak in enumerate(baks):
                try:
                    mtime = os.path.getmtime(bak)
                    # Supprimer si trop ancien OU si excède max_per_file
                    if mtime < cutoff or i >= max_per_file:
                        os.remove(bak)
                        cleaned += 1
                except Exception:
                    pass

    # Chercher dans Standards et dans la bibliothèque externe (récursif)
    search_dirs = [STANDARDS_DIR]
    lib = _get_library_path()
    if lib: search_dirs.append(lib)

    for search_dir in search_dirs:
        pattern = os.path.join(search_dir, '**', '*.json.bak_*')
        _cleanup_bak_files(glob.glob(pattern, recursive=True))

    # Racine du dossier macro (non récursif : Standards/lib déjà couverts
    # ci-dessus, inutile de rescanner tout l'arbre) : couvre
    # category_labels.json.bak_* et tout autre backup JSON créé directement
    # à la racine plutôt que sous Standards/.
    _cleanup_bak_files(glob.glob(os.path.join(MACRO_DIR, '*.json.bak_*')))

    if cleaned:
        _dbg(f'[cleanup] {cleaned} backup(s) JSON supprimé(s)')

    # Supprimer les .FCBak dans les dossiers models/
    fcbak_cleaned = 0
    for search_dir in search_dirs:
        pattern_fb = os.path.join(search_dir, '**', '*.FCBak')
        for fbak in glob.glob(pattern_fb, recursive=True):
            try:
                os.remove(fbak)
                fcbak_cleaned += 1
            except Exception as e:
                _dbg(f'[cleanup] FCBak {os.path.basename(fbak)}: {e}')
    if fcbak_cleaned:
        _dbg(f'[cleanup] {fcbak_cleaned} fichier(s) .FCBak supprimé(s)')


