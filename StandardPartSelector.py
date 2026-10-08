# -*- coding: utf-8 -*-
# Auteur  : Yves Guillou
# Licence : LGPL-2.1 — voir le fichier LICENSE du dépôt
"""
Macro: StandardPartSelector
Sélecteur universel de composants standardisés pour FreeCAD.
"""

__version__ = "1.0"
import FreeCAD as App
import sys, os, json

# Ajouter le dossier des modules au path — toujours en tete (pas
# seulement si absent) : SmartFinder possede aussi son propre module
# local nomme 'i18n.py' et fait la meme manoeuvre sur sys.path. Si
# _sps_dir reste coince derriere le dossier de SmartFinder entre deux
# lancements de la meme session FreeCAD, 'import i18n' plus bas peut
# charger PAR ERREUR le i18n.py de SmartFinder (meme nom de fichier,
# contenu different) -> libelles d'interface affiches en cle brute.
# Se replacer en tete a chaque lancement evite ca.
_sps_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        'StandardPartSelector')
if _sps_dir in sys.path:
    sys.path.remove(_sps_dir)
sys.path.insert(0, _sps_dir)

# Forcer le rechargement des modules à chaque exécution
# (Python met en cache sys.modules entre les lancements de la macro)
# Liste DÉRIVÉE DU DOSSIER, et non écrite à la main.
#
# POURQUOI (mesuré le 15/09/2026) : la liste en dur couvrait 12 noms alors
# que le dossier en contient 14. `Component.py` (17 Ko) et
# `StandardPartSelectorEditor.py` n'étaient JAMAIS rechargés — un correctif
# déployé dedans ne prenait pas effet sans redémarrer FreeCAD, sans qu'aucun
# MD5 ne le signale. Même défaut que celui mesuré dans ORing la veille.
#
# Les modules de cette macro sont à plat (pas de paquet), le balayage par
# préfixe de Swaging ne s'y applique donc pas : on énumère le dossier, ce
# qui rend la purge insensible à l'ajout d'un module.
_SPS_DIR = os.path.dirname(os.path.abspath(__file__))
_SPS_PKG = os.path.join(_SPS_DIR, 'StandardPartSelector')
try:
    _SPS_MODULES = tuple(sorted(
        os.path.splitext(_f)[0] for _f in os.listdir(_SPS_PKG)
        if _f.endswith('.py') and not _f.startswith('__')))
except OSError:
    # Repli sur la liste historique : mieux vaut purger partiellement que
    # pas du tout, mais le dire.
    print("[SPS] AVERT : dossier des modules illisible, purge partielle")
    _SPS_MODULES = ('config', 'catalog', 'geometry', 'sc_props', 'manifest',
                    'insertion', 'sync', 'update', 'ui_selector',
                    'integration', 'i18n', 'category_labels')
for _m in _SPS_MODULES:
    sys.modules.pop(_m, None)

# ── Imports des modules ───────────────────────────────────────────────────
from config    import (MACRO_DIR, _t, _get_qt,
                       _get_library_path, _build_tr, _dbg, _status)
from catalog   import discover_components, load_catalog
from manifest  import reconcile_all
from geometry  import (_detect_cylindrical_geometry,
                       _detect_cylindrical_diameter, _highlight)
from sc_props  import (_detect_selected_sc_component,
                       _resolve_selected_sc_component, ask_edit_or_new_component,
                       _find_varset_in_object, _update_sc_varset,
                       _find_matching_ref, _make_auto_ref_name,
                       _open_editor_for_new_ref, _handle_sc_selection,
                       _replace_component)
from insertion import (generate_component, _apply_placement,
                       _show_orientation_dialog)
from sync      import _sync_library_to_sc_dir, _cleanup_json_backups
from update    import (_scan_sc_components, _show_update_dialog,
                       _apply_bulk_update)
from ui_selector  import select_component, _save_last_selection
from integration  import open_integration_wizard, _detect_non_sc_body

def main():
    # Détecter si un Body hors-standard est sélectionné
    if _detect_non_sc_body() is not None:
        integrated = open_integration_wizard()
        if not integrated:
            return   # l'utilisateur a refusé ou annulé
        # Après intégration, continuer vers le sélecteur

    # Sync bibliothèque → MACRO_DIR au démarrage
    _sync_library_to_sc_dir()
    QtWidgets, _ = _get_qt()
    print(_t("separator"))
    print(_t("app_title"))

    try:
        components = discover_components()
    except FileNotFoundError as e:
        print(str(e))
        QtWidgets.QMessageBox.critical(None, _t("config_error"), str(e))
        return

    if not components:
        msg = f"No components found in:\n{MACRO_DIR}"
        print(msg)
        QtWidgets.QMessageBox.warning(None, "StandardPartSelector", msg)
        return

    # Réconciliation du manifest de version (une fois par lancement, avant
    # d'ouvrir l'UI) : aligne le jeton SC_TimeStamp de chaque FCStd sur son
    # contenu réel, pour que la détection d'obsolescence (update.py) ne
    # déclenche pas de faux positifs après une copie/sync/restauration qui
    # ne change que le mtime.
    try:
        reconcile_all(components=components)
    except Exception as _rex:
        _dbg(f'[main] reconcile_all ignoré : {_rex}')

    _loop_count = [0]
    while True:                                            # boucle macro persistante
        _loop_count[0] += 1
        _dbg(f'[main] ── itération {_loop_count[0]} ──────────────────')
        # 5B.2 — composant SC sélectionné ?
        QtWidgets2, QtCore2 = _get_qt()
        sc_info   = _detect_selected_sc_component()
        if sc_info is None:
            # Sélection directe de l'objet racine : rien trouvé. Remonter
            # depuis un éventuel sous-élément (face/arête/sommet) jusqu'à un
            # composant SC_ parent — et, si trouvé, demander à l'utilisateur
            # s'il souhaite éditer ce composant ou en créer un nouveau,
            # plutôt que de trancher silencieusement (cf. bug rapporté :
            # sélection d'une arête → insertion au lieu de modification).
            _resolved_sc = _resolve_selected_sc_component()
            if _resolved_sc is not None:
                _choice = ask_edit_or_new_component(_resolved_sc, QtWidgets2)
                if _choice is None:
                    _sync_library_to_sc_dir()
                    _cleanup_json_backups()
                    _dbg('[main] break: ask_edit_or_new_component annulé')
                    break
                if _choice == 'edit':
                    sc_info = _resolved_sc
                # _choice == 'new' → sc_info reste None : flux normal de
                # création par géométrie, inchangé.
        preselect = _handle_sc_selection(sc_info, components, QtWidgets2) if sc_info else None
        # Si _handle_sc_selection a déjà effectué le remplacement → fermer
        if sc_info and preselect is None:
            _sync_library_to_sc_dir()  # toujours silencieux ici
            _cleanup_json_backups()
            _dbg('[main] break: _handle_sc_selection direct')
            break
        # Recharger les composants au cas où l'éditeur aurait ajouté une référence
        if preselect:
            components = discover_components()


        # 5B.1 — face cylindrique (seulement si pas de composant SC sélectionné)
        geo_info  = None if sc_info else _detect_cylindrical_geometry()
        prefilter = geo_info['diameter'] if geo_info else None

        # ── Contexte : bannière descriptive dans le sélecteur ────────────────
        if sc_info and preselect:
            _ctx = {
                'type':   'replace',
                'color':  '#F97316',
                'title':  _t('ctx_replace_title', default='Replacement').format(
                              ref=preselect.get('ref', '?')),
                'detail': _t('ctx_replace_detail', default='{family} · {variant}').format(
                              family=preselect.get('family','?'),
                              variant=preselect.get('variant','?')),
            }
        elif geo_info and geo_info.get('is_lcs'):
            # Repère (LCS) sélectionné directement : position ET orientation
            # reprises du repère — pas de diamètre, donc pas de filtrage
            # automatique de variante (voir geometry.py::_detect_cylindrical_geometry).
            _ctx = {
                'type':   'lcs',
                'color':  '#8B5CF6',
                'title':  _t('ctx_lcs_title'),
                'detail': _t('ctx_lcs_detail'),
            }
        elif geo_info:
            diam = geo_info.get('diameter', '?')
            kind = _t('geo_circle') if geo_info.get('is_circle') else _t('geo_arc')
            _ctx = {
                'type':   'geometry',
                'color':  '#22C55E',
                'title':  _t('ctx_geometry_title', default='Insert on {kind} — Ø {diam} mm').format(
                              kind=kind, diam=diam),
                'detail': _t('ctx_geometry_detail'),
            }
        else:
            _ctx = {
                'type':   'free',
                'color':  '#3B82F6',
                'title':  _t('ctx_free_title'),
                'detail': _t('ctx_free_detail'),
            }

        result = select_component(components, prefilter=prefilter,
                                  preselect=preselect, geo_info=geo_info,
                                  replace_fcstd=(preselect or {}).get('fcstd'),
                                  context_info=_ctx)
        if result is None:
            _sync_library_to_sc_dir()                      # sync bibliothèque → MACRO_DIR
            _cleanup_json_backups()                        # nettoyage backups à la sortie
            _dbg('[main] break: résultat None (annulation)')
            break                                          # Annuler → fin de la macro

        comp_key, family, variant, ref, params, cfg, \
            geo_info, replace_mode, sync_done = result
        _dbg(f'[main] ref={ref!r} replace_mode={replace_mode} '
              f'preselect_obj={bool((preselect or {}).get("obj"))}')
        try:
            if replace_mode and preselect and preselect.get('obj'):
                # ── Mode remplacement ─────────────────────────────────────
                old_obj = preselect['obj']
                _replace_component(old_obj, comp_key, family, variant,
                                   ref, params, cfg, QtWidgets2)
                _save_last_selection(comp_key, family, variant, ref)
                _msg_ok = _t('success_msg', ref=ref)
                print(_msg_ok)
                _status(_msg_ok)
                # highlight reste actif jusqu'au clic suivant de l'utilisateur
                if not sync_done:
                    _sync_library_to_sc_dir()
                _cleanup_json_backups()
                _dbg('[main] break: remplacement OK')
                break
            else:
                # ── Mode insertion normal ─────────────────────────────────
                root = generate_component(comp_key, family, variant, ref, params, cfg,
                                          geo_info=geo_info)
                _save_last_selection(comp_key, family, variant, ref)
                _msg_ok = _t('success_msg', ref=ref)
                print(_msg_ok)
                _status(_msg_ok)
                insert_another = _show_orientation_dialog(
                    root, geo_info, QtWidgets2, QtCore2)
                _dbg(f'[main] insert_another={insert_another}')
                if not insert_another:
                    if not sync_done:
                        _sync_library_to_sc_dir()
                    _cleanup_json_backups()
                    _dbg('[main] break: insertion OK')
                    break
        except Exception as e:
            import traceback
            _dbg(f'[main] EXCEPTION: {e}')
            print(traceback.format_exc())
            QtWidgets.QMessageBox.critical(None, _t("error_generation"), str(e))


try:
    main()
except Exception as _e:
    import traceback as _tb
    _msg = _t("error_startup") + str(_e) + "\n\n" + _tb.format_exc()
    print(_msg)
    try:
        from PySide6 import QtWidgets as _Qt
    except ImportError:
        from PySide2 import QtWidgets as _Qt
    _Qt.QMessageBox.critical(None, "StandardPartSelector", _msg)
