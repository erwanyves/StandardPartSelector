# -*- coding: utf-8 -*-
"""
StandardPartSelector.manifest — Suivi de version des templates FCStd.

Modèle de version
-----------------
Chaque template FCStd porte un **jeton de version** dans son VarSet :
`SC_TimeStamp` ('YYYYMMDD-HHMMSS'). C'est l'unique autorité pour décider
si un composant inséré est obsolète : composant.SC_TimeStamp != jeton du
template  ⇒  obsolète (comparaison par ÉGALITÉ, gère aussi les rollbacks).

Chaque famille (`STANDARDS_DIR/<comp_key>/`) possède un manifest
`manifest.json` qui sert d'index rapide :

    {
      "_version": 1,
      "fcstd": {
        "33212.FCStd": { "sc_timestamp": "20260605-104349", "mtime": 1749112429.0 },
        ...
      }
    }

  - `sc_timestamp` : copie cache du jeton du VarSet (évite d'ouvrir le FCStd
                     à chaque scan de mise à jour).
  - `hash`         : empreinte SHA-1 du *contenu de modèle* du FCStd. C'est le
                     *détecteur* d'édition réelle : une copie, un sync ou une
                     sauvegarde sans modification laissent le hash identique.
  - `mtime`        : *filtre rapide* — on ne recalcule le hash que si le mtime
                     a bougé (un vrai changement bumpe toujours le mtime).
                     Le mtime ne juge jamais l'obsolescence à lui seul.

Réconciliation (passe A)
------------------------
La macro se ferme lorsqu'on édite un modèle dans FreeCAD ; notre code n'est
donc pas présent à la sauvegarde. Au **lancement suivant**, `reconcile_all()` :

  - manifest absent / FCStd inconnu → SEED : on enregistre jeton + hash + mtime
    sans rien modifier (migration non destructive de l'existant).
  - mtime identique → rien à faire (filtre rapide).
  - mtime changé mais hash identique → simple touch/copie/sauvegarde sans
    modification : pas de bump, on réaligne le mtime.
  - mtime changé ET hash différent → édition réelle : nouveau jeton (now())
    dans le VarSet, sauvegarde du FCStd, mise à jour jeton + hash + mtime.

Les éditions *via l'interface* (éditeur de catalogues) doivent appeler
`mark_edited()` au moment de la sauvegarde : le code tourne, on pose le jeton
et on aligne le manifest directement, sans attendre la réconciliation.
"""
import os
import json
import hashlib
import zipfile
from datetime import datetime

from config import STANDARDS_DIR

MANIFEST_NAME    = 'manifest.json'
MANIFEST_VERSION = 2
_TS_FMT          = '%Y%m%d-%H%M%S'
_MTIME_TOL       = 1.0   # tolérance (s) sur le mtime (filtre rapide)

# Entrées du ZIP FCStd ignorées par le hash de contenu :
# elles changent sans modification du modèle (état de vue, caméra, couleurs,
# vignettes) et ne doivent donc pas déclencher d'obsolescence.
_HASH_EXCLUDE_EXACT  = {'GuiDocument.xml'}
_HASH_EXCLUDE_PREFIX = ('thumbnails/',)


# ════════════════════════════════════════════════════════════════════════════
# Helpers horodatage
# ════════════════════════════════════════════════════════════════════════════

def now_token():
    """Nouveau jeton de version au format 'YYYYMMDD-HHMMSS'."""
    return datetime.now().strftime(_TS_FMT)


# ════════════════════════════════════════════════════════════════════════════
# Lecture / écriture du manifest
# ════════════════════════════════════════════════════════════════════════════

def manifest_path(comp_key):
    return os.path.join(STANDARDS_DIR, comp_key, MANIFEST_NAME)


def models_dir(comp_key):
    return os.path.join(STANDARDS_DIR, comp_key, 'models')


def load_manifest(comp_key):
    """Charge le manifest d'une famille. Retourne une structure vide si absent."""
    path = manifest_path(comp_key)
    if not os.path.exists(path):
        return {'_version': MANIFEST_VERSION, 'fcstd': {}}
    try:
        with open(path, encoding='utf-8') as f:
            data = json.load(f)
        data.setdefault('_version', MANIFEST_VERSION)
        data.setdefault('fcstd', {})
        return data
    except Exception as e:
        print(f'[manifest] {comp_key}/manifest.json illisible : {e}')
        return {'_version': MANIFEST_VERSION, 'fcstd': {}}


def save_manifest(comp_key, data):
    """Écrit le manifest d'une famille (création du dossier si besoin)."""
    path = manifest_path(comp_key)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        print(f'[manifest] Écriture impossible ({comp_key}) : {e}')
        return False


def content_hash(fcstd_path):
    """
    Hash SHA-1 du *contenu de modèle* d'un FCStd (archive ZIP).

    Couvre toutes les entrées du ZIP (Document.xml + formes .brp, etc.) SAUF
    celles qui changent sans modification du modèle (GuiDocument.xml, vignettes).
    Insensible au mtime, aux copies et aux sauvegardes sans modification réelle :
    deux fichiers au contenu identique ont le même hash.

    Retourne l'empreinte hexadécimale, ou None si le fichier est illisible.

    Note : Document.xml contient SC_TimeStamp. Aucun masquage n'est nécessaire :
    seule notre propre écriture du jeton modifie cette valeur, et le hash est
    immédiatement réenregistré après chaque écriture (mark_edited / bump). Une
    sauvegarde sans modification ne touche pas au jeton → hash stable.
    """
    if not fcstd_path or not os.path.exists(fcstd_path):
        return None
    try:
        h = hashlib.sha1()
        with zipfile.ZipFile(fcstd_path, 'r') as z:
            for name in sorted(z.namelist()):
                if name in _HASH_EXCLUDE_EXACT:
                    continue
                if name.startswith(_HASH_EXCLUDE_PREFIX):
                    continue
                h.update(name.encode('utf-8'))
                h.update(b'\0')
                h.update(z.read(name))
                h.update(b'\0')
        return h.hexdigest()
    except Exception as e:
        print(f'[manifest] Hash impossible ({fcstd_path}) : {e}')
        return None


def get_source_timestamp(comp_key, fcstd_path):
    """
    Lookup rapide du jeton de version d'un FCStd via le manifest.
    Retourne 'YYYYMMDD-HHMMSS' ou None. N'ouvre aucun document.
    Utilisé par le scan de mise à jour (update.py).
    """
    name = os.path.basename(fcstd_path or '')
    if not name:
        return None
    entry = load_manifest(comp_key).get('fcstd', {}).get(name)
    return entry.get('sc_timestamp') if entry else None


# ════════════════════════════════════════════════════════════════════════════
# Accès au VarSet du FCStd (jeton de version embarqué)
# ════════════════════════════════════════════════════════════════════════════

def _find_varset(doc):
    """VarSet racine attendu, repli sur tout objet portant SC_TimeStamp."""
    vs = doc.getObject('VarSet')
    if vs is not None:
        return vs
    for o in doc.Objects:
        if 'SC_TimeStamp' in (getattr(o, 'PropertiesList', None) or []):
            return o
    # Dernier repli : premier App::VarSet rencontré
    for o in doc.Objects:
        if getattr(o, 'TypeId', '') == 'App::VarSet':
            return o
    return None


def _open_source(fcstd_path, App):
    """
    Ouvre (ou récupère) le document FCStd.
    Retourne (doc, opened_here). opened_here=False si déjà ouvert par ailleurs.
    """
    key = os.path.normcase(os.path.abspath(fcstd_path))
    for d in App.listDocuments().values():
        try:
            if d.FileName and os.path.normcase(
                    os.path.abspath(d.FileName)) == key:
                return d, False
        except Exception:
            continue
    return App.openDocument(fcstd_path), True


def read_varset_timestamp(fcstd_path):
    """
    Lit le SC_TimeStamp embarqué dans le VarSet du FCStd. Lecture seule.
    Retourne la chaîne ou None. Ne ferme pas un document déjà ouvert.
    """
    import FreeCAD as App
    if not fcstd_path or not os.path.exists(fcstd_path):
        return None
    prev = App.ActiveDocument
    doc = None
    opened_here = False
    try:
        doc, opened_here = _open_source(fcstd_path, App)
        vs = _find_varset(doc)
        if vs is not None:
            props = getattr(vs, 'PropertiesList', None) or []
            if 'SC_TimeStamp' in props and vs.SC_TimeStamp:
                return str(vs.SC_TimeStamp)
        return None
    except Exception as e:
        print(f'[manifest] Lecture jeton ignorée ({fcstd_path}) : {e}')
        return None
    finally:
        if opened_here and doc is not None:
            try: App.closeDocument(doc.Name)
            except Exception: pass
        try:
            if prev is not None:
                App.setActiveDocument(prev.Name)
        except Exception:
            pass


def write_varset_timestamp(fcstd_path, token):
    """
    Pose `token` dans le VarSet du FCStd et sauvegarde le fichier.
    Crée la propriété SC_TimeStamp si absente. Retourne True/False.
    """
    import FreeCAD as App
    if not fcstd_path or not os.path.exists(fcstd_path):
        return False
    prev = App.ActiveDocument
    doc = None
    opened_here = False
    try:
        doc, opened_here = _open_source(fcstd_path, App)
        vs = _find_varset(doc)
        if vs is None:
            print(f'[manifest] Aucun VarSet dans {os.path.basename(fcstd_path)}')
            return False
        if 'SC_TimeStamp' not in (vs.PropertiesList or []):
            vs.addProperty('App::PropertyString',
                           'SC_TimeStamp', 'StandardPartSelector')
        vs.SC_TimeStamp = token
        doc.recompute()
        doc.save()
        return True
    except Exception as e:
        print(f'[manifest] Écriture jeton impossible ({fcstd_path}) : {e}')
        return False
    finally:
        if opened_here and doc is not None:
            try: App.closeDocument(doc.Name)
            except Exception: pass
        try:
            if prev is not None:
                App.setActiveDocument(prev.Name)
        except Exception:
            pass


# ════════════════════════════════════════════════════════════════════════════
# Édition via l'interface (canal éditeur)
# ════════════════════════════════════════════════════════════════════════════

def mark_edited(comp_key, fcstd_path, token=None):
    """
    À appeler après une édition *via l'interface* d'un template/variante.
    Pose un nouveau jeton dans le VarSet, sauve le FCStd, et aligne le manifest
    (jeton + mtime). Retourne le jeton posé, ou None en cas d'échec.

    Évite que la réconciliation re-déclenche un bump : le mtime stocké
    correspond au fichier juste sauvegardé.
    """
    token = token or now_token()
    if not write_varset_timestamp(fcstd_path, token):
        return None
    data  = load_manifest(comp_key)
    name  = os.path.basename(fcstd_path)
    try:
        mt = os.path.getmtime(fcstd_path)
    except OSError:
        mt = 0.0
    data['fcstd'][name] = {'sc_timestamp': token,
                           'hash': content_hash(fcstd_path),
                           'mtime': mt}
    save_manifest(comp_key, data)
    print(f'[manifest] {comp_key}/{name} marqué édité → {token}')
    return token


# ════════════════════════════════════════════════════════════════════════════
# Réconciliation (passe A) — édition hors interface
# ════════════════════════════════════════════════════════════════════════════

def _family_fcstd_map(comp_key):
    """
    Retourne {basename: chemin_résolu} des FCStd *référencés par le config.json*
    de la famille, résolus comme le reste de l'application (STANDARDS_DIR
    prioritaire, repli bibliothèque). Couvre donc exactement les templates
    réellement utilisés par le catalogue, quel que soit leur emplacement
    physique — et pas seulement ceux présents dans models/ local.

    Plusieurs variantes peuvent partager un même FCStd → dédupliqué par nom.
    """
    try:
        from catalog import load_comp_config, _resolve_fcstd
    except Exception as e:
        print(f'[manifest] catalog indisponible ({comp_key}) : {e}')
        return {}

    raw    = load_comp_config(comp_key)
    result = {}
    for family, variants in raw.items():
        if family.startswith('_') or not isinstance(variants, dict):
            continue
        for variant, entry_data in variants.items():
            if variant.startswith('_') or not isinstance(entry_data, dict):
                continue
            fn = entry_data.get('fcstd')
            if not fn:
                continue
            name = os.path.basename(fn)
            path = _resolve_fcstd(comp_key, name)
            if path and os.path.exists(path):
                result[name] = path
    return result


def reconcile_family(comp_key, fmap=None):
    """
    Réconcilie une famille (voir docstring du module).

    fmap : {basename: chemin_résolu} des FCStd à suivre. Si None, déduit du
    config.json local de la famille (`_family_fcstd_map`). Quand `reconcile_all`
    pilote la passe, il fournit la map issue de `discover_components()`, qui
    couvre aussi les familles/templates résidant en bibliothèque.

    Retourne un dict bilan : {'seeded': [...], 'bumped': [...], 'orphaned': [...]}.
    """
    data    = load_manifest(comp_key)
    entries = data.setdefault('fcstd', {})
    if fmap is None:
        fmap = _family_fcstd_map(comp_key)   # {basename: chemin résolu}
    present = set(fmap)

    seeded, bumped = [], []
    dirty = False

    for name, fcstd_path in sorted(fmap.items()):
        try:
            real_mtime = os.path.getmtime(fcstd_path)
        except OSError:
            continue

        entry = entries.get(name)

        # ── SEED : fichier inconnu → enregistrer l'état existant ──────────
        if entry is None:
            token = read_varset_timestamp(fcstd_path)
            if not token:
                # Template sans jeton : init unique (now()), une seule écriture.
                token = now_token()
                if write_varset_timestamp(fcstd_path, token):
                    try: real_mtime = os.path.getmtime(fcstd_path)
                    except OSError: pass
            entries[name] = {'sc_timestamp': token,
                             'hash': content_hash(fcstd_path),
                             'mtime': real_mtime}
            seeded.append(name)
            dirty = True
            continue

        # ── Filtre rapide : mtime inchangé ⇒ contenu inchangé ─────────────
        # Un changement de contenu bumpe toujours le mtime ; l'inverse est faux.
        # Donc « mtime identique » permet de sauter sans ouvrir le ZIP.
        stored_mtime = entry.get('mtime', 0.0)
        if abs(real_mtime - stored_mtime) <= _MTIME_TOL:
            continue

        # ── mtime modifié → le hash de contenu tranche ────────────────────
        cur_hash    = content_hash(fcstd_path)
        stored_hash = entry.get('hash')

        if cur_hash is not None and stored_hash is not None \
                and cur_hash == stored_hash:
            # Contenu identique : simple touch / copie / sync / sauvegarde
            # sans modification. Pas de bump ; on réaligne juste le mtime.
            entry['mtime'] = real_mtime
            dirty = True
            continue

        if cur_hash is None:
            # FCStd illisible pour le hash : conservateur, ne pas bumper.
            entry['mtime'] = real_mtime
            dirty = True
            continue

        # Contenu réellement modifié → nouveau jeton de version
        token = now_token()
        if write_varset_timestamp(fcstd_path, token):
            try: real_mtime = os.path.getmtime(fcstd_path)   # post-save
            except OSError: pass
            entry['sc_timestamp'] = token
            entry['hash']         = content_hash(fcstd_path)  # post-save
            entry['mtime']        = real_mtime
            bumped.append(name)
            dirty = True
        else:
            # Écriture impossible (ex. template en bibliothèque read-only) :
            # aligner mtime+hash pour ne pas re-déclencher, jeton conservé.
            entry['hash']  = cur_hash
            entry['mtime'] = real_mtime
            dirty = True

    # ── Entrées orphelines (FCStd retiré du catalogue) ───────────────────
    orphaned = [n for n in list(entries) if n not in present]
    for n in orphaned:
        del entries[n]
        dirty = True

    if dirty:
        save_manifest(comp_key, data)

    return {'seeded': seeded, 'bumped': bumped, 'orphaned': orphaned}


def reconcile_all(components=None, verbose=True):
    """
    Réconcilie toutes les familles connues du catalogue.

    À appeler UNE fois au lancement (sélecteur ET éditeur), avant d'ouvrir l'UI.

    La liste des familles et les chemins FCStd proviennent de
    `discover_components()` — la même source que l'insertion et la mise à jour —
    qui scanne STANDARDS_DIR **et** la bibliothèque. Les templates résidant
    uniquement en bibliothèque sont donc bien suivis (ce que l'ancienne version,
    limitée à un glob de STANDARDS_DIR/models, manquait).

    `components` peut être fourni pour éviter un second `discover_components()`.
    Retourne {comp_key: bilan}.
    """
    summary = {}
    if components is None:
        try:
            from catalog import discover_components
            components = discover_components()
        except Exception as e:
            print(f'[manifest] discover_components indisponible : {e}')
            return summary

    for comp_key, cdata in sorted(components.items()):
        # Construire {basename: chemin résolu} pour toutes les variantes
        fmap = {}
        for family, variants in (cdata.get('cfg') or {}).items():
            if not isinstance(variants, dict):
                continue
            for variant, vcfg in variants.items():
                if not isinstance(vcfg, dict):
                    continue
                fp = vcfg.get('fcstd')
                if fp and os.path.exists(fp):
                    fmap[os.path.basename(fp)] = fp
        if not fmap:
            continue

        res = reconcile_family(comp_key, fmap)
        if res['seeded'] or res['bumped'] or res['orphaned']:
            summary[comp_key] = res
            if verbose:
                print(f'[manifest] {comp_key}: '
                      f'seed={len(res["seeded"])} '
                      f'bump={len(res["bumped"])} '
                      f'orphan={len(res["orphaned"])}')
    return summary
