# -*- coding: utf-8 -*-
r"""施工阶段梁桥 —— 核心引擎（纯计算 + MCT 生成，不依赖 tkinter）。

这是《桥梁结构电算》第四讲「施工阶段分析」的建模助手：把施工组织翻译成
  工序 → 施工阶段 → 构件（结构组）/ 边界（边界组）/ 荷载（荷载组）
再生成 MIDAS Civil NX 能直接导入的 .mct。

两个子类型（界面上第 1 步单选切换）：

  A) 连续刚构 · 悬臂浇筑（fcm）
     跨径布置（如 60+100+60）/ 墩高 / 墩顶梁高 / 跨中梁高 / 每墩悬臂节段数 /
     0 号块长度 / 合龙段长度。
     主梁是沿桥向变高的单箱单室截面（梁高、腹板厚、底板厚都随位置变化），
     MCT 里按「多段等截面」输出：每个单元取自己跨中的尺寸，单元够密就看得出变高。
     施工阶段：墩身 → 墩顶 0 号块 → 对称悬臂浇筑 1..n 号节段（每节段一个 CS）→
               边跨支架现浇 → 边跨合龙 → 中跨合龙 → 二期恒载 → 成桥运营。
     湿重按「新浇节段面积 × 容重」折成均布荷载（BEAMLOAD），加在已完成的悬臂端
     单元上；该节段硬化的那个阶段再用 DLOAD 把湿重卸掉 —— 和施工实际一致
     （湿混凝土挂在挂篮上，硬化后才由梁段自己承担）。

  B) 简支变连续（s2c）
     跨径（如 20+20）/ 每跨预制梁划分单元数 / 湿接缝长度 / 预制 T 梁截面。
     施工阶段：预制养护 → 张拉梁内束 → 架设 → 张拉 → 浇湿接缝（湿重）→
               接缝养护 → 张拉连续束 → 撤临时体系（体系转换）→ 成桥运营。

MCT 格式全部对着本机 MIDAS 自己导出的样本写：
  C:\Midas\work\samples\FCM.mct   （悬臂施工：*STAGE / *STAGE-CTRL / *GROUP /
                                   *BNDR-GROUP / *LOAD-GROUP / *SELFWEIGHT / *BEAMLOAD /
                                   *TDM-* 时间依存材料）
  C:\Midas\work\samples\MSS.mct   （梁桥施工阶段：自重 + BEAMLOAD + 边界阶段转换）
  C:\Midas\work\samples\ILM.mct   （顶推：同样的组/阶段写法）
  C:\Midas\wiz\out\fixed\*.mct    （本向导已验证过的 *SECTION / *NODE / *ELEMENT 写法）

约定：
  * 数组里的数值都是字符串（和梁桥流程一致），写 MCT 时统一用 _num()/_numg()；
  * 组名一律 ASCII 且唯一（中文组名有踩坑风险，这里不用）；
  * 截面类型用 MIDAS 的 DBUSER（参数化形状）：主梁箱形 B、预制 T 梁 T、墩身箱形 B。
  * 预应力钢束（*TDN-*）这一版只留接口（tendon_lines()），没有实现张拉，见文件末尾。
"""
import datetime
import math

import wiz_common as wc

CODE = "cs"

SUB_FCM = "fcm"
SUB_S2C = "s2c"
SUB_TYPES = [(SUB_FCM, "连续刚构 · 悬臂浇筑"),
             (SUB_S2C, "简支变连续")]
SUB_LABEL = dict(SUB_TYPES)

GAMMA_CONC = 25.0                     # 混凝土容重 kN/m3
# 弹性模量 kN/m2（JTG 3362-18）
E_CONC = {"C30": 3.00e7, "C35": 3.15e7, "C40": 3.25e7, "C45": 3.35e7,
          "C50": 3.45e7, "C55": 3.55e7, "C60": 3.60e7}
# 28 天立方体抗压强度标准值 fck（kN/m2 = kPa）→ 时间依存材料用 fcm = fck + 8 MPa
FCK_CONC = {"C30": 20.1e3, "C35": 23.4e3, "C40": 26.8e3, "C45": 29.6e3,
            "C50": 32.4e3, "C55": 35.5e3, "C60": 38.5e3}
CONC_GRADES = ["C30", "C35", "C40", "C45", "C50", "C55", "C60"]

# *STAGE-CTRL 的第 1~6 行，逐字段照 FCM.mct / MSS.mct 抄（第 1 行第 11 个字段是 bITD=是否算时间依存）
STAGE_CTRL_L1 = "  YES, , INTERNAL, NO, NO, NO, NO, NO, YES, YES, %s, , , NO, NO, , NO, NO, NO"


# ======================================================================
# 数值 / 文本小工具
# ======================================================================
class LayoutError(ValueError):
    """布置参数不自洽（边跨太短、节段数为 0 等），issues() 会转成中文提示。"""


def _fl(v, name="参数"):
    try:
        return float(str(v).strip())
    except (TypeError, ValueError):
        raise LayoutError("「%s」要填数字，现在填的是：%s" % (name, v))


def _pos(v, name):
    f = _fl(v, name)
    if f <= 0:
        raise LayoutError("「%s」必须大于 0，现在是 %g" % (name, f))
    return f


def _posint(v, name, lo=1, hi=999):
    f = _fl(v, name)
    i = int(round(f))
    if abs(f - i) > 1e-6 or i < lo or i > hi:
        raise LayoutError("「%s」要填 %d~%d 之间的整数，现在是 %s" % (name, lo, hi, v))
    return i


def _span_list(v):
    """把 "60+100+60" / "60,100,60" / "60 100 60" 解析成 [60.0, 100.0, 60.0]。"""
    raw = str(v or "").replace("+", " ").replace("，", " ").replace(",", " ")
    out = []
    for tok in raw.split():
        try:
            f = float(tok)
        except ValueError:
            raise LayoutError("跨径布置「%s」里有不是数字的：%s（写成 60+100+60 这样）" % (v, tok))
        if f <= 0:
            raise LayoutError("跨径布置里的每一跨都必须大于 0，现在有 %g" % f)
        out.append(f)
    if not out:
        raise LayoutError("跨径布置没有填（写成 60+100+60 这样）")
    return out


def _num(v, nd=6):
    """数字写进 MCT 用：去掉浮点噪声，不用科学计数法。"""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return "0"
    if abs(f) < 1e-12:
        return "0"
    s = ("%.*g" % (nd, f))
    if "e" in s or "E" in s:
        s = ("%.*f" % (max(0, nd), f)).rstrip("0").rstrip(".")
    return s if s not in ("", "-0") else "0"


def _numg(v, sig=6):
    return _num(v, sig)


def _ids(nums):
    """MIDAS 的 ID 列表写法：连续段写成 1to10，其它用空格隔开（样本里的习惯）。"""
    a = sorted(set(int(n) for n in nums))
    if not a:
        return ""
    out, i = [], 0
    while i < len(a):
        j = i
        while j + 1 < len(a) and a[j + 1] == a[j] + 1:
            j += 1
        out.append("%dto%d" % (a[i], a[j]) if j > i else str(a[i]))
        i = j + 1
    return " ".join(out)


def _wrap_stage_field(key, items, maxlen=112):
    """*STAGE 的 AELEM=/ALOAD= 这类行：太长就折行（续行缩进，和 MIDAS 导出的一样）。"""
    if not items:
        return []
    lines, cur = [], "  %s=" % key
    for i, it in enumerate(items):
        piece = it if i == 0 else ", " + it
        if i and len(cur) + len(piece) > maxlen:
            lines.append(cur)
            cur = "        " + it
        else:
            cur += piece
    lines.append(cur)
    return lines


# ======================================================================
# 参数化截面（只要面积用于湿重计算；MCT 里交给 MIDAS 按形状自己算特性）
# ======================================================================
def section_area(shape, dims):
    """按 MIDAS 的 DBUSER 形状近似算面积（m2）——只用来把湿重折成均布荷载。"""
    d = []
    for x in dims:
        try:
            d.append(float(str(x).strip() or 0.0))
        except (TypeError, ValueError):
            d.append(0.0)
    d += [0.0] * (8 - len(d))
    H, B, tw, tf1, C, tf2 = d[0], d[1], d[2], d[3], d[4], d[5]
    if shape == "SB":
        return H * B
    if shape == "SR":
        return math.pi * d[0] * d[0] / 4.0
    if shape == "B":                       # 单箱单室：H/B 外轮廓，tw 腹板厚，tf1/tf2 顶底板厚
        hi = H - tf1 - tf2
        bi = B - 2.0 * tw
        return max(0.0, H * B - max(0.0, hi) * max(0.0, bi))
    if shape == "T":                       # T 形：B 翼缘宽，tf 翼缘厚，tw 腹板厚
        return B * tf1 + max(0.0, H - tf1) * tw
    if shape == "P":
        return math.pi * (d[0] ** 2 - max(0.0, d[0] - 2.0 * tw) ** 2) / 4.0
    return H * B


def _add_section(secs, index, shape, dims, offset, name):
    """按 (形状, 尺寸, 偏心) 去重；返回截面号（从 1 开始）。"""
    key = (shape, tuple(round(float(x), 4) for x in dims), offset)
    if key in index:
        return index[key]
    dims = [round(float(x), 4) for x in dims]
    sec = {"index": len(secs) + 1, "name": name or ("S%d" % (len(secs) + 1)),
           "shape": shape, "dims": dims, "offset": offset,
           "area": section_area(shape, dims)}
    secs.append(sec)
    index[key] = sec["index"]
    return sec["index"]


# ======================================================================
# 默认模型
# ======================================================================
def default_fcm():
    """连续刚构 · 悬臂浇筑的默认参数。

    默认尺寸参照《桥梁结构电算》第三讲 3.5 梁式桥与第四讲上机实操里的常规取值：
      跨径 60+100+60 m（主跨 100 m）、墩顶梁高 h=5.5 m（≈L/18）、跨中梁高 2.8 m
      （≈L/36）、0 号块 12 m、合龙段 2 m、单箱单室桥宽 12 m、薄壁墩高 40 m。
    """
    return {
        "spans": "60+100+60",       # 跨径布置（边跨+主跨+边跨，可写 60,100,60）
        "n_seg": "12",              # 每个悬臂方向划分几个节段（每节段一个施工阶段）
        "l_tab": "12",              # 墩顶 0 号块长度（含两侧，单位 m）
        "l_close": "2",             # 合龙段长度（m）
        "h_top": "5.5",             # 墩顶梁高（m）
        "h_mid": "2.8",             # 跨中梁高（m）
        "b_girder": "12",           # 主梁桥宽 / 箱宽（m）
        "t_top": "0.32",            # 顶板厚（m，沿桥向不变）
        "t_bot_top": "0.9",         # 墩顶底板厚（m）
        "t_bot_mid": "0.32",        # 跨中底板厚（m）
        "t_web_top": "0.8",         # 墩顶腹板厚（m）
        "t_web_mid": "0.45",        # 跨中腹板厚（m）
        "h_pier": "40",             # 墩高（m）
        "b_pier": "6",              # 墩身横桥向宽（m）
        "t_pier": "3",              # 墩身顺桥向厚（m）
        "n_pier": "2",              # 墩身分几个单元
        "girder_mat": "C55",        # 主梁混凝土
        "pier_mat": "C40",          # 墩身混凝土
        "gamma": "25",              # 容重 kN/m3
        "creep": True,              # 是否考虑收缩徐变（时间依存材料）
        "humidity": "70",           # 环境年平均湿度 %
        "q2nd": "60",               # 二期恒载（桥面系）kN/m
        "days": {"pier": "30", "tab": "15", "seg": "12",
                 "cast": "20", "close": "20", "end": "10000"},
        "ages": {"pier": "28", "tab": "7", "seg": "3", "cast": "7", "close": "3"},
    }


def default_s2c():
    """简支变连续：两跨 20+20 m 预制 T 梁（第四讲上机实操算例。

    课件图上的尺寸：单跨 20 m、预制梁长 19.75 m、湿接缝 0.5 m、C40、梁高 1.5 m、
    腹板 0.3 m、翼缘 0.15 m；预制 7 d 后张拉梁内束、架设 4 d、现浇连续段 1 d、
    养护 7 d 后张拉连续束、撤临时体系。
    """
    return {
        "spans": "20+20",           # 跨径布置
        "n_div": "4",               # 每跨预制梁划分几个单元
        "l_joint": "0.5",           # 湿接缝长度（m）
        "h_girder": "1.5",          # 预制梁高（m）
        "b_girder": "2.0",          # T 梁翼缘宽（m）
        "t_web": "0.3",             # 腹板厚（m）
        "t_flange": "0.15",         # 翼缘（顶板）厚（m）
        "mat": "C40",               # 混凝土
        "gamma": "25",
        "creep": True,
        "humidity": "70",
        "q2nd": "30",               # 二期恒载 kN/m
        "days": {"precast": "7", "ps1": "1", "erect": "4", "ps2": "1",
                 "joint": "1", "cure": "7", "psc": "1", "trans": "1", "end": "10000"},
    }


def default_model():
    return {
        "mode": "cs",
        "flow": "cs",
        "subtype": SUB_FCM,
        "project": {"name": "", "outdir": "", "force": "KN", "dist": "M",
                    "heat": "KJ", "temp_unit": "C", "styp": "0"},
        SUB_FCM: default_fcm(),
        SUB_S2C: default_s2c(),
    }


def reset_model():
    """「清空全部」回到什么：两种子类型都回默认参数，但保留当前子类型与项目名。"""
    return default_model()


def _sub(model):
    """取出当前子类型的参数（缺项用默认值补全，老会话文件也能打开）。"""
    model = model or {}
    st = model.get("subtype")
    if st not in (SUB_FCM, SUB_S2C):
        st = SUB_FCM
    d = default_model()[st]
    got = model.get(st) or {}
    out = dict(d)
    for k, v in got.items():
        if k in ("days", "ages") and isinstance(v, dict):
            base = dict(d.get(k) or {})
            base.update(v)
            out[k] = base
        else:
            out[k] = v
    return st, out


def _project(model):
    p = dict((model or {}).get("project") or {})
    p.setdefault("name", "")
    p.setdefault("outdir", "")
    p.setdefault("force", "KN")
    p.setdefault("dist", "M")
    return p


def _days(p):
    d = p.get("days") or {}

    def g(k, dflt):
        try:
            v = float(str(d.get(k, dflt)))
        except (TypeError, ValueError):
            v = float(dflt)
        return max(1.0, v)
    return g


def _gamma(p):
    try:
        g = float(str(p.get("gamma", GAMMA_CONC)))
    except (TypeError, ValueError):
        g = GAMMA_CONC
    return g if g > 0 else GAMMA_CONC


def material_of(p, key, dflt):
    m = str(p.get(key) or dflt).strip().upper()
    return m if m in E_CONC else dflt


# ======================================================================
# 布置 A：连续刚构 · 悬臂浇筑（fcm）
# ======================================================================
def fcm_layout(p):
    """把 fcm 参数解析成完整的「节点 / 单元 / 组 / 荷载 / 施工阶段」布置。

    返回 dict（见文件末尾说明），参数不自洽时抛 LayoutError（中文原因）。
    """
    spans = _span_list(p.get("spans"))
    nspan = len(spans)
    if nspan < 3:
        raise LayoutError("连续刚构至少要 3 跨（边跨 + 主跨 + 边跨），现在只填了 %d 跨" % nspan)
    n = _posint(p.get("n_seg"), "每墩悬臂节段数", 1, 60)
    ltab = _pos(p.get("l_tab"), "0 号块长度")
    lcl = _pos(p.get("l_close"), "合龙段长度")
    htop = _pos(p.get("h_top"), "墩顶梁高")
    hmid = _pos(p.get("h_mid"), "跨中梁高")
    if htop < hmid - 1e-9:
        raise LayoutError("墩顶梁高（%g m）不能小于跨中梁高（%g m）—— 连续刚构是根部高、跨中低"
                          % (htop, hmid))
    bg = _pos(p.get("b_girder"), "主梁桥宽")
    ttop = _pos(p.get("t_top"), "顶板厚")
    tbot_top = _pos(p.get("t_bot_top"), "墩顶底板厚")
    tbot_mid = _pos(p.get("t_bot_mid"), "跨中底板厚")
    tw_top = _pos(p.get("t_web_top"), "墩顶腹板厚")
    tw_mid = _pos(p.get("t_web_mid"), "跨中腹板厚")
    hpier = _pos(p.get("h_pier"), "墩高")
    bpier = _pos(p.get("b_pier"), "墩身横桥向宽")
    tpier = _pos(p.get("t_pier"), "墩身顺桥向厚")
    npier = _posint(p.get("n_pier"), "墩身单元数", 1, 20)
    if tpier <= 1.2 + 1e-9 or bpier <= 1.2 + 1e-9:
        raise LayoutError("墩身尺寸太小：薄壁箱形墩的「顺桥向厚」「横桥向宽」都要 > 1.2 m"
                          "（现在 %.3g / %.3g；壁厚按 0.6 m 取）" % (tpier, bpier))
    gamma = _gamma(p)

    # ---- 截面尺寸的基本合理性（箱形截面能不能画出来）----
    for lab, tb in (("跨中", tbot_mid), ("墩顶", tbot_top)):
        if ttop + tb >= min(hmid, htop) - 1e-9:
            raise LayoutError("%s截面：顶板厚 %.3g + 底板厚 %.3g ≥ 梁高 %.3g，箱形截面画不出来"
                              % (lab, ttop, tb, hmid if lab == "跨中" else htop))
    if 2.0 * max(tw_top, tw_mid) >= bg - 1e-9:
        raise LayoutError("腹板厚太大：2×腹板厚（%.3g m）≥ 桥宽（%.3g m）" % (2.0 * max(tw_top, tw_mid), bg))
    if ltab >= min(spans) - lcl:
        raise LayoutError("0 号块长度 %.3g m 太长：比最短跨 %g m 还大" % (ltab, min(spans)))

    # ---- 支点里程 ----
    xsup = [0.0]
    for L in spans:
        xsup.append(xsup[-1] + L)
    total = xsup[-1]

    # ---- 各跨的悬臂节段长度：主跨按 (L - 0 号块 - 合龙段)/(2n) 反算；边跨沿用相邻主跨 ----
    lseg, lcast = {}, {}
    for s in range(1, nspan - 1):
        if spans[s] - ltab - lcl <= 1e-6:
            raise LayoutError("主跨 %.6g m 太短：扣掉 0 号块 %.3g m 和合龙段 %.3g m 后没有悬臂长度了"
                              % (spans[s], ltab, lcl))
        lseg[s] = (spans[s] - ltab - lcl) / (2.0 * n)
    lseg[0] = lseg[1]
    lseg[nspan - 1] = lseg[nspan - 2]
    for s in (0, nspan - 1):
        v = spans[s] - ltab / 2.0 - n * lseg[s] - lcl
        if v <= 1e-6:
            raise LayoutError(
                "边跨 %.6g m 太短：一个方向的悬臂长 %.4g m + 0 号块半宽 %.3g m + 合龙段 %.3g m "
                "就把边跨占满了（边跨现浇段长度 = %.4g m ≤ 0）。请加大边跨，或减少悬臂节段数。"
                % (spans[s], n * lseg[s], ltab / 2.0, lcl, v))
        lcast[s] = v

    # ---- 梁高沿桥向变化（根部高、跨中低，抛物线过渡；0 号块范围内保持墩顶梁高）----
    def depth_at(x):
        s = 0
        for i in range(nspan):
            if xsup[i] - 1e-9 <= x <= xsup[i + 1] + 1e-9:
                s = i
                break
        else:
            s = 0 if x < 0 else nspan - 1
        if 0 < s < nspan - 1:                     # 主跨：两侧都是墩
            xp = xsup[s] if (x - xsup[s]) <= (xsup[s + 1] - x) else xsup[s + 1]
            half = (xsup[s + 1] - xsup[s]) / 2.0
        else:                                     # 边跨：只有靠主跨那一端是墩
            xp = xsup[1] if s == 0 else xsup[nspan - 1]
            half = spans[s]
        den = half - ltab / 2.0
        d = max(0.0, abs(x - xp) - ltab / 2.0)
        u = 0.0 if den <= 1e-9 else min(1.0, d / den)   # u=0 在墩顶（梁最高），u=1 在跨中/桥台（梁最矮）
        return hmid + (htop - hmid) * (1.0 - u) * (1.0 - u)

    def girder_dims(x):
        h = depth_at(x)
        f = 0.0 if htop - hmid <= 1e-9 else (h - hmid) / (htop - hmid)
        f = max(0.0, min(1.0, f))
        tw = tw_mid + (tw_top - tw_mid) * f
        tb = tbot_mid + (tbot_top - tbot_mid) * f
        return [h, bg, tw, ttop, 0.0, tb]

    # ---- 沿桥向的单元划分（一个悬臂节段 = 一个单元；0 号块左右各一个单元；合龙段一个单元）----
    specs = []
    for s in range(nspan):
        if 0 < s < nspan - 1:                     # 主跨（两侧都是墩）
            specs.append(("TAB", ltab / 2.0, (s, "R")))
            for k in range(1, n + 1):
                specs.append(("SEG", lseg[s], (s, "R", k)))
            specs.append(("CLSM", lcl, (s,)))
            for k in range(n, 0, -1):
                specs.append(("SEG", lseg[s], (s + 1, "L", k)))
            specs.append(("TAB", ltab / 2.0, (s + 1, "L")))
        elif s == 0:                              # 左边跨：现浇段 + 合龙段 + 悬臂节段 + 0 号块
            specs.append(("CAST", lcast[0], (0, "L")))
            specs.append(("CLSS", lcl, (0, "L")))
            for k in range(n, 0, -1):
                specs.append(("SEG", lseg[0], (1, "L", k)))
            specs.append(("TAB", ltab / 2.0, (1, "L")))
        else:                                     # 右边跨（镜像）
            specs.append(("TAB", ltab / 2.0, (nspan - 1, "R")))
            for k in range(1, n + 1):
                specs.append(("SEG", lseg[s], (nspan - 1, "R", k)))
            specs.append(("CLSS", lcl, (s, "R")))
            specs.append(("CAST", lcast[s], (s, "R")))

    nodes = [{"x": 0.0, "z": 0.0, "tag": "G"}]
    elems, secs, sec_index = [], [], {}
    seg_eid, tab_eid, cast_eid, clss_eid, clsm_eid = {}, {}, {}, {}, {}
    x = 0.0
    for kind, length, meta in specs:
        x2 = x + length
        nodes.append({"x": round(x2, 9), "z": 0.0, "tag": "G"})
        n1, n2 = len(nodes) - 1, len(nodes)
        eid = len(elems) + 1
        if kind == "TAB":
            gname = "TAB%d" % meta[0]          # meta[0] 就是墩号（1..nspan-2）
        elif kind == "SEG":
            gname = "SG%d%s%02d" % (meta[0], meta[1], meta[2])
        elif kind == "CLSM":
            gname = "CLSM%d" % (meta[0] + 1)
        elif kind == "CLSS":
            gname = "CLSS%d" % (meta[0] + 1)
        else:
            gname = "CAST%d" % (meta[0] + 1)
        dims = girder_dims((x + x2) / 2.0)
        sid = _add_section(secs, sec_index, "B", dims, "CT", "GIR%d" % (len(secs) + 1))
        elems.append({"n1": n1, "n2": n2, "sec": sid, "mat": 1, "group": gname,
                      "part": "girder", "x1": x, "x2": x2, "x_mid": (x + x2) / 2.0,
                      "length": x2 - x, "area": secs[sid - 1]["area"]})
        if kind == "SEG":
            seg_eid[meta] = eid
        elif kind == "TAB":
            tab_eid[meta] = eid
        elif kind == "CAST":
            cast_eid[meta[0]] = eid
        elif kind == "CLSS":
            clss_eid[meta[0]] = eid
        elif kind == "CLSM":
            clsm_eid[meta[0]] = eid
        x = x2
    if abs(x - total) > 1e-6:
        raise LayoutError("内部布置误差：单元总长 %.6f m ≠ 桥长 %.6f m（这是程序 bug，请报告）" % (x, total))

    # ---- 墩身（薄壁箱形墩）：墩顶就是梁上对应节点，墩底固结 ----
    node_at = {}
    for i, nd in enumerate(nodes, start=1):
        node_at[round(nd["x"], 6)] = i
    pier_groups, pier_base = [], []
    for i in range(1, nspan):                 # 中间支点 1..nspan-1 各有一个墩
        top = node_at.get(round(xsup[i], 6))
        if top is None:
            raise LayoutError("内部错误：找不到墩顶节点 x=%g（请报告）" % xsup[i])
        prev = top
        gname = "PIER%d" % i
        pier_groups.append(gname)
        for j in range(1, npier + 1):
            z = -hpier * j / float(npier)
            nodes.append({"x": round(xsup[i], 9), "z": round(z, 9), "tag": "P"})
            nid = len(nodes)
            sid = _add_section(secs, sec_index, "B",
                               [tpier, bpier, 0.6, 0.6, 0.0, 0.6], "CC", "PIER")
            elems.append({"n1": prev, "n2": nid, "sec": sid, "mat": 2, "group": gname,
                          "part": "pier", "x1": xsup[i], "x2": xsup[i], "x_mid": xsup[i],
                          "length": hpier / float(npier), "area": secs[sid - 1]["area"]})
            prev = nid
        pier_base.append(prev)

    # ---- 结构组（组名 ASCII、唯一，和 MIDAS 样本一样：NAME, 节点表, 单元表, 0）----
    groups = {}
    for eid, el in enumerate(elems, start=1):
        g = groups.setdefault(el["group"], {"elems": [], "nodes": []})
        g["elems"].append(eid)
        for nn in (el["n1"], el["n2"]):
            if nn not in g["nodes"]:
                g["nodes"].append(nn)

    # ---- 边界组 ----
    e_cl = elems[cast_eid[0] - 1]
    e_cr = elems[cast_eid[nspan - 1] - 1]
    abut_nodes = [e_cl["n1"], e_cr["n2"]]                       # 两个桥台（边跨现浇段外端）
    scaf_nodes = [e_cl["n1"], e_cl["n2"], e_cr["n1"], e_cr["n2"]]  # 边跨现浇段的支架
    constraints = []
    for nid in pier_base:
        constraints.append({"nodes": [nid], "code": "111111", "group": "BC_PIER"})
    for nid in scaf_nodes:
        constraints.append({"nodes": [nid], "code": "011101", "group": "BC_SCAF"})
    for nid in abut_nodes:
        constraints.append({"nodes": [nid], "code": "011101", "group": "BC_ABUT"})
    bndr_names = ["BC_PIER", "BC_SCAF", "BC_ABUT"]

    # ---- 荷载组：自重 / 湿重（每个新浇节段一组）/ 边跨现浇段 / 合龙段 / 二期恒载 ----
    wc = []          # [{'group','elem','q'}]
    for k in range(1, n + 1):
        gname = "WC%d" % k
        for i in range(1, nspan):
            for side in ("L", "R"):
                if k == 1:
                    tip = elems[tab_eid[(i, side)] - 1]
                    tip_eid = tab_eid[(i, side)]
                else:
                    tip = elems[seg_eid[(i, side, k - 1)] - 1]
                    tip_eid = seg_eid[(i, side, k - 1)]
                new = elems[seg_eid[(i, side, k)] - 1]
                # 新浇节段总重 = 面积×容重×长度；摊到挂篮所在的端部单元上
                q = gamma * new["area"] * new["length"] / tip["length"]
                wc.append({"group": gname, "elem": tip_eid, "q": q})
    for s in (0, nspan - 1):
        e = elems[cast_eid[s] - 1]
        wc.append({"group": "WCCT", "elem": cast_eid[s], "q": gamma * e["area"]})
        e2 = elems[clss_eid[s] - 1]
        wc.append({"group": "WCCS1", "elem": clss_eid[s], "q": gamma * e2["area"]})
    for s in range(1, nspan - 1):
        e = elems[clsm_eid[s] - 1]
        wc.append({"group": "WCCS2", "elem": clsm_eid[s], "q": gamma * e["area"]})
    lgroups = ["Self"] + ["WC%d" % k for k in range(1, n + 1)] + ["WCCT", "WCCS1", "WCCS2", "SDL"]
    q2nd = max(0.0, _fl(p.get("q2nd", "0"), "二期恒载"))
    sdl_elems = [i for i, e in enumerate(elems, start=1) if e["part"] == "girder"]

    # ---- 施工阶段 ----
    days = _days(p)
    ages = p.get("ages") or {}

    def age(k, dflt):
        try:
            return max(0, int(round(float(str(ages.get(k, dflt))))))
        except (TypeError, ValueError):
            return int(dflt)

    def st(name, desc, dur, aelem=None, delem=None, abndr=None, dbndr=None, aload=None, dload=None):
        stages.append({"name": name, "desc": desc, "dur": int(round(dur)),
                       "aelem": aelem or [], "delem": delem or [],
                       "abndr": abndr or [], "dbndr": list(dbndr or []),
                       "aload": aload or [], "dload": dload or []})

    stages = []
    st("CS01", "墩身施工", days("pier", 30),
       aelem=[(g, age("pier", 28)) for g in pier_groups],
       abndr=[("BC_PIER", "ORIGINAL")], aload=[("Self", "FIRST")])
    st("CS02", "墩顶 0 号块浇筑", days("tab", 15),
       aelem=[("TAB%d" % i, age("tab", 7)) for i in range(1, nspan)],
       aload=[("WC1", "FIRST")])
    cs = 2
    dseg = days("seg", 12)
    for k in range(1, n + 1):
        cs += 1
        ael = []
        for i in range(1, nspan):
            for side in ("L", "R"):
                ael.append(("SG%d%s%02d" % (i, side, k), age("seg", 3)))
        st("CS%02d" % cs, "悬臂浇筑 %d 号节段" % k, dseg, aelem=ael,
           aload=[("WC%d" % (k + 1), "FIRST")] if k < n else [],
           dload=[("WC%d" % k, "FIRST")])
    dc = days("cast", 20)
    dcl = days("close", 20)
    cs += 1
    st("CS%02d" % cs, "边跨支架现浇段", dc,
       aelem=[("CAST%d" % (s + 1), age("cast", 7)) for s in (0, nspan - 1)],
       abndr=[("BC_SCAF", "DEFORMED")], aload=[("WCCT", "FIRST")])
    cs += 1
    st("CS%02d" % cs, "边跨合龙（撤支架）", dcl,
       aelem=[("CLSS%d" % (s + 1), age("close", 3)) for s in (0, nspan - 1)],
       abndr=[("BC_ABUT", "DEFORMED")], dbndr=["BC_SCAF"],
       dload=[("WCCT", "FIRST")], aload=[("WCCS1", "FIRST")])
    cs += 1
    st("CS%02d" % cs, "中跨合龙", dcl,
       aelem=[("CLSM%d" % (s + 1), age("close", 3)) for s in range(1, nspan - 1)],
       dload=[("WCCS1", "FIRST")], aload=[("WCCS2", "FIRST")])
    cs += 1
    st("CS%02d" % cs, "桥面系（二期恒载）", max(10, int(round(dc))),
       dload=[("WCCS2", "FIRST")], aload=[("SDL", "FIRST")])
    cs += 1
    st("CS%02d" % cs, "成桥运营（收缩徐变）", days("end", 10000))

    for sec in secs:                       # 每个箱形截面的特性都先算一遍，尺寸不成立就在这一步报
        d = sec["dims"]
        box_section_props(d[0], d[1], d[2], d[3], d[5])

    info = {
        "spans": spans, "xsup": xsup, "total": total, "nspan": nspan,
        "n_seg": n, "l_tab": ltab, "l_close": lcl, "lseg": lseg, "lcast": lcast,
        "h_top": htop, "h_mid": hmid, "h_pier": hpier,
        "girder_mat": material_of(p, "girder_mat", "C55"),
        "pier_mat": material_of(p, "pier_mat", "C40"),
    }
    return _layout("fcm", nodes, elems, secs, groups, constraints, bndr_names, lgroups,
                   wc, [{"group": "SDL", "elems": sdl_elems, "q": q2nd}] if q2nd > 0 else [],
                   stages, gamma, p, info)


def _layout(kind, nodes, elems, secs, groups, constraints, bndr_names, lgroups,
            wc, sdl, stages, gamma, p, info):
    return {"kind": kind, "nodes": nodes, "elems": elems, "sections": secs,
            "groups": groups, "constraints": constraints, "bndr_names": bndr_names,
            "load_groups": lgroups, "wc": wc, "sdl": sdl, "stages": stages,
            "gamma": gamma, "params": p, "info": info}


# ======================================================================
# 布置 B：简支变连续（s2c）
# ======================================================================
def s2c_layout(p):
    """简支变连续的布置：每跨一根预制 T 梁 + 墩顶湿接缝 + 临时/永久支承 + 体系转换。"""
    spans = _span_list(p.get("spans"))
    nspan = len(spans)
    if nspan < 2:
        raise LayoutError("简支变连续至少要 2 跨，现在只填了 %d 跨" % nspan)
    ndiv = _posint(p.get("n_div"), "每跨划分单元数", 1, 40)
    lj = _pos(p.get("l_joint"), "湿接缝长度")
    hg = _pos(p.get("h_girder"), "预制梁高")
    bg = _pos(p.get("b_girder"), "T 梁翼缘宽")
    tw = _pos(p.get("t_web"), "腹板厚")
    tf = _pos(p.get("t_flange"), "翼缘厚")
    gamma = _gamma(p)
    if tf >= hg - 1e-9:
        raise LayoutError("翼缘厚（%.3g m）要小于梁高（%.3g m）" % (tf, hg))
    if tw >= bg - 1e-9:
        raise LayoutError("腹板厚（%.3g m）要小于翼缘宽（%.3g m）" % (tw, bg))
    for L in spans:
        if L - lj <= 1e-6:
            raise LayoutError("跨径 %g m 太短：扣掉湿接缝 %g m 后没有预制梁长度了" % (L, lj))

    xsup = [0.0]
    for L in spans:
        xsup.append(xsup[-1] + L)
    total = xsup[-1]

    nodes, elems, secs, sec_index = [], [], [], {}
    sid = _add_section(secs, sec_index, "T", [hg, bg, tw, tf], "CC", "T梁")
    area = secs[sid - 1]["area"]

    # ---- 预制梁（每跨一根，按单元数等分）----
    span_nodes = []
    for i, L in enumerate(spans):
        xa = xsup[i] + (lj / 2.0 if i > 0 else 0.0)
        xb = xsup[i + 1] - (lj / 2.0 if i + 1 < nspan else 0.0)
        Lg = xb - xa
        if Lg <= 1e-6:
            raise LayoutError("第 %d 跨：湿接缝把跨长占满了，预制梁长度为 %.3g m ≤ 0" % (i + 1, Lg))
        ids = []
        for k in range(ndiv + 1):
            x = xa + Lg * k / float(ndiv)
            nodes.append({"x": round(x, 9), "z": 0.0, "tag": "G%d" % (i + 1)})
            ids.append(len(nodes))
        span_nodes.append(ids)
        for k in range(ndiv):
            x1 = xa + Lg * k / float(ndiv)
            x2 = xa + Lg * (k + 1) / float(ndiv)
            elems.append({"n1": ids[k], "n2": ids[k + 1], "sec": sid, "mat": 1,
                          "group": "G%d" % (i + 1), "part": "girder",
                          "x1": x1, "x2": x2, "x_mid": (x1 + x2) / 2.0,
                          "length": Lg / float(ndiv), "area": area})

    # ---- 湿接缝（每个中间支点两个单元，中间节点就是墩顶）----
    joint_elems, joint_center = [], []
    for i in range(1, nspan):
        xc = xsup[i]
        nl, nr = span_nodes[i - 1][-1], span_nodes[i][0]
        nodes.append({"x": round(xc, 9), "z": 0.0, "tag": "J%d" % i})
        nc = len(nodes)
        e1 = len(elems) + 1
        elems.append({"n1": nl, "n2": nc, "sec": sid, "mat": 1, "group": "J%d" % i,
                      "part": "joint", "x1": xc - lj / 2.0, "x2": xc, "x_mid": xc - lj / 4.0,
                      "length": lj / 2.0, "area": area})
        e2 = len(elems) + 1
        elems.append({"n1": nc, "n2": nr, "sec": sid, "mat": 1, "group": "J%d" % i,
                      "part": "joint", "x1": xc, "x2": xc + lj / 2.0, "x_mid": xc + lj / 4.0,
                      "length": lj / 2.0, "area": area})
        joint_elems.append((i, [e1, e2]))
        joint_center.append((i, nc))

    groups = {}
    for eid, el in enumerate(elems, start=1):
        g = groups.setdefault(el["group"], {"elems": [], "nodes": []})
        g["elems"].append(eid)
        for nn in (el["n1"], el["n2"]):
            if nn not in g["nodes"]:
                g["nodes"].append(nn)

    # ---- 边界：施工期临时支座 + 成桥永久支座 ----
    constraints = []
    for i in range(nspan):
        first, last = span_nodes[i][0], span_nodes[i][-1]
        constraints.append({"nodes": [first], "code": "111101", "group": "BCT%d" % (i + 1)})
        constraints.append({"nodes": [last], "code": "011101", "group": "BCT%d" % (i + 1)})
    constraints.append({"nodes": [span_nodes[0][0]], "code": "011101", "group": "BC_ABUT1"})
    for i, nc in joint_center:
        constraints.append({"nodes": [nc], "code": "111101", "group": "BC_PIER"})
    constraints.append({"nodes": [span_nodes[-1][-1]], "code": "011101", "group": "BC_ABUT2"})
    bndr_names = ["BCT%d" % (i + 1) for i in range(nspan)] + ["BC_ABUT1", "BC_PIER", "BC_ABUT2"]

    # ---- 荷载：湿接缝湿重 + 二期恒载 ----
    wc = []
    for i, eids in joint_elems:
        for eid in eids:
            wc.append({"group": "WCJ", "elem": eid, "q": gamma * area})
    q2nd = max(0.0, _fl(p.get("q2nd", "0"), "二期恒载"))
    sdl_elems = list(range(1, len(elems) + 1))
    lgroups = ["Self", "WCJ", "SDL"]

    # ---- 施工阶段：预制养护 → 张拉梁内束 → 架设 → 张拉 → 浇接缝 → 养护 →
    #               张拉连续束 → 撤临时体系 → 成桥运营 ----
    days = _days(p)

    def st(name, desc, dur, aelem=None, delem=None, abndr=None, dbndr=None, aload=None, dload=None):
        stages.append({"name": name, "desc": desc, "dur": int(round(dur)),
                       "aelem": aelem or [], "delem": delem or [],
                       "abndr": abndr or [], "dbndr": list(dbndr or []),
                       "aload": aload or [], "dload": dload or []})

    stages = []
    st("CS01", "预制梁 1、养护", days("precast", 7), aelem=[("G1", 1)],
       abndr=[("BCT1", "ORIGINAL")], aload=[("Self", "FIRST")])
    st("CS02", "张拉梁内预应力束 1", days("ps1", 1))
    cs = 2
    for i in range(2, nspan + 1):
        cs += 1
        st("CS%02d" % cs, "架设预制梁 %d" % i, days("erect", 4),
           aelem=[("G%d" % i, 1)], abndr=[("BCT%d" % i, "DEFORMED")])
        cs += 1
        st("CS%02d" % cs, "张拉梁内预应力束 %d" % i, days("ps2", 1))
    cs += 1
    st("CS%02d" % cs, "浇筑湿接缝", days("joint", 1),
       aelem=[("J%d" % i, 1) for i in range(1, nspan)], aload=[("WCJ", "FIRST")])
    cs += 1
    st("CS%02d" % cs, "湿接缝养护", days("cure", 7), dload=[("WCJ", "FIRST")])
    cs += 1
    st("CS%02d" % cs, "张拉连续束", days("psc", 1))
    cs += 1
    st("CS%02d" % cs, "撤除临时体系（体系转换）", days("trans", 1),
       abndr=[("BC_ABUT1", "DEFORMED"), ("BC_PIER", "DEFORMED"), ("BC_ABUT2", "DEFORMED")],
       dbndr=["BCT%d" % (i + 1) for i in range(nspan)])
    cs += 1
    st("CS%02d" % cs, "成桥运营", days("end", 10000), aload=[("SDL", "FIRST")])

    info = {"spans": spans, "xsup": xsup, "total": total, "nspan": nspan,
            "n_div": ndiv, "l_joint": lj, "h_girder": hg, "mat": material_of(p, "mat", "C40")}
    return _layout("s2c", nodes, elems, secs, groups, constraints, bndr_names, lgroups,
                   wc, [{"group": "SDL", "elems": sdl_elems, "q": q2nd}] if q2nd > 0 else [],
                   stages, gamma, p, info)


def layout_of(model):
    """按当前子类型算布置；参数不自洽时抛 LayoutError。"""
    st, p = _sub(model)
    return st, p, (fcm_layout(p) if st == SUB_FCM else s2c_layout(p))


# ======================================================================
# 箱形截面特性 + *SECT-PSCVALUE（真实形状的 PSC 一般截面）
# ----------------------------------------------------------------------
# 为什么主梁不用 *SECTION DBUSER 的「箱形 B」：那要依赖 MIDAS 形状库里 D1..D6
# 的尺寸顺序（本机找不到箱形的真实导出样本可对照）。改成 *SECT-PSCVALUE 以后，
# 面积/惯矩是我们自己算出来直接写进去的，MIDAS 不再二次解释尺寸，性质一定对；
# 而且悬臂施工教程导出的 FCM.mct 里主梁本来就是 PSC 截面，写法更贴近真实模型。
# 格式照 arch_core.py 里「已在 MIDAS 里导入并跑过施工阶段分析」的写法。
# ======================================================================
def box_section_props(H, B, tw, tf1, tf2):
    """单箱单室（矩形外轮廓挖掉矩形内轮廓）的截面特性；原点在截面左下角。"""
    H, B, tw, tf1, tf2 = float(H), float(B), float(tw), float(tf1), float(tf2)
    hi = H - tf1 - tf2
    bi = B - 2.0 * tw
    if bi <= 1e-6 or hi <= 1e-6:
        raise LayoutError("箱形截面尺寸不成立：H=%.3g, B=%.3g, 腹板厚=%.3g, 顶板=%.3g, 底板=%.3g"
                          "（内轮廓高 %.3g、宽 %.3g ≤ 0）" % (H, B, tw, tf1, tf2, hi, bi))
    A1, z1 = B * H, H / 2.0
    A2, z2 = bi * hi, tf2 + hi / 2.0
    A = A1 - A2
    if A <= 1e-9:
        raise LayoutError("箱形截面面积算出来是 0，请检查尺寸")
    zc = (A1 * z1 - A2 * z2) / A
    iyy = (B * H ** 3 / 12.0 + A1 * (z1 - zc) ** 2) - (bi * hi ** 3 / 12.0 + A2 * (z2 - zc) ** 2)
    izz = (H * B ** 3 - hi * bi ** 3) / 12.0
    ip = iyy + izz
    ixx = (A ** 4) / (40.0 * ip) if ip > 1e-12 else 0.0      # 圣维南扭转：沿用 wiz_common 的近似
    return {"A": A, "Cy": B / 2.0, "Cz": zc, "Iyy": iyy, "Izz": izz, "Ixx": ixx,
            "CyP": B / 2.0, "CyM": B / 2.0, "CzP": H - zc, "CzM": zc,
            "PERI_OUT": 2.0 * (B + H), "PERI_IN": 2.0 * (bi + hi), "H": H, "B": B,
            "inner": (tw, tf2, bi, hi)}


def psc_section_lines(index, name, dims, offset="CT"):
    """一个箱形截面写成 *SECT-PSCVALUE 的 8 行（含外轮廓 OPOLY / 孔洞 IPOLY）。"""
    H, B, tw, tf1 = dims[0], dims[1], dims[2], dims[3]
    tf2 = dims[5] if len(dims) > 5 else dims[3]
    sp = box_section_props(H, B, tw, tf1, tf2)
    a = sp["A"]
    name = wc.aname(name, "SEC%d" % index)
    L = []
    add = L.append
    add(" SECT=%4d, PSC       , %-17s , %s, 0, 0, 0, 0, 0, 0, YES, NO, VALU, NO, NO, "
        % (index, name, offset))
    add("        %s, %s, %s, %s, %s, %s"
        % (_numg(a), _numg(a * 5.0 / 6.0), _numg(a * 5.0 / 6.0),
           _numg(sp["Ixx"]), _numg(sp["Iyy"]), _numg(sp["Izz"])))
    add("        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s"
        % (_numg(sp["CyP"]), _numg(sp["CyM"]), _numg(sp["CzP"]), _numg(sp["CzM"]),
           _numg(a * sp["CzP"] / 8.0), _numg(a * sp["CyP"] / 8.0),
           _numg(sp["PERI_OUT"]), _numg(sp["PERI_IN"]), _numg(sp["Cy"]), _numg(sp["Cz"])))
    add("        %s, %s, %s, %s, %s, %s, %s, %s"
        % (_numg(-sp["CyM"]), _numg(sp["CyP"]), _numg(sp["CyP"]), _numg(-sp["CyM"]),
           _numg(sp["CzP"]), _numg(sp["CzP"]), _numg(-sp["CzM"]), _numg(-sp["CzM"])))
    add("        0.3, 0.2, 0.1, 0.1")
    add("        YES, 0, 0, YES, , YES, , YES, , 0, YES, , YES, , YES, ")
    add("       OPOLY=" + ", ".join(_numg(v) for p in ((0, 0), (B, 0), (B, H), (0, H))
                                   for v in p))
    _tw, _tf2, bi, hi = sp["inner"]
    hole = [(_tw, _tf2), (_tw, _tf2 + hi), (B - _tw, _tf2 + hi), (B - _tw, _tf2)]
    add("       IPOLY=" + ", ".join(_numg(v) for p in hole for v in p))
    return L


# ======================================================================
# MCT 生成
# ======================================================================
def _materials(lay):
    """返回 [(mat号, 强度等级, 用途)]。"""
    info = lay["info"]
    if lay["kind"] == SUB_FCM:
        g = info.get("girder_mat", "C55")
        pr = info.get("pier_mat", "C40")
        if pr == g:
            return [(1, g, "主梁 / 墩身")]
        return [(1, g, "主梁"), (2, pr, "墩身")]
    return [(1, info.get("mat", "C40"), "预制梁 / 湿接缝")]


def _tdm_blocks(mats, humidity, tdm_on):
    """时间依存材料（收缩徐变 + 抗压强度发展），照 FCM.mct / 拱桥流程已验证的写法。

    注意：CEB-FIP 里填的是 28 天平均抗压强度 fcm（kN/m2），不是弹性模量。
    """
    if not tdm_on:
        return []
    L = ["", "*TDM-TYPE    ; Time Dependent Material (Creep/Shrinkage)",
         "; NAME=NAME, CODE, STR, HU, VOL, AGE, CFACTA, CFACTB, TYPE",
         "; NAME=NAME, CODE, STR, HU, MSIZE, CTYPE, AGE     ; CODE=CEB1990,KS,CEB1978,KSCE 2010,KCI-USD12,CEB-FIP2010"]
    for _i, grade, _use in mats:
        fck = FCK_CONC.get(grade, 32.4e3)
        fcm = fck + 8.0e3
        L.append("   NAME=TDM_%s, CEB, %.6g, %s, 1, NR, 3, 0" % (grade, fcm, humidity))
    L += ["", "*TDM-ELAST    ; Time Dependent Material(Comp. Strength)",
          "; NAME=NAME, TYPE, CODE, STRENGTH, iCTYPE"]
    for _i, grade, _use in mats:
        fck = FCK_CONC.get(grade, 32.4e3)
        fcm = fck + 8.0e3
        L.append("   NAME=TDM_%s, CODE, CEB-FIP, %.6g, 2" % (grade, fcm))
    L += ["", "*TDM-LINK    ; Time Dependent Material Link",
          "; iMAT, TDM-TYPE1(CREEP/SHRINKAGE), TDM-TYPE2(ELASTICITY)"]
    for i, grade, _use in mats:
        L.append("   %d, TDM_%s, TDM_%s" % (i, grade, grade))
    return L


def build_mct(model):
    """生成施工阶段模型的 MCT 文本（GBK 写文件即可）。"""
    st, p, lay = layout_of(model)
    prj = _project(model)
    name = (prj.get("name") or "").strip() or \
        ("连续刚构_悬臂浇筑" if st == SUB_FCM else "简支变连续")
    gamma = lay["gamma"]
    info = lay["info"]
    try:
        hum = float(str(p.get("humidity", "70")))
    except (TypeError, ValueError):
        hum = 70.0
    tdm_on = bool(p.get("creep", False))
    mats = _materials(lay)

    out = []

    def add(s=""):
        out.append(s)

    # ---------------- 抬头 ----------------
    add("; " + "=" * 74)
    add("; MIDAS Civil NX — MCT 命令文件（施工阶段梁桥 · %s）" % SUB_LABEL[st])
    add("; 由「MIDAS 建模向导」生成  " + datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    add("; 项目名称：%s" % name)
    add("; 导入方法：MIDAS Civil NX → 文件 → 导入 → MCT 命令文件（文件名用 .mct）")
    add("; 结构类型：三维结构、但整体在 X-Z 平面内（*STRUCTYPE 第 1 字段 = 0；"
        "所有支承都按样本习惯把平面外 DOF 一并约束）")
    add("; 施工阶段：共 %d 个（见 *STAGE），组名/单元名一一对应，可直接在「施工阶段」里核对" % len(lay["stages"]))
    add("; " + "=" * 74)
    add("")
    add("*VERSION")
    add("   9.6.5")
    add("*PRODUCT-SOLVER-TYPE")
    add("   NX-FES")
    add("*UNIT    ; Unit System")
    add("; FORCE, LENGTH, HEAT, TEMPER")
    add("   KN, M, KJ, C")
    add("*STRUCTYPE    ; Structure Type")
    add("; iSTYP, iMASS, iSMAS, bMASSOFFSET, bSELFWEIGHT, GRAV, TEMPER, bALIGNBEAM, bALIGNSLAB, bROTRIGID")
    add("   0, 1, 1, NO, NO, 9.806, 0, NO, NO, NO")
    add("")

    # ---------------- 节点 / 单元 ----------------
    add("*NODE    ; Nodes")
    add("; iNO, X, Y, Z")
    for i, nd in enumerate(lay["nodes"], start=1):
        add("   %d, %s, 0, %s" % (i, _num(nd["x"], 10), _num(nd["z"], 10)))
    add("")
    add("*ELEMENT    ; Elements")
    add("; iEL, TYPE, iMAT, iPRO, iN1, iN2, ANGLE, iSUB")
    for i, el in enumerate(lay["elems"], start=1):
        add("   %d, BEAM, %d, %d, %d, %d, 0, 0"
            % (i, el.get("mat", 1), el["sec"], el["n1"], el["n2"]))
    add("")

    # ---------------- 组 ----------------
    add("*GROUP    ; Group")
    add("; NAME, NODE_LIST, ELEM_LIST, PLANE_TYPE")
    for gname, g in lay["groups"].items():
        add("   %s, %s, %s, 0" % (gname, _ids(g["nodes"]), _ids(g["elems"])))
    add("")
    add("*BNDR-GROUP    ; Boundary Group")
    add("; NAME, AUTOTYPE")
    for gname in lay["bndr_names"]:
        add("   %s, 0" % gname)
    add("")
    add("*LOAD-GROUP    ; Load Group")
    add("; NAME")
    for gname in lay["load_groups"]:
        add("   %s" % gname)
    add("")

    # ---------------- 材料 / 时间依存 ----------------
    add("*MATERIAL    ; Material")
    add("; iMAT, TYPE, MNAME, SPHEAT, HEATCO, PLAST, TUNIT, bMASS, DAMPRATIO, [DATA1]")
    add("; [DATA1] : 1, STANDARD, CODE, DB, USEELAST, ELAST  /  2, ELAST, POISN, THERMAL, DEN, MASS")
    for i, grade, _use in mats:
        add("   %d, CONC, %s, 0, 0, , C, NO, 0.05, 2, %.6g, 0.2, 1e-05, %s, 0"
            % (i, grade, E_CONC.get(grade, 3.45e7), _num(gamma)))
    for ln in _tdm_blocks(mats, _num(hum), tdm_on):
        add(ln)
    add("")

    # ---------------- 截面 ----------------
    if st == SUB_FCM:
        # 主梁 / 墩身都是箱形：用 PSC 一般截面把算好的面积、惯矩直接写进去（性质一定对）
        add("*SECT-PSCVALUE    ; PSC Value / General Section（箱形主梁 + 薄壁墩，真实形状）")
        add("; SECT=iSEC, TYPE, SNAME, [OFFSET], bSD, bWE, SHAPE, bBU, bEQ, SNAME2(PSC VAL)")
        add(";      AREA, ASy, ASz, Ixx, Iyy, Izz                                    ; 2nd line")
        add(";      CyP, CyM, CzP, CzM, QyB, QzB, PERI_OUT, PERI_IN, Cy, Cz          ; 3rd line")
        add(";      Y1, Y2, Y3, Y4, Z1, Z2, Z3, Z4                                   ; 4th line")
        add(";      HT, BT, T1, T2                                                   ; 5th line(PSC)")
        add(";      bSHEARCHK, Z1, Z3, bAUTO_QY1, QY1, ..., TOR, bAUTO_SHR1, SHR1, ... ; 6th line(PSC)")
        add(";      OPOLY=X1, Y1, ... 外轮廓（逆时针）；IPOLY=... 孔洞（顺时针）")
        for sec in lay["sections"]:
            for ln in psc_section_lines(sec["index"], sec["name"], sec["dims"], sec["offset"]):
                add(ln)
        add("")
    else:
        # 预制 T 梁：DBUSER 参数化形状（这条写法已在梁桥流程的成果里实跑验证过）
        add("*SECTION    ; Section Properties")
        add("; iSEC, TYPE, SNAME, [OFFSET], bSD, bWE, SHAPE, [DATA1], [DATA2]")
        add("; iSEC, TYPE, SNAME, [OFFSET], bSD, bWE, SHAPE, BLT, D1, ..., D8, iCEL")
        for sec in lay["sections"]:
            shape = wc.SHAPE_BY_CODE.get(sec["shape"]) or wc.SHAPE_BY_CODE["SB"]
            nslot = len(shape[2])
            vals = [_num(v) for v in sec["dims"][:nslot]]
            vals += ["0"] * max(0, wc.DIM_SLOTS - len(vals))
            add("   %d, DBUSER, %s, %s, 0, 0, 0, 0, 0, 0, YES, NO, %s, 2, %s"
                % (sec["index"], wc.aname(sec["name"], "SEC%d" % sec["index"]), sec["offset"],
                   sec["shape"], ", ".join(vals)))
        add("")

    # ---------------- 荷载工况 / 约束 ----------------
    add("*STLDCASE    ; Static Load Cases")
    add("; LCNAME, LCTYPE, DESC")
    add("   Self, CS, 自重")
    add("   WC, CS, 湿重（新浇混凝土）")
    add("   SDL, CS, 二期恒载")
    add("")
    add("*CONSTRAINT    ; Supports")
    add("; NODE_LIST, CONST(Dx,Dy,Dz,Rx,Ry,Rz), GROUP")
    for c in lay["constraints"]:
        add("   %s, %s, %s" % (_ids(c["nodes"]), c["code"], c["group"]))
    add("")

    # ---------------- 荷载 ----------------
    add("*USE-STLD, Self")
    add("; *SELFWEIGHT, X, Y, Z, GROUP")
    add("*SELFWEIGHT    ; Self Weight")
    add("   0, 0, -1, Self")
    add("")
    if lay["wc"]:
        add("*USE-STLD, WC")
        add("*BEAMLOAD    ; Element Beam Loads（湿重：新浇混凝土按面积×容重折成均布荷载）")
        add("; ELEM_LIST, CMD, TYPE, DIR, bPROJ, bECCEN, ECCDIR, I-END, J-END, bJ-END, "
            "D1, P1, D2, P2, D3, P3, D4, P4, GROUP, bADDITIONAL, ADD_I-END, ADD_J-END, bADDITIONAL_J-END")
        add("; 湿重加在「已经浇好、挂挂篮的那个节段」上；等新节段混凝土硬化、单元激活的那一步再用 DLOAD 卸掉。")
        for it in lay["wc"]:
            add("     %d, BEAM   , UNILOAD, GZ, NO , NO, LY, , , , 0, %s, 1, %s, 0, 0, 0, 0, %s, NO, 0, 0, NO, "
                % (it["elem"], _numg(-it["q"]), _numg(-it["q"]), it["group"]))
        add("")
    if lay["sdl"]:
        add("*USE-STLD, SDL")
        add("*BEAMLOAD    ; Element Beam Loads（二期恒载 / 桥面系）")
        add("; ELEM_LIST, CMD, TYPE, DIR, bPROJ, bECCEN, ECCDIR, I-END, J-END, bJ-END, "
            "D1, P1, D2, P2, D3, P3, D4, P4, GROUP, bADDITIONAL, ADD_I-END, ADD_J-END, bADDITIONAL_J-END")
        for it in lay["sdl"]:
            add("     %s, BEAM   , UNILOAD, GZ, NO , NO, LY, , , , 0, %s, 1, %s, 0, 0, 0, 0, %s, NO, 0, 0, NO, "
                % (_ids(it["elems"]), _numg(-it["q"]), _numg(-it["q"]), it["group"]))
        add("")

    # ---------------- 施工阶段 ----------------
    add("*STAGE    ; Define Construction Stage")
    add("; NAME=NAME, DURATION, bSAVESTAGE, bSAVESTEP, bINCRESTEP, INCRESTEP  ; line 1")
    add("; STEP=DAY1, DAY2, ...                                               ; line 2")
    add("; AELEM=GROUP1, AGE1, GROUP2, AGE2, ...                              ; line 3")
    add("; DELEM=GROUP1, REDIST1, GROUP2, REDIST2, ...                        ; line 4")
    add("; ABNDR=BGROUP1, POS1, BGROUP2, POS2, ...                            ; line 5")
    add("; DBNDR=BGROUP1, BGROUP2, ...                                       ; line 6")
    add("; ALOAD=LGROUP1, DAY1, LGROUP2, DAY2, ...                            ; line 7")
    add("; DLOAD=LGROUP1, DAY1, LGROUP2, DAY2, ...                            ; line 8")
    for s in lay["stages"]:
        add("; %s  %s" % (s["name"], s["desc"]))
        add("  NAME=%s, %d, YES, YES, NO, 5" % (s["name"], s["dur"]))
        for ln in _wrap_stage_field("AELEM", ["%s, %d" % (g, a) for g, a in s["aelem"]]):
            add(ln)
        for ln in _wrap_stage_field("DELEM", ["%s, %s" % (g, a) for g, a in s["delem"]]):
            add(ln)
        for ln in _wrap_stage_field("ABNDR", ["%s, %s" % (g, pos) for g, pos in s["abndr"]]):
            add(ln)
        for ln in _wrap_stage_field("DBNDR", list(s["dbndr"])):
            add(ln)
        for ln in _wrap_stage_field("ALOAD", ["%s, %s" % (g, d) for g, d in s["aload"]]):
            add(ln)
        for ln in _wrap_stage_field("DLOAD", ["%s, %s" % (g, d) for g, d in s["dload"]]):
            add(ln)
    add("")

    # ---------------- 施工阶段分析控制 ----------------
    add("*STAGE-CTRL    ; Construction Stage Analysis Control Data")
    add("; bLAST-FINAL, FINAL-STAGE, CPFC, bEXT_REPL, bCALC-CFF, bCALC-CSP, bAPPLY-IMF,                    \\ ; line 1")
    add("; bCONV, bTRUSS, bBEAM, bITD, iITD, GROUP,                                                        \\ ; line 1")
    add("; bSAVE-OCS, bLFFC, LFFGR, bCAMBER, bSELFCONS, bCHANGE-CABLE                                         ; line 1")
    add("; bINC-NLA, iNLA-TYPE, bIEMF, iLSTEP, iMAXITER, bENEG, EV, bDISP, DV, bFORC, FV, CF, BSSTEP, ADSTEP  ; line 2")
    add("; bINC-PDL, iITER, TOL                                                                               ; line 3")
    add("; bINC-TDE, bCNS, TYPE, iITER, TOL, bTTLE_CS, bRCE, bVAR, bTTLE_ES, iTTLE_ES, bAPPLY-ELA             ; line 4")
    add("; bOUCC, bITS, iITS, bATS, iT10, iT100, iT1K, iT5K, iT10K                                            ; line 5")
    add("; bSD, iSDOPT, SDCONST, iBSC, bSDLE, GLC1, GLC2, ...                                                 ; line 6")
    add(STAGE_CTRL_L1 % ("YES" if tdm_on else "NO"))
    add("  0 ")
    add("  NO")
    add("  YES, YES, BOTH, 5, 0.01, YES, NO, YES, YES, 0, NO")
    add("  NO, NO, 2, YES, 2, 5, 7, 10, 20")
    add("  NO, , , 1, NO")
    add("")
    add("*ENDDATA")
    return "\r\n".join(out) + "\r\n"


# ======================================================================
# 校验（界面上每一步都会调，返回空列表 = 这一步没问题）
# ======================================================================
DAY_KEYS = {
    SUB_FCM: [("pier", "墩身施工"), ("tab", "0 号块"), ("seg", "每个悬臂节段"),
              ("cast", "边跨现浇段"), ("close", "合龙段"), ("end", "成桥运营期")],
    SUB_S2C: [("precast", "预制养护"), ("ps1", "张拉梁内束 1"), ("erect", "架设"),
              ("ps2", "张拉梁内束 2"), ("joint", "浇筑湿接缝"), ("cure", "接缝养护"),
              ("psc", "张拉连续束"), ("trans", "体系转换"), ("end", "成桥运营期")],
}


def _check_params(st, p, out):
    """数值层面的检查（不依赖几何）。"""
    # 容重 / 二期恒载
    try:
        g = float(str(p.get("gamma", "")))
        if g <= 0:
            out.append("容重必须大于 0（现在是 %g kN/m3）" % g)
    except (TypeError, ValueError):
        out.append("容重「%s」不是数字。" % p.get("gamma"))
    try:
        q2 = float(str(p.get("q2nd", "")))
        if q2 < 0:
            out.append("二期恒载不能是负数（现在是 %g kN/m）" % q2)
    except (TypeError, ValueError):
        out.append("二期恒载「%s」不是数字。" % p.get("q2nd"))
    # 时间依存
    if p.get("creep"):
        try:
            hum = float(str(p.get("humidity", "")))
        except (TypeError, ValueError):
            hum = -1
        if not (30.0 <= hum <= 100.0):
            out.append("环境年平均湿度要在 30~100 %% 之间（现在是 %s），CEB-FIP 的收缩徐变按这个算"
                       % p.get("humidity"))
    # 施工阶段持续时间
    d = p.get("days") or {}
    for key, zh in DAY_KEYS[st]:
        v = d.get(key)
        try:
            f = float(str(v))
        except (TypeError, ValueError):
            out.append("施工阶段「%s」的持续时间「%s」不是数字。" % (zh, v))
            continue
        if f <= 0:
            out.append("施工阶段「%s」的持续时间必须大于 0 天。" % zh)
    # 材料
    if st == SUB_FCM:
        for key, zh, dflt in (("girder_mat", "主梁混凝土", "C55"), ("pier_mat", "墩身混凝土", "C40")):
            v = str(p.get(key) or dflt).strip().upper()
            if v not in E_CONC:
                out.append("%s「%s」不认识（可选：%s）" % (zh, v, "、".join(CONC_GRADES)))
    else:
        v = str(p.get("mat") or "C40").strip().upper()
        if v not in E_CONC:
            out.append("混凝土「%s」不认识（可选：%s）" % (v, "、".join(CONC_GRADES)))


def _check_layout(lay, out):
    """布置层面的自检：组 ↔ 阶段引用、支承、湿重单元、截面。"""
    groups = set(lay["groups"].keys())
    bndrs = set(lay["bndr_names"])
    lgrps = set(lay["load_groups"])
    nelem = len(lay["elems"])
    for s in lay["stages"]:
        for g, _a in s["aelem"] + s["delem"]:
            if g not in groups:
                out.append("内部错误：施工阶段 %s 激活的结构组「%s」不存在（请报告）" % (s["name"], g))
        for g, _p in s["abndr"]:
            if g not in bndrs:
                out.append("内部错误：施工阶段 %s 激活的边界组「%s」不存在（请报告）" % (s["name"], g))
        for g in s["dbndr"]:
            if g not in bndrs:
                out.append("内部错误：施工阶段 %s 钝化的边界组「%s」不存在（请报告）" % (s["name"], g))
        for g, _d in s["aload"] + s["dload"]:
            if g not in lgrps:
                out.append("内部错误：施工阶段 %s 的荷载组「%s」不存在（请报告）" % (s["name"], g))
    for it in lay["wc"] + lay["sdl"]:
        for eid in ([it["elem"]] if "elem" in it else it["elems"]):
            if not (1 <= eid <= nelem):
                out.append("内部错误：荷载引用了不存在的单元 %d（请报告）" % eid)
    # 结构稳定性：至少要有一组约束把整体约束住
    if not lay["constraints"]:
        out.append("模型没有任何支承，结构不稳定。")
    # 施工阶段第一步要激活自重，否则整个施工过程都没有自重
    if lay["stages"] and not any(g == "Self" for g, _d in lay["stages"][0]["aload"]):
        out.append("内部错误：第一个施工阶段没有激活自重（请报告）")


def issues(model, sid):
    """这一步的问题列表（空 = 可以进入下一步）。sids: cs_type / cs_section / cs_stage / cs_check / cs_export"""
    out = []
    model = model or {}
    if model.get("subtype") not in (SUB_FCM, SUB_S2C):
        return ["第 1 步先选一个子类型：连续刚构 · 悬臂浇筑 / 简支变连续。"]
    st, p = _sub(model)
    _check_params(st, p, out)
    lay = None
    try:
        lay = fcm_layout(p) if st == SUB_FCM else s2c_layout(p)
    except LayoutError as exc:
        out.append(str(exc))
    except Exception as exc:                       # noqa: BLE001  程序 bug 也要能看见
        out.append("布置计算出错（%s: %s），请检查参数，或把这个提示报告给老师。" % (type(exc).__name__, exc))
    if lay is not None:
        _check_layout(lay, out)
    if sid == "cs_export" and lay is not None and not out:
        try:
            build_mct(model)
        except Exception as exc:                   # noqa: BLE001
            out.append("生成 MCT 时出错（%s: %s）" % (type(exc).__name__, exc))
    return out


# ======================================================================
# 数据表 / 阶段表（界面 + 成果文件用）
# ======================================================================
def stage_table(model):
    """施工阶段表：[(编号, 名称, 持续天数, 激活, 钝化/卸除), ...]（渲染界面的表格用）。"""
    _st, _p, lay = layout_of(model)
    rows = []
    for s in lay["stages"]:
        act = []
        if s["aelem"]:
            act.append("单元：" + "、".join(g for g, _a in s["aelem"]))
        if s["abndr"]:
            act.append("边界：" + "、".join(g for g, _pos in s["abndr"]))
        if s["aload"]:
            act.append("荷载：" + "、".join(g for g, _d in s["aload"]))
        rem = []
        if s["dbndr"]:
            rem.append("边界：" + "、".join(s["dbndr"]))
        if s["dload"]:
            rem.append("荷载：" + "、".join(g for g, _d in s["dload"]))
        rows.append((s["name"], s["desc"], str(s["dur"]),
                     "；".join(act) or "（只推进时间）", "；".join(rem) or "—"))
    return rows


def summary(model):
    """界面上「一眼看清」的几行摘要。"""
    st, p, lay = layout_of(model)
    info = lay["info"]
    L = []
    if st == SUB_FCM:
        L.append("桥型：%s" % SUB_LABEL[st])
        L.append("跨径：%s m，全长 %.2f m；墩高 %.1f m；每墩每方向 %d 个悬臂节段"
                 % ("+".join(_num(x) + "" for x in info["spans"]), info["total"],
                    info["h_pier"], info["n_seg"]))
        L.append("主梁：墩顶梁高 %.2f m → 跨中梁高 %.2f m（变截面，按多段等截面输出）；箱宽 %.2f m"
                 % (info["h_top"], info["h_mid"], float(str(p.get("b_girder", "0")))))
        L.append("节点 %d 个、单元 %d 个（梁 %d + 墩 %d）、截面 %d 种、结构组 %d 个"
                 % (len(lay["nodes"]), len(lay["elems"]),
                    sum(1 for e in lay["elems"] if e["part"] == "girder"),
                    sum(1 for e in lay["elems"] if e["part"] == "pier"),
                    len(lay["sections"]), len(lay["groups"])))
    else:
        L.append("桥型：%s" % SUB_LABEL[st])
        L.append("跨径：%s m，全长 %.2f m；湿接缝 %.2f m；每跨 %d 个单元"
                 % ("+".join(_num(x) for x in info["spans"]), info["total"],
                    info["l_joint"], info["n_div"]))
        L.append("预制 T 梁：梁高 %.2f m、翼缘宽 %.2f m、腹板厚 %.2f m、翼缘厚 %.2f m"
                 % (info["h_girder"], float(str(p.get("b_girder", "0"))),
                    float(str(p.get("t_web", "0"))), float(str(p.get("t_flange", "0")))))
        L.append("节点 %d 个、单元 %d 个（预制梁 %d + 湿接缝 %d）、截面 %d 种、结构组 %d 个"
                 % (len(lay["nodes"]), len(lay["elems"]),
                    sum(1 for e in lay["elems"] if e["part"] == "girder"),
                    sum(1 for e in lay["elems"] if e["part"] == "joint"),
                    len(lay["sections"]), len(lay["groups"])))
    L.append("边界组 %d 个：%s" % (len(lay["bndr_names"]), "、".join(lay["bndr_names"])))
    L.append("荷载组 %d 个；施工阶段 %d 个；容重 %g kN/m3；收缩徐变：%s"
             % (len(lay["load_groups"]), len(lay["stages"]), lay["gamma"],
                "考虑（时间依存材料 + *STAGE-CTRL 的 bITD=YES）" if p.get("creep") else "不考虑"))
    return L


def data_report(model):
    """人可读的数据表（『生成成果』里写进 *_数据表.txt）。"""
    st, p, lay = _sub(model), None, None
    st, p = _sub(model)
    st, p, lay = layout_of(model)
    info = lay["info"]
    prj = _project(model)
    L = []
    add = L.append
    add("=" * 78)
    add("施工阶段梁桥 —— 模型数据表")
    add("=" * 78)
    add("项目名称：%s" % ((prj.get("name") or "").strip() or "（未命名）"))
    add("桥型子类型：%s" % SUB_LABEL[st])
    add("生成时间：%s" % datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    add("单位制：力 %s，长度 %s；容重 %g kN/m3" % (prj.get("force", "KN"), prj.get("dist", "M"), lay["gamma"]))
    add("")
    add("-" * 78)
    add("一、总体布置")
    add("-" * 78)
    if st == SUB_FCM:
        add("跨径布置：%s m（共 %d 跨，全长 %.2f m）"
            % (" + ".join(_num(x) for x in info["spans"]), info["nspan"], info["total"]))
        add("墩高：%.2f m；每墩每个方向的悬臂节段数：%d 个" % (info["h_pier"], info["n_seg"]))
        add("0 号块长度：%.2f m；合龙段长度：%.2f m" % (info["l_tab"], info["l_close"]))
        for s in range(info["nspan"]):
            if 0 < s < info["nspan"] - 1:
                add("  第 %d 跨（主跨，%.2f m）：悬臂节段长 %.4f m × %d 个/方向，跨中合龙段 %.2f m"
                    % (s + 1, info["spans"][s], info["lseg"][s], info["n_seg"], info["l_close"]))
            else:
                add("  第 %d 跨（边跨，%.2f m）：悬臂节段长 %.4f m × %d 个，边跨现浇段 %.4f m，合龙段 %.2f m"
                    % (s + 1, info["spans"][s], info["lseg"][s], info["n_seg"],
                       info["lcast"][s], info["l_close"]))
        add("主梁梁高：墩顶 %.2f m → 跨中 %.2f m（沿桥向抛物线变化，0 号块范围保持墩顶梁高）"
            % (info["h_top"], info["h_mid"]))
        add("材料：主梁 %s、墩身 %s" % (info["girder_mat"], info["pier_mat"]))
    else:
        add("跨径布置：%s m（共 %d 跨，全长 %.2f m）"
            % (" + ".join(_num(x) for x in info["spans"]), info["nspan"], info["total"]))
        add("湿接缝长度：%.2f m；每跨预制梁划分 %d 个单元" % (info["l_joint"], info["n_div"]))
        add("预制 T 梁：梁高 %.2f m、翼缘宽 %.2f m、腹板厚 %.2f m、翼缘厚 %.2f m"
            % (info["h_girder"], float(str(p.get("b_girder", 0))),
               float(str(p.get("t_web", 0))), float(str(p.get("t_flange", 0)))))
        add("材料：%s" % info["mat"])
    add("收缩徐变：%s" % ("考虑（CEB-FIP 时间依存材料，环境湿度 %s%%）" % p.get("humidity")
                          if p.get("creep") else "不考虑"))
    add("二期恒载：%s kN/m" % p.get("q2nd"))
    add("")
    add("-" * 78)
    add("二、模型规模")
    add("-" * 78)
    add("节点 %d 个；单元 %d 个；截面 %d 种；结构组 %d 个；边界组 %d 个；荷载组 %d 个；施工阶段 %d 个"
        % (len(lay["nodes"]), len(lay["elems"]), len(lay["sections"]), len(lay["groups"]),
           len(lay["bndr_names"]), len(lay["load_groups"]), len(lay["stages"])))
    add("")
    add("-" * 78)
    add("三、截面表（%s；变截面按多段等截面输出）"
        % ("PSC 一般截面 · 箱形，面积/惯矩由程序算好后直接写入" if st == SUB_FCM
           else "MIDAS DBUSER 参数化形状 · T 形"))
    add("-" * 78)
    if st == SUB_FCM:
        add("  编号  名称      类型        H(m)   B(m)   tw(m)  tf1(m)  tf2(m)   面积(m2)")
        for sec in lay["sections"]:
            d = sec["dims"]
            add("  %4d  %-8s  %-8s  %5.3f  %5.3f  %5.3f  %5.3f  %5.3f  %8.4f"
                % (sec["index"], sec["name"], "PSC箱形", d[0], d[1], d[2], d[3], d[5], sec["area"]))
    else:
        add("  编号  名称   形状  H(m)   B(m)   tw(m)  tf(m)   面积(m2)")
        for sec in lay["sections"]:
            d = sec["dims"]
            add("  %4d  %-5s  %-4s  %5.3f  %5.3f  %5.3f  %5.3f  %8.4f"
                % (sec["index"], sec["name"], sec["shape"], d[0], d[1], d[2], d[3], sec["area"]))
    add("")
    add("-" * 78)
    add("四、结构组（施工阶段激活/钝化的基本单位）")
    add("-" * 78)
    for gname, g in lay["groups"].items():
        add("  %-10s 单元 %-28s 节点 %s"
            % (gname, _ids(g["elems"]), _ids(g["nodes"])))
    add("")
    add("-" * 78)
    add("五、边界组")
    add("-" * 78)
    for gname in lay["bndr_names"]:
        rows = [c for c in lay["constraints"] if c["group"] == gname]
        add("  %-10s %d 条约束：%s"
            % (gname, len(rows), "；".join("节点 %s 约束 %s" % (_ids(c["nodes"]), c["code"]) for c in rows)))
    add("")
    add("-" * 78)
    add("六、荷载组 / 湿重")
    add("-" * 78)
    wc_by = {}
    for it in lay["wc"]:
        wc_by.setdefault(it["group"], []).append(it)
    for gname in lay["load_groups"]:
        if gname == "Self":
            add("  Self      自重（*SELFWEIGHT 0, 0, -1）")
        elif gname == "SDL":
            add("  SDL       二期恒载 %s kN/m（BEAMLOAD 加在主梁单元上）" % p.get("q2nd"))
        else:
            items = wc_by.get(gname) or []
            add("  %-8s  %d 条 BEAMLOAD：%s"
                % (gname, len(items),
                   "；".join("单元 %d q=%.2f kN/m" % (it["elem"], it["q"]) for it in items)))
    add("")
    add("-" * 78)
    add("七、施工阶段表")
    add("-" * 78)
    add("  编号    持续(d)  阶段名称                    激活                         钝化/卸除")
    for name_, desc, dur, act, rem in stage_table(model):
        add("  %-6s  %6s   %-24s  %-26s  %s" % (name_, dur, desc, act, rem))
    add("")
    add("-" * 78)
    add("八、生成规则说明")
    add("-" * 78)
    add("1) 自重：*SELFWEIGHT 在第一个施工阶段激活一次，之后新激活的单元自动计入自重。")
    add("2) 湿重：新浇节段的混凝土重量 = 面积 × 容重 × 节段长，折成均布荷载（BEAMLOAD）加在")
    add("   已完成、挂着挂篮的那个端部单元上；该节段硬化（单元激活）的那一步用 DLOAD 卸掉。")
    add("3) 每个悬臂节段一个施工阶段；合龙段、边跨现浇段、体系转换各占一个阶段。")
    add("4) 主梁变截面：MIDAS 里按「多段等截面」输出（每个单元取自己跨中的梁高/板厚），")
    add("   想看真正连续的变截面，可以把单元再加密（加大「每墩悬臂节段数」）。")
    if st == SUB_FCM:
        add("   截面用 *SECT-PSCVALUE（PSC 一般截面，箱形真实轮廓）：面积、惯性矩是程序按尺寸算好")
        add("   后直接写进 MCT 的，MIDAS 不再二次解释形状尺寸，所以导入后的截面特性一定对得上数据表。")
    add("5) 预应力钢束（*TDN-*）：这一版只留了接口（见 cs_core.tendon_lines），")
    add("   张拉阶段目前只作为「工序阶段」存在，不产生预应力效应 —— 需要时请按课件补钢束。")
    return "\r\n".join(L) + "\r\n"


# ======================================================================
# 成果输出：一份完整的「工程文件夹」
# ======================================================================
def import_help(model):
    """导入说明.txt 的内容（带本次模型的规模数字）。"""
    st, p = _sub(model)
    lay = layout_of(model)[2]
    prj = _project(model)
    name = (prj.get("name") or "").strip() or "施工阶段梁桥"
    L = []
    add = L.append
    add("MIDAS Civil NX 导入说明 —— 施工阶段梁桥 · %s" % SUB_LABEL[st])
    add("=" * 70)
    add("工程名称：%s" % name)
    add("")
    add("一、怎么导入")
    add("-" * 70)
    add("1. 打开 MIDAS Civil NX，先新建一个空工程（或用现有工程）。")
    add("2. 菜单：文件 → 导入 → MCT 命令文件 → 选本文件夹里的 %s.mct" % name)
    add("   （编码是 GBK；如果中文显示乱码，换成 UTF-8 再导一次。）")
    add("3. 导入后先别急着算，按下面第 2 条核对一遍。")
    add("")
    add("二、导入后必须核对的 4 项")
    add("-" * 70)
    add("  ① 节点/单元数：应为 %d 个节点、%d 个单元。" % (len(lay["nodes"]), len(lay["elems"])))
    add("  ② 结构组 / 边界组 / 荷载组：分别为 %d / %d / %d 个。"
        % (len(lay["groups"]), len(lay["bndr_names"]), len(lay["load_groups"])))
    add("  ③ 施工阶段：共 %d 个（名称 %s … %s），每个阶段的「激活/钝化」要和 _数据表.txt 里一致。"
        % (len(lay["stages"]), lay["stages"][0]["name"], lay["stages"][-1]["name"]))
    add("  ④ 截面：共 %d 种。本流程按「多段等截面」输出变截面主梁（每个单元取自己跨中的尺寸），"
        % len(lay["sections"]))
    add("     所以截面表里会看到 GIR1、GIR2… 一串梁高逐渐变化的等截面，这是正常的。")
    add("     截面用 PSC 一般截面（箱形真实轮廓），面积/惯性矩是程序按尺寸算好后直接写进去的，"
        if st == SUB_FCM else "     截面用 MIDAS DBUSER 参数化形状（T 形）。")
    add("")
    add("三、运行分析")
    add("-" * 70)
    add("1. 分析 → 分析控制 → 施工阶段分析控制数据：确认「考虑时间依存」与 _数据表.txt 一致")
    add("   （本模型：%s）。" % ("考虑收缩徐变" if p.get("creep") else "不考虑收缩徐变"))
    add("2. 分析 → 运行分析。")
    add("3. 结果 → 施工阶段 → 施工阶段内力 / 位移 / 反力：")
    add("   - 「CS:恒荷载」= 除预应力/收缩徐变以外的荷载（自重 + 湿重 + 二期）；")
    add("   - 「CS:合计」= 该阶段所有荷载效应的合计；")
    add("   - 逐阶段看位移/内力突变，重点核查：架设、张拉、体系转换、合龙这几个阶段。")
    add("")
    add("四、本模型的建模约定（结果判读前先知道）")
    add("-" * 70)
    add("1. 结构按三维结构写（*STRUCTYPE 第 1 字段 = 0），但整体在 X-Z 平面内；")
    add("   所有支承都把平面外自由度（Dy、Rx、Rz）一并约束住了，所以它就是个平面模型。")
    add("2. 自重：第一个施工阶段激活一次「Self」荷载组，之后新激活的单元自动计入自重。")
    add("3. 湿重：新浇节段的混凝土重量 = 面积 × 容重 × 节段长，折成均布荷载（BEAMLOAD）加在")
    add("   已完成的悬臂端 / 已架设的梁上；等这一段硬化（单元激活）的那个阶段再用 DLOAD 卸掉。")
    add("4. 边界：按施工工序激活/钝化（架设临时支座 → 张拉连续束 → 撤临时体系、换永久支座）。")
    add("5. 预应力钢束（*TDN-*）：这一版还没实现。文件里的「张拉梁内束 / 张拉连续束」只是工序")
    add("   阶段，不产生预应力效应。要算预应力，得在 MIDAS 里补钢束，或等程序下一版。")
    add("")
    add("五、同文件夹里其它文件")
    add("-" * 70)
    add("  %s.mct           —— 导入 MIDAS 用的命令文件" % name)
    add("  %s_数据表.txt    —— 人可读的参数、截面、组、施工阶段表（生成时自动出）" % name)
    add("  %s_数据.json     —— 建模向导自己的工程存档（下次可以打开继续改）" % name)
    add("  导入说明.txt      —— 本文件")
    return "\r\n".join(L) + "\r\n"


def write_bundle(model, outdir, prefix=None, enc="gbk"):
    """把一份完整的「工程文件夹」写出来，返回 [(说明, 路径), ...]。

    内容：<名>.mct / <名>_数据.json / <名>_数据表.txt / 导入说明.txt
    纯 Python（不依赖 tkinter），界面「生成成果」和命令行/测试都用这一个函数。
    """
    import json
    import os
    prj = _project(model)
    name = (prefix or prj.get("name") or "").strip() or \
        ("连续刚构_悬臂浇筑" if (_sub(model)[0] == SUB_FCM) else "简支变连续")
    outdir = outdir or prj.get("outdir") or os.getcwd()
    os.makedirs(outdir, exist_ok=True)
    m = dict(model or {})
    m.setdefault("project", {})["name"] = name
    m["project"]["outdir"] = outdir
    if not m.get("flow"):
        m["flow"] = CODE
    files = []
    p1 = os.path.join(outdir, name + ".mct")
    with open(p1, "w", encoding=enc, errors="replace", newline="") as fh:
        fh.write(build_mct(m))
    files.append(("MCT 命令文件（导入 MIDAS 用）", p1))
    p2 = os.path.join(outdir, name + "_数据表.txt")
    with open(p2, "w", encoding=enc, errors="replace", newline="") as fh:
        fh.write(data_report(m))
    files.append(("数据表（参数/截面/组/施工阶段）", p2))
    p3 = os.path.join(outdir, name + "_数据.json")
    with open(p3, "w", encoding="utf-8") as fh:
        json.dump(m, fh, ensure_ascii=False, indent=2)
    files.append(("工程存档（可用向导再打开）", p3))
    p4 = os.path.join(outdir, "导入说明.txt")
    with open(p4, "w", encoding=enc, errors="replace", newline="") as fh:
        fh.write(import_help(m))
    files.append(("导入说明", p4))
    return files


# ======================================================================
# 预应力钢束（*TDN-*）—— 这一版只留接口，不实现
# ======================================================================
def tendon_lines(model):
    """预留给 *TDN-PROPERTY / *TDN-PROFILE / *TDN-PRESTRESS 的接口（本版未实现）。

    想加钢束时的落点：
      1) 在模型里加 m["tendons"] = [{"name","mat","area","elems","profile"}...]；
      2) 这里返回 *TDN-PROPERTY / *TDN-PROFILE 的行；
      3) build_mct() 里在 *STLDCASE 之后加一个 *USE-STLD, PS + *TDN-PRESTRESS 块；
      4) 施工阶段的 ALOAD 里加上钢束组（如 PS1, FIRST）。
    格式参考 C:\\Midas\\work\\samples\\FCM.mct 第 2215~5051 行。
    """
    return []


# ======================================================================
# 命令行自测：python -B -X utf8 cs_core.py
# ======================================================================
if __name__ == "__main__":
    import sys
    for sub in (SUB_FCM, SUB_S2C):
        m = default_model()
        m["subtype"] = sub
        try:
            text = build_mct(m)
        except Exception as exc:                   # noqa: BLE001
            print("[%s] 生成失败：%s: %s" % (sub, type(exc).__name__, exc))
            continue
        print("=" * 70)
        print("[%s] %s —— %d 行、%d 个施工阶段"
              % (sub, SUB_LABEL[sub], text.count("\n"), len(layout_of(m)[2]["stages"])))
        print("\r\n".join(summary(m)))
        print("MCT 前 12 行：")
        print("\r\n".join(text.splitlines()[:12]))
        if len(sys.argv) > 1 and sys.argv[1] == "--save":
            path = "cs_%s_示例.mct" % sub
            with open(path, "w", encoding="gbk", errors="replace", newline="") as fh:
                fh.write(text)
            print("已写：%s" % path)
