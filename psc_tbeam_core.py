# -*- coding: utf-8 -*-
r"""装配式预应力混凝土简支 T 梁桥（整桥 · 钢束 · 全流程）—— 纯计算 + MCT 生成。

与 tbeam_core 的区别：tbeam_core 是「单根预制 T 梁 + 现浇桥面板 · 施工阶段联合截面」
（课件第 04/05 讲的例题），本模块是**整桥**：
    * 5 片 T 梁（梁格）+ 5 道横隔板；
    * 后张法预应力钢束（*TDN-PROPERTY / *TDN-PROFILE / *TDN-PRESTRESS）；
    * 自重、二期恒载；
    * 预留：移动荷载（公路-I 级）、施工阶段、温度梯度。

MCT 写法全部对照 MIDAS Civil NX 自己导出的真实样本：
    C:\Midas\work\samples\FCM.mct（钢束、施工阶段、荷载组）
    C:\Midas\work\samples\CableStayed.mct（静力荷载工况类型）
    以及向导里已经实测导入过的 *SECT-PSCVALUE 写法（tbeam_core）。"""

import copy
import math

import wiz_common as wc

CODE = "psc_tbeam"
LABEL = "装配式预应力混凝土简支T梁桥（整桥·钢束·全流程）"

# ======================================================================
# 默认参数（全部存字符串，跟其它流程保持一致）
# ======================================================================
DEFAULT = {
    "flow": CODE,
    "project": {"name": "南疆某跨渠30m装配式预应力混凝土简支T梁桥",
                "outdir": "", "force": "KN", "dist": "M", "method": "梁格法"},
    "geom": {
        "Lstd": "30", "Lcalc": "29.4", "Lfull": "29.96",
        "ng": "5", "sg": "2.2", "deck": "9.0", "walk": "1.0",
        "ne": "16",
        "h": "2.0", "bf": "2.2", "tf": "0.20", "tw": "0.20",
        "bm": "0.60", "hm": "0.40",
        "ndia": "5", "tdia": "0.16", "hdia": "1.40",
    },
    "mat": {"conc": "C50", "Ec": "3.45e7", "nu": "0.2", "gam": "25", "alpha": "1.0e-5",
            "Es": "1.95e8", "nus": "0.3", "gams": "78.5",
            "fpk": "1.86e6", "fpd": "1.569e6"},
    "tendon": {"nb": "5", "ns": "9", "a1": "1.4e-4", "scon": "1.395e6",
               "mu": "0.2", "k": "0.0015", "slip": "0.006", "rc": "45",
               "z1": "0.10", "z2": "0.28", "z3": "0.55", "z4": "0.95", "z5": "1.30"},
    "load": {"q2c": "4.32", "q2e": "7.82",
             "mv": "1", "lanes": "2", "lane_w": "3.5",
             "qk": "10.5", "pk": "262.4"},
    "stages": {
        "on": "0",
        "rows": [
            {"name": "CS1", "days": "7", "desc": "预制主梁、张拉钢束"},
            {"name": "CS2", "days": "1", "desc": "吊装就位"},
            {"name": "CS3", "days": "7", "desc": "现浇湿接缝与横隔板接头"},
            {"name": "CS4", "days": "7", "desc": "桥面铺装及二期恒载"},
            {"name": "CS5", "days": "3650", "desc": "收缩徐变（10年）"},
        ],
    },
}


def default_model():
    return copy.deepcopy(DEFAULT)


def reset_model():
    return default_model()


# ======================================================================
# 小工具
# ======================================================================
def _f(m, *keys, default=0.0):
    """按路径取浮点数。_f(m, "geom", "Lcalc")。"""
    cur = m
    for k in keys:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(k)
    try:
        return float(str(cur).strip())
    except (TypeError, ValueError):
        return default


def _s(m, *keys, default=""):
    cur = m
    for k in keys:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(k)
    return default if cur is None else str(cur).strip()


def _num(v, nd=6):
    try:
        f = float(str(v).strip())
    except (TypeError, ValueError):
        return "0"
    if f == int(f) and abs(f) < 1e15:
        return str(int(f))
    return ("%." + str(nd) + "g") % f


def _fmt_cs(v, nd=6):
    """截面特性：6 位有效数字，避免浮点噪声。"""
    try:
        return "%.*g" % (nd, float(v))
    except (TypeError, ValueError):
        return "0"


def _fmt_pt(v, nd=4):
    try:
        return "%.*g" % (nd, float(v))
    except (TypeError, ValueError):
        return "0"


def _ascii(s, fallback="SEC"):
    out = "".join(ch for ch in str(s) if ord(ch) < 128).strip()
    return out or fallback


def _ng(v, low=1, high=200, default=1):
    try:
        n = int(round(float(str(v).strip())))
    except (TypeError, ValueError):
        return default
    return max(low, min(high, n))


# ======================================================================
# 几何与截面
# ======================================================================
def geom(m):
    """把输入换算成数字，并算 T 梁/横隔板的多边形与截面特性。"""
    g = m.get("geom") or {}
    L = _f(m, "geom", "Lcalc", default=29.4)
    ng = _ng(g.get("ng"), 2, 9, 5)
    sg = _f(m, "geom", "sg", default=2.2)
    ne = _ng(g.get("ne"), 2, 60, 16)
    h = _f(m, "geom", "h", default=2.0)
    bf = _f(m, "geom", "bf", default=2.2)
    tf = _f(m, "geom", "tf", default=0.20)
    tw = _f(m, "geom", "tw", default=0.20)
    bm = _f(m, "geom", "bm", default=0.60)
    hm = _f(m, "geom", "hm", default=0.40)
    ndia = _ng(g.get("ndia"), 1, 9, 5)
    tdia = _f(m, "geom", "tdia", default=0.16)
    hdia = _f(m, "geom", "hdia", default=1.40)

    # T 梁外轮廓（以梁底为 z=0，横向居中；逆时针）
    t_poly = _t_polygon(h, bf, tf, tw, bm, hm)
    t_props = wc.compute_section_props([t_poly])
    # 横隔板（矩形，截面坐标 = 局部 y(横桥向=构件厚度) × 局部 z(竖向=高度)）
    d_poly = _rect_polygon(tdia, hdia)
    d_props = wc.compute_section_props([d_poly])

    # 节点横向坐标（梁中心线）
    ys = [(i - (ng - 1) / 2.0) * sg for i in range(ng)]
    # 横隔板位置（沿 x 的第几个节点，0-based）
    rows = []
    if ndia <= 1:
        rows = [ne // 2]
    else:
        for k in range(ndia):
            rows.append(int(round(k * ne / float(ndia - 1))))
    rows = sorted(set(max(0, min(ne, r)) for r in rows))
    return {
        "L": L, "ng": ng, "sg": sg, "ne": ne, "h": h, "bf": bf, "tf": tf, "tw": tw,
        "bm": bm, "hm": hm, "ndia": ndia, "tdia": tdia, "hdia": hdia,
        "dx": L / ne if ne else 0.0,
        "ys": ys,
        "rows": rows,
        "t_poly": t_poly, "t_props": t_props,
        "d_poly": d_poly, "d_props": d_props,
        "yb": (t_props or {}).get("Cz", 0.0),
    }


def _t_polygon(h, bf, tf, tw, bm, hm):
    """T 梁外轮廓（含马蹄），(y, z)，z 从梁底算、横向居中。"""
    z_m = min(hm, h - tf)                 # 马蹄顶
    z_w = h - tf                          # 翼缘底 = 腹板顶
    hb = bm / 2.0
    hw = tw / 2.0
    hf = bf / 2.0
    return [(-hb, 0.0), (hb, 0.0), (hb, z_m), (hw, z_m), (hw, z_w),
            (hf, z_w), (hf, h), (-hf, h), (-hf, z_w), (-hw, z_w),
            (-hw, z_m), (-hb, z_m)]


def _rect_polygon(b, h):
    """矩形截面轮廓（以形心为原点）。"""
    return [(-b / 2.0, -h / 2.0), (b / 2.0, -h / 2.0), (b / 2.0, h / 2.0), (-b / 2.0, h / 2.0)]


def section_summary(m):
    g = geom(m)
    tp = g["t_props"] or {}
    dp = g["d_props"] or {}
    return {
        "A": tp.get("A", 0.0), "Iyy": tp.get("Iyy", 0.0), "Izz": tp.get("Izz", 0.0),
        "Ixx": tp.get("Ixx", 0.0), "yb": tp.get("Cz", 0.0),
        "CyP": tp.get("CyP", 0.0), "CzP": tp.get("CzP", 0.0),
        "dA": dp.get("A", 0.0), "dIyy": dp.get("Iyy", 0.0), "dIzz": dp.get("Izz", 0.0),
    }


# ======================================================================
# 节点 / 单元 / 组
# ======================================================================
def nodes(m):
    g = geom(m)
    out = []
    n_per = g["ne"] + 1
    for ig, y in enumerate(g["ys"], start=1):
        for i in range(n_per):
            x = i * g["dx"]
            out.append(((ig - 1) * n_per + i + 1, x, y, 0.0))
    return out


def elements(m):
    """返回 [(id, type, mat, sec, n1, n2)]：先 5 片纵梁，再横隔板。"""
    g = geom(m)
    n_per = g["ne"] + 1
    out = []
    eid = 1
    # 纵梁
    for ig in range(1, g["ng"] + 1):
        for i in range(g["ne"]):
            n1 = (ig - 1) * n_per + i + 1
            n2 = n1 + 1
            out.append((eid, "BEAM", 1, 1, n1, n2))
            eid += 1
    # 横隔板
    dia_start = eid
    for r in g["rows"]:
        for ig in range(1, g["ng"]):
            n1 = (ig - 1) * n_per + r + 1
            n2 = ig * n_per + r + 1
            out.append((eid, "BEAM", 1, 2, n1, n2))
            eid += 1
    return out, dia_start


def girder_elem_range(m, ig):
    g = geom(m)
    return (ig - 1) * g["ne"] + 1, ig * g["ne"]


def groups(m):
    """结构组：G1..Gn（每片纵梁）、DIA（横隔板）、ALL。"""
    g = geom(m)
    elems, dia_start = elements(m)
    last = elems[-1][0]
    out = []
    for ig in range(1, g["ng"] + 1):
        a, b = girder_elem_range(m, ig)
        out.append(("G%d" % ig, "", "%dto%d" % (a, b)))
    out.append(("DIA", "", "%dto%d" % (dia_start, last)))
    out.append(("ALL", "1to%d" % len(nodes(m)), "1to%d" % last))
    return out


def support_nodes(m):
    g = geom(m)
    n_per = g["ne"] + 1
    left = [(ig - 1) * n_per + 1 for ig in range(1, g["ng"] + 1)]
    right = [ig * n_per for ig in range(1, g["ng"] + 1)]
    return left, right


# ======================================================================
# 钢束
# ======================================================================
TENDON_KEYS = ("z1", "z2", "z3", "z4", "z5")


def tendon_levels(m):
    """每束钢束距梁底高度（m），过滤掉 <=0 或超出梁高的。"""
    g = geom(m)
    h = g["h"]
    out = []
    for k in TENDON_KEYS:
        z = _f(m, "tendon", k, default=0.0)
        if 0.02 < z < h - 0.05:
            out.append(z)
    return out


def tendon_area(m):
    """单束钢束面积（m2）。"""
    ns = _ng(_s(m, "tendon", "ns"), 1, 60, 9)
    a1 = _f(m, "tendon", "a1", default=1.4e-4)     # 单根 φs15.2 公称面积 m2
    return ns * a1


def tendon_force(m):
    """单束张拉控制力（kN）= A × σcon。"""
    return tendon_area(m) * _f(m, "tendon", "scon", default=1.395e6)


def tendon_list(m):
    """返回 [(girder_no, tendon_no, name, y, z)]，z 为相对节点线的全局坐标。"""
    g = geom(m)
    yb = g["yb"]
    out = []
    for ig, y in enumerate(g["ys"], start=1):
        for k, z_from_bottom in enumerate(tendon_levels(m), start=1):
            out.append((ig, k, "P%dN%d" % (ig, k), y, z_from_bottom - yb))
    return out


# ======================================================================
# MCT 生成
# ======================================================================
def _hdr(m):
    name = _s(m, "project", "name", default="装配式预应力混凝土简支T梁桥")
    return [
        "; ================================================================",
        "; MIDAS Civil NX — MCT 命令文件",
        "; 由「MIDAS 建模向导」· 装配式预应力混凝土简支 T 梁桥（整桥）流程生成",
        "; 项目名称：%s" % name,
        "; 导入：MIDAS Civil NX → 文件 → 导入 → MCT 命令文件",
        "; ================================================================",
        "",
    ]


def _unit_lines(m):
    return [
        "*VERSION",
        "   9.6.5",
        "",
        "*UNIT    ; Unit System",
        "; FORCE, LENGTH, HEAT, TEMPER",
        "   KN, M, KJ, C",
        "",
        "*STRUCTYPE    ; Structure Type",
        "; iSTYP, iMASS, iSMAS, bMASSOFFSET, bSELFWEIGHT, GRAV, TEMPER, bALIGNBEAM, bALIGNSLAB, bROTRIGID",
        "   0, 1, 1, NO, NO, 9.806, 0, NO, NO, NO",
        "",
    ]


def _material_lines(m):
    Ec = _f(m, "mat", "Ec", default=3.45e7)
    nu = _f(m, "mat", "nu", default=0.2)
    gam = _f(m, "mat", "gam", default=25.0)
    alpha = _f(m, "mat", "alpha", default=1.0e-5)
    Es = _f(m, "mat", "Es", default=1.95e8)
    nus = _f(m, "mat", "nus", default=0.3)
    gams = _f(m, "mat", "gams", default=78.5)
    conc = _ascii(_s(m, "mat", "conc", default="C50"), "C50")
    return [
        "*MATERIAL    ; Material",
        "; iMAT, TYPE, MNAME, SPHEAT, HEATCO, PLAST, TUNIT, bMASS, DAMPRATIO, [DATA1]",
        "   1, CONC, %s, 0, 0, , C, NO, 0.05, 2, %s, %s, %s, %s, 0"
        % (conc, _num(Ec), _num(nu), _num(alpha), _num(gam)),
        "   2, USER, STRAND1860, 0, 0, , C, NO, 0, 2, %s, %s, 1.2e-05, %s, 0"
        % (_num(Es), _num(nus), _num(gams)),
        "",
    ]


def _pscvalue_entry(m, no, name, props, loops, a_ratio=5.0 / 6.0):
    """一段 *SECT-PSCVALUE 数据（照 tbeam_core 已实测通过的字段顺序）。"""
    a = props["A"]
    iyy = props["Iyy"]
    izz = props["Izz"]
    ixx = props.get("Ixx") or 0.0
    cyp = props.get("CyP", 0.0)
    cym = props.get("CyM", 0.0)
    czp = props.get("CzP", 0.0)
    czm = props.get("CzM", 0.0)
    L = [
        " SECT=   %d, PSC       , %-18s, CC, 0, 0, 0, 0, 0, 0, YES, NO, VALU, NO, NO, "
        % (no, _ascii(name)),
        "       %s, %s, %s, %s, %s, %s"
        % (_fmt_cs(a), _fmt_cs(a * a_ratio), _fmt_cs(a * a_ratio),
           _fmt_cs(ixx), _fmt_cs(iyy), _fmt_cs(izz)),
        "       %s, %s, %s, %s, %s, %s, 0, 0, 0, 0"
        % (_fmt_cs(cyp), _fmt_cs(cym), _fmt_cs(czp), _fmt_cs(czm),
           _fmt_cs(a * czp / 8.0), _fmt_cs(a * cyp / 8.0)),
        "       %s, %s, %s, %s, %s, %s, %s, %s"
        % (_fmt_cs(-cym), _fmt_cs(cyp), _fmt_cs(cyp), _fmt_cs(-cym),
           _fmt_cs(-czm), _fmt_cs(-czm), _fmt_cs(czp), _fmt_cs(czp)),
        "       0.3, 0.2, 0.1, 0.1",
        "       YES, 0, 0, YES, , YES, , YES, , 0, YES, , YES, , YES, ",
    ]
    for lp in loops:
        L.append("       OPOLY=" + ", ".join("%s, %s" % (_fmt_pt(y), _fmt_pt(z)) for (y, z) in lp))
    return L


def _section_lines(m):
    g = geom(m)
    tp = g["t_props"] or {}
    dp = g["d_props"] or {}
    outer = (tp.get("OUTER_LOOPS") or [g["t_poly"]])
    dloops = (dp.get("OUTER_LOOPS") or [g["d_poly"]])
    L = ["*SECT-PSCVALUE    ; PSC Value Section（T 梁按多边形一般截面输入）",
         "; SECT=NO, TYPE, NAME, OFFSET, bSD, bWE, ...（字段顺序照 MIDAS 导出样本）"]
    L += _pscvalue_entry(m, 1, "T30", tp, outer)
    L += _pscvalue_entry(m, 2, "DIA", dp, dloops)
    L.append("")
    return L


def _node_lines(m):
    L = ["*NODE    ; Nodes", "; iNO, X, Y, Z"]
    for (nid, x, y, z) in nodes(m):
        L.append("   %d, %s, %s, %s" % (nid, _num(x), _num(y), _num(z)))
    L.append("")
    return L


def _element_lines(m):
    elems, dia_start = elements(m)
    L = ["*ELEMENT    ; Elements", "; iEL, TYPE, iMAT, iPRO, iN1, iN2, ANGLE, iSUB"]
    for (eid, typ, mat, sec, n1, n2) in elems:
        L.append("   %d, %s, %d, %d, %d, %d, 0, 0" % (eid, typ, mat, sec, n1, n2))
    L.append("")
    return L


def _group_lines(m):
    L = ["*GROUP    ; Structure Group", "; NAME, NODE_LIST, ELEM_LIST, PLANE_TYPE"]
    for (name, nl, el) in groups(m):
        L.append("   %s, %s, %s, 0" % (_ascii(name), nl, el))
    L.append("")
    L += ["*BNDR-GROUP    ; Boundary Group", "; NAME, AUTOTYPE", "   BC, 0", ""]
    L += ["*LOAD-GROUP    ; Load Group", "; NAME", "   SW", "   SDL", "   PSG", ""]
    return L


def _stdcase_lines(m, mv=False):
    L = ["*STLDCASE    ; Static Load Cases", "; LCNAME, LCTYPE, DESC",
         "   SW, D, Self weight",
         "   SDL, D, Second dead load",
         "   PS, USER, Prestress"]
    if mv:
        L.append("   MV, L, Moving load")
    L.append("")
    return L


def _constraint_lines(m):
    left, right = support_nodes(m)
    L = ["*CONSTRAINT    ; Supports", "; NODE_LIST, CONST(Dx,Dy,Dz,Rx,Ry,Rz), GROUP"]
    L.append("   %s, 111000, BC" % " ".join(str(n) for n in left))
    L.append("   %s, 011000, BC" % " ".join(str(n) for n in right))
    L.append("")
    return L


def _selfweight_lines(m):
    return ["*USE-STLD, SW", "*SELFWEIGHT    ; Self Weight", "; X, Y, Z, GROUP", "   0, 0, -1,", ""]


def _sdl_lines(m):
    """二期恒载：每片纵梁一根均布梁荷载；边梁另加人行道+栏杆。"""
    g = geom(m)
    q2c = _f(m, "load", "q2c", default=4.32)
    q2e = _f(m, "load", "q2e", default=7.82)
    L = ["*USE-STLD, SDL", "*BEAMLOAD    ; Element Beam Loads（二期恒载，-Z）",
         "; ELEM_LIST, CMD, TYPE, DIR, bPROJ, bPROJ, D1, P1, D2, P2, ..."]
    for ig in range(1, g["ng"] + 1):
        q = q2e if ig in (1, g["ng"]) else q2c
        a, b = girder_elem_range(m, ig)
        for eid in range(a, b + 1):
            L.append("   %d, BEAM, UNILOAD, GZ, NO, NO, LY, , , , 0, %s, 1, %s, 0, 0, 0, 0, SDL, NO, 0, 0, NO,"
                     % (eid, _num(-q), _num(-q)))
    L.append("")
    return L


def _tendon_property_lines(m):
    A = tendon_area(m)
    dia = math.sqrt(4.0 * A / math.pi / _ng(_s(m, "tendon", "ns"), 1, 60, 9) * 1.0)  # 单根等效直径
    # 单根 φs15.2 直径 0.0152 m；MIDAS 里 DIA 填单根直径
    dia = 0.0152
    rc = _f(m, "tendon", "rc", default=45.0)
    mu = _f(m, "tendon", "mu", default=0.2)
    kk = _f(m, "tendon", "k", default=0.0015)
    slip = _f(m, "tendon", "slip", default=0.006)
    fpk = _f(m, "mat", "fpk", default=1.86e6)
    fpd = _f(m, "mat", "fpd", default=1.569e6)
    L = ["*TDN-PROPERTY    ; Tendon Property",
         "; NAME, TYPE, MATL, AREA, DIA, RM, RC, FF, WF, US, YS, LT, ASB, ASE, bBONDED, ALPHA, bOSRF, FT, LR, FPK, ACHANGE, bRELAX, TDMFNAME, WobbleType, AngleDisp",
         "   STRAND9, INTERNAL, 2, %s, %s, 0, %s, %s, %s, %s, %s, POST, %s, %s, YES, 0, , , ,, 0, YES, 0, 0, 0"
         % (_num(A), _num(dia), _num(rc), _num(mu), _num(kk),
            _num(fpk), _num(fpd), _num(slip), _num(slip)),
         ""]
    return L


def _profile_lines(m):
    g = geom(m)
    L = ["*TDN-PROFILE    ; Tendon Profile（每片梁每束一条直线钢束）",
         "; NAME=NAME, TDN-PROPERTY, ELEM_LIST, BEGIN, END, CURVE, INPUT",
         ";      GROUP, LENGOPT, BLEN, ELEN, bTP, rNUM, DeBondBLEN, DeBondELEN",
         ";      SHAPE, IP_X, IP_Y, IP_Z, AXIS, VX, VY",
         ";      XAR_ANGLE, bPROJECTION, GR_AXIX, GR_ANGLE",
         ";      X1, Y1, Z1, bFIX1, RY1, RZ1, RADIUS1"]
    for (ig, k, name, y, z) in tendon_list(m):
        a, b = girder_elem_range(m, ig)
        L.append("   NAME=%s, STRAND9, %dto%d, 0, 0, SPLINE, 3D" % (_ascii(name), a, b))
        L.append("        , USER, 0, 0, NO, , 0, 0")
        L.append("        STRAIGHT, 0, 0, 0, X, 0, 0")
        L.append("        0, YES, Y, 0")
        L.append("        %s, %s, %s, NO, 0, 0, 0" % (_num(0.0), _num(y), _num(z)))
        L.append("        %s, %s, %s, NO, 0, 0, 0" % (_num(g["L"]), _num(y), _num(z)))
    L.append("")
    return L


def _prestress_lines(m):
    scon = _f(m, "tendon", "scon", default=1.395e6)
    L = ["*USE-STLD, PS", "*TDN-PRESTRESS    ; Tendon Prestress Loads",
         "; TDN-NAME, FORCE/STRESS, JACKING, BEGIN, END, iGROUTING, GROUP"]
    for (ig, k, name, y, z) in tendon_list(m):
        L.append("   %s, STRESS, BEGIN, %s, %s, 1, PSG"
                 % (_ascii(name), _num(scon), _num(scon)))
    L.append("")
    return L


def build_mct(m):
    """生成 MIDAS Civil NX 可导入的 MCT 文本（写文件时用 GBK 编码）。"""
    L = []
    L += _hdr(m)
    L += _unit_lines(m)
    L += _material_lines(m)
    L += _section_lines(m)
    L += _node_lines(m)
    L += _element_lines(m)
    L += _group_lines(m)
    L += _stdcase_lines(m)
    L += _constraint_lines(m)
    L += _selfweight_lines(m)
    L += _sdl_lines(m)
    L += _tendon_property_lines(m)
    L += _profile_lines(m)
    L += _prestress_lines(m)
    L.append("*ENDDATA")
    L.append("")
    return "\n".join(L)


# ======================================================================
# 数据表 / 校验
# ======================================================================
def data_report(m):
    g = geom(m)
    sm = section_summary(m)
    proj = m.get("project") or {}
    L = []
    add = L.append
    add("=" * 78)
    add("装配式预应力混凝土简支 T 梁桥（整桥·梁格）—— 输入数据表")
    add("项目：%s" % proj.get("name", ""))
    add("=" * 78)
    add("1. 总体布置")
    add("   标准跨径 Lb = %s m，计算跨径 L = %s m，梁全长 = %s m"
        % (_s(m, "geom", "Lstd"), _s(m, "geom", "Lcalc"), _s(m, "geom", "Lfull")))
    add("   主梁片数 = %d，梁间距 = %s m，纵向单元数 = %d（单元长 %.4f m）"
        % (g["ng"], _s(m, "geom", "sg"), g["ne"], g["dx"]))
    add("   桥面净宽 = %s m，人行道 = %s m，全宽 = %s m"
        % (_s(m, "geom", "deck"), _s(m, "geom", "walk"), _s(m, "geom", "deck") + "＋2×" + _s(m, "geom", "walk")))
    add("   横隔板：%d 道，厚 %s m，高 %s m；位置节点 %s"
        % (g["ndia"], _s(m, "geom", "tdia"), _s(m, "geom", "hdia"),
           "、".join(str(r + 1) for r in g["rows"])))
    add("2. T 梁截面（单根）")
    add("   梁高 %s m，翼缘宽 %s m，翼缘厚 %s m，腹板厚 %s m，马蹄 %s×%s m"
        % (_s(m, "geom", "h"), _s(m, "geom", "bf"), _s(m, "geom", "tf"),
           _s(m, "geom", "tw"), _s(m, "geom", "bm"), _s(m, "geom", "hm")))
    add("   A = %.4f m2，Iyy = %.6f m4，Izz = %.6f m4，Ixx = %.6f m4，形心距梁底 %.4f m"
        % (sm["A"], sm["Iyy"], sm["Izz"], sm["Ixx"], sm["yb"]))
    add("   横隔板截面：A = %.4f m2，Iyy = %.6f m4" % (sm["dA"], sm["dIyy"]))
    add("3. 材料")
    add("   混凝土 %s：E = %s kN/m2，ν = %s，γ = %s kN/m3"
        % (_s(m, "mat", "conc"), _s(m, "mat", "Ec"), _s(m, "mat", "nu"), _s(m, "mat", "gam")))
    add("   钢绞线：E = %s kN/m2，fpk = %s MPa"
        % (_s(m, "mat", "Es"), _num(_f(m, "mat", "fpk") / 1000.0)))
    add("4. 预应力钢束")
    add("   每梁 %d 束，每束 %s 根 φs15.2，单束面积 %.5f m2，张拉控制应力 %s MPa"
        % (len(tendon_levels(m)), _s(m, "tendon", "ns"), tendon_area(m),
           _num(_f(m, "tendon", "scon") / 1000.0)))
    add("   单束张拉力 %.1f kN，单片梁合计 %.1f kN"
        % (tendon_force(m), tendon_force(m) * len(tendon_levels(m))))
    add("   钢束距梁底高度：" + "、".join("%.2f" % z for z in tendon_levels(m)) + " m")
    add("5. 二期恒载")
    add("   中梁 %.2f kN/m，边梁 %.2f kN/m（含桥面铺装、人行道及栏杆）"
        % (_f(m, "load", "q2c"), _f(m, "load", "q2e")))
    add("6. 模型规模")
    ne_elems, _ = elements(m)
    add("   节点 %d 个，单元 %d 个（纵梁 %d + 横隔板 %d）"
        % (len(nodes(m)), len(ne_elems), g["ng"] * g["ne"], len(ne_elems) - g["ng"] * g["ne"]))
    add("=" * 78)
    return "\r\n".join(L) + "\r\n"


def issues(m, sid=""):
    """各步校验。返回中文问题列表（空 = 通过）。"""
    out = []
    g = geom(m)
    if sid in ("", "geom"):
        if g["L"] <= 0:
            out.append("计算跨径要大于 0。")
        if g["ng"] < 2:
            out.append("主梁片数至少 2 片。")
        if g["sg"] <= 0:
            out.append("梁间距要大于 0。")
        if g["h"] <= 0 or g["bf"] <= 0 or g["tf"] <= 0 or g["tw"] <= 0:
            out.append("截面尺寸（梁高/翼缘宽/翼缘厚/腹板厚）要大于 0。")
        if g["tf"] >= g["h"]:
            out.append("翼缘厚不能大于等于梁高。")
        if g["bm"] <= g["tw"]:
            out.append("马蹄宽应大于腹板厚。")
        if g["hm"] >= g["h"] - g["tf"]:
            out.append("马蹄高应小于（梁高 - 翼缘厚）。")
        if g["ne"] < 2:
            out.append("纵向单元数至少 2。")
        if not g["t_props"] or g["t_props"].get("A", 0) <= 0:
            out.append("T 梁截面多边形算不出面积，检查尺寸。")
    if sid in ("", "tendon"):
        if not tendon_levels(m):
            out.append("还没有有效的钢束高度（0.02 < z < 梁高-0.05）。")
        if tendon_area(m) <= 0:
            out.append("钢束面积要大于 0。")
        if _f(m, "tendon", "scon") <= 0:
            out.append("张拉控制应力要大于 0。")
        yb = g["yb"]
        for z in tendon_levels(m):
            zz = z - yb
            if zz > g["t_props"].get("CzP", 0) or zz < -g["t_props"].get("CzM", 0):
                out.append("钢束高度 %.2f m 超出截面范围。" % z)
                break
    if sid in ("", "load"):
        if _f(m, "load", "q2c") < 0 or _f(m, "load", "q2e") < 0:
            out.append("二期恒载不能为负。")
    return out


def all_issues(m):
    out = []
    for sid in ("geom", "tendon", "load"):
        for msg in issues(m, sid):
            out.append(msg)
    return out


def summary(m):
    g = geom(m)
    sm = section_summary(m)
    return ("主梁 %d 片 × %d 单元，横隔板 %d 道；T 梁 A=%.3f m2，Iyy=%.4f m4；"
            "每梁 %d 束钢束" % (g["ng"], g["ne"], g["ndia"], sm["A"], sm["Iyy"],
                                len(tendon_levels(m))))
