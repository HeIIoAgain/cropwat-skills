---
name: cropwat
description: Drive FAO CROPWAT 8.0 (a Windows GUI program with no CLI/API) to compute crop water requirements, effective rainfall and irrigation demand. Use when asked to run CROPWAT, compute ETc/CWR/绿水/蓝水足迹, batch-process CROPWAT climate (.PEM) and rain (.CRM) files, or automate CROPWAT for many station-year-crop combinations. Covers the file formats, the Win32 message driving recipe, the planting-date entry sequence, and result extraction.
---

# Driving CROPWAT 8.0

CROPWAT 8.0 (FAO) is a Delphi/VCL Win32 GUI program. It has **no CLI, no batch mode, no API**.
Everything below was verified against a real installation; the recipes are known-good.

## ★ Operating rules (do not deviate)

### CROPWAT is located automatically — never hard-code its path

`cropwat_lib.find_cropwat()` resolves the installation at run time, in this order:

1. an explicit `--exe` path
2. the `CROPWAT_EXE` / `CROPWAT_HOME` / `CROPWAT_DIR` environment variables
3. the registry — `App Paths\cropwat.exe` and the `InstallLocation` of any uninstall entry whose
   `DisplayName` mentions CROPWAT
4. common directories on every drive (`Program Files`, `Program Files (x86)`, drive root,
   `StudyProgram`, `Program`, `Software`)
5. a shallow scan of each drive to depth 3, skipping system directories, with a 25 s budget
6. `PATH`

On the reference machine the registry hit resolves in well under a second. If nothing is found the
error names every location tried and tells the user to pass `--exe` or set `CROPWAT_EXE`.
`CropWat(workdir=...)` defaults to the directory containing the executable, because CROPWAT must be
launched with its own folder as the working directory.

### Five inputs are mandatory; the output location is not

`run` refuses to start unless all five are supplied, so a missing argument can never silently
escalate into a thousand-case batch.

| # | Input | Flag | Form |
|---|---|---|---|
| ① | CROPWAT **working-files location** | `--data` | directory containing `Climate/`, `Rain/`, `Crop/`, and the soils — `*.SOI` either at the root or in `Soil/` / `Soils/` |
| ② | **Planting date** | `--sowing` | `"WHEAT=05/05,MAIZE=25/04"` — **DD/MM**, per crop |
| ③ | **Period** | `--years` | `2013 2014 … 2024` |
| ④ | **Sites** | `--cities` | directory names, must exist |
| ⑤ | **Crops** | `--crops` | `.CRO` basenames, must exist |

The layout the sibling `cropwat-inputs` skill produces, and which this one expects:

```
<workdir>\
├── Climate\<site>\<site>_<year>.PEM
├── Rain\<site>\<site>_<year>.CRM
├── Crop\<crop>.CRO
└── Soil\<soil>.SOI          (a root-level *.SOI is also accepted)
```

Every value that does not exist in the working directory is rejected by name, with the valid
choices listed.

**The result workbook does not need to be specified.** It is written to
`<Desktop>\cropwrt计算结果.xlsx` and shown in the pre-run summary as `② 结果表格（自动）`. The
Desktop is resolved from `HKCU\…\Explorer\Shell Folders\Desktop`, falling back to `~\Desktop`. The
name is fixed and the file is overwritten every run — but nothing is lost, because the intermediate
summary accumulates across runs and the whole table is regenerated from it, so the file always holds
every result computed so far. `--excel` overrides the path if ever needed.

Intermediate files (the summary CSV and the per-case result text) go to `~\.cropwat_run`, **not** to
the Desktop; override with `--out`.

### Estimate the runtime and warn the user before starting

Each combination takes **≈85 s** (about 9 s of that is CROPWAT's startup). Between them the cases
run for hours as a **background job**, so the console output — including the banner the CLI prints —
is not visible to the user live.

**Therefore the agent must reproduce the warning as chat output, in bold, before launching.**
The console banner alone does not reach the user. Post something like the block below with the real
numbers filled in, and do not start the run until the user has seen it.

> ## ⚠ 测算期间请勿使用键盘和鼠标，也不要切换或遮挡窗口 ⚠
>
> 本程序通过模拟鼠标点击和按键来操作 CROPWAT。任何人工输入、窗口切换、弹窗遮挡都会干扰它，
> 可能导致该组结果错误，甚至整组失败。
>
> | 参数 | 值 |
> |---|---|
> | ① 工作文件位置 | `…` |
> | ② 结果表格（自动） | `<桌面>\cropwrt计算结果.xlsx` |
> | ③ 播种日期 | `WHEAT=05/05, …` |
> | ④ 时间范围 | 2013–2024（12 年） |
> | ⑤ 地点范围 | 12 个 |
> | ⑥ 作物范围 | 7 种 |
>
> **组合总数 = 12 × 7 × 12 = 1008 组**
> **预计连续运行 = 1008 × 85 秒 ≈ 23.8 小时**
>
> 请离开电脑，或至少不要动键鼠。中断也没关系——结果逐组落盘，重新执行同一命令即可续跑。

Say explicitly that the estimate is 地点数 × 作物数 × 年数 × 85 秒, and state the estimated clock
time in hours, not seconds. If the user has already stepped away or confirmed, proceed.

`--dry-run` prints the same numbers plus the task list without launching CROPWAT; use it to draw up
the figures before the real run.

### One launch per combination

**Each CROPWAT launch computes exactly one combination** — one site × one year × one crop — and the
process is closed immediately afterwards. Reusing an instance lets MDI children stack up, after
which later menu commands miss their target; long-lived instances also accumulate state.

### Long runs must survive interruption

Never gate a multi-hour batch on an interactive confirmation — nobody is there to answer it.
Instead:

| Mechanism | Behaviour |
|---|---|
| **Resume by default** | a per-task result file is the completion marker; re-running the same command skips what is done, so an interrupted batch simply continues. `--no-resume` forces a full redo |
| **Incremental atomic writes** | `CWR_summary.csv` is rewritten after every task via a temp file plus `os.replace`, so a kill never leaves a truncated summary |
| **`KeyboardInterrupt` handling** | flushes before exiting |
| **Stray-process cleanup** | kills leftover `cropwat.exe` after any failure so the next case starts clean |
| **Retry** | `--retry` (default 3) re-runs a case whose CROPWAT invocation raised or returned nothing |

The per-case progress line also shows the **remaining time estimate** so an unattended run can be
monitored at a glance.

### Never verify the planting date with `GetWindowText`

That `TMaskEdit` always returns the mask template `__/__`, whatever the screen shows. The reliable
check is to run CWR and read the `Month` column on screen — see the planting-date section.

Bundled resources:

| File | Purpose |
|---|---|
| `cropwat_lib.py` | Importable driver: launch/attach, menu commands, file dialogs, planting-date entry, CWR run, result extraction |
| `cropwat_run.py` | CLI: `check` / `smoke` / `run`, with a summary CSV. **Fully generic** — discovers sites, years and crops by scanning the working directory |
| `demo_cropwat.py` | Worked example carrying the Inner Mongolia project's parameters. Those values are **an example, not a requirement** — swap the `CONFIG` block, or ignore the file and use the CLI |

Run them with the session's Python executable. Only the standard library is required
(`ctypes`), plus `Pillow` if you want screenshots.

```bash
# 1) inspect the working directory (no CROPWAT launched)
python cropwat_run.py check --data "<working dir>"

# 2) can CROPWAT start and are the forms recognisable?
python cropwat_run.py smoke

# 3) trial a couple of cases first
python cropwat_run.py run --data "<working dir>" \
    --sowing "WHEAT=05/05,MAIZE=25/04" --limit 2
```

```python
import sys; sys.path.insert(0, r"<this skill directory>")
from cropwat_lib import CropWat, MENU, run_case
```

## Environment

| Item | Value |
|---|---|
| Executable | `cropwat.exe` — **auto-discovered**, see the operating rules |
| Work dir | the directory holding `cropwat.exe` |
| Bundled data | `data\climate`, `data\rain`, `data\crops\FAO`, `data\soils\FAO` |
| Startup wait | ~9 s before the main window exists |
| One CWR run | 2–4 min wall clock |

**Constraints**

1. CROPWAT writes to the registry at startup. Without full file/registry access it shows
   `Localizer Error` / `Failed to create key` and will not start.
2. Run it as a **background job**. A foreground command that times out will kill the run mid-flight.
3. Main window class `TMainForm`; MDI container `MDIClient`; each module is an MDI child
   (`TMonthEToPMForm`, `TMonthRainForm`, `TCropForm`, `Tsoilform`, `TCropPatForm`, `TCwrForm`).

## File formats

### `.PEM` — climate (CROPWAT native)

```
line 1   CROPWAT 8.0 Climate data
line 2   " 0   3"                       fixed
line 3   station name
line 4   station name
line 5   altitude (m)
line 6   latitude(8.2f) longitude(8.2f)
lines 7-18   12 months, one line each, 48 chars per line
```

Field layout per month line:

| Field | Width | Format | Unit |
|---|---|---|---|
| Tmin | 6 | `%6.1f` | °C |
| Tmax | 6 | `%6.1f` | °C |
| RH | 7 | `%7.1f` | % |
| Wind | 7 | `%7.1f` | **km/day** |
| Sun | 7 | `%7.1f` | **hours per DAY** |
| Rad | 6 | `%6.1f` | MJ/m²/day |
| ETo | 9 | `%9.2f` | mm/day |

> **Three traps.**
> 1. **Tmin comes first.** The CLIMWAT `.pen` format is the opposite (Tmax first). Mixing them up
>    raises no error and silently invalidates every result.
> 2. **Sun is hours per DAY, not per month.** Writing monthly hours inflates Rad roughly eightfold
>    (measured 158.5 vs the correct 8.6).
> 3. **Rad and ETo are recomputed by CROPWAT and your values are ignored.** Only the first five
>    columns are real inputs. Proven by an experiment: setting the whole ETo column to `99.99`
>    and re-saving through CROPWAT produces a byte-identical file to the untouched original.

Write with `encoding='latin-1'` and `newline='\r\n'`.

### `.pen` — CLIMWAT climate (FAO distribution format)

Column order is `Tmax Tmin RH Wind Sun Rad ETo` — **the reverse of `.PEM`**.
Line 1 looks like `"Location 9529","KURNOOL",281,15.80,"N.L.",78.06," 01"`. CROPWAT opens it directly.

### `.CRM` — rain

```
line 1   CROPWAT 8.0 Rain data
line 2   station name
line 3   "  4  3"
line 4   "  0  0  0  0  0  0"
lines 5-16   12 lines x 8 fields
             column 7 = monthly rainfall (mm)   <- the only field you fill
             column 8 = effective rainfall (CROPWAT recomputes it)
             -99.9 means missing
```

### `.CRO` — crop

```
line 1    header          line 2    crop name
lines 3-6   4 stage lengths (days)
lines 7-9   3 Kc values
lines 10-11 2 rooting depths
lines 12-14 3 depletion fractions
lines 15-19 5 yield response factors
line 20     crop height
```

Rice uses a special 12-line variant with paddy parameters.
FAO crops ship in `data\crops\FAO\` (WHEAT, MAIZE, RICE, POTATO, SOYBEAN, SUNFLOWR, GRONDNUT, …).

### `.SOI` — soil

`TAM`, max infiltration, max root depth, initial depletion, initial available water, drainable porosity;
rice adds two fields (`-999` = unset).

> **CROPWAT does not use the soil file when computing CWR.** Four different soils produced identical
> CWR output. Only `Calculations → Irrigation Scheduling` consults it. For green/blue water work the
> soil file merely has to load.

## Driving the GUI

### The one rule that matters most

**Menu commands silently do nothing unless the main window is foreground.**
`File→Open`, `Save As`, `Calculations` all no-op with no error. `CropWat.menu()` calls
`activate()` (AttachThreadInput + SetForegroundWindow + BringWindowToTop + SetFocus) every time.

### Menu IDs

| Item | ID | | Item | ID |
|---|---|---|---|---|
| File→New Session | 2 | | New→Crop (dry) | 22 |
| File→Open Session | 3 | | New→Crop (rice) | 23 |
| File→Save / SaveAs | 4 / 5 | | New→Soil | 24 |
| New→Climate/ETo | 9 | | New→Cropping Pattern | 25 |
| →Monthly PM | **10** | | Module→Open | **26** |
| →Daily PM | 12 | | Module→Save | 27 |
| →Monthly measured ETo | 14 | | Module→Save As | **28** |
| New→Rain | 17 | | Copy Table | 42 |
| →**Monthly** | **18** | | →**Data only** | **43** |
| →Decade / Daily | 19 / 20 | | →Data and Headers | 44 |
| New→Crop | 21 | | **Calculations→CWR** | **51** |
| | | | Window→Close All | **72** |

### File dialogs

Standard `#32770`. Buttons are localised (Chinese on a zh-CN install): `打开(&O)`, `保存(&S)`.
When matching, **exclude `只读` (read-only), `取消` (cancel), `帮助` (help)** — "只读方式打开" also
contains "打开" and will silently open read-only.

### Clipboard traps

```python
u32.GetClipboardData.restype = ctypes.c_void_p     # else the 64-bit pointer truncates -> silent crash
u32.GetClipboardData.argtypes = [w.UINT]
k32.GlobalLock.restype = ctypes.c_void_p
u32.SetClipboardData.argtypes = [w.UINT, ctypes.c_void_p]   # else OverflowError
# read back with ctypes.wstring_at, not string_at
```

Seed the clipboard with a sentinel string before triggering Copy Table; if the sentinel is still
there afterwards, the command did not take effect.

## ★ Planting date — the hard part

### It must go into the **Crop** module — not the Cropping Pattern

This is the single most damaging mistake, and it is silent: results come back looking perfectly
plausible, just computed from the wrong sowing date.

| Module | Menu | Effect on ETc / Peff / irrigation |
|---|---|---|
| **Crop** | `New → Crop` (id 21, dry 22 / rice 23) | ✅ **this is the one that counts** |
| Cropping Pattern | `New → Cropping Pattern` (id 25) | ❌ **no effect on CWR at all** — its date only feeds the irrigation-scheduling / supply modules |

> ### ⚠ The Cropping Pattern module is **ignored** by the CWR calculation
>
> Whatever is entered there — dates, the crop dropdown, the row order, or nothing at all — makes
> **no difference** to ETc, effective rainfall or irrigation requirement. CROPWAT reads the planting
> date from the **Crop module only**.
>
> **Practical consequence: for a CWR/water-footprint job you can skip the Cropping Pattern module
> entirely.** Never create it, never fill it, never worry about it. `cropwat_run.py` does exactly
> that — its per-case sequence opens no Cropping Pattern form, and the numbers are correct.
>
> Only open it if you are asked for irrigation scheduling or scheme supply, which is a different
> calculation and is out of scope here.

`CropWat.set_cropping_pattern()` exists so the form can still be driven if genuinely needed, but it
**does not change the calculation**. Always use `CropWat.set_planting_date()`.

### Call order matters

```python
cw.load_climate(pem)
cw.load_rain(crm)
cw.load_crop(cro)              # ← load the crop FIRST
cw.load_soil(soil)
cw.set_planting_date('05/05')  # ← only then set the date
```

Loading a crop file replaces the whole Crop module, **resetting the planting date**. Set the date
after the last `load_crop`, never before it.

Note what is *absent* from that sequence: there is no `set_cropping_pattern` call, and none is
needed.

### Where the field is on the Crop form

`TCropForm` is an MDI child. Its planting-date field is a **`TMaskEdit`** — the first one found by
walking the form's children. `CropWat.set_planting_date()` locates it that way:

```python
crop_form = self.find_form('TCropForm')
mask = [c for c in _walk(crop_form) if _cls(c) == 'TMaskEdit'][0]
```

Interactively: with the Crop form open, the field labelled *Planting date* sits in the upper-left
block next to *Crop name* and *Harvest*. The Harvest field is read-only and fills itself in once the
date is committed — watching it change from `__/__` to a real date is the on-screen confirmation.

### Date format is DD/MM (day/month) — user-confirmed

| Want | Enter |
|---|---|
| 5 May | `05/05` |
| 20 April | **`20/04`** |
| 1 June | **`01/06`** |

Corroborated arithmetically: planting `05/05` with a 130-day total yields harvest `11/09`, i.e.
**11 September** (5 May + 130 d). MM/DD would give 9 November = 188 days.

### The exact sequence (all five steps are required)

```
① click the Crop form (activates the MDI child)
② click the planting-date field
③ press Backspace 4 times      ← clears the mask template; skip this and the input is discarded
④ type 4 digits (DDMM)
⑤ press Enter to commit
```

`CropWat.set_planting_date('05/05')` performs all five. **Call `activate()` before each click** —
`click()` uses absolute screen coordinates, so another CROPWAT instance (or any window) on top will
swallow the click and the date silently stays empty.

### What does *not* work

| Method | Result |
|---|---|
| `SendMessage(WM_SETTEXT)` | ignored, stays `__/__` |
| `SendMessage(WM_CHAR)` | ignored |
| focus + `WM_KEYDOWN/CHAR/KEYUP` | ignored |
| focus + `Ctrl+V` | ignored |
| `SetWindowTextW` | **appears to work but is cosmetic** — the display changes, the component's internal value does not, and both CWR totals stay identical |

The control does receive focus correctly when clicked (verified with `GetGUIThreadInfo`), yet
injected keyboard messages still do not reach it. `keybd_event` (real `SendInput`-level input)
works, but only after the Backspace clear.

### Verifying the date actually took effect

Three approaches mislead — all three were tried:

| Method | Why it fails |
|---|---|
| `GetWindowText` on the planting-date field | this `TMaskEdit` always returns the mask template `__/__`, whatever the screen shows |
| first column of the copied result table | **Copy Table (Data only) omits the Month column** — the first field is the *decade* (1/2/3) |
| comparing an ETo inferred from the result against the `.PEM`'s ETo | CROPWAT recomputes ETo from the five inputs, so its values differ from the file by 0.3–0.6 mm/day and months become indistinguishable — a two-month error was observed |

**What actually keeps the date correct is the "do not touch the keyboard or mouse" instruction** in
the pre-run warning. When the user stays off the machine, the click-and-type sequence lands every
time. If a date is ever suspected of being lost, the tell-tale sign is an ETc total roughly a
**quarter** of what the crop should need, because the crop has been placed in a low-ETo window:
Hohhot spring wheat reads ≈450 mm with the date applied and 99 mm without.

Two guards keep the click reliable: the crop form is **moved to a fixed position** with
`SetWindowPos` before clicking (CROPWAT remembers window geometry, and a stale position makes
absolute-coordinate clicks miss), and stray `cropwat.exe` processes are killed after any failure.

## Reading results

`Calculations → CWR` (id 51) opens `TCwrForm`. Activate it as the MDI child, focus its grid, then
**Copy Table with id 43 (Data only)** — id 44 produced nothing on the tested build.

The tab-separated table has no header row. Columns are:

```
decade | stage | Kc | ETc (mm/day) | ETc (mm/decade) | effective rain (mm/dec) | irrigation req (mm/dec)
```

The last non-empty line is the total row; its final three numbers are the season totals —
**ETc, effective rainfall, irrigation requirement**.

Green/blue split:

```
green = sum( min(ETc, Peff) )
blue  = sum( max(0, ETc - Peff) )   = irrigation requirement
```

Unit conversions: `1 mm x 1 ha = 10 m3`; wind `m/s -> km/day` = `x 86.4`;
wind at 10 m -> 2 m = `x 4.87 / ln(67.8*10 - 5.42)` ≈ `0.748`.

## Excel deliverable

The user names the destination; the runner writes it. Pass `--excel <path>` to `run`, or convert an
existing summary later without recomputing:

```bash
python cropwat_run.py run   --data "<dir>" --sowing "..." --all \
    --excel "<用户指定路径>/结果.xlsx" --crop-names "WHEAT=小麦,MAIZE=玉米"

python cropwat_run.py excel --summary "<out>/CWR_summary.csv" \
    --out "<用户指定路径>/结果.xlsx" --crop-names "WHEAT=小麦"
```

Three sheets are produced:

| Sheet | Layout |
|---|---|
| **宽表(地方×年份)** | **one row per site-year, ordered by site so a site's years sit together.** Two-row header: crop name merged over three columns, then `ETc(mm)` / `Eff(mm)` / `Irr(mm)` per crop |
| **长表(面板格式)** | site, crop, year, sowing date, ETc, Eff, Irr — one row per observation, ready for panel regressions |
| **说明** | data source, indicator definitions, green/blue water formulas, unit conversion, and the sowing date used per crop |

Site grouping is the outer loop and year the inner loop, so the "same place, different years
adjacent" requirement holds by construction. `--crop-names` is optional; without it the `.CRO`
filenames are used as column labels.

## Other known traps

| Symptom | Cause | Fix |
|---|---|---|
| `Localizer Error` / `Failed to create key` | sandbox blocked the registry write | give full access |
| menu command does nothing | main window not foreground | `activate()` before every command |
| process exits silently while reading the clipboard | `GlobalLock` returned `c_int`, pointer truncated | set `restype = c_void_p` |
| `SetClipboardData` raises OverflowError | missing `argtypes` | set `argtypes = [UINT, c_void_p]` |
| file dialog never closes | matched "只读方式打开" | exclude `只读`/`取消`/`帮助` |
| crop file refuses to load | truncated sample (e.g. `KURN-SORGHUM-HYV.CRO`, 7 lines, no crop height) | use a complete FAO file |
| CWR table is empty | growth period ≥ 365 days (e.g. `KURN-SUGARCAN.CRO`) overflows a single year | keep total stage length < 365 |
| Chinese paths fail | **this was a misdiagnosis** — CROPWAT opens Chinese paths fine | no conversion needed |

## Batch runs

**One CROPWAT launch per combination** — see the operating rules above. Do not chain several
combinations through one instance: stacked MDI children make later menu commands miss their target,
and `Window → Close All` (id 72) only partly cleans up. `cropwat_run.py run` opens and closes a
fresh instance for every site × year × crop, which is why it costs ≈85 s per case.

Useful defaults when deriving planting dates: the FAO crop files carry stage lengths but no sowing
date, so the date must come from local agronomic references. Fixed dates are acceptable only if
stated as such — they bias results systematically otherwise.

Before committing to a large batch, use `--dry-run` to print the parameters, the combination count
and the time estimate, and run a couple of cases with `--limit 2` to confirm the date lands.
