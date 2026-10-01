# -*- coding: utf-8 -*-
"""
cropwat_lib —— CROPWAT 8.0 自动化驱动库
========================================
CROPWAT 8.0 是 Delphi/VCL 编写的 Win32 GUI 程序，没有 CLI/API。
本库用 Win32 消息驱动它：PostMessage(WM_COMMAND, 菜单ID) 模拟菜单点击，
SendMessage/GetWindowText 读写控件，剪贴板取结果。

用法：
    from cropwat_lib import CropWat
    with CropWat() as cw:                       # 一次启动只算一组，退出即关闭
        cw.new_session()
        cw.load_climate(r"<工作目录>\\Climate\\<地点>\\<名称>_<年份>.PEM")
        cw.load_rain(   r"<工作目录>\\Rain\\<地点>\\<名称>_<年份>.CRM")
        cw.load_crop(   r"<工作目录>\\Crop\\<作物>.CRO")
        cw.load_soil(   r"<工作目录>\\<土壤>.SOI")
        cw.set_planting_date("05/05")           # DD/MM（日/月）
        txt = cw.run_cwr()                      # 返回 CWR 结果表格文本
        print(txt)

批量运行请用同目录的 cropwat_run.py（自动扫描工作目录，无需硬编码地点/年份/作物清单）。

依赖：仅标准库（ctypes）。在 Windows + CROPWAT 8.0 下测试通过。

作者注（踩过的坑都在代码注释里标了 ⚠）：
  1. 菜单命令必须主窗口在前台，否则静默失败 → 每次发命令前 activate()
  2. GetClipboardData / GlobalLock 必须设 restype=c_void_p，否则 64 位指针被截断而崩溃
  3. SetClipboardData 必须设 argtypes，否则 OverflowError
  4. 文件对话框按钮是中文，匹配时要排除 "只读"/"取消"/"帮助"
  5. Delphi TMaskEdit 日期框的分隔符是模板字面量，只能发数字
"""
import ctypes
import ctypes.wintypes as w
import os
import subprocess
import time

# ---------------------------------------------------------------- 常量

# CROPWAT 的安装位置**不写死**。运行时按 find_cropwat() 自动查找：
#   显式路径 > 环境变量 > 注册表 > 常见目录 > 浅层磁盘扫描 > PATH
# 找到后把 exe 所在目录当作工作目录（CROPWAT 需要以自身目录为 cwd 启动）。
CROPWAT_EXE_NAME = 'cropwat.exe'

# 仅作为最后的兜底提示，不参与查找优先级
_HINT_PATHS = [
    r'C:\Program Files\CROPWAT 8.0\cropwat.exe',
    r'C:\Program Files (x86)\CROPWAT 8.0\cropwat.exe',
]


def find_cropwat(explicit=None, verbose=False, max_depth=3, budget=25.0):
    """自动定位 CROPWAT 8.0 的 cropwat.exe。

    返回 (exe_path, workdir)；找不到返回 (None, None)。

    查找顺序
    --------
    1. 显式给出的路径（命令行 --exe）
    2. 环境变量 CROPWAT_EXE / CROPWAT_HOME
    3. 注册表：App Paths\\cropwat.exe、卸载项里的 InstallLocation
    4. 常见目录：各盘符下的 Program Files / Program Files (x86) / 根目录
    5. 浅层扫描：各盘符向下 max_depth 层找 cropwat.exe（跳过系统目录）
    6. PATH
    """
    import glob as _glob
    import time as _time

    def say(m):
        if verbose:
            print(m, flush=True)

    def ok(p):
        if p and os.path.isfile(p):
            p = os.path.abspath(p)
            say(f"  找到 CROPWAT: {p}")
            return (p, os.path.dirname(p))
        return None

    # 1) 显式
    if explicit:
        r = ok(explicit)
        if r:
            return r
        say(f"  --exe 指定的路径不存在: {explicit}，继续自动查找")
        # 也可能是目录
        if explicit and os.path.isdir(explicit):
            r = ok(os.path.join(explicit, CROPWAT_EXE_NAME))
            if r:
                return r

    # 2) 环境变量
    for var in ('CROPWAT_EXE', 'CROPWAT_HOME', 'CROPWAT_DIR'):
        v = os.environ.get(var)
        if not v:
            continue
        say(f"  环境变量 {var} = {v}")
        r = ok(v) or ok(os.path.join(v, CROPWAT_EXE_NAME))
        if r:
            return r

    # 3) 注册表
    try:
        import winreg
        APPPATHS = r'SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\cropwat.exe'
        UNINST = r'SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall'
        roots = [(winreg.HKEY_LOCAL_MACHINE, 0), (winreg.HKEY_LOCAL_MACHINE, winreg.KEY_WOW64_32KEY),
                 (winreg.HKEY_CURRENT_USER, 0)]
        for hive, flag in roots:
            # 3a) App Paths
            try:
                with winreg.OpenKey(hive, APPPATHS, 0, winreg.KEY_READ | flag) as k:
                    p, _ = winreg.QueryValueEx(k, '')
                    r = ok(p)
                    if r:
                        return r
            except OSError:
                pass
            # 3b) 卸载项
            try:
                with winreg.OpenKey(hive, UNINST, 0, winreg.KEY_READ | flag) as k:
                    for i in range(winreg.QueryInfoKey(k)[0]):
                        try:
                            sub = winreg.EnumKey(k, i)
                            with winreg.OpenKey(k, sub) as sk:
                                try:
                                    name, _ = winreg.QueryValueEx(sk, 'DisplayName')
                                except OSError:
                                    continue
                                if 'cropwat' not in str(name).lower():
                                    continue
                                say(f"  注册表卸载项: {name}")
                                for vn in ('InstallLocation', 'DisplayIcon', 'UninstallString'):
                                    try:
                                        val, _ = winreg.QueryValueEx(sk, vn)
                                    except OSError:
                                        continue
                                    cand = str(val).strip('"').split('"')[0]
                                    if cand.lower().endswith('.exe'):
                                        r = ok(cand)
                                    else:
                                        r = ok(os.path.join(cand, CROPWAT_EXE_NAME))
                                    if r:
                                        return r
                        except OSError:
                            continue
            except OSError:
                pass
    except Exception as e:
        say(f"  注册表查找跳过: {e}")

    # 4) 常见目录
    drives = [f'{d}:\\' for d in 'CDEFGH' if os.path.exists(f'{d}:\\')]
    common = []
    for d in drives:
        common += [
            os.path.join(d, 'Program Files', 'CROPWAT', CROPWAT_EXE_NAME),
            os.path.join(d, 'Program Files (x86)', 'CROPWAT', CROPWAT_EXE_NAME),
            os.path.join(d, 'Program Files', 'CROPWAT 8.0', CROPWAT_EXE_NAME),
            os.path.join(d, 'Program Files (x86)', 'CROPWAT 8.0', CROPWAT_EXE_NAME),
            os.path.join(d, 'CROPWAT', CROPWAT_EXE_NAME),
            os.path.join(d, 'CROPWAT 8.0', CROPWAT_EXE_NAME),
            os.path.join(d, 'StudyProgram', 'CROPWAT', CROPWAT_EXE_NAME),
            os.path.join(d, 'Program', 'CROPWAT', CROPWAT_EXE_NAME),
            os.path.join(d, 'Software', 'CROPWAT', CROPWAT_EXE_NAME),
        ]
    for p in common:
        r = ok(p)
        if r:
            return r

    # 5) 浅层扫描
    say(f"  常见目录未命中，开始浅层扫描（深度 {max_depth}）…")
    SKIP = {'windows', '$recycle.bin', 'system volume information', 'programdata',
            'appdata', 'node_modules', '$windows.~bt', 'perflogs', 'recovery'}
    t0 = _time.time()
    for d in drives:
        for root, dirs, files in os.walk(d):
            depth = root[len(d):].count(os.sep)
            if depth >= max_depth:
                dirs[:] = []
            dirs[:] = [x for x in dirs if x.lower() not in SKIP and not x.startswith('$')]
            if CROPWAT_EXE_NAME in [f.lower() for f in files]:
                for f in files:
                    if f.lower() == CROPWAT_EXE_NAME:
                        r = ok(os.path.join(root, f))
                        if r:
                            return r
            if _time.time() - t0 > budget:
                say(f"  扫描超时（{budget:.0f}s），停止")
                break
        if _time.time() - t0 > budget:
            break

    # 6) PATH
    try:
        import shutil as _sh
        p = _sh.which('cropwat') or _sh.which(CROPWAT_EXE_NAME)
        r = ok(p)
        if r:
            return r
    except Exception:
        pass

    say("  未能自动找到 CROPWAT")
    return (None, None)


def cropwat_not_found_message():
    return (
        "未能自动找到 CROPWAT 8.0（cropwat.exe）。\n"
        "       请用 --exe 指定它的完整路径，例如：\n"
        "         --exe \"D:\\\\Program Files\\\\CROPWAT 8.0\\\\cropwat.exe\"\n"
        "       或设置环境变量 CROPWAT_EXE=<cropwat.exe 的完整路径> 后重试。\n"
        "       自动查找会依次尝试：环境变量 → 注册表 → 常见目录 → 磁盘浅层扫描 → PATH。"
    )

# 菜单 ID（用 probe_menus() 可在你本机重新枚举核对）
class MENU:
    # File
    NEW_SESSION = 2
    OPEN_SESSION = 3
    SAVE_SESSION = 4
    SAVE_SESSION_AS = 5
    # New
    NEW_CLIMATE = 9
    CLIMATE_MONTHLY_PM = 10
    CLIMATE_DECADE_PM = 11
    CLIMATE_DAILY_PM = 12
    CLIMATE_MONTHLY_MEASURED = 14
    CLIMATE_DECADE_MEASURED = 15
    CLIMATE_DAILY_MEASURED = 16
    NEW_RAIN = 17
    RAIN_MONTHLY = 18
    RAIN_DECADE = 19
    RAIN_DAILY = 20
    NEW_CROP = 21
    CROP_DRY = 22
    CROP_RICE = 23
    NEW_SOIL = 24
    NEW_CROPPING_PATTERN = 25
    # 各模块的 Open / Save / SaveAs
    # 注：26/27/28 是「气候模块」的；其他模块新建后同样用这组 id 打开
    MODULE_OPEN = 26
    MODULE_SAVE = 27
    MODULE_SAVE_AS = 28
    SAVE_ALL = 29
    # Copy Table
    COPY_TABLE = 42
    COPY_DATA_ONLY = 43
    COPY_DATA_AND_HEADERS = 44
    # Calculations
    CALC_CWR = 51
    CALC_IRRIGATION = 52
    CALC_SCHEME_SUPPLY = 53
    # Window
    CLOSE_ALL = 72

# Win32 常量
WM_COMMAND = 0x0111
WM_SETTEXT = 0x000C
WM_CHAR = 0x0102
WM_CLOSE = 0x0010
WM_MDIACTIVATE = 0x0222
BM_CLICK = 0x00F5
IDOK = 1
CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002
SW_RESTORE = 9
CB_SETCURSEL = 0x014E
CBN_SELCHANGE = 1
VK_BACK = 0x08
VK_RETURN = 0x0D
KEYEVENTF_KEYUP = 0x0002
ME_LEFTDOWN = 0x0002
ME_LEFTUP = 0x0004
SWP_SHOWWINDOW = 0x0040
VK_DIGIT = {c: 0x30 + i for i, c in enumerate('0123456789')}

_u32 = ctypes.WinDLL('user32', use_last_error=True)
_k32 = ctypes.WinDLL('kernel32', use_last_error=True)

# ⚠ 关键：不设这些 restype/argtypes，64 位下会静默崩溃或抛 OverflowError
_u32.GetClipboardData.restype = ctypes.c_void_p
_u32.GetClipboardData.argtypes = [w.UINT]
_u32.SetClipboardData.restype = ctypes.c_void_p
_u32.SetClipboardData.argtypes = [w.UINT, ctypes.c_void_p]
_u32.GetForegroundWindow.restype = w.HWND
_u32.GetWindowThreadProcessId.restype = w.DWORD
_u32.OpenClipboard.argtypes = [w.HWND]
_u32.keybd_event.argtypes = [w.BYTE, w.BYTE, w.DWORD, ctypes.c_void_p]
_u32.keybd_event.restype = None
_u32.mouse_event.argtypes = [w.DWORD, w.DWORD, w.DWORD, w.DWORD, ctypes.c_void_p]
_u32.mouse_event.restype = None
_u32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
_k32.GlobalLock.restype = ctypes.c_void_p
_k32.GlobalLock.argtypes = [ctypes.c_void_p]
_k32.GlobalUnlock.argtypes = [ctypes.c_void_p]
_k32.GlobalAlloc.restype = ctypes.c_void_p
_k32.GlobalAlloc.argtypes = [w.UINT, ctypes.c_size_t]
_k32.GetCurrentThreadId.restype = w.DWORD

_WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, w.HWND, w.LPARAM)


def _rect(h):
    r = w.RECT()
    _u32.GetWindowRect(h, ctypes.byref(r))
    return r


# ---------------------------------------------------------------- 底层工具

def _text(h, n=4096):
    b = ctypes.create_unicode_buffer(n)
    _u32.GetWindowTextW(h, b, n)
    return b.value


def _cls(h, n=256):
    b = ctypes.create_unicode_buffer(n)
    _u32.GetClassNameW(h, b, n)
    return b.value


def _pid_of(h):
    p = w.DWORD()
    _u32.GetWindowThreadProcessId(h, ctypes.byref(p))
    return p.value


def _enum_windows():
    out = []
    _u32.EnumWindows(_WNDENUMPROC(lambda h, l: (out.append(h), True)[1]), 0)
    return out


def _enum_children(h):
    out = []
    _u32.EnumChildWindows(h, _WNDENUMPROC(lambda c, l: (out.append(c), True)[1]), 0)
    return out


def _walk(h, acc=None):
    """递归枚举所有后代控件"""
    if acc is None:
        acc = []
    for c in _enum_children(h):
        acc.append(c)
        _walk(c, acc)
    return acc


# ---------------------------------------------------------------- 主类

class CropWatError(RuntimeError):
    pass


class CropWat:
    """CROPWAT 8.0 会话。

    建议用 with 语句，退出时自动 terminate。
        with CropWat() as cw:
            ...
    """

    def __init__(self, exe=None, workdir=None,
                 start_timeout=9.0, verbose=True, log_path=None, attach_pid=None):
        # CROPWAT 位置不写死：exe/workdir 为空时自动查找
        if exe and os.path.isdir(exe):
            exe, workdir = os.path.join(exe, CROPWAT_EXE_NAME), exe
        if not exe or not os.path.isfile(exe or ''):
            found_exe, found_wd = find_cropwat(explicit=exe, verbose=verbose)
            if not found_exe:
                raise CropWatError(cropwat_not_found_message())
            exe, workdir = found_exe, (workdir or found_wd)
        self.exe = os.path.abspath(exe)
        self.workdir = os.path.abspath(workdir or os.path.dirname(self.exe))
        self.verbose = verbose
        self.proc = None
        self.pid = None
        self._log = None
        if log_path:
            self._log = open(log_path, 'w', encoding='utf-8')
        if attach_pid:
            self.pid = int(attach_pid)
            if self.mainform() is None:
                raise CropWatError(f"pid={self.pid} 不是 CROPWAT 主窗口")
            self._say(f"[CropWat] 已附加到已知进程 pid={self.pid}")
        else:
            self._launch(start_timeout)

    # -------------------------------------------------- 生命周期

    def _say(self, msg):
        if self.verbose:
            print(msg, flush=True)
        if self._log:
            self._log.write(str(msg) + "\n")
            self._log.flush()

    def _launch(self, timeout):
        if not os.path.exists(self.exe):
            raise CropWatError(f"找不到 CROPWAT: {self.exe}")
        self.proc = subprocess.Popen([self.exe], cwd=self.workdir)
        time.sleep(timeout)                       # 等主窗体完成初始化
        self.pid = self.proc.pid
        if self.mainform() is None:
            time.sleep(4)
        if self.mainform() is None:
            raise CropWatError("CROPWAT 主窗体未出现（可能需要完整的文件/注册表权限）")
        self.dismiss_dialogs("启动")
        self._say(f"[CropWat] 已启动 pid={self.pid}")

    def close(self):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            time.sleep(1.5)
            if self.proc.poll() is None:
                self.proc.kill()
            self._say("[CropWat] 已关闭")
        if self._log:
            try:
                self._log.close()
            except Exception:
                pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()

    # -------------------------------------------------- 窗体查找

    def all_top(self):
        return [h for h in _enum_windows() if _pid_of(h) == self.pid]

    def mainform(self):
        for h in self.all_top():
            if _cls(h) == 'TMainForm':
                return h
        return None

    def mdi_children(self):
        """所有 MDI 子窗体（气候/降水/作物/土壤/结果 各是一个子窗体）"""
        for c in _enum_children(self.mainform()):
            if _cls(c) == 'MDIClient':
                return _enum_children(c)
        return []

    def mdi_describe(self):
        return [_cls(c) for c in self.mdi_children()]

    def dialogs(self):
        """可见的模态对话框（文件对话框、消息框等）"""
        return [h for h in self.all_top()
                if _cls(h) == '#32770' and _u32.IsWindowVisible(h)]

    def dismiss_dialogs(self, tag="", close=True):
        """关闭所有弹窗；返回弹窗文字列表"""
        out = []
        for d in self.dialogs():
            t = _text(d)
            out.append(t)
            self._say(f"    [弹窗{tag}] {t[:90]}")
            if close:
                _u32.PostMessageW(d, WM_CLOSE, 0, 0)
                time.sleep(0.8)
        return out

    def find_form(self, class_name):
        """按类名找 MDI 子窗体，如 'TMonthEToPMForm' / 'TCropForm' / 'TCwrForm'"""
        for c in self.mdi_children():
            if _cls(c).lower() == class_name.lower():
                return c
        return None

    # -------------------------------------------------- 焦点与菜单

    def activate(self, hwnd=None):
        """⚠ 把主窗口拉到前台。不发这一步，菜单命令会静默失败。"""
        m = self.mainform()
        if m is None:
            return False
        hwnd = hwnd or m
        t_me = _k32.GetCurrentThreadId()
        t_tg = _u32.GetWindowThreadProcessId(m, None)
        try:
            _u32.AttachThreadInput(t_me, t_tg, True)
            _u32.ShowWindow(m, SW_RESTORE)
            _u32.SetForegroundWindow(m)
            _u32.BringWindowToTop(m)
            _u32.SetFocus(hwnd)
            time.sleep(0.4)
            return True
        finally:
            try:
                _u32.AttachThreadInput(t_me, t_tg, False)
            except Exception:
                pass

    def menu(self, menu_id, wait=3.0):
        """发一个菜单命令。内部已包含 activate()。"""
        self.activate()
        _u32.PostMessageW(self.mainform(), WM_COMMAND, menu_id, 0)
        time.sleep(wait)

    # -------------------------------------------------- 剪贴板

    def set_clipboard(self, s):
        if not _u32.OpenClipboard(None):
            return False
        try:
            _u32.EmptyClipboard()
            n = (len(s) + 1) * 2
            h = _k32.GlobalAlloc(GMEM_MOVEABLE, n)
            p = _k32.GlobalLock(h)
            ctypes.memmove(p, ctypes.create_unicode_buffer(s), n)
            _k32.GlobalUnlock(h)
            _u32.SetClipboardData(CF_UNICODETEXT, h)
            return True
        finally:
            _u32.CloseClipboard()

    def get_clipboard(self, retries=15, delay=0.4):
        for _ in range(retries):
            if _u32.OpenClipboard(None):
                break
            time.sleep(delay)
        else:
            return None
        try:
            if not _u32.IsClipboardFormatAvailable(CF_UNICODETEXT):
                return None
            h = _u32.GetClipboardData(CF_UNICODETEXT)
            if not h:
                return None
            p = _k32.GlobalLock(h)
            if not p:
                return None
            try:
                return ctypes.wstring_at(p)       # ⚠ 必须是 wstring_at
            finally:
                _k32.GlobalUnlock(p)
        finally:
            _u32.CloseClipboard()

    # -------------------------------------------------- 文件对话框

    def file_dialog(self, path, keys, tries=4, type_delay=0.9, settle=2.6):
        """在文件对话框里填路径并点按钮。
        keys: ['打开','Open'] 或 ['保存','Save']
        ⚠ 中文按钮里 '只读方式打开' 也含'打开'，必须排除。
        """
        for _ in range(tries):
            time.sleep(2.2)
            dl = self.dialogs()
            if not dl:
                return False
            d = dl[-1]
            cs = _walk(d)
            edits = [c for c in cs if _cls(c) == 'Edit']
            btns = [c for c in cs if _cls(c) in ('Button', 'TButton')]
            if edits:
                _u32.SendMessageW(edits[0], WM_SETTEXT, 0,
                                  ctypes.c_wchar_p(path))
                time.sleep(type_delay)
            chosen = None
            for b in btns:
                t = _text(b).replace('&', '')
                if any(x in t for x in ('取消', '帮助', '只读')):
                    continue
                if any(k in t for k in keys):
                    chosen = b
                    break
            if chosen is not None:
                _u32.SendMessageW(chosen, BM_CLICK, 0, 0)
            else:
                _u32.PostMessageW(d, WM_COMMAND, IDOK, 0)
            time.sleep(settle)
            if not self.dialogs():
                return True
            # 有些对话框需要再来一轮
            _u32.PostMessageW(d, WM_COMMAND, IDOK, 0)
            time.sleep(1.8)
            if not self.dialogs():
                return True
        return False

    def focus_control(self, hwnd):
        """⚠ Delphi 控件不发这一步就收不到 WM_CHAR / 键盘消息。
        返回是否真的拿到了焦点。"""
        m = self.mainform()
        if m is None:
            return False
        t_me = _k32.GetCurrentThreadId()
        t_tg = _u32.GetWindowThreadProcessId(m, None)
        try:
            _u32.AttachThreadInput(t_me, t_tg, True)
            _u32.ShowWindow(m, SW_RESTORE)
            _u32.SetForegroundWindow(m)
            _u32.BringWindowToTop(m)
            _u32.SetFocus(hwnd)
            time.sleep(0.35)
            return _u32.GetFocus() == hwnd
        finally:
            try:
                _u32.AttachThreadInput(t_me, t_tg, False)
            except Exception:
                pass

    def set_mask_text(self, hwnd, text):
        """⚠ 往 Delphi TMaskEdit 写值的唯一有效方法。

        实测（2026-09）：
          SendMessage(WM_SETTEXT)  → 无效，仍是 '__/__'
          SendMessage(WM_CHAR)     → 无效
          聚焦 + keybd_event 键盘   → 无效
          聚焦 + Ctrl+V 粘贴        → 无效
          SetWindowTextW           → ✓ 成功，显示 '05/05'

        即使鼠标点击已让控件确实获得焦点（GetGUIThreadInfo 确认 hwndFocus 是该控件），
        键盘事件依然进不去——这是 CROPWAT 这个 Delphi 程序的特性，不是焦点问题。
        """
        _u32.SetWindowTextW.argtypes = [w.HWND, ctypes.c_wchar_p]
        _u32.SetWindowTextW(hwnd, text)
        time.sleep(0.6)
        return _text(hwnd)

    def type_digits(self, hwnd, s, per_char=0.10, focus=True):
        """兼容旧接口。s 形如 '0505' 会转成 '05/05' 再写。"""
        if '/' not in s and len(s) == 4:
            s = f"{s[:2]}/{s[2:]}"
        return self.set_mask_text(hwnd, s)

    def set_planting_date(self, date, crop_form=None):
        """在「作物窗体」里设置播种期 —— ★已验证可用的完整序列★

        date 格式是 **DD/MM（日/月）**，如 5月5日 = '05/05'，4月20日 = '20/04'。
        ⚠ CROPWAT 用「日/月」而不是「月/日」！验证方法：播种 05/05 + total 130 天
          → 收获显示 11/09，即 9月11日（130 天），若是 11月9日则为 188 天。

        必须严格按此顺序，缺一步就写不进去：
          ① 点击作物窗体（激活 MDI 子窗体）
          ② 点击播种期输入框
          ③ **连按 4 次 Backspace** 清空掩码模板 ← 关键，跳过则输入无效
          ④ 输入 4 位数字
          ⑤ 按回车提交

        ⚠ 不要用 GetWindowText 验证！这个 Delphi TMaskEdit 永远返回 '__/__'，
          读不到真实值。正确验证方式：跑 CWR 看结果表的 Month 列是否从预期月份开始。
        """
        if crop_form is None:
            crop_form = self.find_form('TCropForm')
        if not crop_form:
            self._say("  ! 未找到作物窗体")
            return False
        masks = [c for c in _walk(crop_form) if _cls(c) == 'TMaskEdit']
        if not masks:
            self._say("  ! 作物窗体无播种期输入框")
            return False
        mask = masks[0]

        # ① 先把作物窗体移到**固定位置**，再按相对坐标点击。
        #    ⚠ CROPWAT 会记住窗口位置；若上一轮把窗体挪过地方，绝对坐标点击就会落空，
        #      播种期写不进去（表现为 CWR 结果从年初开始、ETc 只剩约 1/4）。
        _u32.SetWindowPos.argtypes = [w.HWND, w.HWND, ctypes.c_int, ctypes.c_int,
                                      ctypes.c_int, ctypes.c_int, w.UINT]
        _u32.SetWindowPos(crop_form, None, 120, 120, 1080, 520, SWP_SHOWWINDOW)
        time.sleep(0.9)

        self.activate()
        time.sleep(0.35)
        r = _rect(crop_form)
        self.click(r.left + 60, r.top + 12)          # 窗体标题区，激活 MDI 子窗体
        time.sleep(0.5)
        mc = None
        for k in _walk(self.mainform()):
            if _cls(k) == 'MDIClient':
                mc = k
                break
        if mc:
            _u32.SendMessageW(mc, WM_MDIACTIVATE, crop_form, 0)
        self.activate()
        time.sleep(0.5)

        # ② 重新取一次输入框位置（窗体刚被移动过）并点击
        self.activate()
        time.sleep(0.35)
        mr = _rect(mask)
        cx, cy = (mr.left + mr.right) // 2, (mr.top + mr.bottom) // 2
        self.click(cx, cy)
        time.sleep(0.4)

        # ③ 连按 4 次 Backspace 清空掩码
        for _ in range(4):
            self.tap_key(VK_BACK)
        time.sleep(0.3)

        # ④ 输入 DDMM
        digits = date.replace('/', '')
        for ch in digits[:4]:
            self.tap_key(VK_DIGIT[ch])
        time.sleep(0.4)

        # ⑤ 回车提交
        self.tap_key(VK_RETURN, hold=0.15)
        time.sleep(0.8)
        self._say(f"  播种期已输入 {date}（DD/MM）")
        return True

    # 兼容旧名
    def set_crop_planting_date(self, date):
        return self.set_planting_date(date)

    # -------------------------------------------------- 真实输入（鼠标/键盘）

    def click(self, x, y, settle=0.55):
        """模拟真实鼠标点击（SetCursorPos + mouse_event）"""
        _u32.SetCursorPos(int(x), int(y))
        time.sleep(0.25)
        _u32.mouse_event(ME_LEFTDOWN, 0, 0, 0, None)
        time.sleep(0.10)
        _u32.mouse_event(ME_LEFTUP, 0, 0, 0, None)
        time.sleep(settle)

    def tap_key(self, vk, hold=0.09, gap=0.10):
        """模拟真实按键（keybd_event）。掩码框收不到 WM_CHAR，必须用这个。"""
        _u32.keybd_event(vk, 0, 0, None)
        time.sleep(hold)
        _u32.keybd_event(vk, 0, KEYEVENTF_KEYUP, None)
        time.sleep(gap)

    # -------------------------------------------------- 载入各类数据

    def _load(self, new_menu_id, path, label, wait_new=3.5):
        self.menu(new_menu_id, wait_new)          # 新建该类数据
        self.menu(MENU.MODULE_OPEN, 1.0)          # 该模块的 Open
        ok = self.file_dialog(path, ['打开', 'Open'])
        self._say(f"  {label}: {'OK' if ok else 'FAIL'}  子窗体={self.mdi_describe()}")
        if not ok:
            raise CropWatError(f"{label} 打开失败: {path}")
        return ok

    def new_session(self):
        self.menu(MENU.NEW_SESSION, 2.0)

    def load_climate(self, pem_path, measured_eto=False):
        """载入 .PEM。默认用「Monthly PM」——ETo 由 CROPWAT 自算。
        若你的文件是实测 ETo，用 measured_eto=True。"""
        mid = MENU.CLIMATE_MONTHLY_MEASURED if measured_eto else MENU.CLIMATE_MONTHLY_PM
        return self._load(mid, pem_path, 'Climate')

    def load_rain(self, crm_path, mode='monthly'):
        mid = {'monthly': MENU.RAIN_MONTHLY,
               'decade': MENU.RAIN_DECADE,
               'daily': MENU.RAIN_DAILY}[mode]
        return self._load(mid, crm_path, 'Rain')

    def load_crop(self, cro_path, rice=False):
        return self._load(MENU.CROP_RICE if rice else MENU.CROP_DRY, cro_path, 'Crop')

    def load_soil(self, soi_path):
        return self._load(MENU.NEW_SOIL, soi_path, 'Soil')

    # -------------------------------------------------- 茬口与计算

    def set_cropping_pattern(self, date="0505", crop_combo_index=0, row=None):
        """新建茬口并设置播种日期。

        ⚠⚠ **茬口（Cropping Pattern）模块的输入会被 CWR 计算完全忽略。** ⚠⚠

        在里面填日期、选作物、调行序，甚至**根本不建这个模块**，
        对 ETc、有效降水、灌溉需水都**没有任何影响**。
        CROPWAT 只从 **Crop 模块**读取播种期。

        所以做作物需水量 / 水足迹测算时，**这个模块可以整块跳过**——
        cropwat_run.py 的每组流程就完全不建茬口窗体，结果照样正确。

        只有被要求做「灌溉排程 / 供水计划」时才需要它，那是另一套计算。

        要改变计算结果，必须用 set_planting_date()。

        保留本方法只是为了确实需要时能操作茬口窗体本身。

        茬口窗体是一张 20 行的表：每行 = 一个日期框(TMaskEdit) + 一个作物下拉(TComboBox)。
        已载入的作物会自动出现在下拉框里。row=None 时自动选最上面一行（y 最小）。
        """
        self.menu(MENU.NEW_CROPPING_PATTERN, 4.0)
        pat = self.find_form('TCropPatForm')
        if not pat:
            self._say("  ! 未找到茬口窗体")
            return False

        rect = w.RECT()
        masks = [c for c in _walk(pat) if _cls(c) == 'TMaskEdit']
        combos = [c for c in _walk(pat) if _cls(c) == 'TComboBox']
        if not masks:
            self._say("  ! 茬口窗体无日期框")
            return False

        # 给每个控件记下 y 坐标，按 y 排序确定"第一行"
        def ypos(c):
            r = w.RECT()
            _u32.GetWindowRect(c, ctypes.byref(r))
            return r.top

        rows = sorted(set(ypos(c) for c in masks))
        target_y = rows[0] if row is None else rows[min(row, len(rows) - 1)]
        first_masks = [c for c in masks if ypos(c) == target_y]
        first_combos = [c for c in combos if ypos(c) == target_y]

        # 该行选作物
        if first_combos:
            cb = first_combos[0]
            n = _u32.SendMessageW(cb, 0x0146, 0, 0)          # CB_GETCOUNT
            if n:
                _u32.SendMessageW(cb, CB_SETCURSEL, min(crop_combo_index, n - 1), 0)
                time.sleep(0.4)
                cid = _u32.GetDlgCtrlID(cb)
                _u32.SendMessageW(pat, WM_COMMAND,
                                  (CBN_SELCHANGE << 16) | (cid & 0xFFFF), cb)
                time.sleep(1.0)
            self._say(f"  茬口第1行作物下拉: {_text(cb)[:60]!r}")

        # 该行填日期（用 SetWindowTextW）
        mk = first_masks[0]
        got = self.set_mask_text(mk, date if '/' in date else f"{date[:2]}/{date[2:]}")
        self._say(f"  茬口第1行播种期 -> {got!r}  (目标 {date}, 行y={target_y})")
        return got.strip('_/ ') != ''

    def run_cwr(self, copy_mode='auto', wait=8.0, retries=3):
        """运行 Calculations→CWR，返回结果表格文本（失败返回 None）。

        ⚠ Copy Table 用 cid=43(Data only) 才有效；44(Data and Headers) 在本机实测无效。
        """
        self.menu(MENU.CALC_CWR, wait)
        self.dismiss_dialogs("CWR")
        cwr = None
        for c in self.mdi_children():
            if _cls(c).lower() == 'tcwrform':
                cwr = c
                break
        if cwr:
            # 把 MDI 激活 + 强制聚焦表格，Copy Table 才吃得到
            mc = None
            for k in _walk(self.mainform()):
                if _cls(k) == 'MDIClient':
                    mc = k
                    break
            if mc:
                _u32.SendMessageW(mc, WM_MDIACTIVATE, cwr, 0)
            _u32.ShowWindow(cwr, 5)
            time.sleep(1.0)
            grid = None
            for c in _walk(cwr):
                if 'grid' in _cls(c).lower():
                    grid = c
                    break
            if grid:
                self.focus_control(grid)
            self.activate(cwr)
            time.sleep(1.0)
        else:
            self._say("  ! 未找到 CWR 结果窗体")

        if copy_mode == 'auto':
            order = [MENU.COPY_DATA_ONLY, MENU.COPY_DATA_AND_HEADERS]
        else:
            order = [MENU.COPY_DATA_AND_HEADERS if copy_mode == 'headers'
                     else MENU.COPY_DATA_ONLY]
        for cid in order:
            for _ in range(retries):
                self.set_clipboard('###CROPWAT-SENTINEL###')
                self.menu(cid, 4.0)
                txt = self.get_clipboard()
                if txt and txt != '###CROPWAT-SENTINEL###':
                    self._say(f"  CWR 结果已取到 {len(txt)} 字符 (cid={cid})")
                    return txt
                self._say(f"  cid={cid} 未生效，重试…")
                time.sleep(1.5)
        return None

    def save_module_as(self, path):
        """当前模块另存（气候→.PEM，降水→.CRM，作物→.CRO，土壤→.SOI）"""
        self.menu(MENU.MODULE_SAVE_AS, 1.5)
        return self.file_dialog(path, ['保存', 'Save'])

    def close_all(self):
        self.menu(MENU.CLOSE_ALL, 1.5)
        self.dismiss_dialogs("CloseAll")


# ---------------------------------------------------------------- 便捷入口

def run_case(climate_pem, rain_crm, crop_cro, soil_soi,
             sowing_date="05/05", exe=None, workdir=None,
             verbose=True, log_path=None):
    """跑一个完整案例，返回 CWR 结果文本。exe 为空时自动查找 CROPWAT。

    ⚠ 播种期必须写进 **Crop 模块**（set_planting_date），不能写进茬口模块
      （set_cropping_pattern）——后者对 CWR 计算完全无效。这是本项目踩过的坑。

    ⚠ 顺序也重要：必须**先 load_crop 再 set_planting_date**。
      反过来的话，载入作物文件会把播种期重置掉。
    """
    with CropWat(exe=exe, workdir=workdir, verbose=verbose, log_path=log_path) as cw:
        cw.new_session()
        cw.load_climate(climate_pem)
        cw.load_rain(rain_crm)
        cw.load_crop(crop_cro)            # ← 必须先载作物
        cw.load_soil(soil_soi)
        cw.set_planting_date(sowing_date)  # ← 再设播种期（写进 Crop 模块）
        return cw.run_cwr()


if __name__ == '__main__':
    # 自检：只启动、报告菜单与窗体，然后退出
    with CropWat() as cw:
        print("主窗体:", cw.mainform())
        print("MDI 子窗体:", cw.mdi_describe())
        cw.menu(MENU.CLIMATE_MONTHLY_PM, 2.5)
        print("新建气候后 MDI:", cw.mdi_describe())
        cw.close_all()
    print("自检完成")
