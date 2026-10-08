# StandardPartSelector — standard parts for FreeCAD

[![License: LGPL v2.1](https://img.shields.io/badge/License-LGPL_v2.1-blue.svg)](https://www.gnu.org/licenses/old-licenses/lgpl-2.1.html)
[![FreeCAD](https://img.shields.io/badge/FreeCAD-1.0%2B-orange.svg)](https://www.freecad.org)
[![Version](https://img.shields.io/badge/version-1.0-green.svg)](#version-history)

Insert, replace and manage parametric standard parts (bearings, circlips, profiles, magnets…) in FreeCAD from catalogues you can extend yourself.

**Author:** Yves Guillou · **Version:** 1.0 · **License:** LGPL v2.1

---

## Contents

- [Overview](#overview)
- [Installation](#installation)
- [Usage](#usage)
- [Catalogue editor](#catalogue-editor)
- [Creating a source model](#creating-a-source-model)
- [Library layout](#library-layout)
- [SC_ properties](#sc_-properties)
- [Language](#language)
- [Known limitations](#known-limitations)

---

## Overview

Each part family is a **parametric source model** (`.FCStd`) plus a **JSON catalogue** of references and their parameters. When you pick a reference, the macro copies the model into your document and applies the parameters to the model's `App::VarSet`.

The macro also:

- places the part on a selected circular edge or cylindrical face
- recognises parts it inserted earlier, so you can replace them with another reference
- converts a part whose parameters match no reference into a standard part
- synchronises an optional external library with the local one

**Library included in this repository:**

| Family | Content |
|---|---|
| `bearings` | deep-groove ball bearings 60–64, angular-contact ball bearings 70–74, cylindrical roller bearings N, NF, NJ, NU, tapered roller bearings 302–332 |
| `Circlips` | shaft circlips DIN 471 (standard and small) and type ME, bore circlips DIN 472 |
| `Aimants néodyme` | neodymium magnets: discs, cylinders, cubes, blocks, screw-on and self-adhesive types |
| `Profilés` | aluminium profiles 20×20 and 40×40 |
| `Clou` | flat-head nails |

**Check every dimension against your supplier's data before manufacturing.**

---

## Installation

1. Download or clone this repository.
2. Copy `StandardPartSelector.py` and the `StandardPartSelector/` folder into your FreeCAD macro directory. To find it, open the menu **Macro → Macros…**: the directory is shown there.

```
Macro/
├── StandardPartSelector.py           ← entry point, the file you run
└── StandardPartSelector/
    ├── *.py                          ← macro code
    ├── StandardPartSelectorEditor/   ← catalogue editor
    ├── Standards/                    ← the local library (families above)
    ├── locales/                      ← interface languages
    ├── lang.txt                      ← optional: force the language
    └── library_path.txt              ← optional: external library folder
```

3. Run **Macro → Macros… → StandardPartSelector.py → Execute**, or add it to a toolbar.

---

## Usage

### Inserting a part

1. Run the macro with nothing selected.
2. Choose the **family** and the **variant**, then a **reference** in the table.
3. Click **Generate**.
4. In the orientation dialog, set the insertion axis and the offset, then click **OK**.

The part is inserted into the active document as an independent `App::Part` or `PartDesign::Body`. `SC_*` properties are written on it so that the macro recognises it later.

### Inserting on an arc or a circle

Select a **circular edge** (full circle or arc) in the 3D view before running the macro. It reads:

- the **diameter**, to pre-filter the compatible references
- the **normal**, as the proposed insertion axis
- the **centre**, as the insertion point

This works on native parts and on parts **linked into an assembly** (`App::Link`), moved or not.

### Orientation and offset

- choose which **local axis** of the part (X, Y or Z) is aligned with the normal, and **flip** it if needed
- apply an **XYZ offset** in the frame of the arc: Z along the normal, X radial, Y tangential

**Insert another** chains several insertions without closing the macro.

### Replacing a part

Select a part inserted earlier (it carries `SC_*` properties) and run the macro. The interface opens on its family and variant, lists only the references that use **the same source model**, and shows **Replace** instead of **Generate**.

The replacement updates the part's VarSet **in place**: the geometry recomputes without moving the part or breaking assembly constraints. If the part belongs to a linked file, that file is completed first when it was only partially loaded. If it cannot be completed, the replacement is refused with a message, rather than being silently lost.

### Converting to a standard part

If a part carries `SC_*` properties but its parameters match **no reference**, the macro offers to convert it. It proposes a reference name, then opens the catalogue editor pre-filled with the current values.

You can also select a `PartDesign::Body` that has no `SC_*` properties: the macro offers to integrate it into a family.

---

## Catalogue editor

Open it with **Manage catalogues** in the selector. It is a four-step wizard:

| Step | Content |
|---|---|
| 1/4 | Category, family and variant: choose or create. For a new variant, pick the source model and name the JSON file. |
| 2/4 | Columns, read from the model's VarSet (new variants only) |
| 3/4 | References: one row per reference. **+ Row**, **Duplicate**, **↑ / ↓**, **Delete** |
| 4/4 | JSON preview and save |

Column headers can be renamed by double-clicking them. This changes the display label only, not the JSON key.

---

## Creating a source model

The source `.FCStd` is the parametric model that every reference of a variant derives from.

### Required structure

- **Exactly one root object:** an `App::Part` (multi-body part) or a `PartDesign::Body` (single-body part).
- An **`App::VarSet`** holding every parameter. Its property names must match the JSON keys **exactly**, case included. Use length, angle, float or integer properties, and avoid reserved names such as `Label`, `Placement` or `Shape`.
- Every dimension **driven by an expression** pointing at the VarSet:

```
Pad.Length                = <<params>>.length
Sketch.Constraints.bore   = <<params>>.bore_dia / 2
PolarPattern.Occurrences  = <<params>>.ball_count
```

To check a model, change a value by hand in the VarSet and make sure the model recomputes correctly.

### Recommendations

- Add **local coordinate systems** (`LCS_Base`, `LCS_Top`…) placed by expressions. They make assembly constraints easy.
- Give the VarSet valid default values, so that the model recomputes without errors.
- Every PartDesign feature is supported, including MultiTransform.
- Put a PNG named after the model in `thumbnails/`: it is shown in the selector.

### Minimal example

```
App::Part  "AluminiumProfile"
├── App::VarSet  "params"      length = 100 (Length), angle = 0 (Angle)
├── Body → Sketch → Pad        Pad.Length = <<params>>.length
├── LCS_Base
└── LCS_Top                    AttachmentOffset.Base.z = <<params>>.length
```

```json
{
  "_parametres": {"type": "Aluminium profiles 20x20", "fcstd": "Profilé 20x20.FCStd"},
  "_legende":    {"length": "Length (mm)", "angle": "Angle (°)"},
  "profile 20x20 L100": {"length": 100, "angle": 0},
  "profile 20x20 L200": {"length": 200, "angle": 0}
}
```

---

## Library layout

```
Standards/
└── bearings/
    ├── manifest.json
    ├── config.json            ← families, variants and table columns
    ├── models/                ← source models (.FCStd)
    ├── datas/                 ← catalogues (.json)
    └── thumbnails/            ← optional pictures (.png)
```

The library can also live in an **external folder**, for example a network share. Write its path on a line of `library_path.txt`, or set it with the **Library** button of the editor. New or modified files are copied to the local `Standards/` folder at each insertion. Deletions are not propagated.

---

## SC_ properties

Each inserted part carries, on its root object:

| Property | Content |
|---|---|
| `SC_Standard` | library family key (e.g. `bearings`) |
| `SC_Family` | family name |
| `SC_Category` | variant name |
| `SC_Reference` | inserted reference (e.g. `6203`) |
| `SC_TimeStamp` | insertion time, `YYYYMMDD-HHMMSS` |

`SC_Standard` is also the property that [SmartFinder](https://github.com/erwanyves/SmartFinder) uses to recognise these parts.

---

## Language

Five interface languages: English, French, German, Spanish and Italian. The language follows FreeCAD's preference, then the system language, and falls back to English. To force one, write its code (`en`, `fr`, `de`, `es`, `it`) on a line of `StandardPartSelector/lang.txt`.

The family and reference names of the included library are in French.

---

## Known limitations

- A part cannot be replaced by a reference built from a **different** source model. The model filter guarantees consistent geometry.
- Inserted parts are independent copies, not links to the library. Changing a source model does not update existing parts, but the macro warns when it detects a newer model.
- `<ElementMap> Missing element name prefix id` warnings during insertion come from FreeCAD and have no functional effect.

---

## Version history

**1.0** (October 2026): first public release.

---

## License

Licensed under the **GNU Lesser General Public License v2.1**. See [LICENSE](LICENSE).
