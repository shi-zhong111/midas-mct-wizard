# -*- coding: utf-8 -*-
r"""平面刚架（自由建模）· 纯计算引擎 + MCT 生成（frame_core.py）。

本文件不依赖 tkinter / numpy，测试可以直接 import 调用：

    import frame_core as fc
    m = fc.default_model()          # 默认模型 = 习题 8(a)
    t = fc.build_mct(m)             # MIDAS Civil NX 的 MCT 文本（写文件用 GBK）
    sol = fc.solve(m)               # 矩阵位移法求解（杆端力 / 支反力 / 平衡校核）
    print(fc.result_report(m))      # 人可读的求解结果表
    fc.write_outputs(m, outdir)     # 写 4 个成果文件

理论（《桥梁结构电算》第 02 讲「矩阵位移法」）
--------------------------------------------------------------------------
* 每个节点 3 个自由度 (u, v, θ)，平面梁单元 6×6 刚度矩阵；
* 局部坐标：x' 由 i 端指向 j 端，y' = x' 逆时针转 90°（y' = (-sinα, cosα)）；
* 单元刚度矩阵按 6×6 平面梁单元：轴向 EA/L、抗弯 12EI/L^3 / 6EI/L^2 / 4EI/L / 2EI/L；
* 杆端力：{fl} = [k]{δ_local} - {feq}（feq = 分布荷载的等效节点荷载，
  也就是课程里的 {p} = [k]{δ} + {p_固端}）；
* 忽略轴向变形：EA 取「相对抗弯刚度足够大」的值 EA = 1e10·EI/L^2。
  杆端力与 EI 的绝对值无关（只与各杆 EI 的比值有关）——tests/test_frame.py 里验证过；
* 杆端释放（铰接）用**单元级静力凝聚**：把释放掉的转角自由度从单元刚度/等效荷载里凝聚掉，
  恢复杆端力时再代回去，因此释放端的弯矩恰为 0，不需要另加节点；
* 线性分布荷载用 **3 点 Gauss 积分**求等效节点荷载（三角形分布、两端不等都处理）；
* 支承用**行列删除**；两端全释放的节点的转角是「悬空自由度」（刚度为 0），直接取 0；
* 支反力 = [K]{U} - {F}；整体平衡（ΣFx / ΣFy / ΣM）自动校核。

输出约定（写清楚，免得后人猜）
--------------------------------------------------------------------------
    fl = [N_i', Q_i, M_i, N_j', Q_j, M_j]   （局部坐标下的杆端力向量）
    N    = -fl[0]                    轴力，**受拉为正**
    Q_i  =  fl[1],  Q_j = fl[4]      剪力，沿局部 y' 正向为正
    M_i  =  fl[2],  M_j = fl[5]      杆端弯矩（杆端力向量分量），**逆时针为正**
    M_i^图 = -fl[2], M_j^图 = +fl[5] 画弯矩图用，局部 y' 那一侧受拉为正
    「受拉侧」按 M^图 的符号 + 杆件方位给出（水平杆是上缘/下缘，竖杆是左侧/右侧）

MCT 约定（MIDAS Civil NX）
--------------------------------------------------------------------------
* 平面刚架建在**全局 X-Z 平面**（Z 向上，和 MIDAS 的重力方向一致，也与本程序其他流程一致）：
  向导里的 (x, y) → MCT 的 (X, Y, Z) = (x, 0, y)；向导 y 是竖向。
* 面内自由度：DX / DZ / RY。支座按用户勾的 u/v/θ 约束 DX/DZ/RY，
  同时把**平面外**自由度 DY/RX/RZ 一并约束（否则 2D 模型在 MIDAS 里出平面奇异）。
* 铰接用 *FRAME-RLS（Beam End Release）释放**局部 y 方向弯矩**：
  平面刚架建在 X-Z 平面，面内弯曲就是绕局部 y 轴（MIDAS 单元的局部 y 轴落在全局 X-Y 平面内）。
  *FRAME-RLS 按 MIDAS 手册的 2 行格式：
      1st line: ELEM_LIST, FLAG-i, Fxi, Fyi, Fzi, Mxi, Myi, Mzi
      2nd line:           FLAG-j, Fxj, Fyj, Fzj, Mxj, Myj, Mzj, GROUP
  FLAG 是 6 位 0/1（1 = 释放，顺序同 Fx..Mz）；6 个数值是部分释放系数，全填 0（完全释放）。
  注：_ref/pistoia.txt 里 midas Gen 的真实导出在第 1 行 ELEM_LIST 后还多一列 bVALUE(YES/NO)，
  若某个 MIDAS 版本导入报错，在 ELEM_LIST 后面补一个 "NO," 即可（见 RLS_BVALUE 开关）。
* 节点弯矩走 *CONLOAD 的 MX/MY/MZ 位（手册里 *CONLOAD 就是「节点集中力 + 集中弯矩」，
  没有独立的 *CONMOM 命令），面内弯矩（向导 Mz）→ MCT 的 MY。
"""

import datetime
import json
import math
import os

CODE = "frame"
LABEL = "平面刚架（自由建模·矩阵位移法）"

# 忽略轴向变形：EA = EA_FACTOR * EI / L^2。取 1e10 时轴向刚度比抗弯刚度大约 1e10 倍，
# 计算结果与「EA=∞」的解析解相差 < 1e-6（而 EI 的绝对值不影响杆端力）。
EA_FACTOR = 1.0e10
# 默认材料（C40 混凝土，kN/m^2 = kPa）
E_DEFAULT = 3.25e7
GAMMA_DEFAULT = 25.0
NU_DEFAULT = 0.2
ALPHA_DEFAULT = 1.0e-5
# *FRAME-RLS 第 1 行 ELEM_LIST 后面要不要补 bVALUE(YES/NO)：
# MIDAS Civil 手册的格式没有这一列，midas Gen 的真实导出有。默认按手册（不补）。
RLS_BVALUE = ""

TOL = 1e-9
_DIR_NAMES = {"x": "整体X（水平）", "y": "整体Y（竖直）"}
_DIR_BACK = {v: k for k, v in _DIR_NAMES.items()}


# ======================================================================
# 小工具
# ======================================================================
def _f(v, default=0.0):
    """把界面里的字符串转成 float；空串/非数字返回 default。"""
    try:
        s = str(v).strip().replace(",", "")
        if not s:
            return float(default)
        return float(s)
    except (TypeError, ValueError):
        return float(default)


def _is_num(v):
    try:
        s = str(v).strip()
        return bool(s) and _is_float(s)
    except (TypeError, ValueError):
        return False


def _is_float(s):
    try:
        float(s)
        return True
    except (TypeError, ValueError):
        return False


def _int(v, default=None):
    try:
        f = float(str(v).strip())
    except (TypeError, ValueError):
        return default
    if abs(f - round(f)) > 1e-9:
        return default
    return int(round(f))


def _fmt(v, nd=6):
    """数值 → MCT/文本里的紧凑写法（整数不带小数点）。"""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return "0"
    if f != f or f in (float("inf"), float("-inf")):
        return "0"
    if abs(f) < 1e-12:
        return "0"
    if abs(f - round(f)) < 1e-12 and abs(f) < 1e15:
        return str(int(round(f)))
    return "%.*g" % (nd, f)


def _aname(v, fallback=""):
    s = str(v or "").strip()
    for ch in '\\/:*?"<>|,':
        s = s.replace(ch, "-")
    return s or fallback


def _safe_name(s, fallback="平面刚架"):
    s = str(s or "").strip()
    for ch in '\\/:*?"<>|':
        s = s.replace(ch, "-")
    s = s.strip().strip(".")
    return s or fallback


def _ranges(ids):
    """节点/单元编号列表 → MIDAS 的 '1to3 5 7to9' 写法。"""
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


# ======================================================================
# 模型：默认 = 习题 8(a)；预设 = 习题 8(b)（铰接 / 刚接对拍）
# ======================================================================
def _sec_default():
    return {"name": "柱梁 0.4×0.6", "shape": "SB", "dims": ["0.6", "0.4"],
            "offset": "CC", "shear": "YES", "warp": "NO"}


def _node(no, x, y):
    return {"no": str(no), "x": _fmt(x), "y": _fmt(y)}


def _elem(no, i, j, sec=1, label="", ri=False, rj=False):
    return {"no": str(no), "i": str(i), "j": str(j), "sec": str(sec),
            "label": label, "ri": bool(ri), "rj": bool(rj)}


def _sup(node, u=True, v=True, r=True):
    return {"node": str(node), "u": bool(u), "v": bool(v), "r": bool(r)}


def _nload(node, fx=0.0, fy=0.0, mz=0.0):
    return {"node": str(node), "fx": _fmt(fx), "fy": _fmt(fy), "mz": _fmt(mz)}


def _dload(elem, dir="y", q1=0.0, q2=None):
    return {"elem": str(elem), "dir": dir, "q1": _fmt(q1),
            "q2": _fmt(q1 if q2 is None else q2)}


def preset_8a():
    """习题 8(a)（= 默认模型）：
    节点 1(0,0) 2(0,10) 3(10,10) 4(10,0) 5(10,5)；
    杆件 柱1:1-2、梁:2-3、柱2上:3-5、柱2下:5-4；
    支承 1 固定(u,v,θ)、4 只约束 v；
    荷载 梁 2-3 均布 10 kN/m 向下；柱1 三角形水平荷载 0(底)→+10(顶) kN/m（整体 x 向）；
         节点 5 集中力 Fx = -100 kN。
    """
    return {
        "flow": CODE,
        "project": {"name": "平面刚架_习题8a", "outdir": "",
                    "force": "KN", "dist": "M", "styp": "0"},
        "nodes": [_node(1, 0, 0), _node(2, 0, 10), _node(3, 10, 10),
                  _node(4, 10, 0), _node(5, 10, 5)],
        "elems": [_elem(1, 1, 2, 1, "柱1"),
                  _elem(2, 2, 3, 1, "梁"),
                  _elem(3, 3, 5, 1, "柱2上"),
                  _elem(4, 5, 4, 1, "柱2下")],
        "sections": [_sec_default()],
        "mat": {"grade": "C40", "E": _fmt(E_DEFAULT), "nu": _fmt(NU_DEFAULT),
                "gamma": _fmt(GAMMA_DEFAULT)},
        "supports": [_sup(1, True, True, True), _sup(4, False, True, False)],
        "nloads": [_nload(5, fx=-100.0)],
        "dloads": [_dload(2, "y", -10.0),          # 梁 2-3 均布 10 kN/m 向下
                   _dload(1, "x", 0.0, 10.0)],      # 柱1 三角形 0(底) → +10(顶)
    }


def preset_8b(hinge=True):
    """习题 8(b)：节点 1(0,10) 2(10,10) 5(0,0) 4(10,0) 6(5,10)；
    杆件 柱左:5-1、梁左:1-6、梁右:6-2、柱右:2-4；支承 5、4 固定；
    荷载 节点 6 Fy=-50 kN、节点 1 Mz=+30 kN·m；
    hinge=True 时节点 2 是铰：梁右 j 端 + 柱右 i 端释放弯矩（*FRAME-RLS）；
    hinge=False 是对拍用的「节点 2 刚接」模型。
    """
    return {
        "flow": CODE,
        "project": {"name": "平面刚架_习题8b" + ("_铰接" if hinge else "_刚接"), "outdir": "",
                    "force": "KN", "dist": "M", "styp": "0"},
        "nodes": [_node(1, 0, 10), _node(2, 10, 10), _node(4, 10, 0),
                  _node(5, 0, 0), _node(6, 5, 10)],
        "elems": [_elem(1, 5, 1, 1, "柱左"),
                  _elem(2, 1, 6, 1, "梁左"),
                  _elem(3, 6, 2, 1, "梁右", rj=bool(hinge)),
                  _elem(4, 2, 4, 1, "柱右", ri=bool(hinge))],
        "sections": [_sec_default()],
        "mat": {"grade": "C40", "E": _fmt(E_DEFAULT), "nu": _fmt(NU_DEFAULT),
                "gamma": _fmt(GAMMA_DEFAULT)},
        "supports": [_sup(5, True, True, True), _sup(4, True, True, True)],
        "nloads": [_nload(6, fy=-50.0), _nload(1, mz=30.0)],
        "dloads": [],
    }


def default_model():
    """默认模型 = 习题 8(a)（老师第 02 讲算例）。"""
    return preset_8a()


def reset_model():
    return default_model()


def blank_model():
    """「清空」后的空模型：表格为空，截面/材料还是默认值。"""
    return {
        "flow": CODE,
        "project": {"name": "平面刚架", "outdir": "",
                    "force": "KN", "dist": "M", "styp": "0"},
        "nodes": [_node(1, 0, 0)],
        "elems": [],
        "sections": [_sec_default()],
        "mat": {"grade": "C40", "E": _fmt(E_DEFAULT), "nu": _fmt(NU_DEFAULT),
                "gamma": _fmt(GAMMA_DEFAULT)},
        "supports": [],
        "nloads": [],
        "dloads": [],
    }


# ======================================================================
# 读模型（把界面里的字符串整理成数值；出错抛 ModelError）
# ======================================================================
class ModelError(ValueError):
    """模型数据有问题（给界面/报告显示中文原因用）。"""


def read_nodes(model):
    """[{no, x, y}]；没填编号的行跳过（界面上允许有空行）。"""
    out = []
    for k, r in enumerate(model.get("nodes") or [], start=1):
        no = _int(r.get("no"))
        if no is None:
            continue
        out.append({"no": no, "x": _f(r.get("x")), "y": _f(r.get("y")), "row": k})
    return out


def read_elems(model):
    out = []
    for k, r in enumerate(model.get("elems") or [], start=1):
        no = _int(r.get("no"))
        if no is None:
            continue
        out.append({"no": no, "ni": _int(r.get("i")), "nj": _int(r.get("j")),
                    "sec": _int(r.get("sec"), 1), "label": str(r.get("label") or "").strip(),
                    "ri": bool(r.get("ri")), "rj": bool(r.get("rj")), "row": k})
    return out


def read_supports(model):
    """{node: (u, v, r)}（同节点多条取「或」，校验时另行报重复）。"""
    out = {}
    for r in model.get("supports") or []:
        no = _int(r.get("node"))
        if no is None:
            continue
        u, v, rt = bool(r.get("u")), bool(r.get("v")), bool(r.get("r"))
        if no in out:
            a = out[no]
            out[no] = (a[0] or u, a[1] or v, a[2] or rt)
        else:
            out[no] = (u, v, rt)
    return out


def read_nloads(model):
    out = []
    for r in model.get("nloads") or []:
        no = _int(r.get("node"))
        if no is None:
            continue
        out.append((no, _f(r.get("fx")), _f(r.get("fy")), _f(r.get("mz"))))
    return out


def read_dloads(model):
    out = []
    for r in model.get("dloads") or []:
        no = _int(r.get("elem"))
        if no is None:
            continue
        d = str(r.get("dir") or "y").strip().lower()
        if d not in ("x", "y"):
            d = _DIR_BACK.get(str(r.get("dir") or "").strip(), "y")
        out.append({"elem": no, "dir": d, "q1": _f(r.get("q1")), "q2": _f(r.get("q2"))})
    return out


def read_sections(model):
    """截面列表（保持原顺序，编号 = 下标 + 1）。"""
    return [dict(s) for s in (model.get("sections") or [])]


def collect(model):
    """把模型整理成求解/MCT 都要用的结构；有硬错误就抛 ModelError。"""
    nodes = read_nodes(model)
    if not nodes:
        raise ModelError("还没有节点：先在第 1 步「节点表」里填节点。")
    node_ids = [n["no"] for n in nodes]
    if len(set(node_ids)) != len(node_ids):
        raise ModelError("节点编号有重复，改一下再算。")
    xy = {n["no"]: (n["x"], n["y"]) for n in nodes}

    elems = read_elems(model)
    if not elems:
        raise ModelError("还没有杆件：先在第 1 步「杆件表」里填杆件。")
    elem_ids = [e["no"] for e in elems]
    if len(set(elem_ids)) != len(elem_ids):
        raise ModelError("杆件编号有重复，改一下再算。")
    secs = read_sections(model)
    for e in elems:
        if e["ni"] is None or e["nj"] is None:
            raise ModelError("杆件 %d 的节点号没填全。" % e["no"])
        if e["ni"] not in xy or e["nj"] not in xy:
            raise ModelError("杆件 %d 引用了不存在的节点 %s/%s。"
                             % (e["no"], e["ni"], e["nj"]))
        if e["ni"] == e["nj"]:
            raise ModelError("杆件 %d 的 i 端和 j 端是同一个节点。" % e["no"])
        x1, y1 = xy[e["ni"]]
        x2, y2 = xy[e["nj"]]
        L = math.hypot(x2 - x1, y2 - y1)
        if L <= 1e-9:
            raise ModelError("杆件 %d 的长度是 0（两端节点坐标相同）。" % e["no"])
        e["L"] = L
        e["c"] = (x2 - x1) / L
        e["s"] = (y2 - y1) / L
        if not (1 <= e["sec"] <= len(secs)):
            raise ModelError("杆件 %d 的截面号 %d 不存在（现在有 %d 个截面）。"
                             % (e["no"], e["sec"], len(secs)))

    return {
        "nodes": nodes, "xy": xy, "node_ids": node_ids,
        "elems": elems, "elem_ids": elem_ids,
        "supports": read_supports(model),
        "nloads": read_nloads(model),
        "dloads": read_dloads(model),
        "sections": secs,
        "mat": dict(model.get("mat") or {}),
    }


# ======================================================================
# 截面 / 材料特性
# ======================================================================
def material_E(model):
    e = _f((model.get("mat") or {}).get("E"), 0.0)
    return e if e > 0 else E_DEFAULT


def material_gamma(model):
    g = _f((model.get("mat") or {}).get("gamma"), GAMMA_DEFAULT)
    return g if g >= 0 else 0.0


def material_nu(model):
    nu = _f((model.get("mat") or {}).get("nu"), NU_DEFAULT)
    return nu if 0.0 <= nu < 0.5 else NU_DEFAULT


def section_props(sec):
    """截面特性 {A, Iyy, Izz, kind}（m^2 / m^4）。

    面内抗弯惯矩用 Iyy = B·H^3/12（SB）/ πD^4/64（SR）；DXF 自定义截面用导入时算好的 props。
    非矩形/圆形（角钢、T 形等）按外包矩形近似 —— 只在「内置求解器」里用，
    写进 MCT 的是 MIDAS 自己的 DBUSER 截面，MIDAS 会算准确值。
    """
    code = str(sec.get("shape") or "SB").strip().upper()
    dims = [str(v) for v in (sec.get("dims") or [])]
    if code == "CAD":
        pr = sec.get("props") or {}
        A = _f(pr.get("A"), 0.0)
        iyy = _f(pr.get("Iyy"), 0.0)
        izz = _f(pr.get("Izz"), 0.0)
        return {"A": A, "Iyy": iyy, "Izz": izz, "kind": "CAD"}
    try:
        if code == "SR":
            D = _f(dims[0] if len(dims) > 0 else 0, 0.0)
            A = math.pi * D * D / 4.0
            i = math.pi * D ** 4 / 64.0
            return {"A": A, "Iyy": i, "Izz": i, "kind": "SR"}
        H = _f(dims[0] if len(dims) > 0 else 0, 0.0)
        B = _f(dims[1] if len(dims) > 1 else 0, 0.0)
        if H <= 0 or B <= 0:
            return {"A": 0.0, "Iyy": 0.0, "Izz": 0.0, "kind": code}
        return {"A": H * B, "Iyy": B * H ** 3 / 12.0, "Izz": H * B ** 3 / 12.0, "kind": code}
    except (IndexError, TypeError, ValueError):
        return {"A": 0.0, "Iyy": 0.0, "Izz": 0.0, "kind": code}


def elem_EI(model, sec_no):
    """单元的抗弯刚度 EI（kN·m^2）——杆端力只跟各杆 EI 的**比值**有关。"""
    secs = read_sections(model)
    if not (1 <= int(sec_no) <= len(secs)):
        return 0.0
    pr = section_props(secs[int(sec_no) - 1])
    return material_E(model) * pr["Iyy"]


def section_summary(sec):
    """一句话描述截面（数据表用）。"""
    code = str(sec.get("shape") or "SB").strip().upper()
    if code == "CAD":
        pr = sec.get("props") or {}
        return "DXF 自定义：A=%.4g m^2  Iyy=%.4g m^4" % (_f(pr.get("A")), _f(pr.get("Iyy")))
    names = {"SB": "实心矩形", "SR": "实心圆"}
    dims = [str(v) for v in (sec.get("dims") or [])]
    return "%s H=%s B=%s" % (names.get(code, code), dims[0] if dims else "?",
                             dims[1] if len(dims) > 1 else "-")


# ======================================================================
# 矩阵位移法求解器（纯 Python，无 numpy）
# ======================================================================
def _mat_vec(M, v):
    return [sum(M[i][j] * v[j] for j in range(len(v))) for i in range(len(M))]


def _zero(n):
    return [0.0] * n


def _k_local(EI, EA, L):
    """6×6 平面梁单元刚度矩阵（局部坐标）。"""
    k = [[0.0] * 6 for _ in range(6)]

    def setv(i, j, v):
        k[i][j] = v
        k[j][i] = v

    a = EA / L
    b = 12.0 * EI / L ** 3
    c = 6.0 * EI / L ** 2
    d = 4.0 * EI / L
    e = 2.0 * EI / L
    setv(0, 0, a); setv(0, 3, -a); setv(3, 3, a)
    setv(1, 1, b); setv(1, 4, -b); setv(4, 4, b)
    setv(1, 2, c); setv(2, 2, d); setv(2, 5, e); setv(5, 5, d)
    setv(2, 4, -c); setv(4, 5, -c); setv(2, 1, c); setv(1, 5, c)
    return k


def _trans(c, s):
    """局部 → 整体 的 6×6 变换矩阵（δ_local = T·δ_global）。"""
    return [[c, s, 0.0, 0.0, 0.0, 0.0],
            [-s, c, 0.0, 0.0, 0.0, 0.0],
            [0.0, 0.0, 1.0, 0.0, 0.0, 0.0],
            [0.0, 0.0, 0.0, c, s, 0.0],
            [0.0, 0.0, 0.0, -s, c, 0.0],
            [0.0, 0.0, 0.0, 0.0, 0.0, 1.0]]


_GAUSS = ((-math.sqrt(0.6), 5.0 / 9.0), (0.0, 8.0 / 9.0), (math.sqrt(0.6), 5.0 / 9.0))


def _feq_local(L, c, s, q1, q2):
    """线性分布荷载（整体坐标分量 q1/q2 = i/j 端的 (qx, qy) kN/m）的等效节点荷载。

    3 点 Gauss 积分：∫ N·q(x)·dx，可处理三角形/梯形分布。
    """
    out = _zero(6)
    for xg, wg in _GAUSS:
        xi = 0.5 * (xg + 1.0)
        wgt = 0.5 * wg * L
        qx = q1[0] + (q2[0] - q1[0]) * xi
        qy = q1[1] + (q2[1] - q1[1]) * xi
        qxl = c * qx + s * qy
        qyl = -s * qx + c * qy
        Nu0, Nu1 = 1.0 - xi, xi
        Nv0 = 1.0 - 3.0 * xi ** 2 + 2.0 * xi ** 3
        Nv1 = L * (xi - 2.0 * xi ** 2 + xi ** 3)
        Nv2 = 3.0 * xi ** 2 - 2.0 * xi ** 3
        Nv3 = L * (-xi ** 2 + xi ** 3)
        out[0] += wgt * Nu0 * qxl
        out[1] += wgt * Nv0 * qyl
        out[2] += wgt * Nv1 * qyl
        out[3] += wgt * Nu1 * qxl
        out[4] += wgt * Nv2 * qyl
        out[5] += wgt * Nv3 * qyl
    return out


def _inv_small(M):
    """1×1 / 2×2（杆端释放最多两个转角）显式求逆。"""
    n = len(M)
    if n == 1:
        if abs(M[0][0]) < 1e-300:
            raise ModelError("杆端释放的单元刚度是 0，检查杆件长度/截面。")
        return [[1.0 / M[0][0]]]
    if n == 2:
        a, b = M[0]
        cc, d = M[1]
        det = a * d - b * cc
        if abs(det) < 1e-300:
            raise ModelError("杆端释放的单元刚度奇异，检查杆件长度/截面。")
        return [[d / det, -b / det], [-cc / det, a / det]]
    raise ModelError("内部错误：杆端释放个数超过 2。")


def _condense(k, feq, rel):
    """单元级静力凝聚：把释放掉的局部自由度 rel（2=i端转角, 5=j端转角）凝聚掉。

    返回 (凝聚后的 6×6 刚度, 凝聚后的等效节点荷载, 恢复用的记录)。
    """
    rel = sorted(set(int(r) for r in rel))
    if not rel:
        return k, list(feq), None
    keep = [i for i in range(6) if i not in rel]
    kAA = [[k[a][b] for b in rel] for a in rel]
    kAB = [[k[a][b] for b in keep] for a in rel]
    kBA = [[k[b][a] for a in rel] for b in keep]
    kBB = [[k[a][b] for b in keep] for a in keep]
    invA = _inv_small(kAA)
    pr = [[sum(kBA[i][t] * invA[t][j] for t in range(len(rel))) for j in range(len(rel))]
          for i in range(len(keep))]
    kcc = [[kBB[i][j] - sum(pr[i][t] * kAB[t][j] for t in range(len(rel)))
            for j in range(len(keep))] for i in range(len(keep))]
    feqA = [feq[a] for a in rel]
    feqB = [feq[b] for b in keep]
    feqcB = [feqB[i] - sum(pr[i][t] * feqA[t] for t in range(len(rel)))
             for i in range(len(keep))]
    kc = [[0.0] * 6 for _ in range(6)]
    for i, a in enumerate(keep):
        for j, b in enumerate(keep):
            kc[a][b] = kcc[i][j]
    feqc = _zero(6)
    for i, a in enumerate(keep):
        feqc[a] = feqcB[i]
    rec = {"rel": rel, "keep": keep, "invA": invA, "kAB": kAB, "feq": list(feq)}
    return kc, feqc, rec


def _recover_dl(dl, rec):
    """恢复被释放的转角：由「释放端弯矩 = 0」代回去，{fl} = [k]{δ} - {feq}。"""
    if not rec:
        return list(dl)
    rel, keep = rec["rel"], rec["keep"]
    invA, kAB, feq = rec["invA"], rec["kAB"], rec["feq"]
    rhs = [feq[rel[i]] - sum(kAB[i][j] * dl[keep[j]] for j in range(len(keep)))
           for i in range(len(rel))]
    dA = [sum(invA[i][j] * rhs[j] for j in range(len(rel))) for i in range(len(rel))]
    out = list(dl)
    for i, a in enumerate(rel):
        out[a] = dA[i]
    return out


def _solve_linear(A, b, what="刚度矩阵"):
    """高斯消元（部分主元）；奇异时抛 ModelError。"""
    n = len(b)
    M = [list(A[i]) + [b[i]] for i in range(n)]
    amax = max((abs(v) for row in A for v in row), default=0.0) or 1.0
    for col in range(n):
        piv = max(range(col, n), key=lambda r: abs(M[r][col]))
        if abs(M[piv][col]) <= 1e-13 * amax:
            raise ModelError("%s奇异：结构是机构（约束不足或杆件没连上），检查支承和杆件连接。" % what)
        if piv != col:
            M[col], M[piv] = M[piv], M[col]
        pv = M[col][col]
        for r in range(col + 1, n):
            f = M[r][col] / pv
            if f == 0.0:
                continue
            for j in range(col, n + 1):
                M[r][j] -= f * M[col][j]
    x = [0.0] * n
    for i in range(n - 1, -1, -1):
        s = M[i][n] - sum(M[i][j] * x[j] for j in range(i + 1, n))
        x[i] = s / M[i][i]
    return x


def solve(model):
    """矩阵位移法求解。返回 dict：

    elements : {杆件号: {ni, nj, L, c, s, fl, N, Qi, Qj, Mi, Mj, Mi_sag, Mj_sag,
                        tension_i, tension_j, released_i, released_j}}
    order    : 杆件号顺序
    reactions: {节点号: (Rx, Ry, M)}
    moves    : {节点号: (u, v, θ)}
    balance  : {sum_load, sum_react, resid, ok, worst}
    info     : {nnode, nelem, ndof, nfree, nfix, ndangle, EI}
    """
    dat = collect(model)
    nodes = dat["nodes"]
    idx = {n["no"]: i for i, n in enumerate(nodes)}
    ndof = 3 * len(nodes)
    K = [[0.0] * ndof for _ in range(ndof)]
    F = _zero(ndof)
    E = material_E(model)

    elems = dat["elems"]
    by_no = {e["no"]: e for e in elems}
    # 分布荷载先按杆件归并（同一杆件可以有多条，含 x/y 两个方向）
    dq = {}
    for ld in dat["dloads"]:
        by_no.get(ld["elem"]) or (_ for _ in ()).throw(
            ModelError("分布荷载引用了不存在的杆件 %d。" % ld["elem"]))
        dq.setdefault(ld["elem"], []).append(ld)

    for e in elems:
        EI = elem_EI(model, e["sec"])
        if EI <= 0:
            raise ModelError("杆件 %d 的截面没有有效高度（EI=0），先在第 2 步填截面尺寸。"
                             % e["no"])
        L = e["L"]
        EA = EA_FACTOR * EI / (L * L)
        kl = _k_local(EI, EA, L)
        T = _trans(e["c"], e["s"])
        feq = _zero(6)
        for ld in dq.get(e["no"], []):
            if ld["dir"] == "x":
                q1, q2 = (ld["q1"], 0.0), (ld["q2"], 0.0)
            else:
                q1, q2 = (0.0, ld["q1"]), (0.0, ld["q2"])
            f6 = _feq_local(L, e["c"], e["s"], q1, q2)
            for i in range(6):
                feq[i] += f6[i]
        rel = []
        if e["ri"]:
            rel.append(2)
        if e["rj"]:
            rel.append(5)
        kc, feqc, rec = _condense(kl, feq, rel)
        e["_EI"] = EI
        e["_k"] = kl
        e["_T"] = T
        e["_feq"] = feq
        e["_rec"] = rec
        e["_gd"] = [3 * idx[e["ni"]], 3 * idx[e["ni"]] + 1, 3 * idx[e["ni"]] + 2,
                    3 * idx[e["nj"]], 3 * idx[e["nj"]] + 1, 3 * idx[e["nj"]] + 2]
        # 整体刚度：kc 在局部，kg = T^T kc T（δ_local = T·δ_global）
        ktg = [[sum(T[i][a] * kc[i][j] * T[j][b] for i in range(6) for j in range(6))
                for b in range(6)] for a in range(6)]
        for a in range(6):
            for b in range(6):
                K[e["_gd"][a]][e["_gd"][b]] += ktg[a][b]
        fg = [sum(T[i][a] * feqc[i] for i in range(6)) for a in range(6)]
        for a in range(6):
            F[e["_gd"][a]] += fg[a]

    for (no, fx, fy, mz) in dat["nloads"]:
        if no not in idx:
            raise ModelError("节点荷载引用了不存在的节点 %d。" % no)
        i0 = 3 * idx[no]
        F[i0] += fx
        F[i0 + 1] += fy
        F[i0 + 2] += mz

    # ---- 支承 ----
    fixed = set()
    for no, (u, v, r) in dat["supports"].items():
        if no not in idx:
            raise ModelError("支承引用了不存在的节点 %d。" % no)
        i0 = 3 * idx[no]
        if u:
            fixed.add(i0)
        if v:
            fixed.add(i0 + 1)
        if r:
            fixed.add(i0 + 2)

    # ---- 悬空自由度（两端全释放的节点转角）----
    scale = max((abs(v) for row in K for v in row), default=0.0) or 1.0
    dangle = set()
    for i in range(ndof):
        if i in fixed:
            continue
        rmax = max((abs(K[i][j]) for j in range(ndof)), default=0.0)
        cmax = max((abs(K[j][i]) for j in range(ndof)), default=0.0)
        if rmax <= 1e-12 * scale and cmax <= 1e-12 * scale:
            if abs(F[i]) > 1e-9 * scale:
                raise ModelError(
                    "节点 %d 的转角没有任何杆件约束（两端都铰接），那里不能再加节点弯矩。"
                    % nodes[i // 3]["no"])
            dangle.add(i)

    free = [i for i in range(ndof) if i not in fixed and i not in dangle]
    ni = {d: k for k, d in enumerate(free)}
    nf = len(free)
    Kr = [[K[a][b] for b in free] for a in free]
    Fr = [F[a] for a in free]
    Ur = _solve_linear(Kr, Fr)
    U = _zero(ndof)
    for d, k in ni.items():
        U[d] = Ur[k]

    # ---- 杆端力 ----
    res = {}
    order = []
    for e in elems:
        dg = [U[g] for g in e["_gd"]]
        dl = _mat_vec(e["_T"], dg)
        dl = _recover_dl(dl, e["_rec"])
        fl = _mat_vec(e["_k"], dl)
        for i in range(6):
            fl[i] -= e["_feq"][i]
        L = e["L"]
        Mi_sag = -fl[2]
        Mj_sag = fl[5]
        res[e["no"]] = {
            "no": e["no"], "ni": e["ni"], "nj": e["nj"], "label": e["label"],
            "L": L, "c": e["c"], "s": e["s"], "EI": e["_EI"],
            "fl": fl,
            "N": -fl[0], "Qi": fl[1], "Qj": fl[4],
            "Mi": fl[2], "Mj": fl[5],
            "Mi_sag": Mi_sag, "Mj_sag": Mj_sag,
            "tension_i": tension_side(e["c"], e["s"], Mi_sag),
            "tension_j": tension_side(e["c"], e["s"], Mj_sag),
            "released_i": bool(e["ri"]), "released_j": bool(e["rj"]),
        }
        order.append(e["no"])

    # ---- 支反力 = K U - F ----
    KU = _mat_vec(K, U)
    R = [KU[i] - F[i] for i in range(ndof)]
    reactions = {}
    moves = {}
    for n in nodes:
        i0 = 3 * idx[n["no"]]
        reactions[n["no"]] = (R[i0], R[i0 + 1], R[i0 + 2])
        moves[n["no"]] = (U[i0], U[i0 + 1], U[i0 + 2])

    # ---- 整体平衡校核（用等效节点荷载的合力，分布荷载的合力/合力矩是精确的）----
    def resultants(vec):
        sx = sy = sm = 0.0
        for n in nodes:
            i0 = 3 * idx[n["no"]]
            fx, fy, mz = vec[i0], vec[i0 + 1], vec[i0 + 2]
            sx += fx
            sy += fy
            sm += n["x"] * fy - n["y"] * fx + mz
        return (sx, sy, sm)

    sum_load = resultants(F)
    sum_react = resultants(R)
    resid = tuple(sum_load[i] + sum_react[i] for i in range(3))
    xs = [n["x"] for n in nodes] or [0.0]
    ys = [n["y"] for n in nodes] or [0.0]
    lchar = max(max(xs) - min(xs), max(ys) - min(ys), 1.0)
    fscale = max(abs(sum_load[0]), abs(sum_load[1]), 1.0)
    mscale = max(abs(sum_load[2]), 1.0)
    worst = max(abs(resid[0]), abs(resid[1]), abs(resid[2]))
    # 残差按「力的量级 × 结构尺寸」相对判断（EA 取得很大时矩阵条件数 ~1e9，绝对残差 ~1e-5 属正常）
    thr = 1e-6 * max(fscale * lchar, mscale, 1.0)
    balance = {"sum_load": sum_load, "sum_react": sum_react, "resid": resid,
               "worst": worst, "thr": thr, "ok": worst <= thr}

    return {
        "elements": res, "order": order,
        "reactions": reactions, "moves": moves,
        "balance": balance,
        "info": {"nnode": len(nodes), "nelem": len(elems), "ndof": ndof,
                 "nfree": nf, "nfix": len(fixed), "ndangle": len(dangle),
                 "E": E},
    }


def tension_side(c, s, m_sag, tol=1e-6):
    """受拉侧中文说明：M^图 > 0 表示局部 y' 那一侧受拉。"""
    if abs(m_sag) < tol:
        return "无"
    yx, yy = -s, c          # 局部 y' 在整体坐标里的方向
    if m_sag < 0:
        yx, yy = -yx, -yy
    if abs(yy) >= 0.85:
        return "上缘" if yy > 0 else "下缘"
    if abs(yx) >= 0.85:
        return "右侧" if yx > 0 else "左侧"
    return "y'正向侧" if m_sag > 0 else "y'负向侧"


# ======================================================================
# 结果表 / 数据表
# ======================================================================
def _elem_name(e):
    lab = str(e.get("label") or "").strip()
    return ("%d(%s)" % (e["no"], lab)) if lab else str(e["no"])


def result_report(model):
    """人可读的「矩阵位移法结果」：杆端力表 + 支反力 + 平衡校核。"""
    prj = model.get("project") or {}
    L = []
    L.append("平面刚架 · 矩阵位移法求解结果")
    L.append("=" * 96)
    L.append("项目名称：%s" % (prj.get("name") or ""))
    L.append("")
    try:
        sol = solve(model)
    except (ModelError, ValueError) as exc:
        L.append("算不了：%s" % exc)
        return "\r\n".join(L) + "\r\n"

    dat = collect(model)
    info = sol["info"]
    secs = dat["sections"]
    E = material_E(model)
    L.append("单位：kN, m        材料 E = %s kN/m^2（= kPa）" % _fmt(E, 8))
    for i, sec in enumerate(secs, start=1):
        pr = section_props(sec)
        L.append("  截面%d：%-16s A=%s m^2  Iyy=%s m^4  EI=%s kN·m^2"
                 % (i, section_summary(sec), _fmt(pr["A"], 6), _fmt(pr["Iyy"], 6),
                    _fmt(E * pr["Iyy"], 8)))
    L.append("  模型：节点 %d 个、杆件 %d 个、节点荷载 %d 条、分布荷载 %d 条；"
             "自由度 %d 个（约束 %d、悬空 %d、参与求解 %d）"
             % (info["nnode"], info["nelem"], len(dat["nloads"]), len(dat["dloads"]),
                info["ndof"], info["nfix"], info["ndangle"], info["nfree"]))
    L.append("  说明：杆端力与 EI 的**绝对值**无关（只与各杆 EI 的比值有关），"
             "所以改变截面尺寸不会改变内力分配（同截面模型）。")
    L.append("")
    L.append("一、约定（写清楚，免得对不上）")
    L.append("  局部坐标：x' 由 i 端指向 j 端；y' = x' 逆时针转 90°（整体坐标里 y' = (-sinα, cosα)）")
    L.append("  N      = -fl[0]     轴力，**受拉为正**")
    L.append("  Q_i/Q_j= fl[1]/fl[4]  剪力，沿局部 y' 正向为正")
    L.append("  M_i(端)= fl[2], M_j(端)= fl[5]   杆端弯矩（杆端力向量分量），**逆时针为正**")
    L.append("  M_i(图)= -fl[2], M_j(图)= +fl[5] 画弯矩图用，**局部 y' 那一侧受拉为正**")
    L.append("  「受拉侧」按 M(图) 的符号给：水平杆 上缘/下缘，竖杆 左侧/右侧")
    L.append("")
    L.append("二、杆端力表")
    L.append("  %-10s %-8s %7s %9s %9s %9s %10s %10s %10s %10s  %s"
             % ("杆件", "i-j", "L(m)", "N(kN)", "Q_i", "Q_j",
                "M_i(端)", "M_j(端)", "M_i(图)", "M_j(图)", "受拉侧"))
    for no in sol["order"]:
        r = sol["elements"][no]
        L.append("  %-10s %-8s %7.3f %9.3f %9.3f %9.3f %10.3f %10.3f %10.3f %10.3f  i:%s j:%s"
                 % (_elem_name(r), "%s-%s" % (r["ni"], r["nj"]), r["L"],
                    r["N"], r["Qi"], r["Qj"], r["Mi"], r["Mj"],
                    r["Mi_sag"], r["Mj_sag"], r["tension_i"], r["tension_j"]))
    if any(r["released_i"] or r["released_j"] for r in sol["elements"].values()):
        L.append("  （释放端：%s）"
                 % "、".join("%d端%s" % (r["no"], ("i" if r["released_i"] else "")
                                         + ("j" if r["released_j"] else ""))
                            for r in sol["elements"].values()
                            if r["released_i"] or r["released_j"]))
    L.append("")
    L.append("三、支反力表（整体坐标：Rx 沿 x、Ry 沿 y、M 绕 z 逆时针为正；只列有支承的节点）")
    L.append("  %-6s %12s %12s %12s" % ("节点", "Rx(kN)", "Ry(kN)", "M(kN·m)"))
    any_r = False
    for no in dat["node_ids"]:
        if no not in dat["supports"]:
            continue
        rx, ry, rm = sol["reactions"][no]
        any_r = True
        L.append("  %-6s %12.3f %12.3f %12.3f" % (no, rx, ry, rm))
    if not any_r:
        L.append("  （没有任何支反力：检查支承）")
    bad = []
    for no in dat["node_ids"]:
        if no in dat["supports"]:
            continue
        rx, ry, rm = sol["reactions"][no]
        if abs(rx) + abs(ry) + abs(rm) > 1e-3:
            bad.append((no, rx, ry, rm))
    if bad:
        L.append("  ！自由节点上有不平衡残余（说明荷载/约束没对上）：%s"
                 % "；".join("节点 %d (%.3f, %.3f, %.3f)" % b for b in bad))
    L.append("")
    L.append("四、位移（整体坐标：u 沿 x、v 沿 y、θ 绕 z 逆时针为正）")
    L.append("  %-6s %14s %14s %14s" % ("节点", "u(m)", "v(m)", "θ(rad)"))
    for no in dat["node_ids"]:
        u, v, th = sol["moves"][no]
        L.append("  %-6s %14.6e %14.6e %14.6e" % (no, u, v, th))
    L.append("")
    L.append("五、整体平衡校核（荷载合力 + 支反力合力 = 0）")
    sl, sr, rd = sol["balance"]["sum_load"], sol["balance"]["sum_react"], sol["balance"]["resid"]
    L.append("  %-12s %16s %16s %16s" % ("", "ΣFx(kN)", "ΣFy(kN)", "ΣM(kN·m)"))
    L.append("  %-12s %16.6e %16.6e %16.6e" % ("荷载合力", sl[0], sl[1], sl[2]))
    L.append("  %-12s %16.6e %16.6e %16.6e" % ("支反力合力", sr[0], sr[1], sr[2]))
    L.append("  %-12s %16.6e %16.6e %16.6e" % ("差值", rd[0], rd[1], rd[2]))
    L.append("  结论：%s（最大残差 %.3e，允许 %.1e）"
             % ("平衡通过" if sol["balance"]["ok"] else "不平衡，检查支承/荷载",
                sol["balance"]["worst"], sol["balance"].get("thr", 0.0)))
    L.append("")
    L.append("六、复算提示")
    L.append("  · 手算/教材对拍时注意：教材常把杆端弯矩写成「弯矩图值」，本表两种都给了；")
    L.append("  · 分布荷载按 3 点 Gauss 积分等效，三角形分布也精确（等效荷载的合力、合力矩与原荷载一致）；")
    L.append("  · MCT 里铰接用 *FRAME-RLS 释放局部 y 方向弯矩，与上表的「释放端弯矩 = 0」对应。")
    return "\r\n".join(L) + "\r\n"


def data_report(model):
    """人可读的数据表（写 *_数据表.txt）。"""
    prj = model.get("project") or {}
    L = []
    L.append("平面刚架（自由建模 · 矩阵位移法）数据表")
    L.append("=" * 78)
    L.append("项目名称：%s" % (prj.get("name") or ""))
    L.append("单位：kN, m, kN/m, kN·m")
    L.append("")
    L.append("一、节点表")
    L.append("  %-8s %14s %14s" % ("编号", "x (m)", "y (m, 竖向向上)"))
    for r in model.get("nodes") or []:
        L.append("  %-8s %14s %14s" % (r.get("no", ""), r.get("x", ""), r.get("y", "")))
    L.append("")
    L.append("二、杆件表")
    L.append("  %-8s %8s %8s %8s %-10s %6s %6s %10s"
             % ("编号", "节点i", "节点j", "截面号", "备注", "i端铰", "j端铰", "长度(m)"))
    xy = {}
    for r in model.get("nodes") or []:
        no = _int(r.get("no"))
        if no is not None:
            xy[no] = (_f(r.get("x")), _f(r.get("y")))
    for r in model.get("elems") or []:
        ni, nj = _int(r.get("i")), _int(r.get("j"))
        ln = "-"
        if ni in xy and nj in xy:
            ln = "%.4f" % math.hypot(xy[ni][0] - xy[nj][0], xy[ni][1] - xy[nj][1])
        L.append("  %-8s %8s %8s %8s %-10s %6s %6s %10s"
                 % (r.get("no", ""), r.get("i", ""), r.get("j", ""), r.get("sec", ""),
                    r.get("label") or "", "是" if r.get("ri") else "否",
                    "是" if r.get("rj") else "否", ln))
    L.append("  （「铰」= 该端释放弯矩，MCT 里写 *FRAME-RLS；释放端弯矩恒为 0）")
    L.append("")
    L.append("三、截面与材料")
    mat = model.get("mat") or {}
    L.append("  弹性模量 E = %s kN/m^2（= kPa）   容重 γ = %s kN/m^3   泊松比 = %s"
             % (mat.get("E", ""), mat.get("gamma", ""), mat.get("nu", "")))
    E = material_E(model)
    for i, sec in enumerate(read_sections(model), start=1):
        pr = section_props(sec)
        L.append("  截面 %d：%s（%s）  A=%s m^2  Iyy=%s m^4  Izz=%s m^4  EI=%s kN·m^2"
                 % (i, sec.get("name", ""), section_summary(sec),
                    _fmt(pr["A"], 8), _fmt(pr["Iyy"], 8), _fmt(pr["Izz"], 8),
                    _fmt(E * pr["Iyy"], 8)))
    L.append("  （内置求解器用 Iyy 作面内抗弯惯矩；MCT 里写的是 DBUSER 参数化截面，"
             "MIDAS 按自己算的截面特性分析）")
    L.append("")
    L.append("四、支承（u/v/θ；MCT 里还额外约束平面外 DY/RX/RZ）")
    L.append("  %-8s %6s %6s %6s %14s" % ("节点", "u", "v", "θ", "对应 6 位约束码"))
    for r in model.get("supports") or []:
        no = _int(r.get("node"))
        if no is None:
            continue
        L.append("  %-8s %6s %6s %6s %14s"
                 % (no, "√" if r.get("u") else "-", "√" if r.get("v") else "-",
                    "√" if r.get("r") else "-", support_code(r)))
    L.append("")
    L.append("五、荷载")
    nl = model.get("nloads") or []
    L.append("  1) 节点荷载（%d 条）" % len(nl))
    if nl:
        L.append("     %-8s %12s %12s %14s" % ("节点", "Fx(kN)", "Fy(kN)", "Mz(kN·m)"))
        for r in nl:
            L.append("     %-8s %12s %12s %14s"
                     % (r.get("node", ""), r.get("fx", ""), r.get("fy", ""), r.get("mz", "")))
    else:
        L.append("     （无）")
    dl = model.get("dloads") or []
    L.append("  2) 杆件分布荷载（%d 条，i 端 → j 端可线性变化）" % len(dl))
    if dl:
        L.append("     %-8s %-16s %14s %14s" % ("杆件", "方向", "i端(kN/m)", "j端(kN/m)"))
        for r in dl:
            d = str(r.get("dir") or "y").strip().lower()
            L.append("     %-8s %-16s %14s %14s"
                     % (r.get("elem", ""), _DIR_NAMES.get(d, d),
                        r.get("q1", ""), r.get("q2", "")))
    else:
        L.append("     （无）")
    L.append("")
    L.append("六、MCT 约定（导入 MIDAS Civil NX 后照这个核对）")
    L.append("  1) 平面刚架建在全局 X-Z 平面：向导的 x → X，向导的 y（竖向）→ Z，Y 恒为 0；")
    L.append("     面内自由度是 DX / DZ / RY，支座把平面外的 DY / RX / RZ 一并约束（避免 2D 出平面奇异）。")
    L.append("  2) 节点弯矩走 *CONLOAD 的 MX/MY/MZ（面内弯矩 → MY）；分布荷载走 *BEAMLOAD，"
             "水平荷载 DIR=GX、竖向荷载 DIR=GZ。")
    L.append("  3) 铰接 = *FRAME-RLS 释放局部 y 方向弯矩（FLAG 第 5 位 = 1），"
             "和内置求解器的「释放端弯矩 = 0」是同一件事。")
    L.append("  4) 本模型不含自重（*SELFWEIGHT）；要计自重请在 MIDAS 里加，"
             "或在荷载里把自重当分布荷载加上。")
    L.append("")
    L.append("七、内置求解器说明")
    L.append("  · 每节点 3 自由度 (u,v,θ)，平面梁单元 6×6；支承行列删除；")
    L.append("    线性分布荷载用 3 点 Gauss 积分求等效节点荷载（三角形也精确）；")
    L.append("  · 忽略轴向变形：EA = 1e10·EI/L^2（杆端力与 EI 绝对值无关，只与 EI 比值有关）；")
    L.append("  · 杆端释放用单元级静力凝聚，释放端弯矩恢复后恰为 0；")
    L.append("  · 支反力 = [K]{U} - {F}，并自动做 ΣFx / ΣFy / ΣM 整体平衡校核。")
    return "\r\n".join(L) + "\r\n"


def support_code(row):
    """支承行 → MCT 的 6 位约束码（先面内 u/v/θ → DX/DZ/RY，再固定平面外 DY/RX/RZ）。"""
    u = bool(row.get("u"))
    v = bool(row.get("v"))
    r = bool(row.get("r"))
    bits = [1 if u else 0,      # DX
            1,                  # DY（平面外，固定）
            1 if v else 0,      # DZ
            1,                  # RX（平面外，固定）
            1 if r else 0,      # RY（面内转角）
            1]                  # RZ（平面外，固定）
    return "".join(str(b) for b in bits)


# ======================================================================
# MCT 生成（字段数逐条照 MIDAS 样本/手册，见文件头注释）
# ======================================================================
_MATERIAL_FMT = ("   %d, %-5s, %s, 0, 0, , C, NO, 0.05, 2, %.4e, %s, %s, %s, 0")


def _material_lines(model):
    """*MATERIAL：1 行 15 字段（照 CableStayed.mct / cfst 的写法）。"""
    mat = model.get("mat") or {}
    name = _aname(mat.get("grade") or "C40", "C40")
    E = material_E(model)
    nu = material_nu(model)
    gam = material_gamma(model)
    return [_MATERIAL_FMT % (1, "CONC", name, E, _fmt(nu), "1.0e-05", _fmt(gam))]


def _section_lines(model):
    """截面块（*SECT-PSCVALUE + *SECTION）。

    由 Lead 维护的 sect_ui 统一输出（和梁桥/斜拉桥流程逐字段一致）；
    sect_ui 顶层 import 了 tkinter，所以这里延迟导入，引擎本身不依赖界面。
    """
    secs = read_sections(model)
    try:
        import sect_ui                                        # noqa: PLC0415（延迟导入）
        return sect_ui.section_mct_lines(secs)
    except Exception:                                         # noqa: BLE001
        return _section_lines_plain(secs)


def _section_lines_plain(secs):
    """sect_ui 用不了时的等价实现（格式和 sect_ui.section_mct_lines 一致）。"""
    L = []
    add = L.append
    add("*SECTION    ; Section Properties")
    add("; iSEC, TYPE, SNAME, [OFFSET], bSD, bWE, SHAPE, BLT, D1, ..., D8, iCEL")
    add(";       AREA, ASy, ASz, Ixx, Iyy, Izz")
    for i, s in enumerate(secs, start=1):
        code = str(s.get("shape") or "SB").strip().upper()
        dims = [_fmt(v) for v in (s.get("dims") or [])]
        vals = (dims + ["0"] * 10)[:10]
        add("   %d, DBUSER, %s, %s, 0, 0, 0, 0, 0, 0, %s, %s, %s, 2, %s"
            % (i, _aname(s.get("name"), "SEC%d" % i), s.get("offset", "CC"),
               s.get("shear", "YES"), s.get("warp", "NO"), code, ", ".join(vals)))
    add("")
    return L


def _rls_lines(model, dat):
    """*FRAME-RLS：铰接释放（2 行一条）。返回行列表。"""
    rows = []
    for e in dat["elems"]:
        if not (e["ri"] or e["rj"]):
            continue
        # 局部坐标：面内弯曲 = 绕局部 y 轴 → 6 位 FLAG 的第 5 位（Fx,Fy,Fz,Mx,My,Mz）
        fi = "000010" if e["ri"] else "000000"
        fj = "000010" if e["rj"] else "000000"
        vals = "0, 0, 0, 0, 0, 0"
        rows.append("   %d, %s%s, %s" % (e["no"], RLS_BVALUE + (", " if RLS_BVALUE else ""),
                                         fi, vals))
        rows.append("       %s, %s, BC" % (fj, vals))
    return rows


def build_mct(model):
    """生成 MIDAS Civil NX 的 MCT 文本（写文件按 GBK 编码）。"""
    dat = collect(model)
    prj = model.get("project") or {}
    name = _safe_name(prj.get("name"), "平面刚架")
    force = str(prj.get("force") or "KN").strip() or "KN"
    dist = str(prj.get("dist") or "M").strip() or "M"
    nodes = dat["nodes"]
    elems = dat["elems"]
    secs = dat["sections"]
    E = material_E(model)
    ndangle = len([e for e in elems if e["ri"] or e["rj"]])

    L = []
    add = L.append
    add("; ================================================================")
    add("; MIDAS Civil NX — MCT 命令文件（平面刚架 · 自由建模 · 矩阵位移法）")
    add("; 由「MIDAS 建模向导」生成  " + datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    add("; 项目名称：" + name)
    add("; 导入：MIDAS Civil NX → 文件 → 导入 → MCT 命令文件")
    add("; 规模：节点 %d 个、梁单元 %d 个、节点荷载 %d 条、分布荷载 %d 条、杆端释放 %d 处"
        % (len(nodes), len(elems), len(dat["nloads"]), len(dat["dloads"]), ndangle))
    add("; 坐标：平面刚架建在全局 X-Z 平面（Z 向上）；向导 x → X，向导 y（竖向）→ Z，Y=0")
    add("; 面内自由度：DX/DZ/RY；支座的平面外自由度 DY/RX/RZ 一并约束，避免 2D 出平面奇异")
    add("; 铰接：*FRAME-RLS 释放局部 y 方向弯矩（平面刚架的面内弯曲就是绕局部 y 轴）")
    add("; 材料：E = %s kN/m^2（= kPa），容重 %s kN/m^3" % (_fmt(E, 8), _fmt(material_gamma(model))))
    add("; ================================================================")
    add("")
    add("*VERSION")
    add("   9.6.5")
    add("")
    add("*UNIT    ; Unit System")
    add("; FORCE, LENGTH, HEAT, TEMPER")
    add("   %s, %s, KJ, C" % (force, dist))
    add("")
    add("*STRUCTYPE    ; Structure Type")
    add("; iSTYP, iMASS, iSMAS, bMASSOFFSET, bSELFWEIGHT, GRAV, TEMPER, bALIGNBEAM, bALIGNSLAB, bROTRIGID")
    add("   0, 1, 1, NO, NO, 9.806, 0, NO, NO, NO")
    add("")

    # ---- 节点（x, 0, y）----
    add("*NODE    ; Nodes")
    add("; iNO, X, Y, Z")
    for n in nodes:
        add("   %d, %s, 0, %s" % (n["no"], _fmt(n["x"]), _fmt(n["y"])))
    add("")

    # ---- 单元 ----
    add("*ELEMENT    ; Elements")
    add("; iEL, TYPE, iMAT, iPRO, iN1, iN2, ANGLE, iSUB")
    for e in elems:
        add("   %d, BEAM, 1, %d, %d, %d, 0, 0" % (e["no"], e["sec"], e["ni"], e["nj"]))
    add("")

    # ---- 材料 ----
    add("*MATERIAL    ; Material")
    add("; iMAT, TYPE, MNAME, SPHEAT, HEATCO, PLAST, TUNIT, bMASS, DAMPRATIO, [DATA1]")
    add("; [DATA1] : 2, ELAST, POISN, THERMAL, DEN, MASS")
    for ln in _material_lines(model):
        add(ln)
    add("")

    # ---- 截面（sect_ui 生成 *SECTION / *SECT-PSCVALUE）----
    for ln in _section_lines(model):
        add(ln)

    # ---- 结构组（按「柱 / 梁」分：竖杆 = |x1-x2| < 1e-9）----
    col_elems, beam_elems = [], []
    for e in elems:
        if abs(e["c"]) <= 1e-9:
            col_elems.append(e["no"])
        else:
            beam_elems.append(e["no"])
    all_e = [e["no"] for e in elems]
    all_n = [n["no"] for n in nodes]
    col_nodes, beam_nodes = [], []
    for e in elems:
        (col_nodes if e["no"] in col_elems else beam_nodes).extend([e["ni"], e["nj"]])
    add("*GROUP    ; Group")
    add("; NAME, NODE_LIST, ELEM_LIST, PLANE_TYPE")
    add("   ALL, %s, %s, 0" % (_ranges(all_n), _ranges(all_e)))
    if col_elems:
        add("   COL, %s, %s, 0" % (_ranges(col_nodes), _ranges(col_elems)))
    if beam_elems:
        add("   BEAM, %s, %s, 0" % (_ranges(beam_nodes), _ranges(beam_elems)))
    add("")
    add("*BNDR-GROUP    ; Boundary Group")
    add("; NAME, AUTOTYPE")
    add("   BC, 0")
    add("")
    add("*LOAD-GROUP    ; Load Group")
    add("; NAME")
    add("   CL")
    add("   DL")
    add("")

    # ---- 荷载工况 ----
    add("*STLDCASE    ; Static Load Cases")
    add("; LCNAME, LCTYPE, DESC")
    add("   CL, D, 节点荷载")
    add("   DL, D, 分布荷载")
    add("")

    # ---- 支承（同约束码的节点合并成一行）----
    add("*CONSTRAINT    ; Supports")
    add("; NODE_LIST, CONST(Dx,Dy,Dz,Rx,Ry,Rz), GROUP")
    by_code = {}
    for r in model.get("supports") or []:
        no = _int(r.get("node"))
        if no is None:
            continue
        by_code.setdefault(support_code(r), []).append(no)
    for code in sorted(by_code):
        add("   %s, %s, BC" % (_ranges(by_code[code]), code))
    if not by_code:
        add("; （没有支承 —— 结构是机构，MIDAS 会报奇异）")
    add("")

    # ---- 杆端释放 ----
    rls = _rls_lines(model, dat)
    if rls:
        add("*FRAME-RLS    ; Beam End Release（铰接：1=释放，顺序 Fx,Fy,Fz,Mx,My,Mz）")
        add("; ELEM_LIST, FLAG-i, Fxi, Fyi, Fzi, Mxi, Myi, Mzi        ; 1st Line")
        add(";                    FLAG-j, Fxj, Fyj, Fzj, Mxj, Myj, Mzj, GROUP ; 2nd Line")
        for ln in rls:
            add(ln)
        add("")

    # ---- 节点荷载（向导 Fx/Fy/Mz → MCT FX/FZ/MY；*CONLOAD 本来就有 MX/MY/MZ 位）----
    nrows = []
    for (no, fx, fy, mz) in dat["nloads"]:
        if abs(fx) < 1e-12 and abs(fy) < 1e-12 and abs(mz) < 1e-12:
            continue
        nrows.append("   %d, %s, 0, %s, 0, %s, 0, CL"
                     % (no, _fmt(fx), _fmt(fy), _fmt(mz)))
    if nrows:
        add("*USE-STLD, CL")
        add("*CONLOAD    ; Nodal Loads（FX, FY, FZ, MX, MY, MZ, GROUP）")
        add("; NODE_LIST, FX, FY, FZ, MX, MY, MZ, GROUP")
        for ln in nrows:
            add(ln)
        add("")

    # ---- 分布荷载（水平 → GX，竖向 → GZ；D1..P4 = 位置比例 / 强度）----
    drows = []
    for ld in dat["dloads"]:
        if abs(ld["q1"]) < 1e-12 and abs(ld["q2"]) < 1e-12:
            continue
        dir_mct = "GX" if ld["dir"] == "x" else "GZ"
        drows.append("     %d, BEAM   , UNILOAD, %s, NO , NO, LY, , , , 0, %s, 1, %s, "
                     "0, 0, 0, 0, DL, NO, 0, 0, NO, "
                     % (ld["elem"], dir_mct, _fmt(ld["q1"]), _fmt(ld["q2"])))
    if drows:
        add("*USE-STLD, DL")
        add("*BEAMLOAD    ; Element Beam Loads（线性分布：i 端 → j 端）")
        add("; ELEM_LIST, CMD, TYPE, DIR, bPROJ, [ECCEN], [V1..V3], D1, P1, D2, P2, D3, P3, D4, P4, GROUP")
        for ln in drows:
            add(ln)
        add("")

    add("*ENDDATA")
    return "\r\n".join(L) + "\r\n"


def mct_stats(model):
    """MCT 各命令块的行数（测试/日志用）。"""
    txt = build_mct(model)
    out = {"blocks": {}, "nodes": 0, "elems": 0, "beamload": 0, "conload": 0,
           "rls": 0, "constraint": 0, "material": 0, "section": 0}
    cur = None
    for raw in txt.replace("\r\n", "\n").split("\n"):
        st = raw.strip()
        if not st or st.startswith(";"):
            continue
        if st.startswith("*"):
            cur = st.split(";")[0].strip().split(",")[0].strip()
            out["blocks"][cur] = out["blocks"].get(cur, 0) + 1
            continue
        if cur == "*NODE":
            out["nodes"] += 1
        elif cur == "*ELEMENT":
            out["elems"] += 1
        elif cur == "*BEAMLOAD":
            out["beamload"] += 1
        elif cur == "*CONLOAD":
            out["conload"] += 1
        elif cur == "*FRAME-RLS":
            out["rls"] += 1
        elif cur == "*CONSTRAINT":
            out["constraint"] += 1
        elif cur == "*MATERIAL":
            out["material"] += 1
        elif cur == "*SECTION":
            out["section"] += 1
    return out


# ======================================================================
# 参数校验（界面每一步用它拦人；空列表 = 能进下一步）
# ======================================================================
def node_issues(model):
    out = []
    rows = model.get("nodes") or []
    if not rows:
        return ["节点表是空的：至少要有 1 个节点（点「＋ 增加节点」）。"]
    seen = {}
    for k, r in enumerate(rows, start=1):
        no = _int(r.get("no"))
        if no is None:
            out.append("节点表第 %d 行的「编号」要填整数（现在是 %r）。"
                       % (k, r.get("no")))
            continue
        if no in seen:
            out.append("节点编号 %d 重复（第 %d 行和第 %d 行）。" % (no, seen[no], k))
        seen[no] = k
        for key, zh in (("x", "x 坐标"), ("y", "y 坐标")):
            if not _is_num(r.get(key)):
                out.append("节点 %d 的%s不是数字（现在是 %r）。" % (no, zh, r.get(key)))
    if len(seen) < 2:
        out.append("至少要 2 个节点才能组成杆件。")
    return out


def elem_issues(model):
    out = []
    rows = model.get("elems") or []
    if not rows:
        return ["杆件表是空的：至少要有 1 个杆件（点「＋ 增加杆件」）。"]
    nodes = {}
    for r in model.get("nodes") or []:
        no = _int(r.get("no"))
        if no is not None:
            nodes[no] = (_f(r.get("x")), _f(r.get("y")))
    secs = read_sections(model)
    seen = {}
    for k, r in enumerate(rows, start=1):
        no = _int(r.get("no"))
        if no is None:
            out.append("杆件表第 %d 行的「编号」要填整数（现在是 %r）。" % (k, r.get("no")))
            continue
        if no in seen:
            out.append("杆件编号 %d 重复（第 %d 行和第 %d 行）。" % (no, seen[no], k))
        seen[no] = k
        ni, nj = _int(r.get("i")), _int(r.get("j"))
        if ni is None or nj is None:
            out.append("杆件 %d 的 i/j 端节点号要填整数。" % no)
            continue
        for tag, nd in (("i", ni), ("j", nj)):
            if nd not in nodes:
                out.append("杆件 %d 的 %s 端节点 %d 在节点表里不存在。" % (no, tag, nd))
        if ni == nj:
            out.append("杆件 %d 的 i 端和 j 端是同一个节点 %d。" % (no, ni))
        elif ni in nodes and nj in nodes:
            x1, y1 = nodes[ni]
            x2, y2 = nodes[nj]
            if math.hypot(x2 - x1, y2 - y1) <= 1e-9:
                out.append("杆件 %d 的长度是 0（两端节点坐标相同）。" % no)
        secno = _int(r.get("sec"), 1)
        if secs and not (1 <= secno <= len(secs)):
            out.append("杆件 %d 的截面号 %d 不存在（现在有 %d 个截面）。"
                       % (no, secno, len(secs)))
    return out


def section_issues(model):
    out = []
    secs = read_sections(model)
    if not secs:
        out.append("还没有定义截面：至少加 1 个截面（第 2 步「＋ 添加参数化截面」）。")
    for i, sec in enumerate(secs, start=1):
        code = str(sec.get("shape") or "SB").strip().upper()
        if code == "CAD":
            pr = sec.get("props") or {}
            if _f(pr.get("A"), 0.0) <= 0 or _f(pr.get("Iyy"), 0.0) <= 0:
                out.append("截面 %d（%s）是 DXF 自定义截面，但还没算出截面特性"
                           "（点「重新计算自定义截面」）。" % (i, sec.get("name", "")))
            continue
        dims = [str(v) for v in (sec.get("dims") or [])]
        if code == "SR":
            D = _f(dims[0] if dims else 0, 0.0)
            if D <= 0:
                out.append("截面 %d 是实心圆，直径 D 要大于 0。" % i)
            continue
        H = _f(dims[0] if len(dims) > 0 else 0, 0.0)
        B = _f(dims[1] if len(dims) > 1 else 0, 0.0)
        if H <= 0 or B <= 0:
            out.append("截面 %d（%s）的尺寸要大于 0（现在是 H=%r、B=%r）。"
                       % (i, sec.get("name", ""), dims[0] if dims else "",
                          dims[1] if len(dims) > 1 else ""))
        elif H > 20 or B > 20:
            out.append("截面 %d 的尺寸偏大（H=%s、B=%s m），确认一下单位是 m。" % (i, H, B))
    mat = model.get("mat") or {}
    if not _is_num(mat.get("E")):
        out.append("材料弹性模量 E 要填数字（kN/m^2 = kPa，C40 取 3.25e7）。")
    else:
        E = _f(mat.get("E"), 0.0)
        if E <= 0:
            out.append("材料弹性模量 E 要大于 0。")
        elif E < 1.0e6 or E > 1.0e9:
            out.append("材料弹性模量 E=%s 不像混凝土/钢材（kN/m^2），"
                       "C40 = 3.25e7、Q345 = 2.06e8。" % _fmt(E, 6))
    if not _is_num(mat.get("gamma")):
        out.append("材料容重要填数字（kN/m^3，混凝土 25）。")
    elif _f(mat.get("gamma"), 0.0) < 0:
        out.append("材料容重不能是负数。")
    if _is_num(mat.get("nu")):
        nu = _f(mat.get("nu"), 0.0)
        if nu < 0 or nu >= 0.5:
            out.append("泊松比要在 0 ~ 0.5 之间（混凝土 0.2）。")
    return out


def support_issues(model):
    out = []
    rows = model.get("supports") or []
    if not rows:
        return ["还没有支承：至少约束 1 个节点（否则结构是机构，矩阵位移法算不了）。"]
    nodes = set()
    for r in model.get("nodes") or []:
        no = _int(r.get("no"))
        if no is not None:
            nodes.add(no)
    seen = {}
    for k, r in enumerate(rows, start=1):
        no = _int(r.get("node"))
        if no is None:
            out.append("支承表第 %d 行的「节点」要填整数编号。" % k)
            continue
        if no not in nodes:
            out.append("支承引用了不存在的节点 %d。" % no)
        if no in seen:
            out.append("节点 %d 有两条支承记录（第 %d 行和第 %d 行），合并成一条。"
                       % (no, seen[no], k))
        seen[no] = k
        if not (r.get("u") or r.get("v") or r.get("r")):
            out.append("节点 %d 的支承一个自由度都没约束（u/v/θ 至少勾一个）。" % no)
    return out


def load_issues(model):
    out = []
    nodes = set()
    for r in model.get("nodes") or []:
        no = _int(r.get("no"))
        if no is not None:
            nodes.add(no)
    elems = set()
    for r in model.get("elems") or []:
        no = _int(r.get("no"))
        if no is not None:
            elems.add(no)
    for k, r in enumerate(model.get("nloads") or [], start=1):
        no = _int(r.get("node"))
        if no is None:
            out.append("节点荷载第 %d 行的「节点」要填整数编号。" % k)
            continue
        if no not in nodes:
            out.append("节点荷载引用了不存在的节点 %d。" % no)
        for key, zh in (("fx", "Fx"), ("fy", "Fy"), ("mz", "Mz")):
            if not _is_num(r.get(key)):
                out.append("节点 %d 的 %s 不是数字（现在是 %r）。" % (no, zh, r.get(key)))
    for k, r in enumerate(model.get("dloads") or [], start=1):
        no = _int(r.get("elem"))
        if no is None:
            out.append("分布荷载第 %d 行的「杆件」要填整数编号。" % k)
            continue
        if no not in elems:
            out.append("分布荷载引用了不存在的杆件 %d。" % no)
        d = str(r.get("dir") or "y").strip().lower()
        if d not in ("x", "y"):
            out.append("杆件 %d 的分布荷载方向只能是「整体X」或「整体Y」。" % no)
        for key, zh in (("q1", "i 端强度"), ("q2", "j 端强度")):
            if not _is_num(r.get(key)):
                out.append("杆件 %d 分布荷载的%s不是数字（现在是 %r）。"
                           % (no, zh, r.get(key)))
    return out


def model_issues(model):
    """求解前的硬性检查（节点/杆件/截面/支承在不在、EI 有没有）。"""
    out = []
    try:
        collect(model)
    except ModelError as exc:
        out.append(str(exc))
    return out


def issues(model, sid):
    if sid in ("frame_geom", "geom"):
        return node_issues(model) + elem_issues(model)
    if sid in ("frame_section", "section"):
        return section_issues(model) + [
            x for x in elem_issues(model) if "截面号" in x]
    if sid in ("frame_loads", "loads"):
        return support_issues(model) + load_issues(model)
    return all_issues(model)


def all_issues(model):
    out = []
    for fn, title in ((node_issues, "节点表"), (elem_issues, "杆件表"),
                      (section_issues, "截面与材料"), (support_issues, "支承"),
                      (load_issues, "荷载")):
        for msg in fn(model):
            out.append("「%s」%s" % (title, msg))
    return out


# ======================================================================
# 成果文件（4 个）
# ======================================================================
def write_outputs(model, outdir, name=None):
    """写 4 个成果文件，返回 [(说明, 路径)]：

        <名字>.mct             MIDAS Civil NX 的 MCT 命令文件（GBK）
        <名字>_数据表.txt      模型数据表（GBK）
        <名字>_求解结果.txt    矩阵位移法结果：杆端力 + 支反力 + 平衡校核（GBK）
        <名字>_数据.json       向导里的全部输入（UTF-8，可再打开继续改）
    """
    fname = _safe_name(name or (model.get("project") or {}).get("name"), "平面刚架")
    out = outdir or (model.get("project") or {}).get("outdir") or ""
    out = out or os.path.join(os.getcwd(), "平面刚架成果")
    os.makedirs(out, exist_ok=True)
    model.setdefault("project", {})
    model["project"]["name"] = fname
    model["project"]["outdir"] = out
    files = []

    def _w(path, text, enc="gbk"):
        with open(path, "w", encoding=enc, newline="") as fh:
            fh.write(text)
        return path

    p1 = os.path.join(out, fname + ".mct")
    _w(p1, build_mct(model))
    files.append(("MCT 命令文件", p1))
    p2 = os.path.join(out, fname + "_数据表.txt")
    _w(p2, data_report(model))
    files.append(("数据表", p2))
    p3 = os.path.join(out, fname + "_求解结果.txt")
    _w(p3, result_report(model))
    files.append(("求解结果", p3))
    p4 = os.path.join(out, fname + "_数据.json")
    with open(p4, "w", encoding="utf-8") as fh:
        json.dump(model, fh, ensure_ascii=False, indent=2)
    files.append(("向导数据（json）", p4))
    return files
