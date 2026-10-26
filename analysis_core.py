# -*- coding: utf-8 -*-
"""分析设置（实验功能）：移动荷载 / 模态 / 屈曲 —— 生成 MCT 片段 + 校验。

纯函数，不依赖 tkinter；梁桥 / 拱桥 / 斜拉桥等流程共用同一份写法。
所有块**默认关闭**：只有 model["analysis"][kind]["on"] 为真时才生成，
所以不开这个功能时，各流程生成的 MCT 与以前逐字节一致。

MCT 字段照 MIDAS 官方《MCT 文件快速参考》：
  *LINELANE   : NAME=NAME, LDIST, GROUP, ECCEN, VX, VY, VZ      /  iELEM, ECC, FACT, ...
  *VEHICLE    : NAME=NAME, 2, W, PL, PLM, PLV
  *VCLASS     : NAME=NAME                                       /  VLOAD1, VLOAD2, ...
  *MVLDCASE   : NAME=NAME, SCALE1, SCALE2, SCALE3, SCALE4, DESC /  VCLASS, SCALE, iMIN, iMAX, LANE...
  *MOVE-CTRL  : METHOD, POINT, PLATE, FRAME, bREAC, bRG, RGN, bDISP, bDG, DGN, bFM, bFG, FGN
  *EIGEN-CTRL : TYPE, iFREQ, iITER, iDIM, TOL（TYPE=EIGEN；老版本为 4 字段）
  *BUCK-CTRL  : iMODENUM, iITER, TOL                            /  LCNAME1, FACT1, ...

注意：这几条命令本机没有 MIDAS 导出的真实样本可对照，属于「实验写法」，
      导入报错时按 MIDAS 给的行号改；不开就不写。
"""
import wiz_common as wc

KINDS = ("moving", "eigen", "buckle")

DEFAULTS = {
    "moving": {"on": False, "lane": "LANE1", "ecc": "0", "impact": "0", "vname": "CH-I",
               "qk": "10.5", "pk": "360", "lc": "MV", "vclass": "VC1"},
    "eigen": {"on": False, "n": "10", "legacy": False},
    "buckle": {"on": False, "n": "3", "lcs": ""},
}


def get(model, kind, key=None):
    d = (model.get("analysis") or {}).get(kind) or {}
    return d.get(key) if key else d


def is_on(model, kind):
    return bool(get(model, kind, "on"))


def defaults(kind):
    return dict(DEFAULTS[kind])


# ----------------------------------------------------------------------
# 生成 MCT 片段
# ----------------------------------------------------------------------
def moving_lines(model, element_count=None):
    """移动荷载：车道 + 用户定义车辆（公路-I级 qk/Pk）+ 车辆组 + 移动荷载工况 + 分析控制。"""
    mv = get(model, "moving")
    if not mv.get("on"):
        return []
    if element_count is None:
        element_count = len(model.get("elements") or [])
    lane = wc.aname(mv.get("lane"), "LANE1")
    vname = wc.aname(mv.get("vname"), "CH-I")
    vclass = wc.aname(mv.get("vclass"), "VC1")
    mvlc = wc.aname(mv.get("lc"), "MV")
    qk = wc.num(mv.get("qk", 10.5))
    pk = wc.num(mv.get("pk", 360))
    sk = wc.num(mv.get("pk", 360))
    sv = wc.num(mv.get("pk", 360))
    L = []
    add = L.append
    add("*LINELANE    ; Traffic Line Lanes（车道，铺在所有梁单元上）")
    add("; NAME=NAME, LDIST, GROUP, ECCEN, VX, VY, VZ")
    add("   NAME=%s, BEAM, , 0, 0, 1, 0" % lane)
    add("; iELEM1, ECC1, FACT1, ...（单元号, 车道偏心距离, 冲击系数）")
    add("   " + ", ".join("%d, %s, %s" % (i, wc.num(mv.get("ecc", 0)), wc.num(mv.get("impact", 0)))
                         for i in range(1, int(element_count) + 1)))
    add("")
    add("*VEHICLE    ; Vehicles（车辆：用户定义车道荷载，公路-I级＝qk 均布 + Pk 集中）")
    add("; NAME=NAME, 2, W, PL, PLM, PLV")
    add("   NAME=%s, 2, %s, %s, %s, %s" % (vname, qk, pk, sk, sv))
    add("")
    add("*VCLASS    ; Vehicle Classes（车辆荷载群）")
    add("; NAME=NAME")
    add("   NAME=%s" % vclass)
    add("   %s" % vname)
    add("")
    add("*MVLDCASE    ; Moving Load Cases（移动荷载工况）")
    add("; NAME=NAME, SCALE1, SCALE2, SCALE3, SCALE4, DESC")
    add("   NAME=%s, 1, 1, 0.9, 0.75, MV" % mvlc)
    add("; VCLASS1, SCALE1, iMIN1, iMAX1, LANE11, LANE12, ...")
    add("   %s, 1, 1, 1, %s" % (vclass, lane))
    add("")
    add("*MOVE-CTRL    ; Moving Load Analysis Control")
    add("; METHOD, POINT, PLATE, FRAME, bREAC, bRG, RGN, bDISP, bDG, DGN, bFM, bFG, FGN")
    add("   1, 0, CENTER, NORMAL, YES, NO, , YES, NO, , YES, NO, ")
    add("")
    return L


def eigen_lines(model):
    """模态分析控制（*EIGEN-CTRL）。"""
    eig = get(model, "eigen")
    if not eig.get("on"):
        return []
    try:
        n = int(float(eig.get("n") or 10))
    except (TypeError, ValueError):
        n = 10
    L = ["*EIGEN-CTRL    ; Eigenvalue Analysis Control（模态分析控制）"]
    if eig.get("legacy"):
        L.append("; iFREQ, iITER, iDIM, TOL（旧版写法）")
        L.append("   %d, 20, 0, 1e-06" % n)
    else:
        L.append("; TYPE, iFREQ, iITER, iDIM, TOL")
        L.append("   EIGEN, %d, 20, 0, 1e-06" % n)
    L.append("")
    return L


def buckling_lines(model):
    """屈曲分析控制（*BUCK-CTRL）：模态数 + 可变荷载「工况名, 系数」对。"""
    bk = get(model, "buckle")
    if not bk.get("on"):
        return []
    toks = str(bk.get("lcs") or "").replace(",", " ").split()
    pairs = []
    for k in range(0, len(toks) - 1, 2):
        pairs.append("%s, %s" % (wc.aname(toks[k]), wc.num(toks[k + 1])))
    L = ["*BUCK-CTRL    ; Buckling Analysis Control（屈曲分析：可变荷载及系数）",
         "; iMODENUM, iITER, TOL",
         "   %s, 30, 1e-06" % wc.num(bk.get("n", 3)),
         "; LCNAME1, FACT1, LCNAME2, FACT2, ...（工况名, 系数）",
         "   " + ", ".join(pairs),
         ""]
    return L


def lines(model, kinds=KINDS, element_count=None):
    """按 kinds 顺序拼出所有启用的分析块。"""
    out = []
    if "moving" in kinds:
        out += moving_lines(model, element_count)
    if "eigen" in kinds:
        out += eigen_lines(model)
    if "buckle" in kinds:
        out += buckling_lines(model)
    return out


# ----------------------------------------------------------------------
# 校验
# ----------------------------------------------------------------------
def issues(model, kinds=KINDS, element_count=None, loadcase_names=None):
    out = []
    if element_count is None:
        element_count = len(model.get("elements") or [])
    if "moving" in kinds and is_on(model, "moving"):
        mv = get(model, "moving")
        if not str(mv.get("lane") or "").strip():
            out.append("移动荷载：车道名不能空。")
        for key, zh, unit in (("qk", "车道均布 qk", "kN/m"), ("pk", "集中荷载 Pk", "kN")):
            if not wc.is_num(mv.get(key)) or float(mv.get(key)) < 0:
                out.append("移动荷载：%s 要填不小于 0 的数字（%s）。" % (zh, unit))
        if not element_count:
            out.append("移动荷载：还没有单元，车道铺不上去。")
        if wc.is_num(mv.get("impact")) and not (0.0 <= float(mv["impact"]) <= 0.3):
            out.append("移动荷载：冲击系数要填 0~0.3 之间。")
    if "eigen" in kinds and is_on(model, "eigen"):
        n = get(model, "eigen", "n")
        if not wc.is_num(n) or not (1 <= int(float(n)) <= 200):
            out.append("模态分析：提取阶数要填 1~200 的整数。")
    if "buckle" in kinds and is_on(model, "buckle"):
        bk = get(model, "buckle")
        n = bk.get("n")
        if not wc.is_num(n) or not (1 <= int(float(n)) <= 50):
            out.append("屈曲分析：模态数要填 1~50 的整数。")
        toks = str(bk.get("lcs") or "").replace(",", " ").split()
        if len(toks) < 2 or len(toks) % 2:
            out.append("屈曲分析：可变荷载要写成「工况名 系数」成对出现（例如 DL 1.0）。")
        else:
            names = loadcase_names or []
            for k in range(0, len(toks), 2):
                if names and toks[k] not in names:
                    out.append("屈曲分析：工况「%s」不在荷载工况表里。" % toks[k])
                if not wc.is_num(toks[k + 1]):
                    out.append("屈曲分析：工况「%s」的系数要填数字。" % toks[k])
    return out
