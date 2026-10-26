# -*- coding: utf-8 -*-
r"""斜拉桥（鱼骨刺 · 成桥）—— 核心引擎（纯计算 + MCT 生成，不 import tkinter）。

建模对象：三跨（边跨 + 主跨 + 边跨）双塔斜拉桥，按「成桥状态一次成桥」建模。

建模方法照老师课件 3.2.4《鱼骨刺模型》：
  * 主梁沿桥轴一条梁单元链（真实材料 + 真实截面）；
  * 桥塔是竖向梁单元链，塔底固结；
  * 斜拉索单独建节点（双索面时在桥轴两侧 y=±Bc/2），用「鱼骨刺刚性横梁」
    —— 虚拟梁单元（弹性模量取主梁的 1e4 倍，容重 0、线膨胀系数 0，按课件要求）
    连到主梁轴线节点；塔上的索锚点用 *RIGIDLINK 主从到塔柱轴线节点；
  * 斜拉索用 *ELEMENT 的 TENSTR（子类型 = 索 Cable），初拉力写 *PRETENSION；
  * 塔梁连接：固结用 *RIGIDLINK，半飘浮 / 飘浮用 *ELASTICLINK；
  * 初拉力三种给法：按恒载平衡估算 / 统一值 / 逐索输入。
    「按恒载平衡」= 第 k 根索承担的分仓恒载 q·l_trib 由索的竖向分力平衡：
        T = q · l_trib / (n_索面 · sinα)

MCT 格式一律以本机 MIDAS 自己导出的样本为准（不照手册猜）：
    C:\Midas\work\samples\CableStayed.mct
    —— *ELEMENT 的 TENSTR 行 10 个字段、*PRETENSION 3 个字段、
       *ELASTICLINK(GEN) 21 个字段、*RIGIDLINK 4 个字段、*BEAMLOAD 24 个字段 …
       测试里逐行数逗号，和样本一致。

单位：力 KN、长度 M（与梁桥流程一致）。所有数值在 model 里存字符串。
"""

import datetime
import math

from wiz_common import SHAPES, SHAPE_BY_CODE, num, numg, txt, aname, shape_code_of
import analysis_core      # 分析设置（模态 / 屈曲）的 MCT 片段 + 校验


# GBK 里没有上标 ²³⁴（只有 ㎡），写盘前统一换成普通数字，免得变成一串「?」
_SUPERSCRIPT = {"²": "2", "³": "3", "⁴": "4",
                "₁": "1", "₂": "2"}


def text_for_enc(text, enc):
    """按目标编码整理文本：目标编码表示不了的上下标换成普通数字。"""
    try:
        text.encode(enc)
        return text
    except (UnicodeEncodeError, LookupError):
        pass
    for k, v in _SUPERSCRIPT.items():
        text = text.replace(k, v)
    return text

CODE = "cable"
LABEL = "斜拉桥（鱼骨刺·成桥）"
FLOW = {"flow": CODE}          # 写进模型的流程标记（打开工程时靠它切回本流程）

STEP_IDS = ["geom", "section", "material", "boundary", "export"]
STEP_TITLES = ["桥型与几何", "截面", "材料与斜拉索", "边界与荷载", "生成成果"]
STEP_HINTS = [
    "先填跨径、塔高和索的布置；下面的示意图和统计会跟着变。",
    "主梁、桥塔用真实截面（可导入 DXF 自定义截面）；鱼骨刺横梁是虚拟截面，程序自动加。",
    "材料可以直接选规格；初拉力建议先用「按恒载平衡估算」，生成后再到 MIDAS 里精调。",
    "塔底固定、塔梁连接方式和二期恒载在这里定；辅助墩默认只压竖向。",
    "点「生成成果」写出 .mct；也可以复制 MCT 文本粘到 MIDAS 的命令窗口。",
]

# ---------------------------------------------------------------------------
# 材料规格库：(弹模 kN/m², 泊松比, 容重 kN/m³, 线膨胀系数 1/℃)
#   混凝土按 JTG 3362-18 弹性模量；预应力钢绞线 E=1.95×10⁵ MPa。
# ---------------------------------------------------------------------------
CONC_SPECS = {
    "C30": ("3.00e7", "0.2", "25", "0.00001"),
    "C35": ("3.15e7", "0.2", "25", "0.00001"),
    "C40": ("3.25e7", "0.2", "25", "0.00001"),
    "C45": ("3.35e7", "0.2", "25", "0.00001"),
    "C50": ("3.45e7", "0.2", "25", "0.00001"),
    "C55": ("3.55e7", "0.2", "25", "0.00001"),
    "C60": ("3.60e7", "0.2", "25", "0.00001"),
}
STEEL_SPECS = {
    "Q235": ("2.06e8", "0.3", "76.98", "0.000012"),
    "Q345": ("2.06e8", "0.3", "76.98", "0.000012"),
    "Q420": ("2.06e8", "0.3", "76.98", "0.000012"),
}
CABLE_SPECS = {
    "钢绞线1860": ("1.95e8", "0.3", "78.5", "0.000012"),
    "钢丝1670": ("2.00e8", "0.3", "78.5", "0.000012"),
}
SPEC_TABLE = {"CONC": CONC_SPECS, "STEEL": STEEL_SPECS, "CABLE": CABLE_SPECS}

# 索面 / 塔梁连接 / 初拉力方式 —— 界面下拉、引擎判断都用这三张表
PLANE_NAMES = {"single": "单索面（索直接连主梁轴线）", "double": "双索面（经鱼骨刺横梁）"}
LINK_NAMES = {"rigid": "固结（*RIGIDLINK）", "semi": "半飘浮（纵向弹性约束）", "float": "飘浮（纵向自由）"}
TENSION_NAMES = {"balance": "按恒载平衡估算", "uniform": "统一值", "manual": "逐索输入（可在下表改）"}

# MCT 里的名字一律 ASCII 且唯一（中文名 MIDAS 也认，但组名/工况名用英文更稳）
LC_DL, LC_2ND, LC_PRE = "LC-DL", "LC-2ND", "LC-PRE"
LG_SW, LG_2ND, LG_PRE = "LG-SW", "LG-2ND", "LG-PRE"
BG_TOWER, BG_DECK, BG_LINK = "BND-TOWER", "BND-DECK", "BND-LINK"
GR_GIRDER, GR_TOWER, GR_CABLE, GR_FISH = "GIRDER", "TOWER", "CABLE", "FISHBONE"
GR_ALL = "ALL"

# 鱼骨刺虚拟截面的默认尺寸（m）：刚度靠弹模放大，截面只是个载体
FISHBONE_DIMS = ["1.0", "1.0"]


# ===========================================================================
# 一、默认模型
# ===========================================================================
def default_model():
    """默认参数：220 m 主跨双塔双索面斜拉桥。

    参数依据（课件里没有给出斜拉桥算例的具体尺寸，所以按常规值取，建模方法照课件）：
      * 布置：边跨 100 + 主跨 220 + 边跨 100（边跨/主跨 ≈ 0.45，斜拉桥常用 0.40~0.45）；
      * 塔高：桥面以上 60 m（≈0.27×主跨）、以下 24 m 到基础，塔底固结；
      * 索：双索面、索面间距 12 m（≈桥面宽），主跨侧索距 10 m、每塔每侧 10 根，
        边跨侧 8 根，塔上锚固 12~48 m（最高锚点离塔顶还有 12 m 余量）；
      * 鱼骨刺虚拟梁：弹模 = 主梁 ×1e4、容重与线膨胀系数 = 0（第三讲 3.2.4 鱼骨刺模型原话）；
      * 索单元 TENSTR + *PRETENSION（第三讲 3.7 斜拉桥：索单元 + 初拉力；初拉力必须为正值）；
      * 塔梁连接给三种：固结 / 半飘浮 / 飘浮（第三讲 3.7「塔—梁—索协同」的分析控制）。
    """
    return {
        "flow": CODE,
        "mode": "cable",
        "project": {"name": "斜拉桥220m_鱼骨刺成桥", "force": "KN", "dist": "M",
                    "heat": "KJ", "temp_unit": "C", "styp": "0", "outdir": ""},
        "geom": {
            "L_side1": "100", "L_main": "220", "L_side2": "100",
            "z_deck": "0",
            "H_above": "60", "H_below": "24",
            "plane": "double",
            "B_cable": "12",
            "n_main": "10", "n_side": "8",
            "ds": "10", "ds_side": "10",
            "h0": "12", "hz": "4",
            "n_g_main": "22", "n_g_side": "10", "n_t": "6",
        },
        "sections": [
            {"name": "主梁", "role": "girder", "shape": "B",
             "dims": ["2.8", "12.0", "0.30", "0.35", "0.20", "0.30"],
             "offset": "CC", "shear": "YES", "warp": "NO"},
            {"name": "桥塔", "role": "tower", "shape": "SB",
             "dims": ["4.0", "3.0"],
             "offset": "CC", "shear": "YES", "warp": "NO"},
            {"name": "鱼骨刺虚拟横梁", "role": "fishbone", "shape": "SB",
             "dims": list(FISHBONE_DIMS),
             "offset": "CC", "shear": "YES", "warp": "NO"},
        ],
        "materials": {
            "girder": {"name": "MAT-GIRDER", "type": "CONC", "spec": "C50",
                       "elast": "3.45e7", "poisn": "0.2", "den": "25", "thermal": "0.00001"},
            "tower": {"name": "MAT-TOWER", "type": "CONC", "spec": "C50",
                      "elast": "3.45e7", "poisn": "0.2", "den": "25", "thermal": "0.00001"},
            "cable": {"name": "MAT-CABLE", "type": "STEEL", "spec": "钢绞线1860",
                      "elast": "1.95e8", "poisn": "0.3", "den": "78.5", "thermal": "0.000012"},
        },
        "cable": {
            "mode": "balance",          # balance / uniform / manual
            "T_uniform": "3000",
            "factor": "1.0",
            "A": "0.008",               # 单根索面积 m²（≈61 股 φ15.2 钢绞线）
            "items": [],                # 由 sync_cable_items() 填，逐索输入时用户可改 T
        },
        "bnd": {
            "tower_fix": True,          # 塔底固结（111111）
            "abut_dz": True,            # 两端桥台竖向支承
            "abut_dy": False,           # 两端桥台横向约束（单索面建议勾上）
            "deck_link": "rigid",       # 塔梁连接：rigid / semi / float
            "kx": "1e5",                # 半飘浮纵向弹簧刚度 kN/m
            "kd": "1e9",                # 竖向/横向（刚性）连接刚度 kN/m
            "aux_on": False,            # 辅助墩
            "aux_a": "20",              # 辅助墩距梁端距离 m
            "aux_dy": False,            # 辅助墩是否约束横向
        },
        "loads": {
            "sw_on": True, "sw_z": "-1",
            "q2": "60",                 # 二期恒载 kN/m（正数，程序按向下施加）
        },
    }


# ===========================================================================
# 二、参数整理
# ===========================================================================
def _f(v, default=0.0):
    try:
        s = str(v).strip()
    except Exception:                                          # noqa: BLE001
        return float(default)
    if s == "":
        return float(default)
    try:
        return float(s)
    except ValueError:
        return float(default)


def _i(v, default=1, lo=1):
    try:
        n = int(round(float(str(v).strip())))
    except (TypeError, ValueError):
        n = int(default)
    return max(lo, n)


def _r(x, nd=6):
    """坐标取整到 1e-6 再做键，避免浮点数把同一个节点拆成两个。"""
    return round(float(x), nd)


def params(model):
    """把 geom 里的字符串参数整理成浮点数/整数（不带校验，校验在 issues()）。"""
    g = model.get("geom") or {}
    Ls1 = _f(g.get("L_side1"), 100.0)
    Lm = _f(g.get("L_main"), 220.0)
    Ls2 = _f(g.get("L_side2"), Ls1)
    xL = Ls1
    xR = Ls1 + Lm
    p = {
        "Ls1": Ls1, "Lm": Lm, "Ls2": Ls2,
        "xL": xL, "xR": xR, "x_end": xR + Ls2,
        "z_deck": _f(g.get("z_deck"), 0.0),
        "H_above": _f(g.get("H_above"), 60.0),
        "H_below": _f(g.get("H_below"), 24.0),
        "plane": "single" if str(g.get("plane", "double")).strip() == "single" else "double",
        "B_cable": max(0.1, _f(g.get("B_cable"), 12.0)),
        "n_main": _i(g.get("n_main"), 10),
        "n_side": _i(g.get("n_side"), 8),
        "ds": _f(g.get("ds"), 10.0),
        "ds_side": _f(g.get("ds_side"), _f(g.get("ds"), 10.0)),
        "h0": _f(g.get("h0"), 12.0),
        "hz": _f(g.get("hz"), 4.0),
        "n_g_main": _i(g.get("n_g_main"), 22),
        "n_g_side": _i(g.get("n_g_side"), 10),
        "n_t": _i(g.get("n_t"), 6),
    }
    p["n_planes"] = 2 if p["plane"] == "double" else 1
    p["z_base"] = p["z_deck"] - p["H_below"]
    p["z_top"] = p["z_deck"] + p["H_above"]
    p["n_level"] = max(p["n_main"], p["n_side"])
    return p


def anchor_heights(p):
    """塔上各层索锚点的标高（从低到高），共 n_level 层。"""
    return [p["z_deck"] + p["h0"] + k * p["hz"] for k in range(p["n_level"])]


def span_gaps(p):
    """返回 (主跨跨中无索区长度, 左边跨尾索到梁端距离, 右边跨尾索到梁端距离)。"""
    mid_gap = p["Lm"] - 2.0 * p["n_main"] * p["ds"]
    tail1 = p["Ls1"] - p["n_side"] * p["ds_side"]
    tail2 = p["Ls2"] - p["n_side"] * p["ds_side"]
    return mid_gap, tail1, tail2


# ===========================================================================
# 三、截面/材料小工具
# ===========================================================================
def shape_area(sec):
    """按截面形状算面积（m²）。只用于估算恒载；算不出就返回 0。"""
    if not isinstance(sec, dict):
        return 0.0
    code = shape_code_of(sec)
    d = [_f(v, 0.0) for v in (sec.get("dims") or [])]

    def g(i):
        return d[i] if i < len(d) else 0.0

    if code == "CAD":
        return _f((sec.get("props") or {}).get("A"), 0.0)
    if code == "TS":
        return 0.0
    if code == "SB":
        return g(0) * g(1)
    if code == "SR":
        return math.pi * g(0) ** 2 / 4.0
    if code in ("L", "T"):
        return g(1) * g(3) + max(0.0, g(0) - g(3)) * g(2)
    if code in ("C", "H"):
        return g(1) * g(3) + g(4) * g(5) + max(0.0, g(0) - g(3) - g(5)) * g(2)
    if code == "B":
        h, b, tw, tf1, _c, tf2 = g(0), g(1), g(2), g(3), g(4), g(5)
        return max(0.0, h * b - max(0.0, h - tf1 - tf2) * max(0.0, b - 2.0 * tw))
    if code == "P":
        return math.pi * (g(0) ** 2 - max(0.0, g(0) - 2.0 * g(1)) ** 2) / 4.0
    return 0.0


DEFAULT_ROLE_SECTIONS = {
    "girder": {"name": "主梁", "role": "girder", "shape": "B",
               "dims": ["2.8", "12.0", "0.30", "0.35", "0.20", "0.30"],
               "offset": "CC", "shear": "YES", "warp": "NO"},
    "tower": {"name": "桥塔", "role": "tower", "shape": "SB", "dims": ["4.0", "3.0"],
              "offset": "CC", "shear": "YES", "warp": "NO"},
    "fishbone": {"name": "鱼骨刺虚拟横梁", "role": "fishbone", "shape": "SB",
                 "dims": list(FISHBONE_DIMS), "offset": "CC", "shear": "YES", "warp": "NO"},
}


def ensure_roles(model):
    """把三个「角色截面」（主梁 / 桥塔 / 鱼骨刺）补齐，返回补了哪些。

    鱼骨刺虚拟横梁由程序自动加（课件：虚拟梁弹模取主梁 1e4 倍、容重和线膨胀系数取 0），
    所以截面本身也让程序给，用户只需要管主梁和桥塔。
    """
    secs = model.setdefault("sections", [])
    added = []
    for role in ("girder", "tower", "fishbone"):
        if role_index(model, role):
            continue
        secs.append({k: (list(v) if isinstance(v, list) else v)
                     for k, v in DEFAULT_ROLE_SECTIONS[role].items()})
        added.append(role)
    return added


def role_index(model, role):
    """截面角色 → 截面号（1 起），找不到返回 0。"""
    for i, sec in enumerate(model.get("sections") or [], start=1):
        if isinstance(sec, dict) and sec.get("role") == role:
            return i
    return 0


def _sect_or(model, role, fallback=1):
    i = role_index(model, role)
    if i:
        return i
    n = len(model.get("sections") or [])
    return fallback if 1 <= fallback <= n else (1 if n else 1)


def cable_area(model):
    return max(0.0, _f((model.get("cable") or {}).get("A"), 0.005))


def cable_section(model):
    """索截面（程序自动生成，不显示在截面库里）：等效圆直径由索面积反算。"""
    a = cable_area(model)
    d = math.sqrt(4.0 * a / math.pi) if a > 0 else 0.0
    return {"name": "SEC-CABLE", "role": "auto-cable", "shape": "SR",
            "dims": [numg(d) if d else "0"], "offset": "CC", "shear": "YES", "warp": "NO"}


def material_list(model):
    """返回 4 条材料（顺序 = MCT 里的材料号）：主梁 / 桥塔 / 斜拉索 / 鱼骨刺虚拟材料。"""
    ms = model.get("materials") or {}

    def pick(key, name, typ):
        m = ms.get(key) or {}
        return {"name": aname(m.get("name"), name) or name,
                "type": (m.get("type") or typ),
                "elast": _f(m.get("elast"), 3.45e7),
                "poisn": _f(m.get("poisn"), 0.2),
                "den": _f(m.get("den"), 25.0),
                "thermal": _f(m.get("thermal"), 1e-5),
                "spec": txt(m.get("spec"))}

    girder = pick("girder", "MAT-GIRDER", "CONC")
    tower = pick("tower", "MAT-TOWER", "CONC")
    cable = pick("cable", "MAT-CABLE", "STEEL")
    # 鱼骨刺虚拟材料：弹模 = 主梁 ×1e4，容重、线膨胀系数取 0（课件 3.2.4 原文）
    fish = {"name": "MAT-FISHBONE", "type": "USER",
            "elast": girder["elast"] * 1.0e4, "poisn": girder["poisn"],
            "den": 0.0, "thermal": 0.0, "spec": ""}
    return [girder, tower, cable, fish]


def dead_load(model):
    """恒载（kN/m）：主梁自重 + 二期恒载。用于「按恒载平衡」估算初拉力。"""
    secs = model.get("sections") or []
    gi = role_index(model, "girder")
    a = shape_area(secs[gi - 1]) if 1 <= gi <= len(secs) else 0.0
    gden = _f((model.get("materials") or {}).get("girder", {}).get("den"), 25.0)
    q_self = a * gden
    q2 = max(0.0, _f((model.get("loads") or {}).get("q2"), 60.0))
    return q_self, q2, q_self + q2


# ===========================================================================
# 四、几何：节点 / 单元 / 索
# ===========================================================================
def _tributaries(dists, far_end, far_support=False):
    """每根索分担的「恒载分仓长度」l_trib（m）——「按恒载平衡估算」用。

    * 相邻两根索之间：各分一半（从两根索的中点算起）；
    * 靠塔的一端：塔/横梁本身是支承，只算半个分仓（另一半由塔承担）；
    * 远端是桥台（边跨外端，far_support=True）：同样只算半个分仓；
    * 远端是跨中（主跨，far_support=False）：那里没有支承（对称点剪力为 0），
      所以端部那根索把剩下的梁段全部吃下。

    于是每侧索的竖向分力之和 = q ×（该侧分仓总和），学生能直接对上账。
    """
    n = len(dists)
    if n <= 0:
        return []
    trib = []
    for k in range(n):
        start = dists[0] / 2.0 if k == 0 else (dists[k - 1] + dists[k]) / 2.0
        if k < n - 1:
            end = (dists[k] + dists[k + 1]) / 2.0
        else:
            end = float(far_end) if not far_support else (dists[k] + float(far_end)) / 2.0
        trib.append(max(0.0, end - start))
    return trib


class _Mesh(object):
    def __init__(self):
        self.nodes = []          # [(x, y, z)]，下标+1 = 节点号
        self.elements = []       # [{"no","type","mat","sect","i","j","role"}]
        self._key = {}           # 逻辑键 -> 节点号

    def node(self, key, x, y, z):
        if key is not None and key in self._key:
            return self._key[key]
        self.nodes.append((float(x), float(y), float(z)))
        nid = len(self.nodes)
        if key is not None:
            self._key[key] = nid
        return nid

    def elem(self, typ, mat, sect, i, j, role):
        self.elements.append({"no": len(self.elements) + 1, "type": typ, "mat": int(mat),
                              "sect": int(sect), "i": int(i), "j": int(j), "role": role})
        return len(self.elements)


def build_geometry(model):
    """把参数算成节点/单元/索/边界。返回值里的节点号都是 1 起。

    返回 dict：
      params      参数
      nodes       [(x, y, z)]
      elements    [{no,type,mat,sect,i,j,role}]
      cables      每处索一条（双索面时含 2 个单元）：{key,tower,side,k,plane_elems,...}
      rigid_links [{master, slaves, group}]
      elastic_links [{n1, n2, sd[6], group}]
      supports    [{nodes, code, group}]
      node_groups / elem_groups  结构组的节点/单元号
    """
    p = params(model)
    mat_no = {"girder": 1, "tower": 2, "cable": 3, "fishbone": 4}
    sec_g = _sect_or(model, "girder", 1)
    sec_t = _sect_or(model, "tower", 2)
    sec_f = _sect_or(model, "fishbone", 3)
    sec_c = len(model.get("sections") or []) + 1        # 自动追加的索截面
    z_deck = p["z_deck"]
    double = (p["plane"] == "double")
    y_off = p["B_cable"] / 2.0

    # ---- 1) 主梁节点 x 坐标：等分 + 索锚点 + 支承点 ----
    xs = set()

    def addx(x):
        xs.add(_r(x))

    addx(0.0)
    addx(p["xL"])
    addx(p["xR"])
    addx(p["x_end"])
    for k in range(1, p["n_g_side"] + 1):
        addx(p["Ls1"] * k / p["n_g_side"])
        addx(p["xR"] + p["Ls2"] * k / p["n_g_side"])
    for k in range(1, p["n_g_main"] + 1):
        addx(p["xL"] + p["Lm"] * k / p["n_g_main"])

    # 索在主梁上的锚固位置（先算出来，节点表里必须有这些 x）
    specs = []                       # 每根索的位置参数（后面再乘索面）
    for t, xt in enumerate((p["xL"], p["xR"])):
        to_main = 1.0 if t == 0 else -1.0        # 主跨方向：左塔往右，右塔往左
        for k in range(1, p["n_main"] + 1):
            specs.append({"tower": t, "xt": xt, "side": "main", "k": k,
                          "xg": xt + to_main * k * p["ds"], "dist": k * p["ds"]})
        for k in range(1, p["n_side"] + 1):
            specs.append({"tower": t, "xt": xt, "side": "side", "k": k,
                          "xg": xt - to_main * k * p["ds_side"], "dist": k * p["ds_side"]})
    for s in specs:
        addx(s["xg"])

    # 支承点（辅助墩）
    bnd = model.get("bnd") or {}
    aux_on = bool(bnd.get("aux_on"))
    aux_a = _f(bnd.get("aux_a"), 20.0)
    aux_xs = []
    if aux_on:
        for x in (_r(aux_a), _r(p["x_end"] - aux_a)):
            if 1e-0 < x < p["x_end"] - 1e-6:
                aux_xs.append(x)
                addx(x)
    xs_sorted = sorted(xs)

    # ---- 2) 桥塔节点 z 坐标 ----
    zs = set([_r(p["z_base"]), _r(p["z_deck"]), _r(p["z_top"])])
    for k in range(1, p["n_t"] + 1):
        zs.add(_r(p["z_base"] + (p["z_deck"] - p["z_base"]) * k / p["n_t"]))
        zs.add(_r(p["z_deck"] + (p["z_top"] - p["z_deck"]) * k / p["n_t"]))
    z_anchor = [_r(z) for z in anchor_heights(p)]
    zs.update(z_anchor)
    zs_sorted = sorted(zs)

    mesh = _Mesh()
    node_groups = {GR_GIRDER: [], GR_TOWER: [], GR_CABLE: [], GR_FISH: []}

    # ---- 3) 桥塔轴线节点 ----
    tower_ids = []
    for t, xt in enumerate((p["xL"], p["xR"])):
        ids = [mesh.node(("T", t, z), xt, 0.0, z) for z in zs_sorted]
        tower_ids.append(ids)
        node_groups[GR_TOWER].extend(ids)
        for a, b in zip(ids, ids[1:]):
            mesh.elem("BEAM", mat_no["tower"], sec_t, a, b, "tower")

    # ---- 4) 塔上索锚点（双索面：锚点在 y=±Bc/2，用 *RIGIDLINK 主从到塔轴线）----
    rigid_links = []
    tower_anchor = {}                      # (t, k, plane) -> 节点号
    if double:
        for t, xt in enumerate((p["xL"], p["xR"])):
            for k, z in enumerate(z_anchor):
                if k >= p["n_level"]:
                    continue
                master = mesh.node(("T", t, z), xt, 0.0, z)
                slaves = []
                for plane in (-1, 1):
                    nid = mesh.node(("TA", t, z, plane), xt, plane * y_off, z)
                    tower_anchor[(t, k, plane)] = nid
                    node_groups[GR_TOWER].append(nid)
                    slaves.append(nid)
                rigid_links.append({"master": master, "slaves": slaves, "group": BG_LINK})
    else:
        for t, xt in enumerate((p["xL"], p["xR"])):
            for k, z in enumerate(z_anchor):
                if k >= p["n_level"]:
                    continue
                tower_anchor[(t, k, 0)] = mesh.node(("T", t, z), xt, 0.0, z)

    # ---- 5) 主梁节点（桥轴 y=0）----
    g_ids = {}
    for x in xs_sorted:
        nid = mesh.node(("G", x), x, 0.0, z_deck)
        g_ids[x] = nid
        node_groups[GR_GIRDER].append(nid)
    for a, b in zip(xs_sorted, xs_sorted[1:]):
        mesh.elem("BEAM", mat_no["girder"], sec_g, g_ids[a], g_ids[b], "girder")

    # ---- 6) 主梁上的索锚点 + 鱼骨刺刚性横梁 ----
    g_anchor = {}                          # (x, plane) -> 节点号
    if double:
        for s in specs:
            x = _r(s["xg"])
            for plane in (-1, 1):
                if (x, plane) in g_anchor:
                    continue
                nid = mesh.node(("GA", x, plane), x, plane * y_off, z_deck)
                g_anchor[(x, plane)] = nid
                node_groups[GR_FISH].append(nid)
                mesh.elem("BEAM", mat_no["fishbone"], sec_f, g_ids[x], nid, "fishbone")
    else:
        for s in specs:
            x = _r(s["xg"])
            g_anchor[(x, 0)] = g_ids[x]

    # ---- 7) 斜拉索（TENSTR 索单元）----
    cables = []
    q_self, q2, q = dead_load(model)
    factor = _f((model.get("cable") or {}).get("factor"), 1.0)
    tribs = {}                     # 每根索分担的恒载分仓长度 l_trib（按塔、按侧）
    for t in (0, 1):
        for side in ("main", "side"):
            ds = p["ds"] if side == "main" else p["ds_side"]
            n = p["n_main"] if side == "main" else p["n_side"]
            span_end = (p["Lm"] / 2.0) if side == "main" else (p["Ls1"] if t == 0 else p["Ls2"])
            dists = [k * ds for k in range(1, n + 1)]
            tribs[(t, side)] = _tributaries(dists, span_end, far_support=(side == "side"))
    for s in specs:
        t, side, k = s["tower"], s["side"], s["k"]
        x = _r(s["xg"])
        z_t = z_anchor[k - 1]
        dz = z_t - z_deck
        dx = abs(x - s["xt"])
        length = math.hypot(dx, dz)
        alpha = math.atan2(dz, dx) if dx > 1e-9 else math.pi / 2.0
        trib = tribs[(t, side)][k - 1]
        sin_a = math.sin(alpha) if abs(alpha) > 1e-9 else 1.0
        t_est = q * trib / (p["n_planes"] * sin_a) * factor if sin_a > 1e-6 else 0.0
        item = {"key": "%s%d%s%d" % ("T", t, "M" if side == "main" else "S", k),
                "tower": t, "side": side, "k": k, "x": x, "xt": s["xt"],
                "z_t": z_t, "z_g": z_deck, "dist": dx, "trib": trib,
                "alpha": alpha, "length": length, "t_est": t_est, "elems": []}
        planes = (-1, 1) if double else (0,)
        for plane in planes:
            i_node = tower_anchor[(t, k - 1, plane if double else 0)]
            j_node = g_anchor[(x, plane if double else 0)]
            el = mesh.elem("TENSTR", mat_no["cable"], sec_c, i_node, j_node, "cable")
            item["elems"].append(el)
            node_groups[GR_CABLE].append(i_node)
            node_groups[GR_CABLE].append(j_node)
        cables.append(item)

    # ---- 8) 塔梁连接 ----
    elastic_links = []
    mode = str(bnd.get("deck_link") or "rigid").strip()
    kd = max(1.0, _f(bnd.get("kd"), 1e9))
    kx = max(0.0, _f(bnd.get("kx"), 1e5))
    for t, xt in enumerate((p["xL"], p["xR"])):
        tn = mesh.node(("T", t, _r(z_deck)), xt, 0.0, z_deck)
        gn = mesh.node(("G", _r(xt)), xt, 0.0, z_deck)
        if mode == "rigid":
            rigid_links.append({"master": tn, "slaves": [gn], "group": BG_LINK})
        else:
            kx_eff = kx if mode == "semi" else 0.0
            elastic_links.append({"n1": tn, "n2": gn,
                                  "sd": [kx_eff, kd, kd, 0.0, 0.0, 0.0],
                                  "group": BG_LINK})

    # ---- 9) 支承 ----
    supports = []
    if bool(bnd.get("tower_fix", True)):
        supports.append({"nodes": [mesh.node(("T", t, _r(p["z_base"])), xt, 0.0, p["z_base"])
                                   for t, xt in enumerate((p["xL"], p["xR"]))],
                         "code": "111111", "group": BG_TOWER})
    code_deck = "011000" if bool(bnd.get("abut_dy")) else "001000"
    if bool(bnd.get("abut_dz", True)):
        supports.append({"nodes": [g_ids[_r(0.0)], g_ids[_r(p["x_end"])]],
                         "code": code_deck, "group": BG_DECK})
    if aux_xs:
        code_aux = "011000" if bool(bnd.get("aux_dy")) else "001000"
        supports.append({"nodes": [g_ids[x] for x in aux_xs if x in g_ids],
                         "code": code_aux, "group": BG_DECK})

    elem_groups = {GR_GIRDER: [], GR_TOWER: [], GR_CABLE: [], GR_FISH: []}
    for el in mesh.elements:
        if el["role"] in (GR_GIRDER.lower(),):
            elem_groups[GR_GIRDER].append(el["no"])
        elif el["role"] == "tower":
            elem_groups[GR_TOWER].append(el["no"])
        elif el["role"] == "cable":
            elem_groups[GR_CABLE].append(el["no"])
        elif el["role"] == "fishbone":
            elem_groups[GR_FISH].append(el["no"])

    return {"params": p, "nodes": mesh.nodes, "elements": mesh.elements,
            "cables": cables, "rigid_links": rigid_links, "elastic_links": elastic_links,
            "supports": supports, "node_groups": node_groups, "elem_groups": elem_groups,
            "sect": {"girder": sec_g, "tower": sec_t, "fishbone": sec_f, "cable": sec_c},
            "mat_no": mat_no, "dead": (q_self, q2, q), "keys": dict(mesh._key)}


# ===========================================================================
# 五、初拉力（三种给法）
# ===========================================================================
def loadcase_names(model):
    """斜拉桥模型里会生成的荷载工况名（屈曲分析选「可变荷载」时用）。"""
    return [LC_DL, LC_2ND, LC_PRE]


def cable_items(model, geo=None):
    """返回每处索的最终数据（含采用的初拉力 T）。build_mct 与界面共用。"""
    if geo is None:
        geo = build_geometry(model)
    cfg = model.get("cable") or {}
    mode = str(cfg.get("mode") or "balance").strip()
    tuni = _f(cfg.get("T_uniform"), 3000.0)
    saved = {}
    for it in (cfg.get("items") or []):
        if isinstance(it, dict) and it.get("key"):
            saved[it["key"]] = _f(it.get("T"), -1.0)
    items = []
    for it in geo["cables"]:
        c = dict(it)
        if mode == "uniform":
            c["T"] = tuni
        elif mode == "manual":
            v = saved.get(c["key"], -1.0)
            c["T"] = v if v > 0 else c["t_est"]
        else:
            c["T"] = c["t_est"]
        items.append(c)
    return items


def sync_cable_items(model):
    """把算出来的索表写回 model["cable"]["items"]（保留用户已经填过的 T）。"""
    geo = build_geometry(model)
    cfg = model.setdefault("cable", {})
    old = {}
    for it in (cfg.get("items") or []):
        if isinstance(it, dict) and it.get("key"):
            old[it["key"]] = txt(it.get("T"))
    new = []
    for it in cable_items(model, geo):
        rec = {k: it[k] for k in ("key", "tower", "side", "k", "x", "z_t", "dist",
                                  "alpha", "length", "trib", "t_est", "elems")}
        rec["T"] = old.get(it["key"], num(it["T"]))
        if rec["T"] == "":
            rec["T"] = num(it["T"])
        new.append(rec)
    cfg["items"] = new
    return new


def tension_range(model):
    try:
        items = cable_items(model)
    except Exception:                                          # noqa: BLE001
        return (0.0, 0.0)
    vals = [it["T"] for it in items if it["T"] > 0]
    return (min(vals), max(vals)) if vals else (0.0, 0.0)


# ===========================================================================
# 六、统计 / 示意图数据
# ===========================================================================
def summary(model, geo=None):
    """界面上的「节点/单元/索数量」摘要。"""
    if geo is None:
        geo = build_geometry(model)
    p = geo["params"]
    mid_gap, tail1, tail2 = span_gaps(p)
    tmin, tmax = tension_range(model)
    n_cab_el = sum(len(it["elems"]) for it in geo["cables"])
    return {
        "n_node": len(geo["nodes"]),
        "n_elem": len(geo["elements"]),
        "n_girder": len(geo["elem_groups"][GR_GIRDER]),
        "n_tower": len(geo["elem_groups"][GR_TOWER]),
        "n_fish": len(geo["elem_groups"][GR_FISH]),
        "n_cable_el": n_cab_el,
        "n_cable_item": len(geo["cables"]),
        "n_rigid": len(geo["rigid_links"]),
        "n_elastic": len(geo["elastic_links"]),
        "mid_gap": mid_gap, "tail1": tail1, "tail2": tail2,
        "Tmin": tmin, "Tmax": tmax,
        "x_end": p["x_end"],
        "A_girder": shape_area((model.get("sections") or [{}])[
            max(0, role_index(model, "girder") - 1)]),
        "dead": geo["dead"],
    }


def sketch(model):
    """桥型示意图用的简单几何（不建网格，画图时每敲一个字都能重算）。"""
    p = params(model)
    items = []
    try:
        items = cable_items(model)
    except Exception:                                          # noqa: BLE001
        items = []
    cables = [(it["xt"], it["z_t"], it["x"], it["z_g"]) for it in items]
    return {"p": p, "cables": cables,
            "towers": [(p["xL"], p["z_base"], p["z_top"]), (p["xR"], p["z_base"], p["z_top"])],
            "deck": [(0.0, p["z_deck"]), (p["x_end"], p["z_deck"])]}


# ===========================================================================
# 七、校验
# ===========================================================================
def issues(model, sid=None):
    """当前这一步的问题（空列表 = 可以进下一步）。"""
    g = model.get("geom") or {}
    out = []
    if sid in (None, "geom"):
        for key, label in (("L_side1", "左边跨"), ("L_main", "主跨"), ("L_side2", "右边跨")):
            v = txt(g.get(key)).strip()
            if v == "":
                out.append("%s没有填。" % label)
            else:
                try:
                    fv = float(v)
                except ValueError:
                    out.append("%s要填数字。" % label)
                    continue
                if fv <= 0:
                    out.append("%s必须大于 0（现在是 %s）。" % (label, v))
        try:
            hab = float(txt(g.get("H_above")).strip())
        except (TypeError, ValueError):
            hab = 0.0
        if hab <= 0:
            out.append("桥面以上塔高必须大于 0。")
        try:
            hbe = float(txt(g.get("H_below")).strip())
        except (TypeError, ValueError):
            hbe = -1.0
        if hbe < 0:
            out.append("桥面以下塔高不能是负数（填 0 表示塔柱只从桥面起）。")
        nm = _i(g.get("n_main"), 0, lo=0)
        ns = _i(g.get("n_side"), 0, lo=0)
        if nm < 1 or ns < 1:
            out.append("每塔每侧的索数至少 1 根（主跨侧 %d、边跨侧 %d）。" % (nm, ns))
        if nm + ns < 2:
            out.append("全桥至少要有 2 根索，请把每塔每侧的索数调大一点。")
        ds = _f(g.get("ds"), 0.0)
        dss = _f(g.get("ds_side"), 0.0)
        if ds <= 0:
            out.append("主跨侧索距必须大于 0。")
        if dss <= 0:
            out.append("边跨侧索距必须大于 0。")
        Lm = _f(g.get("L_main"), 0.0)
        Ls1 = _f(g.get("L_side1"), 0.0)
        Ls2 = _f(g.get("L_side2"), 0.0)
        mid_gap, tail1, tail2 = span_gaps(params(model))
        if ds > 0 and nm > 0 and mid_gap <= 0:
            out.append("主跨太小：两侧索各占 %g m，主跨只剩 %g m 的无索区；"
                       "请加大主跨、减小索距或减少索数。" % (nm * ds, mid_gap))
        if dss > 0 and ns > 0 and tail1 <= 0:
            out.append("左边跨太小：%d 根索×%g m 已经顶到梁端（余 %g m），请加大边跨或减少索数。"
                       % (ns, dss, tail1))
        if dss > 0 and ns > 0 and tail2 <= 0:
            out.append("右边跨太小：%d 根索×%g m 已经顶到梁端（余 %g m），请加大边跨或减少索数。"
                       % (ns, dss, tail2))
        h0 = _f(g.get("h0"), 0.0)
        hz = _f(g.get("hz"), 0.0)
        if h0 <= 0:
            out.append("塔上首根索的锚固高度必须大于 0（一般在桥面以上 8~20 m）。")
        if hz <= 0 and _i(g.get("n_main"), 1) + _i(g.get("n_side"), 1) > 2:
            out.append("塔上索锚固的竖向间距必须大于 0。")
        if h0 > 0 and hz >= 0:
            ztop = _f(g.get("z_deck"), 0.0) + hab
            zmax = _f(g.get("z_deck"), 0.0) + h0 + (max(nm, ns) - 1) * hz
            if zmax >= ztop:
                out.append("塔太矮：最高的索锚点在 %g m（桥面以上 %g m），已经到/超过塔顶；"
                           "请加大桥面以上塔高、减小锚固高度或减小竖向间距。"
                           % (zmax, zmax - _f(g.get("z_deck"), 0.0)))
        if str(g.get("plane", "double")) == "double":
            if _f(g.get("B_cable"), 0.0) <= 0:
                out.append("双索面的索面间距必须大于 0。")
        for key, label in (("n_g_main", "主跨主梁分段数"), ("n_g_side", "边跨主梁分段数"),
                           ("n_t", "塔柱分段数")):
            if _i(g.get(key), 0, lo=0) < 1:
                out.append("%s至少 1。" % label)
        if bool((model.get("bnd") or {}).get("aux_on")):
            a = _f((model.get("bnd") or {}).get("aux_a"), 0.0)
            if a <= 0:
                out.append("辅助墩距梁端距离必须大于 0。")
            elif Ls1 > 0 and a >= Ls1 - 1e-6:
                out.append("辅助墩落在塔位或主跨里了：距梁端 %g m，边跨只有 %g m。" % (a, Ls1))
            elif Ls2 > 0 and a >= Ls2 - 1e-6:
                out.append("辅助墩落在塔位或主跨里了：距梁端 %g m，右边跨只有 %g m。" % (a, Ls2))
        del Lm, Ls1, Ls2
    if sid in (None, "section"):
        secs = model.get("sections") or []
        if not secs:
            out.append("截面库是空的：至少要有主梁和桥塔两个截面。")
        else:
            gi = role_index(model, "girder")
            ti = role_index(model, "tower")
            if not gi:
                out.append("还没指定「主梁」用哪个截面（在「截面角色」里选一个）。")
            if not ti:
                out.append("还没指定「桥塔」用哪个截面（在「截面角色」里选一个）。")
            for role, label in (("girder", "主梁"), ("tower", "桥塔")):
                i = role_index(model, role)
                if not i:
                    continue
                sec = secs[i - 1]
                code = shape_code_of(sec)
                if code == "CAD":
                    pr = sec.get("props") or {}
                    if _f(pr.get("A"), 0.0) <= 0:
                        out.append("%s用的自定义截面还没算出面积（DXF 没读成功），请重新导入。" % label)
                    continue
                dims = sec.get("dims") or []
                shape_dims = {"SB": 2, "SR": 1, "L": 4, "T": 4, "C": 6, "H": 8, "B": 6, "P": 2}
                need = shape_dims.get(code, 2)
                for k in range(need):
                    v = txt(dims[k] if k < len(dims) else "").strip()
                    if v == "":
                        out.append("%s截面的第 %d 个尺寸没填（%s）。" % (label, k + 1, code))
                        break
                    try:
                        if float(v) <= 0:
                            out.append("%s截面的尺寸必须都大于 0（第 %d 个是 %s）。" % (label, k + 1, v))
                            break
                    except ValueError:
                        out.append("%s截面的尺寸要填数字（第 %d 个是「%s」）。" % (label, k + 1, v))
                        break
    if sid in (None, "material"):
        if cable_area(model) <= 0:
            out.append("斜拉索面积必须大于 0（面积=直径换算也行）。")
        for key, label in (("girder", "主梁"), ("tower", "桥塔")):
            mat = (model.get("materials") or {}).get(key) or {}
            if _f(mat.get("elast"), 0.0) <= 0:
                out.append("%s材料的弹性模量必须大于 0。" % label)
            if _f(mat.get("den"), 0.0) < 0:
                out.append("%s材料的容重不能是负数。" % label)
        mat = (model.get("materials") or {}).get("cable") or {}
        if _f(mat.get("elast"), 0.0) <= 0:
            out.append("斜拉索材料的弹性模量必须大于 0。")
        cfg = model.get("cable") or {}
        mode = str(cfg.get("mode") or "balance")
        if mode == "uniform" and _f(cfg.get("T_uniform"), 0.0) <= 0:
            out.append("统一初拉力必须大于 0（MIDAS 的初拉力荷载只接受正值）。")
        if mode == "manual":
            bad = [it for it in (cfg.get("items") or [])
                   if isinstance(it, dict) and _f(it.get("T"), 0.0) <= 0]
            if bad:
                out.append("逐索输入里有 %d 根索的初拉力不是正数（初拉力必须为正）。" % len(bad))
        try:
            f = float(txt(cfg.get("factor")).strip())
            if f <= 0:
                out.append("初拉力系数必须大于 0。")
        except ValueError:
            out.append("初拉力系数要填数字。")
    if sid in (None, "export"):
        out += analysis_core.issues(model, kinds=("eigen", "buckle"),
                                    loadcase_names=loadcase_names(model))
    if sid in (None, "boundary"):
        bnd = model.get("bnd") or {}
        mode = str(bnd.get("deck_link") or "rigid")
        if mode not in ("rigid", "semi", "float"):
            out.append("塔梁连接方式要选「固结 / 半飘浮 / 飘浮」之一。")
        if mode == "semi" and _f(bnd.get("kx"), 0.0) <= 0:
            out.append("半飘浮的纵向弹簧刚度必须大于 0（要完全自由请选「飘浮」）。")
        if mode in ("semi", "float") and _f(bnd.get("kd"), 0.0) <= 0:
            out.append("弹性连接的竖向/横向刚度必须大于 0。")
        if _f((model.get("loads") or {}).get("q2"), 0.0) < 0:
            out.append("二期恒载填正值（程序按向下加载），不能填负数。")
        if not bool(bnd.get("tower_fix", True)) and not bool(
                [s for s in build_geometry(model)["supports"]
                 if s["group"] in (BG_TOWER, BG_DECK)]):
            out.append("全桥没有任何约束，模型会飘着；至少要固定塔底。")
    return out


def all_issues(model):
    out = []
    for sid in STEP_IDS:
        out.extend(issues(model, sid))
    return out


# ===========================================================================
# 八、MCT 生成
# ===========================================================================
def _fmt(v, nd=6):
    """MCT 数字：优先短整数，其余用有效数字，避免 0.30000000000000004。"""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return "0"
    if f == int(f) and abs(f) < 1e15:
        return str(int(f))
    return "%.*g" % (nd, f)


def _ranges(ids):
    """节点/单元列表 → MIDAS 的 '1to34 36to57 60' 写法。"""
    ids = sorted(set(int(i) for i in ids))
    out = []
    i = 0
    while i < len(ids):
        j = i
        while j + 1 < len(ids) and ids[j + 1] == ids[j] + 1:
            j += 1
        if j - i >= 2:
            out.append("%dto%d" % (ids[i], ids[j]))
        else:
            out.extend(str(v) for v in ids[i:j + 1])
        i = j + 1
    return " ".join(out)


def _section_lines(model):
    """截面块（*SECT-PSCVALUE + *SECTION）。

    截面 MCT 由 Lead 维护的 sect_ui 统一输出（和梁桥流程逐字段一致）；
    sect_ui 顶部 import 了 tkinter，所以这里延迟导入 —— 引擎本身（几何 / 初拉力 /
    MCT 组装）不依赖 tkinter，只有在有 tkinter 的环境里才借用它的写行函数。
    万一没有 tkinter，就退回本文件的等价实现（参数化截面 + 数值截面）。
    """
    secs = [dict(s) for s in (model.get("sections") or [])]
    secs.append(cable_section(model))
    try:
        import sect_ui
        return sect_ui.section_mct_lines(secs)
    except Exception:                                          # noqa: BLE001
        return _section_lines_plain(secs)


def _section_lines_plain(secs):
    """sect_ui 用不了时的等价实现（格式与 sect_ui 完全一致）。"""
    L = []
    std = []
    cad = []
    for i, s in enumerate(secs, start=1):
        if shape_code_of(s) == "CAD":
            cad.append((i, s))
        else:
            std.append((i, s))
    if cad:
        L.append("*SECT-PSCVALUE    ; PSC Value, General Section")
        L.append(";      AREA, ASy, ASz, Ixx, Iyy, Izz")
        for i, s in cad:
            pr = s.get("props") or {}
            L.append(" SECT=%4d, PSC       , %-17s , %s, 0, 0, 0, 0, 0, 0, %s, %s, VALU, NO, NO, "
                     % (i, aname(s.get("name"), "SEC%d" % i), s.get("offset", "CC"),
                        s.get("shear", "YES"), s.get("warp", "NO")))
            L.append("        %s, %s, %s, %s, %s, %s"
                     % (numg(pr.get("A")), numg(pr.get("ASy")), numg(pr.get("ASz")),
                        numg(pr.get("Ixx")), numg(pr.get("Iyy")), numg(pr.get("Izz"))))
        L.append("")
    if std:
        L.append("*SECTION    ; Section Properties")
        L.append("; iSEC, TYPE, SNAME, [OFFSET], bSD, bWE, SHAPE, BLT, D1, ..., D8, iCEL")
        for i, s in std:
            code = shape_code_of(s)
            dims = [_fmt(v) for v in (s.get("dims") or [])]
            vals = (dims + ["0"] * 10)[:10]
            L.append("   %d, DBUSER, %s, %s, 0, 0, 0, 0, 0, 0, %s, %s, %s, 2, %s"
                     % (i, aname(s.get("name"), "SEC%d" % i), s.get("offset", "CC"),
                        s.get("shear", "YES"), s.get("warp", "NO"), code, ", ".join(vals)))
        L.append("")
    return L


def build_mct(model):
    """生成 MIDAS Civil NX 的 MCT 文本（写文件时按 GBK 存）。"""
    geo = build_geometry(model)
    p = geo["params"]
    prj = model.get("project") or {}
    mats = material_list(model)
    items = cable_items(model, geo)
    name = txt(prj.get("name")).strip() or "斜拉桥"
    force = txt(prj.get("force")).strip() or "KN"
    dist = txt(prj.get("dist")).strip() or "M"
    heat = txt(prj.get("heat")).strip() or "KJ"
    tunit = txt(prj.get("temp_unit")).strip() or "C"
    bnd = model.get("bnd") or {}
    loads = model.get("loads") or {}

    L = []
    add = L.append
    add("; ================================================================")
    add("; MIDAS Civil NX — MCT 命令文件（斜拉桥 · 鱼骨刺 · 成桥）")
    add("; 由「MIDAS 建模向导」生成  " + datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    add("; 项目名称：" + name)
    add("; 导入：MIDAS Civil NX → 文件 → 导入 → MCT 命令文件")
    add("; 建模方法：主梁/桥塔 BEAM + 斜拉索 TENSTR + 鱼骨刺虚拟横梁 + 塔梁连接")
    add("; 说明：鱼骨刺横梁是虚拟梁（弹模=主梁×1e4，容重/线膨胀系数=0）；")
    add(";       斜拉索初拉力写在 *PRETENSION（正值=受拉），工况 %s。" % LC_PRE)
    add("; ================================================================")
    add("")
    add("*VERSION")
    add("   9.6.5")
    add("")
    add("*UNIT    ; Unit System")
    add("; FORCE, LENGTH, HEAT, TEMPER")
    add("   %s, %s, %s, %s" % (force, dist, heat, tunit))
    add("")
    add("*STRUCTYPE    ; Structure Type")
    add("; iSTYP, iMASS, iSMAS, bMASSOFFSET, bSELFWEIGHT, GRAV, TEMPER, bALIGNBEAM, bALIGNSLAB, bROTRIGID")
    add("   0, 1, 1, NO, NO, 9.806, 0, NO, NO, NO")
    add("")

    # ---- 材料 ----
    add("*MATERIAL    ; Material")
    add("; iMAT, TYPE, MNAME, SPHEAT, HEATCO, PLAST, TUNIT, bMASS, DAMPRATIO, [DATA1]")
    add("; [DATA1] : 2, ELAST, POISN, THERMAL, DEN, MASS")
    for i, mat in enumerate(mats, start=1):
        add("   %d, %-5s, %s, 0, 0, , C, NO, 0.05, 2, %s, %s, %s, %s, 0"
            % (i, mat["type"], mat["name"], _fmt(mat["elast"]), _fmt(mat["poisn"]),
               _fmt(mat["thermal"]), _fmt(mat["den"])))
    add("")

    # ---- 截面 ----
    for ln in _section_lines(model):
        add(ln)

    # ---- 节点 ----
    add("*NODE    ; Nodes")
    add("; iNO, X, Y, Z")
    for i, (x, y, z) in enumerate(geo["nodes"], start=1):
        add("   %d, %s, %s, %s" % (i, _fmt(x, 12), _fmt(y, 12), _fmt(z, 12)))
    add("")

    # ---- 单元 ----
    add("*ELEMENT    ; Elements")
    add("; iEL, TYPE, iMAT, iPRO, iN1, iN2, ANGLE, iSUB, EXVAL, EXVAL2, bLMT   ; Frame / Comp/Tens Truss")
    for el in geo["elements"]:
        if el["type"] == "TENSTR":
            # 与 CableStayed.mct 的索单元行逐字段一致（10 个字段）：
            #   iEL, TENSTR, iMAT, iPRO, iN1(塔), iN2(梁), ANGLE=0, iSUB=3(索单元), 索长, 3
            n1 = geo["nodes"][el["i"] - 1]
            n2 = geo["nodes"][el["j"] - 1]
            ln = math.sqrt(sum((n1[k] - n2[k]) ** 2 for k in range(3)))
            add("   %d, TENSTR, %d, %d, %d, %d, 0, 3, %s, 3"
                % (el["no"], el["mat"], el["sect"], el["i"], el["j"], _fmt(ln, 8)))
        else:
            add("   %d, BEAM, %d, %d, %d, %d, 0, 0"
                % (el["no"], el["mat"], el["sect"], el["i"], el["j"]))
    add("")

    # ---- 结构组 ----
    add("*GROUP    ; Group")
    add("; NAME, NODE_LIST, ELEM_LIST, PLANE_TYPE")
    ng, eg = geo["node_groups"], geo["elem_groups"]
    for gname, nodes, elems in (
            (GR_GIRDER, ng[GR_GIRDER], eg[GR_GIRDER]),
            (GR_TOWER, ng[GR_TOWER], eg[GR_TOWER]),
            (GR_CABLE, ng[GR_CABLE], eg[GR_CABLE]),
            (GR_FISH, ng[GR_FISH], eg[GR_FISH]),
            (GR_ALL, range(1, len(geo["nodes"]) + 1), [el["no"] for el in geo["elements"]])):
        add("   %s, %s, %s, 0" % (gname, _ranges(nodes), _ranges(elems)))
    add("")

    # ---- 边界组 / 荷载组 ----
    add("*BNDR-GROUP    ; Boundary Group")
    add("; NAME, AUTOTYPE")
    for bg in (BG_TOWER, BG_DECK, BG_LINK):
        add("   %s, 0" % bg)
    add("")
    add("*LOAD-GROUP    ; Load Group")
    add("; NAME")
    for lg in (LG_SW, LG_2ND, LG_PRE):
        add("   %s" % lg)
    add("")

    # ---- 荷载工况 ----
    add("*STLDCASE    ; Static Load Cases")
    add("; LCNAME, LCTYPE, DESC")
    add("   %s, D, Self Weight" % LC_DL)
    add("   %s, D, Additional Dead" % LC_2ND)
    add("   %s, USER, Initial Force" % LC_PRE)
    add("")

    # ---- 支承 ----
    if geo["supports"]:
        add("*CONSTRAINT    ; Supports")
        add("; NODE_LIST, CONST(Dx,Dy,Dz,Rx,Ry,Rz), GROUP")
        for sp in geo["supports"]:
            if not sp["nodes"]:
                continue
            add("   %s, %s, %s" % (_ranges(sp["nodes"]), sp["code"], sp["group"]))
        add("")

    # ---- 塔梁连接 / 塔上索锚点刚接 ----
    if geo["elastic_links"]:
        add("*ELASTICLINK    ; Elastic Link（塔梁连接）")
        add("; iNO, iNODE1, iNODE2, LINK, ANGLE, R_SDx, R_SDy, R_SDz, R_SRx, R_SRy, R_SRz, SDx, SDy, SDz, SRx, SRy, SRz, bSHEAR, DRy, DRz, GROUP")
        for i, lk in enumerate(geo["elastic_links"], start=1):
            sd = lk["sd"]
            add("   %d, %d, %d, GEN, 0, NO, NO, NO, NO, NO, NO, %s, %s, %s, %s, %s, %s, NO, 0.5, 0.5, %s"
                % (i, lk["n1"], lk["n2"], _fmt(sd[0]), _fmt(sd[1]), _fmt(sd[2]),
                   _fmt(sd[3]), _fmt(sd[4]), _fmt(sd[5]), lk["group"]))
        add("")
    if geo["rigid_links"]:
        add("*RIGIDLINK    ; Rigid Link（塔梁固结 / 塔上索锚点）")
        add("; M-NODE, DOF, S-NODE LIST, GROUP")
        for lk in geo["rigid_links"]:
            add("   %d, 111111, %s, %s" % (lk["master"], _ranges(lk["slaves"]), lk["group"]))
        add("")

    # ---- 自重 ----
    if bool(loads.get("sw_on", True)):
        add("*USE-STLD, " + LC_DL)
        add("*SELFWEIGHT    ; Self Weight")
        add("; X, Y, Z, GROUP")
        add("   %s, 0, %s, %s" % ("0", _fmt(loads.get("sw_z", -1)), LG_SW))
        add("")

    # ---- 二期恒载（梁单元荷载）----
    q2 = max(0.0, _f(loads.get("q2"), 60.0))
    g_elems = eg[GR_GIRDER]
    if q2 > 0 and g_elems:
        add("*USE-STLD, " + LC_2ND)
        add("*BEAMLOAD    ; Element Beam Loads")
        add("; ELEM_LIST, CMD, TYPE, DIR, bPROJ, [ECCEN], [VALUE], GROUP")
        add("; [VALUE] : D1, P1, D2, P2, D3, P3, D4, P4")
        for e in g_elems:
            add("     %d, BEAM   , UNILOAD, GZ, YES, NO, LY, , , , 0, %s, 1, %s, 0, 0, 0, 0, %s, NO, 0, 0, NO, "
                % (e, _fmt(-q2), _fmt(-q2), LG_2ND))
        add("")

    # ---- 斜拉索初拉力 ----
    pre = [(el, it["T"]) for it in items for el in it["elems"] if it["T"] > 0]
    if pre:
        add("*USE-STLD, " + LC_PRE)
        add("*PRETENSION    ; Pretension Loads")
        add("; ELEM_LIST, TENS, GROUP")
        for el, t in pre:
            add("     %d, %s, %s" % (el, _fmt(t), LG_PRE))
        add("")

    # ---- 分析控制 ----
    add("*ANAL-CTRL")
    add("; bARDC, bANRC, iITER, TOL, bCSECF, bTRS, bCRBAR, bTPF, bCSO, bSCS, bCLATS")
    add("   YES, YES, 20, 0.001, YES, YES, YES, NO, NO, NO, NO, YES")
    add("")

    # ---- 分析设置（可选，默认不写）：模态 / 屈曲 ----
    for _ln in analysis_core.lines(model, kinds=("eigen", "buckle")):
        add(_ln)

    add("*ENDDATA")
    return "\r\n".join(L) + "\r\n"


# ===========================================================================
# 九、人可读数据表
# ===========================================================================
def data_report(model):
    p = params(model)
    geo = build_geometry(model)
    sm = summary(model, geo)
    items = cable_items(model, geo)
    prj = model.get("project") or {}
    bnd = model.get("bnd") or {}
    loads = model.get("loads") or {}
    L = []
    add = L.append
    add("=" * 78)
    add("MIDAS Civil NX 建模向导 —— 斜拉桥（鱼骨刺·成桥）模型数据表")
    add("=" * 78)
    add("项目名称：%s" % (txt(prj.get("name")).strip() or "（未命名）"))
    add("生成时间：%s" % datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    add("单位制：力 %s，长度 %s" % (prj.get("force", "KN"), prj.get("dist", "M")))
    add("")
    add("-" * 78)
    add("一、桥型与几何")
    add("-" * 78)
    add("  三跨布置：边跨 %g m + 主跨 %g m + 边跨 %g m = 全长 %g m"
        % (p["Ls1"], p["Lm"], p["Ls2"], p["x_end"]))
    add("  桥面（主梁轴线）标高 %g m；塔顶标高 %g m；塔底标高 %g m"
        % (p["z_deck"], p["z_top"], p["z_base"]))
    add("  塔位：x = %g m（左塔）、%g m（右塔）" % (p["xL"], p["xR"]))
    add("  索面：%s%s" % (PLANE_NAMES[p["plane"]],
                         ("，索面间距 %g m" % p["B_cable"]) if p["plane"] == "double" else ""))
    add("  每塔：主跨侧 %d 根索（索距 %g m）、边跨侧 %d 根索（索距 %g m）"
        % (p["n_main"], p["ds"], p["n_side"], p["ds_side"]))
    add("  塔上锚固：首根距桥面 %g m，竖向间距 %g m，最高锚点标高 %g m"
        % (p["h0"], p["hz"], anchor_heights(p)[-1] if anchor_heights(p) else p["z_deck"]))
    add("  跨中无索区 %g m；左边跨尾索到梁端 %g m；右边跨尾索到梁端 %g m"
        % (sm["mid_gap"], sm["tail1"], sm["tail2"]))
    add("  主梁分段：主跨 %d 段、边跨各 %d 段；塔柱竖向分段 %d 段"
        % (p["n_g_main"], p["n_g_side"], p["n_t"]))
    add("")
    add("-" * 78)
    add("二、模型规模")
    add("-" * 78)
    add("  节点 %d 个，单元 %d 个" % (sm["n_node"], sm["n_elem"]))
    add("    主梁 BEAM %d 个、桥塔 BEAM %d 个、鱼骨刺虚拟横梁 %d 个、斜拉索 TENSTR %d 个"
        % (sm["n_girder"], sm["n_tower"], sm["n_fish"], sm["n_cable_el"]))
    add("  每个塔梁连接：%s；塔上索锚点刚性连接 %d 处"
        % (LINK_NAMES.get(str(bnd.get("deck_link") or "rigid"), "?"), sm["n_rigid"]))
    add("")
    add("-" * 78)
    add("三、材料")
    add("-" * 78)
    for i, mat in enumerate(material_list(model), start=1):
        add("  %d) %-14s %-6s E=%s kN/m²  ν=%s  容重=%s kN/m³  α=%s 1/℃"
            % (i, mat["name"], mat["type"], _fmt(mat["elast"]), _fmt(mat["poisn"]),
               _fmt(mat["den"]), _fmt(mat["thermal"])))
    add("    材料 4 是鱼骨刺虚拟材料：弹模取主梁×1e4，容重与线膨胀系数为 0（课件 3.2.4）。")
    add("")
    add("-" * 78)
    add("四、截面")
    add("-" * 78)
    secs = model.get("sections") or []
    for i, sec in enumerate(secs, start=1):
        role = {"girder": "（主梁）", "tower": "（桥塔）",
                "fishbone": "（鱼骨刺虚拟横梁）"}.get(sec.get("role"), "")
        add("  %d) %s %s" % (i, txt(sec.get("name")), role))
        code = shape_code_of(sec)
        shape = SHAPE_BY_CODE.get(code)
        dims = sec.get("dims") or []
        if shape:
            ds = "，".join("%s=%s" % (shape[2][k][0], txt(dims[k]) if k < len(dims) else "0")
                          for k in range(len(shape[2])))
            add("       形状 %s（%s），%s，偏心 %s" % (code, shape[1], ds, sec.get("offset", "CC")))
        else:
            add("       形状 %s（DXF 自定义），偏心 %s" % (code, sec.get("offset", "CC")))
        add("       面积 A = %.4g m²（程序按形状反算，用于估算恒载）" % shape_area(sec))
    add("  %d) %s（斜拉索，程序自动生成）" % (len(secs) + 1, cable_section(model)["name"]))
    add("       等效圆直径 %s m，面积 %s m²"
        % (cable_section(model)["dims"][0], _fmt(cable_area(model))))
    add("")
    add("-" * 78)
    add("五、荷载")
    add("-" * 78)
    add("  自重：%s（系数 Z = %s，荷载组 %s）"
        % ("计入" if loads.get("sw_on", True) else "不计入",
           loads.get("sw_z", "-1"), LG_SW))
    add("  二期恒载：%s kN/m（向下施加在主梁全部单元，荷载组 %s）"
        % (_fmt(max(0.0, _f(loads.get("q2"), 60.0))), LG_2ND))
    add("  初拉力方式：%s；系数 %s" % (TENSION_NAMES.get(str((model.get("cable") or {}).get("mode")),
                                                    "?"), (model.get("cable") or {}).get("factor")))
    add("    恒载 q = 主梁自重 %.4g + 二期 %.4g = %.4g kN/m"
        % (geo["dead"][0], geo["dead"][1], geo["dead"][2]))
    add("")
    add("-" * 78)
    add("六、边界")
    add("-" * 78)
    add("  塔底：%s" % ("固结（111111）" if bnd.get("tower_fix", True) else "未约束"))
    add("  塔梁连接：%s" % LINK_NAMES.get(str(bnd.get("deck_link") or "rigid"), "?"))
    if str(bnd.get("deck_link")) in ("semi", "float"):
        add("    弹性连接刚度：纵向 %s kN/m，竖向/横向 %s kN/m"
            % (_fmt(bnd.get("kx")), _fmt(bnd.get("kd"))))
    add("  桥台：%s%s" % ("竖向支承" if bnd.get("abut_dz", True) else "无竖向支承",
                        " + 横向约束" if bnd.get("abut_dy") else ""))
    if bnd.get("aux_on"):
        add("  辅助墩：距梁端 %s m%s" % (_fmt(bnd.get("aux_a")), "（含横向约束）" if bnd.get("aux_dy") else "（竖向）"))
    else:
        add("  辅助墩：不设")
    add("")
    add("-" * 78)
    add("七、斜拉索与初拉力（共 %d 根索 / %d 个 TENSTR 单元）"
        % (sm["n_cable_item"], sm["n_cable_el"]))
    add("-" * 78)
    add("  编号      位置x(m)   锚固标高(m)   倾角(°)   索长(m)   初拉力(kN)   单元号")
    for it in items:
        add("  %-6s  %8.2f  %11.2f  %8.2f  %8.3f  %10.1f   %s"
            % (it["key"], it["x"], it["z_t"], math.degrees(it["alpha"]),
               it["length"], it["T"], " ".join(str(e) for e in it["elems"])))
    add("")
    add("=" * 78)
    add("说明：本表是向导输入的存档，导入 MIDAS 用的是同目录下的 .mct 文件。")
    add("      初拉力是「成桥状态」的估算值（按恒载平衡），到 MIDAS 里建议再用")
    add("      未知荷载系数法 / 调索功能精调——本向导不做索力优化。")
    add("=" * 78)
    return "\r\n".join(L) + "\r\n"


# ===========================================================================
# 十、成果输出
# ===========================================================================
def export_bundle(model, out_dir, prefix, enc="gbk"):
    """写一套成果：.mct + _数据.json + _数据表.txt + 导入说明.txt。返回 [(标签, 路径)]。"""
    import json
    import os
    os.makedirs(out_dir, exist_ok=True)
    base = txt(prefix).strip() or "斜拉桥模型"
    for ch in '\\/:*?"<>|':
        base = base.replace(ch, "_")
    files = []

    mct_path = os.path.join(out_dir, base + ".mct")
    with open(mct_path, "w", encoding=enc, errors="replace", newline="") as fh:
        fh.write(text_for_enc(build_mct(model), enc))
    files.append(("MIDAS 模型命令文件（导入用）", mct_path))

    json_path = os.path.join(out_dir, base + "_数据.json")
    with open(json_path, "w", encoding="utf-8") as fh:
        json.dump(model, fh, ensure_ascii=False, indent=2)
    files.append(("向导数据（可再打开继续改）", json_path))

    rep_path = os.path.join(out_dir, base + "_数据表.txt")
    with open(rep_path, "w", encoding=enc, errors="replace", newline="") as fh:
        fh.write(text_for_enc(data_report(model), enc))
    files.append(("数据表（人可读）", rep_path))

    readme = os.path.join(out_dir, "导入说明.txt")
    with open(readme, "w", encoding=enc, errors="replace", newline="") as fh:
        fh.write(text_for_enc(
            "本文件夹是「MIDAS Civil NX 建模向导」为斜拉桥项目「%s」生成的成果。\r\n\r\n"
            "1) %s.mct\r\n"
            "   MIDAS 模型命令文件。在 MIDAS Civil NX 里：文件 → 导入 → MCT 命令文件，选中它即可生成模型。\r\n"
            "   也可以打开记事本复制里面的文字，粘贴到 MIDAS 的「工具 → 命令窗口」后点运行。\r\n\r\n"
            "2) %s_数据.json\r\n"
            "   向导里的全部输入。在程序里点「打开工程」选它，就能接着改。\r\n\r\n"
            "3) %s_数据表.txt\r\n"
            "   几何、材料、截面、边界、每根索的初拉力都列成表，导入后照着核对。\r\n\r\n"
            "导入以后建议核对这几件事：\r\n"
            "  * 斜拉索单元类型是「索」（TENSTR）；初拉力工况 %s 里的初拉力都是正值；\r\n"
            "  * 鱼骨刺横梁是虚拟梁：弹模 = 主梁 ×1e4，容重和线膨胀系数 = 0；\r\n"
            "  * 塔底约束、塔梁连接（%s）、桥台支承都在；\r\n"
            "  * 初拉力是按恒载平衡估的，成桥索力精调请用 MIDAS 的未知荷载系数法。\r\n"
            % (txt(model.get("project", {}).get("name")) or "（未命名）", base, base, base,
               LC_PRE, LINK_NAMES.get(str((model.get("bnd") or {}).get("deck_link")), "")), enc))
    files.append(("导入说明", readme))
    return files
