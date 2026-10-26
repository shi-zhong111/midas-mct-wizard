# -*- coding: utf-8 -*-
r"""钢管混凝土拱 · 施工阶段联合截面 —— 核心引擎（纯计算 + MCT 生成，不依赖 tkinter）。

用户只需要填三样东西：拱轴线 → 钢管/核心混凝土尺寸 → 施工阶段，程序生成两份
MIDAS Civil NX 能直接导入的 .mct：

    build_mct()          施工阶段模型（*STAGE + *CPOSECT4CS 联合截面）
    build_one_step_mct() 一次成拱 / 全截面一次浇筑的「简化对比模型」（单材料等效截面）

MCT 格式来源（重要：一律以本机 MIDAS 自己导出的样本为准，不照手册猜）
------------------------------------------------------------------
* 施工阶段 *STAGE、联合截面 *CPOSECT4CS、一般截面 *SECT-PSCVALUE、湿重 *BEAMLOAD、
  *STAGE-CTRL / *ANAL-CTRL 的写法，全部照 C:\Midas\work\e2e\施工阶段.mct ——
  这份模型在 MIDAS Civil NX 里真跑过施工阶段分析（有 .mcb / .OUT / .CA6 成果），
  是 arch_core.py 踩了无数次坑之后定下来的写法。
* 算例尺寸来自老师第 05 讲《钢管混凝土分析》：
    - 单圆管算例：钢管外径 φ=1 m、壁厚 t=0.1 m、Q485 钢材、C50 混凝土，
      跨径 L=50 m、矢高 f=10 m、二次抛物线、分隔 30 个单元；
    - 哑铃型算例：钢管外径 1000 mm、钢管及钢板壁厚均 16 mm、C50 混凝土。

关键格式（照 arch_core.py 的实测结论，别再踩一遍）
------------------------------------------------------------------
1) *CPOSECT4CS 每部件行必须 **37 个字段**：
      5   （PART, MTYPE, MAT, CSTAGE, AGE）
    + 7   （截面特性缩放系数，正常全 1）
    + 2   （偏心 CY, CZ）
    + 20  （刚度 AREA, ASy, ASz, Ixx, Iyy, Izz, CyP, CyM, CzP, CzM,
           Qyb, Qzb, Y1..Y4, Z1..Z4）
    + 3   （h, v/s, 刚度调整 M）
   多写一个字段（例如 IW）整份 MCT 会被 MIDAS「静默拒绝」（返回成功但模型是空的）。
2) 第 1 个部件（钢管，从第 1 阶段就随单元激活）的 CSTAGE / AGE 必须留空 / 0。
3) 基础截面必须是 PSC 类型：*SECT-PSCVALUE（多边形一般截面）。
   OPOLY 逆时针、IPOLY 顺时针；顶点不要重复首点。
4) 湿混凝土先按「均布力」加在灌注阶段，硬化阶段用 DLOAD 卸掉，同时联合截面里
   该部件才在 CSTAGE 阶段开始参与受力 —— 这样重量和刚度不会重复算。
"""

import datetime
import math

# ----------------------------------------------------------------------
# 常量
# ----------------------------------------------------------------------
CODE = "cfst"
LABEL = "钢管混凝土拱（施工阶段联合截面）"

GAMMA_CONC = 25.0          # 混凝土容重 kN/m3（与梁桥/拱桥流程一致）
GAMMA_STEEL = 78.5         # 钢材容重 kN/m3（7.85 t/m3 × 10，MIDAS 常用）
E_STEEL = 2.06e8           # 钢材弹性模量 kN/m2
NU_STEEL = 0.30
ALPHA_STEEL = 1.2e-05      # 线膨胀系数 1/℃

# 混凝土：等级 -> (弹性模量 kN/m2, 28 天抗压强度标准值 fck kPa)
# 取值同 arch_core.py（JTG 3362-18），fck 只在勾选收缩徐变时写 *TDM-ELAST 用
CONC = {
    "C30": (3.00e7, 20.1e3),
    "C35": (3.15e7, 23.4e3),
    "C40": (3.25e7, 26.8e3),
    "C45": (3.35e7, 29.6e3),
    "C50": (3.45e7, 32.4e3),
    "C55": (3.55e7, 35.5e3),
    "C60": (3.60e7, 38.5e3),
}
CONC_LIST = ["C30", "C35", "C40", "C45", "C50", "C55", "C60"]
STEEL_GRADES = [("Q235", 2.06e8), ("Q345", 2.06e8), ("Q390", 2.06e8),
                ("Q420", 2.06e8), ("Q485", 2.06e8)]     # 第 05 讲算例用的是 Q485
STEEL_LIST = [g[0] for g in STEEL_GRADES]

ARCH_TYPES = [("catenary", "悬链线"), ("parabola", "抛物线"), ("circle", "圆弧")]
ARCH_TYPE_NAMES = dict(ARCH_TYPES)

FORMS = [("single", "单圆管"), ("dumbbell", "哑铃形（两管 + 连接板）")]
FORM_NAMES = dict(FORMS)


# ----------------------------------------------------------------------
# 拱轴线（从 arch_core.py 复用；这里复制一份，让本模块自包含、能单独交付）
# ----------------------------------------------------------------------
def arch_axis(L, f, m=1.6, kind="catenary", n=40, z0=0.0):
    """返回拱轴线上 n+1 个点 [(x, z), ...]；左拱脚 z0，拱顶 z0+f。"""
    L, f, n = float(L), float(f), max(4, int(n))
    pts = []
    if kind == "catenary":
        mm = max(1.0000001, float(m))
        k = math.acosh(mm)
        c = f / (mm - 1.0)
        for i in range(n + 1):
            x = L * i / n
            xi = 2.0 * x / L - 1.0
            pts.append((x, z0 + f - c * (math.cosh(k * xi) - 1.0)))
    elif kind == "parabola":
        for i in range(n + 1):
            x = L * i / n
            xi = 2.0 * x / L - 1.0
            pts.append((x, z0 + f * (1.0 - xi * xi)))
    elif kind == "circle":
        R = (L * L / 4.0 + f * f) / (2.0 * f)
        for i in range(n + 1):
            x = L * i / n
            dx = x - L / 2.0
            pts.append((x, z0 + math.sqrt(max(0.0, R * R - dx * dx)) - (R - f)))
    else:
        raise ValueError("未知拱轴类型：%s" % kind)
    return pts


def arch_length(pts):
    return sum(math.hypot(pts[i + 1][0] - pts[i][0], pts[i + 1][1] - pts[i][1])
               for i in range(len(pts) - 1))


# ----------------------------------------------------------------------
# 数字/名字格式化（照抄 arch_core.py，保证和已验证模型逐字兼容）
# ----------------------------------------------------------------------
def _num(v, nd=6):
    if isinstance(v, int):
        return str(v)
    return "%.*g" % (nd, float(v))


def _fmt_cs(v):
    """*CPOSECT4CS 的数字格式：固定小数、不用科学计数法（实测 e 记法会被 MIDAS 拒）。"""
    if isinstance(v, str):
        return v
    try:
        f = float(v)
    except (TypeError, ValueError):
        return str(v)
    if abs(f) < 1e-9:
        return "0"
    s = ("%.6f" % f).rstrip("0").rstrip(".")
    return s if s not in ("", "-0") else "0"


def _fmt_pt(v):
    """多边形顶点坐标：固定小数（圆上 cos90° 会算出 6e-17，e 记法不能写进 MCT）。"""
    f = float(v)
    if abs(f) < 1e-9:
        return "0"
    s = ("%.9f" % f).rstrip("0").rstrip(".")
    return s if s not in ("", "-0") else "0"


def _ascii(s):
    return "".join(ch for ch in str(s) if ord(ch) < 128) or "P"


def _loop_area(lp):
    s = 0.0
    n = len(lp)
    for i in range(n):
        s += lp[i][0] * lp[(i + 1) % n][1] - lp[(i + 1) % n][0] * lp[i][1]
    return s * 0.5


def _ccw(poly):
    return poly if _loop_area(poly) > 0 else list(reversed(poly))


def _cw(poly):
    return list(reversed(_ccw(poly)))


def _f(v, default=0.0):
    """把界面上存成字符串的数值安全地转成 float。"""
    try:
        if v is None or str(v).strip() == "":
            return float(default)
        return float(str(v).strip())
    except (TypeError, ValueError):
        return float(default)


def _clean_loop(pts):
    """去相邻重复点、去闭合重复点（MIDAS 的 OPOLY 顶点不能重复首点）。"""
    out = []
    for p in pts:
        q = (float(p[0]), float(p[1]))
        if not out or abs(q[0] - out[-1][0]) > 1e-9 or abs(q[1] - out[-1][1]) > 1e-9:
            out.append(q)
    if len(out) > 2 and abs(out[0][0] - out[-1][0]) < 1e-9 and abs(out[0][1] - out[-1][1]) < 1e-9:
        out.pop()
    return out


# ----------------------------------------------------------------------
# 图元与部件
#   图元三种（截面坐标：x 横向、z 竖向，单位 m）：
#     ("circle", cx, cz, r)          实心圆
#     ("ring",   cx, cz, R, r)       圆环（R 外半径、r 内半径）
#     ("rect",   x1, x2, z1, z2)     矩形
# ----------------------------------------------------------------------
def _prim_props(p):
    """返回 (面积 A, 形心 x, 形心 z, 自身形心 Iyy=∫z2dA, Izz=∫x2dA, 周长)。"""
    kind = p[0]
    if kind == "circle":
        _, cx, cz, r = p
        a = math.pi * r * r
        i = math.pi * r ** 4 / 4.0
        return a, cx, cz, i, i, 2.0 * math.pi * r
    if kind == "ring":
        _, cx, cz, R, r = p
        a = math.pi * (R * R - r * r)
        i = math.pi * (R ** 4 - r ** 4) / 4.0
        return a, cx, cz, i, i, 2.0 * math.pi * (R + r)
    if kind == "rect":
        _, x1, x2, z1, z2 = p
        b, h = (x2 - x1), (z2 - z1)
        a = b * h
        return (a, (x1 + x2) / 2.0, (z1 + z2) / 2.0,
                b * h ** 3 / 12.0, h * b ** 3 / 12.0, 2.0 * (b + h))
    raise ValueError("未知图元：%r" % (p,))


def _prim_bbox(p):
    kind = p[0]
    if kind == "circle":
        _, cx, cz, r = p
        return (cx - r, cx + r, cz - r, cz + r)
    if kind == "ring":
        _, cx, cz, R, _r = p
        return (cx - R, cx + R, cz - R, cz + R)
    if kind == "rect":
        _, x1, x2, z1, z2 = p
        return (min(x1, x2), max(x1, x2), min(z1, z2), max(z1, z2))
    raise ValueError("未知图元：%r" % (p,))


class Part(object):
    """一个「施工部件」= 若干基本图元拼起来的一块，对应 *CPOSECT4CS 的一行。

    kind : "steel"（钢管/钢板）或 "conc"（核心混凝土），决定用哪个材料号。
    cstage / age : 该部件从哪个施工阶段开始参与受力、材龄（天）。
                   第 1 个部件随单元在第 1 阶段就激活，CSTAGE 留空、AGE=0。
    mtype : "ELEM"（用单元材料）或 "MATL"（用指定材料号）。
    """

    def __init__(self, name, prims, kind="steel", cstage="", age=0.0, mtype="MATL"):
        self.name = name
        self.prims = [tuple(p) for p in (prims or [])]
        self.kind = kind
        self.cstage = cstage
        self.age = age
        self.mtype = mtype

    @property
    def area(self):
        return sum(_prim_props(p)[0] for p in self.prims)

    @property
    def centroid(self):
        sx = sz = 0.0
        a = 0.0
        for p in self.prims:
            ap, px, pz, _i1, _i2, _per = _prim_props(p)
            sx += ap * px
            sz += ap * pz
            a += ap
        return (sx / a, sz / a) if a > 1e-15 else (0.0, 0.0)

    @property
    def perimeter(self):
        return sum(_prim_props(p)[5] for p in self.prims)

    @property
    def bbox(self):
        bs = [_prim_bbox(p) for p in self.prims]
        return (min(b[0] for b in bs), max(b[1] for b in bs),
                min(b[2] for b in bs), max(b[3] for b in bs))

    def inertia(self):
        """关于自身形心的 Iyy=∫z2dA、Izz=∫x2dA。"""
        cx, cz = self.centroid
        iyy = izz = 0.0
        for p in self.prims:
            ap, px, pz, i1, i2, _per = _prim_props(p)
            iyy += i1 + ap * (pz - cz) ** 2
            izz += i2 + ap * (px - cx) ** 2
        return iyy, izz


class Section(object):
    """钢管 + 核心混凝土的组合截面。outer/holes 是给 *SECT-PSCVALUE 的轮廓多边形。"""

    def __init__(self, parts=None, name="拱肋", outer=None, holes=None):
        self.parts = list(parts or [])
        self.name = name
        self.outer = [_clean_loop(lp) for lp in (outer or []) if len(lp) >= 3]
        self.holes = [_clean_loop(lp) for lp in (holes or []) if len(lp) >= 3]

    @property
    def area(self):
        return sum(p.area for p in self.parts)

    @property
    def centroid(self):
        sx = sz = 0.0
        for p in self.parts:
            a = p.area
            px, pz = p.centroid
            sx += a * px
            sz += a * pz
        a = self.area
        return (sx / a, sz / a) if a > 1e-15 else (0.0, 0.0)

    @property
    def bbox(self):
        bs = [p.bbox for p in self.parts if p.prims]
        if not bs:
            return (0.0, 0.0, 0.0, 0.0)
        return (min(b[0] for b in bs), max(b[1] for b in bs),
                min(b[2] for b in bs), max(b[3] for b in bs))

    def part_group_props(self, kinds):
        """某一类部件（如全部钢管）对整体截面形心的 A / Iyy / Izz。"""
        cy, cz = self.centroid
        a = iyy = izz = 0.0
        for p in self.parts:
            if p.kind not in kinds or p.area <= 1e-15:
                continue
            px, pz = p.centroid
            i1, i2 = p.inertia()
            a += p.area
            iyy += i1 + p.area * (pz - cz) ** 2
            izz += i2 + p.area * (px - cy) ** 2
        return a, iyy, izz

    def props(self):
        """整体截面特性；Iyy=∫z2dA（拱平面内抗弯），Izz=∫x2dA。"""
        cy, cz = self.centroid
        a = iyy = izz = 0.0
        for p in self.parts:
            if p.area <= 1e-15:
                continue
            px, pz = p.centroid
            i1, i2 = p.inertia()
            a += p.area
            iyy += i1 + p.area * (pz - cz) ** 2
            izz += i2 + p.area * (px - cy) ** 2
        x1, x2, z1, z2 = self.bbox
        ip = iyy + izz
        j = ip                      # 圆管截面 J = Iyy + Izz（极惯性矩）
        return {"A": a, "Cy": cy, "Cz": cz, "Iyy": iyy, "Izz": izz, "Ixx": j,
                "CyP": x2 - cy, "CyM": cy - x1, "CzP": z2 - cz, "CzM": cz - z1,
                "B": x2 - x1, "H": z2 - z1,
                "ASy": a * 5.0 / 6.0, "ASz": a * 5.0 / 6.0}

    def validate(self):
        out = []
        if not self.parts:
            out.append("截面里一个部件都没有。")
            return out
        for p in self.parts:
            if not p.prims:
                out.append("部件「%s」没有图元。" % p.name)
            for prim in p.prims:
                kind = prim[0]
                if kind in ("circle",) and prim[3] <= 1e-9:
                    out.append("部件「%s」有半径 ≤ 0 的圆。" % p.name)
                if kind == "ring" and (prim[3] <= 1e-9 or prim[4] <= 1e-9 or prim[3] <= prim[4]):
                    out.append("部件「%s」的圆环外半径必须大于内半径，且都 > 0。" % p.name)
                if kind == "rect" and (abs(prim[2] - prim[1]) <= 1e-9 or abs(prim[4] - prim[3]) <= 1e-9):
                    out.append("部件「%s」有宽或高 ≤ 0 的矩形。" % p.name)
        if self.area <= 1e-12:
            out.append("截面总面积是 0。")
        # 钢管与混凝土不能重叠（简化检查：混凝土圆必须落在钢管内孔里 / 两管之间）
        steel = [p for p in self.parts if p.kind == "steel"]
        conc = [p for p in self.parts if p.kind == "conc"]
        if not steel:
            out.append("缺少钢管部件。")
        if not conc:
            out.append("缺少核心混凝土部件。")
        if not self.outer:
            out.append("缺少给 *SECT-PSCVALUE 用的外轮廓。")
        return out


def _circle_outline(R, segments=48):
    """圆的外轮廓（逆时针）。"""
    return [(R * math.cos(2.0 * math.pi * i / segments),
             R * math.sin(2.0 * math.pi * i / segments)) for i in range(segments)]


def _dumbbell_outline(c, R, tp, seg=24):
    """哑铃形外轮廓：两个圆 + 中间水平连接板的外包线（逆时针）。

    c = 圆心到截面对称轴的距离，R = 钢管外半径，tp = 连接板厚度。
    连接板按「水平薄板」简化：在截面里是一条高度 tp、横跨两管净距的矩形。
    """
    zh = min(max(tp, 1e-6), R * 0.9) / 2.0
    xp = math.sqrt(max(R * R - zh * zh, 0.0))
    a = math.asin(min(1.0, zh / R))
    pts = []
    # 右圆：90° → 180°-a（从顶部转到上侧连接点）
    for i in range(seg + 1):
        th = math.pi / 2.0 + (math.pi - a - math.pi / 2.0) * i / seg
        pts.append((c + R * math.cos(th), R * math.sin(th)))
    # 上连接板：右 → 左
    pts.append((-c + xp, zh))
    # 左圆：a → 2π-a
    for i in range(seg + 1):
        th = a + (2.0 * math.pi - 2.0 * a) * i / seg
        pts.append((-c + R * math.cos(th), R * math.sin(th)))
    # 下连接板：左 → 右
    pts.append((c - xp, -zh))
    # 右圆：π+a → 5π/2（回到起点）
    for i in range(seg + 1):
        th = (math.pi + a) + (2.5 * math.pi - (math.pi + a)) * i / seg
        pts.append((c + R * math.cos(th), R * math.sin(th)))
    return _clean_loop(pts)


# ----------------------------------------------------------------------
# 从模型 dict 造截面
# ----------------------------------------------------------------------
def model_steel(model):
    return ((model.get("tube") or {}).get("steel") or "Q485").strip()


def model_conc(model):
    c = ((model.get("tube") or {}).get("conc") or "C50").strip()
    return c if c in CONC else "C50"


def model_gamma(model):
    g = _f(model.get("gamma"), GAMMA_CONC)
    return g if g > 0 else GAMMA_CONC


def section_from_model(model):
    """按 model["tube"] 的参数造出 钢管 + 核心混凝土 两个部件的组合截面。"""
    tb = model.get("tube") or {}
    form = (tb.get("form") or "single").strip()
    D = _f(tb.get("D"), 1.0)
    t = _f(tb.get("t"), 0.1)
    if D <= 0:
        D = 1e-3
    R = D / 2.0
    t = max(1e-4, min(t, R * 0.999))
    r = max(1e-4, R - t)

    conc_stage = str(model.get("conc_stage") or "").strip()
    conc_age = max(0.0, _f(model.get("conc_age"), 28.0))

    if form == "dumbbell":
        s = _f(tb.get("s"), 1.5)
        tp = _f(tb.get("tp"), 0.016)
        if s <= D:
            s = D * 1.2
        c = s / 2.0
        gap = s - D
        tp = max(1e-4, min(tp, D))
        prims_steel = [("ring", -c, 0.0, R, r), ("ring", c, 0.0, R, r),
                       ("rect", -gap / 2.0, gap / 2.0, -tp / 2.0, tp / 2.0)]
        prims_conc = [("circle", -c, 0.0, r), ("circle", c, 0.0, r)]
        outer = [_dumbbell_outline(c, R, tp)]
    else:
        prims_steel = [("ring", 0.0, 0.0, R, r)]
        prims_conc = [("circle", 0.0, 0.0, r)]
        outer = [_circle_outline(R)]

    parts = [
        # 第 1 部件 = 钢管：第 1 阶段随单元一起激活 → CSTAGE 留空、AGE=0（实测要求）
        Part("钢管", prims_steel, kind="steel", cstage="", age=0.0, mtype="ELEM"),
        # 第 2 部件 = 核心混凝土：在「混凝土硬化」阶段开始参与受力
        Part("核心混凝土", prims_conc, kind="conc", cstage=conc_stage,
             age=conc_age, mtype="MATL"),
    ]
    return Section(parts, name=(model.get("section_name") or "拱肋"), outer=outer)


def get_section(model):
    sec = model.get("section")
    if isinstance(sec, Section):
        return sec
    return section_from_model(model)


def summary(model):
    """给界面/数据表用的截面摘要（钢管、混凝土、组合各自的 A / I / 自重）。"""
    sec = get_section(model)
    sp = sec.props()
    A_s, Iyy_s, Izz_s = sec.part_group_props(("steel",))
    A_c, Iyy_c, Izz_c = sec.part_group_props(("conc",))
    gamma_c = model_gamma(model)
    w_steel = A_s * GAMMA_STEEL
    w_conc = A_c * gamma_c
    return {
        "A_steel": A_s, "Iyy_steel": Iyy_s, "Izz_steel": Izz_s,
        "A_conc": A_c, "Iyy_conc": Iyy_c, "Izz_conc": Izz_c,
        "A": sp["A"], "Iyy": sp["Iyy"], "Izz": sp["Izz"], "Ixx": sp["Ixx"],
        "Cy": sp["Cy"], "Cz": sp["Cz"], "B": sp["B"], "H": sp["H"],
        "steel_ratio": (A_s / sp["A"]) if sp["A"] > 1e-12 else 0.0,
        "w_steel": w_steel, "w_conc": w_conc, "w_total": w_steel + w_conc,
        "perimeter_steel": sum(p.perimeter for p in sec.parts if p.kind == "steel"),
        "perimeter_conc": sum(p.perimeter for p in sec.parts if p.kind == "conc"),
        "sec": sec, "props": sp,
    }


# ----------------------------------------------------------------------
# 材料 / 时间依存
# ----------------------------------------------------------------------
def material_lines(model):
    """返回 (材料行列表, 混凝土 fck)。材料 1=钢材（单元材料）、2=混凝土。"""
    steel = model_steel(model)
    conc = model_conc(model)
    E_c, fck = CONC[conc] if conc in CONC else CONC["C50"]
    gamma_c = model_gamma(model)
    lines = [
        "   1, STEEL, %s, 0, 0, , C, NO, 0.05, 2, %.4e, %.2f, %.2e, %s, 0"
        % (steel, E_STEEL, NU_STEEL, ALPHA_STEEL, _num(GAMMA_STEEL)),
        "   2, CONC , %s, 0, 0, , C, NO, 0.05, 2, %.4e, 0.2, 1.0e-05, %s, 0"
        % (conc, E_c, _num(gamma_c)),
    ]
    return lines, fck


def tdm_lines(model):
    """时间依存材料（收缩徐变）——可选。格式照 MIDAS 自己导出的 FCM 教程样本。

    CEB-FIP 里填的是 28 天平均抗压强度 fcm（kN/m2 = kPa），不是弹性模量。
    混凝土是材料 2，所以 *TDM-LINK 引材料 2。
    """
    conc = model_conc(model)
    fck = (CONC.get(conc) or CONC["C50"])[1]
    fcm = fck + 8.0e3
    name = "TDM_%s" % conc
    hum = _f(model.get("humidity"), 70.0)
    return [
        "*TDM-TYPE    ; Time Dependent Material (Creep/Shrinkage)",
        "   NAME=%s, CEB, %.6g, %s, 1, NR, 3, 0" % (name, fcm, _num(hum)),
        "*TDM-ELAST    ; Time Dependent Material (Comp. Strength)",
        "   NAME=%s, CODE, CEB-FIP, %.6g, 2" % (name, fcm),
        "*TDM-LINK    ; Time Dependent Material Link",
        "   2, %s, %s" % (name, name),
        "",
    ]


# ----------------------------------------------------------------------
# 一般截面（*SECT-PSCVALUE）——基础截面必须是 PSC 类型，联合截面才认
# ----------------------------------------------------------------------
def pscvalue_lines(sec, name="SEC1", props=None):
    """生成 *SECT-PSCVALUE 的截面数据行（逐字段照 MIDAS 导出的格式）。"""
    sp = dict(sec.props())
    if props:
        sp.update(props)
    a = sp["A"]
    lines = [
        " SECT=   1, PSC       , %-18s, CC, 0, 0, 0, 0, 0, 0, YES, NO, VALU, NO, NO, " % _ascii(name),
        "       %s, %s, %s, %s, %s, %s"
        % (_num(a), _num(sp["ASy"]), _num(sp["ASz"]), _num(sp["Ixx"]), _num(sp["Iyy"]), _num(sp["Izz"])),
        "       %s, %s, %s, %s, %s, %s, 0, 0, %s, %s"
        % (_num(sp["CyP"]), _num(sp["CyM"]), _num(sp["CzP"]), _num(sp["CzM"]),
           _num(a * sp["CzP"] / 8.0), _num(a * sp["CyP"] / 8.0),
           _num(sp["Cy"]), _num(sp["Cz"])),
        "       %s, %s, %s, %s, %s, %s, %s, %s"
        % (_num(-sp["CyM"]), _num(sp["CyP"]), _num(sp["CyP"]), _num(-sp["CyM"]),
           _num(sp["CzP"]), _num(sp["CzP"]), _num(-sp["CzM"]), _num(-sp["CzM"])),
        "       0.3, 0.2, 0.1, 0.1",
        "       YES, 0, 0, YES, , YES, , YES, , 0, YES, , YES, , YES, ",
    ]
    for lp in sec.outer:
        lines.append("       OPOLY=" + ", ".join("%s, %s" % (_fmt_pt(p[0]), _fmt_pt(p[1]))
                                                 for p in _ccw(lp)))
    for lp in sec.holes:
        lines.append("       IPOLY=" + ", ".join("%s, %s" % (_fmt_pt(p[0]), _fmt_pt(p[1]))
                                                 for p in _cw(lp)))
    return lines


# ----------------------------------------------------------------------
# 模型生成的小工具
# ----------------------------------------------------------------------
def _arch_pts(model):
    a = model.get("arch") or {}
    return arch_axis(_f(a.get("L"), 50.0), _f(a.get("f"), 10.0), _f(a.get("m"), 1.6),
                     (a.get("type") or "parabola"), int(_f(a.get("n"), 30.0)),
                     _f(a.get("z0"), 0.0))


def _support_const(model):
    """拱脚约束：无铰=6 个自由度全约束；两铰=释放面内转角 Ry（3D 模型里是第 5 位）。"""
    a = model.get("arch") or {}
    return "111111" if a.get("fixed", True) else "111101"


def beamload_line(elem, q, group):
    """一行梁单元均布荷载（-Z 方向）。字段顺序照梁桥流程/拱桥流程已验证的写法。"""
    return ("     %d, BEAM   , UNILOAD, GZ, NO , NO, LY, , , , 0, %s, 1, %s, 0, 0, 0, 0, %s, NO, 0, 0, NO, "
            % (elem, _num(-q), _num(-q), group))


def wet_loads(model):
    """湿混凝土均布荷载 [(组名, q kN/m), ...]：按核心混凝土面积 × 容重。"""
    sec = get_section(model)
    gamma = model_gamma(model)
    out = []
    k = 0
    for p in sec.parts:
        if p.kind == "conc" and p.area > 1e-12:
            k += 1
            out.append(("WET%d" % k, p.area * gamma))
    return out


def stage_names(model):
    return [str(s.get("name") or "").strip() for s in (model.get("stages") or [])]


def stage_index(model, name):
    try:
        return stage_names(model).index(str(name or "").strip())
    except ValueError:
        return -1


def wet_stage_of(model):
    """湿重加在「混凝土硬化阶段」的前一个阶段（默认 CS2 ← CS3）。"""
    i = stage_index(model, model.get("conc_stage"))
    names = stage_names(model)
    if i <= 0:
        return ""
    return names[i - 1]


def q2_value(model):
    q = _f(model.get("q2"), 0.0)
    return q if q > 0 else 0.0


# ----------------------------------------------------------------------
# 施工阶段模型（*STAGE + *CPOSECT4CS）
# ----------------------------------------------------------------------
def build_mct(model):
    """生成「施工阶段联合截面」模型的完整 MCT 文本（写文件时用 GBK 编码）。"""
    arch = model.get("arch") or {}
    stages = model.get("stages") or []
    sec = get_section(model)
    sp = sec.props()
    mats, fck = material_lines(model)
    gamma_c = model_gamma(model)
    q2 = q2_value(model)
    creep = bool(model.get("creep"))
    proj = model.get("project") or {}
    name = proj.get("name") or "钢管混凝土拱"
    type_name = ARCH_TYPE_NAMES.get((arch.get("type") or "parabola"), "抛物线")

    pts = _arch_pts(model)
    n_elem = len(pts) - 1
    reduced = max(0.1, min(1.0, _f((model.get("tube") or {}).get("reduce"), 1.0)))

    Lw = []

    def add(s=""):
        Lw.append(s)

    head = [
        "; ================================================================",
        "; MIDAS Civil NX — MCT（钢管混凝土拱 · 施工阶段联合截面）",
        "; 生成时间：" + datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "; 项目：%s" % name,
        "; 流程：先架设空钢管拱 → 灌注管内混凝土（湿重）→ 混凝土硬化（联合截面生效）",
        "; 拱轴：%s  L=%.3f m  f=%.3f m  m=%s  单元 %d 个  拱脚标高 %.3f m"
        % (type_name, _f(arch.get("L"), 0), _f(arch.get("f"), 0), arch.get("m"), n_elem,
           _f(arch.get("z0"), 0.0)),
        "; 截面：%s  钢管 A=%.4f m2 核心混凝土 A=%.4f m2 组合 A=%.4f m2"
        % (FORM_NAMES.get((model.get("tube") or {}).get("form"), "单圆管"),
           summary(model)["A_steel"], summary(model)["A_conc"], sp["A"]),
        "; 施工阶段：" + " → ".join("%s(%s d)" % (s.get("name"), s.get("days")) for s in stages),
        "; ================================================================",
    ]
    for ln in head:
        add(ln)

    add("*VERSION")
    add("   9.5.0")
    add("*UNIT    ; Unit System")
    add("   KN, M, KJ, C")
    add("*STRUCTYPE    ; Structure Type")
    add("   0, 1, 1, NO, NO, 9.806, 0, NO, NO, NO")

    # ---- 节点 / 单元 ----
    add("*NODE    ; Nodes")
    for i, (x, z) in enumerate(pts, start=1):
        add("   %d, %s, 0, %s" % (i, _num(x, 8), _num(z, 8)))
    add("*ELEMENT    ; Elements")
    for i in range(1, n_elem + 1):
        add("   %d, BEAM, 1, 1, %d, %d, 0, 0" % (i, i, i + 1))

    # ---- 组 ----
    add("*GROUP    ; Group")
    add("   EL_ALL, 1to%d, 1to%d, 0" % (n_elem + 1, n_elem))
    add("*BNDR-GROUP    ; Boundary Group")
    add("   BC, 0")
    add("*LOAD-GROUP    ; Load Group")
    add("   SW")
    for g, _q in wet_loads(model):
        add("   %s" % g)
    if q2 > 0:
        add("   DL2")

    # ---- 材料 ----
    add("*MATERIAL    ; Material")
    for ln in mats:
        add(ln)
    if creep:
        for ln in tdm_lines(model):
            add(ln)

    # ---- 基础截面（PSC 一般截面）----
    add("*SECT-PSCVALUE    ; PSC Value / General Section")
    for ln in pscvalue_lines(sec, sec.name):
        add(ln)
    add("")

    # ---- 荷载工况 ----
    add("*STLDCASE    ; Static Load Cases")
    add("   SW, CS, \"Self Weight\"")
    for g, _q in wet_loads(model):
        add("   %s, CS, \"Wet Concrete\"" % g)
    if q2 > 0:
        add("   DL2, CS, \"Second Dead Load\"")

    # ---- 约束 ----
    fx = _support_const(model)
    add("*CONSTRAINT    ; Supports")
    add("   1, %s, BC" % fx)
    add("   %d, %s, BC" % (n_elem + 1, fx))

    # ---- 荷载 ----
    add("*USE-STLD, SW")
    add("*SELFWEIGHT    ; Self Weight")
    add("0, 0, -1, SW")
    for g, q in wet_loads(model):
        add("*USE-STLD, %s" % g)
        add("*BEAMLOAD    ; Element Beam Loads（管内湿混凝土按均布力加）")
        for e in range(1, n_elem + 1):
            add(beamload_line(e, q, g))
    if q2 > 0:
        add("*USE-STLD, DL2")
        add("*BEAMLOAD    ; Element Beam Loads（二期恒载）")
        for e in range(1, n_elem + 1):
            add(beamload_line(e, q2, "DL2"))

    # ---- 施工阶段 ----
    names = stage_names(model)
    wet_name = wet_stage_of(model)
    conc_name = str(model.get("conc_stage") or "").strip()
    add("*STAGE    ; Define Construction Stage")
    for i, st in enumerate(stages, start=1):
        nm = names[i - 1]
        days = st.get("days", 20)
        add("  NAME=%s, %s, YES, YES, NO, 5" % (nm, days))
        if i == 1:
            add("  AELEM=EL_ALL, %s" % days)
            add("  ABNDR=BC, DEFORMED")
            add("  ALOAD=SW, FIRST")
        if nm == wet_name:
            for g, _q in wet_loads(model):
                add("  ALOAD=%s, FIRST" % g)
        if nm == conc_name:
            for g, _q in wet_loads(model):
                add("  DLOAD=%s, FIRST" % g)
        if q2 > 0 and nm == names[-1]:
            add("  ALOAD=DL2, FIRST")

    # ---- 施工阶段联合截面（*CPOSECT4CS，每部件 37 个字段）----
    add("*CPOSECT4CS    ; Composite Section for Construction Stage")
    add("   SEC=1, %s, USER, NO" % names[0])
    cy, cz = sp["Cy"], sp["Cz"]
    for k, part in enumerate(sec.parts):
        px, pz = part.centroid
        a = part.area
        iyy, izz = part.inertia()
        if part.kind == "conc":
            iyy *= reduced          # 管内混凝土刚度折减（PPT 建议 0.6~0.8）
            izz *= reduced
        x1, x2, z1, z2 = part.bbox
        cyp, cym = x2 - px, px - x1
        czp, czm = z2 - pz, pz - z1
        per = part.perimeter
        h = 2.0 * a / per if per > 1e-9 else 0.5
        if k == 0:
            # 第 1 个部件：CSTAGE / AGE 必须留空 / 0（实测：写阶段名整份被拒）
            mtype, mat, cstage, age = "ELEM", "", "", 0
        else:
            mat_no = 1 if part.kind == "steel" else 2
            mtype, mat = "MATL", mat_no
            cstage = part.cstage or (names[min(2, len(names) - 1)] if len(names) > 2 else names[-1])
            age = int(round(part.age))
        row = [k + 1, mtype, mat, cstage, age,
               *[1 for _ in range(7)],
               px - cy, pz - cz,
               a, 5.0 * a / 6.0, 5.0 * a / 6.0, iyy + izz, iyy, izz,
               cyp, cym, czp, czm, 0, 0,
               -cym, cyp, cyp, -cym,
               -czm, -czm, czp, czp,
               h, 0, 0]
        add("       " + ", ".join(_fmt_cs(v) for v in row))

    # ---- 施工阶段分析控制 ----
    add("*STAGE-CTRL    ; Construction Stage Analysis Control Data")
    add("  YES, , INTERNAL, NO, NO, NO, NO, NO, YES, YES, %s, , , NO, NO, , NO, NO, NO"
        % ("YES" if creep else "NO"))
    add("  0 ")
    add("  NO")
    add("  YES, YES, BOTH, 5, 0.01, YES, NO, YES, YES, 0, NO")
    add("  NO, NO, 2, YES, 2, 5, 7, 10, 20")
    add("  NO, , , 1, NO")
    add("*ANAL-CTRL")
    add("   YES, YES, 20, 0.001, YES, YES, YES, NO, NO, NO, NO, YES")
    add("*ENDDATA")
    return "\r\n".join(Lw) + "\r\n"


def one_step_equivalent(model):
    """一次成拱对比模型用的「等效材料 + 等效截面」参数。

    把钢管和混凝土按 E·A、E·I 等效成一种单一材料：
        E_eq  = (Es·As + Ec·Ac) / A          → E_eq·A = ΣE·A（轴向刚度一致）
        I_eq  = (Es·Is + Ec·Ic) / E_eq       → E_eq·I = ΣE·I（抗弯刚度一致）
        ρ_eq  = (As·78.5 + Ac·γc) / A        → 单位长度自重一致
    这样「一次成拱」模型和施工阶段模型的差别只来自施工过程本身，便于对比。
    """
    sec = get_section(model)
    sp = sec.props()
    A_total = sp["A"] if sp["A"] > 1e-12 else 1.0
    A_s, Iyy_s, Izz_s = sec.part_group_props(("steel",))
    A_c, Iyy_c, Izz_c = sec.part_group_props(("conc",))
    E_c = (CONC.get(model_conc(model)) or CONC["C50"])[0]
    gamma_c = model_gamma(model)
    E_eq = (E_STEEL * A_s + E_c * A_c) / A_total
    if E_eq <= 1e-9:
        E_eq = E_STEEL
    Iyy_eq = (E_STEEL * Iyy_s + E_c * Iyy_c) / E_eq
    Izz_eq = (E_STEEL * Izz_s + E_c * Izz_c) / E_eq
    rho_eq = (A_s * GAMMA_STEEL + A_c * gamma_c) / A_total
    return {"E": E_eq, "A": A_total, "Iyy": Iyy_eq, "Izz": Izz_eq, "rho": rho_eq,
            "A_steel": A_s, "A_conc": A_c}


def build_one_step_mct(model):
    """「一次成拱 / 全截面一次浇筑」简化对比模型的 MCT 文本。

    单个施工阶段，整截面从第 1 阶段起全部有效，只加自重。材料与截面按上面的
    等效换算（E·A、E·I、自重三项都与真实组合截面一致），所以和施工阶段模型对比时，
    内力/位移的差别反映的是「施工过程」的影响，而不是材料定义不同。
    """
    arch = model.get("arch") or {}
    sec = get_section(model)
    eq = one_step_equivalent(model)
    proj = model.get("project") or {}
    name = proj.get("name") or "钢管混凝土拱"
    pts = _arch_pts(model)
    n_elem = len(pts) - 1
    sp = sec.props()
    type_name = ARCH_TYPE_NAMES.get((arch.get("type") or "parabola"), "抛物线")

    Lw = []

    def add(s=""):
        Lw.append(s)

    for ln in [
        "; ================================================================",
        "; MIDAS Civil NX — MCT（钢管混凝土拱 · 一次成拱 / 全截面一次浇筑 对比模型）",
        "; 生成时间：" + datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "; 项目：%s（对比模型）" % name,
        "; 说明：单阶段、全截面一次生效、只加自重。钢管+混凝土按 E·A、E·I、自重等效成单材料。",
        "; 等效材料：E=%.4e kN/m2  ρ=%.3f kN/m3  等效 A=%.4f m2  等效 Iyy=%.4f m4  Izz=%.4f m4"
        % (eq["E"], eq["rho"], eq["A"], eq["Iyy"], eq["Izz"]),
        "; 拱轴：%s  L=%.3f m  f=%.3f m  单元 %d 个" % (type_name, _f(arch.get("L"), 0),
                                                   _f(arch.get("f"), 0), n_elem),
        "; ================================================================",
    ]:
        add(ln)

    add("*VERSION")
    add("   9.5.0")
    add("*UNIT    ; Unit System")
    add("   KN, M, KJ, C")
    add("*STRUCTYPE    ; Structure Type")
    add("   0, 1, 1, NO, NO, 9.806, 0, NO, NO, NO")
    add("*NODE    ; Nodes")
    for i, (x, z) in enumerate(pts, start=1):
        add("   %d, %s, 0, %s" % (i, _num(x, 8), _num(z, 8)))
    add("*ELEMENT    ; Elements")
    for i in range(1, n_elem + 1):
        add("   %d, BEAM, 1, 1, %d, %d, 0, 0" % (i, i, i + 1))
    add("*GROUP    ; Group")
    add("   EL_ALL, 1to%d, 1to%d, 0" % (n_elem + 1, n_elem))
    add("*BNDR-GROUP    ; Boundary Group")
    add("   BC, 0")
    add("*LOAD-GROUP    ; Load Group")
    add("   SW")
    add("*MATERIAL    ; Material")
    add("   1, USER , EQ, 0, 0, , C, NO, 0.05, 2, %.6e, 0.2, 1.0e-05, %s, 0"
        % (eq["E"], _num(eq["rho"])))
    add("*SECT-PSCVALUE    ; PSC Value / General Section")
    override = dict(sp)
    override.update({"A": eq["A"], "ASy": eq["A"] * 5.0 / 6.0, "ASz": eq["A"] * 5.0 / 6.0,
                     "Iyy": eq["Iyy"], "Izz": eq["Izz"], "Ixx": eq["Iyy"] + eq["Izz"]})
    for ln in pscvalue_lines(sec, sec.name + "EQ", props=override):
        add(ln)
    add("")
    add("*STLDCASE    ; Static Load Cases")
    add("   SW, CS, \"Self Weight\"")
    fx = _support_const(model)
    add("*CONSTRAINT    ; Supports")
    add("   1, %s, BC" % fx)
    add("   %d, %s, BC" % (n_elem + 1, fx))
    add("*USE-STLD, SW")
    add("*SELFWEIGHT    ; Self Weight")
    add("0, 0, -1, SW")
    add("*STAGE    ; Define Construction Stage")
    add("  NAME=CS1, 30, YES, YES, NO, 5")
    add("  AELEM=EL_ALL, 30")
    add("  ABNDR=BC, DEFORMED")
    add("  ALOAD=SW, FIRST")
    add("*STAGE-CTRL    ; Construction Stage Analysis Control Data")
    add("  YES, , INTERNAL, NO, NO, NO, NO, NO, YES, YES, NO, , , NO, NO, , NO, NO, NO")
    add("  0 ")
    add("  NO")
    add("  YES, YES, BOTH, 5, 0.01, YES, NO, YES, YES, 0, NO")
    add("  NO, NO, 2, YES, 2, 5, 7, 10, 20")
    add("  NO, , , 1, NO")
    add("*ANAL-CTRL")
    add("   YES, YES, 20, 0.001, YES, YES, YES, NO, NO, NO, NO, YES")
    add("*ENDDATA")
    return "\r\n".join(Lw) + "\r\n"


# ----------------------------------------------------------------------
# 默认模型（第 05 讲算例）
# ----------------------------------------------------------------------
def default_model():
    """默认 = 老师第 05 讲「单圆管钢管混凝土拱」算例：
    φ1000×100 钢管 + C50 管内混凝土，L=50 m、f=10 m、二次抛物线、30 个单元，
    施工方法 = 先架设钢拱圈再灌注管内混凝土。
    """
    return {
        "flow": CODE,
        "project": {"name": "钢管混凝土拱_50m", "outdir": "",
                    "force": "KN", "dist": "M", "styp": "0"},
        "arch": {"type": "parabola", "L": "50", "f": "10", "m": "1.6",
                 "n": "30", "z0": "0", "fixed": True},
        "tube": {"form": "single", "D": "1.0", "t": "0.10",
                 "s": "1.5", "tp": "0.016",
                 "conc": "C50", "steel": "Q485", "reduce": "1.0"},
        "stages": [
            {"name": "CS1", "days": "5", "desc": "架设钢拱圈（空钢管）"},
            {"name": "CS2", "days": "20", "desc": "灌注管内混凝土（湿重）"},
            {"name": "CS3", "days": "28", "desc": "混凝土硬化，联合截面参与受力"},
            {"name": "CS4", "days": "30", "desc": "二期恒载 / 成桥"},
        ],
        "conc_stage": "CS3",       # 核心混凝土从哪个阶段开始参与受力
        "conc_age": "28",          # 混凝土硬化龄期（天）
        "q2": "0",                 # 二期恒载（kN/m，0 = 不加）
        "gamma": str(GAMMA_CONC),
        "creep": False,
        "humidity": "70",
    }


def reset_model():
    return default_model()


# ----------------------------------------------------------------------
# 参数校验（界面每一步用它拦人）
# ----------------------------------------------------------------------
def arch_issues(model):
    out = []
    a = model.get("arch") or {}
    try:
        L = float(a.get("L"))
        f = float(a.get("f"))
    except (TypeError, ValueError):
        return ["计算跨径和计算矢高都要填数字（单位 m）。"]
    if L <= 0:
        out.append("计算跨径要大于 0。")
    if f <= 0:
        out.append("计算矢高要大于 0。")
    if f >= L > 0:
        out.append("矢高比跨径还大，检查一下单位（应为 m）。")
    if L > 0 and 0 < f < L:
        ratio = L / f
        if ratio < 2.0 or ratio > 12.0:
            out.append("矢跨比 1/%.2f 不常见（常用 1/3~1/8），确认一下跨径和矢高。" % ratio)
    try:
        n = int(float(a.get("n")))
        if n < 8 or n > 400:
            out.append("单元数建议 8~400（现在 %d）。" % n)
    except (TypeError, ValueError):
        out.append("单元数要填整数。")
    kind = (a.get("type") or "parabola")
    if kind not in dict(ARCH_TYPES):
        out.append("拱轴类型只能是悬链线 / 抛物线 / 圆弧。")
    if kind == "catenary":
        try:
            mm = float(a.get("m"))
            if mm <= 1.0:
                out.append("悬链线的拱轴系数 m 必须大于 1（常用 1.1~3）。")
            elif mm > 5:
                out.append("拱轴系数 m=%g 太大了（常用 1.1~3），确认一下。" % mm)
        except (TypeError, ValueError):
            out.append("悬链线要填拱轴系数 m（大于 1）。")
    if not str(a.get("z0", "")).strip() == "":
        try:
            float(a.get("z0"))
        except (TypeError, ValueError):
            out.append("拱脚标高要填数字（m）。")
    return out


def section_issues(model):
    out = []
    tb = model.get("tube") or {}
    form = (tb.get("form") or "single").strip()
    if form not in dict(FORMS):
        out.append("截面形式只能是单圆管 / 哑铃形。")
    try:
        D = float(tb.get("D"))
    except (TypeError, ValueError):
        return ["钢管外径 D 要填数字（m）。"]
    if D <= 0:
        out.append("钢管外径 D 要大于 0。")
        return out
    try:
        t = float(tb.get("t"))
    except (TypeError, ValueError):
        out.append("钢管壁厚 t 要填数字（m）。")
        t = 0.0
    if t <= 0:
        out.append("钢管壁厚 t 要大于 0。")
    elif D > 0 and t >= D / 2.0:
        out.append("壁厚 %.4g m 太大：必须小于外径的一半（%.4g m）。" % (t, D / 2.0))
    elif D > 0 and (D - 2 * t) < 0.02 * D:
        out.append("管内混凝土直径太小（D-2t=%.4g m），检查壁厚。" % (D - 2 * t))
    if form == "dumbbell":
        try:
            s = float(tb.get("s"))
            if s <= D:
                out.append("哑铃形两管中心距 %.4g m 必须大于外径 %.4g m（否则两管重叠）。" % (s, D))
        except (TypeError, ValueError):
            out.append("哑铃形的两管中心距要填数字（m）。")
        try:
            tp = float(tb.get("tp"))
            if tp <= 0:
                out.append("连接板厚度要大于 0。")
        except (TypeError, ValueError):
            out.append("连接板厚度要填数字（m）。")
    conc = (tb.get("conc") or "").strip()
    if conc not in CONC:
        out.append("混凝土等级选 %s 之一。" % " / ".join(CONC_LIST))
    steel = (tb.get("steel") or "").strip()
    if steel not in STEEL_LIST:
        out.append("钢材牌号选 %s 之一。" % " / ".join(STEEL_LIST))
    try:
        red = float(tb.get("reduce"))
        if red <= 0 or red > 1.0:
            out.append("混凝土刚度折减系数要在 0~1 之间（不折减填 1，PPT 建议 0.6~0.8）。")
    except (TypeError, ValueError):
        out.append("混凝土刚度折减系数要填数字（0~1，不折减填 1）。")
    try:
        g = float(model.get("gamma"))
        if g <= 0 or g > 40:
            out.append("混凝土容重应在 0~40 kN/m3（常用 24~26）。")
    except (TypeError, ValueError):
        out.append("混凝土容重要填数字（kN/m3，常用 25）。")
    out += get_section(model).validate()
    return out


def stage_issues(model):
    out = []
    stages = model.get("stages") or []
    if len(stages) < 2:
        out.append("至少要有 2 个施工阶段（架设空钢管 → 灌注混凝土 → 硬化）。")
    seen = set()
    for i, st in enumerate(stages, start=1):
        nm = str(st.get("name") or "").strip()
        if not nm:
            out.append("第 %d 个阶段没有名字。" % i)
        elif "," in nm or "\n" in nm:
            out.append("阶段名「%s」里不能有逗号/换行。" % nm)
        elif nm in seen:
            out.append("阶段名「%s」重复了。" % nm)
        seen.add(nm)
        try:
            if float(st.get("days", 0)) <= 0:
                out.append("阶段「%s」的天数要大于 0。" % nm)
        except (TypeError, ValueError):
            out.append("阶段「%s」的天数要填数字。" % nm)
    names = [str(s.get("name") or "").strip() for s in stages]
    conc = str(model.get("conc_stage") or "").strip()
    if not conc:
        out.append("要选「混凝土从哪个阶段开始参与受力」。")
    elif conc not in names:
        out.append("混凝土参与受力阶段「%s」不在阶段表里。" % conc)
    elif names and conc == names[0]:
        out.append("混凝土不能在第 1 个阶段（架设空钢管）就参与受力，至少排到第 2 个阶段。")
    try:
        age = float(model.get("conc_age"))
        if age < 0:
            out.append("混凝土硬化龄期不能是负数。")
    except (TypeError, ValueError):
        out.append("混凝土硬化龄期要填数字（天）。")
    try:
        q2 = float(model.get("q2") or 0)
        if q2 < 0:
            out.append("二期恒载不能是负数（不填或填 0 表示不加）。")
    except (TypeError, ValueError):
        out.append("二期恒载要填数字（kN/m），不需要就填 0。")
    if model.get("creep"):
        try:
            hv = float(model.get("humidity"))
            if hv < 0 or hv > 100:
                out.append("环境湿度要在 0~100 % 之间。")
        except (TypeError, ValueError):
            out.append("环境湿度要填 0~100 之间的数字。")
    return out


def issues(model, sid):
    if sid in ("cfst_arch", "arch"):
        return arch_issues(model)
    if sid in ("cfst_section", "section"):
        return section_issues(model)
    if sid in ("cfst_stages", "stages"):
        return stage_issues(model)
    return []


def all_issues(model):
    out = []
    for fn, title in ((arch_issues, "拱轴线"), (section_issues, "钢管混凝土截面"),
                      (stage_issues, "施工阶段")):
        for msg in fn(model):
            out.append("「%s」%s" % (title, msg))
    return out


# ----------------------------------------------------------------------
# 数据表（生成成果时写成 txt）
# ----------------------------------------------------------------------
def data_report(model):
    """人可读的数据表文本。"""
    a = model.get("arch") or {}
    tb = model.get("tube") or {}
    sm = summary(model)
    proj = model.get("project") or {}
    L = []
    L.append("钢管混凝土拱 · 施工阶段联合截面 数据表")
    L.append("=" * 70)
    L.append("项目名称：%s" % (proj.get("name") or ""))
    L.append("")
    L.append("一、拱轴线")
    L.append("  拱轴类型：%s" % ARCH_TYPE_NAMES.get((a.get("type") or "parabola"), ""))
    L.append("  计算跨径 L = %s m   计算矢高 f = %s m   矢跨比 1/%s"
             % (a.get("L"), a.get("f"),
                _num(_f(a.get("L"), 0) / _f(a.get("f"), 1), 4) if _f(a.get("f"), 0) else "-"))
    if (a.get("type") or "") == "catenary":
        L.append("  拱轴系数 m = %s" % a.get("m"))
    L.append("  单元数 = %s（节点 %d 个）   拱脚标高 = %s m   拱脚约束 = %s"
             % (a.get("n"), int(_f(a.get("n"), 30)) + 1, a.get("z0"),
                "无铰（固结）" if a.get("fixed", True) else "两铰"))
    pts = _arch_pts(model)
    L.append("  拱轴线长 = %.3f m   拱顶标高 = %.3f m" % (arch_length(pts), _f(a.get("z0"), 0) + _f(a.get("f"), 0)))
    L.append("")
    L.append("二、钢管混凝土截面（%s）" % FORM_NAMES.get((tb.get("form") or "single"), ""))
    L.append("  钢管：外径 D=%s m  壁厚 t=%s m  牌号 %s" % (tb.get("D"), tb.get("t"), tb.get("steel")))
    L.append("        面积 A = %.4f m2   Iyy = %.6f m4   Izz = %.6f m4"
             % (sm["A_steel"], sm["Iyy_steel"], sm["Izz_steel"]))
    if (tb.get("form") or "") == "dumbbell":
        L.append("        哑铃形：两管中心距 %s m，连接板厚 %s m（按截面内水平薄板简化）"
                 % (tb.get("s"), tb.get("tp")))
    L.append("  核心混凝土：%s   面积 A = %.4f m2   Iyy = %.6f m4   Izz = %.6f m4"
             % (model_conc(model), sm["A_conc"], sm["Iyy_conc"], sm["Izz_conc"]))
    L.append("  组合截面：面积 A = %.4f m2   Iyy = %.6f m4   Izz = %.6f m4   Ixx = %.6f m4"
             % (sm["A"], sm["Iyy"], sm["Izz"], sm["Ixx"]))
    L.append("            含钢率（面积比） = %.2f %%   截面高 %.3f m"
             % (sm["steel_ratio"] * 100.0, sm["H"]))
    L.append("  单位长度自重：钢管 %.2f kN/m + 混凝土 %.2f kN/m = %.2f kN/m"
             % (sm["w_steel"], sm["w_conc"], sm["w_total"]))
    red = _f(tb.get("reduce"), 1.0)
    if abs(red - 1.0) > 1e-9:
        L.append("  管内混凝土刚度折减系数：%.2f（只折减 Iyy/Izz）" % red)
    L.append("")
    L.append("三、施工阶段")
    names = stage_names(model)
    for i, st in enumerate(model.get("stages") or [], start=1):
        tag = ""
        if names and names[i - 1] == str(model.get("conc_stage") or "").strip():
            tag = "  ← 核心混凝土开始参与受力（联合截面生效）"
        elif names and names[i - 1] == wet_stage_of(model):
            tag = "  ← 加管内混凝土湿重"
        L.append("  %-6s 持续 %-4s d  %s%s" % (st.get("name"), st.get("days"), st.get("desc") or "", tag))
    for g, q in wet_loads(model):
        L.append("  湿重 %s：%.2f kN/m（按核心混凝土面积 × %.1f kN/m3）"
                 % (g, q, model_gamma(model)))
    q2 = q2_value(model)
    if q2 > 0:
        L.append("  二期恒载 DL2：%.2f kN/m，加在最后阶段 %s" % (q2, names[-1] if names else ""))
    L.append("  混凝土硬化龄期：%s d   收缩徐变：%s"
             % (model.get("conc_age"), "考虑（CEB-FIP）" if model.get("creep") else "不考虑"))
    L.append("")
    L.append("四、说明")
    L.append("  1) 模型里先给单元赋「组合截面」，再在 *CPOSECT4CS 里把钢管和核心混凝土拆成两个")
    L.append("     Part：钢管（ELEM）随单元在第 1 阶段激活，核心混凝土在 %s 开始参与受力。"
             % (model.get("conc_stage") or ""))
    L.append("  2) 灌注阶段把管内混凝土当「湿重」以梁单元均布力加上，硬化阶段用 DLOAD 卸掉，")
    L.append("     所以重量和刚度都不会重复计算。")
    L.append("  3) 对比模型「一次成拱」把全截面从第 1 阶段就当有效截面、只加自重，")
    L.append("     材料按 E·A、E·I、自重三项等效成单一材料，用来对比施工过程对内力的影响。")
    return "\r\n".join(L) + "\r\n"
