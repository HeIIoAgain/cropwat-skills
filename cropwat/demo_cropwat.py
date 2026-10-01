# -*- coding: utf-8 -*-
"""
CROPWAT 自动化示例 / Worked example
===================================
⚠ 本文件是**示例**，里面用的是内蒙古项目的具体参数。
   这些**不是技能的强制要求**——技能本身完全通用（见 cropwat_run.py，它靠扫描
   工作目录自动发现地点/年份/作物）。
   换项目时只需改下面 CONFIG 区，或直接用 cropwat_run.py 的命令行参数。

批量运行推荐用命令行工具（无需在此硬编码任何清单）：
    python cropwat_run.py check --data "<工作目录>"
    python cropwat_run.py run   --data "<工作目录>" \
        --sowing "WHEAT=05/05,MAIZE=25/04" --limit 2

播种日期格式为 **DD/MM（日/月）**：5月5日 = '05/05'，4月20日 = '20/04'。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cropwat_lib import CropWat, MENU, run_case  # noqa: F401

# ============================================================ 示例配置
# 换项目只改这一段即可。命令行工具 cropwat_run.py 则完全不需要这段。
#
# ⚠ 关于播种期：必须写进 **Crop 模块**（cw.set_planting_date），
#   写进茬口（Cropping Pattern）模块会被 CWR 计算**完全忽略**。
#   下面的示例都不建茬口模块——做作物需水量测算时它可以整块跳过。
CONFIG = {
    # CROPWAT 工作文件目录（内含 Climate/ Rain/ Crop/ 与 .SOI）
    'data': r'<你的 CROPWAT 工作文件目录>',   # 例如桌面\CROPWRT工作文件

    # 地点目录名 -> 文件名里的拼音前缀
    'cities': {
        '呼和浩特': 'Huhehaote', '包头': 'Baotou', '乌海': 'Wuhai', '赤峰': 'Chifeng',
        '通辽': 'Tongliao', '鄂尔多斯': 'Eerduosi', '呼伦贝尔': 'Hulunbeier',
        '巴彦淖尔': 'Bayannaoer', '乌兰察布': 'Wulanchabu', '兴安盟': 'Xinganmeng',
        '锡林郭勒': 'Xilinguole', '阿拉善': 'Alashan',
    },
    'crops': ['WHEAT', 'MAIZE', 'RICE', 'POTATO', 'SOYBEAN', 'SUNFLOWR', 'GRONDNUT'],
    'years': list(range(2013, 2025)),

    # 各作物播种期（DD/MM）。FAO 作物文件不带播种日期，必须按当地农艺资料给定；
    # 全部用同一天会引入系统性偏差。
    'sowing': {
        'WHEAT': '05/05', 'MAIZE': '05/05', 'RICE': '10/05', 'POTATO': '05/05',
        'SOYBEAN': '05/05', 'SUNFLOWR': '05/05', 'GRONDNUT': '05/05',
    },
    'soil': 'Neimeng.SOI',
    'out': r'E:\HelperFoloder\Deepseek-Herness\hohhot2024\cropwat_out',
}


# ============================================================ 示例 1
def example_smoke():
    """自检：能否启动 CROPWAT、窗体类名、菜单是否响应"""
    with CropWat() as cw:
        print("主窗体:", cw.mainform())
        print("启动时 MDI:", cw.mdi_describe())
        cw.menu(MENU.CLIMATE_MONTHLY_PM, 2.5)
        print("新建气候后 MDI:", cw.mdi_describe())
        cw.close_all()


# ============================================================ 示例 2
def example_one_case():
    """一组完整计算：一个地方 × 一个年份 × 一种作物（一次启动，算完关闭）"""
    c = CONFIG
    city, py = '呼和浩特', 'Huhehaote'
    year, crop = 2013, 'WHEAT'
    with CropWat(log_path=os.path.join(c['out'], 'one_trace.txt')) as cw:
        cw.new_session()
        cw.load_climate(os.path.join(c['data'], 'Climate', city, f'{py}_{year}.PEM'))
        cw.load_rain(os.path.join(c['data'], 'Rain', city, f'{py}_{year}.CRM'))
        cw.load_crop(os.path.join(c['data'], 'Crop', f'{crop}.CRO'))
        cw.load_soil(os.path.join(c['data'], c['soil']))
        cw.set_planting_date(c['sowing'][crop])      # DD/MM
        txt = cw.run_cwr()
        print(txt)


# ============================================================ 示例 3
def example_batch(limit=None):
    """批量：一次启动只算一组，算完立即关闭 CROPWAT，再开下一个"""
    c = CONFIG
    os.makedirs(c['out'], exist_ok=True)
    tasks = [(ct, cr, y) for ct in c['cities'] for cr in c['crops'] for y in c['years']]
    if limit:
        tasks = tasks[:limit]
    print(f"共 {len(tasks)} 组")

    ok = fail = 0
    for i, (city, crop, year) in enumerate(tasks, 1):
        py = c['cities'][city]
        tag = f'{city}_{crop}_{year}'
        t0 = __import__('time').time()
        try:
            # ---- 一次启动，只算这一组；with 退出时进程关闭 ----
            with CropWat(verbose=False) as cw:
                cw.new_session()
                cw.load_climate(os.path.join(c['data'], 'Climate', city, f'{py}_{year}.PEM'))
                cw.load_rain(os.path.join(c['data'], 'Rain', city, f'{py}_{year}.CRM'))
                cw.load_crop(os.path.join(c['data'], 'Crop', f'{crop}.CRO'),
                             rice=(crop.upper() == 'RICE'))
                cw.load_soil(os.path.join(c['data'], c['soil']))
                cw.set_planting_date(c['sowing'].get(crop, '05/05'))
                txt = cw.run_cwr()
            if txt:
                open(os.path.join(c['out'], f'{tag}.txt'), 'w', encoding='utf-8').write(txt)
                ok += 1
            else:
                fail += 1
        except Exception as e:
            fail += 1
            print(f"  [{i}] {tag} ✗ {type(e).__name__}: {e}")
        if i % 10 == 0 or i == len(tasks):
            print(f"  进度 {i}/{len(tasks)}  成功 {ok}  失败 {fail}")
    print(f"完成：成功 {ok}，失败 {fail}")


if __name__ == '__main__':
    print(__doc__)
    which = sys.argv[1] if len(sys.argv) > 1 else ''
    if which == 'smoke':
        example_smoke()
    elif which == 'one':
        example_one_case()
    elif which == 'batch':
        example_batch(limit=int(sys.argv[2]) if len(sys.argv) > 2 else None)
    else:
        print("用法: python demo_cropwat.py [smoke|one|batch [N]]")
        print("换项目请改本文件顶部的 CONFIG，或改用 cropwat_run.py 的命令行参数。")
