# -*- coding: utf-8 -*-
"""两跨钢管混凝土拱 · 施工顺序 + 湿重两种模拟对比 —— 界面流程插件（flow_cfst2.py）。

结构：
    * 纯计算 / MCT 生成都在 cfst2_core.py（不依赖 tkinter，测试可以直接调）；
    * 本文件只做「一步一步填数值」的界面，按 flow_api.FlowBase 实现 4 步：
          1. 拱轴与几何    （两跨 L / f / 拱轴类型 / 每跨单元数 / 两跨连接方式 / 二期恒载）
          2. 钢管混凝土截面（φ1000×100 mm、Q485、C50、容重 25，截面示意图 + 特性）
          3. 施工阶段与湿重方式（4 个阶段可改名字/天数 + 方法 A/B 说明卡）
          4. 生成成果       （同时生成方法 A、方法 B 两套 MCT + 数据表 + 对比说明 + json）

主程序 midas_wizard.py 的 FLOW_PLUGIN_MODULES 里需要登记 ("cfst2", "flow_cfst2")，
本文件放到程序目录就会出现「两跨钢管混凝土（施工顺序·湿重对比）」这个流程。
"""

import os
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import cfst2_core as cf2
import cfst_core as cf
from flow_api import FlowBase

# 模块级也把引擎的函数暴露出来（SPEC 约定：测试可以 import flow_cfst2 直接调）
CODE = cf2.CODE
LABEL = cf2.LABEL
default_model = cf2.default_model
reset_model = cf2.reset_model
issues = cf2.issues
data_report = cf2.data_report
comparison_text = cf2.comparison_text
build_mct = cf2.build_mct
build_mct_pair = cf2.build_mct_pair
build_geometry = cf2.build_geometry
section_for = cf2.section_for
summary = cf2.summary


# ======================================================================
# 小工具
# ======================================================================
def _safe_name(s, fallback="两跨CFST"):
    s = str(s or "").strip()
    for ch in '\\/:*?"<>|':
        s = s.replace(ch, "-")
    s = s.strip().strip(".")
    return s or fallback


def _stage_plan_text(model):
    return cf2.stage_plan_text(model)


# ======================================================================
# 画图：两跨拱轴 / 钢管混凝土截面
# ======================================================================
def _draw_spans(cv, model):
    """画两跨拱轴线示意：两个拱圈、三个拱脚、跨径/矢高标注。"""
    cv.delete("all")
    w = max(cv.winfo_width(), 640)
    h = int(cv.winfo_height())
    if h < 60:                     # 窗口还没排好版时按「要求高度」画，否则会画到画布外
        h = int(cv.winfo_reqheight()) or 220
    try:
        gp = cf2.build_geometry(model)
    except Exception:                                  # noqa: BLE001
        gp = None
    if not gp:
        cv.create_text(w / 2.0, h / 2.0, text="（参数还不完整，暂时画不出来）", fill="#999999")
        return
    xs = [p[0] for p in gp["pts"]]
    zs = [p[1] for p in gp["pts"]]
    x0, x1 = min(xs), max(xs)
    z0, z1 = min(zs), max(zs)
    pad = 30
    sx = (w - 2 * pad) / (x1 - x0 or 1.0)
    sz = (h - 2 * pad) / ((z1 - z0) or 1.0)
    s = min(sx, sz)
    ox = pad + (w - 2 * pad - (x1 - x0) * s) / 2.0
    oy = h - pad

    def P(x, z):
        return (ox + (x - x0) * s, oy - (z - z0) * s)

    # 地面线（拱脚标高）
    cv.create_line(*P(x0 - (x1 - x0) * 0.05, z0), *P(x1 + (x1 - x0) * 0.05, z0),
                   fill="#aaaaaa", dash=(4, 3))

    # 两跨拱轴（不同颜色）
    for pts, color, tag in ((gp["span1_pts"], "#2d7d46", "第一跨"),
                            (gp["span2_pts"], "#1f6fb2", "第二跨")):
        poly = []
        for (x, z) in pts:
            poly += list(P(x, z))
        cv.create_line(*poly, fill=color, width=3)
        if len(pts) <= 40:
            for (x, z) in pts:
                px, py = P(x, z)
                cv.create_oval(px - 1.6, py - 1.6, px + 1.6, py + 1.6, fill=color, outline="")
        xm = (pts[0][0] + pts[-1][0]) / 2.0
        px, py = P(xm, z1)
        cv.create_text(px, py - 12, text=tag, fill=color)

    # 拱脚约束（红色方块）；中间拱脚两跨共用时标出来
    mid = gp["mid_nodes"]
    for k, nid in enumerate(gp["supports"]):
        node = [nd for nd in gp["nodes"] if nd[0] == nid]
        if not node:
            continue
        _i, x, z = node[0]
        px, py = P(x, z)
        cv.create_rectangle(px - 6, py - 4, px + 6, py + 10, fill="#c0392b", outline="")
        if len(mid) == 1 and nid == mid[0]:
            cv.create_text(px, py + 24, text="中间拱脚（两跨共用节点 %d）" % nid, fill="#c0392b")
        elif nid == 1:
            cv.create_text(px - 6, py + 24, text="左拱脚", fill="#c0392b", anchor="e")
        elif nid == gp["supports"][-1]:
            cv.create_text(px + 6, py + 24, text="右拱脚", fill="#c0392b", anchor="w")
        elif not gp["share"]:
            cv.create_text(px, py + 24, text="拱脚", fill="#c0392b")

    # 跨径 / 矢高标注（第一跨）
    p1, p2 = P(gp["L"] * 0.0, z0), P(gp["L"], z0)
    cv.create_line(*p1, *p2, arrow="both", fill="#666666")
    cv.create_text((p1[0] + p2[0]) / 2.0, p1[1] + 42,
                   text="每跨 L = %.2f m（两跨共 %.2f m）" % (gp["L"], 2.0 * gp["L"]),
                   fill="#666666")
    pc = P(gp["L"] / 2.0, z0 + (z1 - z0))
    cv.create_line(pc[0], pc[1], pc[0], oy, arrow="both", fill="#666666", dash=(3, 3))
    cv.create_text(pc[0] + 54, (pc[1] + oy) / 2.0, text="f = %.2f m" % (z1 - z0),
                   fill="#666666")
    cv.create_text(12, 12, anchor="nw", fill="#666666",
                   text="节点 %d 个 / 单元 %d 个    %s"
                        % (gp["n_nodes"], gp["n_elems"],
                           cf2.SPAN_JOINT_NAMES.get(gp["joint"], "")))


def _draw_section(cv, sec, model):
    """画钢管 + 核心混凝土的横断面示意（灰色 = 钢管，黄色 = 混凝土）。"""
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

    for part in sec.parts:
        if part.kind != "steel":
            continue
        for prim in part.prims:
            if prim[0] in ("ring", "circle"):
                oval(prim[1], prim[2], prim[3], fill="#aab2bd", outline="#5a6270", width=1.5)
            elif prim[0] == "rect":
                a, b = P(prim[1], prim[4])
                c, d = P(prim[2], prim[3])
                cv.create_rectangle(a, b, c, d, fill="#aab2bd", outline="#5a6270")
    for part in sec.parts:
        if part.kind != "conc":
            continue
        for prim in part.prims:
            if prim[0] == "circle":
                oval(prim[1], prim[2], prim[3], fill="#e8c25a", outline="#c9a227")
            elif prim[0] == "rect":
                a, b = P(prim[1], prim[4])
                c, d = P(prim[2], prim[3])
                cv.create_rectangle(a, b, c, d, fill="#e8c25a", outline="#c9a227")
    a, b = P(x1, 0), P(x2, 0)
    cv.create_line(*a, *b, fill="#888888", dash=(5, 3))
    a, b = P(0, z1), P(0, z2)
    cv.create_line(*a, *b, fill="#888888", dash=(5, 3))
    tb = model.get("tube") or {}
    cv.create_text(w / 2.0, h - 12,
                   text="D = %s mm    t = %s mm" % (tb.get("D_mm"), tb.get("t_mm")),
                   fill="#444444")
    cv.create_text(12, 12, text="■ 钢管   ■ 核心混凝土", anchor="nw", fill="#666666")


# ======================================================================
# 流程
# ======================================================================
class Flow(FlowBase):
    code = cf2.CODE
    label = cf2.LABEL
    step_ids = ["cfst2_geom", "cfst2_section", "cfst2_stages", "cfst2_export"]
    step_titles = ["拱轴与几何", "钢管混凝土截面", "施工阶段与湿重方式", "生成成果"]
    step_hints = [
        "填两跨的计算跨径/矢高、拱轴类型、每跨单元数，再选两跨怎么连接（默认中间拱脚共用节点）；"
        "二期恒载 q2 也在这里填。下方预览会跟着变。",
        "填钢管外径和壁厚（单位 mm）、选钢材牌号与核心混凝土等级；下方灰色是钢管、黄色是核心混凝土，"
        "程序自动算面积、惯矩、自重和管内混凝土湿重 q。",
        "4 个阶段（架设钢管 → 灌注混凝土 → 硬化 → 二期）的名字和天数都能改；下面说明同一模型会生成"
        "「方法 A 湿重作荷载」和「方法 B 材龄 0 d」两套 MCT 的差别。",
        "一次生成两套 MCT（方法 A / 方法 B）+ 数据表 + 对比说明 + json；"
        "拿去 MIDAS Civil NX「文件 → 导入 → MCT 命令文件」分别导入两个文档。",
    ]
    last_hint = ("两套 MCT 已准备好：分别导入两个 MIDAS 文档跑施工阶段分析，"
                 "按「对比说明.txt」里的步骤看方法 A / B 的差别。")

    # ---------------- 弹窗（统一走本模块的 messagebox，测试可以整体替身） ----------------
    def warn(self, title, msg):
        messagebox.showwarning(title, msg)

    def info(self, title, msg):
        messagebox.showinfo(title, msg)

    def ask(self, title, msg):
        return bool(messagebox.askyesno(title, msg))

    # ---------------- 数据 ----------------
    def default_model(self):
        return cf2.default_model()

    def reset_model(self):
        return cf2.reset_model()

    def issues(self, sid):
        return cf2.issues(self.m, sid)

    def data_report(self):
        return cf2.data_report(self.m)

    def clear_title(self, sid):
        return {"cfst2_geom": "拱轴与几何", "cfst2_section": "钢管混凝土截面",
                "cfst2_stages": "施工阶段与湿重方式"}.get(sid, "")

    def clear(self, sid):
        d = cf2.default_model()
        m = self.m
        if sid == "cfst2_geom":
            m["geom"] = dict(d["geom"])
        elif sid == "cfst2_section":
            m["tube"] = dict(d["tube"])
            m["gamma"] = d["gamma"]
        elif sid == "cfst2_stages":
            for k in ("stages", "conc_stage_a", "conc_age_a", "conc_stage_b", "conc_age_b"):
                m[k] = [dict(x) for x in d[k]] if isinstance(d[k], list) else d[k]

    def export(self):
        """生成两套 MCT（外壳不强制调用，导出按钮也会走这里）。"""
        got = self._prepare()
        if not got:
            return []
        fname, out = got
        return self._write_all(fname, out)

    # ==================================================================
    # 第 1 步：拱轴与几何
    # ==================================================================
    def step_cfst2_geom(self):
        m = self.m
        g = m.setdefault("geom", {})
        for k, v in cf2.default_model()["geom"].items():
            g.setdefault(k, v)

        box = self.card("两跨拱轴与几何参数")
        rowA = ttk.Frame(box)
        rowA.pack(fill="x", anchor="w")

        type_names = [t[1] for t in cf2.ARCH_TYPES]
        type_back = {t[1]: t[0] for t in cf2.ARCH_TYPES}
        joint_names = [t[1] for t in cf2.SPAN_JOINTS]
        joint_back = {t[1]: t[0] for t in cf2.SPAN_JOINTS}

        v_type = tk.StringVar(value=cf2.ARCH_TYPE_NAMES.get(g.get("type"), "二次抛物线"))
        ttk.Label(rowA, text="拱轴类型").grid(row=0, column=0, sticky="w", padx=(0, 4), pady=3)
        cb_type = ttk.Combobox(rowA, textvariable=v_type, values=type_names, width=10,
                               state="readonly")
        cb_type.grid(row=0, column=1, sticky="w", padx=(0, 20), pady=3)

        v_joint = tk.StringVar(value=cf2.SPAN_JOINT_NAMES.get(g.get("joint"), joint_names[0]))
        ttk.Label(rowA, text="两跨连接方式").grid(row=0, column=2, sticky="w", padx=(0, 4))
        cb_joint = ttk.Combobox(rowA, textvariable=v_joint, values=joint_names, width=24,
                                state="readonly")
        cb_joint.grid(row=0, column=3, sticky="w", padx=(0, 20))

        # 数字参数按「一行一件事」放：L / f / 单元数，然后 m / 二期恒载
        entries = []
        field_rows = [
            [("计算跨径 L (m)", "L", 8), ("计算矢高 f (m)", "f", 8), ("每跨单元数", "n", 6)],
            [("拱轴系数 m", "m", 6), ("二期恒载 q2 (kN/m)", "q2", 8)],
        ]
        for group in field_rows:
            row_frame = ttk.Frame(box)
            row_frame.pack(fill="x", anchor="w")
            for col, (lab, key, wid) in enumerate(group):
                ttk.Label(row_frame, text=lab).grid(row=0, column=col * 2, sticky="w",
                                                    padx=(0, 4), pady=3)
                e = ttk.Entry(row_frame, textvariable=self.svar(g, key, g.get(key)), width=wid)
                e.grid(row=0, column=col * 2 + 1, sticky="w", padx=(0, 20), pady=3)
                entries.append(e)
        ttk.Label(box, text="（拱轴系数 m 只有悬链线用，必须大于 1，常用 1.1~3；"
                            "二期恒载填 0 表示不加）",
                  style="Hint.TLabel", wraplength=700, justify="left").pack(
            anchor="w", pady=(2, 0))

        info = ttk.Label(box, text="", style="Ok.TLabel", justify="left")
        info.pack(anchor="w", pady=(8, 0))

        prev = self.card("两跨拱轴线预览（改参数自动刷新）")
        cv = tk.Canvas(prev, height=220, bg="white", highlightthickness=1,
                       highlightbackground="#cccccc")
        cv.pack(fill="x")

        def refresh(*_e):
            _draw_spans(cv, m)
            try:
                gp = cf2.build_geometry(m)
            except Exception:                              # noqa: BLE001
                info.configure(text="参数还不完整")
                return
            sm = cf2.summary(m)
            info.configure(text=(
                "节点 %d 个 / 单元 %d 个（每跨 %d 个单元）    单跨拱轴线长 %.3f m\n"
                "拱脚节点：%s（固结）    组合截面 A = %.4f m2    管内混凝土湿重 q = %.3f kN/m"
                % (gp["n_nodes"], gp["n_elems"], gp["n"], gp["arch_len"],
                   " / ".join(str(x) for x in gp["supports"]), sm["A"], cf2.wet_q(m))))

        def on_type(*_e):
            g["type"] = type_back.get(v_type.get(), "parabola")
            self.rerender()

        def on_joint(*_e):
            g["joint"] = joint_back.get(v_joint.get(), "share")
            self.rerender()

        cb_type.bind("<<ComboboxSelected>>", on_type)
        cb_joint.bind("<<ComboboxSelected>>", on_joint)
        cv.bind("<Configure>", refresh)
        for e in entries:
            e.bind("<KeyRelease>", refresh)
            e.bind("<FocusOut>", refresh)
        refresh()
        ttk.Label(prev, text="X 为顺桥向（第一跨 0~L、第二跨 L~2L），Z 竖直向上；"
                             "红色方块是拱脚约束（无铰固结）。",
                  style="Hint.TLabel").pack(anchor="w", pady=(4, 0))

    # ==================================================================
    # 第 2 步：钢管混凝土截面
    # ==================================================================
    def step_cfst2_section(self):
        m = self.m
        tb = m.setdefault("tube", {})
        for k, v in cf2.default_model()["tube"].items():
            tb.setdefault(k, v)

        box = self.card("钢管混凝土截面参数（单位 mm）")
        grid = ttk.Frame(box)
        grid.pack(fill="x", anchor="w")

        v_D = self.svar(tb, "D_mm", tb.get("D_mm"))
        v_t = self.svar(tb, "t_mm", tb.get("t_mm"))
        ttk.Label(grid, text="钢管外径 D (mm)").grid(row=0, column=0, sticky="w", padx=(0, 4), pady=3)
        e_D = ttk.Entry(grid, textvariable=v_D, width=10)
        e_D.grid(row=0, column=1, sticky="w", padx=(0, 22), pady=3)
        ttk.Label(grid, text="钢管壁厚 t (mm)").grid(row=0, column=2, sticky="w", padx=(0, 4))
        e_t = ttk.Entry(grid, textvariable=v_t, width=10)
        e_t.grid(row=0, column=3, sticky="w", padx=(0, 22))

        v_conc = tk.StringVar(value=cf.model_conc(cf2.core_model(m, "A")))
        v_steel = tk.StringVar(value=cf.model_steel(cf2.core_model(m, "A")))
        ttk.Label(grid, text="核心混凝土").grid(row=1, column=0, sticky="w", padx=(0, 4), pady=3)
        cb_conc = ttk.Combobox(grid, textvariable=v_conc, values=cf.CONC_LIST, width=8,
                               state="readonly")
        cb_conc.grid(row=1, column=1, sticky="w", padx=(0, 22), pady=3)
        ttk.Label(grid, text="钢材牌号").grid(row=1, column=2, sticky="w", padx=(0, 4))
        cb_steel = ttk.Combobox(grid, textvariable=v_steel, values=cf.STEEL_LIST, width=8,
                                state="readonly")
        cb_steel.grid(row=1, column=3, sticky="w", padx=(0, 22))

        ttk.Label(grid, text="混凝土容重 (kN/m3)").grid(row=2, column=0, sticky="w",
                                                     padx=(0, 4), pady=3)
        e_gam = ttk.Entry(grid, textvariable=self.svar(m, "gamma", m.get("gamma")), width=10)
        e_gam.grid(row=2, column=1, sticky="w", padx=(0, 22), pady=3)
        ttk.Label(grid, text="（默认 25；管内混凝土湿重 = 核心混凝土面积 × 容重）",
                  style="Hint.TLabel").grid(row=3, column=0, columnspan=8, sticky="w")

        prev = self.card("截面预览与特性（改数字自动刷新）")
        cv = tk.Canvas(prev, height=240, bg="white", highlightthickness=1,
                       highlightbackground="#cccccc")
        cv.pack(fill="x")
        info = ttk.Label(prev, text="", style="Ok.TLabel", justify="left")
        info.pack(anchor="w", pady=(8, 0))

        def refresh(*_e):
            try:
                sec = cf2.section_for(m)
                _draw_section(cv, sec, m)
                sm = cf2.summary(m)
                info.configure(text=(
                    "钢管：A = %.4f m2   Iyy = %.6f m4   Izz = %.6f m4\n"
                    "核心混凝土：A = %.4f m2   Iyy = %.6f m4   Izz = %.6f m4\n"
                    "组合截面：A = %.4f m2   Iyy = %.6f m4   Izz = %.6f m4   含钢率 %.1f %%\n"
                    "单位长度自重：钢管 %.2f + 混凝土 %.2f = %.2f kN/m    "
                    "管内混凝土湿重 q = %.3f kN/m"
                    % (sm["A_steel"], sm["Iyy_steel"], sm["Izz_steel"],
                       sm["A_conc"], sm["Iyy_conc"], sm["Izz_conc"],
                       sm["A"], sm["Iyy"], sm["Izz"], sm["steel_ratio"] * 100.0,
                       sm["w_steel"], sm["w_conc"], sm["w_total"], sm["w_conc"])))
            except Exception:                              # noqa: BLE001
                info.configure(text="参数还不完整")

        def on_conc(*_e):
            tb["conc"] = v_conc.get()
            refresh()

        def on_steel(*_e):
            tb["steel"] = v_steel.get()
            refresh()

        cb_conc.bind("<<ComboboxSelected>>", on_conc)
        cb_steel.bind("<<ComboboxSelected>>", on_steel)
        cv.bind("<Configure>", refresh)
        for e in (e_D, e_t, e_gam):
            e.bind("<KeyRelease>", refresh)
            e.bind("<FocusOut>", refresh)
        refresh()
        ttk.Label(prev, text="钢管按圆环、核心混凝土按实心圆算面积和惯矩；组合截面 = 两者之和。",
                  style="Hint.TLabel").pack(anchor="w", pady=(4, 0))

    # ==================================================================
    # 第 3 步：施工阶段与湿重方式
    # ==================================================================
    def step_cfst2_stages(self):
        m = self.m
        stages = m.setdefault("stages", [])
        if len(stages) < 4:
            d = [dict(x) for x in cf2.default_model()["stages"]]
            stages[:] = d
        names = cf2.stage_names(m)

        box = self.card("施工阶段表（两跨同时施工；名字和天数都能改）")
        grid = self.table(box, ["序号", "阶段名", "持续天数 (d)", "这一步做什么"])
        for i, st in enumerate(stages):
            ttk.Label(grid, text=str(i + 1)).grid(row=i + 1, column=0, sticky="w",
                                                  padx=(0, 12), pady=3)
            self.entry(grid, self.svar(st, "name", st.get("name")), width=10, row=i + 1, col=1)
            self.entry(grid, self.svar(st, "days", st.get("days")), width=8, row=i + 1, col=2)
            ttk.Label(grid, text=str(st.get("desc") or ""), style="Hint.TLabel").grid(
                row=i + 1, column=3, sticky="w", padx=(0, 12), pady=3)
        ttk.Label(box, text="默认：CS1 7 d 架设两跨钢管 → CS2 1 d 灌注 → CS3 20 d 硬化 → "
                            "CS4 7 d 二期恒载。改完名字点一下别处就会重新生成下面的说明。",
                  style="Hint.TLabel", wraplength=680, justify="left").pack(
            anchor="w", pady=(8, 0))

        # ---- 湿重模拟方式说明卡 ----
        box2 = self.card("湿重模拟方式（同一模型生成两套 MCT 做对比）")
        wet = cf2.wet_q(m)
        sm = cf2.summary(m)
        name_a = cf2.conc_stage_name(m, "A") or "—"
        name_b = cf2.conc_stage_name(m, "B") or "—"
        age_a = cf2.conc_age(m, "A")
        age_b = cf2.conc_age(m, "B")
        wet_stage = cf2.wet_stage_name(m) or "—"
        ttk.Label(box2, justify="left", wraplength=680, text=(
            "方法 A（湿重作梁单元荷载）：混凝土 Part 在「%s」才激活、材龄 %.0f d（硬化后才参与刚度）；\n"
            "    在「%s」对两跨全部钢单元加 %s 均布荷载 q = %.3f kN/m（= 核心混凝土面积 %.4f m2 × 容重 %.1f），\n"
            "    到「%s」用 DLOAD 卸掉 %s —— 重量和刚度都不会重复计算。\n\n"
            "方法 B（材龄 0 d，湿重 = 混凝土单元自重）：混凝土 Part 在「%s」就激活、材龄 %.0f d；\n"
            "    MIDAS 里材龄 0 表示混凝土处于流动（湿重）状态：只计重量、不计刚度（第五讲做法）；\n"
            "    湿重就是该 Part 的自重，由 *SELFWEIGHT 随 Part 激活自动计入，不另加湿重荷载工况。\n\n"
            "两套 MCT 的 *CPOSECT4CS 第 2 个部件（核心混凝土）的「叠合阶段 / 材龄」不同，"
            "方法 A 还多一个 %s 荷载工况 —— 其余命令完全一样。"
            % (name_a, age_a, wet_stage, cf2.WET_GROUP, wet, sm["A_conc"],
               cf._f(m.get("gamma"), cf2.GAMMA_CONC), name_a, cf2.WET_GROUP,
               name_b, age_b, cf2.WET_GROUP))
        ).pack(anchor="w")

        # ---- 混凝土 Part 的激活阶段 / 材龄（可调） ----
        g2 = ttk.Frame(box2)
        g2.pack(fill="x", pady=(10, 0))
        idx_a = cf2.conc_stage_index(m, "A")
        idx_b = cf2.conc_stage_index(m, "B")
        cur_a = names[idx_a] if 0 <= idx_a < len(names) else (names[2] if len(names) > 2 else "")
        cur_b = names[idx_b] if 0 <= idx_b < len(names) else (names[1] if len(names) > 1 else "")

        ttk.Label(g2, text="方法 A 混凝土激活阶段").grid(row=0, column=0, sticky="w",
                                                     padx=(0, 4), pady=3)
        v_a = tk.StringVar(value=cur_a)
        cb_a = ttk.Combobox(g2, textvariable=v_a, values=names, width=10, state="readonly")
        cb_a.grid(row=0, column=1, sticky="w", padx=(0, 22), pady=3)
        ttk.Label(g2, text="材龄 (d)").grid(row=0, column=2, sticky="w", padx=(0, 4))
        self.entry(g2, self.svar(m, "conc_age_a", m.get("conc_age_a")), width=8, row=0, col=3)

        ttk.Label(g2, text="方法 B 混凝土激活阶段").grid(row=1, column=0, sticky="w",
                                                     padx=(0, 4), pady=3)
        v_b = tk.StringVar(value=cur_b)
        cb_b = ttk.Combobox(g2, textvariable=v_b, values=names, width=10, state="readonly")
        cb_b.grid(row=1, column=1, sticky="w", padx=(0, 22), pady=3)
        ttk.Label(g2, text="材龄 (d)").grid(row=1, column=2, sticky="w", padx=(0, 4))
        self.entry(g2, self.svar(m, "conc_age_b", m.get("conc_age_b")), width=8, row=1, col=3)

        def on_a(*_e):
            try:
                m["conc_stage_a"] = names.index(v_a.get())
            except ValueError:
                pass
            self.rerender()

        def on_b(*_e):
            try:
                m["conc_stage_b"] = names.index(v_b.get())
            except ValueError:
                pass
            self.rerender()

        cb_a.bind("<<ComboboxSelected>>", on_a)
        cb_b.bind("<<ComboboxSelected>>", on_b)

        # ---- 阶段计划说明 ----
        box3 = self.card("阶段计划（自动生成，核对一下再生成成果）")
        txt = tk.Text(box3, height=6, wrap="none", font=("Consolas", 9))
        txt.insert("1.0", _stage_plan_text(m))
        txt.configure(state="disabled")
        txt.pack(fill="x")
        ttk.Label(box3, text="湿重 q = 核心混凝土面积 × 容重，两跨合计 = q × 两跨拱轴线总长；"
                             "方法 A 在灌注阶段加、硬化阶段卸，方法 B 则由 Part 自重自动进入。",
                  style="Hint.TLabel", wraplength=680, justify="left").pack(
            anchor="w", pady=(6, 0))

    # ==================================================================
    # 第 4 步：生成成果
    # ==================================================================
    def step_cfst2_export(self):
        m = self.m
        prj = m.setdefault("project", {})
        if not hasattr(self, "_v_outdir"):
            self._v_outdir = tk.StringVar(value=prj.get("outdir") or self.out_dir())
        if not hasattr(self, "_v_fname"):
            self._v_fname = tk.StringVar(value=prj.get("name") or "两跨CFST")

        box = self.card("生成成果（同时生成方法 A / 方法 B 两套 MCT）")
        line0 = ttk.Frame(box)
        line0.pack(fill="x")
        ttk.Label(line0, text="输出文件夹").pack(side="left")
        ttk.Entry(line0, textvariable=self._v_outdir, width=52).pack(side="left", padx=(6, 6))
        ttk.Button(line0, text="选择…", command=self._pick_dir).pack(side="left")
        ttk.Button(line0, text="打开", command=self._open_dir).pack(side="left", padx=(6, 0))

        line1 = ttk.Frame(box)
        line1.pack(fill="x", pady=(8, 0))
        ttk.Label(line1, text="文件名前缀").pack(side="left")
        ttk.Entry(line1, textvariable=self._v_fname, width=26).pack(side="left", padx=(6, 14))
        ttk.Label(box, text="（会生成 <前缀>_方法A_湿重作荷载.mct / <前缀>_方法B_材龄0d.mct "
                            "/ <前缀>_数据表.txt / <前缀>_对比说明.txt / <前缀>_数据.json）",
                  style="Hint.TLabel", wraplength=680, justify="left").pack(
            anchor="w", pady=(4, 0))

        # 两法的关键差别一览
        gp = cf2.build_geometry(m)
        sm = cf2.summary(m)
        box0 = self.card("本模型两法差异一览（生成前核对）")
        grid = self.table(box0, ["项目", "方法 A", "方法 B"])
        rows = [
            ("湿重怎么加", "CS2 加 %s 梁单元均布 q=%.3f kN/m" % (cf2.WET_GROUP, cf2.wet_q(m)),
             "不加荷载，混凝土 Part 自重自动计入"),
            ("混凝土 Part 激活阶段",
             "%s（材龄 %.0f d）" % (cf2.conc_stage_name(m, "A"), cf2.conc_age(m, "A")),
             "%s（材龄 %.0f d）" % (cf2.conc_stage_name(m, "B"), cf2.conc_age(m, "B"))),
            ("MCT 文件", "<前缀>%s" % cf2.METHOD_FILES["A"].strip(),
             "<前缀>%s" % cf2.METHOD_FILES["B"].strip()),
            ("几何 / 单元", "%d 节点、%d 单元" % (gp["n_nodes"], gp["n_elems"]),
             "完全相同"),
        ]
        for r, (k, a, b) in enumerate(rows, start=1):
            ttk.Label(grid, text=k).grid(row=r, column=0, sticky="w", padx=(0, 14), pady=3)
            ttk.Label(grid, text=a).grid(row=r, column=1, sticky="w", padx=(0, 22), pady=3)
            ttk.Label(grid, text=b, style="Hint.TLabel").grid(row=r, column=2, sticky="w", pady=3)
        ttk.Label(box0, text="（两法在灌注阶段给结构加的总重量相同 = 核心混凝土面积 × 容重，"
                             "差别只在「刚度何时参与」）", style="Hint.TLabel",
                  wraplength=680, justify="left").pack(anchor="w", pady=(6, 0))

        line2 = ttk.Frame(box)
        line2.pack(fill="x", pady=(10, 0))
        ttk.Button(line2, text="① 生成两套 MCT + 全部成果",
                   command=self._gen_all).pack(side="left")
        ttk.Button(line2, text="② 预览方法 A", command=lambda: self._preview("A")).pack(
            side="left", padx=(8, 0))
        ttk.Button(line2, text="③ 预览方法 B", command=lambda: self._preview("B")).pack(
            side="left", padx=(8, 0))

        line3 = ttk.Frame(box)
        line3.pack(fill="x", pady=(6, 0))
        ttk.Button(line3, text="④ 复制方法 A 的 MCT 文本",
                   command=lambda: self._copy("A")).pack(side="left")
        ttk.Button(line3, text="⑤ 复制方法 B 的 MCT 文本",
                   command=lambda: self._copy("B")).pack(side="left", padx=(8, 0))

        ttk.Label(box, justify="left", wraplength=680, text=(
            "MIDAS Civil NX 里：文件 → 新建项目 → 文件 → 导入 → MCT 命令文件，先导入方法 A 的 MCT；\n"
            "再新建一个文档导入方法 B 的 MCT。两个文档都「运行分析」，按《对比说明.txt》里的步骤看\n"
            "CS2 / CS3 / CS4 阶段的位移、钢管应力、反力和成桥内力，即可比较两种湿重模拟方式的差别。"),
            style="Hint.TLabel").pack(anchor="w", pady=(8, 0))

        box2 = self.card("日志 / 预览")
        self.log = tk.Text(box2, height=16, wrap="none", font=("Consolas", 9))
        sb = ttk.Scrollbar(box2, orient="vertical", command=self.log.yview)
        self.log.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.log.pack(side="left", fill="both", expand=True)
        self.log.insert("1.0", "（先点 ① 生成两套 MCT + 全部成果，或点 ②/③ 预览。）")
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
        probs = cf2.all_issues(self.m)
        if probs:
            messagebox.showwarning("先改完这些再生成",
                                   "下面这些问题会让 MIDAS 导入报错，先改好（点左侧步骤名可直接跳过去）：\n\n"
                                   + "\n".join("· " + p for p in probs[:10])
                                   + ("\n…还有 %d 条" % (len(probs) - 10) if len(probs) > 10 else ""))
            return None
        fname = _safe_name(self._v_fname.get() or self.m["project"].get("name") or "两跨CFST")
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
        """写 GBK（和梁桥/拱桥/单跨 CFST 流程一致；MIDAS 的 MCT 用 ANSI/GBK 最稳）。"""
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

    def _write_all(self, fname, out):
        """一次写出 2 套 MCT + 数据表 + 对比说明 + json；返回 [(说明, 路径), ...]。"""
        try:
            os.makedirs(out, exist_ok=True)
        except OSError as exc:
            self.warn("文件夹建不了", str(exc))
            return []
        try:
            mcts = cf2.build_mct_pair(self.m)
        except Exception as exc:                               # noqa: BLE001
            self.warn("生成失败", "MCT 生成出错（参数里可能有异常值）：\n%s" % exc)
            return []
        files = []
        for method, mct in (("A", mcts["A"]), ("B", mcts["B"])):
            p = os.path.join(out, fname + cf2.METHOD_FILES[method])
            if self._write_text(p, mct):
                files.append(("方法 %s MCT（%s）" % (method, cf2.METHOD_NAMES[method]), p))
        p_data = os.path.join(out, fname + "_数据表.txt")
        if self._write_text(p_data, cf2.data_report(self.m)):
            files.append(("数据表", p_data))
        p_cmp = os.path.join(out, fname + "_对比说明.txt")
        if self._write_text(p_cmp, cf2.comparison_text(self.m)):
            files.append(("两法对比说明", p_cmp))
        p_json = os.path.join(out, fname + "_数据.json")
        if self._write_json(p_json):
            files.append(("向导数据（json）", p_json))
        self.save()
        return files

    def _gen_a(self):
        got = self._prepare()
        if not got:
            return None
        fname, out = got
        try:
            text = cf2.build_mct(self.m, "A")
        except Exception as exc:                               # noqa: BLE001
            self.warn("生成失败", "方法 A MCT 生成出错：\n%s" % exc)
            return None
        p = os.path.join(out, fname + cf2.METHOD_FILES["A"])
        if not self._write_text(p, text):
            return None
        self._log("√ 已生成方法 A MCT：%s" % p)
        return p

    def _gen_b(self):
        got = self._prepare()
        if not got:
            return None
        fname, out = got
        try:
            text = cf2.build_mct(self.m, "B")
        except Exception as exc:                               # noqa: BLE001
            self.warn("生成失败", "方法 B MCT 生成出错：\n%s" % exc)
            return None
        p = os.path.join(out, fname + cf2.METHOD_FILES["B"])
        if not self._write_text(p, text):
            return None
        self._log("√ 已生成方法 B MCT：%s" % p)
        return p

    def _gen_all(self):
        got = self._prepare()
        if not got:
            return
        fname, out = got
        files = self._write_all(fname, out)
        if not files:
            return
        gp = cf2.build_geometry(self.m)
        for label, p in files:
            self._log("√ %s：%s" % (label, p))
        self._log("   节点 %d 个、单元 %d 个、施工阶段 %d 个、联合截面部件 2 个"
                  % (gp["n_nodes"], gp["n_elems"], len(self.m.get("stages") or [])))
        self._log("   方法 A：%s 激活混凝土 Part（材龄 %.0f d），湿重 %s q=%.3f kN/m"
                  % (cf2.conc_stage_name(self.m, "A"), cf2.conc_age(self.m, "A"),
                     cf2.WET_GROUP, cf2.wet_q(self.m)))
        self._log("   方法 B：%s 激活混凝土 Part（材龄 %.0f d），湿重 = 混凝土 Part 自重"
                  % (cf2.conc_stage_name(self.m, "B"), cf2.conc_age(self.m, "B")))
        messagebox.showinfo("已生成",
                            "已生成 %d 个文件：\n%s\n\n在 MIDAS 里分别新建两个文档，"
                            "各导入一套 MCT，跑完施工阶段分析后按《对比说明.txt》里的步骤对比。"
                            % (len(files), "\n".join("· %s" % p for _l, p in files)))

    def _preview(self, method="A"):
        try:
            text = cf2.build_mct(self.m, method)
        except Exception as exc:                               # noqa: BLE001
            self.warn("预览失败", str(exc))
            return
        try:
            self.log.configure(state="normal")
            self.log.delete("1.0", "end")
            self.log.insert("1.0", text)
            self.log.configure(state="disabled")
        except (AttributeError, tk.TclError):
            return
        self._log("\n—— 上面是方法 %s 的 MCT 全文（%d 行）；改参数后重新点「预览」会刷新。——"
                  % (method, text.count("\n") + 1))

    def _copy(self, method="A"):
        probs = cf2.all_issues(self.m)
        if probs:
            self.warn("先改完这些", "\n".join(probs[:8]))
            return
        try:
            text = cf2.build_mct(self.m, method)
        except Exception as exc:                               # noqa: BLE001
            self.warn("生成失败", str(exc))
            return
        try:
            self.wiz.clipboard_clear()
            self.wiz.clipboard_append(text)
        except tk.TclError:
            return
        self._log("√ 方法 %s 的 MCT 全文已复制到剪贴板（可粘到 MIDAS 的 MCT 命令窗口运行）"
                  % method)
