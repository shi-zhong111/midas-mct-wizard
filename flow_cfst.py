# -*- coding: utf-8 -*-
"""钢管混凝土拱 · 施工阶段联合截面 —— 界面流程插件（flow_cfst.py）。

结构：
    * 纯计算 / MCT 生成都在 cfst_core.py（不依赖 tkinter，测试可以直接调）；
    * 本文件只做「一步一步填数值」的界面，按 flow_api.FlowBase 实现 4 步：
          1. 拱轴线        （拱轴类型、跨径、矢高、拱轴系数、单元数、拱脚约束）
          2. 钢管混凝土截面（单圆管 / 哑铃形、管径壁厚、材料、截面特性示意图）
          3. 施工阶段       （阶段表：架设空钢管 → 灌注混凝土（湿重）→ 混凝土硬化 → 二期）
          4. 生成成果       （施工阶段 MCT + 一次成拱对比 MCT；预览/复制/打开文件夹）

主程序 midas_wizard.py 的 FLOW_PLUGIN_MODULES 里已经登记了 ("cfst", "flow_cfst")，
本文件放到程序目录就会出现「钢管混凝土拱（施工阶段联合截面）」这个流程。
"""

import os
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import cfst_core as cf
from flow_api import FlowBase

# 模块级也把引擎的几个函数暴露出来（SPEC 约定：测试可以 import flow_cfst 直接调）
CODE = cf.CODE
LABEL = cf.LABEL
default_model = cf.default_model
reset_model = cf.reset_model
issues = cf.issues
data_report = cf.data_report
build_mct = cf.build_mct
build_one_step_mct = cf.build_one_step_mct
section_from_model = cf.section_from_model
one_step_equivalent = cf.one_step_equivalent


# ======================================================================
# 小工具
# ======================================================================
def _safe_name(s, fallback="钢管混凝土拱"):
    s = str(s or "").strip()
    for ch in '\\/:*?"<>|':
        s = s.replace(ch, "-")
    s = s.strip().strip(".")
    return s or fallback


def _fmt(v, nd=4):
    try:
        return ("%." + str(nd) + "f") % float(v)
    except (TypeError, ValueError):
        return "-"


def _stage_plan_text(model):
    """把施工阶段表翻译成人能看懂的一句话计划（第 3 步表格下面显示）。"""
    names = cf.stage_names(model)
    conc = str(model.get("conc_stage") or "").strip()
    wet = cf.wet_stage_of(model)
    q2 = cf.q2_value(model)
    lines = []
    for st in model.get("stages") or []:
        nm = str(st.get("name") or "").strip()
        notes = []
        if names and nm == names[0]:
            notes.append("激活单元 / 边界 / 自重")
        if nm == wet:
            notes.append("加管内混凝土湿重 %s" % ", ".join(
                "%.2f kN/m" % q for _g, q in cf.wet_loads(model)))
        if nm == conc:
            notes.append("卸湿重，核心混凝土开始参与受力（联合截面生效）")
        if q2 > 0 and names and nm == names[-1]:
            notes.append("加二期恒载 %.2f kN/m" % q2)
        lines.append("  %-8s %-4s d  %s%s"
                     % (nm, st.get("days", ""), st.get("desc") or "",
                        ("    ← " + "；".join(notes)) if notes else ""))
    return "\n".join(lines)


# ======================================================================
# 画图（拱轴线 / 截面）
# ======================================================================
def _draw_arch(cv, pts, arch):
    cv.delete("all")
    w = max(cv.winfo_width(), 640)
    h = int(cv.winfo_height())
    if h < 60:                     # 窗口还没排好版时按「要求高度」画，否则会画到画布外
        h = int(cv.winfo_reqheight()) or 200
    if not pts:
        cv.create_text(w / 2.0, h / 2.0, text="（参数还不完整，暂时画不出来）", fill="#999999")
        return
    xs = [p[0] for p in pts]
    zs = [p[1] for p in pts]
    x0, x1 = min(xs), max(xs)
    z0, z1 = min(zs), max(zs)
    pad = 26
    sx = (w - 2 * pad) / (x1 - x0 or 1)
    sz = (h - 2 * pad) / ((z1 - z0) or 1)
    s = min(sx, sz)
    ox = pad + (w - 2 * pad - (x1 - x0) * s) / 2.0
    oy = h - pad

    def P(x, z):
        return (ox + (x - x0) * s, oy - (z - z0) * s)

    # 地面线
    cv.create_line(*P(x0 - (x1 - x0) * 0.06, z0), *P(x1 + (x1 - x0) * 0.06, z0),
                   fill="#aaaaaa", dash=(4, 3))
    # 拱轴（带节点小点）
    poly = []
    for (x, z) in pts:
        poly += list(P(x, z))
    cv.create_line(*poly, fill="#2d7d46", width=3)
    if len(pts) <= 60:
        for (x, z) in pts:
            px, py = P(x, z)
            cv.create_oval(px - 1.6, py - 1.6, px + 1.6, py + 1.6, fill="#2d7d46", outline="")
    # 拱脚
    for (x, z) in (pts[0], pts[-1]):
        px, py = P(x, z)
        cv.create_rectangle(px - 6, py - 4, px + 6, py + 6, fill="#c0392b", outline="")
    # 跨径 / 矢高标注
    p1, p2 = P(x0, z0), P(x1, z0)
    cv.create_line(*p1, *p2, arrow="both", fill="#666666")
    cv.create_text((p1[0] + p2[0]) / 2.0, p1[1] + 13,
                   text="计算跨径 L = %.2f m" % (x1 - x0), fill="#666666")
    pc = P((x0 + x1) / 2.0, z0 + (z1 - z0))
    cv.create_line(pc[0], pc[1], pc[0], oy, arrow="both", fill="#666666", dash=(3, 3))
    cv.create_text(pc[0] + 52, (pc[1] + oy) / 2.0,
                   text="计算矢高 f = %.2f m" % (z1 - z0), fill="#666666")
    # 拱脚约束
    fixed = (arch or {}).get("fixed", True)
    cv.create_text(x0 + 42, oy - 14, text="无铰（固结）" if fixed else "两铰",
                   fill="#c0392b")


def _draw_section(cv, sec, model):
    """画钢管 + 核心混凝土的横断面示意（灰色=钢管，黄色=混凝土）。"""
    cv.delete("all")
    w = max(cv.winfo_width(), 560)
    h = int(cv.winfo_height())
    if h < 60:
        h = int(cv.winfo_reqheight()) or 240
    x1, x2, z1, z2 = sec.bbox
    bw = (x2 - x1) or 1.0
    bh = (z2 - z1) or 1.0
    pad = 34
    s = min((w - 2 * pad) / bw, (h - 2 * pad) / bh)
    cx = w / 2.0 - (x1 + x2) / 2.0 * s
    cz = h / 2.0 + (z1 + z2) / 2.0 * s

    def P(x, z):
        return (cx + x * s, cz - z * s)

    def oval(cx0, cz0, r, **kw):
        a, b = P(cx0 - r, cz0 + r)
        c, d = P(cx0 + r, cz0 - r)
        cv.create_oval(a, b, c, d, **kw)

    def rect(rx1, rx2, rz1, rz2, **kw):
        a, b = P(rx1, rz2)
        c, d = P(rx2, rz1)
        cv.create_rectangle(a, b, c, d, **kw)

    # 1) 钢管（圆环画成「外圆灰底 + 内圆挖空」的效果）
    for part in sec.parts:
        if part.kind != "steel":
            continue
        for prim in part.prims:
            if prim[0] == "ring":
                oval(prim[1], prim[2], prim[3], fill="#aab2bd", outline="#5a6270", width=1.5)
            elif prim[0] == "rect":
                rect(prim[1], prim[2], prim[3], prim[4], fill="#aab2bd", outline="#5a6270")
            elif prim[0] == "circle":
                oval(prim[1], prim[2], prim[3], fill="#aab2bd", outline="#5a6270")
    # 2) 核心混凝土（画在钢管内孔里）
    for part in sec.parts:
        if part.kind != "conc":
            continue
        for prim in part.prims:
            if prim[0] == "circle":
                oval(prim[1], prim[2], prim[3], fill="#e8c25a", outline="#c9a227")
            elif prim[0] == "rect":
                rect(prim[1], prim[2], prim[3], prim[4], fill="#e8c25a", outline="#c9a227")
    # 3) 形心轴线 + 尺寸标注
    a, b = P(x1, 0), P(x2, 0)
    cv.create_line(*a, *b, fill="#888888", dash=(5, 3))
    a, b = P(0, z1), P(0, z2)
    cv.create_line(*a, *b, fill="#888888", dash=(5, 3))
    tb = model.get("tube") or {}
    txt = "D = %s m    t = %s m" % (tb.get("D"), tb.get("t"))
    if (tb.get("form") or "single") == "dumbbell":
        txt += "    中心距 = %s m" % tb.get("s")
    cv.create_text(w / 2.0, h - 12, text=txt, fill="#444444")
    cv.create_text(12, 12, text="■ 钢管   ■ 核心混凝土", anchor="nw", fill="#666666")


# ======================================================================
# 流程
# ======================================================================
class Flow(FlowBase):
    code = cf.CODE
    label = cf.LABEL
    step_ids = ["cfst_arch", "cfst_section", "cfst_stages", "cfst_export"]
    step_titles = ["拱轴线", "钢管混凝土截面", "施工阶段", "生成成果"]
    step_hints = [
        "填拱轴类型和跨径/矢高/拱轴系数/单元数；默认是第 05 讲算例：L=50 m、f=10 m、"
        "二次抛物线、30 个单元。改数字后下面的预览会立刻跟着变。",
        "填钢管外径 D 和壁厚 t，选混凝土等级和钢材牌号；下方灰色是钢管、黄色是核心混凝土，"
        "程序自动算出两者的面积、惯矩和组合截面特性。",
        "阶段表默认按「架设空钢管 → 灌注混凝土（湿重）→ 混凝土硬化（联合截面生效）→ 二期」排好，"
        "名字和天数都能改；再选核心混凝土从哪个阶段开始参与受力。",
        "生成施工阶段 MCT（含 *STAGE + *CPOSECT4CS 联合截面）和一次成拱对比 MCT，"
        "拿去 MIDAS Civil NX「文件 → 导入 → MCT 命令文件」即可。",
    ]
    last_hint = "文件已准备好，可以保存或复制 MCT；MIDAS 里用「文件 → 导入 → MCT 命令文件」导入。"

    # ---------------- 弹窗（统一走本模块的 messagebox，测试可以整体替身） ----------------
    def warn(self, title, msg):
        messagebox.showwarning(title, msg)

    def info(self, title, msg):
        messagebox.showinfo(title, msg)

    def ask(self, title, msg):
        return bool(messagebox.askyesno(title, msg))

    # ---------------- 数据 ----------------
    def default_model(self):
        return cf.default_model()

    def reset_model(self):
        return cf.reset_model()

    def issues(self, sid):
        return cf.issues(self.m, sid)

    def data_report(self):
        return cf.data_report(self.m)

    def clear_title(self, sid):
        return {"cfst_arch": "拱轴线", "cfst_section": "钢管混凝土截面",
                "cfst_stages": "施工阶段"}.get(sid, "")

    def clear(self, sid):
        d = cf.default_model()
        m = self.m
        if sid == "cfst_arch":
            m["arch"] = dict(d["arch"])
        elif sid == "cfst_section":
            m["tube"] = dict(d["tube"])
        elif sid == "cfst_stages":
            for k in ("stages", "conc_stage", "conc_age", "q2", "gamma", "creep", "humidity"):
                m[k] = d[k] if not isinstance(d[k], list) else [dict(x) for x in d[k]]

    def export(self):
        """生成两份 MCT（外壳不强制调用，导出按钮也会走这里）。"""
        got = self._prepare()
        if not got:
            return []
        fname, out = got
        return self._write_all(fname, out)

    # ==================================================================
    # 第 1 步：拱轴线
    # ==================================================================
    def step_cfst_arch(self):
        m = self.m
        a = m.setdefault("arch", {})
        for k, v in cf.default_model()["arch"].items():
            a.setdefault(k, v)

        box = self.card("拱轴线参数")
        g = ttk.Frame(box)
        g.pack(fill="x", anchor="w")

        type_names = [t[1] for t in cf.ARCH_TYPES]
        type_back = {t[1]: t[0] for t in cf.ARCH_TYPES}
        v_type = tk.StringVar(value=cf.ARCH_TYPE_NAMES.get(a.get("type"), "抛物线"))
        ttk.Label(g, text="拱轴类型").grid(row=0, column=0, sticky="w", padx=(0, 4), pady=3)
        cb_type = ttk.Combobox(g, textvariable=v_type, values=type_names, width=8, state="readonly")
        cb_type.grid(row=0, column=1, sticky="w", padx=(0, 22), pady=3)

        v_fix = tk.StringVar(value="无铰（固结）" if a.get("fixed", True) else "两铰")
        ttk.Label(g, text="拱脚约束").grid(row=0, column=2, sticky="w", padx=(0, 4))
        cb_fix = ttk.Combobox(g, textvariable=v_fix, values=["无铰（固结）", "两铰"], width=10,
                              state="readonly")
        cb_fix.grid(row=0, column=3, sticky="w", padx=(0, 22))

        fields = [("计算跨径 L (m)", "L", 8), ("计算矢高 f (m)", "f", 8),
                  ("拱轴系数 m", "m", 6), ("拱脚标高 (m)", "z0", 8), ("单元数", "n", 6)]
        entries = []
        col = 0
        row = 1
        for lab, key, wid in fields:
            ttk.Label(g, text=lab).grid(row=row, column=col, sticky="w", padx=(0, 4), pady=3)
            e = ttk.Entry(g, textvariable=self.svar(a, key, a.get(key)), width=wid)
            e.grid(row=row, column=col + 1, sticky="w", padx=(0, 22), pady=3)
            entries.append(e)
            col += 2
        ttk.Label(g, text="（拱轴系数 m 只有悬链线用：m 必须大于 1，常用 1.1~3）",
                  style="Hint.TLabel").grid(row=2, column=0, columnspan=10, sticky="w")

        # ---- 摘要 + 预览（改数字立刻重画，不用点下一步）----
        info = ttk.Label(box, text="", style="Ok.TLabel")
        info.pack(anchor="w", pady=(8, 0))

        prev = self.card("拱轴线预览（改参数自动刷新）")
        cv = tk.Canvas(prev, height=200, bg="white", highlightthickness=1,
                       highlightbackground="#cccccc")
        cv.pack(fill="x")

        def pts_now():
            try:
                return cf._arch_pts(m)
            except Exception:                                  # noqa: BLE001
                return []

        def refresh(*_e):
            pts = pts_now()
            _draw_arch(cv, pts, a)
            if pts:
                info.configure(text="拱线长 %.2f m      拱顶标高 %.2f m      矢跨比 1/%.2f      "
                                    "节点 %d 个 / 单元 %d 个"
                               % (cf.arch_length(pts), cf._f(a.get("z0"), 0) + cf._f(a.get("f"), 0),
                                  (cf._f(a.get("L"), 0) / cf._f(a.get("f"), 1))
                                  if cf._f(a.get("f"), 0) else 0.0,
                                  len(pts), len(pts) - 1))
            else:
                info.configure(text="参数还不完整")

        def on_type(*_e):
            a["type"] = type_back.get(v_type.get(), "parabola")
            self.rerender()

        def on_fix(*_e):
            a["fixed"] = v_fix.get().startswith("无铰")
            self.rerender()

        cb_type.bind("<<ComboboxSelected>>", on_type)
        cb_fix.bind("<<ComboboxSelected>>", on_fix)
        cv.bind("<Configure>", refresh)
        for e in entries:
            e.bind("<KeyRelease>", refresh)
            e.bind("<FocusOut>", refresh)
        refresh()
        ttk.Label(prev, text="X 为跨径方向、Z 竖直向上，左拱脚在 X=0；红色方块是拱脚约束。",
                  style="Hint.TLabel").pack(anchor="w", pady=(4, 0))

    # ==================================================================
    # 第 2 步：钢管混凝土截面
    # ==================================================================
    def step_cfst_section(self):
        m = self.m
        tb = m.setdefault("tube", {})
        for k, v in cf.default_model()["tube"].items():
            tb.setdefault(k, v)
        is_db = (tb.get("form") or "single") == "dumbbell"

        box = self.card("截面形式与尺寸（单位 m）")
        g = ttk.Frame(box)
        g.pack(fill="x", anchor="w")

        form_names = [t[1] for t in cf.FORMS]
        form_back = {t[1]: t[0] for t in cf.FORMS}
        v_form = tk.StringVar(value=cf.FORM_NAMES.get(tb.get("form"), "单圆管"))
        ttk.Label(g, text="截面形式").grid(row=0, column=0, sticky="w", padx=(0, 4), pady=3)
        cb_form = ttk.Combobox(g, textvariable=v_form, values=form_names, width=20,
                               state="readonly")
        cb_form.grid(row=0, column=1, sticky="w", padx=(0, 22), pady=3)

        v_D = self.svar(tb, "D", tb.get("D"))
        v_t = self.svar(tb, "t", tb.get("t"))
        ttk.Label(g, text="钢管外径 D").grid(row=0, column=2, sticky="w", padx=(0, 4))
        e_D = ttk.Entry(g, textvariable=v_D, width=8)
        e_D.grid(row=0, column=3, sticky="w", padx=(0, 22))
        ttk.Label(g, text="钢管壁厚 t").grid(row=0, column=4, sticky="w", padx=(0, 4))
        e_t = ttk.Entry(g, textvariable=v_t, width=8)
        e_t.grid(row=0, column=5, sticky="w", padx=(0, 22))

        entries = [e_D, e_t]
        if is_db:
            v_s = self.svar(tb, "s", tb.get("s"))
            v_tp = self.svar(tb, "tp", tb.get("tp"))
            ttk.Label(g, text="两管中心距").grid(row=1, column=0, sticky="w", padx=(0, 4), pady=3)
            e_s = ttk.Entry(g, textvariable=v_s, width=8)
            e_s.grid(row=1, column=1, sticky="w", padx=(0, 22), pady=3)
            ttk.Label(g, text="连接板厚").grid(row=1, column=2, sticky="w", padx=(0, 4))
            e_tp = ttk.Entry(g, textvariable=v_tp, width=8)
            e_tp.grid(row=1, column=3, sticky="w", padx=(0, 22))
            ttk.Label(g, text="（哑铃形 = 两根钢管 + 中间水平连接板，按截面里的水平薄板简化）",
                      style="Hint.TLabel").grid(row=2, column=0, columnspan=8, sticky="w")
            entries += [e_s, e_tp]

        v_conc = tk.StringVar(value=cf.model_conc(m))
        v_steel = tk.StringVar(value=cf.model_steel(m))
        row = 3 if is_db else 1
        ttk.Label(g, text="混凝土等级").grid(row=row, column=0, sticky="w", padx=(0, 4), pady=3)
        cb_conc = ttk.Combobox(g, textvariable=v_conc, values=cf.CONC_LIST, width=8,
                               state="readonly")
        cb_conc.grid(row=row, column=1, sticky="w", padx=(0, 22), pady=3)
        ttk.Label(g, text="钢材牌号").grid(row=row, column=2, sticky="w", padx=(0, 4))
        cb_steel = ttk.Combobox(g, textvariable=v_steel, values=cf.STEEL_LIST, width=8,
                                state="readonly")
        cb_steel.grid(row=row, column=3, sticky="w", padx=(0, 22))

        v_red = self.svar(tb, "reduce", tb.get("reduce"))
        ttk.Label(g, text="混凝土刚度折减").grid(row=row, column=4, sticky="w", padx=(0, 4))
        e_red = ttk.Entry(g, textvariable=v_red, width=8)
        e_red.grid(row=row, column=5, sticky="w", padx=(0, 22))
        entries.append(e_red)
        ttk.Label(g, text="（填 1 不折减；第 05 讲建议管内混凝土折减 0.6~0.8，只折减 Iyy/Izz）",
                  style="Hint.TLabel").grid(row=row + 1, column=0, columnspan=8, sticky="w")

        # ---- 预览 + 特性 ----
        prev = self.card("截面预览与特性（改数字自动刷新）")
        cv = tk.Canvas(prev, height=240, bg="white", highlightthickness=1,
                       highlightbackground="#cccccc")
        cv.pack(fill="x")
        info = ttk.Label(prev, text="", style="Ok.TLabel", justify="left")
        info.pack(anchor="w", pady=(8, 0))

        def refresh(*_e):
            try:
                sec = cf.section_from_model(m)
                _draw_section(cv, sec, m)
                sm = cf.summary(m)
                info.configure(text=(
                    "钢管：A = %.4f m2   Iyy = %.6f m4   Izz = %.6f m4\n"
                    "核心混凝土：A = %.4f m2   Iyy = %.6f m4   Izz = %.6f m4\n"
                    "组合截面：A = %.4f m2   Iyy = %.6f m4   Izz = %.6f m4   含钢率 %.1f %%\n"
                    "单位长度自重：钢管 %.2f + 混凝土 %.2f = %.2f kN/m"
                    % (sm["A_steel"], sm["Iyy_steel"], sm["Izz_steel"],
                       sm["A_conc"], sm["Iyy_conc"], sm["Izz_conc"],
                       sm["A"], sm["Iyy"], sm["Izz"], sm["steel_ratio"] * 100.0,
                       sm["w_steel"], sm["w_conc"], sm["w_total"])))
            except Exception:                                  # noqa: BLE001
                info.configure(text="参数还不完整")

        def on_form(*_e):
            tb["form"] = form_back.get(v_form.get(), "single")
            self.rerender()

        def on_conc(*_e):
            tb["conc"] = v_conc.get()
            refresh()

        def on_steel(*_e):
            tb["steel"] = v_steel.get()
            refresh()

        cb_form.bind("<<ComboboxSelected>>", on_form)
        cb_conc.bind("<<ComboboxSelected>>", on_conc)
        cb_steel.bind("<<ComboboxSelected>>", on_steel)
        cv.bind("<Configure>", refresh)
        for e in entries:
            e.bind("<KeyRelease>", refresh)
            e.bind("<FocusOut>", refresh)
        refresh()
        ttk.Label(prev, text="钢管按圆环、核心混凝土按实心圆算面积和惯矩；组合截面 = 两者之和。",
                  style="Hint.TLabel").pack(anchor="w", pady=(4, 0))

    # ==================================================================
    # 第 3 步：施工阶段
    # ==================================================================
    def step_cfst_stages(self):
        m = self.m
        stages = m.setdefault("stages", [])
        if not stages:
            stages.extend(cf.default_model()["stages"])

        box = self.card("施工阶段表（默认：架设空钢管 → 灌注混凝土 → 混凝土硬化 → 二期）")
        g = self.table(box, ["序号", "阶段名", "持续天数 (d)", "这一步做什么", ""])
        for i, st in enumerate(stages):
            ttk.Label(g, text=str(i + 1)).grid(row=i + 1, column=0, sticky="w",
                                               padx=(0, 12), pady=3)
            self.entry(g, self.svar(st, "name", st.get("name")), width=10, row=i + 1, col=1)
            self.entry(g, self.svar(st, "days", st.get("days")), width=8, row=i + 1, col=2)
            self.entry(g, self.svar(st, "desc", st.get("desc")), width=42, row=i + 1, col=3)
            ttk.Button(g, text="删除", width=6,
                       command=lambda k=i: self._del_stage(k)).grid(row=i + 1, column=4,
                                                                    sticky="w", padx=(0, 0),
                                                                    pady=3)
        ttk.Button(box, text="＋ 增加一个阶段", command=self._add_stage).pack(anchor="w", pady=(8, 0))

        names = [str(s.get("name") or "").strip() for s in stages]

        # ---- 核心混凝土什么时候参与受力 ----
        box2 = self.card("核心混凝土：什么时候开始参与受力（联合截面生效）")
        g2 = ttk.Frame(box2)
        g2.pack(fill="x", anchor="w")
        cur = str(m.get("conc_stage") or "").strip()
        if cur not in names:
            cur = names[2] if len(names) > 2 else (names[-1] if names else "")
            m["conc_stage"] = cur
        v_conc = tk.StringVar(value=cur)
        ttk.Label(g2, text="混凝土参与受力阶段").grid(row=0, column=0, sticky="w", padx=(0, 4), pady=3)
        cb_conc = ttk.Combobox(g2, textvariable=v_conc, values=names, width=10, state="readonly")
        cb_conc.grid(row=0, column=1, sticky="w", padx=(0, 22), pady=3)
        ttk.Label(g2, text="混凝土硬化龄期 (d)").grid(row=0, column=2, sticky="w", padx=(0, 4))
        self.entry(g2, self.svar(m, "conc_age", m.get("conc_age")), width=8, row=0, col=3)
        ttk.Label(g2, text="二期恒载 q2 (kN/m)").grid(row=0, column=4, sticky="w", padx=(0, 4))
        self.entry(g2, self.svar(m, "q2", m.get("q2")), width=8, row=0, col=5)
        wet = cf.wet_stage_of(m)
        ttk.Label(box2, text="（灌注阶段 = 硬化阶段的前一个阶段，现在湿重加在「%s」；"
                             "二期恒载填 0 表示不加）" % (wet or "—"),
                  style="Hint.TLabel").pack(anchor="w", pady=(6, 0))

        def on_conc(*_e):
            m["conc_stage"] = v_conc.get()
            self.rerender()

        cb_conc.bind("<<ComboboxSelected>>", on_conc)

        # ---- 收缩徐变（可选）----
        box3 = self.card("材料与时间依存（可选）")
        g3 = ttk.Frame(box3)
        g3.pack(fill="x", anchor="w")
        cb_creep = ttk.Checkbutton(g3, text="考虑收缩徐变（CEB-FIP 时间依存）",
                                   variable=self.bvar(m, "creep", bool(m.get("creep"))))
        cb_creep.grid(row=0, column=0, sticky="w", padx=(0, 22), pady=3)
        ttk.Label(g3, text="环境湿度 (%)").grid(row=0, column=1, sticky="w", padx=(0, 4))
        self.entry(g3, self.svar(m, "humidity", m.get("humidity")), width=8, row=0, col=2)

        # ---- 阶段计划说明 ----
        box4 = self.card("阶段计划（自动生成，核对一下再进下一步）")
        txt = tk.Text(box4, height=7, wrap="none", font=("Consolas", 9))
        txt.insert("1.0", _stage_plan_text(m))
        txt.configure(state="disabled")
        txt.pack(fill="x")
        ttk.Label(box4, text="湿重 = 核心混凝土面积 × 容重，加在灌注阶段、硬化阶段用 DLOAD 卸掉；"
                             "硬化后核心混凝土才在 *CPOSECT4CS 里开始参与受力。",
                  style="Hint.TLabel").pack(anchor="w", pady=(6, 0))

    def _add_stage(self):
        stages = self.m.setdefault("stages", [])
        n = len(stages) + 1
        name = "CS%d" % n
        while any(str(s.get("name") or "") == name for s in stages):
            n += 1
            name = "CS%d" % n
        stages.append({"name": name, "days": "20", "desc": ""})
        self.save()
        self.rerender()

    def _del_stage(self, idx):
        stages = self.m.get("stages") or []
        if len(stages) <= 2:
            self.warn("删不了", "至少要保留 2 个施工阶段（架设空钢管 → 灌注混凝土 → 硬化）。")
            return
        old = str(stages[idx].get("name") or "")
        if not self.ask("删除阶段", "确定删除第 %d 个阶段「%s」吗？" % (idx + 1, old)):
            return
        stages.pop(idx)
        names = [str(s.get("name") or "").strip() for s in stages]
        if str(self.m.get("conc_stage") or "").strip() not in names:
            self.m["conc_stage"] = names[2] if len(names) > 2 else names[-1]
        self.save()
        self.rerender()

    # ==================================================================
    # 第 4 步：生成成果
    # ==================================================================
    def step_cfst_export(self):
        m = self.m
        prj = m.setdefault("project", {})
        if not hasattr(self, "_v_outdir"):
            self._v_outdir = tk.StringVar(value=prj.get("outdir") or self.out_dir())
        if not hasattr(self, "_v_fname"):
            self._v_fname = tk.StringVar(value=prj.get("name") or "钢管混凝土拱")

        box = self.card("生成成果")
        line0 = ttk.Frame(box)
        line0.pack(fill="x")
        ttk.Label(line0, text="输出文件夹").pack(side="left")
        ttk.Entry(line0, textvariable=self._v_outdir, width=52).pack(side="left", padx=(6, 6))
        ttk.Button(line0, text="选择…", command=self._pick_dir).pack(side="left")
        ttk.Button(line0, text="打开", command=self._open_dir).pack(side="left", padx=(6, 0))

        line1 = ttk.Frame(box)
        line1.pack(fill="x", pady=(8, 0))
        ttk.Label(line1, text="文件名").pack(side="left")
        ttk.Entry(line1, textvariable=self._v_fname, width=26).pack(side="left", padx=(6, 14))
        ttk.Label(line1, text="（会生成 <名字>.mct 和 <名字>_一次成拱.mct）",
                  style="Hint.TLabel").pack(side="left")

        line2 = ttk.Frame(box)
        line2.pack(fill="x", pady=(10, 0))
        ttk.Button(line2, text="① 生成施工阶段 MCT",
                   command=self._gen_main).pack(side="left")
        ttk.Button(line2, text="② 生成一次成拱对比 MCT",
                   command=self._gen_one).pack(side="left", padx=(8, 0))
        ttk.Button(line2, text="③ 两份都生成",
                   command=self._gen_all).pack(side="left", padx=(8, 0))
        ttk.Button(line2, text="④ 预览 MCT",
                   command=self._preview).pack(side="left", padx=(8, 0))
        ttk.Button(line2, text="⑤ 复制 MCT 文本",
                   command=self._copy).pack(side="left", padx=(8, 0))

        ttk.Label(box, text="MIDAS Civil NX 里：文件 → 导入 → MCT 命令文件，选 <名字>.mct。\n"
                            "施工阶段模型里 *STAGE 定义 4 个阶段、*CPOSECT4CS 定义钢管 + 核心混凝土"
                            "两个部件；打开分析前记得先「另存为」，否则 MIDAS 会弹保存对话框。",
                  style="Hint.TLabel", justify="left").pack(anchor="w", pady=(8, 0))

        box2 = self.card("日志 / 预览")
        self.log = tk.Text(box2, height=16, wrap="none", font=("Consolas", 9))
        sb = ttk.Scrollbar(box2, orient="vertical", command=self.log.yview)
        self.log.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.log.pack(side="left", fill="both", expand=True)
        self.log.insert("1.0", "（先点 ① 或 ② 生成 MCT，或点 ④ 预览文本。）")
        self.log.configure(state="disabled")

    # ---------------- 生成 ----------------
    def _log(self, text):
        try:
            self.log.configure(state="normal")
            self.log.insert("end", str(text) + "\n")
            self.log.see("end")
            self.log.configure(state="disabled")
        except (AttributeError, tk.TclError):
            pass

    def _pick_dir(self):
        start = self._v_outdir.get().strip() or self.out_dir()
        try:
            os.makedirs(start, exist_ok=True)
        except OSError:
            pass
        d = filedialog.askdirectory(title="选择成果文件夹", initialdir=start)
        if d:
            self._v_outdir.set(d)

    def _open_dir(self):
        d = self._v_outdir.get().strip() or self.out_dir()
        try:
            os.makedirs(d, exist_ok=True)
            import subprocess
            subprocess.Popen(["explorer", os.path.normpath(d)])
        except OSError as exc:
            self.warn("打不开", str(exc))

    def _prepare(self):
        """生成前体检 + 整理项目名/输出目录；有问题返回 None。"""
        probs = cf.all_issues(self.m)
        if probs:
            messagebox.showwarning("先改完这些再生成",
                                   "下面这些问题会让 MIDAS 导入报错，先改好（点左侧步骤名可直接跳过去）：\n\n"
                                   + "\n".join("· " + p for p in probs[:10])
                                   + ("\n…还有 %d 条" % (len(probs) - 10) if len(probs) > 10 else ""))
            return None
        fname = _safe_name(self._v_fname.get() or self.m["project"].get("name") or "钢管混凝土拱")
        out = (self._v_outdir.get() or "").strip() or self.out_dir()
        try:
            os.makedirs(out, exist_ok=True)
        except OSError as exc:
            self.warn("文件夹建不了", str(exc))
            return None
        self.m["project"]["name"] = fname
        self.m["project"]["outdir"] = out
        self._v_fname.set(fname)
        return fname, out

    def _write_text(self, path, text):
        """写 GBK（和梁桥/拱桥流程一致；MIDAS 的 MCT 用 ANSI/GBK 最稳）。"""
        try:
            with open(path, "w", encoding="gbk", errors="replace", newline="") as fh:
                fh.write(text)
        except OSError as exc:
            self.warn("写文件失败", "%s\n\n%s" % (path, exc))
            return False
        return True

    def _write_json(self, path):
        import json
        try:
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(self.m, fh, ensure_ascii=False, indent=2)
        except (OSError, TypeError) as exc:
            self.warn("写文件失败", "%s\n\n%s" % (path, exc))
            return False
        return True

    def _write_readme(self, path, fname):
        text = ("本文件夹是「MIDAS Civil NX 建模向导 — 钢管混凝土拱（施工阶段联合截面）」"
                "为项目「%s」生成的成果。\r\n\r\n"
                "1) %s.mct\r\n"
                "   施工阶段模型（*STAGE 4 个阶段 + *CPOSECT4CS 联合截面：钢管 + 核心混凝土）。\r\n"
                "   MIDAS Civil NX：文件 → 导入 → MCT 命令文件，选中它即可生成模型。\r\n"
                "   注意：分析前先「另存为」，否则 MIDAS 会弹保存对话框。\r\n\r\n"
                "2) %s_一次成拱.mct\r\n"
                "   一次成拱 / 全截面一次浇筑的简化对比模型（单阶段、全截面一次生效、只加自重）。\r\n"
                "   另开一个 MIDAS 文档导入它，和上面的施工阶段模型比拱顶弯矩/位移。\r\n\r\n"
                "3) %s_数据.json\r\n"
                "   向导里的全部输入；在程序里「打开工程」选它就能接着改。\r\n\r\n"
                "4) %s_数据表.txt\r\n"
                "   拱轴线、钢管/混凝土截面特性、施工阶段表，导入后照着核对。\r\n"
                % (str(self.m.get("project", {}).get("name") or ""), fname, fname, fname, fname))
        return self._write_text(path, text)

    def _write_all(self, fname, out):
        files = []
        try:
            os.makedirs(out, exist_ok=True)
        except OSError as exc:
            self.warn("文件夹建不了", str(exc))
            return []
        try:
            mct = cf.build_mct(self.m)
            one = cf.build_one_step_mct(self.m)
        except Exception as exc:                               # noqa: BLE001
            self.warn("生成失败", "MCT 生成出错（参数里可能有异常值）：\n%s" % exc)
            return []
        p1 = os.path.join(out, fname + ".mct")
        p2 = os.path.join(out, fname + "_一次成拱.mct")
        pt = os.path.join(out, fname + "_数据表.txt")
        if self._write_text(p1, mct):
            files.append(("施工阶段 MCT", p1))
        if self._write_text(p2, one):
            files.append(("一次成拱对比 MCT", p2))
        if self._write_text(pt, cf.data_report(self.m)):
            files.append(("数据表", pt))
        pj = os.path.join(out, fname + "_数据.json")
        if self._write_json(pj):
            files.append(("向导数据（json）", pj))
        pr = os.path.join(out, "导入说明.txt")
        if self._write_readme(pr, fname):
            files.append(("导入说明", pr))
        self.save()
        return files

    def _gen_main(self):
        got = self._prepare()
        if not got:
            return
        fname, out = got
        try:
            mct = cf.build_mct(self.m)
        except Exception as exc:                               # noqa: BLE001
            self.warn("生成失败", "施工阶段 MCT 生成出错：\n%s" % exc)
            return
        p = os.path.join(out, fname + ".mct")
        if not self._write_text(p, mct):
            return
        pt = os.path.join(out, fname + "_数据表.txt")
        self._write_text(pt, cf.data_report(self.m))
        self._write_json(os.path.join(out, fname + "_数据.json"))
        sm = cf.summary(self.m)
        self._log("√ 已生成施工阶段 MCT：%s" % p)
        self._log("   数据表：%s" % pt)
        self._log("   节点 %d 个、单元 %d 个、施工阶段 %d 个、联合截面部件 2 个"
                  % (int(cf._f((self.m.get("arch") or {}).get("n"), 30)) + 1,
                     int(cf._f((self.m.get("arch") or {}).get("n"), 30)),
                     len(self.m.get("stages") or [])))
        self._log("   截面：钢管 A=%.4f m2，核心混凝土 A=%.4f m2，组合 A=%.4f m2"
                  % (sm["A_steel"], sm["A_conc"], sm["A"]))
        messagebox.showinfo("已生成", "施工阶段 MCT 已生成：\n%s\n\n接着：MIDAS → 文件 → 导入 → MCT 命令文件。"
                            % p)

    def _gen_one(self):
        got = self._prepare()
        if not got:
            return
        fname, out = got
        try:
            one = cf.build_one_step_mct(self.m)
        except Exception as exc:                               # noqa: BLE001
            self.warn("生成失败", "一次成拱对比 MCT 生成出错：\n%s" % exc)
            return
        p = os.path.join(out, fname + "_一次成拱.mct")
        if not self._write_text(p, one):
            return
        eq = cf.one_step_equivalent(self.m)
        self._log("√ 已生成一次成拱对比 MCT：%s" % p)
        self._log("   等效材料：E=%.4e kN/m2，ρ=%.3f kN/m3，等效 A=%.4f m2，等效 Iyy=%.5f m4"
                  % (eq["E"], eq["rho"], eq["A"], eq["Iyy"]))
        messagebox.showinfo("已生成", "一次成拱对比 MCT 已生成：\n%s" % p)

    def _gen_all(self):
        got = self._prepare()
        if not got:
            return
        fname, out = got
        files = self._write_all(fname, out)
        if not files:
            return
        for label, p in files:
            self._log("√ %s：%s" % (label, p))
        messagebox.showinfo("已生成",
                            "已生成 %d 个文件：\n%s\n\n在 MIDAS 里先导入施工阶段模型，"
                            "再导入一次成拱对比模型（各开一个文档）。"
                            % (len(files), "\n".join("· %s" % p for _l, p in files)))

    def _preview(self):
        try:
            mct = cf.build_mct(self.m)
        except Exception as exc:                               # noqa: BLE001
            self.warn("预览失败", str(exc))
            return
        try:
            self.log.configure(state="normal")
            self.log.delete("1.0", "end")
            self.log.insert("1.0", mct)
            self.log.configure(state="disabled")
        except (AttributeError, tk.TclError):
            return
        self._log("\n—— 上面是施工阶段 MCT 全文（%d 行）；改参数后重新点「预览」会刷新。——"
                  % (mct.count("\n") + 1))

    def _copy(self):
        probs = cf.all_issues(self.m)
        if probs:
            self.warn("先改完这些", "\n".join(probs[:8]))
            return
        try:
            mct = cf.build_mct(self.m)
        except Exception as exc:                               # noqa: BLE001
            self.warn("生成失败", str(exc))
            return
        try:
            self.wiz.clipboard_clear()
            self.wiz.clipboard_append(mct)
        except tk.TclError:
            return
        self._log("√ 施工阶段 MCT 全文已复制到剪贴板（可粘到 MIDAS 的 MCT 命令窗口运行）")
