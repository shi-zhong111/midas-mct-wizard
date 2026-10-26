# -*- coding: utf-8 -*-
"""建模向导的共享底座（纯函数，不依赖 tkinter / 不依赖主程序）。

从 midas_wizard.py 原样搬出来的公共部分，供主程序和各个「流程插件」(flow_*.py) 共用：
  * 截面形状表 / 单位换算常量
  * 数值与文本格式化（num / numg / txt / aascii / aname / is_num）
  * DXF 自定义截面的读取与截面特性计算（compute_section_props / read_dxf_loops …）

注意：这里的函数是 midas_wizard.py 里逐字搬过来的，改这里就等于改主程序的行为，
改完必须跑 C:/Midas/wiz/tests/run_checks.py 回归（3 套样例成果必须逐字节不变）。
"""
import math


SHAPES = [
    ("SB", "实心矩形", [("H", "高"), ("B", "宽")]),
    ("SR", "实心圆", [("D", "直径")]),
    ("L", "角钢", [("H", "高"), ("B", "宽"), ("tw", "腹板厚"), ("tf", "翼缘厚")]),
    ("T", "T 形", [("H", "高"), ("B", "宽"), ("tw", "腹板厚"), ("tf", "翼缘厚")]),
    ("C", "槽钢", [("H", "高"), ("B1", "上宽"), ("tw", "腹板厚"), ("tf1", "上翼厚"), ("B2", "下宽"), ("tf2", "下翼厚")]),
    ("H", "工字钢", [("H", "高"), ("B1", "上宽"), ("tw", "腹板厚"), ("tf1", "上翼厚"),
                     ("B2", "下宽"), ("tf2", "下翼厚"), ("r1", "圆角 1"), ("r2", "圆角 2")]),
    ("B", "箱形", [("H", "高"), ("B", "宽"), ("tw", "腹板厚"), ("tf1", "上翼厚"), ("C", "倒角"), ("tf2", "下翼厚")]),
    ("P", "圆管", [("D", "外径"), ("tw", "壁厚")]),
]
SHAPE_BY_CODE = {s[0]: s for s in SHAPES}
CAD_SHAPE = "CAD"
TS_SHAPE = "TS"            # 变截面（引用两端截面，沿梁高线性渐变）
DIM_SLOTS = 10             # DBUSER 截面固定写 10 个数值槽位（真实导出为 24 字段定长）


def is_num(v):
    try:
        float(str(v).strip())
        return str(v).strip() != ""
    except (TypeError, ValueError):
        return False


def num(v):
    try:
        f = float(str(v).strip())
    except (TypeError, ValueError):
        return "0"
    if f == int(f) and abs(f) < 1e15:
        return str(int(f))
    return repr(f)


def numg(v, sig=6):
    """截面特性用：6 位有效数字，免得写出 0.39612500000000017 这种浮点噪声。"""
    try:
        return "%.*g" % (sig, float(v))
    except (TypeError, ValueError):
        return "0"


def txt(v):
    return "" if v is None else str(v)


def aascii(v):
    """边界组名专用：*CONSTRAINT 的组名里出现中文会导致整份导入失败（实测）。"""
    return "".join(ch for ch in txt(v) if ord(ch) < 128)


def aname(v, fallback=""):
    """整理写进 MCT 的名称：去掉逗号/换行/制表符（会把一行拆成多列），连续空格压成一个。

    中文名 MIDAS Civil NX 实测能正常导入，所以这里不转 ASCII（转掉反而丢名字）；
    万一哪版 MIDAS 真不认中文，再用上面现成的 aascii()。
    """
    s = txt(v).strip()
    for ch in (",", "\r", "\n", "\t"):
        s = s.replace(ch, " ")
    while "  " in s:
        s = s.replace("  ", " ")
    return s.strip() or fallback


STYPE_NAMES = {"0": "三维 (3D)", "1": "二维 X-Z 平面", "2": "二维 Y-Z 平面", "3": "二维 X-Y 平面", "4": "三维 (Z 向旋转约束)"}
MAT_TYPE_NAMES = {"CONC": "混凝土", "STEEL": "钢材", "USER": "用户自定义"}
MAT_MODES = [("spec", "规格自动填数"), ("db", "规范数据库"), ("user", "自定义数值")]
MAT_MODE_NAMES = {k: v for k, v in MAT_MODES}
MAT_MODE_CODES = {v: k for k, v in MAT_MODES}
MAT_TYPE_CODES = {v: k for k, v in MAT_TYPE_NAMES.items()}


def _dist(p, q):
    return ((p[0] - q[0]) ** 2 + (p[1] - q[1]) ** 2) ** 0.5


def _perimeter(pts):
    n = len(pts)
    return sum(_dist(pts[i], pts[(i + 1) % n]) for i in range(n))


def _point_in_poly(pt, poly):
    x, y = pt
    inside = False
    n = len(poly)
    for i in range(n):
        x0, y0 = poly[i]
        x1, y1 = poly[(i + 1) % n]
        if (y0 > y) != (y1 > y):
            xin = (x1 - x0) * (y - y0) / (y1 - y0) + x0
            if x < xin:
                inside = not inside
    return inside


def _loop_terms(pts):
    """返回 (A, Sy, Sz, Iyy, Izz)：面积、一次矩、关于原点的二次矩。"""
    n = len(pts)
    a2 = sy = sz = iyy = izz = 0.0
    for i in range(n):
        y0, z0 = pts[i]
        y1, z1 = pts[(i + 1) % n]
        cr = y0 * z1 - y1 * z0
        a2 += cr
        sy += (y0 + y1) * cr
        sz += (z0 + z1) * cr
        iyy += (z0 * z0 + z0 * z1 + z1 * z1) * cr
        izz += (y0 * y0 + y0 * y1 + y1 * y1) * cr
    return a2 / 2.0, sy / 6.0, sz / 6.0, iyy / 12.0, izz / 12.0


def compute_section_props(loops):
    """由若干闭合环（含孔洞）算出截面特性；失败返回 None。

    注意：CAD 里画截面常常随手画在图纸中间（坐标可能是 317321 这种大数）。
    大数相减会把惯性矩算成 1e5 这种垃圾值，写进 MCT 还会被 6 位有效数字抹平、
    让多边形退化成重复点 —— MIDAS 就报「截面尺寸输入有错误」。
    所以这里先整体平移，再积分，最后把形心加回去；写 MCT 的环一律以形心为原点。
    """
    loops = [[[float(p[0]), float(p[1])] for p in lp] for lp in loops if len(lp) >= 3]
    if not loops:
        return None
    # 去掉相邻重复点（含首尾重复）与面积可忽略的退化环
    clean = []
    for lp in loops:
        pts = []
        for p in lp:
            if not pts or abs(p[0] - pts[-1][0]) > 1e-9 or abs(p[1] - pts[-1][1]) > 1e-9:
                pts.append(p)
        if len(pts) >= 3 and abs(pts[0][0] - pts[-1][0]) < 1e-9 and abs(pts[0][1] - pts[-1][1]) < 1e-9:
            pts = pts[:-1]
        if len(pts) >= 3 and abs(_loop_terms(pts)[0]) > 1e-12:
            clean.append(pts)
    loops = clean
    if not loops:
        return None

    _ox = min(p[0] for lp in loops for p in lp)
    _oz = min(p[1] for lp in loops for p in lp)
    loops = [[[p[0] - _ox, p[1] - _oz] for p in lp] for lp in loops]

    depth = []
    for i, lp in enumerate(loops):
        d = 0
        for j, other in enumerate(loops):
            if i != j and _point_in_poly(lp[0], other):
                d += 1
        depth.append(d)

    A = Sy = Sz = Iyy = Izz = 0.0
    peri_out = peri_in = 0.0
    ys, zs = [], []
    for lp, d in zip(loops, depth):
        a, sy, sz, iyy, izz = _loop_terms(lp)
        sign = 1.0 if d % 2 == 0 else -1.0
        if (a > 0) != (sign > 0):
            a, sy, sz, iyy, izz = -a, -sy, -sz, -iyy, -izz
        A += a
        Sy += sy
        Sz += sz
        Iyy += iyy
        Izz += izz
        if sign > 0:
            peri_out += _perimeter(lp)
            ys.extend(p[0] for p in lp)
            zs.extend(p[1] for p in lp)
        else:
            peri_in += _perimeter(lp)
    if A <= 1e-12:
        return None

    cy = Sy / A
    cz = Sz / A
    iyy_c = Iyy - A * cz * cz          # 关于形心，对应 MIDAS 的 Iyy
    izz_c = Izz - A * cy * cy          # 对应 MIDAS 的 Izz
    ip = iyy_c + izz_c
    # 扭转常数：实心截面用圣维南近似 J ≈ A⁴/(40·Ip)（圆截面误差约 1.5%，方截面约 7%）
    j_approx = (A ** 4) / (40.0 * ip) if ip > 1e-12 else 0.0
    return {
        "A": A,
        "ASy": A * 5.0 / 6.0,          # 剪切面积按矩形近似取 5/6·A，*SECTION 数值截面兜底要用
        "ASz": A * 5.0 / 6.0,
        "Cy": cy + _ox,                # 回到原图坐标，只为了让界面能显示「画在哪儿」
        "Cz": cz + _oz,
        "Iyy": iyy_c,
        "Izz": izz_c,
        "Ixx": j_approx,
        "CyP": max(ys) - cy,
        "CyM": cy - min(ys),
        "CzP": max(zs) - cz,
        "CzM": cz - min(zs),
        "PERI_OUT": peri_out,
        "PERI_IN": peri_in,
        "N_LOOP": len(loops),
        "N_HOLE": sum(1 for d in depth if d % 2 == 1),
        "J_APPROX": True,
        # 写 *SECT-PSCVALUE 的 OPOLY / IPOLY 用（偶数层=外轮廓，奇数层=孔洞）。
        # 这里已经把形心平移到原点：MIDAS 的一般截面就是这么存的，
        # 坐标也变小了，不会被 6 位有效数字抹成重复点。
        "OUTER_LOOPS": [[[round(p[0] - cy, 9), round(p[1] - cz, 9)] for p in lp]
                        for lp, d in zip(loops, depth) if d % 2 == 0],
        "HOLE_LOOPS": [[[round(p[0] - cy, 9), round(p[1] - cz, 9)] for p in lp]
                       for lp, d in zip(loops, depth) if d % 2 == 1],
        "DRAW_OFFSET": [round(_ox, 6), round(_oz, 6)],   # 原图里画在哪儿（显示/排查用）
    }


def suggest_unit(sec):
    """面积明显不对时，按面积反推这张图应该选哪个图纸单位，返回单位名（如 "m 米"）。

    面积随单位比例的平方变化：A ∝ scale²。用当前单位算出的面积反推需要的比例，
    再挑最接近的那个单位。
    """
    try:
        cur = float(sec.get("unit") or "0.001")
        a = float((sec.get("props") or {}).get("A") or 0)
    except (TypeError, ValueError):
        return ""
    if a <= 0 or cur <= 0:
        return ""
    need = cur * (1.0 / a) ** 0.5          # 想要 1 m² 上下
    return min(UNIT_SCALES, key=lambda u: abs(float(u[1]) / need - 1.0))[0]


def unit_advice(sec):
    """面积明显不像梁截面时，给人一句能照做的建议。"""
    _sug = suggest_unit(sec)
    _cur = {u[1]: u[0] for u in UNIT_SCALES}.get(txt(sec.get("unit")).strip(), "")
    if _sug and _cur and _sug == _cur:
        return ("现在选的单位（%s）其实就是对的，只是这个面积还是按老单位算出来的 —— "
                "点一下「重新计算」重算一遍。" % _cur)
    if _sug:
        return "「图纸单位」多半选错了（这张图按「%s」算才合理），改过来程序会立刻重算。" % _sug
    return "请检查第 4 步的「图纸单位」是不是选错了。"


def loop_area(lp):
    """多边形的有向面积（鞋带公式）×2：正=逆时针。"""
    s = 0.0
    n = len(lp)
    for i in range(n):
        x1, y1 = lp[i][0], lp[i][1]
        x2, y2 = lp[(i + 1) % n][0], lp[(i + 1) % n][1]
        s += x1 * y2 - x2 * y1
    return s


def orient_loop(lp, ccw=True):
    """按 MIDAS 的习惯整理环的方向：外轮廓逆时针(CCW)、孔洞顺时针(CW)。

    注意：顶点里不要重复第一点（实测重复首点会让 MIDAS 报「截面尺寸输入有错误」），
    相邻的重复点也一并去掉。
    """
    pts = []
    for p in lp:
        q = [float(p[0]), float(p[1])]
        if not pts or abs(q[0] - pts[-1][0]) > 1e-9 or abs(q[1] - pts[-1][1]) > 1e-9:
            pts.append(q)
    if len(pts) > 1 and abs(pts[0][0] - pts[-1][0]) < 1e-9 and abs(pts[0][1] - pts[-1][1]) < 1e-9:
        pts = pts[:-1]
    a = loop_area(pts)
    if (a < 0 and ccw) or (a > 0 and not ccw):
        pts.reverse()
    return pts


def _tess_circle(cx, cy, r, n=72):
    import math
    return [(cx + r * math.cos(2 * math.pi * i / n), cy + r * math.sin(2 * math.pi * i / n)) for i in range(n)]


def _tess_arc(cx, cy, r, a1, a2, n=48):
    import math
    if a2 < a1:
        a2 += 360.0
    return [(cx + r * math.cos(math.radians(a1 + (a2 - a1) * i / n)),
             cy + r * math.sin(math.radians(a1 + (a2 - a1) * i / n))) for i in range(n + 1)]


def _chain_segments(segs, tol):
    """把开口线段首尾相接成闭合环。返回 (闭合环列表, 没用上的线段数)。"""
    segs = [list(s) for s in segs if len(s) >= 2]
    total = len(segs)
    used = 0
    loops = []
    while segs:
        cur = segs.pop(0)
        used += 1
        joined = True
        while joined and not (len(cur) > 2 and _dist(cur[0], cur[-1]) <= tol):
            joined = False
            for i, s in enumerate(segs):
                if _dist(cur[-1], s[0]) <= tol:
                    cur.extend(s[1:])
                elif _dist(cur[-1], s[-1]) <= tol:
                    cur.extend(list(reversed(s))[1:])
                elif _dist(cur[0], s[-1]) <= tol:
                    cur = s[:-1] + cur
                elif _dist(cur[0], s[0]) <= tol:
                    cur = list(reversed(s))[:-1] + cur
                else:
                    continue
                segs.pop(i)
                used += 1
                joined = True
                break
        if len(cur) >= 4 and _dist(cur[0], cur[-1]) <= tol:
            loops.append(cur[:-1])
        elif len(cur) < 2:
            used -= 1
    return loops, max(0, total - used)


def read_dxf_loops(path, unit_scale=1.0):
    """读 DXF，返回 (闭合环列表, 提示信息列表)。"""
    raw = None
    for enc in ("utf-8-sig", "utf-8", "gbk", "latin-1"):
        try:
            with open(path, "r", encoding=enc, errors="strict") as fh:
                raw = fh.read()
            break
        except (UnicodeDecodeError, LookupError):
            continue
        except OSError as exc:
            return [], ["读不了文件：%s" % exc]
    if raw is None:
        return [], ["dxf 编码无法识别（既不是 UTF-8 也不是 GBK）"]

    lines = raw.splitlines()
    pairs = []
    i = 0
    while i + 1 < len(lines):
        code = lines[i].strip()
        val = lines[i + 1].strip()
        i += 2
        try:
            pairs.append((int(code), val))
        except ValueError:
            continue

    seg, in_ent = [], False
    for k, (code, val) in enumerate(pairs):
        if code == 0 and val == "SECTION":
            nxt = pairs[k + 1] if k + 1 < len(pairs) else (0, "")
            in_ent = (nxt[0] == 2 and nxt[1] == "ENTITIES")
            continue
        if code == 0 and val == "ENDSEC":
            in_ent = False
            continue
        if in_ent:
            seg.append((code, val))

    closed, open_segs, notes = [], [], []
    state = {"type": None, "pts": [], "flags": 0, "num": None}

    def flush():
        t, pts, fl, nu = state["type"], state["pts"], state["flags"], state["num"]
        if t in ("LWPOLYLINE", "POLYLINE") and len(pts) >= 3:
            (closed if (fl & 1) else open_segs).append(pts)
        elif t == "CIRCLE" and nu and nu[2] > 0:
            closed.append(_tess_circle(nu[0], nu[1], nu[2]))
        elif t == "ARC" and nu and nu[2] > 0:
            open_segs.append(_tess_arc(nu[0], nu[1], nu[2], nu[3], nu[4]))
        elif t == "LINE" and nu and len(nu) >= 4:
            open_segs.append([(nu[0], nu[1]), (nu[2], nu[3])])
        elif t in ("SPLINE", "ELLIPSE", "INSERT", "SOLID", "HATCH", "3DFACE"):
            msg = "%s 暂不支持，已忽略（请把轮廓炸开成多段线 / 直线 / 圆弧）" % t
            if msg not in notes:
                notes.append(msg)
        state["type"], state["pts"], state["flags"], state["num"] = None, [], 0, None

    for code, val in seg:
        if code == 0:
            flush()
            state["type"] = val
            state["num"] = [0.0, 0.0, 0.0, 0.0, 0.0]
            continue
        t = state["type"]
        try:
            f = float(val)
        except ValueError:
            continue
        if t in ("LWPOLYLINE", "POLYLINE", "VERTEX"):
            if code == 10:
                state["pts"].append([f, 0.0])
            elif code == 20 and state["pts"]:
                state["pts"][-1][1] = f
            elif code == 70:
                state["flags"] |= int(f)
        elif t == "CIRCLE":
            if code == 10:
                state["num"][0] = f
            elif code == 20:
                state["num"][1] = f
            elif code == 40:
                state["num"][2] = f
        elif t == "ARC":
            idx = {10: 0, 20: 1, 40: 2, 50: 3, 51: 4}.get(code)
            if idx is not None:
                state["num"][idx] = f
        elif t == "LINE":
            idx = {10: 0, 20: 1, 11: 2, 21: 3}.get(code)
            if idx is not None:
                state["num"][idx] = f
    flush()

    if open_segs:
        xs = [p[0] for s in open_segs for p in s]
        ys = [p[1] for s in open_segs for p in s]
        diag = ((max(xs) - min(xs)) ** 2 + (max(ys) - min(ys)) ** 2) ** 0.5 if xs else 1.0
        tol = max(diag * 1e-6, 1e-9)
        chained, leftover = _chain_segments(open_segs, tol)
        if chained:
            notes.append("由直线/圆弧串成了 %d 个闭合环" % len(chained))
            closed.extend(chained)
        if leftover:
            notes.append("有 %d 段线没能围成闭合环，已忽略——请确认轮廓是闭合的" % leftover)

    loops = []
    for lp in closed:
        pts = [(p[0] * unit_scale, p[1] * unit_scale) for p in lp]
        if len(pts) >= 3:
            loops.append(pts)
    return loops, notes


UNIT_SCALES = [("mm 毫米", "0.001"), ("cm 厘米", "0.01"), ("dm 分米", "0.1"), ("m 米", "1"),
               ("in 英寸", "0.0254"), ("ft 英尺", "0.3048")]


def shape_code_of(sec):
    """从截面记录里取出形状代码（下拉框显示的是 'SB · 实心矩形' 这种文本）。"""
    raw = txt(sec.get("shape", "SB"))
    code = raw.split("·")[0].strip().split(" ")[0].strip()
    if code in (CAD_SHAPE, TS_SHAPE) or code in SHAPE_BY_CODE:
        return code
    return "SB"
