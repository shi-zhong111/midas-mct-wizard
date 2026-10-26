# -*- coding: utf-8 -*-
r"""预制 T 梁 + 现浇桥面板 · 施工阶段联合截面 —— 核心引擎（纯计算 + MCT 生成，不依赖 tkinter）。

算例 = 老师第 05 讲计算示例 / 《桥梁结构电算》习题 4（20 m 混凝土 T 梁）：
    预制主梁（梁肋）0.40 m 宽 × 1.00 m 高，现浇桥面板 1.60 m 宽 × 0.25 m 厚，
    主梁预制架设 → 现浇桥面板（湿重）→ 龄期 20 d 桥面板硬化（联合截面形成）→ 桥面系 20 kN/m。

本模块只做两件事：
    1) 纯函数算题解要的全部结果（截面特性 / 各阶段内力 / 联合截面内力分配 / 跨中应力 /
       与一次成型对比），界面和测试都调这些函数；
    2) 生成 MIDAS Civil NX 能直接导入的 .mct（*STAGE + *CPOSECT4CS 联合截面）。

MCT 格式来源（一律以本机 MIDAS 真跑过的样本为准，不照手册猜）
------------------------------------------------------------------
*STAGE、*CPOSECT4CS、*SECT-PSCVALUE、*BEAMLOAD、*STAGE-CTRL / *ANAL-CTRL 的写法全部照
C:\Midas\work\e2e\施工阶段.mct —— 那份模型在 MIDAS Civil NX 里真跑过施工阶段分析。
关键结论（别再踩一遍）：
  1) *CPOSECT4CS 每个部件行必须 **37 个字段**：
       5  （PART, MTYPE, MAT, CSTAGE, AGE）
     + 7  （截面特性缩放系数，正常全 1）
     + 2  （CY, CZ：该部件形心相对「基准点」的偏距）
     + 20 （AREA, ASy, ASz, Ixx, Iyy, Izz, CyP, CyM, CzP, CzM, Qyb, Qzb, Y1..Y4, Z1..Z4）
     + 3  （h 理论厚度, v/s, 刚度调整 M）
     多一个字段整份 MCT 会被 MIDAS 静默拒绝。
     数字必须写成固定小数（e 记法会被拒）。
  2) 第 1 个部件（预制主梁，从 CS1 就随单元激活）的 CSTAGE / AGE 留空 / 0，
     它的激活阶段写在 SEC= 行的第 2 个字段（本例 CS1）。
  3) 真跑过的样本里，各部件 CZ 按面积加权 **ΣA·CZ = 0** —— 也就是说「基准点」就是
     单元线，且单元线通过组合截面形心。本流程照此：节点线（单元线）取在组合截面形心高度，
     主梁 Part 的 CZ = -0.3125 m、桥面板 Part 的 CZ = +0.3125 m（可验算 ΣA·CZ = 0）。
     以梁底为 0 起算：梁底 z = -0.8125、主梁顶 / 板底 z = +0.1875、板顶 z = +0.4375。
  4) 单元上赋的虚拟截面必须是 PSC 类型（*SECT-PSCVALUE），本例取「组合 T 形」
     （A=0.8 m2、Iyy=0.113542 m4，Center: z = 0.8125），与联合截面的实际截面同位置。
  5) 荷载：本例三个荷载都用梁单元均布荷载（*BEAMLOAD）显式给 q 值，不用 *SELFWEIGHT：
     单元上的是组合 T 形虚拟截面（A=0.8 m2），*SELFWEIGHT 会算出 0.8×25 = 20 kN/m，
     和题解的 q1 = q2 = 10 kN/m 对不上；显式给 q 才能和题解逐项核对。
  6) 湿重（桥面板湿混凝土 10 kN/m）在 CS2 加在预制主梁上，**CS3 不卸除**：
     它就是桥面板硬化后的恒载（题解里 CS3 只让桥面板 Part 开始参与刚度、不加减荷载），
     卸掉的话总弯矩只剩 1500 kN·m，和题解的 2000 kN·m 对不上。
"""

import datetime
import math

# ----------------------------------------------------------------------
# 常量
# ----------------------------------------------------------------------
CODE = "tbeam"
LABEL = "预制T梁+现浇桥面板（施工阶段联合截面）"

GAMMA_CONC = 25.0              # 混凝土容重 kN/m3

# 混凝土：等级 -> (弹性模量 kN/m2, 28 天抗压强度标准值 kPa)
# 取值同 cfst_core.py / arch_core.py（JTG 3362-18），fck 只留给以后开收缩徐变用
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

STEP_IDS = ["tbeam_geom", "tbeam_stages", "tbeam_check", "tbeam_export"]
STEP_TITLES = ["跨径与截面", "施工阶段", "结果预览", "生成成果"]

# 题解默认值（第 05 讲 / 习题 4）
DEF_L = 20.0
DEF_BS = 1.60
DEF_TS = 0.25
DEF_BW = 0.40
DEF_HW = 1.00
DEF_N = 20
DEF_Q3 = 20.0


# ----------------------------------------------------------------------
# 数字 / 字符串小工具
# ----------------------------------------------------------------------
def _f(v, default=0.0):
    """把界面上存成字符串的数值安全地转成 float。"""
    try:
        if v is None or str(v).strip() == "":
            return float(default)
        return float(str(v).strip())
    except (TypeError, ValueError):
        return float(default)


def _num(v, nd=6):
    """MCT 里的普通数字（6 位有效数字）。"""
    if isinstance(v, int):
        return str(v)
    try:
        f = float(v)
    except (TypeError, ValueError):
        return str(v)
    if abs(f) < 1e-12:
        return "0"
    return "%.*g" % (nd, f)


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
    """多边形顶点坐标：固定小数（别让 6e-17 这种 e 记法写进 MCT）。"""
    f = float(v)
    if abs(f) < 1e-9:
        return "0"
    s = ("%.9f" % f).rstrip("0").rstrip(".")
    return s if s not in ("", "-0") else "0"


def _ascii(s, fallback="P"):
    return "".join(ch for ch in str(s) if ord(ch) < 128) or fallback


def _mm(v, nd=2):
    """界面/报告里的数字（定点小数）。"""
    try:
        return ("%." + str(nd) + "f") % float(v)
    except (TypeError, ValueError):
        return "-"


# ----------------------------------------------------------------------
# 参数 / 截面几何特性
# ----------------------------------------------------------------------
def model_conc(model):
    c = str((model.get("geom") or {}).get("conc") or "C50").strip()
    return c if c in CONC else "C50"


def model_gamma(model):
    g = _f((model.get("geom") or {}).get("gamma"), GAMMA_CONC)
    return g if g > 0 else GAMMA_CONC


def geom(model):
    """按 model["geom"] 算截面几何特性（单位 m / m2 / m4）。

    坐标约定：以 **梁底为 0**、向上为正（z / y 都是竖坐标）。
      A1,y1,I1  预制主梁梁肋：bw×hw，形心距梁底 hw/2
      A2,y2,I2  现浇桥面板：bs×ts，形心距梁底 hw+ts/2
      A,yb,I    组合截面（形心距梁底 yb）
      I1c,I2c   各部件绕组合形心的惯性矩（= I_i + A_i·d_i^2），决定联合后弯矩分担
      k1,k2     分担比例（题解 63.76 % / 36.24 %）
      h1,h2     各部件理论厚度 h = 2A/u（u = 与大气接触的周长）
    """
    p = model.get("geom") or {}
    L = _f(p.get("L"), DEF_L)
    bs = _f(p.get("bs"), DEF_BS)
    ts = _f(p.get("ts"), DEF_TS)
    bw = _f(p.get("bw"), DEF_BW)
    hw = _f(p.get("hw"), DEF_HW)
    n = int(_f(p.get("n"), DEF_N))
    gamma = model_gamma(model)
    q3 = _f(p.get("q3"), DEF_Q3)
    conc = model_conc(model)

    # 参数坏掉时也要能算（返回 0），界面靠 issues() 拦人
    A1 = bw * hw
    y1 = hw / 2.0
    I1 = bw * hw ** 3 / 12.0
    A2 = bs * ts
    y2 = hw + ts / 2.0
    I2 = bs * ts ** 3 / 12.0
    A = A1 + A2
    yb = (A1 * y1 + A2 * y2) / A if abs(A) > 1e-15 else 0.0
    d1 = yb - y1
    d2 = y2 - yb
    I1c = I1 + A1 * d1 ** 2
    I2c = I2 + A2 * d2 ** 2
    I = I1c + I2c
    k1 = I1c / I if abs(I) > 1e-15 else 0.5
    k2 = I2c / I if abs(I) > 1e-15 else 0.5

    # 理论厚度 h = 2Ac/u（u = 与大气接触的周边长度）
    #   主梁：两侧面 2hw + 底面 bw（顶面被桥面板/模板盖住）
    #   桥面板：顶面 bs + 两侧 2ts + 底面外露部分 (bs - bw)
    u1 = 2.0 * hw + bw
    u2 = bs + 2.0 * ts + max(bs - bw, 0.0)
    h1 = 2.0 * A1 / u1 if u1 > 1e-9 else 0.0
    h2 = 2.0 * A2 / u2 if u2 > 1e-9 else 0.0

    # 组合截面绕竖轴（∫x2dA），给 *SECT-PSCVALUE 用
    izz1 = hw * bw ** 3 / 12.0
    izz2 = ts * bs ** 3 / 12.0

    return {
        "L": L, "bs": bs, "ts": ts, "bw": bw, "hw": hw, "n": n,
        "gamma": gamma, "q3": q3, "conc": conc,
        "B": bs, "H": hw + ts,
        "A1": A1, "y1": y1, "I1": I1, "d1": d1, "I1c": I1c,
        "A2": A2, "y2": y2, "I2": I2, "d2": d2, "I2c": I2c,
        "A": A, "yb": yb, "I": I, "k1": k1, "k2": k2,
        "Izz": izz1 + izz2, "Ixx": I + izz1 + izz2,
        "h1": h1, "h2": h2, "u1": u1, "u2": u2,
        "z_beam_bot": -yb, "z_beam_top": hw - yb, "z_slab_top": hw + ts - yb,
        "cstage": str(model.get("comp_stage") or "CS3").strip(),
        "age": max(0.0, _f(model.get("slab_age"), 20.0)),
    }


def section_rows(model):
    """数据表用：两块截面 + 组合截面的特性表（列表套列表）。"""
    g = geom(model)
    return [
        ["预制主梁（梁肋）", "%s × %s" % (_mm(g["bw"]), _mm(g["hw"])),
         _mm(g["A1"], 4), _mm(g["y1"], 4), _mm(g["I1"], 6)],
        ["现浇桥面板", "%s × %s" % (_mm(g["bs"]), _mm(g["ts"])),
         _mm(g["A2"], 4), _mm(g["y2"], 4), _mm(g["I2"], 6)],
        ["组合截面（虚拟截面 = 实际截面）", "T 形",
         _mm(g["A"], 4), _mm(g["yb"], 4), _mm(g["I"], 6)],
    ]


# ----------------------------------------------------------------------
# 施工阶段 / 荷载
# ----------------------------------------------------------------------
def stage_names(model):
    return [str(s.get("name") or "").strip() for s in (model.get("stages") or [])]


def stage_days(model):
    return [_f(s.get("days"), 0.0) for s in (model.get("stages") or [])]


def stage_index(model, name):
    try:
        return stage_names(model).index(str(name or "").strip())
    except ValueError:
        return -1


def comp_stage_of(model):
    """桥面板 Part 开始参与受力（联合截面生效）的阶段名。"""
    nm = str(model.get("comp_stage") or "").strip()
    names = stage_names(model)
    if nm in names:
        return nm
    return names[2] if len(names) > 2 else (names[-1] if names else "")


def wet_stage_of(model):
    """湿重加在「联合截面生效阶段」的前一个阶段（默认 CS2 ← CS3）。"""
    i = stage_index(model, comp_stage_of(model))
    names = stage_names(model)
    if i <= 0:
        return ""
    return names[i - 1]


def q1_value(model):
    """主梁自重 q1 = A1 × γ（kN/m）。"""
    g = geom(model)
    return g["A1"] * g["gamma"]


def q2_value(model):
    """桥面板湿重 q2 = A2 × γ（kN/m）。"""
    g = geom(model)
    return g["A2"] * g["gamma"]


def q3_value(model):
    """二期恒载 q3（kN/m）。"""
    q = _f((model.get("geom") or {}).get("q3"), DEF_Q3)
    return q if q > 0 else 0.0


def load_items(model):
    """三个施工阶段荷载：[(组名, q kN/m, 阶段, 名称, 由谁承担), ...]"""
    names = stage_names(model)
    first = names[0] if names else "CS1"
    wet = wet_stage_of(model)
    last = names[-1] if names else "CS4"
    return [
        ("SW", q1_value(model), first, "主梁自重 q1 = A1×γ", "预制主梁截面"),
        ("WET1", q2_value(model), wet, "现浇板湿重 q2 = A2×γ", "预制主梁截面"),
        ("DL2", q3_value(model), last, "二期恒载 q3（桥面系）", "组合截面"),
    ]


# ----------------------------------------------------------------------
# 结果计算（题解 2.3 / 2.4 / 2.5 / 2.6 的全部数值）
# ----------------------------------------------------------------------
def sigma(M, y, ybar, I):
    """弯矩 M（kN·m）下、距梁底 y 处的正应力（kPa，受拉为正）。

    σ(y) = -M·(y - ybar)/I：M 正（下挠）、y 在形心上方时为压。
    """
    if abs(I) < 1e-15:
        return 0.0
    return -M * (y - ybar) / I


def results(model):
    """题解全部结果。返回 dict（数值单位 kN / kN·m / m / m2 / m4；应力 MPa 另存 stress）。"""
    g = geom(model)
    L, gamma, q3 = g["L"], g["gamma"], q3_value(model)
    q1 = g["A1"] * gamma
    q2 = g["A2"] * gamma

    M1 = q1 * L * L / 8.0
    M2 = q2 * L * L / 8.0
    M3 = q3 * L * L / 8.0
    V1 = q1 * L / 2.0
    V2 = q2 * L / 2.0
    V3 = q3 * L / 2.0
    M_total = M1 + M2 + M3
    V_total = V1 + V2 + V3

    # ---- 内力分配（题解 2.4）----
    M_pre = M1 + M2              # 阶段 1+2：全部由预制主梁承担
    M_beam = M_pre + M3 * g["k1"]
    M_slab = M3 * g["k2"]

    # ---- 应力（题解 2.5）：阶段 1+2 用预制主梁截面，阶段 3 用组合截面 ----
    hw, ts, yb = g["hw"], g["ts"], g["yb"]
    y_slab_top = hw + ts
    pre_bot = sigma(M_pre, 0.0, g["y1"], g["I1"])
    pre_top = sigma(M_pre, hw, g["y1"], g["I1"])
    comp_bot = sigma(M3, 0.0, yb, g["I"])
    comp_beam_top = sigma(M3, hw, yb, g["I"])       # 主梁顶 = 板底（同一个标高）
    comp_slab_top = sigma(M3, y_slab_top, yb, g["I"])

    # ---- 一次成型对比（题解 2.6）：全部 2000 kN·m 由组合截面承担 ----
    one_bot = sigma(M_total, 0.0, yb, g["I"])
    one_beam_top = sigma(M_total, hw, yb, g["I"])
    one_slab_top = sigma(M_total, y_slab_top, yb, g["I"])

    return {
        "g": g,
        "q1": q1, "q2": q2, "q3": q3,
        "M1": M1, "M2": M2, "M3": M3, "M_total": M_total,
        "V1": V1, "V2": V2, "V3": V3, "V_total": V_total,
        "M_pre": M_pre, "M_beam": M_beam, "M_slab": M_slab,
        "share_beam": g["k1"], "share_slab": g["k2"],
        "stress": {
            "pre": {"beam_bot": pre_bot / 1e3, "beam_top": pre_top / 1e3},
            "comp": {"beam_bot": comp_bot / 1e3, "beam_top": comp_beam_top / 1e3,
                     "slab_bot": comp_beam_top / 1e3, "slab_top": comp_slab_top / 1e3},
            "total": {"beam_bot": (pre_bot + comp_bot) / 1e3,
                      "beam_top": (pre_top + comp_beam_top) / 1e3,
                      "slab_bot": comp_beam_top / 1e3,
                      "slab_top": comp_slab_top / 1e3},
            "one_step": {"beam_bot": one_bot / 1e3, "beam_top": one_beam_top / 1e3,
                         "slab_top": one_slab_top / 1e3},
        },
    }


def stress_rows(model):
    """结果表的行：[(位置, 阶段1+2, 阶段3, 叠加, 一次成型), ...]（MPa）。"""
    r = results(model)
    s = r["stress"]
    t, p, c, o = s["total"], s["pre"], s["comp"], s["one_step"]
    return [
        ["主梁底（z = 梁底）", _mm(p["beam_bot"]), _mm(c["beam_bot"]),
         _mm(t["beam_bot"]), _mm(o["beam_bot"])],
        ["主梁顶 / 板底", _mm(p["beam_top"]), _mm(c["beam_top"]),
         _mm(t["beam_top"]), _mm(o["beam_top"])],
        ["板顶", "—", _mm(c["slab_top"]), _mm(t["slab_top"]), _mm(o["slab_top"])],
    ]


def force_rows(model):
    """内力表的行：[(荷载/阶段, 跨中 M, 支点 V, 承担截面), ...]。"""
    r = results(model)
    g = r["g"]
    rows = [
        ["主梁自重 q1（CS1）", _mm(r["M1"], 2), _mm(r["V1"], 2), "预制主梁截面"],
        ["现浇板湿重 q2（CS2）", _mm(r["M2"], 2), _mm(r["V2"], 2), "预制主梁截面"],
        ["桥面系 q3（CS4）", _mm(r["M3"], 2), _mm(r["V3"], 2),
         "组合截面（主梁 %.2f %% / 板 %.2f %%）" % (g["k1"] * 100.0, g["k2"] * 100.0)],
        ["合计", _mm(r["M_total"], 2), _mm(r["V_total"], 2), "—"],
    ]
    return rows


def share_rows(model):
    """联合截面内力分配表的行。"""
    r = results(model)
    return [
        ["预制主梁", _mm(r["M_pre"], 2), _mm(r["M3"] * r["g"]["k1"], 2),
         _mm(r["M_beam"], 2)],
        ["现浇桥面板", "—", _mm(r["M_slab"], 2), _mm(r["M_slab"], 2)],
        ["合计", _mm(r["M_pre"], 2), _mm(r["M3"], 2), _mm(r["M_total"], 2)],
    ]


# ----------------------------------------------------------------------
# 联合截面（*CPOSECT4CS）部件定义
# ----------------------------------------------------------------------
def parts(model):
    """两个施工部件（= *CPOSECT4CS 两行）。CZ 相对「基准点 = 单元线」。

    基准点：单元线（= 节点线）取在组合截面形心高度（z = 0），
    所以主梁 Part 的 CZ = y1 - yb = -0.3125 m、桥面板 Part 的 CZ = y2 - yb = +0.3125 m，
    按面积加权 ΣA·CZ = 0 —— 和 MIDAS 真跑过的施工阶段样本（work/e2e/施工阶段.mct）一致。
    """
    g = geom(model)
    comp = comp_stage_of(model)
    age = g["age"]
    bw, hw, bs, ts = g["bw"], g["hw"], g["bs"], g["ts"]
    return [
        {   # 位置 1：预制主梁，材料类型 ELEM（用建立单元时的材料），CS1 随单元激活
            "no": 1, "name": "预制主梁", "mtype": "ELEM", "mat": "", "cstage": "", "age": 0.0,
            "cz": g["y1"] - g["yb"], "cy": 0.0,
            "A": g["A1"], "Iyy": g["I1"], "Izz": hw * bw ** 3 / 12.0,
            "b": bw, "h": hw, "h_th": g["h1"],
            "kind": "beam",
        },
        {   # 位置 2：现浇桥面板，材料类型 MATL（材料 1 = C50），CS3 龄期 20 d 开始参与受力
            "no": 2, "name": "现浇桥面板", "mtype": "MATL", "mat": 1, "cstage": comp,
            "age": age, "cz": g["y2"] - g["yb"], "cy": 0.0,
            "A": g["A2"], "Iyy": g["I2"], "Izz": ts * bs ** 3 / 12.0,
            "b": bs, "h": ts, "h_th": g["h2"],
            "kind": "slab",
        },
    ]


def cposect_row(part):
    """一行 *CPOSECT4CS 数据（严格 37 个字段，逐字段照真跑过的样本）。"""
    a = part["A"]
    iyy = part["Iyy"]
    izz = part["Izz"]
    b, h = part["b"], part["h"]
    row = [
        part["no"], part["mtype"], part["mat"], part["cstage"], part["age"],
        1, 1, 1, 1, 1, 1, 1,                       # 7 个截面特性缩放系数
        part["cy"], part["cz"],                    # CY / CZ：部件形心到基准点的偏距
        a, 5.0 * a / 6.0, 5.0 * a / 6.0,           # AREA, ASy, ASz
        iyy + izz, iyy, izz,                       # Ixx, Iyy, Izz
        b / 2.0, b / 2.0, h / 2.0, h / 2.0, 0, 0,  # CyP, CyM, CzP, CzM, Qyb, Qzb
        -b / 2.0, b / 2.0, b / 2.0, -b / 2.0,      # Y1..Y4
        -h / 2.0, -h / 2.0, h / 2.0, h / 2.0,      # Z1..Z4
        part["h_th"], 0, 0,                        # h 理论厚度, v/s, 刚度调整
    ]
    return "       " + ", ".join(_fmt_cs(v) for v in row)


def cposect_lines(model):
    """*CPOSECT4CS 整个命令块（含 SEC= 行）。"""
    names = stage_names(model)
    first = names[0] if names else "CS1"
    lines = ["*CPOSECT4CS    ; Composite Section for Construction Stage",
             "   SEC=1, %s, USER, NO" % first]
    for p in parts(model):
        lines.append(cposect_row(p))
    return lines


# ----------------------------------------------------------------------
# 虚拟截面（*SECT-PSCVALUE）：组合 T 形
# ----------------------------------------------------------------------
def t_outline(model):
    """T 形外轮廓（逆时针、顶点不重复首点），坐标以梁底为 0、向上为正、横向居中。"""
    g = geom(model)
    bw, hw, bs = g["bw"], g["hw"], g["bs"]
    hb = min(bw, bs) / 2.0
    hs = bs / 2.0
    return [(-hb, 0.0), (hb, 0.0), (hb, hw), (hs, hw),
            (hs, hw + g["ts"]), (-hs, hw + g["ts"]), (-hs, hw), (-hb, hw)]


def pscvalue_lines(model, name="COMP_T"):
    """*SECT-PSCVALUE（虚拟截面 = 组合 T 形）的数据行，逐字段照真跑过的样本。"""
    g = geom(model)
    a = g["A"]
    cy, cz = 0.0, g["yb"]
    cyp = g["bs"] / 2.0
    cym = g["bs"] / 2.0
    czp = g["H"] - g["yb"]
    czm = g["yb"]
    lines = [
        " SECT=   1, PSC       , %-18s, CC, 0, 0, 0, 0, 0, 0, YES, NO, VALU, NO, NO, "
        % _ascii(name),
        "       %s, %s, %s, %s, %s, %s"
        % (_num(a), _num(5.0 * a / 6.0), _num(5.0 * a / 6.0),
           _num(g["Ixx"]), _num(g["I"]), _num(g["Izz"])),
        "       %s, %s, %s, %s, %s, %s, 0, 0, %s, %s"
        % (_num(cyp), _num(cym), _num(czp), _num(czm),
           _num(a * czp / 8.0), _num(a * cyp / 8.0), _num(cy), _num(cz)),
        "       %s, %s, %s, %s, %s, %s, %s, %s"
        % (_num(-cym), _num(cyp), _num(cyp), _num(-cym),
           _num(-czm), _num(-czm), _num(czp), _num(czp)),
        "       0.3, 0.2, 0.1, 0.1",
        "       YES, 0, 0, YES, , YES, , YES, , 0, YES, , YES, , YES, ",
    ]
    pts = t_outline(model)
    lines.append("       OPOLY=" + ", ".join("%s, %s" % (_fmt_pt(x), _fmt_pt(z))
                                            for (x, z) in pts))
    return lines


# ----------------------------------------------------------------------
# MCT 生成
# ----------------------------------------------------------------------
def _beamload_line(elem, q, group):
    """一行梁单元均布荷载（-Z 方向）。字段顺序照梁桥/拱桥流程已验证的写法。"""
    return ("     %d, BEAM   , UNILOAD, GZ, NO , NO, LY, , , , 0, %s, 1, %s, 0, 0, 0, 0, %s, NO, 0, 0, NO, "
            % (elem, _num(-q), _num(-q), group))


def _ndof(model):
    g = geom(model)
    return max(1, int(g["n"]))


def build_mct(model):
    """生成 MIDAS Civil NX 可导入的 MCT 文本（写文件时用 GBK 编码）。"""
    g = geom(model)
    proj = model.get("project") or {}
    name = proj.get("name") or "预制T梁_20m_施工阶段"
    n_elem = _ndof(model)
    n_node = n_elem + 1
    L = g["L"]
    stages = model.get("stages") or []
    names = stage_names(model)
    comp = comp_stage_of(model)
    wet = wet_stage_of(model)
    q1, q2 = q1_value(model), q2_value(model)
    q3 = q3_value(model)
    r = results(model)
    E_c = (CONC.get(model_conc(model)) or CONC["C50"])[0]
    gamma = g["gamma"]

    Lw = []

    def add(s=""):
        Lw.append(s)

    for ln in [
        "; ================================================================",
        "; MIDAS Civil NX — MCT（预制 T 梁 + 现浇桥面板 · 施工阶段联合截面）",
        "; 生成时间：" + datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "; 项目：%s" % name,
        "; 算例：20 m 简支 T 梁（第 05 讲 / 习题 4）",
        "; 流程：CS1 架设预制主梁（自重）→ CS2 现浇桥面板（湿重）→ CS3 桥面板硬化（龄期 %.0f d，"
        % g["age"],
        ";        联合截面生效）→ CS4 施工桥面系（二期恒载）",
        "; 截面：预制梁肋 %.3f×%.3f m（A=%.4f m2、形心距梁底 %.4f m、I=%.6f m4）"
        % (g["bw"], g["hw"], g["A1"], g["y1"], g["I1"]),
        ";       现浇桥面板 %.3f×%.3f m（A=%.4f m2、形心距梁底 %.4f m、I=%.6f m4）"
        % (g["bs"], g["ts"], g["A2"], g["y2"], g["I2"]),
        ";       组合截面 A=%.4f m2、形心距梁底 %.4f m、I=%.6f m4；刚度分担 主梁 %.2f %% / 板 %.2f %%"
        % (g["A"], g["yb"], g["I"], g["k1"] * 100.0, g["k2"] * 100.0),
        "; 单元线（节点线）位置：取在**组合截面形心高度**（x 轴为桥轴、z 竖直向上，节点 z = 0）。",
        ";       以梁底为 0 起算：梁底 z = %.4f、主梁顶 / 板底 z = %.4f、板顶 z = %.4f。"
        % (g["z_beam_bot"], g["z_beam_top"], g["z_slab_top"]),
        "; 联合截面基准点 = 单元线：主梁 Part CZ = %.4f m、桥面板 Part CZ = %+.4f m"
        % (g["y1"] - g["yb"], g["y2"] - g["yb"]),
        ";       （按面积加权 ΣA·CZ = 0，与 MIDAS 真跑过的 C:\\Midas\\work\\e2e\\施工阶段.mct 一致）",
        "; 虚拟截面（单元上赋的 *SECTION）= 组合 T 形，形心放在单元线上（Center: z = %.4f）。"
        % g["yb"],
        "; 荷载：q1 = %.2f kN/m（主梁自重）、q2 = %.2f kN/m（现浇板湿重）、q3 = %.2f kN/m（二期）"
        % (q1, q2, q3),
        ";       三个荷载都用梁单元均布荷载显式施加（不用 *SELFWEIGHT：单元上是组合 T 形虚拟截面",
        ";       A=%.2f m2，*SELFWEIGHT 会算出 %.1f kN/m，和题解的 %.1f kN/m 对不上）。"
        % (g["A"], g["A"] * gamma, q1),
        "; 施工阶段：%s" % " → ".join("%s(%s d)" % (s.get("name"), s.get("days")) for s in stages),
        "; 结果（弹性、与题解逐项对应）：跨中 M=%.2f kN·m，主梁承担 %.2f、桥面板 %.2f；"
        % (r["M_total"], r["M_beam"], r["M_slab"]),
        ";       应力（受拉为正）主梁底 %+.2f、主梁顶 %+.2f、板顶 %+.2f MPa"
        % (r["stress"]["total"]["beam_bot"], r["stress"]["total"]["beam_top"],
           r["stress"]["total"]["slab_top"]),
        "; ================================================================",
    ]:
        add(ln)

    add("*VERSION")
    add("   9.5.0")
    add("*UNIT    ; Unit System")
    add("   KN, M, KJ, C")
    add("*STRUCTYPE    ; Structure Type")
    add("   0, 1, 1, NO, NO, 9.806, 0, NO, NO, NO")

    # ---- 节点 / 单元 ----
    add("*NODE    ; Nodes")
    for i in range(n_node):
        x = L * i / float(n_elem)
        add("   %d, %s, 0, 0" % (i + 1, _num(x, 10)))
    add("*ELEMENT    ; Elements")
    for i in range(1, n_elem + 1):
        add("   %d, BEAM, 1, 1, %d, %d, 0, 0" % (i, i, i + 1))

    # ---- 组 ----
    add("*GROUP    ; Group")
    add("   EL_ALL, 1to%d, 1to%d, 0" % (n_node, n_elem))
    add("*BNDR-GROUP    ; Boundary Group")
    add("   BC, 0")
    add("*LOAD-GROUP    ; Load Group")
    add("   SW")
    add("   WET1")
    if q3 > 0:
        add("   DL2")

    # ---- 材料（主梁和桥面板都是同一种混凝土：材料 1）----
    add("*MATERIAL    ; Material")
    add("   1, CONC , %s, 0, 0, , C, NO, 0.05, 2, %.4e, 0.2, 1.0e-05, %s, 0"
        % (model_conc(model), E_c, _num(gamma)))

    # ---- 虚拟截面（PSC 一般截面：组合 T 形）----
    add("*SECT-PSCVALUE    ; PSC Value / General Section（虚拟截面 = 组合 T 形）")
    for ln in pscvalue_lines(model):
        add(ln)
    add("")

    # ---- 荷载工况 ----
    add("*STLDCASE    ; Static Load Cases")
    add("   SW, CS, \"Self Weight (precast girder)\"")
    add("   WET1, CS, \"Wet Concrete (deck slab)\"")
    if q3 > 0:
        add("   DL2, CS, \"Second Dead Load\"")

    # ---- 约束：简支（一端固定约束、一端竖向约束）----
    add("*CONSTRAINT    ; Supports")
    add("   1, 111100, BC")
    add("   %d, 011100, BC" % n_node)

    # ---- 荷载：三个梁单元均布荷载 ----
    add("*USE-STLD, SW")
    add("*BEAMLOAD    ; Element Beam Loads（主梁自重 q1）")
    for e in range(1, n_elem + 1):
        add(_beamload_line(e, q1, "SW"))
    add("*USE-STLD, WET1")
    add("*BEAMLOAD    ; Element Beam Loads（现浇桥面板湿重 q2，加在预制主梁上）")
    for e in range(1, n_elem + 1):
        add(_beamload_line(e, q2, "WET1"))
    if q3 > 0:
        add("*USE-STLD, DL2")
        add("*BEAMLOAD    ; Element Beam Loads（二期恒载 q3，由组合截面承担）")
        for e in range(1, n_elem + 1):
            add(_beamload_line(e, q3, "DL2"))

    # ---- 施工阶段 ----
    add("*STAGE    ; Define Construction Stage")
    for i, st in enumerate(stages, start=1):
        nm = names[i - 1]
        days = _num(st.get("days", 7), 6)
        add("  NAME=%s, %s, YES, YES, NO, 5" % (nm, days))
        if i == 1:
            add("  AELEM=EL_ALL, %s" % days)
            add("  ABNDR=BC, DEFORMED")
            add("  ALOAD=SW, FIRST")
        if nm == wet:
            add("  ALOAD=WET1, FIRST")
        if q3 > 0 and i == len(stages):
            add("  ALOAD=DL2, FIRST")

    # ---- 施工阶段联合截面（*CPOSECT4CS，每部件 37 个字段）----
    for ln in cposect_lines(model):
        add(ln)
    add("")
    # 说明：湿重不卸除（它就是桥面板硬化后的恒载）
    if wet and comp:
        add("; 注：湿重 WET1 在 %s 施加后一直保留 —— 它就是桥面板硬化后的自重，不是临时荷载；"
            % wet)
        add(";     题解里 CS3 只让桥面板 Part 参与刚度，没有加/卸荷载，总跨中弯矩才是 2000 kN·m。")

    # ---- 施工阶段分析控制 ----
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
# 数据表 / 结果表（人可读文本，写文件用）
# ----------------------------------------------------------------------
def _table(rows, headers, widths=None):
    rows = [[str(c) for c in r] for r in (rows or [])]
    cols = [headers] + rows
    n = max(len(r) for r in cols)
    if not widths:
        widths = [max(len(r[i]) if i < len(r) else 0 for r in cols) for i in range(n)]
    out = []
    for k, r in enumerate(cols):
        cells = []
        for i in range(n):
            v = r[i] if i < len(r) else ""
            cells.append(v.ljust(widths[i]))
        out.append("  ".join(cells).rstrip())
        if k == 0:
            out.append("  ".join("-" * widths[i] for i in range(n)))
    return "\n".join(out)


def data_report(model):
    """人可读的数据表（写 <名字>_数据表.txt）。"""
    g = geom(model)
    r = results(model)
    proj = model.get("project") or {}
    names = stage_names(model)
    comp = comp_stage_of(model)
    wet = wet_stage_of(model)
    L = []
    L.append("=" * 74)
    L.append("预制 T 梁 + 现浇桥面板（施工阶段联合截面）—— 数据表")
    L.append("项目：%s" % (proj.get("name") or ""))
    L.append("生成时间：%s" % datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    L.append("=" * 74)
    L.append("")
    L.append("一、跨径与截面（单位 m）")
    L.append("  计算跨径 L = %.3f m    单元数 = %d（节点 %d 个）"
             % (g["L"], g["n"], g["n"] + 1))
    L.append("  预制主梁梁肋 %.3f（宽）× %.3f（高）   现浇桥面板 %.3f（宽）× %.3f（厚）"
             % (g["bw"], g["hw"], g["bs"], g["ts"]))
    L.append("  材料 %s   容重 γ = %.2f kN/m3   二期恒载 q3 = %.2f kN/m"
             % (g["conc"], g["gamma"], g["q3"]))
    L.append("")
    L.append("二、截面几何特性")
    L.append(_table(section_rows(model),
                    ["部位", "尺寸 (m)", "面积 A (m2)", "形心距梁底 (m)", "自身 I (m4)"]))
    L.append("")
    L.append("  组合截面中各部件绕组合形心的惯性矩（决定联合后弯矩分担）：")
    L.append(_table(
        [["预制主梁", "%.6f" % g["I1c"], "%.2f %%" % (g["k1"] * 100.0)],
         ["现浇桥面板", "%.6f" % g["I2c"], "%.2f %%" % (g["k2"] * 100.0)],
         ["合计", "%.6f" % g["I"], "100 %"]],
        ["部件", "I' = I + A·d^2 (m4)", "分担比例"]))
    L.append("")
    L.append("  理论厚度（收缩徐变用）h = 2A/u：主梁 h = %.4f m（u = %.3f m）、"
             "桥面板 h = %.4f m（u = %.3f m）" % (g["h1"], g["u1"], g["h2"], g["u2"]))
    L.append("")
    L.append("三、施工阶段与荷载")
    rows = []
    for i, st in enumerate(model.get("stages") or []):
        nm = str(st.get("name") or "")
        notes = []
        if i == 0:
            notes.append("激活主梁单元 + 边界 + 主梁自重 q1 = %.2f kN/m" % r["q1"])
        if nm == wet:
            notes.append("现浇桥面板湿重 q2 = %.2f kN/m（加在预制主梁上）" % r["q2"])
        if nm == comp:
            notes.append("桥面板硬化（龄期 %.0f d），联合截面生效（Part 2 开始参与刚度）"
                         % g["age"])
        if i == len(names) - 1:
            notes.append("二期恒载 q3 = %.2f kN/m（由组合截面承担）" % r["q3"])
        rows.append([nm, "%s" % st.get("days", ""), st.get("desc") or "", "；".join(notes)])
    L.append(_table(rows, ["阶段", "天数 (d)", "内容", "这一步做什么"]))
    L.append("")
    L.append("四、施工阶段联合截面（*CPOSECT4CS）的定义")
    L.append("  联合形式：USER（用户），截面编号 1，激活施工阶段 %s" % (names[0] if names else "CS1"))
    L.append("  单元线（节点线）位置：取在组合截面形心高度（节点 z = 0；以梁底为 0 起算：")
    L.append("      梁底 z = %.4f m、主梁顶/板底 z = %.4f m、板顶 z = %.4f m）"
             % (g["z_beam_bot"], g["z_beam_top"], g["z_slab_top"]))
    L.append("  基准点 = 单元线；Cy / Cz = 部件形心到基准点的偏距：")
    rows = []
    for p in parts(model):
        rows.append([str(p["no"]), p["name"], p["mtype"],
                     "—" if p["cstage"] == "" else p["cstage"],
                     "%.0f" % p["age"], "%.4f" % p["cy"], "%+.4f" % p["cz"],
                     "%.4f" % p["A"], "%.6f" % p["Iyy"], "%.4f" % p["h_th"]])
    L.append(_table(rows, ["位置", "部件", "材料类型", "叠合阶段", "材龄(d)", "CY(m)",
                           "CZ(m)", "A(m2)", "Iyy(m4)", "h(m)"]))
    L.append("  说明：位置 1 的激活阶段写在 SEC= 行的「%s」；行内 CSTAGE/AGE 留空/0"
             % (names[0] if names else "CS1"))
    L.append("        （MIDAS 真跑过的样本就是这样写的）。按面积加权 ΣA·CZ = 0，"
             "即单元线通过组合截面形心。")
    L.append("")
    L.append("五、荷载与湿重的处理")
    L.append(_table([[it[0], "%.2f" % it[1], it[2], it[3], it[4]] for it in load_items(model)],
                    ["荷载组", "q (kN/m)", "施加阶段", "含义", "由谁承担"]))
    L.append("  注：湿重（桥面板湿混凝土 10 kN/m）在 %s 加在预制主梁上后**一直保留** ——"
             % (wet or "CS2"))
    L.append("      它就是桥面板硬化后的自重；%s 只在联合截面里让桥面板 Part 开始参与刚度。"
             % (comp or "CS3"))
    L.append("      三个荷载都用梁单元均布荷载显式给 q，不用 *SELFWEIGHT：单元上是组合 T 形")
    L.append("      虚拟截面（A=%.2f m2），*SELFWEIGHT 会算出 %.1f kN/m，和题解 %.1f kN/m 对不上。"
             % (g["A"], g["A"] * g["gamma"], r["q1"]))
    L.append("")
    L.append("六、MCT 文件说明")
    L.append("  <名字>.mct：MIDAS Civil NX → 文件 → 导入 → MCT 命令文件。")
    L.append("  %d 个单元（BEAM）、%d 个节点；*STAGE %d 个阶段、*CPOSECT4CS 2 个部件（每行 37 字段）；"
             % (g["n"], g["n"] + 1, len(model.get("stages") or [])))
    L.append("  *SECT-PSCVALUE 是组合 T 形虚拟截面（A=%.4f m2、Iyy=%.6f m4，形心在单元线上）。"
             % (g["A"], g["I"]))
    L.append("  导入后：结果 → 结果表格 → 施工阶段联合截面，逐个 Part 对照 <名字>_计算结果.txt。")
    L.append("=" * 74)
    return "\n".join(L) + "\n"


def result_report(model):
    """人可读的计算结果表（写 <名字>_计算结果.txt）。"""
    r = results(model)
    g = r["g"]
    names = stage_names(model)
    comp = comp_stage_of(model)
    wet = wet_stage_of(model)
    L = []
    L.append("=" * 74)
    L.append("预制 T 梁 + 现浇桥面板（施工阶段联合截面）—— 计算结果")
    L.append("计算假定：弹性、素混凝土截面（题解口径）；跨中弯矩 M = q·L^2/8、支点剪力 V = q·L/2。")
    L.append("应力符号：正 = 拉，负 = 压；单位 MPa。")
    L.append("=" * 74)
    L.append("")
    L.append("一、截面几何特性")
    L.append("  预制主梁：A1 = %.4f m2   形心距梁底 y1 = %.4f m   I1 = %.6f m4"
             % (g["A1"], g["y1"], g["I1"]))
    L.append("  现浇桥面板：A2 = %.4f m2   形心距梁底 y2 = %.4f m   I2 = %.6f m4"
             % (g["A2"], g["y2"], g["I2"]))
    L.append("  组合截面：A = %.4f m2   形心距梁底 yb = %.4f m   I = %.6f m4"
             % (g["A"], g["yb"], g["I"]))
    L.append("  联合后弯矩分担：主梁 I1' = %.6f m4（%.2f %%）、板 I2' = %.6f m4（%.2f %%）"
             % (g["I1c"], g["k1"] * 100.0, g["I2c"], g["k2"] * 100.0))
    L.append("")
    L.append("二、各阶段荷载与跨中内力（L = %.3f m）" % g["L"])
    L.append(_table(force_rows(model), ["荷载 / 阶段", "跨中弯矩 (kN·m)",
                                        "支点剪力 (kN)", "承担截面"]))
    L.append("")
    L.append("三、联合截面内力分配（跨中）")
    L.append(_table(share_rows(model), ["部件", "阶段 1+2 承担 (kN·m)",
                                        "组合阶段分担 (kN·m)", "合计弯矩 (kN·m)"]))
    L.append("  即：跨中总弯矩 %.1f kN·m 中，预制主梁 %.1f、桥面板 %.1f kN·m；"
             % (r["M_total"], r["M_beam"], r["M_slab"]))
    L.append("      阶段 1+2 的 %.1f kN·m 全部由预制主梁承担（那时还没有组合截面）。"
             % r["M_pre"])
    L.append("")
    L.append("四、跨中截面应力（受拉为正，MPa）")
    L.append(_table(stress_rows(model),
                    ["位置", "阶段 1+2（仅主梁）", "阶段 3（组合截面）", "叠加",
                     "一次成型对比"]))
    L.append("  一次成型 = 把 %.1f kN·m 全部按组合截面算（同一截面、同一形心）。"
             % r["M_total"])
    L.append("")
    L.append("五、与一次成型的对比")
    t, o = r["stress"]["total"], r["stress"]["one_step"]
    L.append(_table([
        ["主梁底", "%+.2f" % o["beam_bot"], "%+.2f" % t["beam_bot"],
         "联合截面：主梁底多了预制阶段承受的弯矩，拉应力更大"],
        ["主梁顶", "%+.2f" % o["beam_top"], "%+.2f" % t["beam_top"],
         "主梁先受力、后组合，压应力更大"],
        ["板顶", "%+.2f" % o["slab_top"], "%+.2f" % t["slab_top"],
         "板只承担后加荷载，压应力比一次成型小"],
    ], ["位置", "一次成型 (MPa)", "施工阶段联合截面 (MPa)", "差异说明"]))
    L.append("")
    L.append("六、结论")
    L.append("  ① 一次成型时 %.0f kN·m 全部按组合截面分配，主梁底只有 %.2f MPa；"
             % (r["M_total"], o["beam_bot"]))
    L.append("  ② 实际分阶段施工时主梁先承担了 %.0f kN·m（自重 + 湿重），组合以后才分担后加荷载，"
             % r["M_pre"])
    L.append("     所以主梁底应力增大到 %.2f MPa、板顶应力反而减小到 %.2f MPa。"
             % (t["beam_bot"], t["slab_top"]))
    L.append("  ③ 施工阶段（%s 加湿重、%s 龄期 %.0f d 联合）和一次成型差这么多，"
             % (wet or "CS2", comp or "CS3", g["age"]))
    L.append("     就是「施工阶段联合截面」与「一次成桥」结果不同的根本原因。")
    L.append("")
    L.append("=" * 74)
    return "\n".join(L) + "\n"


# ----------------------------------------------------------------------
# 默认模型 / 校验
# ----------------------------------------------------------------------
def default_model():
    """默认 = 第 05 讲算例 / 习题 4：20 m 简支 T 梁，L=20 m、板 1.6×0.25、肋 0.4×1.0、C50。"""
    return {
        "flow": CODE,
        "project": {"name": "预制T梁_20m_施工阶段", "outdir": "",
                    "force": "KN", "dist": "M", "styp": "0"},
        "geom": {
            "L": "20",          # 计算跨径 (m)
            "bs": "1.6",        # 桥面板宽 (m)
            "ts": "0.25",       # 桥面板厚 (m)
            "bw": "0.40",       # 梁肋宽 (m)
            "hw": "1.0",        # 梁肋高 (m)
            "n": "20",          # 单元数
            "conc": "C50",      # 混凝土等级
            "gamma": "25",      # 容重 kN/m3
            "q3": "20",         # 二期恒载 kN/m
        },
        "stages": [
            {"name": "CS1", "days": "7", "desc": "架设预制主梁（简支），加主梁自重"},
            {"name": "CS2", "days": "1", "desc": "现浇桥面板，湿混凝土重量加在预制主梁上"},
            {"name": "CS3", "days": "20", "desc": "桥面板硬化（龄期 20 d），联合截面形成"},
            {"name": "CS4", "days": "7", "desc": "施工桥面系（二期恒载）"},
        ],
        "comp_stage": "CS3",   # 桥面板 Part 从哪个阶段开始参与受力
        "slab_age": "20",      # 桥面板硬化龄期（天）
    }


def reset_model():
    return default_model()


# ---- 参数校验 ----
def geom_issues(model):
    out = []
    p = model.get("geom") or {}
    try:
        L = float(p.get("L"))
        if L <= 0:
            out.append("计算跨径 L 要大于 0（现在 %s）。" % p.get("L"))
        elif L > 500:
            out.append("计算跨径 L=%g m 太大，确认一下单位（应为 m）。" % L)
    except (TypeError, ValueError):
        out.append("计算跨径 L 要填数字。")
    for key, name in (("bw", "梁肋宽"), ("hw", "梁肋高"), ("bs", "板宽"), ("ts", "板厚")):
        try:
            v = float(p.get(key))
            if v <= 0:
                out.append("%s要大于 0（现在 %s）。" % (name, p.get(key)))
        except (TypeError, ValueError):
            out.append("%s要填数字。" % name)
    try:
        bw = float(p.get("bw"))
        bs = float(p.get("bs"))
        if bs < bw:
            out.append("板宽（%.3f m）小于梁肋宽（%.3f m），T 形截面不成形。" % (bs, bw))
        elif bs > 30:
            out.append("板宽 %.3f m 太大（单梁翼缘一般 1~3 m），确认一下单位。" % bs)
    except (TypeError, ValueError):
        pass
    try:
        hw = float(p.get("hw"))
        ts = float(p.get("ts"))
        if hw > 0 and ts > 0 and ts >= hw:
            out.append("板厚 %.3f m 不小于梁肋高 %.3f m，确认一下（T 梁的板一般 0.15~0.4 m）。"
                       % (ts, hw))
    except (TypeError, ValueError):
        pass
    try:
        n = int(float(p.get("n")))
        if n < 4 or n > 200:
            out.append("单元数建议 4~200（现在 %d）。" % n)
    except (TypeError, ValueError):
        out.append("单元数要填整数。")
    if str(p.get("conc") or "") not in CONC:
        out.append("混凝土等级「%s」不认识，可选：%s。" % (p.get("conc"), "、".join(CONC_LIST)))
    try:
        if float(p.get("gamma")) <= 0:
            out.append("容重 γ 要大于 0（混凝土一般 24~26 kN/m3）。")
    except (TypeError, ValueError):
        out.append("容重 γ 要填数字。")
    try:
        if float(p.get("q3")) < 0:
            out.append("二期恒载 q3 不能是负数（填 0 表示不加）。")
    except (TypeError, ValueError):
        out.append("二期恒载 q3 要填数字。")
    return out


def stage_issues(model):
    out = []
    stages = model.get("stages") or []
    names = stage_names(model)
    if len(stages) < 3:
        out.append("至少要 3 个施工阶段（架设主梁 → 浇板 → 硬化 → 二期）。")
        return out
    if len(stages) > 10:
        out.append("施工阶段太多（%d 个），建议 3~8 个。" % len(stages))
    for i, st in enumerate(stages):
        nm = str(st.get("name") or "").strip()
        if not nm:
            out.append("第 %d 个阶段的名字不能空。" % (i + 1))
        elif nm != _ascii(nm):
            out.append("阶段名「%s」含中文/全角字符，MIDAS 里建议用 CS1、CS2 这种。" % nm)
        try:
            d = float(st.get("days"))
            if d <= 0:
                out.append("阶段「%s」的天数要大于 0（现在 %s）。" % (nm or i + 1, st.get("days")))
            elif d > 36500:
                out.append("阶段「%s」的天数 %g 太大。" % (nm, d))
        except (TypeError, ValueError):
            out.append("阶段「%s」的天数要填数字。" % (nm or i + 1))
    if len(set(names)) != len(names):
        out.append("施工阶段名字有重复：%s。" % "、".join(names))
    raw_comp = str(model.get("comp_stage") or "").strip()
    if raw_comp and raw_comp not in names:
        out.append("「联合截面生效阶段」填的「%s」不在阶段表里。" % raw_comp)
    comp = comp_stage_of(model)
    idx = stage_index(model, comp)
    if idx < 0:
        out.append("「联合截面生效阶段」不在阶段表里。")
    elif idx == 0:
        out.append("联合截面不能在第 1 个阶段就生效（主梁还没架设完）。")
    elif idx >= len(stages) - 1:
        out.append("联合截面生效阶段要早于最后一个阶段（二期恒载要加在组合截面上）。")
    try:
        age = float(model.get("slab_age"))
        if age <= 0:
            out.append("桥面板材龄要大于 0（题解用 20 d；填 0 相当于湿重，不是硬化）。")
        elif age > 10000:
            out.append("桥面板材龄 %g d 太大。" % age)
    except (TypeError, ValueError):
        out.append("桥面板材龄要填数字。")
    return out


def issues(model, sid=""):
    """这一步的问题列表；空 = 能进下一步。"""
    if sid in ("", "tbeam_geom"):
        return geom_issues(model)
    if sid == "tbeam_stages":
        return stage_issues(model)
    return []


def all_issues(model):
    out = []
    for sid in STEP_IDS:
        for msg in issues(model, sid):
            if msg not in out:
                out.append(msg)
    return out


# ----------------------------------------------------------------------
# 给界面/测试用的小摘要
# ----------------------------------------------------------------------
def summary(model):
    """界面第 3 步要显示的一行摘要。"""
    r = results(model)
    g = r["g"]
    t = r["stress"]["total"]
    return ("跨中 M = %.1f kN·m（主梁 %.1f / 桥面板 %.1f）    "
            "主梁底 %+.2f MPa   主梁顶 %+.2f MPa   板顶 %+.2f MPa"
            % (r["M_total"], r["M_beam"], r["M_slab"],
               t["beam_bot"], t["beam_top"], t["slab_top"]))


def stage_plan_lines(model):
    """界面第 2 步：把阶段表翻译成人能看懂的计划（逐行文本）。"""
    r = results(model)
    names = stage_names(model)
    comp = comp_stage_of(model)
    wet = wet_stage_of(model)
    out = []
    for i, st in enumerate(model.get("stages") or []):
        nm = str(st.get("name") or "")
        notes = []
        if i == 0:
            notes.append("激活主梁单元 + 边界 + 主梁自重 q1 = %.2f kN/m" % r["q1"])
        if nm == wet:
            notes.append("加现浇板湿重 q2 = %.2f kN/m（按均布荷载加在预制主梁上）" % r["q2"])
        if nm == comp:
            notes.append("桥面板硬化（材龄 %.0f d），*CPOSECT4CS 里 Part 2 开始参与受力"
                         "（联合截面生效，不加减荷载）" % r["g"]["age"])
        if i == len(names) - 1:
            notes.append("加二期恒载 q3 = %.2f kN/m（由组合截面承担）" % r["q3"])
        out.append("  %-6s %-3s d  %-26s  %s"
                   % (nm, st.get("days", ""), st.get("desc") or "", "；".join(notes)))
    return out
