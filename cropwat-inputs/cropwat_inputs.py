# -*- coding: utf-8 -*-
"""
cropwat_inputs.py —— 生成与校验 CROPWAT 8.0 的输入文件
=====================================================
支持两种创建方式：

  A. 从原始气象数据算（推荐）
     Tmin/Tmax/相对湿度/风速/日照 → FAO-56 Penman-Monteith → ETo → .PEM
     逐月降水量                                              → .CRM

  B. 按固定宽度直接写（已有算好的 Rad/ETo 时）

同时提供**读回**与**自检**：CROPWAT 自己会重算 Rad 与 ETo 两列，
所以判断文件是否正确，不能只看那两列。

固定宽度格式（已用真实文件逐字节核对）
--------------------------------------
.PEM   18 行 = 6 行头 + 12 行月数据 + 末尾换行，数据行 48 字符
        数据行 = Tmin%6.1f  Tmax%6.1f  RH%7.1f  Wind%7.1f  Sun%7.1f  Rad%6.1f  ETo%9.2f
        ⚠ **Tmin 在前**（CLIMWAT 的 .pen 是反的：Tmax 在前，70 字符 7 段 %10）
.CRM   16 行 = 4 行头 + 12 行月数据 + 末尾换行，数据行 74 字符
        数据行 = 6 个 -99.9(%9.1f) + 降水量(%10.1f) + 有效降水占位(%10.1f)

编码 encoding='latin-1'，行尾 newline='\\r\\n'（缺一不可）。
"""
import csv
import json
import urllib.parse
import urllib.request
import math
import os
import sys
import time

# ============================================================ 常量

PEM_HEADER = 'CROPWAT 8.0 Climate data'
CRM_HEADER = 'CROPWAT 8.0 Rain data'
MISSING = -99.9

# .PEM 头部第 2 行（CROPWAT 建新气候文件时的固定值）
PEM_TYPE_LINE = ' 0   3'

# .CRM 头部第 3、4 行。两套值都能被 CROPWAT 读取：
#   CROPWAT 自带样板      : '  1  3' / '80  0  0  0  0  0'
#   本项目 288 个文件实测 : '  4  3' / '  0  0  0  0  0  0'  ← 默认用这套（已验证）
CRM_HDR_LINE2 = '  4  3'
CRM_HDR_LINE3 = '  0  0  0  0  0  0'
CRM_HDR_LINE2_FAO = '  1  3'
CRM_HDR_LINE3_FAO = '80  0  0  0  0  0'

DAYS = [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
MONTH_NAMES = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
               'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

# 工作目录的默认名字：直接放桌面，不需要用户指定路径
WORKDIR_NAME = 'CROPWRT工作文件'


def desktop_dir():
    """定位当前用户的桌面目录（优先读注册表，退回 ~\\Desktop）"""
    try:
        import winreg
        k = r'Software\Microsoft\Windows\CurrentVersion\Explorer\Shell Folders'
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, k) as key:
            p = os.path.expandvars(winreg.QueryValueEx(key, 'Desktop')[0])
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


def default_workdir():
    """桌面\\CROPWRT工作文件 —— 生成出来的整套工作文件都放这里"""
    return os.path.join(desktop_dir(), WORKDIR_NAME)


def days_in_month(y, m):
    if m == 2 and (y % 4 == 0 and (y % 100 != 0 or y % 400 == 0)):
        return 29
    return DAYS[m - 1]


def mid_month_doy(y, m):
    """该月中旬的年内日序 —— .PEM 每月一行，用 15 日作代表"""
    import datetime
    return datetime.date(y, m, 15).timetuple().tm_yday


# ============================================================ FAO-56 计算

def esat(t):
    """饱和水汽压 (kPa)，t 为 ℃"""
    return 0.6108 * math.exp(17.27 * t / (t + 237.3))


def ra(doy, lat):
    """天顶辐射 Ra (MJ m-2 d-1) —— FAO-56 式 21"""
    phi = math.radians(lat)
    dr = 1 + 0.033 * math.cos(2 * math.pi * doy / 365)
    dec = 0.409 * math.sin(2 * math.pi * doy / 365 - 1.39)
    ws = math.acos(max(-1.0, min(1.0, -math.tan(phi) * math.tan(dec))))
    return ((24 * 60 / math.pi) * 0.0820 * dr *
            (ws * math.sin(phi) * math.sin(dec) +
             math.cos(phi) * math.cos(dec) * math.sin(ws)))


def daylight(doy, lat):
    """最大日照时数 N (h) —— FAO-56 式 34"""
    phi = math.radians(lat)
    dec = 0.409 * math.sin(2 * math.pi * doy / 365 - 1.39)
    ws = math.acos(max(-1.0, min(1.0, -math.tan(phi) * math.tan(dec))))
    return 24 / math.pi * ws


def wind_10m_to_2m(u10):
    """10 m 风速 → 2 m 风速（FAO-56 式 47），系数约 0.748"""
    return u10 * 4.87 / math.log(67.8 * 10 - 5.42)


def ms_to_kmd(u):
    """m/s → km/day"""
    return u * 86.4


def eto_pm(tmax, tmin, rh, u2, sun_h, doy, lat, alt):
    """FAO-56 Penman-Monteith 日 ETo。

    参数
    ----
    tmax, tmin : ℃            月平均最高/最低气温
    rh         : %            月平均相对湿度
    u2         : **m/s**      2 m 高度风速
                 ⚠⚠ FAO-56 彭曼公式里的 u2 是 m/s。
                    .PEM 文件里存的是 **km/day**，读回时务必 ÷86.4！
                    直接把 km/day 喂进来会让风速项偏大约 86 倍。
    sun_h      : hours/day    实际日照时数（⚠ 不是每月小时数）
    doy        : 年内日序（用 15 日）
    lat        : 纬度（度，北正）
    alt        : 海拔（m）

    返回 (Rs, ETo)：Rs 为太阳辐射 MJ m-2 d-1，ETo 为 mm/day。
    """
    ra_ = ra(doy, lat)
    n = daylight(doy, lat)
    # 埃斯屈朗：Rs = (0.25 + 0.50·n/N)·Ra      ← 系数 0.25/0.50 是 FAO 默认
    rs = (0.25 + 0.50 * min(sun_h / n, 1.0)) * ra_
    rso = (0.75 + 2e-5 * alt) * ra_          # 晴空辐射
    rns = 0.77 * rs                          # 净短波（反照率 0.23）
    es = (esat(tmax) + esat(tmin)) / 2.0     # 饱和水汽压（取 Tmax/Tmin 均值）
    ea = rh / 100.0 * es                     # 实际水汽压
    rnl = (4.903e-9 *
           ((tmax + 273.16) ** 4 + (tmin + 273.16) ** 4) / 2.0 *
           (0.34 - 0.14 * math.sqrt(max(ea, 1e-6))) *
           (1.35 * min(rs / rso, 1.0) - 0.35))   # 净长波
    rn = rns - rnl
    tmean = (tmax + tmin) / 2.0
    p = 101.3 * ((293 - 0.0065 * alt) / 293) ** 5.26   # 气压
    gamma = 0.665e-3 * p                                # 湿度计常数
    delta = 4098 * esat(tmean) / (tmean + 237.3) ** 2   # 饱和水汽压曲线斜率
    num = 0.408 * delta * rn + gamma * (900 / (tmean + 273)) * u2 * (es - ea)
    den = delta + gamma * (1 + 0.34 * u2)
    return rs, max(0.0, num / den)


# ============================================================ .PEM 读写

def write_pem(path, name, lat, lon, alt, months, crm_header_like=False):
    """写 .PEM。

    months: 12 个 dict，键为
        tmin, tmax, rh, wind_kmd, sun_h, rad(可选), eto(可选)
    rad/eto 不给就自动用 FAO-56 算（给不给都无所谓——CROPWAT 会自己重算这两列）。
    """
    rows = []
    for i, m in enumerate(months):
        doy = m.get('doy') or 180
        rs = m.get('rad')
        e = m.get('eto')
        if rs is None or e is None:
            # ⚠ 文件里存 km/day，FAO-56 公式要 m/s
            rs2, e2 = eto_pm(m['tmax'], m['tmin'], m['rh'],
                             m['wind_kmd'] / 86.4,
                             m['sun_h'], doy, lat, alt)
            rs = rs2 if rs is None else rs
            e = e2 if e is None else e
        rows.append(f"{m['tmin']:6.1f}{m['tmax']:6.1f}{m['rh']:7.1f}"
                    f"{m['wind_kmd']:7.1f}{m['sun_h']:7.1f}{rs:6.1f}{e:9.2f}")
    lines = [PEM_HEADER, PEM_TYPE_LINE, name, name, f"{alt:.2f}",
             f"{lat:8.2f}{lon:8.2f}"] + rows
    _write(path, lines)


def read_pem(path):
    """.PEM → dict（含 12 个月的原始列值）"""
    L = _read_lines(path)
    if not L[0].startswith('CROPWAT'):
        raise ValueError(f"不像 .PEM 文件: {path}")
    out = {'name': L[2], 'alt': float(L[4]),
           'lat': float(L[5][0:8]), 'lon': float(L[5][8:16]), 'months': []}
    for i in range(6, 18):
        s = L[i]
        out['months'].append({
            'tmin': float(s[0:6]), 'tmax': float(s[6:12]), 'rh': float(s[12:19]),
            'wind_kmd': float(s[19:26]), 'sun_h': float(s[26:33]),
            'rad': float(s[33:39]), 'eto': float(s[39:48]),
        })
    return out


def check_pem(path, tol_eto=0.02, tol_rad=0.15):
    """自检：用前五列重算 Rad/ETo，与文件里存的值比。

    返回 (ok, 最大ETo偏差, 最大Rad偏差, 逐月明细)。
    ⚠ CROPWAT 会重算这两列，所以它们**不是**判断文件对错的依据；
      这里只是核对前五列与算出来的量自洽，纯做数据体检。
    """
    d = read_pem(path)
    detail = []
    worst_e = worst_r = 0.0
    for i, m in enumerate(d['months']):
        doy = m.get('doy') or _doy_from_name(d['name'], i + 1)
        # ⚠ 文件是 km/day，公式要 m/s
        rs, e = eto_pm(m['tmax'], m['tmin'], m['rh'], m['wind_kmd'] / 86.4,
                       m['sun_h'], doy, d['lat'], d['alt'])
        de, dr = abs(e - m['eto']), abs(rs - m['rad'])
        worst_e, worst_r = max(worst_e, de), max(worst_r, dr)
        detail.append((i + 1, m['eto'], round(e, 2), m['rad'], round(rs, 2)))
    return (worst_e <= tol_eto and worst_r <= tol_rad), worst_e, worst_r, detail


def _doy_from_name(name, month):
    """文件名以 _YYYY 结尾时用真实年份定日序，否则按平年"""
    import re
    m = re.search(r'_(\d{4})$', name)
    y = int(m.group(1)) if m else 2001
    return mid_month_doy(y, month)


# ============================================================ .CRM 读写

def write_crm(path, name, rain_mm, fao_header=False):
    """写 .CRM。rain_mm: 12 个月的降水量 (mm)，None 表示缺测"""
    h2 = CRM_HDR_LINE2_FAO if fao_header else CRM_HDR_LINE2
    h3 = CRM_HDR_LINE3_FAO if fao_header else CRM_HDR_LINE3
    rows = []
    for x in rain_mm:
        v = f"{MISSING:>10.1f}" if x is None else f"{x:>10.1f}"
        # 6 个占位（旬/日降水等，本工具不使用）+ 月降水 + 有效降水占位
        rows.append(f"{MISSING:>9.1f}" * 6 + v + f"{MISSING:>10.1f}")
    _write(path, [CRM_HEADER, name, h2, h3] + rows)


def read_crm(path):
    """.CRM → 12 个月的降水量 (mm)，缺测为 None"""
    L = _read_lines(path)
    if not L[0].startswith('CROPWAT'):
        raise ValueError(f"不像 .CRM 文件: {path}")
    out = []
    for i in range(4, 16):
        s = L[i]
        if len(s) < 74:
            raise ValueError(f"第 {i+1} 行长度 {len(s)}，应为 74")
        v = float(s[54:64].strip())
        out.append(None if v <= MISSING + 0.05 else v)
    return {'name': L[1], 'rain': out}


# ============================================================ 底层

def _write(path, lines):
    """⚠ latin-1 + CRLF，两者缺一不可"""
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, 'w', encoding='latin-1', newline='\r\n') as f:
        f.write('\n'.join(lines) + '\n')


def _read_lines(path):
    raw = open(path, 'rb').read().decode('latin-1')
    return raw.replace('\r\n', '\n').rstrip('\n').split('\n')


# ============================================================ 定位 CROPWAT 的 data 目录

def find_cropwat_data(explicit=None, verbose=False):
    """自动查找 CROPWAT 安装目录下的 data\\ 目录。

    顺序：显式路径 > 环境变量 > 注册表 > 常见目录 > 磁盘浅层扫描。
    找到 cropwat.exe 就取它旁边的 data\\。
    返回 data 目录路径；找不到返回 None。
    """
    def say(m):
        if verbose:
            print(m, flush=True)

    def as_data(p):
        """把各种可能的输入都归一到 CROPWAT 的**安装根目录**。

        ⚠ CROPWAT 装完后 crops/ climate/ rain/ soils/ 会在**两处**出现：
              <安装根>\\crops\\            ← 程序自带的原始文件
              <安装根>\\data\\crops\\      ← 用户自己那份（可能被改过）
          实测确认：data\\ 下会被用户编辑（例：data\\soils\\FAO\\MEDIUM.SOI 的
          水稻参数被改成 0 / 125，而根目录那份仍是 -999）。
          **所以优先取安装根目录**，保证拿到的是程序自带的原始文件。

          传入的路径若已经是 data 目录，则用它的上一级（安装根）。
        """
        if not p:
            return None
        p = os.path.abspath(os.path.expandvars(p.strip('"').strip()))
        base = p
        # 传进来的是 ...\data 就退回安装根
        if os.path.basename(p).lower() == 'data':
            base = os.path.dirname(p)
        cands = [base,                                   # ← 优先：程序自带
                 os.path.join(base, 'data'),             # ← 次选：用户那份
                 os.path.dirname(base)]
        for c in cands:
            if os.path.isdir(c) and os.path.isdir(os.path.join(c, 'crops')):
                return c
        return None

    def from_exe(exe):
        if exe and os.path.isfile(exe):
            return as_data(os.path.dirname(exe))
        return None

    # 1) 显式
    r = as_data(explicit)
    if r:
        say(f"  找到 CROPWAT data: {r}")
        return r

    # 2) 环境变量
    for var in ('CROPWAT_DATA', 'CROPWAT_HOME', 'CROPWAT_DIR', 'CROPWAT_EXE'):
        v = os.environ.get(var)
        if v:
            say(f"  环境变量 {var} = {v}")
            r = as_data(v) or from_exe(v)
            if r:
                return r

    # 3) 注册表（App Paths + 卸载项）
    try:
        import winreg
        APPPATHS = r'SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\cropwat.exe'
        UNINST = r'SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall'
        for hive, flag in ((winreg.HKEY_LOCAL_MACHINE, 0),
                           (winreg.HKEY_LOCAL_MACHINE, winreg.KEY_WOW64_32KEY),
                           (winreg.HKEY_CURRENT_USER, 0)):
            try:
                with winreg.OpenKey(hive, APPPATHS, 0, winreg.KEY_READ | flag) as k:
                    r = from_exe(winreg.QueryValueEx(k, '')[0])
                    if r:
                        say(f"  注册表 App Paths → {r}")
                        return r
            except OSError:
                pass
            try:
                with winreg.OpenKey(hive, UNINST, 0, winreg.KEY_READ | flag) as k:
                    for i in range(winreg.QueryInfoKey(k)[0]):
                        try:
                            with winreg.OpenKey(k, winreg.EnumKey(k, i)) as sk:
                                try:
                                    nm = str(winreg.QueryValueEx(sk, 'DisplayName')[0])
                                except OSError:
                                    continue
                                if 'cropwat' not in nm.lower():
                                    continue
                                for vn in ('InstallLocation', 'DisplayIcon', 'UninstallString'):
                                    try:
                                        val = str(winreg.QueryValueEx(sk, vn)[0])
                                    except OSError:
                                        continue
                                    cand = val.strip('"').split('"')[0]
                                    r = as_data(cand) or from_exe(cand)
                                    if r:
                                        say(f"  注册表卸载项『{nm}』→ {r}")
                                        return r
                        except OSError:
                            continue
            except OSError:
                pass
    except Exception as e:
        say(f"  注册表查找跳过: {e}")

    # 4) 常见目录
    for d in [f'{x}:\\' for x in 'CDEFGH' if os.path.exists(f'{x}:\\')]:
        for sub in ('Program Files\\CROPWAT', 'Program Files (x86)\\CROPWAT',
                    'Program Files\\CROPWAT 8.0', 'Program Files (x86)\\CROPWAT 8.0',
                    'CROPWAT', 'CROPWAT 8.0', 'StudyProgram\\CROPWAT',
                    'Program\\CROPWAT', 'Software\\CROPWAT'):
            r = as_data(os.path.join(d, sub))
            if r:
                say(f"  常见目录 → {r}")
                return r

    # 5) 浅层扫描（找 cropwat.exe 再取旁边的 data）
    import time as _t
    t0 = _t.time()
    SKIP = {'windows', '$recycle.bin', 'system volume information', 'programdata',
            'appdata', 'node_modules', 'perflogs', 'recovery'}
    for d in [f'{x}:\\' for x in 'CDEFGH' if os.path.exists(f'{x}:\\')]:
        for root, dirs, files in os.walk(d):
            if root[len(d):].count(os.sep) >= 3:
                dirs[:] = []
            dirs[:] = [x for x in dirs if x.lower() not in SKIP and not x.startswith('$')]
            if any(f.lower() == 'cropwat.exe' for f in files):
                r = as_data(root)
                if r:
                    say(f"  磁盘扫描 → {r}")
                    return r
            if _t.time() - t0 > 20:
                break
        if _t.time() - t0 > 20:
            break

    say("  未找到 CROPWAT 的 data 目录")
    return None


def _find_fao_dir(data_dir):
    """data 目录下的 FAO 作物文件在哪"""
    for sub in (os.path.join('crops', 'FAO'), 'crops', 'FAO'):
        p = os.path.join(data_dir, sub)
        if os.path.isdir(p) and any(f.upper().endswith('.CRO') for f in os.listdir(p)):
            return p
    return None


# ============================================================ .SOI 土壤文件

SOI_HEADER = 'CROPWAT 8.0 Soil Data'

# .SOI 的字段（名称, 单位/说明）。第 0、1 行是表头和土壤名。
# 前 5 个所有作物共用；后 3 行（含一行两个数）是**水稻附加区 AdditionalRiceBox**。
SOI_FIELDS = [
    ('tam',                  'Total available soil moisture (FC - WP)', 'mm/m'),
    ('max_infiltration',     'Maximum rain infiltration rate',          'mm/day'),
    ('max_root_depth',       'Maximum rooting depth',                   'cm'),
    ('init_depletion',       'Initial soil moisture depletion (% TAM)', '%'),
    ('init_available',       'Initial available soil moisture',         'mm/m'),
    ('drainable_porosity',   '★ Drainable porosity (SAT - FC)   [水稻]', '—'),
    ('percolation',          '★ Max percolation rate after puddling [水稻]', 'mm/day'),
    ('water_type',           '★ Water type flag                    [水稻]', '—'),
    ('max_water_depth',      '★ Maximum water depth                 [水稻]', 'mm'),
]

# ★ 水稻参数的推荐固定值。
#
# 依据（两方互相印证）：
#   《灌溉排水设计规范》3.2.5：水稻田适宜日渗漏量 2～8 mm/d（粘性土取小，沙性土取大）
#   FAO 随 CROPWAT 发行的 BLACK CLAY SOIL.SOI：渗漏率 5、田面水深 120
#   节水灌溉方案实例：泡田定额 120 mm，插秧时田面水层 20 mm
#
# 5 mm/day 落在 2～8 的中位，也是 FAO 样本值 —— 作为**固定值**最稳妥。
SOI_RICE_DEFAULTS = {
    'drainable_porosity': 0.6,
    'percolation': 5,          # mm/day —— 黏土偏小可取 2~3，砂性土偏大可取 6~8
    'water_type': 1,
    'max_water_depth': 120,    # mm —— 泡田定额
}
# 不在 .SOI 里存储、但在 CROPWAT 界面里要填的水稻参数（供参考）
SOI_RICE_UI_ONLY = {
    'water_at_planting': (20, 'mm', '插秧时田面水层；节水灌溉实例取 20 mm'),
    'puddling_depth': (60, 'mm', '泡田水深'),
    'percolation_non_puddled': (10, 'mm/day', '未泡田土壤最大渗漏率；不填则按 FAO 公式推导'),
}


def _delphi_e(v, width=10):
    """Delphi/Fortran 风格的科学计数法：200 → ' 2.0E+0002'（指数 4 位）

    ⚠ Python 的 %E 给的是 2 位指数（'2.0E+02'），与 CROPWAT 的写法不同，必须自己拼。
    """
    m, e = f"{v:.1E}".split('E')
    return f"{m}E{e[0]}{int(e[1:]):04d}".rjust(width)


def read_soil(path):
    """.SOI → dict（含 8 个字段）"""
    L = _read_lines(path)
    if not L[0].startswith('CROPWAT'):
        raise ValueError(f"不像 .SOI 文件: {path}")
    d = {'name': L[1].strip(), 'path': path}
    d['tam'] = float(L[2])
    d['max_infiltration'] = int(float(L[3]))
    d['max_root_depth'] = int(float(L[4]))
    d['init_depletion'] = int(float(L[5]))
    d['init_available'] = int(float(L[6]))
    d['drainable_porosity'] = float(L[7])
    # 第 8 行塞了两个数：<值><值右对齐3位>
    line8 = L[8]
    d['percolation'] = int(float(line8[:-3]))
    d['water_type'] = int(float(line8[-3:]))
    d['max_water_depth'] = int(float(L[9]))
    return d


def write_soil(path, soil):
    """写 .SOI。soil 为 dict，缺的键用 SOI_RICE_DEFAULTS 补。"""
    s = dict(soil)
    for k, v in SOI_RICE_DEFAULTS.items():
        s.setdefault(k, v)
    lines = [
        SOI_HEADER,
        str(s['name']),
        _delphi_e(float(s['tam'])),
        f"{int(s['max_infiltration'])}",
        f"{int(s['max_root_depth'])}",
        f"{int(s['init_depletion'])}",
        f"{int(s['init_available'])}",
        f"{float(s['drainable_porosity']):5.1f}",
        f"{int(s['percolation'])}{int(s['water_type']):>3}",
        f"{int(s['max_water_depth'])}",
    ]
    _write(path, lines)


def _all_soi(soils_dir):
    """列出 soils 目录下的全部 .SOI（含子目录，如 soils\\FAO\\HEAVY.SOI）"""
    out = []
    if not os.path.isdir(soils_dir):
        return out
    for root, _dirs, files in os.walk(soils_dir):
        for f in sorted(files):
            if f.upper().endswith('.SOI'):
                out.append(os.path.join(root, f))
    return sorted(out)


def find_soil_template(data_dir, prefer='BLACK CLAY SOIL'):
    """找出适合做水稻的土壤模板。

    ⚠ FAO 只发行 7 个 .SOI，其中**只有 BLACK CLAY SOIL 把水稻附加参数填了**，
      其余（RED LOAMY / RED SANDY / RED SANDY LOAM / FAO\\HEAVY / MEDIUM / LIGHT）
      的那几个槽位全是 -99.9 / -999。做水稻必须拿填好的那个当模板。
    """
    allsoi = _all_soi(os.path.join(data_dir, 'soils'))
    for p in allsoi:
        if os.path.splitext(os.path.basename(p))[0].upper() == prefer.upper():
            return p, [os.path.basename(x) for x in allsoi]
    return None, [os.path.basename(x) for x in allsoi]





# ============================================================ 经纬度/海拔自动查询

# 两级地理编码：
#   ① OpenStreetMap Nominatim —— 中国地级行政区覆盖好（乌海/通辽/兴安盟/锡林郭勒都能查到）
#   ② Open-Meteo Geocoding  —— 兜底，且顺带返回海拔
# 海拔统一用 Open-Meteo 的海拔接口补（Nominatim 不提供）。
NOMINATIM_URL = 'https://nominatim.openstreetmap.org/search'
GEOCODE_URL = 'https://geocoding-api.open-meteo.com/v1/search'
ELEVATION_URL = 'https://api.open-meteo.com/v1/elevation'
UA = {'User-Agent': 'cropwat-inputs/1.0 (CROPWAT input generator; research use)'}


def _geo_cache_path():
    return os.path.join(os.path.expanduser('~'), '.cropwat_geocode.json')


def _load_geo_cache():
    try:
        return json.load(open(_geo_cache_path(), encoding='utf-8'))
    except Exception:
        return {}


def _save_geo_cache(c):
    try:
        with open(_geo_cache_path(), 'w', encoding='utf-8') as f:
            json.dump(c, f, ensure_ascii=False, indent=1, sort_keys=True)
    except Exception:
        pass


def _http_json(url, timeout=25):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode('utf-8'))


def _nominatim(name, province=None, timeout=25):
    """OSM Nominatim 查地名 → 候选列表 [{lat, lon, display, admin1, ...}]"""
    q = f'{name}, 中国' if province is None else f'{name}, {province}, 中国'
    url = NOMINATIM_URL + '?' + urllib.parse.urlencode(
        {'q': q, 'format': 'json', 'limit': 5, 'addressdetails': 1,
         'accept-language': 'zh-CN'})
    try:
        raw = _http_json(url, timeout)
    except Exception:
        return []
    out = []
    for x in raw:
        ad = x.get('address') or {}
        out.append({
            'lat': float(x['lat']), 'lon': float(x['lon']),
            'matched': (x.get('display_name') or '').split(',')[0].strip(),
            'display': x.get('display_name') or '',
            'admin1': ad.get('state') or ad.get('province') or '',
            'city': ad.get('city') or ad.get('county') or '',
            'type': x.get('type') or '', 'source': 'nominatim',
        })
    return out


def _openmeteo_geo(name, country='CN', timeout=20):
    """Open-Meteo 地理编码兜底 → 候选列表（带 elevation）"""
    url = GEOCODE_URL + '?' + urllib.parse.urlencode(
        {'name': name, 'count': 10, 'language': 'zh', 'format': 'json'})
    try:
        d = _http_json(url, timeout)
    except Exception:
        return []
    out = []
    for x in (d.get('results') or []):
        out.append({
            'lat': x.get('latitude'), 'lon': x.get('longitude'),
            'alt': x.get('elevation'),
            'matched': x.get('name'), 'admin1': x.get('admin1') or '',
            'cc': x.get('country_code') or '',
            'population': x.get('population') or 0, 'source': 'open-meteo',
        })
    return out


def _elevation(lat, lon, timeout=25):
    """Open-Meteo 海拔接口（支持批量）"""
    url = f'{ELEVATION_URL}?latitude={lat}&longitude={lon}'
    try:
        d = _http_json(url, timeout)
        e = d.get('elevation')
        if isinstance(e, list):
            return e[0] if e else None
        return e
    except Exception:
        return None


def _named_similar(a, b):
    """宽松名字匹配：忽略「市/盟/地区/自治州」等后缀"""
    import re as _re
    strip = lambda s: _re.sub(r'(市|盟|地区|自治州|自治县|县|区|省)$', '', str(s or '')).strip()
    return strip(a) == strip(b)


def _pick_best(cands, name, province=None):
    """挑最可能的一个。

    打分：省匹配 +50；名字相同 +40；是城市/行政中心 +25；是机场/车站等负分；
          人口开方（只用于同分时的微小加权）。
    """
    def score(c):
        s = 0.0
        a1 = str(c.get('admin1') or '')
        if province and (province in a1 or a1 in province):
            s += 50
        if _named_similar(c.get('matched'), name):
            s += 40
        t = str(c.get('type') or '').lower()
        if t in ('city', 'town', 'administrative', 'municipality', ''):
            s += 25
        if any(k in t for k in ('airport', 'aerodrome', 'station', 'bus_stop')):
            s -= 60
        if any(k in str(c.get('matched') or '') for k in ('机场', '车站', '火车站')):
            s -= 60
        s += min(10.0, ((c.get('population') or 0) ** 0.5) / 200.0)
        return s
    return max(cands, key=score) if cands else None


def _reverse_en(lat, lon, timeout=25):
    """在同一点反查，取拉丁字母地名（.PEM 只能用 latin-1，写不进中文）"""
    url = 'https://nominatim.openstreetmap.org/reverse?' + urllib.parse.urlencode(
        {'lat': lat, 'lon': lon, 'format': 'json', 'zoom': 10,
         'accept-language': 'en'})
    try:
        d = _http_json(url, timeout)
        ad = d.get('address') or {}
        for k in ('city', 'county', 'state_district', 'state', 'municipality'):
            v = ad.get(k)
            if v and _is_latin1(v):
                return str(v)
        n = d.get('name')
        if n and _is_latin1(n):
            return str(n)
    except Exception:
        pass
    return None


def _haversine(a1, o1, a2, o2):
    R = 6371.0
    p1, p2 = math.radians(a1), math.radians(a2)
    dp = p2 - p1
    dl = math.radians(o2 - o1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(h))


def _latin_name(name, lat, lon, province=None, timeout=25):
    """同一个地名再做一次英文正查，取**离 (lat,lon) 最近**那条的拉丁名。

    ⚠ 别用反查：反查落点在行政区几何中心，返回的是那个点所在的区/镇
      （实测 兴安盟→Inner_Mongolia、赤峰→Daban_Town、呼和浩特→Huimin_District），
      名字很难看。正查+就近匹配能拿到 Hohhot City / Hinggan League / Alxa League。
    """
    q = f'{name}, {province}, 中国' if province else f'{name}, 中国'
    url = NOMINATIM_URL + '?' + urllib.parse.urlencode(
        {'q': q, 'format': 'json', 'limit': 8, 'addressdetails': 1,
         'accept-language': 'en'})
    try:
        raw = _http_json(url, timeout)
    except Exception:
        return None
    # ⚠ 过滤掉河流/山峰/自然保护区等自然地物：
    #   实测 锡林郭勒 会匹到「Xilin Guole River」，必须按类型排掉。
    BAD = ('river', 'stream', 'water', 'lake', 'reservoir', 'natural',
           'peak', 'ridge', 'valley', 'desert', 'protected', 'wood',
           'forest', 'canal', 'wadi', 'spring', 'wetland', 'glacier')
    best, bd = None, 1e18
    for x in raw:
        t = f"{x.get('type') or ''} {x.get('class') or ''}".lower()
        if any(k in t for k in BAD):
            continue
        try:
            d = _haversine(lat, lon, float(x['lat']), float(x['lon']))
        except Exception:
            continue
        nm = (x.get('name') or '').strip()
        if nm and _is_latin1(nm) and d < bd:
            best, bd = nm, d
    if best and bd < 300:            # 300 km 内才算同一个地方
        return best
    return None


def _is_latin1(t):
    try:
        str(t).encode('latin-1')
        return True
    except Exception:
        return False


def _safe_name(name, lat=None, lon=None, idx=None, sleep=1.1):
    """给一个能写进 .PEM/.CRM 的名字（只允许 latin-1）。

    顺序：原名若已是 latin-1 → 用它；否则反查英文地名；
          再不行用 Site_<序号>_<纬度>_<经度>。
    返回 (ascii_name, 来源说明)
    """
    import re as _re
    n = str(name or '').strip()
    if n and _is_latin1(n):
        return _re.sub(r'[^\w\-]+', '_', n)[:40] or 'Site', '原名'
    if lat is not None and lon is not None:
        v = _latin_name(name, lat, lon)
        if sleep:
            time.sleep(sleep)
        if not v:
            v = _reverse_en(lat, lon)
            if sleep:
                time.sleep(sleep)
        if v:
            return _re.sub(r'[^\w\-]+', '_', v)[:40], '英文地名'
    tag = f'_{idx}' if idx is not None else ''
    return _re.sub(r'[^\w\-]+', '_', f'Site{tag}_{lat}_{lon}'), '坐标兜底'


def geocode_one_site(name, province=None, country='CN', sleep=1.1, verbose=False):
    """查一个地名 → dict(lat, lon, alt, matched, admin1, via)"""
    cands = _nominatim(name, province)
    via = 'nominatim'
    best = _pick_best(cands, name, province)
    if not best:
        cands2 = _openmeteo_geo(name, country)
        best = _pick_best(cands2, name, province)
        via = 'open-meteo'
    if not best:
        return {'lat': None, 'lon': None, 'alt': None, 'matched': None,
                'error': 'not-found', 'via': via}
    alt = best.get('alt')
    if alt is None:
        alt = _elevation(best['lat'], best['lon'])
    if sleep:
        time.sleep(sleep)          # Nominatim 使用政策：≤1 次/秒
    return {'lat': round(float(best['lat']), 5), 'lon': round(float(best['lon']), 5),
            'alt': alt, 'matched': best.get('matched'),
            'admin1': best.get('admin1') or best.get('city') or '',
            'via': via}


def geocode_sites(names, country='CN', province_of=None, use_cache=True,
                  refresh=False, verbose=True):
    """批量查 → {name: {lat, lon, alt, matched, admin1, via, source}}"""
    cache = _load_geo_cache() if use_cache else {}
    out = {}
    for i, n in enumerate(names, 1):
        prov = (province_of or {}).get(n)
        key = f'{country}|{prov or ""}|{n}'
        if use_cache and not refresh and key in cache:
            e = dict(cache[key]); e['source'] = 'cache'
            out[n] = e
            if verbose:
                print(f"  [{i}/{len(names)}] {n:<12} → {e['matched']} / "
                      f"{e['admin1']}  lat={e['lat']} lon={e['lon']} alt={e['alt']}"
                      f"  (缓存)")
            continue
        try:
            e = geocode_one_site(n, prov, country)
        except Exception as ex:
            e = {'lat': None, 'lon': None, 'alt': None, 'matched': None,
                 'error': f'{type(ex).__name__}: {ex}', 'via': '-'}
        if e.get('lat') is not None:
            e['source'] = 'api'
            if use_cache:
                cache[key] = e
            if verbose:
                print(f"  [{i}/{len(names)}] {n:<12} → {e['matched']} / "
                      f"{e['admin1']}  lat={e['lat']} lon={e['lon']} alt={e['alt']}"
                      f"  [{e['via']}]")
        elif verbose:
            print(f"  [{i}/{len(names)}] {n:<12} ✗ {e.get('error') or '查不到'}")
        out[n] = e
    if use_cache and not refresh:
        _save_geo_cache(cache)
    return out


def write_sites_csv(path, sites):
    """把查到的坐标写成 CSV，供人工核对/修改；build 可用 --sites 读回。"""
    with open(path, 'w', encoding='utf-8-sig', newline='') as f:
        w = csv.writer(f)
        w.writerow(['name', 'matched', 'admin1', 'lat', 'lon', 'alt', 'via', 'source'])
        for n, e in sites.items():
            w.writerow([n, e.get('matched') or '', e.get('admin1') or '',
                        e.get('lat'), e.get('lon'), e.get('alt'),
                        e.get('via') or '', e.get('source') or ''])
    return path


def read_sites_csv(path):
    """读回站点坐标表 → {name: {lat, lon, alt}}（只取有值的行）"""
    out = {}
    with open(path, encoding='utf-8-sig', newline='') as f:
        for r in csv.DictReader(f):
            n = (r.get('name') or '').strip()
            if not n:
                continue
            try:
                out[n] = {'lat': float(r['lat']), 'lon': float(r['lon']),
                          'alt': float(r['alt'])}
            except (TypeError, ValueError, KeyError):
                continue
    return out


def _cmd_geocode(args):
    """查地名 → 经纬度/海拔，写成站点坐标表供人工核对。"""
    if not args.csv:
        raise SystemExit("请用 --csv <源数据.csv> 指定（读 name 列；"
                         "有 province 列则用于消歧）")
    names, prov = [], {}
    with open(args.csv, encoding=args.encoding, newline='') as f:
        for r in csv.DictReader(f):
            n = (r.get('name') or '').strip()
            if n and n not in names:
                names.append(n)
                if r.get('province'):
                    prov[n] = r['province'].strip()
    print(f"\n从 {args.csv} 读到 {len(names)} 个地名")
    print(f"  数据源: OpenStreetMap Nominatim（主）+ Open-Meteo（兜底与海拔）")
    print(f"  缓存:   {_geo_cache_path()}\n")
    sites = geocode_sites(names, country=args.country, province_of=prov,
                          use_cache=not args.no_cache, refresh=args.refresh)
    bad = [n for n, e in sites.items() if not e.get('lat')]
    out = os.path.abspath(args.out) if args.out else \
        os.path.join(os.path.dirname(os.path.abspath(args.csv)), '站点坐标.csv')
    write_sites_csv(out, sites)
    print(f"\n站点坐标表: {out}")
    print(f"   共 {len(sites)} 个地名，成功 {len(sites) - len(bad)}，失败 {len(bad)}")
    if bad:
        print(f"   ⚠ 没查到的: {', '.join(bad)}")
        print("      → 打开上面那个 CSV 手工填 lat/lon/alt，再用 build --sites 读回")
    print("\n   ⚠ 一定要过一眼：查回来的是行政中心点，不是你那个气象站的点。")
    print("     重名也很常见。改完坐标后：")
    print("       python cropwat_inputs.py build --csv ... --sites 站点坐标.csv")
    return 0


# ============================================================ 数据明细备查表

DETAIL_NAME = 'CROPWAT输入数据明细'


def default_detail_path():
    """桌面\\CROPWAT输入数据明细.xlsx —— 生成输入文件时用了哪些数，一目了然"""
    return os.path.join(desktop_dir(), DETAIL_NAME + '.xlsx')


def build_detail(groups, out_path, workdir=None):
    """生成"数据明细备查表"。

    groups: {(name, year): {'meta': csv行, 'months': {月: csv行}}}
    工作表：
      逐月明细(全部要素)   —— 一行一个 地方×年×月，含五个输入列 + Rad + ETo + 降水
      年度汇总(地方×年)     —— 年均气温/湿度/风速、年日照、年ETo、年降水、生长季降水
      降水量矩阵(地方×年月)
      气温矩阵Tmax(地方×年月)
      气温矩阵Tmin(地方×年月)
      站点与文件           —— 坐标、海拔、文件路径模板
    """
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Font, Alignment, PatternFill, Border, Side
        from openpyxl.utils import get_column_letter
    except ImportError:
        raise SystemExit("生成数据明细表需要 openpyxl：pip install openpyxl")

    def num(x, d=None):
        try:
            return float(x)
        except (TypeError, ValueError):
            return d

    # ---- 整理 ----
    sites, years = [], []
    for (n, y) in groups:
        if n not in sites:
            sites.append(n)
        if y not in years:
            years.append(y)
    sites.sort()
    years.sort()

    # 显示名：CSV 里有 cn 列就用它，否则用 name
    label = {}
    meta = {}
    for (n, y), g in groups.items():
        label[n] = (g['meta'].get('cn') or n)
        meta[n] = g['meta']

    monthly = []          # (site, year, month, dict)
    for n in sites:
        for y in years:
            g = groups.get((n, y))
            if not g:
                continue
            for m in range(1, 13):
                r = g['months'].get(m)
                if not r:
                    continue
                lat = float(r['lat']); alt = float(r['alt'])
                sun = num(r['sun_h'])
                if str(r.get('sun_is_monthly', '')).lower() in ('1', 'true', 'yes'):
                    sun /= days_in_month(y, m)
                rs, eto = eto_pm(num(r['tmax']), num(r['tmin']), num(r['rh']),
                                 num(r['wind_kmd']) / 86.4, sun,
                                 mid_month_doy(y, m), lat, alt)
                monthly.append((n, y, m, {
                    'tmin': num(r['tmin']), 'tmax': num(r['tmax']),
                    'rh': num(r['rh']), 'wind': num(r['wind_kmd']),
                    'sun': sun, 'rad': rs, 'eto': eto,
                    'rain': num(r.get('rain_mm')),
                }))

    wb = Workbook()
    HDR = Font(bold=True, color='FFFFFF', size=10)
    FILL = PatternFill('solid', fgColor='2F5597')
    THIN = Side(style='thin', color='D0D0D0')
    BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
    CEN = Alignment(horizontal='center', vertical='center')

    def style_header(ws, ncol):
        for c in ws[1][:ncol]:
            c.font = HDR
            c.fill = FILL
            c.alignment = CEN
            c.border = BORDER
        ws.freeze_panes = 'A2'

    # ---- ① 逐月明细 ----
    ws = wb.active
    ws.title = '逐月明细(全部要素)'
    ws.append(['地方', '年', '月', '最低气温(℃)', '最高气温(℃)', '相对湿度(%)',
               '风速(km/天)', '日照(小时/天)', '太阳辐射(MJ/m²/d)',
               '参考蒸散ETo(mm/天)', '降水量(mm)'])
    for n, y, m, v in monthly:
        ws.append([label[n], y, m, v['tmin'], v['tmax'], v['rh'],
                   v['wind'], round(v['sun'], 1), round(v['rad'], 1),
                   round(v['eto'], 2),
                   None if v['rain'] is None else round(v['rain'], 1)])
    style_header(ws, 11)
    for i, w in enumerate([12, 7, 5, 13, 13, 13, 14, 13, 18, 20, 12], 1):
        ws.column_dimensions[get_column_letter(i)].width = w

    # ---- ② 年度汇总 ----
    ws2 = wb.create_sheet('年度汇总(地方×年)')
    ws2.append(['地方', '年', '年均最低气温(℃)', '年均最高气温(℃)', '年均相对湿度(%)',
                '年均风速(km/天)', '年日照时数(小时)', '年参考蒸散ETo(mm)',
                '年降水量(mm)', '生长季(4-9月)降水(mm)'])
    for n in sites:
        for y in years:
            vs = [v for (a, b, m, v) in monthly if a == n and b == y]
            if not vs:
                continue
            avg = lambda k: round(sum(v[k] for v in vs) / len(vs), 2)
            suntime = round(sum(v['sun'] * days_in_month(y, m)
                                for (a, b, m, v) in monthly if a == n and b == y), 0)
            etoyr = round(sum(v['eto'] * days_in_month(y, m)
                              for (a, b, m, v) in monthly if a == n and b == y), 0)
            rains = [v['rain'] for v in vs if v['rain'] is not None]
            grow = [v['rain'] for (a, b, m, v) in monthly
                    if a == n and b == y and 4 <= m <= 9 and v['rain'] is not None]
            ws2.append([label[n], y, avg('tmin'), avg('tmax'), avg('rh'), avg('wind'),
                        suntime, etoyr,
                        round(sum(rains), 1) if rains else None,
                        round(sum(grow), 1) if grow else None])
    style_header(ws2, 10)
    for i, w in enumerate([12, 7, 16, 16, 16, 16, 17, 19, 13, 22], 1):
        ws2.column_dimensions[get_column_letter(i)].width = w

    # ---- ③④⑤ 三个矩阵 ----
    cols = [f'{y}-{m:02d}' for y in years for m in range(1, 13)]
    idx = {}
    for n, y, m, v in monthly:
        idx[(n, y, m)] = v
    for title, key, fmt in (('降水量矩阵(地方×年月)', 'rain', '%.1f'),
                            ('气温矩阵Tmax(地方×年月)', 'tmax', '%.1f'),
                            ('气温矩阵Tmin(地方×年月)', 'tmin', '%.1f')):
        w = wb.create_sheet(title)
        w.append(['地方'] + cols)
        for n in sites:
            row = [label[n]]
            for y in years:
                for m in range(1, 13):
                    v = idx.get((n, y, m), {}).get(key)
                    row.append(None if v is None else float(fmt % v))
            w.append(row)
        style_header(w, len(cols) + 1)
        w.column_dimensions['A'].width = 12
        for i in range(2, len(cols) + 2):
            w.column_dimensions[get_column_letter(i)].width = 9

    # ---- ⑥ 站点与文件 ----
    ws6 = wb.create_sheet('站点与文件')
    ws6.append(['地方', '名称(文件名前缀)', '纬度', '经度', '海拔(m)',
                '气候文件路径', '降水文件路径'])
    sub = 'Climate' if workdir else 'Climate'
    for n in sites:
        m = meta[n]
        ws6.append([label[n], n, num(m['lat']), num(m['lon']), num(m['alt']),
                    f'Climate\\{n}\\{n}_<年>.PEM',
                    f'Rain\\{n}\\{n}_<年>.CRM'])
    style_header(ws6, 7)
    for i, w in enumerate([12, 20, 9, 9, 10, 34, 34], 1):
        ws6.column_dimensions[get_column_letter(i)].width = w

    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    wb.save(out_path)
    return out_path, len(monthly), len(sites), len(years)


# ============================================================ 命令行

def _cmd_inspect(args):
    for p in args.files:
        print("=" * 70)
        print(f"文件: {p}   ({os.path.getsize(p)} 字节)")
        raw = open(p, 'rb').read()
        eol = 'CRLF' if b'\r\n' in raw else 'LF'
        print(f"  编码测试: latin-1 OK   行尾: {eol}")
        if p.upper().endswith('.PEM'):
            d = read_pem(p)
            print(f"  名称={d['name']}  海拔={d['alt']}  纬度={d['lat']}  经度={d['lon']}")
            print(f"  {'月':<4}{'Tmin':>7}{'Tmax':>7}{'RH':>7}"
                  f"{'风速':>8}{'日照':>7}{'Rad':>7}{'ETo':>8}")
            for i, m in enumerate(d['months']):
                print(f"  {MONTH_NAMES[i]:<4}{m['tmin']:>7.1f}{m['tmax']:>7.1f}"
                      f"{m['rh']:>7.1f}{m['wind_kmd']:>8.1f}{m['sun_h']:>7.1f}"
                      f"{m['rad']:>7.1f}{m['eto']:>8.2f}")
            ok, we, wr, _ = check_pem(p)
            ann = sum(m['eto'] * days_in_month(2001, i + 1)
                      for i, m in enumerate(d['months']))
            print(f"  年 ETo（Σ 月值×天数）= {ann:.0f} mm")
            print(f"  自洽校验: {'✓ 通过' if ok else '✗ 偏差偏大'}"
                  f"  （最大 ETo 差 {we:.3f}，Rad 差 {wr:.3f}）")
        elif p.upper().endswith('.CRM'):
            d = read_crm(p)
            r = d['rain']
            print(f"  名称={d['name']}")
            for i in range(0, 12, 4):
                print("  " + "  ".join(
                    f"{MONTH_NAMES[j]}={('缺测' if r[j] is None else f'{r[j]:.1f}')}"
                    for j in range(i, min(i + 4, 12))))
            print(f"  年降水量 = {sum(x for x in r if x is not None):.1f} mm")
        print()


def _cmd_crops(args):
    """从 CROPWAT 安装目录复制 FAO 作物文件（和土壤文件）到工作目录。

    ⚠ 只从**安装根目录**取（程序自带的原始文件），不取 data\\ 下用户那份。
    """
    data = find_cropwat_data(args.cropwat_data, verbose=True)
    if not data:
        raise SystemExit(
            "未能自动找到 CROPWAT 的安装目录。\n"
            "  请用 --cropwat-data <路径> 指定（CROPWAT 安装目录），\n"
            "  或设环境变量 CROPWAT_DATA / CROPWAT_HOME。")
    fao = _find_fao_dir(data)
    if not fao:
        raise SystemExit(f"在 {data} 下找不到 FAO 作物文件目录")

    src = sorted(f for f in os.listdir(fao) if f.upper().endswith('.CRO'))

    # ---- 只列出 ----
    if args.list:
        print(f"\nFAO 作物文件目录: {fao}")
        print(f"共 {len(src)} 个：\n")
        for i in range(0, len(src), 4):
            print("   " + "".join(f"{x:<20}" for x in src[i:i + 4]))
        soils = os.path.join(data, 'soils')
        if os.path.isdir(soils):
            print(f"\n土壤文件（含子目录）:")
            for p in _all_soi(soils):
                rel = os.path.relpath(p, soils)
                print(f"   {rel}")
        return 0

    # ---- 决定放哪 ----
    if not args.workdir and not args.out:
        args.workdir = default_workdir()
        print(f"\n[默认] 未指定 --workdir，输出到桌面: {args.workdir}")
    root = os.path.abspath(args.workdir) if args.workdir else None
    if root:
        cout = os.path.join(root, 'Crop')       # 作物 → <工作目录>\Crop\
        sout = args.soil_out or os.path.join(root, 'Soil')   # 土壤 → <工作目录>\Soil\
    else:
        cout = os.path.abspath(args.out)
        sout = args.soil_out or os.path.dirname(cout)

    # ---- 选哪些作物 ----
    if args.names:
        want = [n.upper() for n in args.names]
        picked, missing = [], []
        for w in want:
            hit = next((f for f in src if os.path.splitext(f)[0].upper() == w), None)
            if hit:
                picked.append(hit)
            else:
                loose = [f for f in src if os.path.splitext(f)[0].upper().startswith(w)]
                if len(loose) == 1:
                    picked.append(loose[0])
                else:
                    missing.append(w)
        if missing:
            print(f"⚠ 这些名字在 FAO 目录里没有唯一匹配，已跳过: {', '.join(missing)}")
            print(f"   可用的: {', '.join(os.path.splitext(f)[0] for f in src)}")
    else:
        picked = src

    # ---- 复制作物 ----
    os.makedirs(cout, exist_ok=True)
    n = 0
    for f in picked:
        d = os.path.join(cout, f)
        if os.path.exists(d) and not args.force:
            continue
        with open(os.path.join(fao, f), 'rb') as fi, open(d, 'wb') as fo:
            fo.write(fi.read())           # 逐字节复制，不做任何改写
        n += 1
    print(f"\n复制 {n} 个 .CRO → {cout}")
    for f in picked[:60]:
        print(f"   {f}")

    # ---- 复制土壤 ----
    if args.soils:
        os.makedirs(sout, exist_ok=True)
        m = 0
        for sp in _all_soi(os.path.join(data, 'soils')):
            f = os.path.basename(sp)
            if args.soil_names and f.upper() not in [x.upper() for x in args.soil_names]:
                continue
            d = os.path.join(sout, f)
            if os.path.exists(d) and not args.force:
                continue
            with open(sp, 'rb') as fi, open(d, 'wb') as fo:
                fo.write(fi.read())
            m += 1
            print(f"   {f}")
        print(f"复制 {m} 个 .SOI → {sout}")
        print("   ⚠ 这 7 个土壤的水稻两项参数（渗漏率 / 最大田面水深）全是 -999 缺测。")
        print("     做水稻请再跑一次:")
        print(f'       python cropwat_inputs.py soil --workdir "{root or os.path.dirname(sout)}" '
              f'--template BLACK CLAY SOIL')

    if root:
        print(f"\n工作目录已就绪: {root}")
        print(f"   {root}\\Crop\\   {len(picked)} 个作物文件")
        if args.soils:
            print(f"   {root}\\Soil\\   {m} 个土壤文件")
    return 0


def _cmd_soil(args):
    """生成水稻用的 .SOI：以 FAO 自带土壤为模板，水稻两项参数填固定值。

    ⚠ 只从**安装根目录**取模板（程序自带的原始文件）。
    输出默认命名 Recommed-<模板名>.SOI，放进 <工作目录>\\Soil\\。
    """
    if args.show_defaults:
        print("\n★ 水稻土壤参数的推荐固定值（写进 .SOI 的）\n")
        for k, v in SOI_RICE_DEFAULTS.items():
            f = next((x for x in SOI_FIELDS if x[0] == k), None)
            print(f"   {k:<20} = {v:<8} {('  ' + f[1]) if f else ''}")
        print("\n   依据（两方互相印证）：")
        print("     《灌溉排水设计规范》3.2.5：水稻田适宜日渗漏量 2～8 mm/d")
        print("       （粘性土取较小值，沙性土取较大值）")
        print("     FAO 随 CROPWAT 发行的 BLACK CLAY SOIL.SOI：渗漏率 5、田面水深 120")
        print("     节水灌溉实例：泡田定额 120 mm，插秧时田面水层 20 mm")
        print("\n   按土壤调整渗漏率：重黏土(有犁底层) 2~3 | 黏壤土 5 | 砂性土 6~8")
        print("   充分泡田、基本不透水的田块可以填 0。")
        print("\n   不在 .SOI 里、但界面要填的：")
        for k, (v, u, note) in SOI_RICE_UI_ONLY.items():
            print(f"   {k:<24} ≈ {v} {u:<7} {note}")
        print()
        return 0

    data = find_cropwat_data(args.cropwat_data, verbose=True)
    if not data:
        raise SystemExit("未能自动找到 CROPWAT 安装目录；请用 --cropwat-data 指定。")

    tpl, allsoi = find_soil_template(data, args.template)
    if not tpl:
        raise SystemExit(f"在 {data}\\soils 下找不到模板 {args.template}.SOI\n"
                         f"  可用: {', '.join(allsoi)}")

    base = read_soil(tpl)
    print(f"\n模板: {tpl}")
    print(f"  原值: 渗漏率={base['percolation']}  水型={base['water_type']}  "
          f"可排水孔隙度={base['drainable_porosity']}  最大田面水深={base['max_water_depth']}")

    if args.name:
        base['name'] = args.name

    # ⚠ 只填**缺测**的字段，模板里本来就有效的值不动
    #    （例：MEDIUM 的孔隙度 0.4 是 FAO 真实值，不该被默认 0.6 覆盖）
    MISSING_INT, MISSING_FLT = -999, -99.9
    explicit = {'drainable_porosity': args.drainable_porosity,
                'percolation': args.percolation,
                'max_water_depth': args.max_water_depth,
                'water_type': args.water_type}
    filled = []
    for k, v in explicit.items():
        cur = base.get(k)
        is_missing = (cur == MISSING_INT) if isinstance(cur, int) else (
            abs(float(cur) - MISSING_FLT) < 0.05 if cur is not None else True)
        if v is not None:                      # 显式指定 → 总是覆盖
            base[k] = v
            filled.append(f'{k}={v}(指定)')
        elif is_missing:                       # 缺测 → 用推荐值补上
            base[k] = SOI_RICE_DEFAULTS[k]
            filled.append(f"{k}={SOI_RICE_DEFAULTS[k]}(补缺)")
        else:
            filled.append(f"{k}={cur}(沿用模板)")
    for f in filled:
        print(f"   {f}")

    # ---- 决定输出路径 ----
    #   默认文件名：Recommed-<模板名>.SOI
    #   未指定时放到桌面 CROPWRT工作文件\Soil\
    if not args.workdir and not args.out:
        args.workdir = default_workdir()
        print(f"\n[默认] 未指定 --workdir，输出到桌面: {args.workdir}")
    default_name = f"Recommed-{os.path.splitext(os.path.basename(tpl))[0]}.SOI"
    if args.workdir:
        out = args.out or os.path.join(os.path.abspath(args.workdir), 'Soil', default_name)
    else:
        out = args.out or default_name
    if not out.upper().endswith('.SOI'):
        out += '.SOI'

    write_soil(out, base)
    chk = read_soil(out)
    print(f"  新值: 渗漏率={chk['percolation']}  水型={chk['water_type']}  "
          f"可排水孔隙度={chk['drainable_porosity']}  最大田面水深={chk['max_water_depth']}")
    print(f"\n已生成: {os.path.abspath(out)}")
    print("   （其余字段原样保留模板值；latin-1 + CRLF 写出）")
    return 0


def _cmd_eto(args):
    """单点试算：给五列，输出 Rad/ETo"""
    rs, e = eto_pm(args.tmax, args.tmin, args.rh, args.wind, args.sun,
                   args.doy, args.lat, args.alt)
    print(f"  Ra     = {ra(args.doy, args.lat):8.2f} MJ/m2/d")
    print(f"  N      = {daylight(args.doy, args.lat):8.2f} h")
    print(f"  Rs     = {rs:8.2f} MJ/m2/d")
    print(f"  ETo    = {e:8.2f} mm/day")


def _cmd_build(args):
    """从 CSV 批量生成。

    CSV 列：name,lat,lon,alt,year,month,tmin,tmax,rh,wind_kmd,sun_h[,rain_mm]

    两种输出：
      --workdir DIR   分门别类（推荐）→ DIR\\Climate\\<地点>\\<地点>_<年>.PEM
                                        DIR\\Rain\\<地点>\\<地点>_<年>.CRM
      --out DIR       全部平铺到一个目录
    """
    import collections
    if not args.workdir and not args.out:
        args.workdir = default_workdir()
        print(f"\n[默认] 未指定 --workdir，输出到桌面: {args.workdir}")

    groups = collections.OrderedDict()
    with open(args.csv, encoding=args.encoding, newline='') as f:
        for r in csv.DictReader(f):
            if not r.get('name'):
                continue
            key = (r['name'].strip(), int(r.get('year') or 2001))
            groups.setdefault(key, {'meta': r, 'months': {}})
            groups[key]['months'][int(r['month'])] = r

    sites = sorted({k[0] for k in groups})
    years = sorted({k[1] for k in groups})
    total_rows = sum(len(g['months']) for g in groups.values())
    print(f"\n源数据: {args.csv}")
    print(f"  {total_rows} 行 = {len(sites)} 个地点 × {len(years)} 个年份"
          f"（{len(groups)} 组 地点×年）")

    # ---- 经纬度/海拔：缺了就查（公开信息，不用自己填）----
    def _has_coords(r):
        try:
            return (r.get('lat') not in (None, '', 'NA')
                    and r.get('lon') not in (None, '', 'NA')
                    and r.get('alt') not in (None, '', 'NA'))
        except Exception:
            return False

    missing = [n for n in sites if not _has_coords(groups[(n, years[0])]['meta'])]
    if missing:
        coord = {}
        if args.sites:                       # 优先用人工核对过的站点表
            coord.update(read_sites_csv(args.sites))
            print(f"\n站点坐标表: {args.sites}  （{len(coord)} 个站点）")
        if args.geocode:
            need = [n for n in missing if n not in coord]
            if need:
                print(f"\n有 {len(need)} 个地点缺经纬度/海拔，联网查询中"
                      f"（缓存 {_geo_cache_path()}）…")
                prov = {}
                for n in need:
                    p = groups[(n, years[0])]['meta'].get('province')
                    if p:
                        prov[n] = str(p).strip()
                q = geocode_sites(need, country=args.country_of,
                                  province_of=prov, use_cache=not args.no_cache,
                                  refresh=args.refresh_geo, verbose=True)
                bad = [n for n, e in q.items() if e.get('lat') is None]
                coord.update({n: e for n, e in q.items() if e.get('lat') is not None})
                if bad:
                    print(f"   ⚠ 没查到: {', '.join(bad)}")
                    print("     请手工补进站点坐标表，再用 --sites 读回")
        still = [n for n in missing if n not in coord]
        if still:
            raise SystemExit(
                f"这些地点缺少经纬度/海拔且未能自动补上: {', '.join(still)}\n"
                "  三种办法：\n"
                "    1) 加 --geocode 让程序联网查（公开信息，无需手工填）\n"
                "    2) 先跑 geocode 子命令生成站点坐标表，核对后用 --sites 读回\n"
                "    3) 直接在源 CSV 里补 lat / lon / alt 三列")
        for (n, y), g in groups.items():
            if n in coord:
                g['meta'].update({k: str(v) for k, v in coord[n].items()
                                  if v is not None})
        if args.geocode:
            sp = os.path.join(os.path.dirname(os.path.abspath(args.csv)),
                              '站点坐标.csv')
            try:
                write_sites_csv(sp, {n: coord[n] for n in sorted(coord)})
                print(f"\n站点坐标表已保存: {sp}（请过一眼，重名很常见）")
            except Exception:
                pass

    if years:
        print(f"  年份: {years[0]}–{years[-1]}")

    # ---- 站名必须是 latin-1（.PEM/.CRM 的硬约束，写不进中文）----
    outof = {}
    renamed = []
    for i, nm in enumerate(sites, 1):
        if _is_latin1(nm):
            outof[nm] = nm
        else:
            meta0 = groups[(nm, years[0])]['meta'] if years else {}
            lat = meta0.get('lat'); lon = meta0.get('lon')
            try:
                lat = float(lat); lon = float(lon)
            except (TypeError, ValueError):
                lat = lon = None
            safe, how = _safe_name(nm, lat, lon, i)
            outof[nm] = safe
            renamed.append((nm, safe, how))
    if renamed:
        print(f"\n有 {len(renamed)} 个站名含非 latin-1 字符，已换成 ASCII 名"
              f"（CROPWAT 的 .PEM/.CRM 只能用 latin-1）：")
        for nm, safe, how in renamed:
            print(f"   {nm:<12} → {safe:<22} [{how}]")
        print("   ⚠ 想用自己的英文名，在源 CSV 里加 name_en 列指定")

    root = os.path.abspath(args.workdir) if args.workdir else None
    if not root:
        os.makedirs(args.out, exist_ok=True)

    n_pem = n_crm = 0
    for (name, year), g in groups.items():
        m0 = g['meta']
        lat, lon, alt = float(m0['lat']), float(m0['lon']), float(m0['alt'])
        months, rain = [], []
        for m in range(1, 13):
            r = g['months'].get(m)
            if not r:
                raise SystemExit(f"{name} {year} 缺 {m} 月数据")
            sun = float(r['sun_h'])
            if args.sun_is_monthly:            # ⚠ 小时/月 → 小时/天
                sun /= days_in_month(year, m)
            months.append({
                'tmin': float(r['tmin']), 'tmax': float(r['tmax']),
                'rh': float(r['rh']), 'wind_kmd': float(r['wind_kmd']),
                'sun_h': sun, 'doy': mid_month_doy(year, m),
            })
            rv = r.get('rain_mm')
            rain.append(None if rv in (None, '', 'NA') else float(rv))

        on = outof.get(name, name)
        cd = os.path.join(root, 'Climate', on) if root else args.out
        rd = os.path.join(root, 'Rain', on) if root else args.out

        if args.pem or not (args.pem or args.crm):
            write_pem(os.path.join(cd, f'{on}_{year}.PEM'),
                      f'{on}_{year}', lat, lon, alt, months)
            n_pem += 1
        if args.crm and any(x is not None for x in rain):
            write_crm(os.path.join(rd, f'{on}_{year}.CRM'),
                      f'{on}_{year}', rain)
            n_crm += 1

    where = root or os.path.abspath(args.out)
    print(f"\n生成 {n_pem} 个 .PEM + {n_crm} 个 .CRM → {where}")
    if root:
        print("\n目录结构（每个地点一个子文件夹，年份都放在里面）:")
        print(f"  {where}\\Climate\\<地点>\\<地点>_<年份>.PEM")
        print(f"  {where}\\Rain\\<地点>\\<地点>_<年份>.CRM")
        for s in sites[:8]:
            k = len([x for x in groups if x[0] == s])
            print(f"    Climate\\{outof.get(s, s)}\\   {k} 个年份")
        if len(sites) > 8:
            print(f"    ... 其余 {len(sites) - 8} 个地点")
        print("\n下一步（补作物与土壤，同样按目录分类）:")
        print("  python cropwat_inputs.py crops --names WHEAT MAIZE RICE --soils")
        print("  python cropwat_inputs.py soil  --template MEDIUM")
        print("\n然后交给 cropwat 技能:")
        print(f"  python cropwat_run.py check --data \"{where}\"")

    # ---- 数据明细备查表 ----
    if not args.no_detail:
        dp = os.path.abspath(args.detail) if args.detail else default_detail_path()
        try:
            _p, rows, ns, ny = build_detail(groups, dp, root)
            print(f"\n数据明细备查表: {dp}")
            print(f"   {rows} 条逐月记录 = {ns} 个地点 × {ny} 个年份 × 12 月")
            print("   6 个工作表：逐月明细 / 年度汇总 / 降水量矩阵 / "
                  "气温矩阵Tmax / 气温矩阵Tmin / 站点与文件")
        except SystemExit as e:
            print(f"⚠ {e}")
        except Exception as e:
            print(f"⚠ 生成数据明细表失败: {type(e).__name__}: {e}")
    return 0


def _cmd_detail(args):
    """只生成数据明细备查表（不写 .PEM/.CRM）"""
    import collections
    if not args.csv:
        raise SystemExit("请用 --csv <源数据.csv> 指定气象数据")
    groups = collections.OrderedDict()
    with open(args.csv, encoding=args.encoding, newline='') as f:
        for r in csv.DictReader(f):
            if not r.get('name'):
                continue
            key = (r['name'].strip(), int(r.get('year') or 2001))
            groups.setdefault(key, {'meta': r, 'months': {}})
            groups[key]['months'][int(r['month'])] = r
    out = os.path.abspath(args.out) if args.out else default_detail_path()
    print(f"\n源数据: {args.csv}")
    p, rows, ns, ny = build_detail(groups, out)
    print(f"\n数据明细备查表: {p}")
    print(f"   {rows} 条逐月记录 = {ns} 个地点 × {ny} 个年份 × 12 月")
    print("   6 个工作表：逐月明细 / 年度汇总 / 降水量矩阵 / "
          "气温矩阵Tmax / 气温矩阵Tmin / 站点与文件")
    return 0


def main():
    import argparse
    p = argparse.ArgumentParser(
        prog='cropwat_inputs.py',
        description='生成与校验 CROPWAT 8.0 输入文件（.PEM 气候 / .CRM 降水）',
        formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    sub = p.add_subparsers(dest='cmd')

    pi = sub.add_parser('inspect', help='读回并体检已有文件')
    pi.add_argument('files', nargs='+')
    pi.set_defaults(func=_cmd_inspect)

    pe = sub.add_parser('eto', help='单点试算 FAO-56 ETo')
    pe.add_argument('--tmax', type=float, required=True)
    pe.add_argument('--tmin', type=float, required=True)
    pe.add_argument('--rh', type=float, required=True)
    pe.add_argument('--wind', type=float, required=True,
                    help='2m 风速 **m/s**（⚠ FAO-56 公式的单位；文件里是 km/day，= m/s × 86.4）')
    pe.add_argument('--sun', type=float, required=True, help='日照 小时/天')
    pe.add_argument('--doy', type=int, required=True, help='年内日序')
    pe.add_argument('--lat', type=float, required=True)
    pe.add_argument('--alt', type=float, required=True)
    pe.set_defaults(func=_cmd_eto)

    pg = sub.add_parser('geocode', help='查地名 → 经纬度/海拔（Open-Meteo，免费）',
                        formatter_class=argparse.RawDescriptionHelpFormatter,
                        description='从源 CSV 的 name 列取出地名，联网查经纬度与海拔，\n'
                                    '写成站点坐标表供人工核对。结果会缓存，重跑不再联网。\n'
                                    '⚠ 重名很常见（包头在内蒙古也在海南），务必过一眼。')
    pg.add_argument('--csv', required=True, help='源数据 CSV（读 name 列；有 province 列则用于消歧）')
    pg.add_argument('--out', metavar='CSV', help='输出站点坐标表（默认与 --csv 同目录的 站点坐标.csv）')
    pg.add_argument('--country', default='CN', help='国别代码（默认 CN）')
    pg.add_argument('--language', default='zh', help='返回语言（默认 zh）')
    pg.add_argument('--encoding', default='utf-8-sig')
    pg.add_argument('--no-cache', action='store_true')
    pg.add_argument('--refresh', action='store_true', help='忽略缓存重新查')
    pg.set_defaults(func=_cmd_geocode)

    pd_ = sub.add_parser('detail', help='生成数据明细备查表（不写 .PEM/.CRM）',
                         formatter_class=argparse.RawDescriptionHelpFormatter,
                         description='把生成输入文件用到的每个数都落成一张 Excel，便于备查。\n'
                                     '6 个工作表：逐月明细 / 年度汇总 / 降水量矩阵 /\n'
                                     '气温矩阵Tmax / 气温矩阵Tmin / 站点与文件。')
    pd_.add_argument('--csv', required=True, help='源数据 CSV（列同 build）')
    pd_.add_argument('--out', metavar='XLSX',
                     help=f'输出路径（默认 {default_detail_path()}，自动放桌面）')
    pd_.add_argument('--encoding', default='utf-8-sig')
    pd_.set_defaults(func=_cmd_detail)

    pb = sub.add_parser('build', help='从 CSV 批量生成 .PEM/.CRM（按目录分类）')
    pb.add_argument('--csv', required=True)
    pb.add_argument('--workdir', metavar='DIR',
                    help=f'★ 工作目录（**默认 {default_workdir()}**）：'
                         '按 Climate\\<地点>\\ 与 Rain\\<地点>\\ 分类输出')
    pb.add_argument('--out', metavar='DIR', help='全部平铺到一个目录（旧行为）')
    pb.add_argument('--encoding', default='utf-8-sig')
    pb.add_argument('--pem', action='store_true', help='生成 .PEM')
    pb.add_argument('--crm', action='store_true', help='生成 .CRM')
    pb.add_argument('--sun-is-monthly', action='store_true',
                    help='⚠ CSV 里的日照是「小时/月」，自动换算成「小时/天」')
    pb.add_argument('--detail', metavar='XLSX',
                    help=f'数据明细备查表路径（默认 {default_detail_path()}，自动放桌面）')
    pb.add_argument('--no-detail', action='store_true', help='不生成数据明细备查表')
    pb.add_argument('--geocode', action='store_true',
                    help='★ 经纬度/海拔缺失时自动联网查询（公开信息，Open-Meteo，无需密钥）')
    pb.add_argument('--sites', metavar='CSV',
                    help='站点坐标表（geocode 子命令生成，可人工核对修改）')
    pb.add_argument('--country-of', default='CN', help='国别代码，用于消歧（默认 CN）')
    pb.add_argument('--no-cache', action='store_true', help='不使用地理编码缓存')
    pb.add_argument('--refresh-geo', action='store_true', help='忽略缓存，重新联网查询')
    pb.set_defaults(func=_cmd_build)

    pc = sub.add_parser('crops', help='从 CROPWAT 安装目录复制 FAO 作物/土壤文件')
    pc.add_argument('--workdir', metavar='DIR',
                    help=f'★ 工作目录（**默认 {default_workdir()}**）：'
                         '作物 → DIR\\Crop\\，土壤 → DIR\\Soil')
    pc.add_argument('--out', metavar='DIR', help='不用 --workdir 时，作物复制到哪里')
    pc.add_argument('--names', nargs='+', metavar='CROP',
                    help='只要这几种，如 WHEAT MAIZE RICE（默认全部 38 个）')
    pc.add_argument('--list', action='store_true', help='只列出安装目录里有哪些文件')
    pc.add_argument('--force', action='store_true', help='覆盖已存在的文件')
    pc.add_argument('--soils', action='store_true', help='同时复制 .SOI 土壤文件')
    pc.add_argument('--soil-out', metavar='DIR', help='土壤放哪（默认 <workdir>\\Soil 或与 --out 同级）')
    pc.add_argument('--soil-names', nargs='+', metavar='SOI', help='只要这几个土壤文件')
    pc.add_argument('--cropwat-data', metavar='DIR',
                    help='CROPWAT 安装目录（默认自动查找；始终取安装根，不取 data\\）')
    pc.set_defaults(func=_cmd_crops)

    ps = sub.add_parser('soil', help='生成水稻用的 .SOI（水稻参数填固定值）',
                        formatter_class=argparse.RawDescriptionHelpFormatter,
                        description='默认输出 Recommed-<模板名>.SOI，放进 <工作目录>\\Soil\\。\n'
                                    '⚠ FAO 只发行 7 个 .SOI，只有 BLACK CLAY SOIL 把水稻\n'
                                    '  附加参数填了，其余 6 个的对应槽位全是 -99.9 / -999。')
    ps.add_argument('--workdir', metavar='DIR',
                    help=f'★ 工作目录（**默认 {default_workdir()}**）：'
                         '输出到 DIR\\Soil\\Recommed-<模板名>.SOI')
    ps.add_argument('--out', metavar='FILE.SOI',
                    help='直接指定输出文件（不给就用 Recommed-<模板名>.SOI）')
    ps.add_argument('--name', help='土壤名（写进第 2 行），如 "HETAO PADDY"')
    ps.add_argument('--template', default='BLACK CLAY SOIL',
                    help='用哪个自带土壤当模板（默认 BLACK CLAY SOIL；'
                         '也可 MEDIUM / HEAVY / LIGHT / RED LOAMY 等）')
    ps.add_argument('--percolation', type=int, metavar='MMDAY',
                    help='泡田后最大渗漏率 mm/day。规范 2～8；黏土偏小、砂性土偏大'
                         '（默认 5；不透水田块可填 0）')
    ps.add_argument('--max-water-depth', type=int, metavar='MM',
                    help='最大田面水深 mm（泡田定额，默认 120）')
    ps.add_argument('--drainable-porosity', type=float, metavar='V',
                    help='可排水孔隙度 SAT-FC（默认 0.6）')
    ps.add_argument('--water-type', type=int, metavar='N', help='水型标志（默认 1）')
    ps.add_argument('--show-defaults', action='store_true',
                    help='只打印推荐值及其依据，不生成文件')
    ps.add_argument('--cropwat-data', metavar='DIR', help='CROPWAT 安装目录（默认自动查找）')
    ps.set_defaults(func=_cmd_soil)

    a = p.parse_args()
    if not a.cmd:
        p.print_help()
        return 2
    return a.func(a)


if __name__ == '__main__':
    sys.exit(main())
