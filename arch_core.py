# -*- coding: utf-8 -*-
"""拱桥·施工阶段联合截面 —— 核心引擎（纯计算 + MCT 生成，不依赖 tkinter）。

用户只需要：填拱轴参数 → 用矩形拼出截面并指定每个部件的浇筑/受力阶段 → 生成 MCT。

本模块负责：
  1) 悬链线/抛物线/圆弧拱轴线；
  2) 部件（矩形集合）的面积、形心、惯性矩，以及联合截面所需的相对位置；
  3) 生成一份 MIDAS Civil NX 能直接导入、能跑施工阶段分析的 .mct；
  4) 生成"一次成拱"的对比模型。

关键格式（全部对着 MIDAS 实测导出，别再照手册猜）：
* 施工阶段：*STAGE，行内 NAME= / AELEM= / ABNDR= / ALOAD= / DLOAD=
* 联合截面：*CPOSECT4CS，SEC=1, <激活阶段>, USER, NO
     每部件一行 37 个字段：
       PART, MTYPE, MAT, CSTAGE, AGE,
       SCALE(AREA,ASY,ASZ,IXX,IYY,IZZ,WAREA 共7个缩放，正常全 1),
       CY, CZ,
       STIFF(AREA,ASY,ASZ,IXX,IYY,IZZ,CYP,CYM,CZP,CZM,QYB,QZB,X1,X2,X3,X4,Y1,Y2,Y3,Y4,IW 共20个),
       H, VS, M
* 一般截面（能画真实形状、PSC 类型、联合截面认可）：*SECT-PSCVALUE + OPOLY/IPOLY
* 分析前必须先把模型另存为文件（否则 MIDAS 弹"另存为"并取消分析）。
"""
import datetime
import math

import analysis_core      # 分析设置（模态 / 屈曲）的 MCT 片段

GAMMA_CONC = 25.0                      # 混凝土容重 kN/m³
E_CONC = {"C30": 3.00e7, "C35": 3.15e7, "C40": 3.25e7, "C45": 3.35e7,
          "C50": 3.45e7, "C55": 3.55e7, "C60": 3.60e7}   # kN/m²（JTG 3362-18）
# 28 天抗压强度标准值 fck（kN/m² = kPa），给时间依存材料用
FCM_CONC = {"C30": 20.1e3, "C35": 23.4e3, "C40": 26.8e3, "C45": 29.6e3,
            "C50": 32.4e3, "C55": 35.5e3, "C60": 38.5e3}
ARCH_TYPES = [("catenary", "悬链线"), ("parabola", "抛物线"), ("circle", "圆弧")]


# ================================================================ 拱轴线
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


# ================================================================ 截面部件
class Part:
    """施工部件 = 一组矩形 (x1,x2,z1,z2)，单位 m。x 横向、z 竖向，原点在截面左下角。"""

    def __init__(self, name, rects=None, material="C50", cast_stage=None,
                 composite_stage=None, age=20.0, mtype="MATL"):
        self.name = name
        self.rects = [tuple(float(v) for v in r) for r in (rects or [])]
        self.material = material
        self.cast_stage = cast_stage            # 浇筑阶段（湿重）
        self.composite_stage = composite_stage  # 开始参与受力的阶段
        self.age = age
        self.mtype = mtype

    @property
    def area(self):
        return sum((r[1] - r[0]) * (r[3] - r[2]) for r in self.rects)

    @property
    def centroid(self):
        sx = sz = 0.0
        for x1, x2, z1, z2 in self.rects:
            a = (x2 - x1) * (z2 - z1)
            sx += a * (x1 + x2) / 2.0
            sz += a * (z1 + z2) / 2.0
        a = self.area
        return (sx / a, sz / a) if a > 1e-12 else (0.0, 0.0)

    @property
    def bbox(self):
        if not self.rects:
            return (0.0, 0.0, 0.0, 0.0)
        return (min(r[0] for r in self.rects), max(r[1] for r in self.rects),
                min(r[2] for r in self.rects), max(r[3] for r in self.rects))

    def inertia(self):
        """关于自身形心的 Iyy=∫z²dA、Izz=∫x²dA。"""
        cx, cz = self.centroid
        iyy = izz = 0.0
        for x1, x2, z1, z2 in self.rects:
            b, h = x2 - x1, z2 - z1
            a = b * h
            mx, mz = (x1 + x2) / 2.0, (z1 + z2) / 2.0
            iyy += b * h ** 3 / 12.0 + a * (mz - cz) ** 2
            izz += h * b ** 3 / 12.0 + a * (mx - cx) ** 2
        return iyy, izz

    def perimeter(self):
        x1, x2, z1, z2 = self.bbox
        return 2.0 * ((x2 - x1) + (z2 - z1))


class Section:
    def __init__(self, parts=None, name="主拱圈"):
        self.parts = list(parts or [])
        self.name = name

    @property
    def area(self):
        return sum(p.area for p in self.parts)

    @property
    def centroid(self):
        sx = sz = 0.0
        for p in self.parts:
            a = p.area
            cx, cz = p.centroid
            sx += a * cx
            sz += a * cz
        a = self.area
        return (sx / a, sz / a) if a > 1e-12 else (0.0, 0.0)

    @property
    def bbox(self):
        bs = [p.bbox for p in self.parts if p.rects]
        if not bs:
            return (0.0, 0.0, 0.0, 0.0)
        return (min(b[0] for b in bs), max(b[1] for b in bs),
                min(b[2] for b in bs), max(b[3] for b in bs))

    def props(self):
        """整体截面特性；Iyy=∫z²dA（绕横向水平轴，抗弯主惯性矩），Izz=∫y²dA。"""
        cy, cz = self.centroid
        iyy = izz = 0.0
        for p in self.parts:
            if p.area <= 1e-12:
                continue
            px, pz = p.centroid
            i1, i2 = p.inertia()
            iyy += i1 + p.area * (pz - cz) ** 2
            izz += i2 + p.area * (px - cy) ** 2
        x1, x2, z1, z2 = self.bbox
        ip = iyy + izz
        j = (self.area ** 4) / (40.0 * ip) if ip > 1e-12 else 0.0
        return {"A": self.area, "Cy": cy, "Cz": cz, "Iyy": iyy, "Izz": izz, "Ixx": j,
                "CyP": x2 - cy, "CyM": cy - x1, "CzP": z2 - cz, "CzM": cz - z1,
                "B": x2 - x1, "H": z2 - z1,
                "ASy": self.area * 5.0 / 6.0, "ASz": self.area * 5.0 / 6.0}

    def cells(self):
        """截面里的孔洞（给 *SECT-PSCVALUE 的 IPOLY 用）：先按矩形算，够画图了。"""
        return []

    def validate(self):
        out = []
        if not self.parts:
            out.append("截面里一个部件都没有。")
            return out
        for p in self.parts:
            if not p.rects:
                out.append("部件「%s」没有矩形。" % p.name)
            for r in p.rects:
                if r[1] - r[0] <= 1e-9 or r[3] - r[2] <= 1e-9:
                    out.append("部件「%s」有宽或高 ≤ 0 的矩形：(%g,%g,%g,%g)" % (p.name, r[0], r[1], r[2], r[3]))
        if self.area <= 1e-9:
            out.append("截面总面积是 0。")
        # 防重叠：部件之间面积不能重复（简单按矩形两两检查）
        rects = [(p, r) for p in self.parts for r in p.rects]
        for i in range(len(rects)):
            for j in range(i + 1, len(rects)):
                pi, ri = rects[i]
                pj, rj = rects[j]
                if pi is pj:
                    continue
                ox = min(ri[1], rj[1]) - max(ri[0], rj[0])
                oz = min(ri[3], rj[3]) - max(ri[2], rj[2])
                if ox > 1e-6 and oz > 1e-6:
                    out.append("部件「%s」和「%s」的矩形重叠了（面积会被算两遍）：%s / %s"
                               % (pi.name, pj.name, ri, rj))
        return out


# ================================================================ 预设：多箱室箱形截面
def box_parts(W=0.0, cell_w=1.2, cell_h=1.0, cells=3,
              t_top_pre=0.10, t_bot=0.20, t_out=0.20, t_in=0.10,
              t_joint=0.30, t_top_cip=0.15, material="C50"):
    """按"多箱室箱形截面"自动生成三个施工部件：
        部件1 预制部分（底板 + 预制顶板 + 外腹板 + 内腹板）
        部件2 纵缝（每道纵缝一个部件，其实就是竖条，合并成一个部件也行）
        部件3 现浇顶板
    W>0 时把它当总宽：只反算「箱室净宽」去凑，其余尺寸不动（界面会把结果写回去）。
    返回 Section（sec.box 里记着实际尺寸）。坐标：x 从 0 起，z 从 0 起。
    """
    cells = max(1, int(cells))
    _fixed = 2.0 * t_out + (cells - 1) * (2.0 * t_in + t_joint)
    cell_w = float(cell_w)
    if W and float(W) > 0:
        _cw = (float(W) - _fixed) / cells
        if _cw > 0.05:
            cell_w = _cw
    H_pre = t_bot + cell_h + t_top_pre
    H = H_pre + t_top_cip
    # 横向布置：外腹板 + [箱室 + (内腹板 + 纵缝 + 内腹板)] × ... + 外腹板
    x = t_out
    cell_ranges, joint_ranges = [], []
    for i in range(cells):
        cell_ranges.append((x, x + cell_w))
        x += cell_w
        if i < cells - 1:
            j0 = x + t_in
            joint_ranges.append((j0, j0 + t_joint))
            x = j0 + t_joint + t_in
    total_w = x + t_out
    W = total_w

    z_bot0, z_bot1 = 0.0, t_bot
    z_cell0, z_cell1 = t_bot, t_bot + cell_h
    z_top0, z_top1 = z_cell1, H_pre

    pre = [(0.0, W, z_bot0, z_bot1),        # 底板
           (0.0, W, z_top0, z_top1)]        # 预制顶板
    pre.append((0.0, t_out, z_cell0, z_cell1))            # 左外腹板
    pre.append((W - t_out, W, z_cell0, z_cell1))          # 右外腹板
    for (j0, j1) in joint_ranges:                          # 纵缝两侧的内腹板
        pre.append((j0 - t_in, j0, z_cell0, z_cell1))
        pre.append((j1, j1 + t_in, z_cell0, z_cell1))
    pre = [r for r in pre if r[1] - r[0] > 1e-9 and r[3] - r[2] > 1e-9]

    parts = [Part("预制部分", pre, material=material)]
    if joint_ranges:
        jr = [(j0, j1, z_cell0, z_cell1) for (j0, j1) in joint_ranges]
        parts.append(Part("纵缝", jr, material=material))
    if t_top_cip > 0:
        parts.append(Part("现浇顶板", [(0.0, W, z_top1, H)], material=material))
    sec = Section(parts, name="主拱圈")
    sec.box = {"W": W, "H": H, "cells": cells, "cell_w": cell_w, "cell_h": cell_h,
               "t_bot": t_bot, "t_top_pre": t_top_pre, "t_top_cip": t_top_cip,
               "t_out": t_out, "t_in": t_in, "t_joint": t_joint,
               "z_bot": z_bot1, "z_cell0": z_cell0, "z_cell1": z_cell1, "z_top": z_top1,
               "cell_ranges": cell_ranges, "joint_ranges": joint_ranges}
    return sec


def box_outline(sec):
    """从 part 的矩形拼出外轮廓 + 孔洞多边形（给 *SECT-PSCVALUE 画图用）。

    这里不做通用布尔运算：矩形拼出来的箱形，外轮廓 = 包围盒，
    孔洞 = 每个箱室（cell_ranges × [z_cell0, z_cell1]）。
    """
    box = getattr(sec, "box", None)
    x1, x2, z1, z2 = sec.bbox
    if not box:
        return [(x1, z1), (x2, z1), (x2, z2), (x1, z2)], []
    cells = []
    for (a, b) in box["cell_ranges"]:
        cells.append([(a, box["z_cell0"]), (a, box["z_cell1"]),
                      (b, box["z_cell1"]), (b, box["z_cell0"])])
    return [(x1, z1), (x2, z1), (x2, z2), (x1, z2)], cells


def section_from_model(model):
    """从 model["parts"]（可 JSON 存的字典列表）构造 Section。"""
    parts = []
    for p in model.get("parts", []):
        part = Part(p.get("name", "部件"), p.get("rects", []),
                    material=p.get("material", model.get("material", "C50")),
                    cast_stage=p.get("cast_stage"),
                    composite_stage=p.get("composite_stage"),
                    age=p.get("age", 20.0))
        parts.append(part)
    sec = Section(parts, name=model.get("section_name", "主拱圈"))
    for k in ("box",):
        if k in model:
            sec.box = model[k]
    return sec


def loadcase_names(model):
    """这个拱桥模型里会生成的荷载工况名（屈曲分析选「可变荷载」时用）。"""
    names = ["SW"]
    try:
        sec = get_section(model)
    except Exception:                                          # noqa: BLE001
        return names
    for idx in range(1, len(sec.parts)):
        names.append("WET%d" % idx)
    return names


def get_section(model):
    sec = model.get("section")
    if isinstance(sec, Section):
        return sec
    return section_from_model(model)


# ================================================================ 小工具
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


def _ascii(s):
    return "".join(ch for ch in str(s) if ord(ch) < 128) or "P"


def _ccw(poly):
    s = sum(poly[i][0] * poly[(i + 1) % len(poly)][1] - poly[(i + 1) % len(poly)][0] * poly[i][1]
            for i in range(len(poly)))
    return poly if s > 0 else list(reversed(poly))


def _cw(poly):
    p = _ccw(poly)
    return list(reversed(p))


# ================================================================ 一般截面（PSC-VALUE）
def pscvalue_lines(sec, name="SEC1"):
    """生成 *SECT-PSCVALUE 的截面数据行（和 MIDAS 自己导出的格式逐字段对齐）。

    注意：第 3 行最后两个是整体形心 Cy, Cz；第 4 行只有 8 个数（Y1..Y4, Z1..Z4），
    多写会整份导入失败（实测）。
    """
    sp = sec.props()
    outer, cells = box_outline(sec)
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
        "       OPOLY=" + ", ".join("%s, %s" % (_num(p[0]), _num(p[1])) for p in _ccw(outer)),
    ]
    for c in cells:
        lines.append("       IPOLY=" + ", ".join("%s, %s" % (_num(p[0]), _num(p[1])) for p in _cw(c)))
    return lines


# ================================================================ MCT 生成
def tdm_lines(model):
    """时间依存材料（收缩徐变 / 抗压强度发展）的 MCT 块。

    格式照 MIDAS 自己导出的 FCM 教程样本写（CEB 规范）。注意：CEB-FIP 里填的是
    28 天平均抗压强度 fcm（kN/m² = kPa），不是弹性模量 —— 填成弹模会算出天文数字的徐变。
    """
    mat = model.get("material", "C50")
    fck = FCM_CONC.get(mat, 32.4e3)
    fcm = fck + 8.0e3
    name = "TDM_%s" % _ascii(mat)
    try:
        hum = float(model.get("humidity") or 70)
    except (TypeError, ValueError):
        hum = 70.0
    lines = ["*TDM-TYPE    ; Time Dependent Material (Creep/Shrinkage)",
             "   NAME=%s, CEB, %.6g, %s, 1, NR, 3, 0" % (name, fcm, _num(hum)),
             "*TDM-ELAST    ; Time Dependent Material (Comp. Strength)",
             "   NAME=%s, CODE, CEB-FIP, %.6g, 2" % (name, fcm),
             "*TDM-LINK    ; Time Dependent Material Link",
             "   1, %s, %s" % (name, name),
             ""]
    return lines


def build_mct(model):
    """生成施工阶段模型的 MCT 文本。model 见 default_model()。"""
    arch = model["arch"]
    sec = get_section(model)
    stages = model["stages"]
    try:
        gamma = float(model.get("gamma") or GAMMA_CONC)
    except (TypeError, ValueError):
        gamma = GAMMA_CONC
    mat = model.get("material", "C50")
    name = model["project"].get("name") or "拱桥模型"
    E = E_CONC.get(mat, 3.45e7)

    pts = arch_axis(arch["L"], arch["f"], arch.get("m", 1.6),
                    arch.get("type", "catenary"), arch.get("n", 40), arch.get("z0", 0.0))
    n_elem = len(pts) - 1
    sp = sec.props()
    outer, cells = box_outline(sec)
    Lw = []

    def add(s=""):
        Lw.append(s)

    add("; ================================================================")
    add("; MIDAS Civil NX — MCT（拱桥 · 施工阶段联合截面）")
    add("; 生成时间：" + datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    add("; 项目：%s" % name)
    add("; ================================================================")
    add("*VERSION")
    add("   9.5.0")
    add("*UNIT    ; Unit System")
    add("   KN, M, KJ, C")
    add("*STRUCTYPE    ; Structure Type")
    add("   0, 1, 1, NO, NO, 9.806, 0, NO, NO, NO")

    # ---- 节点/单元 ----
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
    wet_groups = []
    for idx, part in enumerate(sec.parts[1:], start=1):
        g = "WET%d" % idx          # 组名必须唯一且 ASCII（中文名过滤后容易重名）
        wet_groups.append(g)
        add("   %s" % g)

    # ---- 材料 ----
    add("*MATERIAL    ; Material")
    add("   1, CONC , %s, 0, 0, , C, NO, 0.05, 2, %.4e, 0.2, 1.0e-05, %s, 0"
        % (mat, E, _num(gamma)))
    if model.get("creep"):
        for ln in tdm_lines(model):
            add(ln)

    # ---- 截面（*SECT-PSCVALUE，真实形状 + PSC 类型）----
    add("*SECT-PSCVALUE    ; PSC Value / General Section")
    for ln in pscvalue_lines(sec, sec.name):
        add(ln)
    add("")

    # ---- 荷载工况 ----
    add("*STLDCASE    ; Static Load Cases")
    add("   SW, CS, \"Self Weight\"")
    for g in wet_groups:
        add("   %s, CS, \"Wet Concrete\"" % g)

    # ---- 约束 ----
    add("*CONSTRAINT    ; Supports")
    fx = "111111" if arch.get("fixed", True) else "011111"
    add("   1, %s, BC" % fx)
    add("   %d, %s, BC" % (n_elem + 1, fx))

    # ---- 荷载 ----
    add("*USE-STLD, SW")
    add("*SELFWEIGHT    ; Self Weight")
    add("0, 0, -1, SW")
    for part, g in zip(sec.parts[1:], wet_groups):
        q = part.area * gamma
        add("*USE-STLD, %s" % g)
        add("*BEAMLOAD    ; Element Beam Loads（湿重）")
        for e in range(1, n_elem + 1):
            add("     %d, BEAM   , UNILOAD, GZ, NO , NO, LY, , , , 0, %s, 1, %s, 0, 0, 0, 0, %s, NO, 0, 0, NO, "
                % (e, _num(-q), _num(-q), g))

    # ---- 施工阶段 ----
    add("*STAGE    ; Define Construction Stage")
    for i, st in enumerate(stages, start=1):
        add("  NAME=%s, %s, YES, YES, NO, 5" % (st["name"], st.get("days", 20)))
        if i == 1:
            add("  AELEM=EL_ALL, %s" % st.get("age", 20))
            add("  ABNDR=BC, DEFORMED")
            add("  ALOAD=SW, FIRST")
        else:
            act = []
            if i - 2 < len(wet_groups):
                act.append("%s, FIRST" % wet_groups[i - 2])
            if i - 3 >= 0 and i - 3 < len(wet_groups):
                # 上一批湿混凝土硬化 → 卸掉湿重
                add("  DLOAD=%s, FIRST" % wet_groups[i - 3])
            if act:
                add("  ALOAD=" + ", ".join(act))

    # ---- 联合截面 ----
    add("*CPOSECT4CS    ; Composite Section for Construction Stage")
    add("   SEC=1, %s, USER, NO" % stages[0]["name"])
    cy, cz = sec.centroid
    for k, part in enumerate(sec.parts):
        px, pz = part.centroid
        a = part.area
        iyy, izz = part.inertia()
        x1, x2, z1, z2 = part.bbox
        cyp, cym = x2 - px, px - x1
        czp, czm = z2 - pz, pz - z1
        per = part.perimeter()
        h = 2.0 * a / per if per > 1e-9 else 0.5
        if k == 0:
            # 第 1 部件就是"激活阶段"那一份，CSTAGE/AGE 留空（实测：写阶段名会被拒）
            cstage, age = "", 0
        elif part.composite_stage:
            cstage, age = part.composite_stage, part.age
        else:
            idx = min(k + 1, len(stages) - 1)
            cstage, age = stages[idx]["name"], part.age
        row = [_fmt_cs(k + 1), "ELEM" if k == 0 else "MATL", _fmt_cs(1 if k > 0 else ""),
               cstage, _fmt_cs(age),
               *[_fmt_cs(1) for _ in range(7)],
               _fmt_cs(px - cy), _fmt_cs(pz - cz),
               _fmt_cs(a), _fmt_cs(5 * a / 6), _fmt_cs(5 * a / 6),
               _fmt_cs(iyy + izz), _fmt_cs(iyy), _fmt_cs(izz),
               _fmt_cs(cyp), _fmt_cs(cym), _fmt_cs(czp), _fmt_cs(czm), _fmt_cs(0), _fmt_cs(0),
               _fmt_cs(-cym), _fmt_cs(cyp), _fmt_cs(cyp), _fmt_cs(-cym),
               _fmt_cs(-czm), _fmt_cs(-czm), _fmt_cs(czp), _fmt_cs(czp),
               _fmt_cs(h), _fmt_cs(0), _fmt_cs(0)]
        add("       " + ", ".join(str(v) for v in row))

    # ---- 控制 ----
    add("*STAGE-CTRL    ; Construction Stage Analysis Control Data")
    add("  YES, , INTERNAL, NO, NO, NO, NO, NO, YES, YES, %s, , , NO, NO, , NO, NO, NO"
        % ("YES" if model.get("creep") else "NO"))
    add("  0 ")
    add("  NO")
    add("  YES, YES, BOTH, 5, 0.01, YES, NO, YES, YES, 0, NO")
    add("  NO, NO, 2, YES, 2, 5, 7, 10, 20")
    add("  NO, , , 1, NO")
    add("*ANAL-CTRL")
    add("   YES, YES, 20, 0.001, YES, YES, YES, NO, NO, NO, NO, YES")

    # ---- 分析设置（可选，默认不写）：模态 / 屈曲 ----
    for _ln in analysis_core.lines(model, kinds=("eigen", "buckle")):
        add(_ln)

    add("*ENDDATA")
    return "\r\n".join(Lw) + "\r\n"


def build_one_step_mct(model):
    """一次成拱对比模型：单阶段、全截面从开始就有效、只加自重。"""
    arch = model["arch"]
    sec = get_section(model)
    mat = model.get("material", "C50")
    try:
        gamma = float(model.get("gamma") or GAMMA_CONC)
    except (TypeError, ValueError):
        gamma = GAMMA_CONC
    E = E_CONC.get(mat, 3.45e7)
    pts = arch_axis(arch["L"], arch["f"], arch.get("m", 1.6),
                    arch.get("type", "catenary"), arch.get("n", 40), arch.get("z0", 0.0))
    n_elem = len(pts) - 1
    sp = sec.props()
    outer, cells = box_outline(sec)
    Lw = []

    def add(s=""):
        Lw.append(s)

    add("; 一次成拱（对比模型）")
    add("*VERSION"); add("   9.5.0")
    add("*UNIT    ; Unit System"); add("   KN, M, KJ, C")
    add("*STRUCTYPE    ; Structure Type"); add("   0, 1, 1, NO, NO, 9.806, 0, NO, NO, NO")
    add("*NODE    ; Nodes")
    for i, (x, z) in enumerate(pts, start=1):
        add("   %d, %s, 0, %s" % (i, _num(x, 8), _num(z, 8)))
    add("*ELEMENT    ; Elements")
    for i in range(1, n_elem + 1):
        add("   %d, BEAM, 1, 1, %d, %d, 0, 0" % (i, i, i + 1))
    add("*GROUP    ; Group")
    add("   EL_ALL, 1to%d, 1to%d, 0" % (n_elem + 1, n_elem))
    add("*BNDR-GROUP    ; Boundary Group"); add("   BC, 0")
    add("*LOAD-GROUP    ; Load Group"); add("   SW")
    add("*MATERIAL    ; Material")
    add("   1, CONC , %s, 0, 0, , C, NO, 0.05, 2, %.4e, 0.2, 1.0e-05, %s, 0"
        % (mat, E, _num(gamma)))
    if model.get("creep"):
        for ln in tdm_lines(model):
            add(ln)
    add("*SECT-PSCVALUE    ; PSC Value / General Section")
    for ln in pscvalue_lines(sec, sec.name):
        add(ln)
    add("*STLDCASE    ; Static Load Cases"); add("   SW, CS, \"Self Weight\"")
    add("*CONSTRAINT    ; Supports")
    fx = "111111" if arch.get("fixed", True) else "011111"
    add("   1, %s, BC" % fx)
    add("   %d, %s, BC" % (n_elem + 1, fx))
    add("*USE-STLD, SW")
    add("*SELFWEIGHT    ; Self Weight"); add("0, 0, -1, SW")
    add("*STAGE    ; Define Construction Stage")
    add("  NAME=CS1, 30, YES, YES, NO, 5")
    add("  AELEM=EL_ALL, 30")
    add("  ABNDR=BC, DEFORMED")
    add("  ALOAD=SW, FIRST")
    add("*STAGE-CTRL    ; Construction Stage Analysis Control Data")
    add("  YES, , INTERNAL, NO, NO, NO, NO, NO, YES, YES, %s, , , NO, NO, , NO, NO, NO"
        % ("YES" if model.get("creep") else "NO"))
    add("  0 "); add("  NO")
    add("  YES, YES, BOTH, 5, 0.01, YES, NO, YES, YES, 0, NO")
    add("  NO, NO, 2, YES, 2, 5, 7, 10, 20")
    add("  NO, , , 1, NO")
    add("*ANAL-CTRL")
    add("   YES, YES, 20, 0.001, YES, YES, YES, NO, NO, NO, NO, YES")
    add("*ENDDATA")
    return "\r\n".join(Lw) + "\r\n"


# ================================================================ 默认模型（习题 5）
def default_model():
    """习题 5：100 m 跨、20 m 矢高、m=1.6、C50、预制拼装 + 纵缝 + 现浇顶板。"""
    sec = box_parts(W=5.2, cell_w=1.2, cell_h=1.0, cells=3,
                    t_top_pre=0.10, t_bot=0.20, t_out=0.20, t_in=0.10,
                    t_joint=0.30, t_top_cip=0.15, material="C50")
    stages = [{"name": "CS1", "days": 5, "age": 5, "desc": "预制拱圈安装、合龙"},
              {"name": "CS2", "days": 20, "age": 20, "desc": "浇筑纵缝混凝土（湿重）"},
              {"name": "CS3", "days": 20, "age": 20, "desc": "纵缝硬化并参与受力、浇筑顶板（湿重）"},
              {"name": "CS4", "days": 30, "age": 30, "desc": "顶板硬化、全截面成拱"}]
    comp = ["CS1", "CS3", "CS4"]
    ages = [5, 20, 30]
    parts = []
    for k, p in enumerate(sec.parts):
        parts.append({"name": p.name, "rects": [list(r) for r in p.rects],
                      "composite_stage": comp[k] if k < len(comp) else "CS1",
                      "age": ages[k] if k < len(ages) else 20})
    return {
        "mode": "arch",
        "project": {"name": "拱桥100m_施工阶段", "force": "KN", "dist": "M"},
        "arch": {"L": 100.0, "f": 20.0, "m": 1.6, "type": "catenary",
                 "n": 40, "z0": 0.0, "fixed": True},
        "parts": parts, "box": sec.box, "section_name": sec.name,
        "material": "C50", "gamma": GAMMA_CONC, "creep": False, "humidity": 70, "stages": stages,
    }
