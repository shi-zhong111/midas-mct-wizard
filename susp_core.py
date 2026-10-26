# -*- coding: utf-8 -*-
r"""悬索桥流程的引擎（纯计算，不依赖 tkinter，可以被 python -c 直接调用）。

课程背景：给《桥梁结构电算》的学生用「一步一步填数值」的方式建悬索桥模型，
最后生成 MIDAS Civil NX 的 .mct 命令文件（MIDAS 里：文件 → 导入 → MCT 命令文件）。

模型约定（二维 X-Z 平面，所有节点 Y = 0）：
  * 主缆：抛物线。主跨 z(x) = z_top - f·(1 - (2x/L)²)，f = (f/L)·L；
    边跨取「与主跨在塔顶相切、并落到桥面标高处锚固点」的抛物线:
        z(u) = z_top - s·u + a·u²，s = 4f/L，a = (z_deck - z_top + s·Ls) / Ls²
    （u 从塔顶向锚碇方向量，u=Ls 处 z=z_deck，即主缆锚在加劲梁端部）。
  * 主缆单元用 TENSTR 的 Cable 子类型，写法严格照本机 MIDAS 自己导出的
    C:\Midas\work\samples\CableStayed.mct：
        iEL, TENSTR, iMAT, iPRO, iN1, iN2, ANGLE, iSUB=3, EXVAL=无应力索长, EXVAL2=3
    共 10 个字段（样本里每行也是 10 个）。也可以切到普通 TRUSS 桁架单元
    （8 个字段，和样本里的 BEAM 行同构），给老版本 MIDAS 兜底。
  * 吊杆：主跨沿主缆均匀布置（间距 ds）、边跨每侧按「边跨吊杆数」均匀布置；
    吊杆竖直，上端接主缆节点、下端接同一 x 的加劲梁节点。
  * 加劲梁 / 桥塔：BEAM 单元。桥塔从 z_base 到 z_top 分 n_tower 段，
    并在 z_deck 处强制放一个节点，用来表达「塔梁连接」。
  * 边界：塔底固结；主缆锚固点（= 加劲梁两端节点）固结；
    塔梁连接三选一：固结（*RIGIDLINK）/ 竖向支承（*CONSTRAINT）/ 漂浮；
    主缆过塔顶二选一：共用塔顶节点 / 塔顶节点 + *RIGIDLINK 主从（鞍座）。
  * 荷载：自重 *SELFWEIGHT、二期恒载 *BEAMLOAD（均布）、初始索力 *PRETENSION。
    主缆初拉力按恒载水平分力 H = w·L²/(8f) 估算，再按各索段倾角分摊
    （T = H/cosθ）；吊杆初拉力按分担的恒载估算，可以逐根改。

默认算例尺寸：老师第三讲 3.8 悬索桥只讲了建模方法、没给具体算例尺寸，所以这里按
常规悬索桥比例取值（注释里写清楚，学生可以照着改）：
    主跨 L = 500 m，每侧边跨 150 m（L/3.3），矢跨比 1/10（矢高 50 m），
    主跨吊杆间距 20 m（25 个节间）、边跨每侧 6 根吊杆，
    塔底 0 m / 桥面 20 m / 塔顶 100 m（桥面以上 80 m ≈ L/6.3），
    塔分 5 段（正好在桥面标高处有一个节点），加劲梁每个吊杆间距内 1 段。
"""

import datetime
import math
import os

import wiz_common as wc

CODE = "susp"
LABEL = "悬索桥（主缆 · 吊杆 · 加劲梁 · 桥塔）"

# ---------------------------------------------------------------- 名字约定
# 组名 / 工况名一律用 ASCII：最稳，也方便学生在 MIDAS 里对照查找。
G_GIRDER = "Girder"
G_CABLE = "MainCable"
G_HANGER = "Hanger"
G_TOWER = "Tower"
G_ANCHOR = "Anchor"
B_FIXED = "Fixed"
B_BEARING = "Bearing"
B_RIGID = "RigidLink"
L_SELF = "SelfWeight"
L_ADD = "AddDead"
L_PRET = "TForce"

MAT_GIRDER, MAT_TOWER, MAT_CABLE, MAT_HANGER = 1, 2, 3, 4
MAT_ROWS = [
    # (编号, 类型, 名字, E 的 susp 键, ν 的 susp 键, 容重键, 线膨胀系数, 中文说明)
    (MAT_GIRDER, "STEEL", "Q345", "girder_E", "girder_nu", "girder_den", "0.000012", "加劲梁"),
    (MAT_TOWER, "CONC", "C50", "tower_E", "tower_nu", "tower_den", "0.00001", "桥塔"),
    (MAT_CABLE, "STEEL", "Cable", "cable_E", "cable_nu", "cable_den", "0.000012", "主缆"),
    (MAT_HANGER, "STEEL", "Hanger", "hanger_E", "hanger_nu", "hanger_den", "0.000012", "吊杆"),
]

SADDLE_SAME = "same"          # 主缆与塔顶共用一个节点（默认，最省事）
SADDLE_RIGID = "rigid"        # 塔顶节点 + 主缆从节点，用 *RIGIDLINK 连（模拟鞍座）
TOWER_BEAM_FIXED = "fixed"    # 塔梁固结
TOWER_BEAM_BEARING = "bearing"  # 塔梁竖向支承（活动支座）
TOWER_BEAM_FLOATING = "floating"  # 塔梁不连（漂浮体系）
CABLE_TENSTR = "tenstr"       # 主缆/吊杆用 TENSTR 索单元（照 CableStayed.mct）
CABLE_TRUSS = "truss"         # 主缆/吊杆用普通 TRUSS 桁架单元（兜底写法）

TOWER_BEAM_CHOICES = [
    (TOWER_BEAM_FIXED, "固结（塔与梁用 *RIGIDLINK 连成整体）"),
    (TOWER_BEAM_BEARING, "竖向支承（梁在塔处只受压，可纵向滑动）"),
    (TOWER_BEAM_FLOATING, "漂浮（塔与梁不连，桥面只靠吊杆吊住）"),
]
SADDLE_CHOICES = [
    (SADDLE_SAME, "共用节点（主缆直接过塔顶）"),
    (SADDLE_RIGID, "刚性连接（塔顶主节点 + 主缆从节点，*RIGIDLINK）"),
]
CABLE_ELEM_CHOICES = [
    (CABLE_TENSTR, "TENSTR 索单元（Cable 子类型，照 MIDAS 样本写法）"),
    (CABLE_TRUSS, "TRUSS 桁架单元（老版本 MIDAS 导入出问题时改用它）"),
]

# 默认参数（字符串，和梁桥流程一致：界面里存的就是字符串）
DEFAULT_SUSP = {
    # 第 1 步：桥型与几何
    "L": "500", "Ls": "150", "fr": "0.10", "ds": "20", "ns": "6",
    "z_base": "0", "z_deck": "20", "z_top": "100",
    "n_tower": "5", "n_girder": "1",
    # 第 3 步：材料、索、初拉力
    "girder_E": "2.06e8", "girder_nu": "0.3", "girder_den": "76.98",
    "tower_E": "3.45e7", "tower_nu": "0.2", "tower_den": "25",
    "cable_A": "0.25", "cable_E": "2.0e8", "cable_nu": "0.3", "cable_den": "76.98",
    "hanger_A": "0.008", "hanger_E": "2.0e8", "hanger_nu": "0.3", "hanger_den": "76.98",
    "q2": "50", "auto_H": True, "H": "", "hanger_factor": "1.0", "hanger_T": [],
    # 第 4 步：边界与荷载
    "tower_fix": True, "anchor_fix": True,
    "tower_beam": TOWER_BEAM_FIXED, "saddle": SADDLE_SAME,
    "cable_elem": CABLE_TENSTR,
    "selfweight": True, "dl2": True, "pretension": True, "hanger_ps": True,
    "lc_sw": "SW", "lc_dl2": "DL2", "lc_ps": "PS",
    # 第 5 步：成果
    "fname": "",
}

# 数值参数：键 -> (中文名, 默认值, 是否整数)
NUM_KEYS = [
    ("L", "主跨 L", 500.0, False),
    ("Ls", "单侧边跨长", 150.0, False),
    ("fr", "矢跨比 f/L", 0.10, False),
    ("ds", "主跨吊杆间距", 20.0, False),
    ("ns", "边跨吊杆数（每侧）", 6, True),
    ("z_base", "塔底标高", 0.0, False),
    ("z_deck", "桥面（加劲梁轴线）标高", 20.0, False),
    ("z_top", "塔顶标高", 100.0, False),
    ("n_tower", "桥塔分段数", 5, True),
    ("n_girder", "加劲梁分段数（每个吊杆间距内）", 1, True),
    ("q2", "二期恒载", 50.0, False),
    ("girder_E", "加劲梁弹性模量 E", 2.06e8, False),
    ("girder_nu", "加劲梁泊松比 ν", 0.3, False),
    ("girder_den", "加劲梁容重", 76.98, False),
    ("tower_E", "桥塔弹性模量 E", 3.45e7, False),
    ("tower_nu", "桥塔泊松比 ν", 0.2, False),
    ("tower_den", "桥塔容重", 25.0, False),
    ("cable_A", "主缆面积 A", 0.25, False),
    ("cable_E", "主缆弹性模量 E", 2.0e8, False),
    ("cable_nu", "主缆泊松比 ν", 0.3, False),
    ("cable_den", "主缆容重", 76.98, False),
    ("hanger_A", "吊杆面积 A", 0.008, False),
    ("hanger_E", "吊杆弹性模量 E", 2.0e8, False),
    ("hanger_nu", "吊杆泊松比 ν", 0.3, False),
    ("hanger_den", "吊杆容重", 76.98, False),
    ("hanger_factor", "吊杆初拉力系数", 1.0, False),
]
GEO_KEYS = ["L", "Ls", "fr", "ds", "ns", "z_base", "z_deck", "z_top", "n_tower", "n_girder"]
MAT_KEYS = ["q2", "girder_E", "girder_nu", "girder_den", "tower_E", "tower_nu", "tower_den",
            "cable_A", "cable_E", "cable_nu", "cable_den",
            "hanger_A", "hanger_E", "hanger_nu", "hanger_den",
            "hanger_factor", "H", "hanger_T"]


def _txt(v):
    return wc.txt(v)


def _parse_float(v, default=0.0):
    """宽松地转 float（界面摘要里用；解析错误不抛异常）。"""
    try:
        return float(wc.txt(v).strip())
    except (TypeError, ValueError):
        return default


def _bool(v, default=False):
    """把界面上/存盘文件里的各种写法都认成 bool。"""
    if isinstance(v, bool):
        return v
    s = _txt(v).strip().lower()
    if s in ("1", "true", "yes", "y", "on", "是", "勾"):
        return True
    if s in ("0", "false", "no", "n", "off", "否"):
        return False
    return default


def default_model():
    """本流程的初始模型（和第 1 步的默认算例一致）。"""
    s = dict(DEFAULT_SUSP)
    s["hanger_T"] = []
    return {
        "flow": CODE,
        "project": {"name": "", "outdir": "", "force": "KN", "dist": "M", "heat": "KJ",
                    "temp_unit": "C", "styp": "1", "smas": "1", "grav": "9.806",
                    "temper": "0", "compat": False, "alt_mct": False},
        "susp": s,
        "sections": [
            # 加劲梁：钢箱梁，高 3.0 m、宽 12.0 m（双索面 2D 化成一个索面）
            {"name": "加劲梁", "shape": "B", "dims": ["3.0", "12.0", "0.024", "0.032", "0.5", "0.032"],
             "offset": "CC", "shear": "YES", "warp": "NO", "file": "", "unit": "0.001", "props": None},
            # 桥塔：混凝土实心矩形 4 m × 3 m（塔柱截面）
            {"name": "桥塔", "shape": "SB", "dims": ["4.0", "3.0"],
             "offset": "CC", "shear": "YES", "warp": "NO", "file": "", "unit": "0.001", "props": None},
        ],
    }


def ensure_model(m):
    """把旧存盘里缺的键补齐（切流程 / 打开旧工程时用），返回 model 本身。"""
    if not isinstance(m, dict):
        return default_model()
    m.setdefault("flow", CODE)
    m.setdefault("project", default_model()["project"])
    s = m.setdefault("susp", {})
    for k, v in DEFAULT_SUSP.items():
        if k not in s:
            s[k] = list(v) if isinstance(v, list) else v
    if not isinstance(s.get("hanger_T"), list):
        s["hanger_T"] = []
    if not m.get("sections"):
        m["sections"] = default_model()["sections"]
    return m


# ======================================================================
# 参数解析 / 校验
# ======================================================================
def parse(m):
    """把 m["susp"] 里的字符串解析成数字/选项。

    返回 (P, errs)：P 是参数 dict；errs 是 {键: 中文说明}，只装「填的不是数字」这类错。
    这个函数永远不抛异常（界面每一步都要能调它）。
    """
    s = m.get("susp") or {}
    errs = {}
    P = {}
    default = dict((k, v) for k, _n, v, _i in [(a, b, c, d) for a, b, c, d in NUM_KEYS])
    for key, name, dflt, is_int in NUM_KEYS:
        raw = s.get(key, dflt)
        txt = _txt(raw).strip()
        if is_int:
            try:
                P[key] = int(round(float(txt)))
            except (TypeError, ValueError):
                errs[key] = "「%s」要填整数（现在是「%s」）。" % (name, txt or "空")
                P[key] = int(dflt)
        else:
            try:
                P[key] = float(txt)
            except (TypeError, ValueError):
                errs[key] = "「%s」要填数字（现在是「%s」）。（单位：长度 m、力 kN、模量 kN/m²）"
                P[key] = float(dflt)
    # 选项
    P["auto_H"] = _bool(s.get("auto_H", True), True)
    P["H_manual"] = None
    hv = _txt(s.get("H", "")).strip()
    if not P["auto_H"]:
        if not hv:
            errs["H"] = "已选择手填水平分力 H，但 H 是空的。"
        else:
            try:
                P["H_manual"] = float(hv)
            except ValueError:
                errs["H"] = "「手填水平分力 H」要填数字（kN），或者勾上「自动估算」。"
    P["tower_fix"] = _bool(s.get("tower_fix", True), True)
    P["anchor_fix"] = _bool(s.get("anchor_fix", True), True)
    P["tower_beam"] = _txt(s.get("tower_beam") or TOWER_BEAM_FIXED).strip() or TOWER_BEAM_FIXED
    P["saddle"] = _txt(s.get("saddle") or SADDLE_SAME).strip() or SADDLE_SAME
    P["cable_elem"] = _txt(s.get("cable_elem") or CABLE_TENSTR).strip() or CABLE_TENSTR
    P["selfweight"] = _bool(s.get("selfweight", True), True)
    P["dl2"] = _bool(s.get("dl2", True), True)
    P["pretension"] = _bool(s.get("pretension", True), True)
    P["hanger_ps"] = _bool(s.get("hanger_ps", True), True)
    P["lc_sw"] = wc.aname(s.get("lc_sw") or "SW", "SW")
    P["lc_dl2"] = wc.aname(s.get("lc_dl2") or "DL2", "DL2")
    P["lc_ps"] = wc.aname(s.get("lc_ps") or "PS", "PS")
    P["fname"] = _txt(s.get("fname") or "")
    # 吊杆初拉力逐根自定义（空串 = 用估算值）
    over = []
    raw = s.get("hanger_T")
    if isinstance(raw, list):
        for i, v in enumerate(raw):
            t = _txt(v).strip()
            if not t:
                over.append(None)
                continue
            try:
                val = float(t)
                over.append(val if val > 0 else None)
            except ValueError:
                over.append(None)
                if "hanger_T" not in errs:
                    errs["hanger_T"] = "第 %d 根吊杆的初拉力「%s」不是数字（留空 = 用估算值）。" % (i + 1, t)
    P["hanger_T_override"] = over
    return P, errs


# ---------------------------------------------------------------- 几何
def _z_cable(x, P):
    """主缆标高（抛物线）。主跨与边跨在塔顶相切。"""
    L, Ls = P["L"], P["Ls"]
    f = P["fr"] * L
    z_top, z_deck = P["z_top"], P["z_deck"]
    if abs(x) <= L / 2.0 + 1e-9:
        t = 2.0 * x / L
        return z_top - f * (1.0 - t * t)
    u = abs(x) - L / 2.0
    if Ls <= 0:
        return z_top
    s = 4.0 * f / L
    a = (z_deck - z_top + s * Ls) / (Ls * Ls)
    return z_top - s * u + a * u * u


def _side_curve_ok(P):
    """边跨主缆会不会低于桥面（边跨太长 / 塔太矮时会出现）。返回 (是否OK, 最低点标高, 说明)。"""
    L, Ls = P["L"], P["Ls"]
    f = P["fr"] * L
    z_top, z_deck = P["z_top"], P["z_deck"]
    if Ls <= 0:
        return True, z_top, ""
    s = 4.0 * f / L
    a = (z_deck - z_top + s * Ls) / (Ls * Ls)
    if a <= 0:
        return True, z_deck, ""
    u = s / (2.0 * a)
    if u <= 0 or u >= Ls:
        return True, z_deck, ""
    zmin = z_top - s * u + a * u * u
    return (zmin > z_deck + 1e-6), zmin, ""


def _tower_levels(z_base, z_deck, z_top, n_tower):
    """塔身标高：均分 n_tower 段，并保证 z_deck 处一定有一个节点（塔梁连接用）。"""
    n = max(1, int(n_tower))
    levels = [z_base + (z_top - z_base) * k / float(n) for k in range(n + 1)]
    levels[-1] = z_top
    if not any(abs(lv - z_deck) <= 1e-6 * max(1.0, abs(z_top)) for lv in levels):
        levels.append(z_deck)
        levels.sort()
    # 去掉太近的重复点
    out = []
    for lv in levels:
        if not out or abs(lv - out[-1]) > 1e-6:
            out.append(lv)
    return out


def _dedupe(xs, tol=1e-9):
    out = []
    for x in sorted(xs):
        if not out or abs(x - out[-1]) > tol:
            out.append(x)
    return out


def _ranges(ids):
    """[1,2,3,5] -> '1to3 5'（MIDAS 的 *GROUP 节点/单元列表写法）。"""
    ids = sorted(set(int(i) for i in ids))
    if not ids:
        return ""
    out = []
    start = prev = ids[0]
    for v in ids[1:]:
        if v == prev + 1:
            prev = v
            continue
        out.append(str(start) if start == prev else "%dto%d" % (start, prev))
        start = prev = v
    out.append(str(start) if start == prev else "%dto%d" % (start, prev))
    return " ".join(out)


def _ids_text(ids):
    return " ".join(str(int(i)) for i in ids)


def _geometry(P, n_secs):
    """按参数生成节点/单元/边界（纯几何，不检查参数合法性——合法性由 issues() 负责）。"""
    L, Ls = P["L"], P["Ls"]
    f = P["fr"] * L
    ds_in = P["ds"]
    n_main = max(1, int(round(L / ds_in))) if ds_in > 0 else 1
    ds = L / n_main if n_main else 0.0
    ns = max(0, int(P["ns"]))
    side_ds = Ls / (ns + 1) if ns >= 0 else Ls
    z_base, z_deck, z_top = P["z_base"], P["z_deck"], P["z_top"]
    n_girder = max(1, int(P["n_girder"]))
    cable_type = "TENSTR" if P["cable_elem"] == CABLE_TENSTR else "TRUSS"
    sect_girder, sect_tower = 1, 2
    sect_cable, sect_hanger = n_secs + 1, n_secs + 2

    # ---- 平面 x 坐标（先放基准点：锚碇、塔、主跨吊杆、边跨吊杆）----
    base_x = [-(L / 2.0 + Ls)]
    for k in range(ns, 0, -1):
        base_x.append(-(L / 2.0) - k * side_ds)
    base_x.append(-L / 2.0)
    for i in range(1, n_main):
        base_x.append(-L / 2.0 + i * ds)
    base_x.append(L / 2.0)
    for k in range(1, ns + 1):
        base_x.append(L / 2.0 + k * side_ds)
    base_x.append(L / 2.0 + Ls)
    base_x = _dedupe(base_x)

    xs = list(base_x)
    if n_girder > 1:                                # 加劲梁再细分
        sub = []
        for a, b in zip(base_x, base_x[1:]):
            for k in range(n_girder):
                sub.append(a + (b - a) * k / float(n_girder))
        sub.append(base_x[-1])
        xs = _dedupe(sub)

    hanger_x = []
    for k in range(ns, 0, -1):
        hanger_x.append(-(L / 2.0) - k * side_ds)
    for i in range(1, n_main):
        hanger_x.append(-L / 2.0 + i * ds)
    for k in range(1, ns + 1):
        hanger_x.append(L / 2.0 + k * side_ds)
    hanger_x = _dedupe(hanger_x)

    kx = lambda v: round(v, 6)
    nodes = []

    def add_node(x, z, role):
        nodes.append({"no": len(nodes) + 1, "x": x, "z": z, "role": role})
        return nodes[-1]["no"]

    # ---- 1) 加劲梁节点（含两端锚碇节点、塔处节点）----
    deck_id = {}
    for x in xs:
        deck_id[kx(x)] = add_node(x, z_deck, "deck")

    # ---- 2) 主缆上的吊杆节点（在梁节点正上方）----
    cable_id = {}
    for x in hanger_x:
        cable_id[kx(x)] = add_node(x, _z_cable(x, P), "cable")

    # ---- 3) 桥塔节点（塔底 → 塔顶，z_deck 处必有节点）----
    towers = {}
    for sym, tx in (("L", -L / 2.0), ("R", L / 2.0)):
        levels = _tower_levels(z_base, z_deck, z_top, P["n_tower"])
        ids = [add_node(tx, lv, "tower") for lv in levels]
        i_deck = min(range(len(levels)), key=lambda i: abs(levels[i] - z_deck))
        towers[sym] = {"x": tx, "levels": levels, "ids": ids,
                       "base": ids[0], "top": ids[-1],
                       "deck_level": levels[i_deck], "deck_node": ids[i_deck]}

    # ---- 4) 主缆过塔顶：共用塔顶节点 or 单独一个从节点（鞍座）----
    saddle_id = {}
    for sym in ("L", "R"):
        saddle_id[sym] = towers[sym]["top"]
    if P["saddle"] == SADDLE_RIGID:
        for sym in ("L", "R"):
            saddle_id[sym] = add_node(towers[sym]["x"], z_top, "cable")

    # ---- 主缆节点链：左锚碇 → 左边跨吊杆 → 左塔顶 → 主跨吊杆 → 右塔顶 → 右边跨 → 右锚碇 ----
    anchor_l = deck_id[kx(-(L / 2.0 + Ls))]
    anchor_r = deck_id[kx(L / 2.0 + Ls)]
    chain = [anchor_l]
    for k in range(ns, 0, -1):
        chain.append(cable_id[kx(-(L / 2.0) - k * side_ds)])
    chain.append(saddle_id["L"])
    for i in range(1, n_main):
        chain.append(cable_id[kx(-L / 2.0 + i * ds)])
    chain.append(saddle_id["R"])
    for k in range(1, ns + 1):
        chain.append(cable_id[kx(L / 2.0 + k * side_ds)])
    chain.append(anchor_r)

    # ---- 吊杆：上端主缆节点、下端梁节点 ----
    hangers = []
    for x in hanger_x:
        hangers.append({"x": x, "cable": cable_id[kx(x)], "deck": deck_id[kx(x)],
                        "len": nodes[cable_id[kx(x)] - 1]["z"] - z_deck,
                        "main": abs(x) <= L / 2.0 + 1e-9})

    # ---- 单元 ----
    elems = []

    def add_el(etype, mat, sect, i, j, role, lu=None, slope=None, hanger_index=None):
        elems.append({"no": len(elems) + 1, "type": etype, "mat": mat, "sect": sect,
                      "i": i, "j": j, "role": role,
                      "lu": lu, "slope": slope, "hanger_index": hanger_index})

    # 1) 加劲梁（BEAM）
    for a, b in zip(xs, xs[1:]):
        add_el("BEAM", MAT_GIRDER, sect_girder, deck_id[kx(a)], deck_id[kx(b)], "girder")
    # 2) 主缆（TENSTR/TRUSS）
    for a, b in zip(chain, chain[1:]):
        na, nb = nodes[a - 1], nodes[b - 1]
        dx, dz = nb["x"] - na["x"], nb["z"] - na["z"]
        ln = math.hypot(dx, dz)
        add_el(cable_type, MAT_CABLE, sect_cable, a, b, "cable",
               lu=ln, slope=(dz / dx if abs(dx) > 1e-12 else 0.0))
    # 3) 吊杆（TENSTR/TRUSS）
    for h in hangers:
        add_el(cable_type, MAT_HANGER, sect_hanger, h["cable"], h["deck"], "hanger",
               lu=max(h["len"], 0.0), hanger_index=len([e for e in elems if e["role"] == "hanger"]))
    # 4) 桥塔（BEAM）
    for sym in ("L", "R"):
        ids = towers[sym]["ids"]
        for a, b in zip(ids, ids[1:]):
            add_el("BEAM", MAT_TOWER, sect_tower, a, b, "tower")

    # ---- 边界 ----
    supports = []
    if P["tower_fix"]:
        supports.append({"nodes": [towers["L"]["base"], towers["R"]["base"]],
                         "code": "111111", "group": B_FIXED, "why": "塔底固结"})
    if P["anchor_fix"]:
        supports.append({"nodes": [anchor_l, anchor_r],
                         "code": "111111", "group": B_FIXED, "why": "主缆锚固点（=加劲梁两端）固结"})
    bearing_nodes = []
    if P["tower_beam"] == TOWER_BEAM_BEARING:
        bearing_nodes = [deck_id[kx(-L / 2.0)], deck_id[kx(L / 2.0)]]
        supports.append({"nodes": bearing_nodes, "code": "011000", "group": B_BEARING,
                         "why": "塔梁竖向支承（Dy+Dz）"})
    links = []
    if P["saddle"] == SADDLE_RIGID:
        for sym in ("L", "R"):
            links.append({"master": towers[sym]["top"], "slaves": [saddle_id[sym]],
                          "group": B_RIGID, "why": "塔顶鞍座：主缆从节点"})
    if P["tower_beam"] == TOWER_BEAM_FIXED:
        for sym, tx in (("L", -L / 2.0), ("R", L / 2.0)):
            links.append({"master": towers[sym]["deck_node"],
                          "slaves": [deck_id[kx(tx)]],
                          "group": B_RIGID, "why": "塔梁固结"})

    # ---- 统计 ----
    def _by_role(role):
        return [e for e in elems if e["role"] == role]

    e_girder = _by_role("girder")
    e_cable = _by_role("cable")
    e_hanger = _by_role("hanger")
    e_tower = _by_role("tower")
    cable_length = sum((e["lu"] or 0.0) for e in e_cable)
    hanger_lens = [h["len"] for h in hangers]
    mid = min(range(len(hangers)), key=lambda i: abs(hangers[i]["x"])) if hangers else None
    return {
        "P": P,
        "f": f, "n_main": n_main, "ds": ds, "ns": ns, "side_ds": side_ds,
        "n_girder": n_girder, "n_tower": len(_tower_levels(z_base, z_deck, z_top, P["n_tower"])) - 1,
        "xs": xs, "hanger_x": hanger_x,
        "nodes": nodes, "deck_id": deck_id, "cable_id": cable_id,
        "towers": towers, "saddle_id": saddle_id,
        "anchor_l": anchor_l, "anchor_r": anchor_r,
        "chain": chain, "hangers": hangers, "elements": elems,
        "supports": supports, "links": links, "bearing_nodes": bearing_nodes,
        "sections": {"girder": sect_girder, "tower": sect_tower,
                     "cable": sect_cable, "hanger": sect_hanger},
        "counts": {"nodes": len(nodes), "elements": len(elems),
                   "girder": len(e_girder), "cable": len(e_cable),
                   "hanger": len(e_hanger), "tower": len(e_tower)},
        "cable_length": cable_length,
        "hanger_len_min": min(hanger_lens) if hanger_lens else 0.0,
        "hanger_len_max": max(hanger_lens) if hanger_lens else 0.0,
        "hanger_mid": (hanger_lens[mid] if mid is not None else 0.0),
        "cable_type": cable_type,
        "sect_cable": sect_cable, "sect_hanger": sect_hanger,
        "z_base": z_base, "z_deck": z_deck, "z_top": z_top,
        "z_mid": z_top - f,
    }


def geometry(m):
    """给界面画图/测试用：解析 + 生成几何。参数不合法时抛 ValueError。"""
    P, perrs = parse(m)
    errs = list(perrs.values()) + check_geo(P)
    if errs:
        raise ValueError("；".join(errs[:6]))
    return _geometry(P, len(m.get("sections") or []))


# ======================================================================
# 校验
# ======================================================================
def _pick(errs, keys):
    return [errs[k] for k in keys if k in errs]


def check_geo(P):
    out = []
    L, Ls, fr = P["L"], P["Ls"], P["fr"]
    if L <= 0:
        out.append("主跨 L 要大于 0（单位 m）。")
    if Ls <= 0:
        out.append("单侧边跨长 Ls 要大于 0（悬索桥一般边跨是主跨的 1/4 ~ 1/2）。")
    if fr <= 0:
        out.append("矢跨比 f/L 要大于 0（常用 1/12 ~ 1/8）。")
    elif fr < 0.03 or fr > 0.25:
        out.append("矢跨比 f/L = %g 不合理（常用 1/12 ~ 1/8，即 0.08 ~ 0.12）。" % fr)
    z_base, z_deck, z_top = P["z_base"], P["z_deck"], P["z_top"]
    if z_top <= z_deck:
        out.append("塔顶标高（%g m）要高于桥面标高（%g m）。" % (z_top, z_deck))
    if z_deck <= z_base:
        out.append("桥面标高（%g m）要高于塔底标高（%g m）。" % (z_deck, z_base))
    if L > 0 and fr > 0 and z_top > z_deck:
        f = fr * L
        if z_top - f <= z_deck + 1e-6:
            out.append("矢高 f = %.1f m 太大：塔顶标高 %.1f - f = %.1f m 不高于桥面 %.1f m，"
                       "跨中吊杆会变成 0 或负数。请加高塔顶或减小矢跨比。"
                       % (f, z_top, z_top - f, z_deck))
        ok, zmin, _note = _side_curve_ok(P)
        if not ok:
            out.append("边跨太长/塔太矮：边跨主缆最低点 %.1f m 低于桥面 %.1f m，"
                       "边跨吊杆会出现负长度。请减小边跨长或加高塔顶。" % (zmin, z_deck))
    if P["ds"] <= 0:
        out.append("主跨吊杆间距要大于 0。")
    elif L > 0:
        n_main = int(round(L / P["ds"]))
        if n_main < 4:
            out.append("按现在的吊杆间距，主跨只分得出 %d 个节间（至少 4 个）。" % n_main)
        elif n_main > 200:
            out.append("主跨要分 %d 个节间，太多了（最多 200）：把吊杆间距加大。" % n_main)
    if P["ns"] < 0:
        out.append("边跨吊杆数不能是负数。")
    elif P["ns"] > 60:
        out.append("每侧边跨吊杆数 %d 太多（最多 60）。" % P["ns"])
    if P["n_tower"] < 1:
        out.append("桥塔分段数至少 1 段。")
    elif P["n_tower"] > 40:
        out.append("桥塔分段数 %d 太多（最多 40）。" % P["n_tower"])
    if P["n_girder"] < 1:
        out.append("加劲梁分段数至少 1。")
    elif P["n_girder"] > 8:
        out.append("加劲梁每个吊杆间距内最多分 8 段。")
    return out


def _shape_area(sec):
    """按截面形状估算面积（m²）——只用来估算恒载/初拉力，不写进 MCT（MIDAS 自己算）。"""
    code = wc.shape_code_of(sec)
    dims = sec.get("dims") or []

    def d(k, default=0.0):
        try:
            return float(_txt(dims[k]).strip())
        except (IndexError, ValueError):
            return default

    if code == wc.CAD_SHAPE:
        pr = sec.get("props") or {}
        try:
            return float(pr.get("A") or 0.0)
        except (TypeError, ValueError):
            return 0.0
    if code == "SB":
        return d(0) * d(1)
    if code == "SR":
        r = d(0) / 2.0
        return math.pi * r * r
    if code == "B":                      # 箱形：H, B, tw, tf1, C, tf2（倒角按 C²/2×2 扣）
        H, B, tw, tf1, C, tf2 = d(0), d(1), d(2), d(3), d(4), d(5)
        inner = max(B - 2 * tw, 0.0) * max(H - tf1 - tf2, 0.0)
        return max(B * H - inner - C * C, 0.0)
    if code == "P":                      # 圆管：D, tw
        D, tw = d(0), d(1)
        r = D / 2.0
        return math.pi * (r * r - max(r - tw, 0.0) ** 2)
    if code == "H":
        return d(1) * d(3) + d(4) * d(5) + max(d(0) - d(3) - d(5), 0.0) * d(2)
    if code == "T":
        return d(1) * d(3) + max(d(0) - d(3), 0.0) * d(2)
    if code == "C":
        return d(1) * d(3) + d(4) * d(5) + max(d(0) - d(3) - d(5), 0.0) * d(2)
    if code == "L":
        return d(0) * d(3) + max(d(1) - d(3), 0.0) * d(2)
    return 0.0


def deck_area(m):
    secs = m.get("sections") or []
    return _shape_area(secs[0]) if secs else 0.0


def check_sect(m):
    out = []
    secs = m.get("sections") or []
    if len(secs) < 2:
        out.append("截面库里至少要有 2 个截面：第 1 个给加劲梁、第 2 个给桥塔（可以再导入 DXF 自定义截面）。")
        return out
    for i, name in ((0, "加劲梁"), (1, "桥塔")):
        sec = secs[i]
        code = wc.shape_code_of(sec)
        if code == wc.TS_SHAPE:
            out.append("本流程的%s不支持变截面（TS），请用等截面；要变截面请到梁桥流程里用。" % name)
            continue
        if code == wc.CAD_SHAPE:
            pr = sec.get("props") or {}
            try:
                a = float(pr.get("A") or 0)
            except (TypeError, ValueError):
                a = 0.0
            if a <= 0:
                out.append("%s用的是 DXF 自定义截面，但还没算出截面特性（请到第 2 步点「重新计算自定义截面」）。" % name)
            continue
        shape = wc.SHAPE_BY_CODE.get(code)
        if not shape:
            out.append("第 %d 个截面（%s）的形状「%s」不认识。" % (i + 1, name, code))
            continue
        dims = sec.get("dims") or []
        zero_ok = {"C", "r1", "r2"}                      # 倒角/圆角可以填 0
        for k, (key, zh) in enumerate(shape[2]):
            raw = _txt(dims[k]).strip() if k < len(dims) else ""
            try:
                v = float(raw)
            except (TypeError, ValueError):
                out.append("%s截面（%s）的「%s」要填数字（现在是「%s」）。" % (name, shape[1], zh, raw or "空"))
                continue
            if v < 0 or (v <= 0 and key not in zero_ok):
                out.append("%s截面（%s）的「%s」要大于 0（现在是 %g）。" % (name, shape[1], zh, v))
    if len(secs) > 2:
        pass    # 多出来的截面不参与本流程，照常写进 MCT 也不影响
    return out


def _ascii_ok(s):
    return all(ord(ch) < 128 for ch in _txt(s))


def check_mat(m, P):
    out = []
    for key, name, lo, hi in (
            ("girder_E", "加劲梁弹性模量 E", 1e6, 1e10),
            ("tower_E", "桥塔弹性模量 E", 1e6, 1e10),
            ("cable_E", "主缆弹性模量 E", 1e6, 1e10),
            ("hanger_E", "吊杆弹性模量 E", 1e6, 1e10)):
        v = P[key]
        if v <= 0:
            out.append("「%s」要大于 0（单位 kN/m²；钢材 2.06e8、混凝土 3.45e7）。" % name)
        elif not (lo <= v <= hi):
            out.append("「%s」= %g 不像正常值（kN/m²，钢材 2.06e8、混凝土 3.45e7）。" % (name, v))
    for key, name in (("girder_nu", "加劲梁泊松比"), ("tower_nu", "桥塔泊松比"),
                      ("cable_nu", "主缆泊松比"), ("hanger_nu", "吊杆泊松比")):
        v = P[key]
        if not (0.0 <= v < 0.5):
            out.append("「%s」要在 0 ~ 0.5 之间（钢材 0.3、混凝土 0.2）。" % name)
    for key, name in (("girder_den", "加劲梁容重"), ("tower_den", "桥塔容重"),
                      ("cable_den", "主缆容重"), ("hanger_den", "吊杆容重")):
        if P[key] <= 0:
            out.append("「%s」要大于 0（kN/m³；钢材约 77、混凝土 25）。" % name)
    if P["cable_A"] <= 0:
        out.append("主缆面积 A 要大于 0（m²，常规 0.1 ~ 0.5）。")
    elif P["cable_A"] > 5:
        out.append("主缆面积 A = %g m² 太大（常规 0.1 ~ 0.5）。" % P["cable_A"])
    if P["hanger_A"] <= 0:
        out.append("吊杆面积 A 要大于 0（m²，常规 0.002 ~ 0.02）。")
    elif P["hanger_A"] > 0.5:
        out.append("吊杆面积 A = %g m² 太大（常规 0.002 ~ 0.02）。" % P["hanger_A"])
    if P["q2"] < 0:
        out.append("二期恒载不能是负数（kN/m）。")
    if P["hanger_factor"] <= 0:
        out.append("吊杆初拉力系数要大于 0。")
    if not P["auto_H"]:
        if P["H_manual"] is None:
            out.append("手动初拉力：H 要填一个大于 0 的数字（kN）。")
        elif P["H_manual"] <= 0:
            out.append("手动水平分力 H 要大于 0（kN）。")
    # 估算不出来（比如加劲梁截面面积 0）时提醒
    if not check_sect(m):
        A = deck_area(m)
        if A <= 0:
            out.append("加劲梁截面面积算出来是 0，估算不了初拉力：请回第 2 步把加劲梁截面尺寸填好。")
    return out


def check_bc(m, P):
    out = []
    proj = m.get("project") or {}
    styp = _txt(proj.get("styp", "1")).strip() or "1"
    if styp != "1":
        out.append("悬索桥流程按二维 X-Z 平面建模：请把顶部「结构类型」改成「1 · 二维 X-Z 平面」。")
    if not P["tower_fix"]:
        out.append("塔底必须固结（不固结的话桥塔是悬臂/机构，一算就发散）。")
    if not P["anchor_fix"]:
        out.append("主缆锚固点必须约束（不约束的话主缆端部自由，模型是机构）。")
    if P["tower_beam"] not in (TOWER_BEAM_FIXED, TOWER_BEAM_BEARING, TOWER_BEAM_FLOATING):
        out.append("「塔梁连接方式」选项不认识：%s" % P["tower_beam"])
    if P["saddle"] not in (SADDLE_SAME, SADDLE_RIGID):
        out.append("「主缆过塔顶」选项不认识：%s" % P["saddle"])
    if P["cable_elem"] not in (CABLE_TENSTR, CABLE_TRUSS):
        out.append("「索单元类型」选项不认识：%s" % P["cable_elem"])
    if not (P["selfweight"] or P["dl2"] or P["pretension"]):
        out.append("至少勾一种荷载（自重 / 二期恒载 / 初始索力），否则算出来什么都没有。")
    used = []
    if P["selfweight"]:
        used.append(("自重工况名", P["lc_sw"]))
    if P["dl2"]:
        used.append(("二期恒载工况名", P["lc_dl2"]))
    if P["pretension"]:
        used.append(("初始索力工况名", P["lc_ps"]))
    names = []
    for label, nm in used:
        if not _txt(nm).strip():
            out.append("%s不能空。" % label)
        elif not _ascii_ok(nm):
            out.append("%s「%s」请用英文字母/数字（MCT 里工况名用 ASCII 最稳）。" % (label, nm))
        else:
            names.append(nm)
    if len(set(names)) != len(names):
        out.append("荷载工况名不能重复（现在是 %s）。" % "、".join(names))
    return out


def issues(m, sid):
    """这一步能不能进下一步：返回中文问题列表（空 = 可以）。不抛异常。"""
    P, perrs = parse(m)
    if sid == "susp_geo":
        return _pick(perrs, GEO_KEYS) + check_geo(P)
    if sid == "susp_sect":
        return check_sect(m)
    if sid == "susp_mat":
        return _pick(perrs, MAT_KEYS) + check_mat(m, P)
    if sid == "susp_bc":
        return check_bc(m, P)
    return []


def all_issues(m):
    out = []
    for sid in ("susp_geo", "susp_sect", "susp_mat", "susp_bc"):
        out += issues(m, sid)
    return out


# ======================================================================
# 恒载 / 初拉力估算
# ======================================================================
def estimate(m, P=None, G=None):
    """估算恒载集度和初拉力。

    H = w·L²/(8f)（三跨悬索桥主跨的经典抛物线公式），w 取
    加劲梁自重 + 二期恒载 + 主缆自重 + 吊杆自重折算（单索面 2D 模型，全部由一根主缆承担）。
    """
    if P is None:
        P, _perrs = parse(m)
    if G is None:
        G = _geometry(P, len(m.get("sections") or []))
    A = deck_area(m)
    q_deck = A * P["girder_den"]
    q2 = P["q2"]
    q_cable = P["cable_A"] * P["cable_den"]
    total_len = (G["xs"][-1] - G["xs"][0]) if G["xs"] else 0.0
    hanger_total = sum(h["len"] for h in G["hangers"])
    q_hanger = (hanger_total * P["hanger_A"] * P["hanger_den"] / total_len) if total_len > 0 else 0.0
    w = q_deck + q2 + q_cable + q_hanger
    f = P["fr"] * P["L"]
    H_auto = w * P["L"] ** 2 / (8.0 * f) if (f > 0 and P["L"] > 0) else 0.0
    H = H_auto if (P["auto_H"] or P["H_manual"] is None) else P["H_manual"]
    slope_tower = 4.0 * f / P["L"] if P["L"] > 0 else 0.0
    T_tower = H * math.sqrt(1.0 + slope_tower ** 2)
    # 边跨主缆在锚固端最陡（抛物线越往下越陡），那里的索力最大
    slope_anchor = 0.0
    if P["Ls"] > 0 and P["L"] > 0:
        a = (P["z_deck"] - P["z_top"] + slope_tower * P["Ls"]) / (P["Ls"] * P["Ls"])
        slope_anchor = abs(-slope_tower + 2.0 * a * P["Ls"])
    T_anchor = H * math.sqrt(1.0 + slope_anchor ** 2)
    # 吊杆初拉力：按分担的恒载（加劲梁自重 + 二期）估算，可逐根手填覆盖
    hanger_T = []
    overrides = P.get("hanger_T_override") or []
    for i, h in enumerate(G["hangers"]):
        spacing = G["ds"] if h["main"] else G["side_ds"]
        t = (q_deck + q2) * spacing * P["hanger_factor"]
        if i < len(overrides) and overrides[i] is not None:
            t = overrides[i]
        hanger_T.append(t)
    cable_T = []
    for e in G["elements"]:
        if e["role"] != "cable":
            continue
        sl = e["slope"] or 0.0
        cable_T.append(H * math.sqrt(1.0 + sl * sl))
    return {
        "A_deck": A, "q_deck": q_deck, "q2": q2, "q_cable": q_cable, "q_hanger": q_hanger,
        "w": w, "f": f, "H_auto": H_auto, "H": H, "manual_H": (not P["auto_H"] and P["H_manual"] is not None),
        "slope_tower": slope_tower, "T_tower": T_tower,
        "slope_anchor": slope_anchor, "T_anchor": T_anchor,
        "hanger_T": hanger_T, "cable_T": cable_T,
        "cable_stress": (H / P["cable_A"] if P["cable_A"] > 0 else 0.0),
        "T_max": max(cable_T) if cable_T else 0.0,
    }


def pretension_list(m, P=None, G=None, est=None):
    """返回 [(单元号, 初拉力 kN, 类型说明), ...]，*PRETENSION 就是按这个写的。"""
    if P is None:
        P, _perrs = parse(m)
    if G is None:
        G = _geometry(P, len(m.get("sections") or []))
    if est is None:
        est = estimate(m, P, G)
    out = []
    i_cable = 0
    for e in G["elements"]:
        if e["role"] == "cable":
            t = est["cable_T"][i_cable] if i_cable < len(est["cable_T"]) else est["H"]
            i_cable += 1
            out.append((e["no"], t, "主缆"))
        elif e["role"] == "hanger" and P["hanger_ps"]:
            idx = e.get("hanger_index") or 0
            t = est["hanger_T"][idx] if idx < len(est["hanger_T"]) else 0.0
            out.append((e["no"], t, "吊杆"))
    return out


def summary(m):
    """界面第 1 步的实时摘要（也是测试里对数的入口）。永远不抛异常。"""
    P, perrs = parse(m)
    geo_errs = _pick(perrs, GEO_KEYS) + check_geo(P)
    info = {"ok": False, "errors": geo_errs}
    if geo_errs:
        return info
    G = _geometry(P, len(m.get("sections") or []))
    info.update({
        "ok": True, "errors": [],
        "L": P["L"], "Ls": P["Ls"], "fr": P["fr"], "f": G["f"],
        "z_base": P["z_base"], "z_deck": P["z_deck"], "z_top": P["z_top"],
        "tower_h": P["z_top"] - P["z_base"], "tower_above_deck": P["z_top"] - P["z_deck"],
        "z_mid": G["z_mid"], "clearance": G["z_mid"] - P["z_deck"],
        "n_main": G["n_main"], "ds": G["ds"], "ns": P["ns"], "side_ds": G["side_ds"],
        "counts": G["counts"], "nodes": G["counts"]["nodes"], "elements": G["counts"]["elements"],
        "hangers": len(G["hangers"]),
        "cable_length": G["cable_length"],
        "hanger_len_min": G["hanger_len_min"], "hanger_len_max": G["hanger_len_max"],
        "hanger_mid": G["hanger_mid"],
        "n_supports": sum(len(s["nodes"]) for s in G["supports"]),
        "n_links": len(G["links"]),
        "cable_type": G["cable_type"],
        "G": G,
    })
    try:
        info["est"] = estimate(m, P, G)
    except Exception:                                       # noqa: BLE001
        info["est"] = None
    return info


# ======================================================================
# 截面 MCT 行（不依赖 sect_ui / tkinter）
# ======================================================================
def _shape_dims_for_mct(sec):
    code = wc.shape_code_of(sec)
    shape = wc.SHAPE_BY_CODE.get(code) or wc.SHAPES[0]
    dims = sec.get("dims") or []
    vals = []
    for k in range(len(shape[2])):
        raw = _txt(dims[k]).strip() if k < len(dims) else ""
        vals.append(wc.num(raw) if wc.is_num(raw) else "0")
    return code, shape, vals + ["0"] * max(0, wc.DIM_SLOTS - len(vals))


def _pscvalue_lines(i, sec):
    """DXF 自定义截面 → *SECT-PSCVALUE 行（写法与 sect_ui.section_mct_lines 一致）。"""
    L = []
    pr = sec.get("props") or {}
    name = wc.aname(sec.get("name"), "SEC%d" % i)
    off = sec.get("offset", "CC")
    outs = [wc.orient_loop(lp, True) for lp in (pr.get("OUTER_LOOPS") or [])
            if len(lp) >= 3 and abs(wc.loop_area(lp)) > 1e-12]
    ins = [wc.orient_loop(lp, False) for lp in (pr.get("HOLE_LOOPS") or [])
           if len(lp) >= 3 and abs(wc.loop_area(lp)) > 1e-12]
    if len(outs) != 1:
        # 外轮廓不唯一：*SECT-PSCVALUE 放不下，退回「数值截面」（和 sect_ui 的处理一致）
        return _value_section_lines(i, sec)
    try:
        A = float(pr.get("A") or 0); iyy = float(pr.get("Iyy") or 0)
        izz = float(pr.get("Izz") or 0); ixx = float(pr.get("Ixx") or 0)
        cyp = float(pr.get("CyP") or 0); cym = float(pr.get("CyM") or 0)
        czp = float(pr.get("CzP") or 0); czm = float(pr.get("CzM") or 0)
        po = float(pr.get("PERI_OUT") or 0); pi = float(pr.get("PERI_IN") or 0)
        uyy = A * 5.0 / 6.0; uzz = A * 5.0 / 6.0
    except (TypeError, ValueError):
        A = iyy = izz = ixx = cyp = cym = czp = czm = po = pi = uyy = uzz = 0.0
    L.append(" SECT=%4d, PSC       , %-17s , %s, 0, 0, 0, 0, 0, 0, %s, %s, VALU, NO, NO, "
             % (i, name, off, sec.get("shear", "YES"), sec.get("warp", "NO")))
    L.append("        %s, %s, %s, %s, %s, %s"
             % (wc.numg(A), wc.numg(uyy), wc.numg(uzz), wc.numg(ixx), wc.numg(iyy), wc.numg(izz)))
    L.append("        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s"
             % (wc.numg(cyp), wc.numg(cym), wc.numg(czp), wc.numg(czm),
                wc.numg(A * czp / 8.0), wc.numg(A * cyp / 8.0), wc.numg(po), wc.numg(pi),
                wc.numg(cyp), wc.numg(czp)))
    L.append("        %s, %s, %s, %s, %s, %s, %s, %s"
             % (wc.numg(-cym), wc.numg(cyp), wc.numg(cyp), wc.numg(-cym),
                wc.numg(czp), wc.numg(czp), wc.numg(-czm), wc.numg(-czm)))
    L.append("        0.1, 0.1, 0.1, 0.1")
    L.append("        YES, 0, 0, YES, , YES, , YES, , 0, YES, , YES, , YES, ")
    for lp in outs:
        L.append("       OPOLY=" + ", ".join(wc.numg(v) for p in lp for v in (p[0], p[1])))
    for lp in ins:
        L.append("       IPOLY=" + ", ".join(wc.numg(v) for p in lp for v in (p[0], p[1])))
    return L


def _value_section_lines(i, sec):
    """数值截面（VALUE）行——主缆/吊杆的索截面也用这个写法（样本里 Cable 截面就是这样）。"""
    pr = sec.get("props") or {}
    name = wc.aname(sec.get("name"), "SEC%d" % i)
    off = sec.get("offset", "CC")
    try:
        A = float(pr.get("A") or 0); iyy = float(pr.get("Iyy") or 0)
        izz = float(pr.get("Izz") or 0); ixx = float(pr.get("Ixx") or 0)
        cy = float(pr.get("Cy") or 0); cz = float(pr.get("Cz") or 0)
        cyp = float(pr.get("CyP") or 0); cym = float(pr.get("CyM") or 0)
        czp = float(pr.get("CzP") or 0); czm = float(pr.get("CzM") or 0)
        po = float(pr.get("PERI_OUT") or 0); pi = float(pr.get("PERI_IN") or 0)
        asy = float(pr.get("ASy") or 0); asz = float(pr.get("ASz") or 0)
    except (TypeError, ValueError):
        A = iyy = izz = ixx = cy = cz = cyp = cym = czp = czm = po = pi = asy = asz = 0.0
    L = ["   %d, VALUE     , %s, %s, 0, 0, 0, 0, 0, 0, %s, %s, SB , BUILT, %s, %s, 0, 0, 0, 0, 0, 0, 0"
         % (i, name, off, sec.get("shear", "YES"), sec.get("warp", "NO"),
            wc.numg(czp + czm), wc.numg(cyp + cym))]
    L.append("      %s, %s, %s, %s, %s, %s" % (wc.numg(A), wc.numg(asy), wc.numg(asz),
                                              wc.numg(ixx), wc.numg(iyy), wc.numg(izz)))
    L.append("      %s, %s, %s, %s, 0, 0, %s, %s, %s, %s"
             % (wc.numg(cyp), wc.numg(cym), wc.numg(czp), wc.numg(czm),
                wc.numg(po), wc.numg(pi), wc.numg(cy), wc.numg(cz)))
    L.append("      0, 0, 0, 0, 0, 0, 0, 0, 0, 0")
    return L


def cable_section_rows(m, P):
    """主缆 / 吊杆的索截面（只有面积，I 全 0；照 CableStayed.mct 里 Cable 截面的写法）。"""
    secs = m.get("sections") or []
    base = len(secs)
    L = []
    for k, (name, A) in enumerate((("CableMain", P["cable_A"]), ("HangerRod", P["hanger_A"]))):
        i = base + 1 + k
        L += _value_section_lines_pure(i, name, A)
    return L


def _value_section_lines_pure(i, name, A):
    """纯面积截面（主缆/吊杆用）：第 2 行只填面积，其余 0。"""
    return [
        "   %d, VALUE     , %-16s, CC, 0, 0, 0, 0, 0, 0, YES, NO, SB , BUILT, 0, 0, 0, 0, 0, 0, 0, 0, 0" % (i, name),
        "      %s, 0, 0, 0, 0, 0" % wc.numg(A),
        "      0, 0, 0, 0, 0, 0, 0, 0, 0, 0",
        "      0, 0, 0, 0, 0, 0, 0, 0, 0, 0",
    ]


def section_block(m, P):
    """整个 *SECTION 块（含 DXF 自定义截面的 *SECT-PSCVALUE 块）。"""
    secs = m.get("sections") or []
    cad = [(i, s) for i, s in enumerate(secs, 1) if s.get("shape") == wc.CAD_SHAPE]
    L = []
    if cad:
        L.append("*SECT-PSCVALUE    ; PSC Value, General Section（DXF 自定义截面）")
        L.append("; SECT=iSEC, TYPE, SNAME, [OFFSET], bSD, bWE, SHAPE, bBU, bEQ, SNAME2(PSC VAL)")
        L.append(";      AREA, ASy, ASz, Ixx, Iyy, Izz")
        L.append(";      CyP, CyM, CzP, CzM, QyB, QzB, PERI_OUT, PERI_IN, Cy, Cz")
        L.append(";      Y1, Y2, Y3, Y4, Z1, Z2, Z3, Z4")
        L.append(";      HT, BT, T1, T2")
        L.append(";      bSHEARCHK, Z1, Z3, bAUTO_QY1, QY1, ..., TOR, bAUTO_SHR1, SHR1, ...")
        L.append(";      OPOLY=X1, Y1, X2, Y2, ...  外轮廓（逆时针）；IPOLY=...  孔洞（顺时针）")
        for i, sec in cad:
            L += _pscvalue_lines(i, sec)
        L.append("")
    L.append("*SECTION    ; Section Properties")
    L.append("; iSEC, TYPE, SNAME, [OFFSET], bSD, bWE, SHAPE, [DATA1], [DATA2]      ; 1st line - DB/USER")
    L.append("; iSEC, TYPE, SNAME, [OFFSET], bSD, bWE, SHAPE, BLT, D1, ..., D8, iCEL ; 1st line - VALUE")
    L.append(";       AREA, ASy, ASz, Ixx, Iyy, Izz                                 ; 2nd line")
    L.append(";       CyP, CyM, CzP, CzM, QyB, QzB, PERI_OUT, PERI_IN, Cy, Cz       ; 3rd line")
    for i, sec in enumerate(secs, 1):
        if sec.get("shape") == wc.CAD_SHAPE:
            continue
        code, shape, vals = _shape_dims_for_mct(sec)
        name = wc.aname(sec.get("name"), "SEC%d" % i)
        off = sec.get("offset", "CC")
        L.append("   %d, DBUSER, %s, %s, 0, 0, 0, 0, 0, 0, %s, %s, %s, 2, %s"
                 % (i, name, off, sec.get("shear", "YES"), sec.get("warp", "NO"), code,
                    ", ".join(vals)))
    L += cable_section_rows(m, P)
    L.append("")
    return L


# ======================================================================
# MCT 生成
# ======================================================================
def _material_lines(m, P):
    L = ["*MATERIAL    ; Material",
         "; iMAT, TYPE, MNAME, SPHEAT, HEATCO, PLAST, TUNIT, bMASS, DAMPRATIO, [DATA1]",
         "; [DATA1] : 2, ELAST, POISN, THERMAL, DEN, MASS"]
    for i, typ, name, ek, nk, dk, alpha, _zh in MAT_ROWS:
        L.append("   %d, %s, %s, 0, 0, , C, NO, 0.05, 2, %s, %s, %s, %s, 0"
                 % (i, typ, name, wc.num(P[ek]), wc.num(P[nk]), alpha, wc.num(P[dk])))
    L.append("")
    return L


def _node_lines(G):
    L = ["*NODE    ; Nodes", "; iNO, X, Y, Z"]
    for nd in G["nodes"]:
        L.append("   %d, %s, 0, %s" % (nd["no"], wc.numg(nd["x"], 8), wc.numg(nd["z"], 8)))
    L.append("")
    return L


def _element_lines(G, P):
    L = ["*ELEMENT    ; Elements",
         "; iEL, TYPE, iMAT, iPRO, iN1, iN2, ANGLE, iSUB,                     ; Frame  Element",
         "; iEL, TYPE, iMAT, iPRO, iN1, iN2, ANGLE, iSUB, EXVAL, EXVAL2, bLMT ; Comp/Tens Truss"]
    for e in G["elements"]:
        if e["type"] == "TENSTR":
            # 照 CableStayed.mct：iSUB=3（Cable）、EXVAL=无应力索长、EXVAL2=3，共 10 个字段
            L.append("   %d, TENSTR, %d, %d, %d, %d, 0, 3, %s, 3"
                     % (e["no"], e["mat"], e["sect"], e["i"], e["j"], wc.numg(e["lu"] or 0.0, 6)))
        else:
            L.append("   %d, %s, %d, %d, %d, %d, 0, 0"
                     % (e["no"], e["type"], e["mat"], e["sect"], e["i"], e["j"]))
    L.append("")
    return L


def _group_lines(G):
    by_role = {}
    for e in G["elements"]:
        by_role.setdefault(e["role"], []).append(e["no"])
    deck_nodes = [n["no"] for n in G["nodes"] if n["role"] == "deck"]
    cable_nodes = [n["no"] for n in G["nodes"] if n["role"] == "cable"]
    tower_nodes = [n["no"] for n in G["nodes"] if n["role"] == "tower"]
    hanger_cable_nodes = [h["cable"] for h in G["hangers"]]
    L = ["*GROUP    ; Group", "; NAME, NODE_LIST, ELEM_LIST, PLANE_TYPE"]
    L.append("   %s, %s, %s, 0" % (G_GIRDER, _ranges(deck_nodes), _ranges(by_role.get("girder", []))))
    L.append("   %s, %s, %s, 0" % (G_CABLE, _ranges(set(G["chain"]) | set(cable_nodes)),
                                  _ranges(by_role.get("cable", []))))
    L.append("   %s, %s, %s, 0" % (G_HANGER, _ranges(hanger_cable_nodes), _ranges(by_role.get("hanger", []))))
    L.append("   %s, %s, %s, 0" % (G_TOWER, _ranges(tower_nodes), _ranges(by_role.get("tower", []))))
    L.append("   %s, %s, , 0" % (G_ANCHOR, _ranges([G["anchor_l"], G["anchor_r"]])))
    L.append("")
    return L


def _bndr_group_lines(G):
    names = [B_FIXED]
    if G["bearing_nodes"]:
        names.append(B_BEARING)
    if G["links"]:
        names.append(B_RIGID)
    L = ["*BNDR-GROUP    ; Boundary Group", "; NAME, AUTOTYPE"]
    for nm in names:
        L.append("   %s, 0" % nm)
    L.append("")
    return L


def _load_group_lines(P):
    L = ["*LOAD-GROUP    ; Load Group", "; NAME"]
    if P["selfweight"]:
        L.append("   %s" % L_SELF)
    if P["dl2"]:
        L.append("   %s" % L_ADD)
    if P["pretension"]:
        L.append("   %s" % L_PRET)
    L.append("")
    return L


def _constraint_lines(G):
    if not G["supports"]:
        return []
    L = ["*CONSTRAINT    ; Supports", "; NODE_LIST, CONST(Dx,Dy,Dz,Rx,Ry,Rz), GROUP"]
    for s in G["supports"]:
        L.append("   %s, %s, %s" % (_ids_text(s["nodes"]), s["code"], s["group"]))
    L.append("")
    return L


def _rigidlink_lines(G):
    if not G["links"]:
        return []
    L = ["*RIGIDLINK    ; Rigid Link", "; M-NODE, DOF, S-NODE LIST, GROUP"]
    for lk in G["links"]:
        L.append("   %d, 111111, %s, %s" % (lk["master"], _ids_text(lk["slaves"]), lk["group"]))
    L.append("")
    return L


def _load_lines(m, G, P, est):
    lcs = []
    if P["selfweight"]:
        lcs.append((P["lc_sw"], "D", "Self weight"))
    if P["dl2"]:
        lcs.append((P["lc_dl2"], "D", "Additional dead load"))
    if P["pretension"]:
        lcs.append((P["lc_ps"], "USER", "Initial cable force"))
    L = ["*STLDCASE    ; Static Load Cases", "; LCNAME, LCTYPE, DESC"]
    for nm, tp, desc in lcs:
        L.append("   %s, %s, %s" % (wc.aname(nm), tp, desc))
    L.append("")
    if P["selfweight"]:
        L += ["*USE-STLD, " + P["lc_sw"], "",
              "*SELFWEIGHT    ; Self Weight", "; X, Y, Z, GROUP",
              "   %s, %s, %s, %s" % (wc.num(0), wc.num(0), wc.num(-1), L_SELF), ""]
    if P["dl2"]:
        q = -abs(P["q2"])
        girder_ids = [e["no"] for e in G["elements"] if e["role"] == "girder"]
        L += ["*USE-STLD, " + P["lc_dl2"], "",
              "*BEAMLOAD    ; Element Beam Loads",
              "; ELEM_LIST, CMD, TYPE, DIR, bPROJ, [ECCEN], [VALUE], GROUP",
              "; [VALUE] : D1, P1, D2, P2, D3, P3, D4, P4",
              "   %s, BEAM, UNILOAD, GZ, NO, NO, LY, , , , 0, %s, 1, %s, 0, 0, 0, 0, %s, NO, 0, 0, NO,"
              % (_ranges(girder_ids), wc.num(q), wc.num(q), L_ADD), ""]
    if P["pretension"]:
        items = pretension_list(m, P, G, est)
        if items:
            L += ["*USE-STLD, " + P["lc_ps"], "",
                  "*PRETENSION    ; Pretension Loads", "; ELEM_LIST, TENS, GROUP"]
            for no, t, _kind in items:
                L.append("   %d, %s, %s" % (no, wc.num(round(t, 2)), L_PRET))
            L.append("")
    return L


def build_mct(m):
    """生成完整 MCT 文本（GBK 写文件）。参数不合法时抛 ValueError（带中文说明）。"""
    P, perrs = parse(m)
    errs = list(perrs.values())
    if not errs:
        errs = check_geo(P) + check_sect(m) + check_mat(m, P) + check_bc(m, P)
    if errs:
        raise ValueError("悬索桥参数还没填好，先改这些：\n" + "\n".join("· " + e for e in errs[:12]))
    secs = m.get("sections") or []
    G = _geometry(P, len(secs))
    est = estimate(m, P, G)
    p = m.get("project") or {}
    L = []
    add = L.append
    add("; ================================================================")
    add("; MIDAS Civil NX — MCT 命令文件（悬索桥：主缆-吊杆-加劲梁-桥塔）")
    add("; 由「MIDAS Civil NX 建模向导 · 悬索桥流程」生成  "
        + datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    if _txt(p.get("name")).strip():
        add("; 项目名称：" + _txt(p.get("name")).strip())
    add("; 导入：MIDAS Civil NX → 文件 → 导入 → MCT 命令文件")
    add("; 主跨 L=%.1f m，矢跨比 f/L=%g（矢高 f=%.1f m），每侧边跨 %.1f m"
        % (P["L"], P["fr"], G["f"], P["Ls"]))
    add("; 主缆/吊杆：%s；主缆初拉力按 H = wL²/(8f) 估算（H=%.1f kN，w=%.2f kN/m）"
        % (G["cable_type"], est["H"], est["w"]))
    add("; ================================================================")
    add("")
    # *UNIT
    add("*UNIT    ; Unit System")
    add("; FORCE, LENGTH, HEAT, TEMPER")
    add("   %s, %s, %s, %s" % (_txt(p.get("force", "KN")), _txt(p.get("dist", "M")),
                              _txt(p.get("heat", "KJ")), _txt(p.get("temp_unit", "C"))))
    add("")
    # *STRUCTYPE
    add("*STRUCTYPE    ; Structure Type")
    add("; iSTYP, iMASS, iSMAS, bMASSOFFSET, bSELFWEIGHT, GRAV, TEMPER, bALIGNBEAM, bALIGNSLAB, bROTRIGID")
    add("   %s, 1, 1, YES, NO, %s, %s, NO, NO, NO"
        % (_txt(p.get("styp", "1")), wc.num(p.get("grav", "9.806")), wc.num(p.get("temper", "0"))))
    add("")
    L += _material_lines(m, P)
    L += section_block(m, P)
    L += _node_lines(G)
    L += _element_lines(G, P)
    L += _group_lines(G)
    L += _bndr_group_lines(G)
    L += _load_group_lines(P)
    L += _constraint_lines(G)
    L += _rigidlink_lines(G)
    L += _load_lines(m, G, P, est)
    add("*ENDDATA")
    return "\r\n".join(L) + "\r\n"


# ======================================================================
# 数据表（人可读）
# ======================================================================
def data_report(m):
    P, perrs = parse(m)
    L = []
    add = L.append
    add("=" * 78)
    add("悬索桥（主缆 · 吊杆 · 加劲梁 · 桥塔）—— 模型数据表")
    add("=" * 78)
    p = m.get("project") or {}
    add("项目名称：%s" % (_txt(p.get("name")).strip() or "（未命名）"))
    add("生成时间：%s" % datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    add("单位制：力 %s，长度 %s；结构类型：二维 X-Z 平面"
        % (_txt(p.get("force", "KN")), _txt(p.get("dist", "M"))))
    add("")
    s = m.get("susp") or {}
    add("-" * 78)
    add("一、总体尺寸")
    add("-" * 78)
    add("  主跨 L = %s m      每侧边跨 = %s m      矢跨比 f/L = %s"
        % (wc.num(P["L"]), wc.num(P["Ls"]), wc.num(P["fr"])))
    add("  矢高 f = %.2f m      塔底 %.2f m / 桥面 %.2f m / 塔顶 %.2f m（桥面以上 %.2f m）"
        % (P["fr"] * P["L"], P["z_base"], P["z_deck"], P["z_top"], P["z_top"] - P["z_deck"]))
    add("  主跨吊杆间距 %s m（%s 个节间）      边跨每侧吊杆 %s 根（间距 %.2f m）"
        % (wc.num(P["ds"]), wc.num(int(round(P["L"] / P["ds"])) if P["ds"] else 0),
           wc.num(P["ns"]), P["Ls"] / (P["ns"] + 1)))
    add("  加劲梁每个吊杆间距内分 %s 段；桥塔分 %s 段" % (wc.num(P["n_girder"]), wc.num(P["n_tower"])))
    try:
        G = _geometry(P, len(m.get("sections") or []))
        est = estimate(m, P, G)
        add("  跨中主缆标高 %.2f m（比桥面高 %.2f m）      主缆几何总长 %.1f m"
            % (G["z_mid"], G["z_mid"] - P["z_deck"], G["cable_length"]))
        add("  吊杆长度 %.1f ~ %.1f m（跨中最短 %.1f m）" % (G["hanger_len_min"], G["hanger_len_max"], G["hanger_mid"]))
        add("  节点 %d 个；单元 %d 个（加劲梁 %d / 主缆 %d / 吊杆 %d / 桥塔 %d）"
            % (G["counts"]["nodes"], G["counts"]["elements"], G["counts"]["girder"],
               G["counts"]["cable"], G["counts"]["hanger"], G["counts"]["tower"]))
    except Exception:                                       # noqa: BLE001
        G = None
        est = None
        add("  （几何参数还有问题，节点/单元统计从略）")
    add("")
    add("-" * 78)
    add("二、截面（第 1 个 = 加劲梁，第 2 个 = 桥塔）")
    add("-" * 78)
    secs = m.get("sections") or []
    for i, sec in enumerate(secs, 1):
        code = wc.shape_code_of(sec)
        if code == wc.CAD_SHAPE:
            pr = sec.get("props") or {}
            add("  %d) %-12s DXF 自定义截面：A=%.6g m²  Iyy=%.6g  Izz=%.6g"
                % (i, _txt(sec.get("name")), float(pr.get("A") or 0),
                   float(pr.get("Iyy") or 0), float(pr.get("Izz") or 0)))
        else:
            shape = wc.SHAPE_BY_CODE.get(code) or wc.SHAPES[0]
            dims = sec.get("dims") or []
            ds = "，".join("%s=%s" % (shape[2][k][0], _txt(dims[k]) if k < len(dims) else "")
                          for k in range(len(shape[2])))
            add("  %d) %-12s %s（%s）：%s" % (i, _txt(sec.get("name")), shape[1], code, ds))
    base = len(secs)
    add("  %d) %-12s 主缆索截面：只有面积 A=%s m²（索单元不需要惯性矩）"
        % (base + 1, "CableMain", wc.num(P["cable_A"])))
    add("  %d) %-12s 吊杆索截面：只有面积 A=%s m²"
        % (base + 2, "HangerRod", wc.num(P["hanger_A"])))
    add("")
    add("-" * 78)
    add("三、材料")
    add("-" * 78)
    for i, typ, name, ek, nk, dk, alpha, zh in MAT_ROWS:
        add("  %d) %-8s %s：E=%s kN/m²，ν=%s，容重=%s kN/m³，线膨胀系数=%s    —— %s"
            % (i, typ, name, wc.num(P[ek]), wc.num(P[nk]), wc.num(P[dk]), alpha, zh))
    add("")
    add("-" * 78)
    add("四、恒载与初始索力")
    add("-" * 78)
    if est:
        add("  加劲梁自重（按截面面积估算）q = %.2f kN/m（面积 %.4f m² × 容重 %s）"
            % (est["q_deck"], est["A_deck"], wc.num(P["girder_den"])))
        add("  二期恒载 q2 = %.2f kN/m；主缆自重 %.2f kN/m；吊杆自重折算 %.2f kN/m"
            % (est["q2"], est["q_cable"], est["q_hanger"]))
        add("  合计 w = %.2f kN/m" % est["w"])
        add("  恒载水平分力 H = wL²/(8f) = %.2f×%.2f²/(8×%.2f) = %.1f kN%s"
            % (est["w"], P["L"], est["f"], est["H_auto"],
               "（现在用手填值 %.1f kN）" % P["H_manual"] if est["manual_H"] else ""))
        add("  塔顶处主缆倾角 tanθ = 4f/L = %.4f，索力 T = H/cosθ = %.1f kN；"
            "主缆应力 = H/A = %.1f MPa" % (est["slope_tower"], est["T_tower"], est["cable_stress"] / 1000.0))
        add("  锚固端主缆更陡（tanθ = %.4f），索力最大 T = H/cosθ = %.1f kN（悬索桥边跨主缆越靠近锚碇越陡）"
            % (est["slope_anchor"], est["T_anchor"]))
        add("  吊杆初拉力：主跨 %.1f kN/根，边跨 %.1f kN/根（系数 %s）"
            % ((est["q_deck"] + est["q2"]) * (P["L"] / max(1, int(round(P["L"] / P["ds"])))),
               (est["q_deck"] + est["q2"]) * (P["Ls"] / (P["ns"] + 1)),
               wc.num(P["hanger_factor"])))
        over = [i for i, v in enumerate(P.get("hanger_T_override") or []) if v is not None]
        if over:
            add("  其中 %d 根吊杆用了手填初拉力：%s"
                % (len(over), "、".join("第%d根=%.1f kN" % (i + 1, P["hanger_T_override"][i]) for i in over[:12])))
    else:
        add("  （参数还不全，算不出初拉力）")
    add("")
    add("-" * 78)
    add("五、边界条件与荷载")
    add("-" * 78)
    tb = {"fixed": "塔梁固结（*RIGIDLINK）", "bearing": "塔梁竖向支承（*CONSTRAINT）",
          "floating": "塔梁漂浮（不连接）"}.get(P["tower_beam"], P["tower_beam"])
    sd = {"same": "主缆与塔顶共用节点", "rigid": "塔顶节点 + *RIGIDLINK 主从（鞍座）"
          }.get(P["saddle"], P["saddle"])
    add("  塔底固结：%s；主缆锚固点固结：%s" % ("是" if P["tower_fix"] else "否",
                                          "是" if P["anchor_fix"] else "否"))
    add("  塔梁连接：%s；主缆过塔顶：%s" % (tb, sd))
    add("  索单元类型：%s" % ("TENSTR（Cable 子类型）" if P["cable_elem"] == CABLE_TENSTR else "TRUSS"))
    add("  荷载：自重%s / 二期恒载%s / 初始索力%s（吊杆初拉力%s）"
        % ("（工况 %s）" % P["lc_sw"] if P["selfweight"] else "（不计）",
           "（工况 %s，%.1f kN/m）" % (P["lc_dl2"], P["q2"]) if P["dl2"] else "（不计）",
           "（工况 %s）" % P["lc_ps"] if P["pretension"] else "（不计）",
           "计入" if P["hanger_ps"] else "不计入"))
    add("")
    add("=" * 78)
    add("说明：")
    add("  1) 本表是向导输入的存档；导入 MIDAS 用的是同目录下的 .mct 文件。")
    add("  2) 初拉力 H 只是按恒载水平分力做的估算（老师第三讲 3.8「悬索桥」：输入主缆无应力长度、")
    add("     水平分力或初始索力；H = wL²/(8f) 是主跨抛物线的经典公式），成桥真实索力/线形")
    add("     请按课程方法迭代，或做施工阶段分析（第四讲）后再取几何刚度初始荷载。")
    add("  3) 默认参数依据：老师第三讲 3.8「悬索桥」只讲了建模方法（主缆用索单元、吊杆用杆/索单元、")
    add("     加劲梁与桥塔用梁单元、初始平衡状态），没有给算例尺寸；默认值按常规悬索桥比例取：")
    add("     主跨 500 m、矢跨比 1/10（矢高 50 m）、每侧边跨 150 m、主跨吊杆间距 20 m、")
    add("     边跨每侧 6 根吊杆、桥面标高 20 m、塔顶标高 100 m（桥面以上 80 m ≈ L/6.3）、塔分 5 段。")
    add("=" * 78)
    return "\r\n".join(L) + "\r\n"


# ======================================================================
# 成果输出
# ======================================================================
def gbk_safe(text):
    """把 GBK 编不了的字符换成 ASCII 写法（上标 ² ³ ⁴ 在 GBK 里没有，写文件会变问号）。

    MCT 里的中文注释、数据表都走这个函数，保证 GBK 文件里不出现 ? 号：
        ² -> ^2     ³ -> ^3     ⁴ -> ^4     其它编不了的字符 -> ?
    """
    sup = {"⁰": "^0", "¹": "^1", "²": "^2", "³": "^3", "⁴": "^4",
           "⁵": "^5", "⁶": "^6", "⁷": "^7", "⁸": "^8", "⁹": "^9"}
    for k, v in sup.items():
        text = text.replace(k, v)
    out = []
    for ch in text:
        try:
            ch.encode("gbk")
        except UnicodeEncodeError:
            out.append("?")
        else:
            out.append(ch)
    return "".join(out)


def safe_name(name, fallback="悬索桥"):
    base = _txt(name).strip() or fallback
    for ch in '\\/:*?"<>|':
        base = base.replace(ch, "_")
    return base.strip() or fallback


def _safe_est_H(m, P):
    try:
        return estimate(m, P)["H"]
    except Exception:                                       # noqa: BLE001
        return 0.0


def readme_text(m, name):
    """成果文件夹里的「导入说明.txt」。"""
    P, _e = parse(m)
    prj = m.get("project") or {}
    title = _txt(prj.get("name")).strip() or "（未命名）"
    lines = [
        "本文件夹是「MIDAS Civil NX 建模向导 · 悬索桥流程」为项目「%s」生成的成果。" % title,
        "",
        "1) %s.mct" % name,
        "   MIDAS 模型命令文件。在 MIDAS Civil NX 里：文件 → 导入 → MCT 命令文件，选中它即可生成模型。",
        "   也可以打开记事本复制里面的文字，粘贴到 MIDAS 的「工具 → 命令窗口」后点运行。",
        "",
        "2) %s_数据.json" % name,
        "   向导里的全部输入。在程序里点「打开工程」选它，就能接着改。",
        "",
        "3) %s_数据表.txt" % name,
        "   把总体尺寸、截面、材料、边界、荷载、初拉力列成表格，导入后照着核对。",
        "",
        "4) 导入后请按顺序检查",
        "   · 结构类型应为「二维 X-Z 平面」，全部节点 Y=0；",
        "   · 主缆/吊杆是索单元，初拉力在荷载工况「%s」里（*PRETENSION）；" % P["lc_ps"],
        "   · 塔底、主缆锚固点固结；塔梁连接方式见数据表第五节；",
        "   · 恒载 = 自重（%s）+ 二期恒载（%s），先算恒载看主缆线形和吊杆力是否合理。"
        % (P["lc_sw"], P["lc_dl2"]),
        "",
        "模型桥型：主跨 %s m，矢跨比 %s，每侧边跨 %s m，桥面标高 %s m，塔顶标高 %s m。"
        % (wc.num(P["L"]), wc.num(P["fr"]), wc.num(P["Ls"]), wc.num(P["z_deck"]), wc.num(P["z_top"])),
        "",
        "说明：初拉力是按 H = wL²/(8f) 估算的（本模型 H = %.1f kN），只作为成桥状态的起步值；"
        % _safe_est_H(m, P),
        "      真实成桥索力需要按课程里的方法迭代，或做施工阶段分析。",
        "",
    ]
    return "\r\n".join(lines)


def write_bundle(m, out_dir, prefix, enc="gbk"):
    """写一整套成果：.mct + 数据 json + 数据表 + 导入说明。返回 [(说明, 路径), ...]。"""
    os.makedirs(out_dir, exist_ok=True)
    name = safe_name(prefix)
    files = []
    safe = (lambda s: gbk_safe(s)) if str(enc).lower().startswith("gbk") else (lambda s: s)
    mct_path = os.path.join(out_dir, name + ".mct")
    with open(mct_path, "w", encoding=enc, errors="replace", newline="") as fh:
        fh.write(safe(build_mct(m)))
    files.append(("MIDAS 模型命令文件（导入用）", mct_path))
    json_path = os.path.join(out_dir, name + "_数据.json")
    with open(json_path, "w", encoding="utf-8") as fh:
        import json
        json.dump(m, fh, ensure_ascii=False, indent=2)
    files.append(("向导数据（可再打开继续改）", json_path))
    rep_path = os.path.join(out_dir, name + "_数据表.txt")
    with open(rep_path, "w", encoding=enc, errors="replace", newline="") as fh:
        fh.write(safe(data_report(m)))
    files.append(("数据表（人可读，核对/存档）", rep_path))
    readme_path = os.path.join(out_dir, "导入说明.txt")
    with open(readme_path, "w", encoding=enc, errors="replace", newline="") as fh:
        fh.write(safe(readme_text(m, name)))
    files.append(("导入说明", readme_path))
    return files
