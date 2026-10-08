# -*- coding: utf-8 -*-
"""
StandardPartSelector.insertion — Insertion et placement des composants.
"""
import FreeCAD as App, os, ast, types
from config import _dbg
from config import _t, _get_library_path, MACRO_DIR
from catalog import load_catalog
from sc_props import _find_varset_in_object, _update_sc_varset
from geometry import _highlight

def generate_component(comp_key, family, variant, ref, params, cfg,
                       geo_info=None):
    fcstd_path = cfg.get('fcstd')
    _dbg(f'[gen] comp_key={comp_key!r} family={family!r} variant={variant!r}')
    _dbg(f'[gen] ref={ref!r}  fcstd={fcstd_path!r}')
    _dbg(f'[gen] params keys={list(params.keys())}')
    if not fcstd_path:
        raise ValueError(_t("error_no_fcstd", family=family, variant=variant))

    label = ref                                             # 1.3 — suppression de l'espace final

    print(_t("separator"))
    print(_t("component_label") + comp_key)
    print(_t("family_label")    + f"{family} — {variant}")
    print(_t("reference_label") + ref)
    print(_t("fcstd_label")     + os.path.basename(fcstd_path))

    # Charger Component.py
    component_module = os.path.join(MACRO_DIR, 'Component.py')
    if not os.path.exists(component_module):
        raise FileNotFoundError(_t("error_component_py") + component_module)

    src  = open(component_module, 'r', encoding='utf-8').read()
    tree = ast.parse(src)
    ast.fix_missing_locations(tree)
    code = compile(tree, component_module, 'exec')
    bb   = types.ModuleType('Component')
    bb.__file__ = component_module
    bb.App = App
    exec(code, bb.__dict__)

    excluded_keys = cfg.get('excluded_keys', set())         # 2.2
    data_props    = cfg.get('data_props', {})               # colonnes propagées
    root, _ = bb.create_component(
                        params=params, fcstd_path=fcstd_path, label=label,
                        excluded_keys=excluded_keys,
                        comp_key=comp_key, family=family, variant=variant,
                        ref=ref, geo_info=geo_info,
                        data_props=data_props)                               # 5A.1
    return root


def _apply_placement(root, geo_info, local_axis='X', invert=False):
    """
    Positionne root selon la géométrie sélectionnée (ou à l'origine).

    Avec geo_info :
      - centre  = centre du cercle/cylindre sélectionné
      - src     = axe local choisi (local_axis) du composant
      - target  = axe normal de la géométrie (éventuellement inversé)

    Sans geo_info :
      - centre  = origine (0,0,0)
      - src     = axe X du composant (axe principal)
      - target  = axe monde correspondant au bouton local_axis
    """
    import math
    try:
        import FreeCAD as _FC
        _world = {
            'X': _FC.Vector(1, 0, 0),
            'Y': _FC.Vector(0, 1, 0),
            'Z': _FC.Vector(0, 0, 1),
        }

        if geo_info is not None:
            # ── Avec géométrie ────────────────────────────────────────────
            c = geo_info['center']
            a = geo_info['axis']
            center = _FC.Vector(float(c.x), float(c.y), float(c.z))
            ax, ay, az = float(a.x), float(a.y), float(a.z)
            length = math.sqrt(ax*ax + ay*ay + az*az)
            if length < 1e-10:
                _dbg('[placement] Axe géométrie nul — ignoré')
                return
            normal = _FC.Vector(ax/length, ay/length, az/length)
            target = normal   # pour le print (non-inversée)
            src    = _world.get(local_axis, _FC.Vector(1, 0, 0))
            _pm    = {'X': 'Y', 'Y': 'Z', 'Z': 'X'}

            # Étape 1 : aligner src sur normal (SANS inversion)
            dot       = max(-1.0, min(1.0, src.dot(normal)))
            angle     = math.degrees(math.acos(dot))
            cross     = src.cross(normal)
            cross_len = cross.Length
            if cross_len < 1e-10:
                rot1 = _FC.Rotation() if dot > 0 else _FC.Rotation(
                    _world['Y'] if abs(src.x) > 0.9 else _world['X'], 180)
            else:
                rot1 = _FC.Rotation(
                    _FC.Vector(cross.x/cross_len,
                               cross.y/cross_len,
                               cross.z/cross_len), angle)

            # Étape 2 : roll via radial (référence = normal)
            rot    = rot1
            wr_raw = geo_info.get('radial')
            if wr_raw is not None:
                wr  = _FC.Vector(wr_raw)
                rp  = rot1.multVec(_world[_pm[local_axis]])
                wj  = wr - normal * normal.dot(wr)
                if wj.Length > 1e-10:
                    wj    = wj.normalize()
                    cos_r = max(-1.0, min(1.0, rp.dot(wj)))
                    sin_r = normal.dot(rp.cross(wj))
                    roll  = math.degrees(math.atan2(sin_r, cos_r))
                    rot   = _FC.Rotation(normal, roll) * rot1
                    _dbg(f'[placement] roll={roll:.1f}°')

            # Étape 3 : inversion = post-composition en espace composant
            _pm_flip = {'X': 'Y', 'Y': 'X', 'Z': 'X'}
            if invert:
                rot = rot * _FC.Rotation(_world[_pm_flip[local_axis]], 180)

        else:
            # ── Sans géométrie ────────────────────────────────────────────
            # Même sémantique que le mode géo :
            #   src    = axe LOCAL choisi du composant
            #   target = Z monde (orientation "droite" naturelle)
            # Z → Z : identité (orientation par défaut, aucune rotation)
            # X → Z : composant tourné pour que son X pointe vers le haut
            # Y → Z : composant tourné pour que son Y pointe vers le haut
            center = _FC.Vector(0, 0, 0)
            src    = _world.get(local_axis, _FC.Vector(0, 0, 1))
            w_up   = _FC.Vector(0, 0, 1)   # Z monde = axe cible
            target = _FC.Vector(-w_up.x, -w_up.y, -w_up.z) if invert else w_up
            dot       = max(-1.0, min(1.0, src.dot(target)))
            angle     = math.degrees(math.acos(dot))
            cross     = src.cross(target)
            cross_len = cross.Length
            if cross_len < 1e-10:
                rot = _FC.Rotation() if dot > 0 else _FC.Rotation(
                    _world['Y'] if abs(src.x) > 0.9 else _world['X'], 180)
            else:
                rot = _FC.Rotation(
                    _FC.Vector(cross.x/cross_len,
                               cross.y/cross_len,
                               cross.z/cross_len), angle)

        # ── Application du Placement ──────────────────────────────────────
        placement = _FC.Placement(center, rot)
        root.Placement = placement
        # Forcer le recalcul du document pour mettre à jour le rendu 3D
        if root.Document:
            root.Document.recompute()
        # Vérification immédiate : le setter a-t-il été pris en compte ?
        actual = root.Placement.Base
        _dbg(f'[placement] mode={"geo" if geo_info else "libre"}  '
              f'local={local_axis}  inv={invert}  angle={angle:.1f}°')
        _dbg(f'[placement] cible  : ({center.x:.3f},{center.y:.3f},{center.z:.3f})')
        _dbg(f'[placement] lu     : ({actual.x:.3f},{actual.y:.3f},{actual.z:.3f})')
        if abs(actual.x-center.x)>0.01 or abs(actual.y-center.y)>0.01 \
                or abs(actual.z-center.z)>0.01:
            _dbg(f'[placement] ⚠ NON APPLIQUÉ — TypeId={root.TypeId}')
        return placement

    except Exception as e:
        import traceback as _tb
        _dbg(f'[placement] Erreur : {e}')
        print(_tb.format_exc())
    return None



# ============================================================
# Mini dialogue d'orientation post-insertion (5B.1)
# ============================================================
def _show_orientation_dialog(root, geo_info, QtWidgets, QtCore):
    """
    Dialogue réduit affiché après chaque insertion.
    - geo_info présent : contrôles axe/inversion actifs, placement en temps réel
    - geo_info absent  : contrôles désactivés
    Retourne True si l'utilisateur veut insérer un autre composant, False sinon.
    """
    doc = root.Document if root is not None else None
    has_geo = geo_info is not None

    dlg = QtWidgets.QDialog()
    dlg.setWindowTitle(_t('orient_title'))
    dlg.setFixedWidth(300)
    # Rester au premier plan même après un clic dans la vue 3D
    dlg.setWindowFlags(
        dlg.windowFlags()
        | QtCore.Qt.WindowStaysOnTopHint
        | QtCore.Qt.Tool)
    layout = QtWidgets.QVBoxLayout()

    # Axe + inversion — toujours visibles
    # Avec géo : src=local_axis, target=axe géométrie
    # Sans géo : src=X composant, target=axe monde du bouton choisi
    axis_group = QtWidgets.QButtonGroup(dlg)
    axis_btns  = {}
    axis_layout = QtWidgets.QHBoxLayout()
    axis_layout.addWidget(QtWidgets.QLabel(_t('axis_label')))
    for name in ('X', 'Y', 'Z'):
        btn = QtWidgets.QRadioButton(name)
        btn.setChecked(name == 'Z')   # Z par défaut dans les deux modes
        axis_group.addButton(btn)
        axis_layout.addWidget(btn)
        axis_btns[name] = btn
    layout.addLayout(axis_layout)
    invert_cb = QtWidgets.QCheckBox(_t('orient_invert'))
    layout.addWidget(invert_cb)

    # Auto-sélectionner Z pour le mode géométrique (arc et cercle)
    if has_geo and geo_info:
        try:
            axis_btns['Z'].setChecked(True)
            invert_cb.setChecked(False)
            _dbg(f'[orient] Auto : Z inv=False')
        except Exception as _oe:
            _dbg(f'[orient] Auto-sélection erreur : {_oe}')

    if not has_geo:
        lbl = QtWidgets.QLabel(_t('orient_no_geo'))
        lbl.setStyleSheet('color: gray; font-style: italic;')
        layout.addWidget(lbl)

    # ── Offset XYZ ──────────────────────────────────────────────────────────
    layout.addWidget(QtWidgets.QLabel(_t('offset_label')))
    offset_layout = QtWidgets.QGridLayout()
    offset_spins  = {}
    for col, axis in enumerate(('X', 'Y', 'Z')):
        lbl_o = QtWidgets.QLabel(axis)
        lbl_o.setAlignment(QtCore.Qt.AlignCenter)
        spn = QtWidgets.QDoubleSpinBox()
        spn.setRange(-9999.0, 9999.0)
        spn.setSingleStep(0.1)
        spn.setDecimals(2)
        spn.setValue(0.0)
        spn.setSuffix(' mm')
        spn.setFixedWidth(90)
        offset_layout.addWidget(lbl_o, 0, col)
        offset_layout.addWidget(spn,   1, col)
        offset_spins[axis] = spn
    layout.addLayout(offset_layout)

    # Boutons OK (terminé) / Insérer un autre
    btn_layout = QtWidgets.QHBoxLayout()
    ok_btn    = QtWidgets.QPushButton(_t('orient_ok'))
    new_btn   = QtWidgets.QPushButton(_t('orient_new'))
    ok_btn.setDefault(True)       # OK = bouton par défaut (Entrée)
    btn_layout.addWidget(ok_btn)   # OK en premier (position primaire)
    btn_layout.addWidget(new_btn)
    layout.addLayout(btn_layout)

    # Résultat : False = terminé, True = insérer un autre
    result = [False]
    def _on_ok():
        _highlight(root, on=False)
        result[0] = False
        dlg.accept()
    def _on_new():
        _highlight(root, on=False)
        result[0] = True
        dlg.accept()
    ok_btn.clicked.connect(_on_ok)
    new_btn.clicked.connect(_on_new)

    dlg.setLayout(layout)

    def _update_placement():
        if root is None: return
        local_axis = next((n for n, b in axis_btns.items()
                           if b.isChecked()), 'X')
        invert     = invert_cb.isChecked()
        _apply_placement(root, geo_info,
                         local_axis=local_axis, invert=invert)
        # Appliquer l'offset dans le repère de l'arc d'insertion
        try:
            from FreeCAD import Vector as _V, Placement as _Pl
            ox = offset_spins['X'].value()
            oy = offset_spins['Y'].value()
            oz = offset_spins['Z'].value()
            p  = root.Placement
            if geo_info and (abs(ox) > 1e-10 or
                             abs(oy) > 1e-10 or
                             abs(oz) > 1e-10):
                # Repère de l'arc :
                #   Z = normale (axe d'insertion)
                #   X = radiale (centre → p0)
                #   Y = Z × X  (tangent)
                _nz = _V(geo_info['axis']).normalize()
                _raw_r = geo_info.get('radial')
                if _raw_r is not None:
                    _nx = _V(_raw_r).normalize()
                else:
                    # Pas de radial → construire un X arbitraire ⊥ Z
                    _ref = _V(1,0,0) if abs(_nz.dot(_V(1,0,0))) < 0.9 \
                           else _V(0,1,0)
                    _nx = (_nz.cross(_ref)).normalize()
                _ny = _nz.cross(_nx).normalize()
                off_world = _nx * ox + _ny * oy + _nz * oz
                root.Placement = _Pl(p.Base + off_world, p.Rotation)
            elif abs(ox) > 1e-10 or abs(oy) > 1e-10 or abs(oz) > 1e-10:
                # Sans geo_info : offset en coords monde
                off_world = p.Rotation.multVec(_V(ox, oy, oz))
                root.Placement = _Pl(p.Base + off_world, p.Rotation)
        except Exception as _oe:
            _dbg(f'[orient] Offset ignoré : {_oe}')
        # Pas de recompute : le Placement est un transform libre
        # qui n'a pas besoin de recompute pour être appliqué.
        # processEvents() force le repaint 3D en attente.
        try:
            import FreeCADGui as Gui
            try:
                from PySide2.QtWidgets import QApplication
            except ImportError:
                from PySide.QtWidgets import QApplication
            QApplication.processEvents()   # traite les events Qt dont repaint 3D
            Gui.updateGui()
        except Exception as e:
            _dbg(f'[placement] Refresh ignoré : {e}')

    # Connexions temps réel
    for btn in axis_btns.values():
        btn.toggled.connect(lambda checked, *_: _update_placement() if checked else None)
    invert_cb.stateChanged.connect(lambda *_: _update_placement())
    for spn in offset_spins.values():
        spn.valueChanged.connect(lambda *_: _update_placement())

    # Placement initial si géométrie présente
    _update_placement()
    # Mettre en surbrillance le composant inséré
    _highlight(root, on=True)
    # Flush la vue 3D avant d'ouvrir le dialogue
    try:
        import FreeCADGui as Gui
        from PySide2.QtWidgets import QApplication
        QApplication.processEvents()
        Gui.updateGui()
    except Exception:
        pass

    # show() + QEventLoop local : la vue 3D FreeCAD peut se rafraîchir
    # (dlg.exec() est ApplicationModal et bloque les paint events de la vue)
    try:
        from PySide2.QtCore import QEventLoop
    except ImportError:
        from PySide.QtCore import QEventLoop
    _loop = QEventLoop()
    dlg.finished.connect(_loop.quit)
    dlg.show()
    _loop.exec()
    return result[0]


# ============================================================
# Main
# ============================================================
