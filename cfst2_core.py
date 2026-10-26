# -*- coding: utf-8 -*-
r"""两跨钢管混凝土拱 · 施工顺序 + 湿重两种模拟对比 —— 核心引擎。

纯计算 / MCT 生成，不依赖 tkinter（测试可以直接 import 调用）。界面在 flow_cfst2.py。

模型
------------------------------------------------------------------
* 两跨拱，每跨计算跨径 L（默认 50 m）、矢高 f（默认 10 m）、二次抛物线
  （备选悬链线）、每跨 30 个单元；
* 两跨拱脚分别位于 x = 0 / L / 2L。默认「两跨在中间拱脚共用节点」：
  节点 2n+1 个、单元 2n 个（n = 每跨单元数），中间节点在 x=L；
  备选「两跨独立」：x=L 处各有一个节点，节点 2n+2 个、单元仍是 2n 个；
* 钢管 φ1000×100 mm（Q485）+ 核心混凝土 C50，管内混凝土容重 25 kN/m³；
* 施工阶段（默认）：CS1(7 d) 两跨钢管同时架设 → CS2(1 d) 两跨同时灌注
  → CS3(20 d) 混凝土硬化（联合截面生效）→ CS4(7 d) 二期恒载 20 kN/m。

湿重的两种模拟（同一模型生成两套 MCT 做对比）
------------------------------------------------------------------
方法 A（湿重作梁单元荷载）：
    混凝土 Part 在 CS3 才激活、材龄 30 d（硬化后才参与刚度）；
    CS2 在两跨全部钢单元上加 WET1 均布荷载 q = 核心混凝土面积 × 容重，
    CS3 用 DLOAD 把 WET1 卸掉 —— 重量与刚度都不重复计算。
方法 B（材龄 0 d，湿重 = 混凝土单元自重）：
    混凝土 Part 在 CS2 激活、材龄 0 d。MIDAS 里「材龄 0」表示混凝土处于
    流动（湿重）状态：只计重量、不计刚度（第五讲：管内混凝土灌注时材龄为 0，
    表示湿重）；湿重就是该 Part 的自重，由 *SELFWEIGHT 随 Part 激活自动计入，
    不另加湿重荷载工况。CS3 起材龄随时段增长、混凝土逐渐参与刚度。

MCT 格式（一律照 MIDAS 自己导出的样本，不照手册猜）
------------------------------------------------------------------
* 施工阶段 *STAGE、联合截面 *CPOSECT4CS、一般截面 *SECT-PSCVALUE、湿重
  *BEAMLOAD 的写法全部沿用已在本机 MIDAS Civil NX 真跑过的
  C:\Midas\work\e2e\施工阶段.mct；
* *CPOSECT4CS 每部件行必须 37 个字段（格式细节见 cfst_core.py 顶部注释）；
* Part 1（钢管）随单元在第 1 阶段激活：CSTAGE 留空、AGE = 0；
  Part 2（核心混凝土）的 CSTAGE / AGE 就是方法 A、B 的唯一区别（连同湿重工况）。
"""

import datetime
import math

import cfst_core as cf

# ----------------------------------------------------------------------
# 常量
# ----------------------------------------------------------------------
CODE = "cfst2"
LABEL = "两跨钢管混凝土（施工顺序·湿重对比）"

GAMMA_CONC = cf.GAMMA_CONC        # 25 kN/m3
GAMMA_STEEL = cf.GAMMA_STEEL      # 78.5 kN/m3

# 两跨连接方式：共用中间拱脚节点 / 两跨独立
SPAN_JOINTS = [("share", "两跨在中间拱脚共用节点"),
               ("separate", "两跨独立（各一个节点）")]
SPAN_JOINT_NAMES = dict(SPAN_JOINTS)

# 拱轴类型（第 05 讲算例只用抛物线；悬链线作为备选）
ARCH_TYPES = [("parabola", "二次抛物线"), ("catenary", "悬链线")]
ARCH_TYPE_NAMES = dict(ARCH_TYPES)

# 湿重模拟方法
METHODS = [("A", "湿重作梁单元荷载"),
           ("B", "混凝土 Part 在灌注阶段激活、材龄 0 d（湿重 = 自重）")]
METHOD_NAMES = dict(METHODS)
METHOD_FILES = {"A": "_方法A_湿重作荷载.mct",
                "B": "_方法B_材龄0d.mct"}

WET_GROUP = "WET1"                # 方法 A 的湿重荷载组
Q2_GROUP = "DL2"                  # 二期恒载工况


# ----------------------------------------------------------------------
# 小工具（直接复用 cfst_core 里已验证过格式的那几个）
# ----------------------------------------------------------------------
def _f(v, default=0.0):
    return cf._f(v, default)


def _num(v, nd=6):
    return cf._num(v, nd)


def _fmt_cs(v):
    return cf._fmt_cs(v)


# ----------------------------------------------------------------------
# 模型参数 / 几何
# ----------------------------------------------------------------------
def geom_of(model):
    """把 model["geom"] 里的字符串参数整理成数值字典（坏值给默认，校验交给 issues）。"""
    g = model.get("geom") or {}
    n = int(max(4, min(400, _f(g.get("n"), 30.0))))
    return {
        "L": _f(g.get("L"), 50.0),
        "f": _f(g.get("f"), 10.0),
        "m": _f(g.get("m"), 1.6),
        "n": n,
        "type": (g.get("type") or "parabola"),
        "z0": _f(g.get("z0"), 0.0),
        "joint": (g.get("joint") or "share"),
        "q2": _f(g.get("q2", model.get("q2")), 20.0),
    }


def span_points(L, f, kind="parabola", m=1.6, n=30, z0=0.0):
    """一跨拱轴线上的 n+1 个点 [(x, z), ...]（x 从 0 到 L）。"""
    pts = cf.arch_axis(L, f, m, kind, n, z0)
    return [(float(x), float(z)) for (x, z) in pts]


def build_geometry(model):
    """两跨拱的节点 / 单元 / 拱脚约束（两种连接方式都支持）。

    返回的键：
        nodes    [(节点号, x, z), ...]     按节点号顺序
        elems    [(单元号, 起点节点, 终点节点), ...]
        supports [拱脚节点号, ...]          左右拱脚 + 中间拱脚
        n_nodes / n_elems  总数
        span1_pts / span2_pts  各跨拱轴点（全局坐标）
    """
    gp = geom_of(model)
    L, f, n = gp["L"], gp["f"], gp["n"]
    z0 = gp["z0"]
    share = gp["joint"] != "separate"

    p1 = span_points(L, f, gp["type"], gp["m"], n, z0)
    p2 = [(x + L, z) for (x, z) in p1]

    nodes = []
    for i, (x, z) in enumerate(p1):
        nodes.append((i + 1, x, z))
    if share:
        # 第一跨最后一个节点 = 中间拱脚，第二跨从它的下一个节点开始（共用 x=L 节点）
        for j in range(1, n + 1):
            x, z = p2[j]
            nodes.append((n + 1 + j, x, z))
    else:
        # 两跨独立：第二跨从 0 开始，x=L 处各有一个节点
        for j in range(0, n + 1):
            x, z = p2[j]
            nodes.append((n + 2 + j, x, z))

    elems = []
    for i in range(1, n + 1):                      # 第一跨
        elems.append((i, i, i + 1))
    if share:
        for k in range(1, n + 1):                  # 第二跨（共用 x=L 节点）
            elems.append((n + k, n + k, n + k + 1))
        supports = [1, n + 1, 2 * n + 1]
    else:
        for k in range(1, n + 1):                  # 第二跨（独立节点）
            elems.append((n + k, n + 1 + k, n + 2 + k))
        supports = [1, n + 1, n + 2, 2 * n + 2]

    return {
        "L": L, "f": f, "n": n, "z0": z0,
        "type": gp["type"], "m": gp["m"], "joint": gp["joint"], "share": share,
        "nodes": nodes, "elems": elems, "supports": supports,
        "n_nodes": len(nodes), "n_elems": len(elems),
        "pts": [(x, z) for (_i, x, z) in nodes],
        "span1_pts": p1, "span2_pts": p2,
        "mid_nodes": ([n + 1] if share else [n + 1, n + 2]),
        "arch_len": cf.arch_length(p1),
        "sum_spans": 2.0 * L,
        "q2": gp["q2"],
    }


def span_count(model):
    """节点数、单元数的快速计算（界面摘要用，不用建整套几何）。"""
    gp = geom_of(model)
    n = gp["n"]
    share = gp["joint"] != "separate"
    return {"n_nodes": 2 * n + 1 if share else 2 * n + 2,
            "n_elems": 2 * n, "n": n, "share": share}


# ----------------------------------------------------------------------
# 截面（复用 cfst_core 的 Part / Section / 截面特性）
# ----------------------------------------------------------------------
def stage_names(model):
    return [str(s.get("name") or "").strip() for s in (model.get("stages") or [])]


def conc_stage_index(model, method="A"):
    """核心混凝土在哪个阶段开始参与受力（存的是 stages 的下标，改名字也不受影响）。"""
    key = "conc_stage_a" if str(method).upper().startswith("A") else "conc_stage_b"
    default = 2 if str(method).upper().startswith("A") else 1
    try:
        return int(model.get(key, default))
    except (TypeError, ValueError):
        return default


def conc_stage_name(model, method="A"):
    names = stage_names(model)
    i = conc_stage_index(model, method)
    return names[i] if 0 <= i < len(names) else ""


def conc_age(model, method="A"):
    key = "conc_age_a" if str(method).upper().startswith("A") else "conc_age_b"
    default = 30.0 if str(method).upper().startswith("A") else 0.0
    return max(0.0, _f(model.get(key), default))


def wet_stage_index(model):
    """方法 A 的湿重加在哪一阶段 = 混凝土激活阶段的前一阶段（默认 CS2 ← CS3）。"""
    return conc_stage_index(model, "A") - 1


def wet_stage_name(model):
    names = stage_names(model)
    i = wet_stage_index(model)
    return names[i] if 0 <= i < len(names) else ""


def q2_value(model):
    q = _f((model.get("geom") or {}).get("q2", model.get("q2")), 0.0)
    return q if q > 1e-12 else 0.0


def core_model(model, method="A"):
    """把两跨模型翻译成 cfst_core 认识的单跨模型字典（只为了复用截面/材料函数）。"""
    gp = geom_of(model)
    tb = model.get("tube") or {}
    D_m = _f(tb.get("D_mm"), 1000.0) / 1000.0
    t_m = _f(tb.get("t_mm"), 100.0) / 1000.0
    return {
        "project": dict(model.get("project") or {}),
        "arch": {"type": gp["type"], "L": "%.12g" % gp["L"], "f": "%.12g" % gp["f"],
                 "m": "%.12g" % gp["m"], "n": str(gp["n"]),
                 "z0": "%.12g" % gp["z0"], "fixed": True},
        "tube": {"form": "single",
                 "D": "%.12g" % D_m, "t": "%.12g" % t_m,
                 "conc": str(tb.get("conc") or "C50").strip(),
                 "steel": str(tb.get("steel") or "Q485").strip(),
                 "reduce": "1.0"},
        "stages": [dict(s) for s in (model.get("stages") or [])],
        "conc_stage": conc_stage_name(model, method),
        "conc_age": "%.12g" % conc_age(model, method),
        "q2": "%.12g" % gp["q2"],
        "gamma": "%.12g" % _f(model.get("gamma"), GAMMA_CONC),
        "creep": False,
    }


def section_for(model, method="A"):
    """钢管 + 核心混凝土的组合截面（Part 2 的激活阶段/材龄按 method 取）。"""
    return cf.section_from_model(core_model(model, method))


def summary(model, method="A"):
    """截面特性摘要（A_steel / A_conc / 组合 A、I / 自重 / 湿重 q …）。"""
    return cf.summary(core_model(model, method))


def wet_q(model):
    """管内混凝土湿重的均布集度 q = 核心混凝土面积 × 容重（kN/m）。"""
    return summary(model)["w_conc"]


def q2_lines_count(model):
    gp = build_geometry(model)
    return gp["n_elems"] if q2_value(model) > 0 else 0


# ----------------------------------------------------------------------
# MCT 生成
# ----------------------------------------------------------------------
def _material_lines(model):
    mats, _fck = cf.material_lines(core_model(model, "A"))
    return mats


def _header_lines(model, method):
    gp = build_geometry(model)
    sm = summary(model)
    names = stage_names(model)
    idx_a = conc_stage_index(model, "A")
    idx_b = conc_stage_index(model, "B")
    q2 = q2_value(model)
    wet = wet_q(model)
    prj = model.get("project") or {}
    n = gp["n"]
    type_name = ARCH_TYPE_NAMES.get(gp["type"], "二次抛物线")
    joint_name = SPAN_JOINT_NAMES.get(gp["joint"], "两跨在中间拱脚共用节点")
    head = [
        "; ================================================================",
        "; MIDAS Civil NX — MCT（两跨钢管混凝土拱 · 施工阶段联合截面 · 湿重模拟方法 %s）" % method,
        "; 生成时间：" + datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "; 项目：%s" % (prj.get("name") or "两跨钢管混凝土拱"),
        "; 模型：两跨拱  每跨 L=%.3f m  f=%.3f m  %s  每跨 %d 个单元  %s"
        % (gp["L"], gp["f"], type_name, n, joint_name),
        ";       节点 %d 个、单元 %d 个；拱脚节点 %s 固结（无铰）。"
        % (gp["n_nodes"], gp["n_elems"], " / ".join(str(x) for x in gp["supports"])),
        "; 截面：钢管 φ%.0f×%.0f mm（A=%.4f m2）+ 核心混凝土 %s（A=%.4f m2）"
        % (_f((model.get("tube") or {}).get("D_mm"), 1000.0),
           _f((model.get("tube") or {}).get("t_mm"), 100.0),
           sm["A_steel"], cf.model_conc(core_model(model, "A")), sm["A_conc"]),
        "; 施工阶段：" + " → ".join("%s(%s d)" % (s.get("name"), s.get("days"))
                                 for s in (model.get("stages") or [])),
    ]
    if str(method).upper().startswith("A"):
        head += [
            "; 方法 A（湿重作梁单元荷载）：混凝土 Part 在「%s」激活、材龄 %.0f d；"
            % (conc_stage_name(model, "A"), conc_age(model, "A")),
            ";   湿重 WET1 q=%.3f kN/m（= 核心混凝土面积 × %.1f kN/m3）在「%s」加到两跨全部钢单元，"
            % (wet, _f(model.get("gamma"), GAMMA_CONC), wet_stage_name(model)),
            ";   在「%s」用 DLOAD 卸掉，同一阶段混凝土 Part 开始参与受力 —— 重量、刚度都不重复。"
            % conc_stage_name(model, "A"),
        ]
    else:
        head += [
            "; 方法 B（材龄 0 d，湿重 = 混凝土单元自重）：混凝土 Part 在「%s」激活、材龄 0 d。"
            % conc_stage_name(model, "B"),
            ";   材龄 0 表示混凝土处于流动（湿重）状态：只计重量、不计刚度；湿重就是该 Part 的自重，",
            ";   由 *SELFWEIGHT 随 Part 激活自动计入，不另设湿重荷载工况。",
            ";   「%s」起材龄随时段增长，混凝土逐渐参与刚度。" % conc_stage_name(model, "A"),
        ]
    head += [
        "; 二期恒载：%s" % ("q2=%.3f kN/m，在最后阶段 %s 加 DL2" % (q2, names[-1]) if q2 > 0
                          else "不加（q2=0）"),
        "; 提示：本文件是「方法 %s」模型，方法 %s 的另一套 MCT 由同一向导同时生成，"
        % (method, "B" if str(method).upper().startswith("A") else "A"),
        ";       两份都导入 MIDAS（各开一个文档）即可对比湿重模拟方式对施工阶段结果的影响。",
        "; ================================================================",
    ]
    return head


def stage_rows(model, method="A"):
    """*STAGE 的 NAME / 子行（湿重只在方法 A 里出现）。"""
    method = str(method).upper()[:1]
    names = stage_names(model)
    stages = model.get("stages") or []
    q2 = q2_value(model)
    idx_a = conc_stage_index(model, "A")
    rows = []
    for i, st in enumerate(stages):
        nm = names[i] if i < len(names) else str(st.get("name") or "CS%d" % (i + 1))
        days = st.get("days", 20)
        rows.append("  NAME=%s, %s, YES, YES, NO, 5" % (nm, days))
        if i == 0:
            rows.append("  AELEM=EL_ALL, %s" % days)
            rows.append("  ABNDR=BC, DEFORMED")
            rows.append("  ALOAD=SW, FIRST")
        if method == "A":
            if i == idx_a - 1:
                rows.append("  ALOAD=%s, FIRST" % WET_GROUP)
            if i == idx_a:
                rows.append("  DLOAD=%s, FIRST" % WET_GROUP)
        if q2 > 0 and i == len(stages) - 1:
            rows.append("  ALOAD=%s, FIRST" % Q2_GROUP)
    return rows


def cposect_lines(model, method="A"):
    """*CPOSECT4CS：SEC 行 + 每个部件 37 个字段（第 2 部件的激活阶段/材龄随 method 变）。"""
    sec = section_for(model, method)
    sp = sec.props()
    names = stage_names(model)
    reduced = 1.0
    lines = ["*CPOSECT4CS    ; Composite Section for Construction Stage",
             "   SEC=1, %s, USER, NO" % (names[0] if names else "CS1")]
    cy, cz = sp["Cy"], sp["Cz"]
    for k, part in enumerate(sec.parts):
        px, pz = part.centroid
        a = part.area
        iyy, izz = part.inertia()
        if part.kind == "conc":
            iyy *= reduced
            izz *= reduced
        x1, x2, z1, z2 = part.bbox
        cyp, cym = x2 - px, px - x1
        czp, czm = z2 - pz, pz - z1
        per = part.perimeter
        h = 2.0 * a / per if per > 1e-9 else 0.5
        if k == 0:
            # 第 1 个部件（钢管）：CSTAGE / AGE 必须留空 / 0（实测写阶段名整份被拒）
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
        lines.append("       " + ", ".join(_fmt_cs(v) for v in row))
    return lines


def beamload_lines(model, q, group):
    """梁单元均布荷载（-Z 方向），一个单元一行。"""
    gp = build_geometry(model)
    return [cf.beamload_line(eid, q, group) for (eid, _n1, _n2) in gp["elems"]]


def build_mct(model, method="A"):
    """生成一套完整的施工阶段 MCT（method = "A" 湿重作荷载 / "B" 材龄 0 d）。"""
    method = str(method or "A").upper()[:1]
    if method not in ("A", "B"):
        method = "A"
    gp = build_geometry(model)
    names = stage_names(model)
    q2 = q2_value(model)
    wet = wet_q(model)
    is_a = (method == "A")

    Lw = []

    def add(s=""):
        Lw.append(s)

    for ln in _header_lines(model, method):
        add(ln)

    add("*VERSION")
    add("   9.5.0")
    add("*UNIT    ; Unit System")
    add("   KN, M, KJ, C")
    add("*STRUCTYPE    ; Structure Type")
    add("   0, 1, 1, NO, NO, 9.806, 0, NO, NO, NO")

    # ---- 节点 / 单元 ----
    add("*NODE    ; Nodes")
    for nid, x, z in gp["nodes"]:
        add("   %d, %s, 0, %s" % (nid, _num(x, 8), _num(z, 8)))
    add("*ELEMENT    ; Elements")
    for eid, n1, n2 in gp["elems"]:
        add("   %d, BEAM, 1, 1, %d, %d, 0, 0" % (eid, n1, n2))

    # ---- 组 ----
    add("*GROUP    ; Group")
    add("   EL_ALL, 1to%d, 1to%d, 0" % (gp["n_nodes"], gp["n_elems"]))
    add("*BNDR-GROUP    ; Boundary Group")
    add("   BC, 0")
    add("*LOAD-GROUP    ; Load Group")
    add("   SW")
    if is_a:
        add("   %s" % WET_GROUP)
    if q2 > 0:
        add("   %s" % Q2_GROUP)

    # ---- 材料 ----
    add("*MATERIAL    ; Material")
    for ln in _material_lines(model):
        add(ln)

    # ---- 基础截面（PSC 一般截面，联合截面必须有它） ----
    add("*SECT-PSCVALUE    ; PSC Value / General Section")
    sec = section_for(model, method)
    for ln in cf.pscvalue_lines(sec, sec.name):
        add(ln)
    add("")

    # ---- 荷载工况 ----
    add("*STLDCASE    ; Static Load Cases")
    add("   SW, CS, \"Self Weight\"")
    if is_a:
        add("   %s, CS, \"Wet Concrete\"" % WET_GROUP)
    if q2 > 0:
        add("   %s, CS, \"Second Dead Load\"" % Q2_GROUP)

    # ---- 约束：拱脚固结 ----
    add("*CONSTRAINT    ; Supports")
    for nid in gp["supports"]:
        add("   %d, 111111, BC" % nid)

    # ---- 荷载 ----
    add("*USE-STLD, SW")
    add("*SELFWEIGHT    ; Self Weight")
    add("0, 0, -1, SW")
    if is_a and wet > 0:
        add("*USE-STLD, %s" % WET_GROUP)
        add("*BEAMLOAD    ; Element Beam Loads（方法 A：管内湿混凝土按均布力加）")
        for ln in beamload_lines(model, wet, WET_GROUP):
            add(ln)
    if q2 > 0:
        add("*USE-STLD, %s" % Q2_GROUP)
        add("*BEAMLOAD    ; Element Beam Loads（二期恒载）")
        for ln in beamload_lines(model, q2, Q2_GROUP):
            add(ln)

    # ---- 施工阶段 ----
    add("*STAGE    ; Define Construction Stage")
    for ln in stage_rows(model, method):
        add(ln)

    # ---- 施工阶段联合截面 ----
    for ln in cposect_lines(model, method):
        add(ln)

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


def build_mct_pair(model):
    """同时生成两套 MCT，返回 {"A": text, "B": text}。"""
    return {"A": build_mct(model, "A"), "B": build_mct(model, "B")}


def mct_stats(model, method="A"):
    """给界面日志用的统计（行数 / 节点 / 单元 / 湿重 / 关键字段）。"""
    gp = build_geometry(model)
    text = build_mct(model, method)
    return {
        "method": method, "lines": len(text.splitlines()),
        "n_nodes": gp["n_nodes"], "n_elems": gp["n_elems"],
        "n_stages": len(model.get("stages") or []),
        "q2": q2_value(model), "wet": wet_q(model),
        "conc_stage": conc_stage_name(model, method),
        "conc_age": conc_age(model, method),
        "text": text,
    }


# ----------------------------------------------------------------------
# 阶段计划说明（界面第 3 步 / 数据表共用）
# ----------------------------------------------------------------------
def stage_plan_text(model):
    """把施工阶段表翻译成「方法 A / 方法 B 各自会发生什么」的人话。"""
    names = stage_names(model)
    stages = model.get("stages") or []
    q2 = q2_value(model)
    wet = wet_q(model)
    idx_a = conc_stage_index(model, "A")
    idx_b = conc_stage_index(model, "B")
    age_a = conc_age(model, "A")
    age_b = conc_age(model, "B")
    lines = []
    for i, st in enumerate(stages):
        nm = names[i] if i < len(names) else str(st.get("name") or "")
        notes = []
        if i == 0:
            notes.append("激活两跨全部单元 + 边界 + 自重")
        if i == idx_a - 1:
            notes.append("方法A：加湿重 %s q=%.2f kN/m" % (WET_GROUP, wet))
        if i == idx_b:
            notes.append("方法B：混凝土 Part 激活（材龄 %.0f d，湿重=自重）" % age_b)
        if i == idx_a:
            notes.append("方法A：卸湿重 + 混凝土 Part 激活（材龄 %.0f d，联合截面生效）" % age_a)
        if i == idx_a and i == idx_b:
            notes.append("（两法同一阶段生效）")
        if q2 > 0 and i == len(stages) - 1:
            notes.append("加二期恒载 %s q=%.2f kN/m" % (Q2_GROUP, q2))
        lines.append("  %-8s %-4s d  %s%s"
                     % (nm, st.get("days", ""), st.get("desc") or "",
                        ("    ← " + "；".join(notes)) if notes else ""))
    return "\n".join(lines)


# ----------------------------------------------------------------------
# 默认模型
# ----------------------------------------------------------------------
def default_model():
    """两跨钢管混凝土拱：每跨 L=50 m / f=10 m、φ1000×100 mm、C50、每跨 30 单元。"""
    return {
        "flow": CODE,
        "project": {"name": "两跨CFST", "outdir": "",
                    "force": "KN", "dist": "M", "styp": "0"},
        "geom": {"type": "parabola", "L": "50", "f": "10", "m": "1.6",
                 "n": "30", "z0": "0", "joint": "share", "q2": "20"},
        "tube": {"D_mm": "1000", "t_mm": "100", "steel": "Q485", "conc": "C50"},
        "gamma": str(GAMMA_CONC),
        "stages": [
            {"name": "CS1", "days": "7", "desc": "架设第一、二跨钢管（空钢管）"},
            {"name": "CS2", "days": "1", "desc": "灌注第一、二跨管内混凝土（湿重）"},
            {"name": "CS3", "days": "20", "desc": "混凝土硬化，联合截面参与受力"},
            {"name": "CS4", "days": "7", "desc": "二期恒载（成桥）"},
        ],
        "conc_stage_a": 2,       # 方法 A：CS3 开始参与受力（硬化后）
        "conc_age_a": "30",
        "conc_stage_b": 1,       # 方法 B：CS2 灌注时激活，材龄 0 d（流动/湿重状态）
        "conc_age_b": "0",
    }


def reset_model():
    return default_model()


# ----------------------------------------------------------------------
# 参数校验
# ----------------------------------------------------------------------
def geom_issues(model):
    out = []
    g = model.get("geom") or {}
    try:
        L = float(g.get("L"))
        f = float(g.get("f"))
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
        n = int(float(g.get("n")))
        if n < 8 or n > 400:
            out.append("每跨单元数建议 8~400（现在 %d）。" % n)
    except (TypeError, ValueError):
        out.append("每跨单元数要填整数。")
    kind = (g.get("type") or "parabola")
    if kind not in ARCH_TYPE_NAMES:
        out.append("拱轴类型只能是二次抛物线 / 悬链线。")
    if kind == "catenary":
        try:
            mm = float(g.get("m"))
            if mm <= 1.0:
                out.append("悬链线的拱轴系数 m 必须大于 1（常用 1.1~3）。")
            elif mm > 5:
                out.append("拱轴系数 m=%g 太大了（常用 1.1~3），确认一下。" % mm)
        except (TypeError, ValueError):
            out.append("悬链线要填拱轴系数 m（大于 1）。")
    joint = (g.get("joint") or "share")
    if joint not in SPAN_JOINT_NAMES:
        out.append("两跨连接方式只能是「共用中间拱脚节点」或「两跨独立」。")
    try:
        q2 = float(g.get("q2", model.get("q2", 0)) or 0)
        if q2 < 0:
            out.append("二期恒载不能是负数（不填或填 0 表示不加）。")
    except (TypeError, ValueError):
        out.append("二期恒载要填数字（kN/m），不需要就填 0。")
    return out


def section_issues(model):
    out = []
    tb = model.get("tube") or {}
    try:
        D = float(tb.get("D_mm"))
    except (TypeError, ValueError):
        return ["钢管外径 D 要填数字（mm）。"]
    if D <= 0:
        out.append("钢管外径 D 要大于 0。")
        return out
    try:
        t = float(tb.get("t_mm"))
    except (TypeError, ValueError):
        out.append("钢管壁厚 t 要填数字（mm）。")
        t = 0.0
    if t <= 0:
        out.append("钢管壁厚 t 要大于 0。")
    elif t >= D / 2.0:
        out.append("壁厚 %.4g mm 太大：必须小于外径的一半（半径 %.4g mm）。" % (t, D / 2.0))
    elif (D - 2.0 * t) < 0.02 * D:
        out.append("管内混凝土直径太小（D-2t=%.4g mm），检查壁厚。" % (D - 2.0 * t))
    steel = str(tb.get("steel") or "").strip()
    if steel not in cf.STEEL_LIST:
        out.append("钢材牌号选 %s 之一。" % " / ".join(cf.STEEL_LIST))
    conc = str(tb.get("conc") or "").strip()
    if conc not in cf.CONC:
        out.append("混凝土等级选 %s 之一。" % " / ".join(cf.CONC_LIST))
    try:
        gam = float(model.get("gamma"))
        if gam <= 0 or gam > 40:
            out.append("混凝土容重应在 0~40 kN/m3（常用 24~26）。")
    except (TypeError, ValueError):
        out.append("混凝土容重要填数字（kN/m3，常用 25）。")
    if not out:
        out += section_for(model).validate()
    return out


def stage_issues(model):
    out = []
    stages = model.get("stages") or []
    if len(stages) < 4:
        out.append("至少要 4 个施工阶段（架设钢管 → 灌注混凝土 → 硬化 → 二期）。")
    seen = set()
    names = stage_names(model)
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
    n = len(stages)

    idx_a = conc_stage_index(model, "A")
    idx_b = conc_stage_index(model, "B")
    if not (0 <= idx_a < n):
        out.append("方法 A 的混凝土激活阶段不在阶段表里。")
    elif idx_a == 0:
        out.append("方法 A 的混凝土不能在第 1 个阶段（架设空钢管）就参与受力。")
    if not (0 <= idx_b < n):
        out.append("方法 B 的混凝土激活阶段不在阶段表里。")
    elif idx_b == 0:
        out.append("方法 B 的混凝土不能在第 1 个阶段（架设空钢管）就激活。")
    if 0 <= idx_a < n and 0 <= idx_b < n and idx_a <= idx_b:
        out.append("方法 A 的激活阶段要晚于方法 B（A=硬化后参与，B=灌注时激活）。")
    try:
        if conc_age(model, "A") < 0:
            out.append("方法 A 的混凝土材龄不能是负数。")
        if conc_age(model, "B") < 0:
            out.append("方法 B 的混凝土材龄不能是负数。")
    except (TypeError, ValueError):
        out.append("混凝土材龄要填数字（天）。")
    q2 = (model.get("geom") or {}).get("q2", model.get("q2", 0))
    try:
        if float(q2 or 0) < 0:
            out.append("二期恒载不能是负数（不填或填 0 表示不加）。")
    except (TypeError, ValueError):
        out.append("二期恒载要填数字（kN/m），不需要就填 0。")
    return out


def issues(model, sid):
    if sid in ("cfst2_geom", "geom"):
        return geom_issues(model)
    if sid in ("cfst2_section", "section"):
        return section_issues(model)
    if sid in ("cfst2_stages", "stages"):
        return stage_issues(model)
    return []


def all_issues(model):
    out = []
    for fn, title in ((geom_issues, "拱轴与几何"), (section_issues, "钢管混凝土截面"),
                      (stage_issues, "施工阶段")):
        for msg in fn(model):
            out.append("「%s」%s" % (title, msg))
    return out


# ----------------------------------------------------------------------
# 数据表 / 对比说明
# ----------------------------------------------------------------------
def data_report(model):
    """人可读数据表（生成成果时写成 *_数据表.txt）。"""
    gp = build_geometry(model)
    g = model.get("geom") or {}
    tb = model.get("tube") or {}
    sm = summary(model)
    proj = model.get("project") or {}
    names = stage_names(model)
    q2 = q2_value(model)
    wet = wet_q(model)
    L = []
    L.append("两跨钢管混凝土拱 · 施工顺序 + 湿重两种模拟对比 数据表")
    L.append("=" * 72)
    L.append("项目名称：%s" % (proj.get("name") or ""))
    L.append("")
    L.append("一、拱轴与几何")
    L.append("  拱轴类型：%s    两跨连接方式：%s"
             % (ARCH_TYPE_NAMES.get(gp["type"], ""),
                SPAN_JOINT_NAMES.get(gp["joint"], "")))
    L.append("  每跨计算跨径 L = %.3f m    计算矢高 f = %.3f m    矢跨比 1/%.2f"
             % (gp["L"], gp["f"], (gp["L"] / gp["f"]) if gp["f"] else 0.0))
    if gp["type"] == "catenary":
        L.append("  拱轴系数 m = %s" % g.get("m"))
    L.append("  每跨单元数 n = %d    总节点数 = %d    总单元数 = %d"
             % (gp["n"], gp["n_nodes"], gp["n_elems"]))
    L.append("  两跨拱脚 x = 0 / %.3f / %.3f m；拱脚节点（固结）：%s"
             % (gp["L"], 2.0 * gp["L"],
                " / ".join(str(x) for x in gp["supports"])))
    if gp["share"]:
        L.append("  中间拱脚（x=%.3f m）两跨共用节点 %d。" % (gp["L"], gp["mid_nodes"][0]))
    else:
        L.append("  中间拱脚（x=%.3f m）两跨各一个节点：%s（两跨独立）。"
                 % (gp["L"], " / ".join(str(x) for x in gp["mid_nodes"])))
    L.append("  单跨拱轴线长 = %.3f m    两跨合计 = %.3f m"
             % (gp["arch_len"], 2.0 * gp["arch_len"]))
    L.append("")
    L.append("二、钢管混凝土截面")
    L.append("  钢管：外径 D = %s mm  壁厚 t = %s mm  牌号 %s"
             % (tb.get("D_mm"), tb.get("t_mm"), tb.get("steel")))
    L.append("        A = %.4f m2   Iyy = %.6f m4   Izz = %.6f m4"
             % (sm["A_steel"], sm["Iyy_steel"], sm["Izz_steel"]))
    L.append("  核心混凝土：%s   A = %.4f m2   Iyy = %.6f m4   Izz = %.6f m4"
             % (cf.model_conc(core_model(model, "A")), sm["A_conc"],
                sm["Iyy_conc"], sm["Izz_conc"]))
    L.append("  组合截面：A = %.4f m2   Iyy = %.6f m4   Izz = %.6f m4"
             % (sm["A"], sm["Iyy"], sm["Izz"]))
    L.append("  含钢率 = %.2f %%    组合截面高 = %.3f m"
             % (sm["steel_ratio"] * 100.0, sm["H"]))
    L.append("  单位长度自重：钢管 %.2f + 混凝土 %.2f = %.2f kN/m"
             % (sm["w_steel"], sm["w_conc"], sm["w_total"]))
    L.append("  管内混凝土湿重 q = %.3f kN/m（= %.4f m2 × %.1f kN/m3）"
             % (wet, sm["A_conc"], _f(model.get("gamma"), GAMMA_CONC)))
    L.append("")
    L.append("三、施工阶段（两跨同时施工）")
    L.append("  %-8s %-6s %-34s %s" % ("阶段", "天数", "这一步做什么", "两法差异"))
    for i, st in enumerate(model.get("stages") or []):
        nm = names[i] if i < len(names) else str(st.get("name") or "")
        notes = []
        if i == conc_stage_index(model, "A") - 1:
            notes.append("方法A：加湿重 %.2f kN/m" % wet)
        if i == conc_stage_index(model, "B"):
            notes.append("方法B：混凝土 Part 激活（材龄 %.0f d）" % conc_age(model, "B"))
        if i == conc_stage_index(model, "A"):
            notes.append("方法A：卸湿重 + Part 激活（材龄 %.0f d）" % conc_age(model, "A"))
        if q2 > 0 and i == len(names) - 1:
            notes.append("加二期恒载 %.2f kN/m" % q2)
        L.append("  %-8s %-6s %-34s %s" % (nm, st.get("days", ""), st.get("desc") or "",
                                           "；".join(notes)))
    L.append("")
    L.append("四、湿重两种模拟方式")
    L.append("  方法 A（湿重作梁单元荷载）：")
    L.append("    · 混凝土 Part 在「%s」激活、材龄 %.0f d（硬化后才参与刚度）；"
             % (conc_stage_name(model, "A"), conc_age(model, "A")))
    L.append("    · 「%s」在两跨全部 %d 个钢单元上加 %s 均布荷载 q = %.3f kN/m；"
             % (wet_stage_name(model), gp["n_elems"], WET_GROUP, wet))
    L.append("    · 「%s」用 DLOAD 卸掉 %s，同一阶段混凝土 Part 参与受力。"
             % (conc_stage_name(model, "A"), WET_GROUP))
    L.append("  方法 B（材龄 0 d，湿重 = 混凝土单元自重）：")
    L.append("    · 混凝土 Part 在「%s」激活、材龄 %.0f d（MIDAS 中材龄 0 表示流动/湿重状态："
             % (conc_stage_name(model, "B"), conc_age(model, "B")))
    L.append("      只计重量、不计刚度）；")
    L.append("    · 不设湿重荷载工况，湿重 = 该 Part 的自重，由 *SELFWEIGHT 随 Part 激活自动计入；")
    L.append("    · 「%s」起材龄随时段增长，混凝土逐渐参与刚度。" % conc_stage_name(model, "A"))
    L.append("")
    L.append("五、说明")
    L.append("  1) 两套 MCT 的几何、材料、基础截面（*SECT-PSCVALUE）完全一样，差别只在：")
    L.append("     *CPOSECT4CS 第 2 部件（核心混凝土）的「叠合阶段 / 材龄」，以及方法 A 多出来的")
    L.append("     %s 工况（*STLDCASE / *LOAD-GROUP / *BEAMLOAD / *STAGE 的 ALOAD、DLOAD）。" % WET_GROUP)
    L.append("  2) 两法在灌注阶段给结构加的总重量相同（都是核心混凝土面积 × 容重），")
    L.append("     可以直接比较同一施工阶段的位移/内力，差别只来自湿重的模拟方式。")
    L.append("  3) 方法是「同一模型 → 两套 MCT」，不是两个不同结构；先各开一个 MIDAS 文档分别导入。")
    return "\r\n".join(L) + "\r\n"


def comparison_text(model):
    """两法差异 + 怎么在 MIDAS 里看对比结果（生成 *_对比说明.txt）。"""
    gp = build_geometry(model)
    sm = summary(model)
    g = model.get("geom") or {}
    names = stage_names(model)
    q2 = q2_value(model)
    wet = wet_q(model)
    idx_a = conc_stage_index(model, "A")
    idx_b = conc_stage_index(model, "B")
    name_a = conc_stage_name(model, "A")
    name_b = conc_stage_name(model, "B")
    age_a = conc_age(model, "A")
    age_b = conc_age(model, "B")
    L = []
    L.append("两跨钢管混凝土拱 · 湿重两种模拟方式 对比说明")
    L.append("=" * 72)
    L.append("")
    L.append("一、模型概况")
    L.append("  两跨拱：每跨 L=%.1f m、f=%.1f m、%s，每跨 %d 个单元，%s；"
             % (gp["L"], gp["f"], ARCH_TYPE_NAMES.get(gp["type"], ""), gp["n"],
                SPAN_JOINT_NAMES.get(gp["joint"], "")))
    L.append("  截面：钢管 φ%s×%s mm（%s）+ 核心 %s；管内混凝土容重 %.1f kN/m3。"
             % ((model.get("tube") or {}).get("D_mm"), (model.get("tube") or {}).get("t_mm"),
                (model.get("tube") or {}).get("steel"), (model.get("tube") or {}).get("conc"),
                _f(model.get("gamma"), GAMMA_CONC)))
    L.append("  施工阶段：%s。"
             % " → ".join("%s(%s d)" % (s.get("name"), s.get("days"))
                         for s in (model.get("stages") or [])))
    L.append("  二期恒载 q2 = %s kN/m（加在 %s）。" % (_num(q2, 6), names[-1] if names else ""))
    L.append("")
    L.append("二、两套 MCT 的真实差别（逐条对应 MCT 里的命令）")
    L.append("  1) *CPOSECT4CS 第 2 个部件（核心混凝土）：")
    L.append("     方法 A：叠合阶段 = %s，材龄 = %.0f d；" % (name_a, age_a))
    L.append("        —— %s 之前管内混凝土只以湿重存在，到 %s 才变成刚度。"
             % (name_a, name_a))
    L.append("     方法 B：叠合阶段 = %s，材龄 = %.0f d；" % (name_b, age_b))
    L.append("        —— MIDAS 里材龄 0 表示混凝土处于流动（湿重）状态：只计重量、不计刚度，")
    L.append("           所以混凝土 Part 的「重量」从 %s 起就自动计入，刚度则随时段增长。"
             % name_b)
    L.append("     （第 1 个部件「钢管」两法完全一样：ELEM、材料留空、CSTAGE 留空、AGE=0。）")
    L.append("  2) *STLDCASE / *LOAD-GROUP / *BEAMLOAD：")
    L.append("     方法 A：多一个 %s 工况，对两跨全部 %d 个单元逐行加湿重均布荷载 q = %.3f kN/m；"
             % (WET_GROUP, gp["n_elems"], wet))
    L.append("             这个重量 = 核心混凝土面积 %.4f m2 × 容重 %.1f kN/m3。" % (sm["A_conc"], _f(model.get("gamma"), GAMMA_CONC)))
    L.append("     方法 B：没有 %s 工况，湿重就是混凝土 Part 的自重（*SELFWEIGHT 里自动含）。" % WET_GROUP)
    L.append("  3) *STAGE：")
    L.append("     方法 A：%s 有「ALOAD=%s, FIRST」，%s 有「DLOAD=%s, FIRST」（先加后卸）。"
             % (names[idx_a - 1] if 0 <= idx_a - 1 < len(names) else "",
                WET_GROUP, name_a, WET_GROUP))
    L.append("     方法 B：%s、%s 这两个阶段只有「NAME=…, 天数」一行，不加也不卸任何荷载"
             % (name_b, name_a))
    L.append("             —— 因为混凝土自重跟着 Part 自动进、自动出。")
    L.append("  4) 其余命令（*NODE / *ELEMENT / *GROUP / *MATERIAL / *SECT-PSCVALUE /")
    L.append("     *CONSTRAINT / *SELFWEIGHT / 二期恒载 %s / *STAGE-CTRL …）两法完全一样，"
             % (Q2_GROUP if q2 > 0 else "（无）"))
    L.append("     所以两法结果的差别只来自湿重的模拟方式。")
    L.append("")
    L.append("三、怎么在 MIDAS Civil NX 里看两法对比")
    L.append("  1) 打开 MIDAS Civil NX →「文件 → 新建项目」，先导入方法 A 的 MCT：")
    L.append("     「文件 → 导入 → MCT 命令文件」，选 *_方法A_湿重作荷载.mct；")
    L.append("     再「文件 → 新建项目」开第二个文档，导入 *_方法B_材龄0d.mct。")
    L.append("     （MIDAS 一次只能打开一个模型，所以两个 MCT 各开一个文档，分别分析。）")
    L.append("  2) 两个文档都打开「分析 → 运行分析」，跑完施工阶段分析。")
    L.append("  3) 看施工阶段位移：结果 → 变形 → 变形形状，荷载工况选「CS: 合计」，")
    L.append("     阶段选 CS2 / CS3 / CS4，对比两种方法在拱顶（跨中节点）的竖向位移。")
    L.append("  4) 看联合截面各部分的应力：结果 → 结果表格 → 施工阶段联合截面，")
    L.append("     可以分别看 Part 1（钢管）与 Part 2（核心混凝土）的轴力/应力；")
    L.append("     方法 A 在 CS2 阶段混凝土还没有刚度（只有湿重），方法 B 在 CS2 阶段")
    L.append("     混凝土是材龄 0 的流动状态，两者 CS2 的钢管应力差别就是湿重模拟的影响。")
    L.append("  5) 看反力/总重量：结果 → 反力 → 反力表格，选 CS2「合计」，")
    L.append("     两法的支座反力总和应该基本相同（都等于 钢管自重 + 湿混凝土重），")
    L.append("     如果差很多，先检查方法 A 的 %s 是否在 %s 正确卸掉（DLOAD）。"
             % (WET_GROUP, name_a))
    L.append("  6) 看二期恒载后（CS4）的内力：结果 → 内力 → 梁单元内力图，")
    L.append("     看拱顶/拱脚弯矩包络，对比两种湿重假设对成桥内力的影响量级。")
    L.append("")
    L.append("四、两法各自适用 / 注意事项")
    L.append("  · 方法 A（湿重作荷载）是「教科书标准做法」：灌注阶段混凝土只当荷载，")
    L.append("    硬化阶段才把 Part 激活并卸掉湿重，重量与刚度都不重复，最稳，推荐交作业/出图用。")
    L.append("  · 方法 B（材龄 0 d）对应第五讲「管内混凝土灌注时材龄为 0，表示湿重」的做法：")
    L.append("    依赖 MIDAS 施工阶段联合截面「Part 激活即计入该部分自重」的行为；")
    L.append("    优点是湿重与 Part 一一对应，不会漏加或重复加，灌注顺序改了也好维护。")
    L.append("  · 两法在灌注阶段（%s）加的总重量应当相同，差别应该在「刚度何时参与」上，"
             % name_b)
    L.append("    所以 CS2 阶段的位移/钢管应力会有可见差别，CS4 成桥阶段差别会小一些。")
    L.append("  · 如果方法 B 算出来 CS2 的反力里没有混凝土重量，说明该版本 MIDAS 没有把")
    L.append("    Part 自重自动计入，此时改用方法 A 的模型即可（或把单元材料换成组合材料）。")
    L.append("  · 对比时注意：两个文档的材料、截面、荷载、阶段天数必须完全一致（本向导已保证），")
    L.append("    并且都用同一单位制（KN / M）。")
    return "\r\n".join(L) + "\r\n"
