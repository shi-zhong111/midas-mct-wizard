# -*- coding: utf-8 -*-
r"""预制 T 梁 + 现浇桥面板 · 施工阶段联合截面 —— 界面流程插件（flow_tbeam.py）。

结构（照 flow_cfst.py 的写法）：
    * 纯计算 / MCT 生成都在 tbeam_core.py（不依赖 tkinter，测试可以直接调）；
    * 本文件只做「一步一步填数值」的界面，按 flow_api.FlowBase 实现 4 步：
          1. 跨径与截面  （L、板宽/板厚、梁肋宽/高、单元数、材料、容重、二期恒载、
                          施工阶段天数；右边实时画 T 形截面并给截面特性）
          2. 施工阶段    （阶段表：名字/天数/说明可改；说明湿重加在 CS2、
                          联合截面在 CS3 生效（材龄 20 d））
          3. 结果预览    （纯函数算的截面特性 / 各阶段内力 / 联合截面内力分配 /
                          跨中应力 / 与一次成型对比，只读文本 + 示意图）
          4. 生成成果    （<名字>.mct + _数据表.txt + _计算结果.txt + _数据.json）

主程序 midas_wizard.py 的 FLOW_PLUGIN_MODULES 里需要登记 ("tbeam", "flow_tbeam")，
本文件放到程序目录就会出现「预制T梁+现浇桥面板（施工阶段联合截面）」这个流程。
"""

import json
import os
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import tbeam_core as tb
from flow_api import FlowBase

# 模块级也把引擎的函数暴露出来（SPEC 约定：测试可以 import flow_tbeam 直接调）
CODE = tb.CODE
LABEL = tb.LABEL
default_model = tb.default_model
reset_model = tb.reset_model
issues = tb.issues
data_report = tb.data_report
result_report = tb.result_report
build_mct = tb.build_mct
geom = tb.geom
results = tb.results
stage_names = tb.stage_names


# ======================================================================
# 小工具
# ======================================================================
def _safe_name(s, fallback="预制T梁_20m_施工阶段"):
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


# ======================================================================
# 画图：T 形截面 + 施工阶段示意
# ======================================================================
def _canvas_size(cv, min_w=560, min_h=220):
    """窗口还没排好版时 winfo_height() 可能是 1，用 winfo_reqheight() 兜底（老流程踩过的坑）。"""
    w = max(cv.winfo_width(), min_w)
    h = int(cv.winfo_height())
    if h < 60:
        h = int(cv.winfo_reqheight()) or min_h
    return w, h


def _draw_section(cv, model, model_ok=True):
    """画 T 形截面：黄色=现浇桥面板（Part 2）、灰色=预制主梁梁肋（Part 1），标形心轴与尺寸。"""
    cv.delete("all")
    w, h = _canvas_size(cv, 520, 240)
    try:
        g = tb.geom(model)
    except Exception:                                   # noqa: BLE001
        g = None
    if not g or g["bw"] <= 0 or g["hw"] <= 0 or g["bs"] <= 0 or g["ts"] <= 0:
        cv.create_text(w / 2.0, h / 2.0, text="（参数还不完整，暂时画不出来）", fill="#999999")
        return
    bw, hw, bs, ts, yb = g["bw"], g["hw"], g["bs"], g["ts"], g["yb"]
    H = hw + ts
    pad_x, pad_t, pad_b = 64, 26, 46
    s = min((w - 2 * pad_x) / max(bs, 1e-9), (h - pad_t - pad_b) / max(H, 1e-9))
    s = max(s, 1e-6)
    ox = w / 2.0
    oy = h - pad_b                     # 梁底

    def P(x, z):
        return (ox + x * s, oy - z * s)

    # 预制主梁梁肋（灰）
    a, b = P(-bw / 2.0, hw), P(bw / 2.0, 0.0)
    cv.create_rectangle(a[0], a[1], b[0], b[1], fill="#b9c0c9", outline="#5a6270", width=1.5)
    # 现浇桥面板（黄）
    a, b = P(-bs / 2.0, H), P(bs / 2.0, hw)
    cv.create_rectangle(a[0], a[1], b[0], b[1], fill="#e8c25a", outline="#c9a227", width=1.5)
    # 组合截面形心轴 = 单元线（节点线）
    y_bar = P(0.0, yb)
    cv.create_line(10, y_bar[1], w - 10, y_bar[1], fill="#c0392b", dash=(6, 3), width=1.4)
    cv.create_text(w - 12, y_bar[1] - 9, text="组合形心 = 单元线 z = 0", anchor="e",
                   fill="#c0392b")
    # 主梁自身形心轴
    y1 = P(0.0, hw / 2.0)
    cv.create_line(10, y1[1], w - 10, y1[1], fill="#7f8c8d", dash=(2, 4))
    cv.create_text(12, y1[1] - 8, text="预制主梁形心（距梁底 %.3f m）" % (hw / 2.0),
                   anchor="w", fill="#7f8c8d")
    # 尺寸标注
    p1, p2 = P(-bs / 2.0, H + 0.02), P(bs / 2.0, H + 0.02)
    cv.create_line(*p1, *p2, arrow="both", fill="#666666")
    cv.create_text((p1[0] + p2[0]) / 2.0, p1[1] - 9, text="板宽 %.2f m" % bs, fill="#666666")
    p1, p2 = P(bw / 2.0 + 0.03, 0.0), P(bw / 2.0 + 0.03, hw)
    cv.create_line(*p1, *p2, arrow="both", fill="#666666")
    cv.create_text(p1[0] + 8, (p1[1] + p2[1]) / 2.0, text="肋高 %.2f m" % hw, anchor="w",
                   fill="#666666")
    cv.create_text(12, 12, text="■ 预制主梁梁肋（%.2f×%.2f m）   ■ 现浇桥面板（%.2f×%.2f m）"
                   % (bw, hw, bs, ts), anchor="nw", fill="#666666")
    # 组合截面读数
    cv.create_text(12, h - 10, anchor="w", fill="#444444",
                   text="组合截面：A = %.4f m2    形心距梁底 yb = %.4f m    I = %.6f m4"
                        % (g["A"], yb, g["I"]))


def _draw_stages(cv, model):
    """画 4 个施工阶段：每个阶段一个小图，标荷载、参与受力的部件、跨中弯矩。"""
    cv.delete("all")
    w, h = _canvas_size(cv, 760, 200)
    try:
        r = tb.results(model)
        names = tb.stage_names(model)
        comp = tb.comp_stage_of(model)
        wet = tb.wet_stage_of(model)
    except Exception:                                   # noqa: BLE001
        cv.create_text(w / 2.0, h / 2.0, text="（参数还不完整，暂时画不出来）", fill="#999999")
        return
    stages = model.get("stages") or []
    n = max(1, len(stages))
    gap = 12
    bw_box = (w - gap * (n + 1)) / n
    bh_box = h - 46
    top = 20
    for i, st in enumerate(stages):
        nm = str(st.get("name") or "")
        x0 = gap + i * (bw_box + gap)
        x1 = x0 + bw_box
        # 阶段框
        cv.create_rectangle(x0, top, x1, top + bh_box, fill="#f7f8fa",
                            outline="#c8ccd2")
        cv.create_text(x0 + 6, top + 6, anchor="nw", fill="#2c3e50",
                       text="%s  %s d" % (nm, st.get("days", "")))
        # 梁：预制（灰）+ 板（黄，只有联合后/浇板后画淡色）
        cx = (x0 + x1) / 2.0
        beam_y = top + bh_box * 0.62
        sl = 4.0
        bw_h = bh_box * 0.30
        cv.create_rectangle(cx - 5, beam_y - bw_h, cx + 5, beam_y, fill="#b9c0c9",
                            outline="#5a6270")
        # 板：CS1 还没有板；CS2 画虚线的湿板；>=CS3 画实心黄板
        idx = tb.stage_index(model, nm)
        comp_idx = tb.stage_index(model, comp)
        has_slab = idx >= comp_idx
        wet_slab = (nm == wet)
        if has_slab or wet_slab:
            y_slab = beam_y - bw_h - 5
            cv.create_rectangle(cx - bw_box * 0.34, y_slab - sl, cx + bw_box * 0.34, y_slab,
                                fill=("#e8c25a" if has_slab else "#f6e6b8"),
                                outline=("#c9a227" if has_slab else "#c9a227"),
                                dash=() if has_slab else (2, 2))
            struct_top = y_slab - sl
        else:
            struct_top = beam_y - bw_h
        # 荷载箭头（画在结构上方，箭头朝下）
        arrows = []
        if i == 0:
            arrows.append(("q1=%.1f" % r["q1"], "#2c3e50"))
        if nm == wet:
            arrows.append(("q2=%.1f" % r["q2"], "#b9770e"))
        if i == len(stages) - 1 and r["q3"] > 0:
            arrows.append(("q3=%.1f" % r["q3"], "#c0392b"))
        ax = cx - (len(arrows) - 1) * 18
        for label, color in arrows:
            cv.create_line(ax, struct_top - 26, ax, struct_top - 3, arrow="last",
                           fill=color, width=1.8)
            cv.create_text(ax, struct_top - 34, text=label, fill=color, anchor="s")
            ax += 36
        # 承担截面说明
        if has_slab:
            note, color = "组合截面受力", "#2d7d46"
        elif i == 0:
            note, color = "仅预制主梁", "#7f8c8d"
        else:
            note, color = "仅预制主梁（湿重）", "#b9770e"
        cv.create_text(cx, top + bh_box - 24, text=note, fill=color)
        # 跨中弯矩（逐步累加）
        if i == 0:
            mtxt = "ΣM = %.0f" % r["M1"]
        elif i < comp_idx or i == comp_idx:
            mtxt = "ΣM = %.0f" % r["M_pre"]
        else:
            mtxt = "ΣM = %.0f" % r["M_total"]
        cv.create_text(cx, top + bh_box - 8, text=mtxt, fill="#444444")
    cv.create_text(w / 2.0, h - 8, anchor="s", fill="#666666",
                   text="CS1 架主梁（自重）→ CS2 浇板（湿重）→ %s 板硬化、联合截面生效（不加减荷载）"
                        "→ 二期恒载" % (comp or "CS3"))


# ======================================================================
# 流程
# ======================================================================
class Flow(FlowBase):
    code = tb.CODE
    label = tb.LABEL
    step_ids = list(tb.STEP_IDS)
    step_titles = list(tb.STEP_TITLES)
    step_hints = [
        "填跨径和 T 形截面尺寸（梁肋 0.40×1.00 m、桥面板 1.60×0.25 m 是习题 4 的默认值），"
        "右边会实时画出截面并给出面积/形心/惯矩；改数字后可以直接看检查项。",
        "阶段表默认按「架设主梁 → 现浇桥面板（湿重）→ 板硬化龄期 20 d（联合截面生效）→ 二期恒载」排好，"
        "名字和天数都能改；湿重加在联合截面生效阶段的前一个阶段。",
        "这里是纯函数算出来的结果：截面特性、各阶段跨中内力、联合截面内力分配、跨中应力、"
        "与一次成型对比。拿它和题解 2.4/2.5/2.6 的表逐项对。",
        "生成 MCT（含 *STAGE + *CPOSECT4CS 施工阶段联合截面）+ 数据表 + 计算结果 + 数据 json；"
        "MIDAS 里用「文件 → 导入 → MCT 命令文件」导入 <名字>.mct。",
    ]
    last_hint = "文件已准备好，可以复制/预览 MCT；MIDAS 里用「文件 → 导入 → MCT 命令文件」导入。"

    # ---------------- 弹窗（统一走本模块的 messagebox，测试可以整体替身） ----------------
    def warn(self, title, msg):
        messagebox.showwarning(title, msg)

    def info(self, title, msg):
        messagebox.showinfo(title, msg)

    def ask(self, title, msg):
        return bool(messagebox.askyesno(title, msg))

    # ---------------- 数据 ----------------
    def default_model(self):
        return tb.default_model()

    def reset_model(self):
        return tb.reset_model()

    def issues(self, sid):
        return tb.issues(self.m, sid)

    def data_report(self):
        return tb.data_report(self.m)

    def clear_title(self, sid):
        return {"tbeam_geom": "跨径与截面", "tbeam_stages": "施工阶段"}.get(sid, "")

    def clear(self, sid):
        d = tb.default_model()
        m = self.m
        if sid == "tbeam_geom":
            for k in ("geom", "project"):
                m[k] = dict(d[k])
        elif sid == "tbeam_stages":
            m["stages"] = [dict(x) for x in d["stages"]]
            for k in ("comp_stage", "slab_age"):
                m[k] = d[k]

    def export(self):
        """生成 4 个成果文件（外壳不强制调用，导出按钮也会走这里）。"""
        got = self._prepare()
        if not got:
            return []
        fname, out = got
        return self._write_all(fname, out)

    # ==================================================================
    # 第 1 步：跨径与截面
    # ==================================================================
    def step_tbeam_geom(self):
        m = self.m
        g = m.setdefault("geom", {})
        for k, v in tb.default_model()["geom"].items():
            g.setdefault(k, v)

        # ---- 跨径 / 单元数 ----
        box = self.card("跨径与单元数")
        line = ttk.Frame(box)
        line.pack(fill="x", anchor="w")
        ttk.Label(line, text="计算跨径 L (m)").grid(row=0, column=0, sticky="w", padx=(0, 4), pady=3)
        eL = ttk.Entry(line, textvariable=self.svar(g, "L", g.get("L")), width=8)
        eL.grid(row=0, column=1, sticky="w", padx=(0, 22))
        ttk.Label(line, text="单元数").grid(row=0, column=2, sticky="w", padx=(0, 4))
        eN = ttk.Entry(line, textvariable=self.svar(g, "n", g.get("n")), width=6)
        eN.grid(row=0, column=3, sticky="w", padx=(0, 22))
        ttk.Label(line, text="（简支梁，节点 %d 个；20 m 算例建议 20 个单元）" % (int(tb._f(g.get("n"), 20)) + 1),
                  style="Hint.TLabel").grid(row=0, column=4, sticky="w")

        # ---- 截面尺寸 ----
        box2 = self.card("T 形截面尺寸（单位 m）")
        grid = ttk.Frame(box2)
        grid.pack(fill="x", anchor="w")
        fields = [("梁肋宽 bw", "bw", 7), ("梁肋高 hw", "hw", 7),
                  ("桥面板宽 bs", "bs", 7), ("桥面板厚 ts", "ts", 7)]
        entries = [eL, eN]
        for i, (lab, key, wid) in enumerate(fields):
            ttk.Label(grid, text=lab).grid(row=0, column=2 * i, sticky="w", padx=(0, 4), pady=3)
            e = ttk.Entry(grid, textvariable=self.svar(g, key, g.get(key)), width=wid)
            e.grid(row=0, column=2 * i + 1, sticky="w", padx=(0, 18), pady=3)
            entries.append(e)
        ttk.Label(grid, text="（默认 = 习题 4：梁肋 0.40×1.00、桥面板 1.60×0.25；"
                             "板宽不能小于梁肋宽）",
                  style="Hint.TLabel").grid(row=1, column=0, columnspan=10, sticky="w")

        # ---- 材料 / 容重 / 二期恒载 ----
        box3 = self.card("材料与荷载")
        line3 = ttk.Frame(box3)
        line3.pack(fill="x", anchor="w")
        v_conc = tk.StringVar(value=tb.model_conc(m))
        ttk.Label(line3, text="混凝土等级").grid(row=0, column=0, sticky="w", padx=(0, 4), pady=3)
        cb = ttk.Combobox(line3, textvariable=v_conc, values=tb.CONC_LIST, width=7,
                          state="readonly")
        cb.grid(row=0, column=1, sticky="w", padx=(0, 20), pady=3)
        ttk.Label(line3, text="容重 γ (kN/m³)").grid(row=0, column=2, sticky="w", padx=(0, 4))
        eG = ttk.Entry(line3, textvariable=self.svar(g, "gamma", g.get("gamma")), width=7)
        eG.grid(row=0, column=3, sticky="w", padx=(0, 20))
        ttk.Label(line3, text="二期恒载 q3 (kN/m)").grid(row=0, column=4, sticky="w", padx=(0, 4))
        eQ3 = ttk.Entry(line3, textvariable=self.svar(g, "q3", g.get("q3")), width=7)
        eQ3.grid(row=0, column=5, sticky="w", padx=(0, 20))
        entries.append(eG)
        entries.append(eQ3)

        def on_conc(*_e):
            g["conc"] = v_conc.get()
            refresh()

        cb.bind("<<ComboboxSelected>>", on_conc)

        # ---- 截面预览 + 特性 ----
        prev = self.card("截面预览与特性（改数字自动刷新）")
        cv = tk.Canvas(prev, height=250, bg="white", highlightthickness=1,
                       highlightbackground="#cccccc")
        cv.pack(fill="x")
        info = ttk.Label(prev, text="", style="Ok.TLabel", justify="left")
        info.pack(anchor="w", pady=(8, 0))

        def refresh(*_e):
            try:
                gg = tb.geom(m)
                _draw_section(cv, m)
                r = tb.results(m)
                info.configure(text=(
                    "预制主梁：A1 = %.4f m2   形心距梁底 %.4f m   I1 = %.6f m4\n"
                    "现浇桥面板：A2 = %.4f m2   形心距梁底 %.4f m   I2 = %.6f m4\n"
                    "组合截面：A = %.4f m2   形心距梁底 yb = %.4f m   I = %.6f m4   "
                    "联合后分担 主梁 %.2f %% / 板 %.2f %%\n"
                    "荷载：主梁自重 q1 = %.2f kN/m、现浇板湿重 q2 = %.2f kN/m、二期 q3 = %.2f kN/m；"
                    "跨中总 M = %.1f kN·m"
                    % (gg["A1"], gg["y1"], gg["I1"], gg["A2"], gg["y2"], gg["I2"],
                       gg["A"], gg["yb"], gg["I"], gg["k1"] * 100.0, gg["k2"] * 100.0,
                       r["q1"], r["q2"], r["q3"], r["M_total"])))
            except Exception:                              # noqa: BLE001
                info.configure(text="参数还不完整")

        cv.bind("<Configure>", refresh)
        for e in entries:
            e.bind("<KeyRelease>", refresh)
            e.bind("<FocusOut>", refresh)
        refresh()
        ttk.Label(prev, text="灰色 = 预制主梁梁肋（Part 1，CS1 起参与受力）；黄色 = 现浇桥面板"
                             "（Part 2，CS3 龄期 20 d 起参与受力）。红色虚线的位置就是单元线。",
                  style="Hint.TLabel", wraplength=880, justify="left").pack(anchor="w", pady=(4, 0))

    # ==================================================================
    # 第 2 步：施工阶段
    # ==================================================================
    def step_tbeam_stages(self):
        m = self.m
        stages = m.setdefault("stages", [])
        if not stages:
            stages.extend(tb.default_model()["stages"])
        for i, d in enumerate(tb.default_model()["stages"]):
            if i < len(stages):
                for k, v in d.items():
                    stages[i].setdefault(k, v)

        box = self.card("施工阶段表（默认：架设主梁 → 浇板（湿重）→ 板硬化 → 二期恒载）")
        g = self.table(box, ["序号", "阶段名", "持续天数 (d)", "这一步做什么", ""])
        for i, st in enumerate(stages):
            ttk.Label(g, text=str(i + 1)).grid(row=i + 1, column=0, sticky="w",
                                               padx=(0, 12), pady=3)
            self.entry(g, self.svar(st, "name", st.get("name")), width=10, row=i + 1, col=1)
            self.entry(g, self.svar(st, "days", st.get("days")), width=8, row=i + 1, col=2)
            self.entry(g, self.svar(st, "desc", st.get("desc")), width=44, row=i + 1, col=3)
            ttk.Button(g, text="删除", width=6,
                       command=lambda k=i: self._del_stage(k)).grid(row=i + 1, column=4,
                                                                    sticky="w", pady=3)
        ttk.Button(box, text="＋ 增加一个阶段", command=self._add_stage).pack(anchor="w", pady=(8, 0))

        names = tb.stage_names(m)

        # ---- 联合截面生效阶段 + 材龄 ----
        box2 = self.card("桥面板什么时候开始参与受力（联合截面生效）")
        g2 = ttk.Frame(box2)
        g2.pack(fill="x", anchor="w")
        cur = str(m.get("comp_stage") or "").strip()
        if cur not in names:
            cur = names[2] if len(names) > 2 else (names[-1] if names else "")
            m["comp_stage"] = cur
        v_comp = tk.StringVar(value=cur)
        ttk.Label(g2, text="桥面板参与受力阶段").grid(row=0, column=0, sticky="w", padx=(0, 4), pady=3)
        cb_comp = ttk.Combobox(g2, textvariable=v_comp, values=names, width=10, state="readonly")
        cb_comp.grid(row=0, column=1, sticky="w", padx=(0, 22), pady=3)
        ttk.Label(g2, text="桥面板材龄 (d)").grid(row=0, column=2, sticky="w", padx=(0, 4))
        self.entry(g2, self.svar(m, "slab_age", m.get("slab_age")), width=8, row=0, col=3)
        wet = tb.wet_stage_of(m)
        ttk.Label(box2, text="（湿重自动加在生效阶段的前一个阶段，现在加在「%s」；"
                             "材龄 = *CPOSECT4CS 里 Part 2 的 AGE，本题 20 d）" % (wet or "—"),
                  style="Hint.TLabel", wraplength=880, justify="left").pack(anchor="w", pady=(6, 0))

        def on_comp(*_e):
            m["comp_stage"] = v_comp.get()
            self.rerender()

        cb_comp.bind("<<ComboboxSelected>>", on_comp)

        # ---- 阶段计划说明 ----
        box3 = self.card("阶段计划（自动生成，核对一下再进下一步）")
        txt = tk.Text(box3, height=6, wrap="none", font=("Consolas", 9))
        try:
            plan = "\n".join(tb.stage_plan_lines(m))
        except Exception:                                  # noqa: BLE001
            plan = "（参数还不完整）"
        txt.insert("1.0", plan)
        txt.configure(state="disabled")
        txt.pack(fill="x")
        ttk.Label(box3, text="说明：湿重 q2 = 桥面板面积 × 容重，加在「浇板」阶段、由预制主梁承担；"
                             "板硬化阶段（材龄 20 d）只在 *CPOSECT4CS 里让 Part 2 开始参与刚度，"
                             "湿重不卸除（它就是板的自重）。",
                  style="Hint.TLabel", wraplength=880, justify="left").pack(anchor="w", pady=(6, 0))

        # ---- 阶段示意图 ----
        prev = self.card("施工阶段示意")
        cv = tk.Canvas(prev, height=210, bg="white", highlightthickness=1,
                       highlightbackground="#cccccc")
        cv.pack(fill="x")
        cv.bind("<Configure>", lambda _e: _draw_stages(cv, m))
        _draw_stages(cv, m)

    def _add_stage(self):
        stages = self.m.setdefault("stages", [])
        n = len(stages) + 1
        name = "CS%d" % n
        while any(str(s.get("name") or "") == name for s in stages):
            n += 1
            name = "CS%d" % n
        stages.append({"name": name, "days": "7", "desc": ""})
        self.save()
        self.rerender()

    def _del_stage(self, idx):
        stages = self.m.get("stages") or []
        if len(stages) <= 3:
            self.warn("删不了", "至少要保留 3 个施工阶段（架设主梁 → 浇板 → 板硬化 → 二期）。")
            return
        old = str(stages[idx].get("name") or "")
        if not self.ask("删除阶段", "确定删除第 %d 个阶段「%s」吗？" % (idx + 1, old)):
            return
        stages.pop(idx)
        names = tb.stage_names(self.m)
        if str(self.m.get("comp_stage") or "").strip() not in names:
            self.m["comp_stage"] = names[2] if len(names) > 2 else names[-1]
        self.save()
        self.rerender()

    # ==================================================================
    # 第 3 步：结果预览
    # ==================================================================
    def step_tbeam_check(self):
        m = self.m
        try:
            r = tb.results(m)
        except Exception as exc:                            # noqa: BLE001
            self.card("结果预览")
            ttk.Label(self.form, text="参数还不完整，算不出来：%s" % exc,
                      style="Err.TLabel").pack(anchor="w")
            return

        box = self.card("汇总")
        ttk.Label(box, text=tb.summary(m), style="Ok.TLabel",
                  wraplength=940, justify="left").pack(anchor="w")
        ttk.Label(box, text="（题解：跨中总 M = 2000.0 kN·m；主梁 1637.6、桥面板 362.4；"
                            "主梁底 +22.16、主梁顶/板底 -16.65、板顶 -3.85 MPa）",
                  style="Hint.TLabel", wraplength=940, justify="left").pack(anchor="w", pady=(4, 0))

        prev = self.card("截面与施工阶段示意")
        cv = tk.Canvas(prev, height=230, bg="white", highlightthickness=1,
                       highlightbackground="#cccccc")
        cv.pack(fill="x")
        cv.bind("<Configure>", lambda _e: _draw_section(cv, m))
        _draw_section(cv, m)
        cv2 = tk.Canvas(prev, height=200, bg="white", highlightthickness=1,
                        highlightbackground="#cccccc")
        cv2.pack(fill="x", pady=(8, 0))
        cv2.bind("<Configure>", lambda _e: _draw_stages(cv2, m))
        _draw_stages(cv2, m)

        box2 = self.card("计算结果（只读；可滚轮翻页）")
        holder = ttk.Frame(box2)
        holder.pack(fill="both", expand=True)
        txt = tk.Text(holder, height=20, wrap="none", font=("Consolas", 9))
        sb_y = ttk.Scrollbar(holder, orient="vertical", command=txt.yview)
        sb_x = ttk.Scrollbar(holder, orient="horizontal", command=txt.xview)
        txt.configure(yscrollcommand=sb_y.set, xscrollcommand=sb_x.set)
        sb_y.pack(side="right", fill="y")
        sb_x.pack(side="bottom", fill="x")
        txt.pack(side="left", fill="both", expand=True)
        txt.insert("1.0", _check_text(m))
        txt.configure(state="disabled")
        ttk.Label(box2, text="这张表就是纯函数算出来的全部结果；生成成果时会把同样内容写成 "
                             "_计算结果.txt。",
                  style="Hint.TLabel", wraplength=860, justify="left").pack(anchor="w", pady=(6, 0))

    # ==================================================================
    # 第 4 步：生成成果
    # ==================================================================
    def step_tbeam_export(self):
        m = self.m
        prj = m.setdefault("project", {})
        if not hasattr(self, "_v_outdir"):
            self._v_outdir = tk.StringVar(value=prj.get("outdir") or self.out_dir())
        if not hasattr(self, "_v_fname"):
            self._v_fname = tk.StringVar(value=prj.get("name") or "预制T梁_20m_施工阶段")

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
        ttk.Entry(line1, textvariable=self._v_fname, width=30).pack(side="left", padx=(6, 14))
        ttk.Label(line1, text="（生成 <名字>.mct、<名字>_数据表.txt、<名字>_计算结果.txt、"
                              "<名字>_数据.json）", style="Hint.TLabel").pack(side="left")

        line2 = ttk.Frame(box)
        line2.pack(fill="x", pady=(10, 0))
        ttk.Button(line2, text="① 生成全部成果",
                   command=self._gen_all).pack(side="left")
        ttk.Button(line2, text="② 只生成 MCT",
                   command=self._gen_main).pack(side="left", padx=(8, 0))
        ttk.Button(line2, text="③ 预览 MCT",
                   command=self._preview).pack(side="left", padx=(8, 0))
        ttk.Button(line2, text="④ 复制 MCT 文本",
                   command=self._copy).pack(side="left", padx=(8, 0))

        ttk.Label(box, text="MIDAS Civil NX 里：文件 → 导入 → MCT 命令文件，选 <名字>.mct。\n"
                            "模型是 20 m 简支梁、20 个单元；*STAGE 4 个阶段、*CPOSECT4CS 2 个部件；"
                            "导入前先「另存为」，否则 MIDAS 会弹保存对话框。",
                  style="Hint.TLabel", wraplength=880, justify="left").pack(anchor="w", pady=(8, 0))

        box2 = self.card("日志 / 预览")
        self.log = tk.Text(box2, height=16, wrap="none", font=("Consolas", 9))
        sb = ttk.Scrollbar(box2, orient="vertical", command=self.log.yview)
        self.log.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.log.pack(side="left", fill="both", expand=True)
        self.log.insert("1.0", "（先点 ① 生成成果，或点 ③ 预览 MCT 文本。）")
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
        probs = tb.all_issues(self.m)
        if probs:
            self.warn("先改完这些再生成",
                      "下面这些问题会让 MIDAS 导入报错，先改好（点左侧步骤名可直接跳过去）：\n\n"
                      + "\n".join("· " + p for p in probs[:10]))
            return None
        fname = _safe_name(self._v_fname.get() or self.m["project"].get("name"))
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
        try:
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(self.m, fh, ensure_ascii=False, indent=2)
        except (OSError, TypeError) as exc:
            self.warn("写文件失败", "%s\n\n%s" % (path, exc))
            return False
        return True

    def _write_all(self, fname, out):
        """生成 4 个文件：MCT + 数据表 + 计算结果 + 数据 json。"""
        files = []
        try:
            os.makedirs(out, exist_ok=True)
        except OSError as exc:
            self.warn("文件夹建不了", str(exc))
            return []
        try:
            mct = tb.build_mct(self.m)
            rep = tb.data_report(self.m)
            res = tb.result_report(self.m)
        except Exception as exc:                           # noqa: BLE001
            self.warn("生成失败", "MCT / 数据表生成出错（参数里可能有异常值）：\n%s" % exc)
            return []
        p1 = os.path.join(out, fname + ".mct")
        p2 = os.path.join(out, fname + "_数据表.txt")
        p3 = os.path.join(out, fname + "_计算结果.txt")
        p4 = os.path.join(out, fname + "_数据.json")
        if self._write_text(p1, mct):
            files.append(("施工阶段 MCT", p1))
        if self._write_text(p2, rep):
            files.append(("数据表", p2))
        if self._write_text(p3, res):
            files.append(("计算结果", p3))
        if self._write_json(p4):
            files.append(("向导数据（json）", p4))
        self.save()
        return files

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
        r = tb.results(self.m)
        self._log("   跨中 M = %.1f kN·m（主梁 %.1f / 桥面板 %.1f）；主梁底 %+.2f MPa"
                  % (r["M_total"], r["M_beam"], r["M_slab"], r["stress"]["total"]["beam_bot"]))
        self._log("   MCT：%d 行，*STAGE %d 个阶段、*CPOSECT4CS 2 个部件（每行 37 字段）"
                  % (tb.build_mct(self.m).count("\n") + 1,
                     len(self.m.get("stages") or [])))
        self.info("已生成", "已生成 %d 个文件：\n%s\n\n接着：MIDAS → 文件 → 导入 → MCT 命令文件。"
                  % (len(files), "\n".join("· %s" % p for _l, p in files)))

    def _gen_main(self):
        got = self._prepare()
        if not got:
            return
        fname, out = got
        try:
            mct = tb.build_mct(self.m)
        except Exception as exc:                           # noqa: BLE001
            self.warn("生成失败", "MCT 生成出错：\n%s" % exc)
            return
        p = os.path.join(out, fname + ".mct")
        if not self._write_text(p, mct):
            return
        self._log("√ 已生成 MCT：%s（%d 行）" % (p, mct.count("\n") + 1))
        self.info("已生成", "MCT 已生成：\n%s" % p)

    def _preview(self):
        try:
            mct = tb.build_mct(self.m)
        except Exception as exc:                           # noqa: BLE001
            self.warn("预览失败", str(exc))
            return
        try:
            self.log.configure(state="normal")
            self.log.delete("1.0", "end")
            self.log.insert("1.0", mct)
            self.log.configure(state="disabled")
        except (AttributeError, tk.TclError):
            return
        self._log("\n—— 上面是 MCT 全文（%d 行）；改参数后重新点「预览」会刷新。——"
                  % (mct.count("\n") + 1))

    def _copy(self):
        probs = tb.all_issues(self.m)
        if probs:
            self.warn("先改完这些", "\n".join(probs[:8]))
            return
        try:
            mct = tb.build_mct(self.m)
        except Exception as exc:                           # noqa: BLE001
            self.warn("生成失败", str(exc))
            return
        try:
            self.wiz.clipboard_clear()
            self.wiz.clipboard_append(mct)
        except tk.TclError:
            return
        self._log("√ MCT 全文已复制到剪贴板（可粘到 MIDAS 的 MCT 命令窗口运行）")


# ======================================================================
# 第 3 步的只读结果文本
# ======================================================================
def _check_text(model):
    """第 3 步 Text 里显示的文本（= 计算结果表的精简版）。"""
    try:
        return tb.result_report(model)
    except Exception as exc:                                # noqa: BLE001
        return "算不出来：%s" % exc
