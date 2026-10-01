#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
cropwat_run.py —— CROPWAT 批量计算命令行工具
=============================================
⚠ 两个参数**必须由用户指定**，没有默认值：
     --data     CROPWAT 工作文件目录
     --sowing   播种日期（DD/MM，日/月）

用法
----
  # ① 先检查工作文件是否齐全（不启动 CROPWAT）
  python cropwat_run.py check --data "E:\\path\\to\\工作目录"

  # ② 自检：能否启动并识别窗体
  python cropwat_run.py smoke

  # ③ 批量计算
  python cropwat_run.py run --data "E:\\path\\to\\工作目录" \
      --sowing "WHEAT=05/05,MAIZE=25/04,RICE=10/05" --limit 3

  # 单一日期（所有作物相同）——会给出警告
  python cropwat_run.py run --data "..." --sowing 05/05

目录结构约定
------------
  <data>/Climate/<地方>/<拼音>_<年份>.PEM
  <data>/Rain/<地方>/<拼音>_<年份>.CRM
  <data>/Crop/<作物名>.CRO
  <data>/*.SOI                      （唯一一个土壤文件；多个时用 --soil 指定）

播种日期格式为 DD/MM（日/月），例如 5月5日 = 05/05，4月20日 = 20/04。
"""
import argparse
import os
import re
import sys
import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cropwat_lib import CropWat, MENU, find_cropwat, cropwat_not_found_message  # noqa: F401

# ⚠ CROPWAT 的安装位置**不写死**。
#    运行时会自动查找：显式 --exe > 环境变量 > 注册表 > 常见目录 > 磁盘浅层扫描 > PATH
#    找法的实现见 cropwat_lib.find_cropwat()。

# 单组实测耗时（秒）。运行前据此估算总用时：
#     预计总用时 = 地点数 × 作物数 × 年数 × SEC_PER_CASE
SEC_PER_CASE = 85

# 结果表格固定名称与位置：直接放桌面，不需要用户指定
RESULT_NAME = 'cropwrt计算结果'


def desktop_dir():
    """定位当前用户的桌面目录（优先读注册表，退回 ~\\Desktop）"""
    try:
        import winreg
        k = r'Software\Microsoft\Windows\CurrentVersion\Explorer\Shell Folders'
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, k) as key:
            p, _ = winreg.QueryValueEx(key, 'Desktop')
            p = os.path.expandvars(p)
            if os.path.isdir(p):
                return p
    except Exception:
        pass
    for cand in (os.path.join(os.path.expanduser('~'), 'Desktop'),
                 os.path.join(os.path.expanduser('~'), '桌面'),
                 os.path.expanduser('~')):
        if os.path.isdir(cand):
            return cand
    return os.getcwd()


def default_excel_path():
    """桌面\\cropwrt计算结果.xlsx。

    固定名称、直接覆盖：汇总 CSV 会跨次累积，每次运行后整表重新生成，
    所以同一个文件始终是迄今为止的全部结果。
    """
    return os.path.join(desktop_dir(), RESULT_NAME + '.xlsx')


def work_dir_default():
    """中间文件（汇总 CSV、每组结果 txt）——放在用户目录下，不占桌面"""
    d = os.path.join(os.path.expanduser('~'), '.cropwat_run')
    os.makedirs(d, exist_ok=True)
    return d


# ============================================================ 工具
def fail(msg):
    print(f"\n错误: {msg}\n", file=sys.stderr)
    sys.exit(2)


def parse_sowing(spec, crops):
    """把 --sowing 解析成 {作物: 'DD/MM'}
    支持 'WHEAT=05/05,MAIZE=25/04' 或单一 '05/05'（套用到全部作物）。"""
    if not spec:
        fail("必须用 --sowing 指定播种日期（DD/MM，如 05/05 或 WHEAT=05/05,MAIZE=25/04）")
    spec = spec.strip()
    out = {}
    if '=' not in spec:
        if not re.fullmatch(r'\d{2}/\d{2}', spec):
            fail(f"--sowing 格式不对: {spec!r}；应为 DD/MM（如 05/05）或 作物=DD/MM 列表")
        print(f"⚠ 警告: --sowing 只给了一个日期 {spec}，将套用到全部作物。")
        print("   不同作物的播种期通常不同，建议逐作物指定，例如：")
        print("   --sowing \"WHEAT=05/05,MAIZE=25/04,RICE=10/05\"\n")
        return {c: spec for c in crops}
    for part in re.split(r'[,;]', spec):
        if not part.strip():
            continue
        if '=' not in part:
            fail(f"--sowing 片段缺少 '=': {part!r}")
        k, v = part.split('=', 1)
        k, v = k.strip().upper(), v.strip()
        if not re.fullmatch(r'\d{2}/\d{2}', v):
            fail(f"播种日期 {v!r} 格式不对，应为 DD/MM（如 05/05）")
        dd, mm = int(v[:2]), int(v[3:])
        if not (1 <= mm <= 12 and 1 <= dd <= 31):
            fail(f"播种日期 {v!r} 不是合法日期")
        out[k] = v
    return out


def parse_ddmm(s):
    return datetime.date(2001, int(s[3:]), int(s[:2]))     # 仅用于校验


def scan_data(data):
    """扫描工作目录，返回 (cities, crops, years, soil, 缺项)"""
    clim = os.path.join(data, 'Climate')
    rain = os.path.join(data, 'Rain')
    crop = os.path.join(data, 'Crop')
    if not os.path.isdir(data):
        fail(f"--data 目录不存在: {data}")
    for d in (clim, rain, crop):
        if not os.path.isdir(d):
            fail(f"工作目录缺少子目录: {d}")

    cities = sorted(x for x in os.listdir(clim) if os.path.isdir(os.path.join(clim, x)))
    crops = sorted(os.path.splitext(f)[0] for f in os.listdir(crop)
                   if f.upper().endswith('.CRO'))
    # 土壤文件可能在根目录，也可能在 Soil\ 或 Soils\ 子目录（cropwat-inputs 的分类输出）
    soils = []
    for d in (data, os.path.join(data, 'Soil'), os.path.join(data, 'Soils')):
        if os.path.isdir(d):
            for f in sorted(os.listdir(d)):
                if f.upper().endswith('.SOI'):
                    soils.append(os.path.join(d, f))
    soils = sorted(set(soils))

    years = set()
    for c in cities:
        for f in os.listdir(os.path.join(clim, c)):
            m = re.search(r'_(\d{4})\.PEM$', f, re.I)
            if m:
                years.add(int(m.group(1)))
    years = sorted(years)
    return cities, crops, years, soils, clim, rain, crop


def index_files(data):
    """建立 (地方, 年) -> (pem, crm) 索引"""
    clim = os.path.join(data, 'Climate')
    rain = os.path.join(data, 'Rain')
    idx = {}
    for city in os.listdir(clim):
        d = os.path.join(clim, city)
        if not os.path.isdir(d):
            continue
        for f in os.listdir(d):
            m = re.search(r'_(\d{4})\.PEM$', f, re.I)
            if not m:
                continue
            y = int(m.group(1))
            pem = os.path.join(d, f)
            stem = f[:-4]
            crm = None
            rd = os.path.join(rain, city)
            if os.path.isdir(rd):
                for rf in os.listdir(rd):
                    if rf.upper() == (stem + '.CRM').upper():
                        crm = os.path.join(rd, rf)
                        break
            idx[(city, y)] = (pem, crm, stem)
    return idx


# ============================================================ 命令
def cmd_check(args):
    cities, crops, years, soils, clim, rain, crop = scan_data(args.data)
    idx = index_files(args.data)
    print("=" * 78)
    print(f"工作目录: {args.data}")
    print("=" * 78)
    print(f"  地方 ({len(cities)}): {', '.join(cities)}")
    print(f"  作物 ({len(crops)}): {', '.join(crops)}")
    print(f"  年份 ({len(years)}): {years[0]}–{years[-1]}" if years else "  年份: 无")
    print(f"  土壤: {', '.join(soils) if soils else '未找到 .SOI'}")
    print()

    miss = [f"{c}{y}" for c in cities for y in years if (c, y) not in idx or idx[(c, y)][1] is None]
    print(f"  气候/降水配对: {len(idx)} 组；缺失 {len(miss)} 组")
    if miss:
        print("    缺失:", ', '.join(miss[:20]), '...' if len(miss) > 20 else '')
    for city, y in list(idx)[:2]:
        pem, crm, stem = idx[(city, y)]
        print(f"    样例 {city} {y}:")
        print(f"      PEM {pem}")
        print(f"      CRM {crm}")
    print()
    print(f"  潜在组合数: {len(cities)} 地方 × {len(crops)} 作物 × {len(years)} 年 = "
          f"{len(cities) * len(crops) * len(years)}")
    print()
    print("  下一步:")
    print(f'    python {os.path.basename(__file__)} run --data "{args.data}" '
          f'--sowing "WHEAT=05/05,..."')
    return 0


def cmd_smoke(args):
    print("=== 自检：查找 CROPWAT / 启动 / 窗体 / 菜单 ===")
    exe, wd = find_cropwat(explicit=args.exe, verbose=True)
    if not exe:
        fail(cropwat_not_found_message())
    print(f"  CROPWAT : {exe}")
    print(f"  工作目录: {wd}")
    with CropWat(exe=exe, workdir=wd) as cw:
        print("  主窗体:", cw.mainform())
        print("  启动时 MDI:", cw.mdi_describe())
        cw.menu(MENU.CLIMATE_MONTHLY_PM, 2.5)
        print("  新建气候后 MDI:", cw.mdi_describe())
        cw.close_all()
    print("  ✓ 通过")
    return 0


def totals(txt):
    for line in reversed([l for l in txt.replace('\r', '\n').split('\n') if l.strip()]):
        f = [x for x in line.split('\t') if x.strip()]
        if len(f) >= 3:
            try:
                return [float(x) for x in f[-3:]]
            except ValueError:
                continue
    return None


def _fmt_hms(seconds):
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    return f"{h} 小时 {m} 分" if h else f"{m} 分"


def _warn_no_touch(total_sec):
    """运行前最醒目的提示"""
    bar = "=" * 78
    print()
    print(bar)
    print(bar)
    print()
    print("        ⚠⚠⚠    测 算 期 间 请 勿 使 用 键 盘 和 鼠 标    ⚠⚠⚠")
    print("        ⚠⚠⚠    也 请 不 要 切 换 窗 口 / 遮 挡 窗 口    ⚠⚠⚠")
    print()
    print("   本程序通过模拟鼠标点击和按键来操作 CROPWAT。")
    print("   任何人工输入、窗口切换、弹窗遮挡都会干扰它，")
    print("   可能导致该组结果错误，甚至整组失败。")
    print()
    print(f"   本次预计连续运行  {_fmt_hms(total_sec)}")
    print("   请离开电脑，或至少不要动键鼠。")
    print()
    print("   （中断也没关系：结果逐组落盘，重新执行同一命令即可续跑）")
    print()
    print(bar)
    print(bar)
    print()


def cmd_run(args):
    # ---- 六项必填参数（由 argparse 保证非空）----
    cities_all, crops_all, years_all, soils, clim, rain, cropdir = scan_data(args.data)
    idx = index_files(args.data)

    cities = [c for c in cities_all if c in args.cities]
    miss_c = [c for c in args.cities if c not in cities_all]
    if miss_c:
        fail(f"--cities 里这些地点在工作目录中不存在: {', '.join(miss_c)}\n"
             f"       可选的 {len(cities_all)} 个地点: {', '.join(cities_all)}")

    crops = [c for c in crops_all if c.upper() in [x.upper() for x in args.crops]]
    miss_k = [x for x in args.crops if x.upper() not in [c.upper() for c in crops_all]]
    if miss_k:
        fail(f"--crops 里这些作物在工作目录中不存在: {', '.join(miss_k)}\n"
             f"       可选的 {len(crops_all)} 种作物: {', '.join(crops_all)}")

    years = sorted(y for y in years_all if y in args.years)
    miss_y = [y for y in args.years if y not in years_all]
    if miss_y:
        fail(f"--years 里这些年份在工作目录中没有数据: {miss_y}\n"
             f"       可选的年份: {years_all[0]}–{years_all[-1]}" if years_all else "无年份")

    if not (cities and crops and years):
        fail("地点/作物/年份筛选后为空")

    soil = args.soil or (soils[0] if soils else None)
    if not soil:
        fail("工作目录里没有 .SOI 土壤文件，请用 --soil 指定")
    soil = soil if os.path.isabs(soil) else os.path.join(args.data, soil)
    if not os.path.exists(soil):
        fail(f"土壤文件不存在: {soil}")

    # ---- 播种期（必填，逐作物）----
    sowing = parse_sowing(args.sowing, crops)
    missing = [c for c in crops if c not in sowing and c.upper() not in sowing
               and not any(k.upper() == c.upper() for k in sowing)]
    if missing:
        fail(f"这些作物没有指定播种期: {', '.join(missing)}\n"
             f'       请用 --sowing "WHEAT=05/05,MAIZE=25/04,..." 逐作物给出（DD/MM）')

    # ---- 结果表格：固定放桌面，不需要用户指定 ----
    xlsx = os.path.abspath(args.excel) if args.excel else default_excel_path()
    if not xlsx.lower().endswith('.xlsx'):
        fail(f"结果表格必须是 .xlsx 路径: {xlsx}")
    d = os.path.dirname(xlsx)
    try:
        if d:
            os.makedirs(d, exist_ok=True)
    except Exception as e:
        fail(f"无法创建结果表格的输出目录 {d}: {e}")
    # 中间文件放用户目录，不占桌面
    outdir = os.path.abspath(args.out) if args.out else work_dir_default()
    os.makedirs(outdir, exist_ok=True)

    crop_names = {}
    for part in (args.crop_names or '').split(','):
        if '=' in part:
            k, v = part.split('=', 1)
            crop_names[k.strip().upper()] = v.strip()

    # ---- CROPWAT 位置：自动查找（不写死）----
    cw_exe, cw_wd = find_cropwat(explicit=args.exe, verbose=False)
    if not cw_exe:
        fail(cropwat_not_found_message())

    # ---- 任务清单 ----
    tasks = [(c, cr, y) for c in cities for cr in crops for y in years]
    if args.limit is not None:
        tasks = tasks[:max(0, args.limit)]
    if not tasks:
        fail("任务数为 0")

    # ---- 断点续跑 ----
    done = set()
    if args.resume and os.path.isdir(outdir):
        for f in os.listdir(outdir):
            if f.endswith('.txt') and not f.startswith('trace'):
                done.add(f[:-4])
    todo = [t for t in tasks if f'{t[0]}_{t[1]}_{t[2]}' not in done]

    total_sec = len(todo) * SEC_PER_CASE

    # ---- 参数摘要 + 用时估算 ----
    bar = "=" * 78
    print()
    print(bar)
    print("  CROPWAT 批量测算 —— 参数确认")
    print(bar)
    print(f"  ① CROPWAT 工作文件位置 : {os.path.abspath(args.data)}")
    print(f"  ② 结果表格（自动）     : {xlsx}")
    print(f"     （中间文件           : {outdir}）")
    print(f"  ③ 地点范围 ({len(cities):>2} 个)    : {', '.join(cities)}")
    print(f"  ④ 作物范围 ({len(crops):>2} 种)    : {', '.join(crops)}")
    print(f"  ⑤ 时间范围 ({len(years):>2} 年)    : {years[0]}–{years[-1]}"
          if years else "  ⑤ 时间范围             : 无")
    print(f"  ⑥ 播种日期 (DD/MM)     : "
          + ", ".join(f"{c}={sowing.get(c, sowing.get(c.upper(), '?'))}" for c in crops))
    print(f"     土壤文件             : {soil}")
    print(f"     CROPWAT              : {cw_exe}")
    print(bar)
    print(f"  组合总数 = {len(cities)} 地点 × {len(crops)} 作物 × {len(years)} 年 "
          f"= {len(cities) * len(crops) * len(years)} 组")
    if done:
        print(f"  已完成 {len(done)} 组，本次将执行 {len(todo)} 组（续跑已自动跳过）")
    print(f"  单组实测耗时 ≈ {SEC_PER_CASE} 秒")
    print(f"  预计总用时 = {len(todo)} × {SEC_PER_CASE} 秒 = {_fmt_hms(total_sec)}"
          + (f"（约 {total_sec / 3600:.1f} 小时）" if total_sec >= 3600 else ""))
    print(bar)

    if not todo:
        print("\n所有任务都已完成，无需运行。")
        return 0

    if args.dry_run:
        print("\n[干跑] 不启动 CROPWAT。将要执行的任务：")
        for i, (c, cr, y) in enumerate(todo[:60], 1):
            print(f"  {i:>4}. {c} / {cr} / {y}")
        if len(todo) > 60:
            print(f"  ... 其余 {len(todo) - 60} 组")
        print("\n去掉 --dry-run 即开始执行。")
        return 0

    # ---- 醒目警告 ----
    _warn_no_touch(total_sec)

    ok = fail_n = skip = 0
    summary = [['地方', '作物', '年份', '播种期', 'ETc_mm', '有效降水_mm', '灌溉需水_mm']]
    csvpath = os.path.join(outdir, 'CWR_summary.csv')

    def flush():
        """每完成一组就落盘，中断也不会丢进度（配合续跑可接着跑）"""
        import csv as _csv
        tmp = csvpath + '.tmp'
        with open(tmp, 'w', encoding='utf-8-sig', newline='') as f:
            _csv.writer(f).writerows(summary)
        os.replace(tmp, csvpath)      # 原子替换，避免中断留下半个文件

    # 续跑时把已有结果读回来，最终 Excel 才是完整的
    if done and os.path.exists(csvpath):
        try:
            for r in load_summary(csvpath):
                summary.append(r)
        except Exception:
            pass
    flush()

    # ⚠ 每次启动 CROPWAT 只算一组（一地点 × 一年 × 一作物），算完立即关闭。
    #
    # ⚠ 注意下面的调用序列里**没有**茬口（Cropping Pattern）模块：
    #   茬口模块的输入会被 CWR 计算完全忽略，填不填、建不建都不影响
    #   ETc / 有效降水 / 灌溉需水。CROPWAT 只从 **Crop 模块**读播种期。
    #   所以这里直接跳过它，用 set_planting_date() 写进 Crop 窗体即可。
    try:
        for i, (city, crp, year) in enumerate(todo, 1):
            tag = f'{city}_{crp}_{year}'
            pem, crm, stem = idx.get((city, year), (None, None, None))
            if not pem or not crm:
                print(f"  [{i}/{len(todo)}] {tag} ⚠ 缺气候或降水文件，跳过", flush=True)
                skip += 1
                continue
            sdate = sowing.get(crp) or sowing.get(crp.upper())
            cro = os.path.join(cropdir, f'{crp}.CRO')
            if not os.path.exists(cro):
                cro = next((os.path.join(cropdir, f) for f in os.listdir(cropdir)
                            if os.path.splitext(f)[0].upper() == crp.upper()), None)
            if not cro:
                print(f"  [{i}/{len(todo)}] {tag} ⚠ 找不到作物文件，跳过", flush=True)
                skip += 1
                continue

            t0 = datetime.datetime.now()
            txt = None
            last_err = None
            logp = os.path.join(outdir, 'logs', f'{tag}.txt') if args.debug else None
            if logp:
                os.makedirs(os.path.dirname(logp), exist_ok=True)

            for attempt in range(1, max(1, args.retry) + 1):
                try:
                    with CropWat(exe=cw_exe, workdir=cw_wd, verbose=False,
                                 log_path=logp if attempt == 1 else None) as cw:
                        cw.new_session()
                        cw.load_climate(pem)
                        cw.load_rain(crm)
                        cw.load_crop(cro, rice=(crp.upper() == 'RICE'))
                        cw.load_soil(soil)
                        cw.set_planting_date(sdate)
                        txt = cw.run_cwr()
                except Exception as e:
                    last_err = f"{type(e).__name__}: {e}"
                    txt = None
                    _kill_stray()
                    continue
                if not txt or not txt.strip():
                    last_err = '未取到结果'
                    txt = None
                    _kill_stray()
                    continue
                break

            dt = (datetime.datetime.now() - t0).total_seconds()
            if not txt or not txt.strip():
                fail_n += 1
                print(f"  [{i}/{len(todo)}] {tag} ✗ {last_err or '未取到结果'}（{dt:.0f}s）", flush=True)
                continue
            open(os.path.join(outdir, f'{tag}.txt'), 'w', encoding='utf-8').write(txt)
            t = totals(txt) or [None, None, None]
            summary.append([city, crp, year, sdate] + t)
            ok += 1
            flush()
            eta = _fmt_hms((len(todo) - i) * SEC_PER_CASE)
            print(f"  [{i}/{len(todo)}] {tag}  ETc={t[0]}  有效降水={t[1]}  灌溉需水={t[2]}"
                  f"  ({dt:.0f}s)   剩余约 {eta}", flush=True)
    except KeyboardInterrupt:
        print("\n\n[中断] 收到停止信号，正在收尾…", flush=True)

    flush()
    _kill_stray()
    print()
    print(f"完成：成功 {ok}  失败 {fail_n}  跳过 {skip}")

    # ---- 生成结果 Excel ----
    try:
        p, n = build_excel(summary[1:], xlsx, crop_names)
        print(f"结果表格: {p}   （{n} 条记录，按地点分组、同年份相邻）")
    except Exception as e:
        print(f"⚠ 生成结果表格失败: {type(e).__name__}: {e}")

    if not args.no_resume_hint:
        print(f"续跑: 若中途中断，重新执行同一命令即可跳过已完成的组")
    return 0


def _kill_stray():
    """清掉可能残留的 CROPWAT 进程，避免影响下一次运行"""
    try:
        import subprocess
        subprocess.run(['taskkill', '/F', '/IM', 'cropwat.exe'],
                       capture_output=True, timeout=15)
    except Exception:
        pass


# ============================================================ Excel 输出
# 汇总 CSV 的列：地方, 作物, 年份, 播种期, ETc_mm, 有效降水_mm, 灌溉需水_mm  → 共 7 列
SUMMARY_COLS = 7


def load_summary(path):
    """读回 CWR_summary.csv -> [(地方, 作物, 年份, 播种期, ETc, Eff, Irr)]"""
    import csv as _csv
    rows = []
    with open(path, encoding='utf-8-sig', newline='') as f:
        rd = _csv.reader(f)
        next(rd, None)                       # 跳过表头
        for r in rd:
            if len(r) < SUMMARY_COLS or not r[0].strip():
                continue                     # 跳过残缺行/空行
            rows.append(r[:SUMMARY_COLS])
    return rows


def build_excel(rows, out_path, crop_names=None):
    """生成结果 Excel。

    工作表:
      ① 宽表(地方×年份)  —— 按地方分组，同一地方的年份相邻；每种作物三列 ETc/Eff/Irr
      ② 长表(面板格式)   —— 地方/作物/年份/ETc/Eff/Irr，可直接喂给面板回归
      ③ 说明             —— 单位、指标含义、播种期
    """
    from openpyxl import Workbook
    from openpyxl.styles import Font, Alignment, PatternFill, Border, Side
    from openpyxl.utils import get_column_letter

    def num(x):
        try:
            return float(x)
        except (TypeError, ValueError):
            return None

    cn = crop_names or {}

    def crop_label(c):
        return cn.get(c, cn.get(c.upper(), c))

    # ---- 整理数据 ----
    data = {}
    sowing_by_crop = {}
    cities = []
    for r in rows:
        city, crop, year, sow = r[0], r[1], r[2], r[3]
        etc, eff, irr = num(r[4]), num(r[5]), num(r[6])
        data[(city, int(year), crop)] = (etc, eff, irr)
        sowing_by_crop.setdefault(crop, sow)
        if city not in cities:
            cities.append(city)
    if not cities:
        raise SystemExit("汇总里没有可用数据")

    crops = sorted({k[2] for k in data}, key=lambda c: (c.upper() != 'WHEAT', c))
    years = sorted({k[1] for k in data})

    wb = Workbook()
    HDR = Font(bold=True, color='FFFFFF', size=10)
    FILL = PatternFill('solid', fgColor='2F5597')
    SUB = PatternFill('solid', fgColor='D9E2F3')
    THIN = Side(style='thin', color='D0D0D0')
    BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
    CEN = Alignment(horizontal='center', vertical='center')

    # ---- ① 宽表 ----
    ws = wb.active
    ws.title = '宽表(地方×年份)'
    # 两行表头：第1行作物名（横向合并3列），第2行指标名
    #   地方 | 年份 |      小麦      |      玉米      |
    #               ETc Eff  Irr    ETc Eff  Irr
    ws.append(['地方', '年份'] + [crop_label(c) if k == 0 else ''
                                  for c in crops for k in range(3)])
    ws.append(['', ''] + ['ETc(mm)', 'Eff(mm)', 'Irr(mm)'] * len(crops))
    # ⚠ 按地方分组：外层地方、内层年份 → 同一地方的年份必然相邻
    for city in cities:
        for y in years:
            row = [city, y]
            for c in crops:
                etc, eff, irr = data.get((city, y, c), (None, None, None))
                row += [etc, eff, irr]
            ws.append(row)

    ws.merge_cells(start_row=1, start_column=1, end_row=2, end_column=1)
    ws.merge_cells(start_row=1, start_column=2, end_row=2, end_column=2)
    col = 3
    for c in crops:
        ws.merge_cells(start_row=1, start_column=col, end_row=1, end_column=col + 2)
        for k in range(3):
            ws.cell(row=1, column=col + k).fill = SUB
            ws.cell(row=1, column=col + k).font = Font(bold=True)
        col += 3
    # 表头样式
    for r in (1, 2):
        for cell in ws[r]:
            cell.alignment = CEN
            cell.border = BORDER
            cell.fill = SUB
    for cell in ws[1][:2]:
        cell.font = HDR
        cell.fill = FILL
    for cell in ws[1][2:]:
        cell.font = Font(bold=True)
    for cell in ws[2][:2]:
        cell.fill = FILL
    ws.freeze_panes = 'C3'
    ws.column_dimensions['A'].width = 12
    ws.column_dimensions['B'].width = 8
    for i in range(3, 3 + len(crops) * 3):
        ws.column_dimensions[get_column_letter(i)].width = 9

    # ---- ② 长表（面板格式）----
    ws2 = wb.create_sheet('长表(面板格式)')
    ws2.append(['地方', '作物', '年份', '播种期(DD/MM)',
                'ETc(mm)', '有效降水Eff(mm)', '灌溉需水Irr(mm)'])
    for city in cities:
        for c in crops:
            for y in years:
                v = data.get((city, y, c))
                if v is None:
                    continue
                ws2.append([city, c, y, sowing_by_crop.get(c, ''), v[0], v[1], v[2]])
    for cell in ws2[1]:
        cell.font = HDR
        cell.fill = FILL
        cell.alignment = CEN
        cell.border = BORDER
    for row in ws2.iter_rows(min_row=2):
        for cell in row:
            cell.border = BORDER
    ws2.freeze_panes = 'A2'
    ws2.auto_filter.ref = ws2.dimensions
    for i, w in enumerate([12, 12, 8, 15, 11, 16, 16], 1):
        ws2.column_dimensions[get_column_letter(i)].width = w

    # ---- ③ 说明 ----
    ws3 = wb.create_sheet('说明')
    for line in [
        ['项目', '说明'],
        ['数据来源', 'FAO CROPWAT 8.0，彭曼-蒙特斯法（FAO-56）'],
        ['ETc', '作物需水量（mm），生育期内累计'],
        ['Eff', '有效降水量（mm），生育期内累计'],
        ['Irr', '灌溉需水量（mm），生育期内累计'],
        ['绿水足迹', 'Σ min(ETc, Eff)'],
        ['蓝水足迹', 'Σ max(0, ETc − Eff)，即 Irr'],
        ['单位换算', '1 mm × 1 ha = 10 m³'],
        ['播种期', '格式 DD/MM（日/月），必须按当地农艺资料给定；全部用同一天会引入系统性偏差'],
        ['', ''],
        ['作物', '播种期(DD/MM)', ''],
    ] + [[c, sowing_by_crop.get(c, '')] for c in crops]:
        ws3.append(line)
    ws3['A1'].font = Font(bold=True)
    ws3['B1'].font = Font(bold=True)
    ws3.column_dimensions['A'].width = 16
    ws3.column_dimensions['B'].width = 60
    ws3.column_dimensions['C'].width = 16

    wb.save(out_path)
    return out_path, len(rows)


def cmd_excel(args):
    """把已有的汇总 CSV 转成 Excel（不重跑计算）"""
    rows = load_summary(args.summary)
    if not rows:
        fail(f"汇总文件没有数据: {args.summary}")
    names = {}
    for part in (args.crop_names or '').split(','):
        if '=' in part:
            k, v = part.split('=', 1)
            names[k.strip().upper()] = v.strip()
    p, n = build_excel(rows, args.out, names)
    print(f"已生成: {p}   （{n} 条记录）")
    return 0


# ============================================================ 入口
def main():
    p = argparse.ArgumentParser(
        prog='cropwat_run.py',
        description='CROPWAT 批量计算（需用户指定工作目录与播种日期）',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__)
    p.add_argument('--exe', default=None, metavar='PATH',
                   help='CROPWAT 的 cropwat.exe 路径（**默认自动查找**：'
                        '环境变量 CROPWAT_EXE > 注册表 > 常见目录 > 磁盘浅层扫描 > PATH）')
    sub = p.add_subparsers(dest='cmd')

    pc = sub.add_parser('check', help='检查工作文件是否齐全（不启动 CROPWAT）')
    pc.add_argument('--data', required=True, help='★ CROPWAT 工作文件目录（必填）')
    pc.set_defaults(func=cmd_check)

    ps = sub.add_parser('smoke', help='自检：启动 CROPWAT 并识别窗体')
    ps.set_defaults(func=cmd_smoke)

    pr = sub.add_parser('run', help='批量计算',
                        formatter_class=argparse.RawDescriptionHelpFormatter,
                        description='五项参数必填：工作文件位置、播种日期、时间范围、地点范围、作物范围。\n'
                                    'CROPWAT 位置自动查找；结果表格自动写到桌面。')
    pr.add_argument('--data', required=True, metavar='DIR',
                    help='① 必填 CROPWAT 工作文件位置（内含 Climate/ Rain/ Crop/ 与 .SOI）')
    pr.add_argument('--sowing', required=True, metavar='SPEC',
                    help='② 必填 播种日期 DD/MM（日/月），逐作物：'
                         '"WHEAT=05/05,MAIZE=25/04,RICE=10/05"')
    pr.add_argument('--years', required=True, nargs='+', type=int, metavar='Y',
                    help='③ 必填 时间范围（年份列表），如 --years 2013 2014 ... 2024')
    pr.add_argument('--cities', required=True, nargs='+', metavar='CITY',
                    help='④ 必填 地点范围（工作目录里的地点目录名）')
    pr.add_argument('--crops', required=True, nargs='+', metavar='CROP',
                    help='⑤ 必填 作物范围（.CRO 文件名，不含扩展名）')
    pr.add_argument('--excel', metavar='XLSX',
                    help=f'结果表格路径（**默认桌面\\{RESULT_NAME}.xlsx，一般无需指定**）')
    pr.add_argument('--crop-names', metavar='MAP',
                    help='结果表格里的作物中文名，如 "WHEAT=小麦,MAIZE=玉米"（可省略）')
    pr.add_argument('--soil', metavar='FILE',
                    help='土壤文件（默认取工作目录下唯一的 .SOI）')
    pr.add_argument('--out', metavar='DIR',
                    help='中间文件目录（默认放到结果表格同级的 _cwr_work）')
    pr.add_argument('--limit', type=int, help='只跑前 N 组（0 表示不跑任何组）')
    pr.add_argument('--dry-run', action='store_true',
                    help='只打印任务清单与用时估算，不启动 CROPWAT')
    pr.add_argument('--no-resume', dest='resume', action='store_false',
                    help='不跳过已有结果，全部重算（默认跳过已完成的组）')
    pr.add_argument('--no-resume-hint', dest='no_resume_hint', action='store_true',
                    help='结尾不打印续跑提示')
    pr.add_argument('--retry', type=int, default=3,
                    help='单组失败时的重试次数（默认 3）')
    pr.add_argument('--debug', action='store_true',
                    help='为每组任务保存一份详细日志到 <out>/logs/')
    pr.set_defaults(func=cmd_run, resume=True, no_resume_hint=False)

    pe = sub.add_parser('excel', help='把已有的汇总 CSV 转成 Excel（不重跑计算）')
    pe.add_argument('--summary', required=True, help='CWR_summary.csv 路径')
    pe.add_argument('--out', required=True, help='★ 输出 Excel 路径')
    pe.add_argument('--crop-names', help='作物中文名，如 "WHEAT=小麦,MAIZE=玉米"')
    pe.set_defaults(func=cmd_excel)

    a = p.parse_args()
    if not a.cmd:
        p.print_help()
        print("\n⚠ 提示：run 子命令必须提供 --data 与 --sowing")
        return 2
    return a.func(a)


if __name__ == '__main__':
    sys.exit(main())
