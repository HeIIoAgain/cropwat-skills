# CROPWAT 技能使用说明

给 [DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness)（DSH）用的两个**技能**，
把 **FAO CROPWAT 8.0** 的完整流程自动化 —— 从原始气象数据一路算到作物需水量结果表。

装好之后，**你不需要自己敲任何命令，只要用中文跟 DSH 说话就行。**

---

## 一、这两个技能分别干什么

| 技能 | 干什么 | 好比 |
|---|---|---|
| **`cropwat-inputs`** | **造文件** —— 把原始气象数据变成 CROPWAT 能读的输入文件 | 备菜 |
| **`cropwat`** | **跑模型** —— 驱动 CROPWAT 算出作物需水量，输出结果 Excel | 炒菜 |

> **【重点】顺序不能反，必须先"备菜"再"炒菜"。**

---

## 二、装进 DSH（只做一次）

1. 按 `Win + R`，输入下面这行，回车：

   ```
   %USERPROFILE%\.dsh\skills
   ```

2. 把 `cropwat` 和 `cropwat-inputs` 两个文件夹，整个拖进去
3. 重启一次 DSH（保险起见）

装好可以验证：在 DSH 里问一句 **"你现在有哪些技能？"**，回答里应该能看到 `cropwat` 和 `cropwat-inputs`。

> **【换电脑也一样】** 把两个文件夹拷过去，拖进 `skills` 目录，重启。

---

## 三、你要准备什么

### (1) 气象数据

每个地点、每个年份、每个月一行。需要这几列：

| 列 | 含义 |
|---|---|
| `name` | 地点名。会用作文件夹名和文件名 |
| `lat` `lon` | 纬度、经度（度）。北纬为正 |
| `alt` | 海拔（米） |
| `year` `month` | 年、月 |
| `tmin` | 月平均最低气温（摄氏度）<br>**【注意】要月平均值，不是当月极值。用极值 ETo 会虚高约 19%** |
| `tmax` | 月平均最高气温（摄氏度）。同上，要平均值 |
| `rh` | 月平均相对湿度（%） |
| `wind_kmd` | 风速（公里/天）—— **不一定是这个单位，告诉 DSH 你的数据单位即可** |
| `sun_h` | 日照时数（小时/天）—— **不一定是这个单位，告诉 DSH 你的数据单位即可** |
| `rain_mm` | 月降水量（毫米） |

> **【好消息】`lat` / `lon` / `alt` 这三列可以不给！**
> 经纬度海拔都是公开信息，跟 DSH 说一句"坐标你上网查"，它就会自动查好。
> 程序还会生成一张**"站点坐标表.csv"**，你可以核对、改正，再让它用你改过的坐标。

> **【好消息】这些数据可以是一张表格，也可以是一堆表格，甚至是一堆表格的文件夹。**
> 可以掺杂大量不相关的数据，DSH 会自己找出有用的，**只要把文件路径告诉 DSH 即可**。
> **甚至可以是网页！给 DSH 网页链接就行。**

### (2) 每种作物的播种日期

> **【重点】每种作物都要单独给，不能用同一个日期。播种期不同，算出来的结果差很多。**

### (3) 跑哪些地方、哪些作物、哪些年份

---

## 四、话术参考（照着说就行）

### 【场景 1】从头开始（最常用）

> "用 cropwat-inputs 技能，从 `E:\我的数据\气象数据.csv` 生成 CROPWAT 工作文件。
> 经纬度海拔我没有，你上网查。"

说明：DSH 会生成到桌面 `CROPWRT工作文件\` 文件夹，并顺手生成一张数据明细表放桌面。

如果你源数据里已经有经纬度海拔，就不用说后半句。
**如果你的源数据存在多个文件或网页中，在这里告诉 DSH。**

### 【场景 2】跑测算（核心，最常用）

> "用 cropwat 技能跑水足迹测算。
> 地点：北京、上海、广州
> 作物：小麦、玉米、水稻
> 年份：2013 到 2024
> 播种日期：小麦 5月5日，玉米 4月25日，水稻 5月10日"

> **【重点】这五样说齐了 DSH 就能开始，少哪样它会问你。**

### 【场景 3】先看看要跑多少组、要多久

> "先给我估算一下参数和预计用时，别真跑。"

### 【场景 4】中断了要接着跑

> "上次跑到一半断了，接着跑。"

说明：已经跑完的会自动跳过，不用重新来。

### 【场景 5】只要某一张表

> "重新生成一下数据明细表。"
> "把结果表重新导出一遍。"

### 【场景 6】先小规模试试

> "先只跑北京 2013 年的小麦，看看对不对。"

---

## 五、跑起来之后你要做什么

### 【最重要的一条】坐着别动

**测算期间，不要碰键盘鼠标，不要切换窗口，也不要让别的窗口盖住 CROPWAT。**

原因：程序是用**"模拟鼠标点击和按键"**来操作 CROPWAT 的。你一碰，它就可能点错地方，
那一组的结果就是错的，**而且【不会报错】，悄悄地就错了**。请离开电脑，或者至少别动键鼠。

### 要多久？

**每组大约 85 秒。**

```
预计总用时 = 地点数 × 作物数 × 年数 × 85 秒
```

DSH 开跑前会先把参数和预计用时列给你看，你确认了它才动手。

### 中途断了也没关系

结果是一组一组往硬盘上写的。重新说一遍同样的要求就行，**已经跑完的会自动跳过**。

---

## 六、结果在哪里

### 桌面 `CROPWRT工作文件\`

这里面是生成出来的工作文件：

```
Climate\<地点>\<地点>_<年份>.PEM    气候文件
Rain\<地点>\<地点>_<年份>.CRM       降水文件
Crop\<作物>.CRO                     作物参数
Soil\<土壤>.SOI                     土壤参数
```

每个地点一个子文件夹，年份都在里面。

> **【注意】站名只能用英文字母。** CROPWAT 的文件格式装不下中文，所以中文站名会被自动转成英文
> （如 `呼和浩特 → Hohhot_City`、`锡林郭勒 → Xilinhot_City`）。

### 桌面 `CROPWAT输入数据明细.xlsx`

生成文件时用到的**每一个数**。

### 桌面 `cropwrt计算结果.xlsx`

三个工作表：

| 工作表 | 内容 |
|---|---|
| **宽表** | 一行一个"地方-年份"，同一地方的年份挨着 |
| **长表** | 一行一个观测，可以直接做面板回归 |
| **说明** | 指标定义、公式、单位换算 |

每种作物三列：

| 列 | 含义 |
|---|---|
| **ETc** | 作物需水量（毫米） |
| **Eff** | 有效降水量（毫米） |
| **Irr** | 灌溉需水量（毫米） |

---

## 七、注意事项（坑都在这里）

**1. 跑的时候别碰键鼠、别切窗口**
后果：播种日期可能没输进去，结果错但不报错。

**2. 播种日期必须说清楚，每种作物分开**
后果：全用同一天会系统性偏差。

**2b. 自动查坐标时，一定核对一下**
重名很常见（包头在内蒙古也在海南）；而且查回来的是**行政中心点**，不一定是你那个气象站的点。
**海拔尤其要看清，它影响 ETo 计算。** 程序会生成"站点坐标表.csv"，改完让它用 `--sites` 读回。

**3. 日期格式是"某月某日"**
直接说"5月5日"就行，DSH 会转成程序要求的格式。

**4. 气温要用月平均值，不是月极值**
后果：用极值 ETo 虚高约 19%。

**5. 风速单位容易错**
文件里是"公里/天"，公式里是"米/秒"。**监督让 DSH 换算。**

**6. 日照单位是"小时/天"**
说成"小时/月"会让辐射虚高约 8 倍。

**7. 数据明细表一定要留着**
这是你论文数据来源的证据。

**8. 云服务器的特殊使用**

由于该技能运行**依赖窗口**，而云服务器没有显示器，所以推荐安装**虚拟显示器**。
Windows Server 系列操作系统，实测较为好用的虚拟显示器为 **`usbmmidd_v2`**，
参考该网站：<https://blog.csdn.net/toooooop8/article/details/158696996>。

**该软件的安装包与离线版网页均在本仓库的 [`附件`](附件/) 文件夹下。**

> **注意：此软件仅支持 Windows Server 2019**，若你的系统版本不是这个版本，请重装。

同样由于该技能运行依赖窗口，**若使用远程桌面连接云服务器，窗口将出现在远程桌面上**
（远程桌面对于 Windows 来说也是一个显示器），因此**当远程桌面断开时，无法继续测算**。

解决方案：安装虚拟显示器后，使用 **Sunshine / ToDesk / 向日葵** 等远程操纵软件，
此时其将投射虚拟显示器画面，可跳出此限制。

> **一定要关闭远程操纵软件的自动锁屏功能，防止中断。
> 同时，Windows 系统也要关闭自动锁屏、休眠等功能。**

---

## 八、常见问题怎么说

| 你想干什么 | 就这么说 |
|---|---|
| 不知道从哪开始 | "我要用 CROPWAT 算作物需水量，你告诉我该准备什么。" |
| 不知道数据格式对不对 | "帮我看看这份气象数据格式对不对，能不能用。" |
| 经纬度、海拔不知道填多少 | "坐标你上网查，公开信息。" |
| 查回来的坐标不放心 | "把这些站点的坐标列出来我核对一下。" |
| 不知道播种日期填多少 | "内蒙古小麦一般什么时候播种？" |
| 想看现在跑到哪了 | "现在跑到第几组了？" |
| 想停下来 | "停。" |
| 结果看不懂 | "帮我解释一下结果表里 ETc、Eff、Irr 分别是什么。" |
| 想画图 | "用结果表画各盟市 ETc 的对比图。" |
| 想换台电脑 | "我要换电脑，需要带走哪些文件？" |

---

## 九、一句话总结

1. **装一次** —— 两个文件夹拖进 `%USERPROFILE%\.dsh\skills`
2. **准备数据** —— 气象数据表 + 播种日期 + 地点/作物/年份
   （经纬度海拔不用自己填，让 DSH 查）
3. **跟 DSH 说** —— 用中文说清楚要什么，四要素别漏
4. **跑的时候走开**，别碰键鼠
5. **结果在桌面** —— `CROPWRT工作文件\`、`CROPWAT输入数据明细.xlsx`、`cropwrt计算结果.xlsx`

**有拿不准的，直接问 DSH，它比这份说明懂得多。**

---

## 仓库内容

```
.
├── README.md                      本文件
├── LICENSE                        MIT
├── .gitignore
├── cropwat/                       技能一：驱动测算
│   ├── SKILL.md                     操作指南（代理加载的入口，含全部规范与踩过的坑）
│   ├── cropwat_lib.py               驱动库（Win32 消息驱动 CROPWAT）
│   ├── cropwat_run.py               命令行工具 check / smoke / run / excel
│   ├── demo_cropwat.py              示例
│   └── 安装说明.md
├── cropwat-inputs/                技能二：生成输入文件
│   ├── SKILL.md                     完整指南：字节格式、FAO-56 公式、单位换算、数据源陷阱、校验
│   ├── cropwat_inputs.py            库 + 命令行（7 个子命令）
│   └── 安装说明.md
└── 附件/                          云服务器用的虚拟显示器
    ├── usbmmidd_v2.zip
    └── usbmmidd_v2使用方法.pdf
```

### 依赖

| 条件 | 说明 |
|---|---|
| **Windows** | CROPWAT 8.0 是 Win32 图形程序 |
| **CROPWAT 8.0** | 免费，FAO 官网可下载。**安装位置不用配**，技能会自动查找（注册表 → 环境变量 → 常见目录 → 磁盘扫描 → PATH） |
| **Python 3.9+** | 读写 `.PEM`/`.CRM`/`.SOI` 只用标准库 |
| **openpyxl** | 生成结果 Excel 和明细表用（`pip install openpyxl`） |
| 网络 | 只有经纬度自动查询需要；生成后会缓存，之后可离线 |

驱动 CROPWAT 需要**完整的文件/注册表权限**（CROPWAT 启动时会写注册表）。

### 这两个技能不含任何项目数据

地点、年份、作物、播种期全靠参数传入，可以直接用于**任何地区、任何作物**。

---

## English summary

Two [DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness) **skills** that automate
FAO **CROPWAT 8.0** — a Windows GUI program with no CLI and no API.

| Skill | Role |
|---|---|
| `cropwat-inputs` | **Build the inputs.** Computes reference ET (FAO-56 Penman-Monteith) from raw weather data and writes CROPWAT's fixed-width `.PEM` climate and `.CRM` rain files; also supplies FAO `.CRO`/`.SOI` files and a rice-ready soil. CLI: `inspect` `eto` `geocode` `detail` `build` `crops` `soil` |
| `cropwat` | **Run the model.** Drives the CROPWAT GUI via Win32 messages and simulated input to compute ETc, effective rainfall and irrigation requirement for many site × crop × year combinations, writing a formatted Excel workbook. CLI: `check` `smoke` `run` `excel` |

**Requirements**: Windows, CROPWAT 8.0 (auto-discovered), Python 3.9+, openpyxl.<br>
**Install**: copy both folders into `%USERPROFILE%\.dsh\skills\`.

Both skills are project-agnostic — no hard-coded sites, years or crops; CROPWAT's install path is
found automatically; coordinates are looked up online (OpenStreetMap + Open-Meteo) when missing.
The `SKILL.md` files document the exact byte layouts, the FAO-56 formulas, the unit traps, and how
to validate the output.

> **Headless servers**: the driver needs a window. See [附件/](附件/) for a virtual display driver,
> and note the remote-desktop caveat described in section 七.8.

---

## 许可证

[MIT](LICENSE)

CROPWAT 8.0 本身由 **FAO** 开发并免费提供，**不在本仓库内**，请从其官方渠道获取。
