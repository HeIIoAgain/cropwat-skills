---
name: cropwat-inputs
description: Build and validate CROPWAT 8.0 input files — climate (.PEM), rain (.CRM), crop (.CRO), soil (.SOI) — from raw weather data. Use when asked to generate CROPWAT files, compute reference evapotranspiration ETo with FAO-56 Penman-Monteith, convert station or gridded weather data into CROPWAT's fixed-width formats, or check why CROPWAT results look wrong because of a malformed input file. Covers the exact byte layout, the FAO-56 formulas, unit conversions, data-source pitfalls, and validation by round-trip.
---

# Building CROPWAT 8.0 input files

`cropwat` (the sibling skill) **drives** the CROPWAT GUI. This one **makes the files it eats**.
Use them together: build `.PEM`/`.CRM` here, then run the model there.

Bundled resources:

| File | Purpose |
|---|---|
| `cropwat_inputs.py` | Library + CLI: FAO-56 ETo, `.PEM`/`.CRM` read & write, self-check, batch build |
| `SKILL.md` | This document |

```bash
# write and read the two formats
python cropwat_inputs.py inspect <file.PEM> [<file.CRM> ...]
python cropwat_inputs.py eto --tmax 26.8 --tmin 14.7 --rh 67.9 \
                            --wind 2.09 --sun 7.7 --doy 196 --lat 40.81 --alt 1051
python cropwat_inputs.py detail  --csv weather.csv         # audit workbook only
python cropwat_inputs.py geocode --csv weather.csv         # name -> lat/lon/elevation
python cropwat_inputs.py build --csv weather.csv --pem --crm --geocode

# get the crop and soil files out of the CROPWAT installation
python cropwat_inputs.py crops --list
python cropwat_inputs.py crops --names WHEAT MAIZE RICE --soils

# rice-ready soil file, with the paddy parameters filled in
python cropwat_inputs.py soil --show-defaults
python cropwat_inputs.py soil --template MEDIUM
```

```python
import sys; sys.path.insert(0, r"<this skill directory>")
from cropwat_inputs import (write_pem, write_crm, read_pem, read_crm, eto_pm,
                            check_pem, find_cropwat_data, default_workdir,
                            read_soil, write_soil, SOI_RICE_DEFAULTS,
                            build_detail)
```

### Complete workflow — always build a categorised working directory

Generate into **category folders**, never a flat dump. With many sites × many years this is the only
layout that stays manageable, and it is exactly what the sibling `cropwat` skill scans for.

**The working directory defaults to `<Desktop>\CROPWRT工作文件`** — no path has to be supplied, and
each command prints where it is writing.

```bash
# 1. climate + rain, one folder per site, all its years inside
python cropwat_inputs.py build --csv weather.csv --pem --crm

# 2. crops and soils, each in its own folder
python cropwat_inputs.py crops --names WHEAT MAIZE RICE --soils

# 3. a rice-ready soil, filling the values that are missing in the template
python cropwat_inputs.py soil --template MEDIUM

# 4. hand over
python cropwat_run.py check --data "<Desktop>\CROPWRT工作文件"
```

Resulting layout:

```
<Desktop>\CROPWRT工作文件\
├── Climate\<site>\<site>_<year>.PEM      one folder per site
├── Rain\<site>\<site>_<year>.CRM         one folder per site
├── Crop\<crop>.CRO
└── Soil\<soil>.SOI                       includes Recommed-<template>.SOI
```

The Desktop comes from `HKCU\…\Explorer\Shell Folders\Desktop`, falling back to `~\Desktop`.
`--workdir <path>` overrides it; `--out` still does a flat single-folder dump. `build` prints the
site/year counts and the folders it is about to create, so a thousand-file run is verifiable before
it happens.

### Coordinates are looked up, not typed in

`lat`, `lon` and `alt` are **public information** — the user should not have to supply them.
With `--geocode`, `build` fetches whatever is missing:

| Step | Service | Why |
|---|---|---|
| primary lookup | **OpenStreetMap Nominatim** | good coverage of Chinese prefecture-level divisions — Open-Meteo's geocoder returns *nothing* for 乌海 / 通辽 / 兴安盟 / 锡林郭勒 |
| fallback lookup | **Open-Meteo geocoding** | broader global coverage, and returns elevation |
| elevation | **Open-Meteo elevation API** | Nominatim has no elevation; `.PEM` needs it for the pressure term |

No API key. Results are cached in `~/.cropwat_geocode.json`, so re-runs are offline and
reproducible. Every lookup is written to a **station-coordinates CSV** next to the source data, so
the values can be reviewed and corrected before committing:

```bash
python cropwat_inputs.py geocode --csv weather.csv          # review the CSV it writes
python cropwat_inputs.py build --csv weather.csv --geocode  # or do both at once
python cropwat_inputs.py build --csv weather.csv --sites 站点坐标.csv   # use your edited values
```

> **Always eyeball the result.** Two things go wrong:
> 1. **Homonyms.** 包头 is in Inner Mongolia *and* in Hainan; 阿拉善 exists in Xinjiang too.
>    Add a `province` column to the source CSV and the scorer prefers that province.
> 2. **Administrative centroids, not your station.** The lookup returns the *city/league* point.
>    If your weather series is from a specific station, overwrite the coordinates in the CSV and
>    re-run with `--sites`. Elevation matters most — it drives the pressure term in ETo.

Scoring also filters out natural features. Without that filter 锡林郭勒 matches a *river*
(`Xilin Guole River`), and 巴彦淖尔 matched an *airport*; with it they resolve to `Xilinhot_City`
and `Bayannur_City`.

### Station names must be Latin-1 — ASCII fallback is automatic

`.PEM` and `.CRM` are written as **latin-1**, so a Chinese station name cannot be stored — the write
fails outright. When a name is not latin-1, `build` derives one:

1. the name itself, if it is already latin-1
2. the **English place name** from a second Nominatim query, matched to the primary hit **by
   coordinate proximity** — this yields `Hohhot_City`, `Hinggan_League`, `Alxa_League`
3. reverse geocoding at the point (weaker — returns the *district* containing the centroid)
4. a coordinate fallback, `Site_<n>_<lat>_<lon>`

The mapping is printed at the top of the run so the user sees what happened. To choose your own
names, add a `name_en` column, or put the ASCII name in `name` directly.

> Do **not** rely on reverse geocoding for the name: measured at the administrative centroid it
> gives `兴安盟 → Inner_Mongolia`, `赤峰 → Daban_Town`, `呼和浩特 → Huimin_District`.

### Audit workbook — every number that went into the files

`build` also writes **`<Desktop>\CROPWAT输入数据明细.xlsx`**, recording every value used, so the
generated files can be checked later without re-deriving anything. Six sheets:

| Sheet | Contents |
|---|---|
| **逐月明细(全部要素)** | one row per site × year × month: the five input columns, plus the computed `Rad` and `ETo`, plus rainfall |
| **年度汇总(地方×年)** | annual mean Tmin/Tmax/RH/wind, annual sunshine hours, annual ETo (Σ eto×days), annual rainfall, growing-season (Apr–Sep) rainfall |
| **降水量矩阵(地方×年月)** | sites as rows, `year-month` as columns |
| **气温矩阵Tmax(地方×年月)** | as above, maximum temperature |
| **气温矩阵Tmin(地方×年月)** | as above, minimum temperature |
| **站点与文件** | coordinates, altitude, and the `.PEM`/`.CRM` path template for each site |

`--detail <path>` redirects it, `--no-detail` suppresses it. To rebuild it alone, without touching
the `.PEM`/`.CRM` files:

```bash
python cropwat_inputs.py detail --csv weather.csv
```

Annual ETo in the summary is **Σ(ETo_month × days_in_month)** — summing the monthly mm/day figures
directly would understate it roughly thirtyfold.

### `soil` output naming and merge rule

Without `--out`, the generated soil is named **`Recommed-<template>.SOI`** and placed in
`<workdir>\Soil\`:

```bash
python cropwat_inputs.py soil --template MEDIUM
#   -> <Desktop>\CROPWRT工作文件\Soil\Recommed-MEDIUM.SOI
```

**Only missing fields are filled.** The rice block of each shipped soil looks like this, and the
command fills only what reads `-999` / `-99.9`:

| Soil | Drainable porosity | Percolation | Max water depth |
|---|---|---|---|
| `BLACK CLAY SOIL` | 0.6 present | 5 present | 120 present → nothing to do |
| `MEDIUM` | **0.4 present — kept** | `-999` → **5** | `-999` → **120** |
| `HEAVY` | 0.6 present → kept | `-999` → 5 | `-999` → 120 |
| `LIGHT` | 0.4 present → kept | `-999` → 5 | `-999` → 120 |
| `RED LOAMY` / `RED SANDY` / `RED SANDY LOAM` | `-99.9` → 0.6 | `-999` → 5 | `-999` → 120 |

MEDIUM's `0.4` is a real FAO value and survives; only the two absent numbers get the recommended
figures. Any field can still be forced with `--percolation`, `--max-water-depth`,
`--drainable-porosity`, `--water-type`.

**Verified equivalence.** The writers here reproduce this skill's reference corpus — 144 `.PEM`
plus 144 `.CRM` — **byte for byte**, and the ETo recomputed from the first five columns matches the
stored ETo column to within 0.02 mm/day across all 144 climate files. If you change a formula, rerun
that comparison before trusting the change.

---

## 1. Byte layout

Both formats are fixed-width, `encoding='latin-1'`, `newline='\r\n'`. Getting the line endings or
the widths wrong makes CROPWAT read garbage or refuse the file.

### `.PEM` — climate / ETo

```
line 1      CROPWAT 8.0 Climate data
line 2      " 0   3"                        fixed
line 3      station name
line 4      station name                    (repeated)
line 5      altitude, %7.2f      e.g. 1051.00
line 6      latitude %8.2f + longitude %8.2f   e.g. "   40.81  111.65"
lines 7-18  twelve monthly rows, 48 chars each
```

Monthly row, 48 characters exactly:

| Field | Width | Format | Unit |
|---|---|---|---|
| Tmin | 6 | `%6.1f` | °C |
| Tmax | 6 | `%6.1f` | °C |
| RH | 7 | `%7.1f` | % |
| Wind | 7 | `%7.1f` | **km/day** |
| Sun | 7 | `%7.1f` | **hours per DAY** |
| Rad | 6 | `%6.1f` | MJ/m²/day |
| ETo | 9 | `%9.2f` | mm/day |

> **`.PEM` is Tmin-first.** The CLIMWAT `.pen` shipped with FAO is the opposite — `Tmax Tmin RH Wind
> Sun Rad ETo`, 7 fields of 10 chars = 70 char rows, with a quoted header line like
> `"Location 9529","KURNOOL",281,15.80,"N.L.",78.06," 01"`. CROPWAT opens both, **without warning**,
> so a swapped pair silently invalidates every result. Always confirm which one you are writing.

### `.CRM` — rain

```
line 1      CROPWAT 8.0 Rain data
line 2      station name
line 3      "  4  3"                        header metadata (see note)
line 4      "  0  0  0  0  0  0"            header metadata
lines 5-16  twelve monthly rows, 74 chars each
```

Monthly row: six `%9.1f` placeholders (all `-99.9`) + monthly rainfall `%10.1f` + effective-rainfall
placeholder `%10.1f`. **Column 7 is the only field you fill**; CROPWAT computes effective rainfall
itself. `-99.9` means missing.

> **Header metadata.** FAO's shipped samples all carry `"  1  3"` / `"80  0  0  0  0  0"`, while this
> skill's corpus uses `"  4  3"` / `"  0  0  0  0  0  0"`. Both load, compute and re-save correctly;
> `write_crm(..., fao_header=True)` switches to FAO's variant if you need it.

### `.CRO` and `.SOI` — copy them, do not write them

**Get them straight out of the CROPWAT installation.** FAO's crop files are the authoritative
templates; hand-writing one is far more likely to introduce an error than to improve anything.

```bash
python cropwat_inputs.py crops --list                      # see what is available
python cropwat_inputs.py crops --out "<workdir>\Crop" --names WHEAT MAIZE RICE
python cropwat_inputs.py crops --out "<workdir>\Crop" --names WHEAT --soils --soil-out "<workdir>"
```

The command **locates the installation itself** (`find_cropwat_data()`) by trying, in order: an
explicit `--cropwat-data`, the `CROPWAT_DATA` / `CROPWAT_HOME` / `CROPWAT_DIR` / `CROPWAT_EXE`
environment variables, the registry (`App Paths\cropwat.exe`, then the `InstallLocation` of any
uninstall entry whose `DisplayName` mentions CROPWAT), common directories on each drive, and
finally a shallow disk scan. On the reference machine the registry resolves it immediately. Files
are copied **byte for byte** — no re-encoding, no rewriting.

> ### Two copies exist — always take the **installation root** one
>
> CROPWAT keeps `crops\`, `climate\`, `rain\` and `soils\` in **two** places:
>
> ```
> <install>\crops\            ← shipped by the program, never edited
> <install>\data\crops\       ← the user's working copy, editable from the GUI
> ```
>
> **The `data\` copy is the one CROPWAT writes to when you edit a soil or crop in the GUI**, so it
> accumulates the user's changes. On the reference machine `data\soils\FAO\MEDIUM.SOI` had its two
> rice fields set to `0` / `125` while the root copy still read `-999` / `-999`.
>
> `find_cropwat_data()` therefore resolves to the **root**, and demotes a path that already points at
> `…\data`. Use the root whenever you want the pristine distribution files as a template.

FAO ships **38 crop files** in `crops\FAO\` — ALFALFA0/1, ARTICHOK, BANANA1/2, BARLEY, BEANS-DR,
BEANS-GR, CABBAGE, CITRUS, COTTON, DATEPALM, GRAINS, GRAPES-T/W, GRASS-C/W, GRONDNUT, MAIZE, MANGO,
MILLET, PASTURE, PEPPER, POTATO, PULSES, RICE, SORGHUM, SOYBEAN, SUGARBET, SUGARCAN, SUNFLOWR,
SW-MELON, TOBACCO, TOMATO, VEGETABL, W-WHEAT, W-WHEATF, WHEAT — plus 7 Kurnool samples in `crops\`.

**Soils: 7 files in two groups.**

| File | TAM | Infil. | Root depth | Notes |
|---|---|---|---|---|
| `soils\BLACK CLAY SOIL.SOI` | 200 | 30 | 900 | **the only one with the rice fields populated** (0.6 / 5 / 120) |
| `soils\RED LOAMY.SOI` | 180 | 30 | 900 | rice fields `-99.9` / `-999` |
| `soils\RED SANDY LOAM.SOI` | 140 | 30 | 900 | rice fields unset |
| `soils\RED SANDY.SOI` | 100 | 30 | 900 | rice fields unset |
| `soils\FAO\HEAVY.SOI` | 200 | 40 | 900 | FAO standard texture; rice fields unset |
| `soils\FAO\MEDIUM.SOI` | 290 | 40 | 900 | FAO standard texture; rice fields unset |
| `soils\FAO\LIGHT.SOI` | 60 | 40 | 900 | FAO standard texture; rice fields unset |

The three under `soils\FAO\` are the classic FAO texture classes and are often the right choice for
a rainfed or dry-crop run. **None of them carries the rice parameters** — for paddy you must fill
them, which is what `cropwat_inputs.py soil` does. `crops --soils` copies all seven.

Layout of a `.CRO` (two variants):

```
dry crops                              rice (paddy variant, 12 lines)
line 1  header                         line 1  header
line 2  crop name                      line 2  crop name
line 3  4 stage lengths (days)         line 3  4 stage lengths (days)
line 4  3 Kc (initial/mid/end)         ...
line 5  2 rooting depths
line 6  3 depletion fractions
line 7  5 yield response factors
line 8  crop height
```

`.SOI` layout: TAM, max infiltration, max root depth, initial depletion, initial available water,
drainable porosity (rice adds two fields; `-999` = unset). To make a custom one, **copy a shipped
`.SOI` and change only line 2 (the name)**, keeping every other byte — that is exactly how the
`HETAO PADDY.SOI` in the reference project was made.

> **CROPWAT ignores the soil file when computing CWR.** Four different soils gave identical
> ETc / effective rainfall / irrigation requirement. Only `Calculations → Irrigation Scheduling`
> reads it. For green/blue water work the soil file merely has to load.

---

## 1b. Rice needs extra soil parameters — here are the values to use

CROPWAT's soil form has a **`GeneralBox`** (all crops) and an **`AdditionalRiceBox`** (paddy rice
only). The rice-only fields, taken from the form definition inside `cropwat.exe`:

| Control | Caption | Unit |
|---|---|---|
| `PorosityIntEdit` | **Drainable porosity (SAT − FC)** | — |
| `PuddlePercRealEdit` | **Maximum Percolation rate after puddling** | mm/day |
| `InitWaterIntEdit` | **Water at planting** | see below |
| `maxWDIntEdit` | **Maximum water depth** | mm |
| `WatertypeComboBox` | unit selector for water at planting: **`mm WD` / `% desat.` / `% depl.`** | — |
| `PFactorRealEdit` | P factor | — |

Percolation is either a **fixed rate** or derived by an **FAO formula** (`MaxPercOpt` sub-dialog).
The radio pair is literally labelled `FAO formula:` and offers:

```
Maximum Percolation Rate after puddling
Maximum Percolation Rate of non-puddled soil ^ 0.33
Daily decrease in Maximum Percolation Rate during puddling
      = [1/days puddling] * LN(Max. Perc Rate after puddling / Max. Perc Rate of non-puddled soil)
```

**Prefer the fixed value.** It is one number, it is what the reference corpora use, and the formula
model needs two more parameters that are rarely measured.

### Recommended fixed values (two independent sources agree)

| Parameter | Value | Why |
|---|---|---|
| **Maximum percolation rate after puddling** | **5 mm/day** | 《灌溉排水设计规范》3.2.5 gives **2–8 mm/day** for paddy (clay → low, sand → high). FAO's own `BLACK CLAY SOIL.SOI` ships **5** — mid-range, i.e. a clay loam |
| **Maximum water depth** | **120 mm** | the puddling water requirement. A published water-saving schedule uses 泡田定额 120 mm; FAO's sample also ships **120** |
| Drainable porosity (SAT − FC) | 0.6 | FAO's sample value |
| Water type flag | 1 | FAO's sample value |
| *Water at planting* (UI only) | ≈ 20 mm | the same schedule ponded 20 mm at transplanting |

Tune the percolation rate to the soil: **2–3 mm/day** for heavy clay with a plough pan,
**5** for clay loam, **6–8** for sandy soils. Nothing else in the rice block needs changing.

```bash
python cropwat_inputs.py soil --show-defaults
python cropwat_inputs.py soil --out "<workdir>\HETAO PADDY.SOI" --name "HETAO PADDY"
python cropwat_inputs.py soil --out "<workdir>\SANDY.SOI" --name "SANDY" --percolation 7
```

The command takes FAO's `BLACK CLAY SOIL.SOI` as the template — **the only one of the four shipped
soils that has the rice slots populated**. Change only the name and the rice numbers; every other
field is copied through unchanged.

### `.SOI` layout (10 lines, `latin-1`, CRLF)

```
1   CROPWAT 8.0 Soil Data
2   soil name
3   Total available soil moisture (FC - WP)         %10.1E, Delphi style -> " 2.0E+0002"
4   Maximum rain infiltration rate                  mm/day
5   Maximum rooting depth                           cm
6   Initial soil moisture depletion (% TAM)         %
7   Initial available soil moisture                 mm/m
--- from here on: AdditionalRiceBox ---
8   Drainable porosity (SAT - FC)                   %5.1f
9   <percolation rate><water type, right-aligned 3> e.g. "5  1" / "-999  1"
10  Maximum water depth                             mm
```

**Field 9 and 10 confirmed empirically.** A user edited `MEDIUM.SOI` through the GUI, changing exactly
the two percolation/water fields; the file went from

```
[8] '-999  1'   [9] '-999'      (pristine)
[8] '0  1'      [9] '125'       (after the user set percolation = 0 and max water depth = 125)
```

so field 9's first number is the **percolation rate** and field 10 is the **maximum water depth** —
the two values that must be filled for a paddy run. (Percolation `0` is a legitimate choice: a
thoroughly puddled field with a plough pan can be treated as effectively impermeable.)

> **The scientific-notation field is Delphi/Fortran style, not C style.** `200` is written
> `" 2.0E+0002"` — a **four-digit exponent**. Python's `%E` produces `"2.0E+02"`, which will not
> round-trip. `write_soil()` uses a `_delphi_e()` helper for this.
>
> Verified: reading and rewriting all six pristine `.SOI` files reproduces them **byte for byte**,
> 6/6.

### Validation rules built into the program (so you know what constrains what)

```
Puddling stage must be less than total land preparation stage
Error in soildata: initial waterlevel must be <= maximum waterdepth
Nursery period must be longer than land preparation stage
Error? depletion of puddle > potential depletion puddle
```

### The rice crop file carries its own extras

`RICE.CRO` is **12 lines** versus 8 for a dry crop, and the crop form grows a whole
`LandPrepTabSheet` containing `LandPrepSettingsTabSheet`, `LandPrepPrePuddleTabSheet` and
`landpreppuddletabsheet`, with controls such as `SoakBelowPuddleDepthIntEdit`,
`PrePuddlingTimingCombobox`, `PuddlingAFixedWDIntEdit`. The extra crop-side inputs are:

**nursery period · land preparation period · puddling period · transplanting date ·
puddling depth · soak-below-puddle depth**

Again: copy `RICE.CRO` from `crops\FAO\` and leave it alone for a CWR job.

---

## 2. FAO-56 Penman-Monteith, step by step

`cropwat_inputs.eto_pm()` implements exactly this and returns `(Rs, ETo)`.

```python
def esat(t):                    # kPa, t in °C
    return 0.6108 * exp(17.27*t / (t + 237.3))

def Ra(doy, lat):               # MJ m-2 d-1   (FAO-56 eq. 21)
    phi = radians(lat)
    dr  = 1 + 0.033*cos(2*pi*doy/365)
    dec = 0.409*sin(2*pi*doy/365 - 1.39)
    ws  = acos(clamp(-tan(phi)*tan(dec)))
    return (24*60/pi)*0.0820*dr*(ws*sin(phi)*sin(dec) + cos(phi)*cos(dec)*sin(ws))

def N(doy, lat):                # daylight hours (eq. 34)
    ws = acos(clamp(-tan(phi)*tan(dec)))
    return 24/pi * ws

# --- radiation
Rs  = (0.25 + 0.50 * min(sun_h/N, 1)) * Ra      # Ångström (eq. 35), FAO defaults 0.25 / 0.50
Rso = (0.75 + 2e-5*alt) * Ra                    # clear-sky (eq. 37)
Rns = 0.77 * Rs                                 # albedo 0.23
es  = (esat(Tmax) + esat(Tmin)) / 2
ea  = RH/100 * es
Rnl = 4.903e-9 * ((Tmax+273.16)**4 + (Tmin+273.16)**4)/2 \
      * (0.34 - 0.14*sqrt(ea)) * (1.35*min(Rs/Rso,1) - 0.35)
Rn  = Rns - Rnl

# --- aerodynamics
T   = (Tmax + Tmin)/2
P   = 101.3 * ((293 - 0.0065*alt)/293)**5.26    # eq. 7
gam = 0.665e-3 * P                              # eq. 8
dlt = 4098 * esat(T) / (T + 237.3)**2           # eq. 13

ETo = max(0, (0.408*dlt*Rn + gam*(900/(T+273))*u2*(es-ea)) / (dlt + gam*(1 + 0.34*u2)))
```

**Use the 15th of each month** for `doy`. `.PEM` carries one row per month, so a representative
day is all the format allows.

`es` is built from `esat(Tmax)` and `esat(Tmin)` averaged, **not** from `esat(Tmean)`. The two differ,
and FAO-56 specifies the former.

> The equation is **symmetric in Tmax and Tmin** for the radiation terms: swapping them changes
> `es`, `Rnl`'s temperature term and `Tmean` only in second order. That is why a Tmin/Tmax swap in a
> `.PEM` does **not** show up as a wild ETo — it corrupts the file quietly. Check the column order
> by inspection, not by looking at ETo.

---

## 3. Unit conversions — where the errors hide

| Quantity | Source unit | File / formula unit | Conversion |
|---|---|---|---|
| Wind, formula | 10 m | 2 m | `× 4.87 / ln(67.8·10 − 5.42)` ≈ **× 0.748** |
| Wind | m/s | **km/day** (file column) | `× 86.4` |
| Wind | km/day (file) | **m/s** (FAO-56 formula) | `÷ 86.4` |
| Sunshine | hours per **month** | hours per **day** (file column) | `÷ days_in_month` |
| Precipitation | mm | mm | none |
| 1 mm over 1 ha | | 10 m³ | `× 10` |

> ### ⚠ The u2 trap — the one that cost the most time
>
> `ETo = [… + γ·(900/(T+273))·**u2**·(es−ea)] / [… + γ(1 + 0.34·**u2**)]` takes **u2 in m/s**.
> The `.PEM` **wind column stores km/day**. Feeding the column value straight into the formula makes
> the wind term ~86× too large and inflates ETo by up to **6 mm/day**.
>
> Symptom: the radiation column checks out (`Rs` matches to 0.1) while ETo is wildly off. If Rs
> agrees and ETo does not, suspect the wind unit before anything else.
>
> ```python
> rs, eto = eto_pm(tmax, tmin, rh, wind_kmd / 86.4, sun_h, doy, lat, alt)
> ```

---

## 4. Data-source pitfalls

These are the traps that actually produced bad files on a real project. Each was caught only by
cross-checking against an independent source.

| Symptom | Cause | Test that catches it |
|---|---|---|
| ETo inflated ~**19 %** (to +30 %) | using **monthly extreme** Tmax/Tmin where **monthly means** are needed. A weather API's `minTmp`/`maxTmp` are extremes, not means — e.g. `14.9` mean vs `21.1` extreme reported for the same city-month | compare against a reanalysis mean (ERA5) for one city-month |
| Precipitation inflated **1.6–3×** | wrong variable or wrong aggregation; eastern sites reached 1293 mm/yr against an official national maximum of 824 mm | compare the annual total against the official climate bulletin / 1991–2020 normal |
| Cold-season precipitation shaped like **snow depth** (e.g. 247 mm in December, 47× the normal) | snow-depth series read as precipitation | check the winter: precipitation must not exceed the warm-season values in a monsoon climate |
| Radiation ~**8×** too high | sunshine written as **hours/month** into a column that wants **hours/day** | `Rs` of 158 vs the correct 8.6 |
| Everything shifted, no error | **Tmin/Tmax column order** swapped (`.PEM` vs `.pen`) | read line 7 and check the first number is the colder one |
| Results look fine but are wrong | the planting date was never applied — see the `cropwat` skill | ETc about a quarter of what the crop should need |

**Prefer a single source per variable.** Splicing two sources mid-series creates a step change that
is easy to mistake for climate signal. If you must splice, verify the overlap: compare the warm-season
totals of the outgoing and incoming source across the boundary and record the ratio.

**Reject, do not "correct".** Additive and multiplicative rescaling of a bad precipitation series was
tried and abandoned — both produced physically implausible values. Replace the source instead.

---

## 5. Validation

### 5.1 Self-consistency (cheap, catches the u2 trap)

Recompute Rad and ETo from the first five columns and compare with what the file stores:

```bash
python cropwat_inputs.py inspect <file.PEM>
#   自洽校验: ✓ 通过  （最大 ETo 差 0.006，Rad 差 0.088）
```

On the reference corpus this reads 144/144 pass with max ΔETo 0.019 mm/day.

This catches internal inconsistency — a wrong unit fed to the formula. It does **not** catch a wrong
input value that was consistently written.

> **Precision note.** Round-tripping through a `.PEM` loses precision: the stored sunshine is already
> rounded to 1 decimal, so rebuilding from it shifts `Rad` by ~0.1 and occasionally flips the last
> digit of `ETo`. The **first five columns stay byte-identical**, which is all that matters. Feed the
> writers full-precision source data, not values read back out of a `.PEM`.

> **Rad and ETo are recomputed by CROPWAT and the stored values are ignored.** Only the first five
> columns are real inputs. Proven by experiment: setting the whole ETo column to `99.99` and
> re-saving through CROPWAT produces a **byte-identical** file to the untouched original. So do not
> spend effort on those two columns — but do use them as a self-check.

### 5.2 Round-trip through CROPWAT

Open each file in CROPWAT and re-save it. A correct file comes back **byte-identical**; any change
means CROPWAT reinterpreted something. This is the strongest available check short of comparing
model output.

### 5.3 Plausibility against official normals

Compare annual precipitation and annual ETo against an official source:

| Series | This corpus | Official 1991–2020 normal | Deviation |
|---|---|---|---|
| Annual precipitation, 12-site mean | 314 mm | 324 mm | 3 % |
| Range across sites | 95 – 478 mm | 41.9 – 824.3 mm (national) | plausible |

A 12-site mean within a few percent of the published normal is the level of agreement to aim for.
Large per-site errors cancel in the mean, so **check the extremes too**.

### 5.4 Annual ETo must be summed with day counts

The `.PEM` stores mm/**day**. Annual ETo = `Σ(eto_month × days_in_month)`, not `Σ(eto_month)`.
Omitting the day counts understates annual ETo by a factor of ~30 (33 mm instead of 1022 mm).
This mistake affects only your summary spreadsheet, never the `.PEM` itself.

---

## 6. Reference values

Hohhot (40.81 °N, 111.65 °E, 1051 m), 2013, as a worked example:

| | Jan | Feb | Mar | Apr | May | Jun | Jul | Aug | Sep | Oct | Nov | Dec |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Tmin °C | −20.4 | −15.3 | −7.4 | −1.3 | 8.5 | 12.7 | 14.7 | 13.3 | 7.1 | −0.2 | −9.5 | −17.5 |
| Tmax °C | −6.1 | −1.5 | 8.7 | 13.5 | 22.8 | 25.6 | 26.8 | 26.8 | 20.4 | 13.6 | 3.2 | −4.0 |
| RH % | 59.8 | 43.7 | 37.4 | 34.5 | 33.8 | 52.5 | 67.9 | 66.6 | 61.0 | 52.3 | 53.2 | 52.2 |
| Wind km/d | 172.1 | 194.7 | 225.0 | 263.8 | 261.0 | 219.4 | 180.6 | 165.8 | 186.2 | 196.1 | 186.9 | 170.7 |
| Sun h/d | 6.4 | 7.5 | 8.4 | 9.0 | 8.8 | 7.4 | 7.7 | 7.8 | 7.6 | 7.8 | 6.5 | 6.4 |
| Rad | 8.6 | 12.1 | 16.4 | 20.5 | 22.1 | 20.9 | 21.0 | 19.6 | 16.5 | 13.4 | 9.3 | 7.9 |
| ETo mm/d | 0.49 | 1.07 | 2.36 | 3.66 | 5.49 | 4.97 | 4.43 | 4.06 | 3.05 | 2.13 | 1.02 | 0.59 |

Annual ETo by Σ(eto × days) = **1017 mm**. FAO's own shipped `KURNOOL.pen` reads ETo 3.91–7.83
mm/day, so values in this range are normal.

---

## 7. Traps checklist

| # | Trap | Guard |
|---|---|---|
| 1 | `.PEM` written Tmax-first | read line 7; the first field must be the colder one |
| 2 | u2 fed in km/day | divide the wind column by 86.4 before the formula |
| 3 | sunshine fed as hours/month | divide by days in month |
| 4 | LF instead of CRLF, or UTF-8 instead of latin-1 | round-trip must be byte-identical |
| 5 | wrong data-line width | `.PEM` 48, `.CRM` 74 — assert it |
| 6 | monthly extremes used as means | cross-check one city-month against ERA5 |
| 7 | precipitation 1.6–3× too high | compare the annual total against the official normal |
| 8 | snow depth read as precipitation | winter must not exceed the monsoon-season values |
| 9 | two sources spliced mid-series | verify the overlap ratio, or use one source |
| 10 | annual ETo summed without day counts | multiply each month by its day count |
| 11 | effort spent tuning the Rad/ETo columns | CROPWAT ignores them; only five columns matter |
| 12 | expecting the soil file to change CWR | it does not; only irrigation scheduling reads it |
