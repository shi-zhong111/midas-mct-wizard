# -*- coding: utf-8 -*-
"""平面刚架 · 自由建模 —— 界面流程插件（flow_frame.py）。

结构：
    * 纯计算 / MCT 生成都在 frame_core.py（不依赖 tkinter，测试可以直接调）；
    * 本文件只做「一步一步填数值」的界面，按 flow_api.FlowBase 实现 4 步：
          1. frame_geom    几何：节点表（x, y）+ 杆件表（i, j, 截面号, i/j 端铰）
          2. frame_section 截面与材料：sect_ui.SectionEditor + 材料 E / 容重 / 泊松比
          3. frame_loads   荷载与支承：支承（u/v/θ）+ 节点荷载（Fx/Fy/Mz）
                            + 杆件分布荷载（整体 X/Y，可线性变化）
          4. frame_export  生成成果：.mct + 数据表 + 求解结果 + json（内置矩阵位移法）
    第 3 步下方直接显示「矩阵位移法结果」预览（杆端力表），第 4 步写文件。

主程序 midas_wizard.py 的 FLOW_PLUGIN_MODULES 里需要登记 ("frame", "flow_frame")，
本文件放到程序目录就会出现「平面刚架（自由建模·矩阵位移法）」这个流程。

理论/约定见 frame_core.py 文件头（局部坐标、杆端力正负号、矩阵位移法、铰接释放）。
"""

import os
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import frame_core as fc
from flow_api import FlowBase

# 模块级也把引擎的函数暴露出来（SPEC 约定：测试可以 import flow_frame 直接调）
CODE = fc.CODE
LABEL = fc.LABEL
default_model = fc.default_model
reset_model = fc.reset_model
blank_model = fc.blank_model
preset_8a = fc.preset_8a
preset_8b = fc.preset_8b
issues = fc.issues
all_issues = fc.all_issues
data_report = fc.data_report
result_report = fc.result_report
build_mct = fc.build_mct
solve = fc.solve
write_outputs = fc.write_outputs
mct_stats = fc.mct_stats

_DIR_NAMES = fc._DIR_NAMES
_DIR_BACK = fc._DIR_BACK


# ======================================================================
# 画图：平面刚架示意（节点、杆件、铰、支承、荷载）
# ======================================================================
def _draw_frame(cv, model):
    """在 Canvas 上画刚架简图（改数字自动刷新）。"""
    cv.delete("all")
    w = max(cv.winfo_width(), 560)
    h = int(cv.winfo_height())
    if h < 60:                              # 窗口还没排好版时按「要求高度」画
        h = int(cv.winfo_reqheight()) or 260
    try:
        nodes = fc.read_nodes(model)
    except Exception:                                          # noqa: BLE001
        nodes = []
    xy = {n["no"]: (n["x"], n["y"]) for n in nodes}
    try:
        elems = [e for e in fc.read_elems(model)
                 if e["ni"] in xy and e["nj"] in xy and e["ni"] != e["nj"]]
    except Exception:                                          # noqa: BLE001
        elems = []
    if not nodes or not elems:
        cv.create_text(w / 2.0, h / 2.0, text="（先填节点表和杆件表，这里会画出刚架简图）",
                       fill="#999999")
        return
    xs = [p[0] for p in xy.values()]
    ys = [p[1] for p in xy.values()]
    x0, x1 = min(xs), max(xs)
    y0, y1 = min(ys), max(ys)
    pad = 44
    sx = (w - 2 * pad) / ((x1 - x0) or 1.0)
    sy = (h - 2 * pad) / ((y1 - y0) or 1.0)
    s = min(sx, sy)
    ox = pad + (w - 2 * pad - (x1 - x0) * s) / 2.0
    oy = pad + (h - 2 * pad - (y1 - y0) * s) / 2.0

    def P(x, y):
        return (ox + (x - x0) * s, h - oy - (y - y0) * s)

    # 杆件
    for e in elems:
        a, b = P(*xy[e["ni"]]), P(*xy[e["nj"]])
        cv.create_line(*a, *b, fill="#2d6ca3", width=3)
        # 铰：释放端画个空心小圆
        mx, my = (a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0
        if e["ri"]:
            px, py = (a[0] * 0.72 + b[0] * 0.28), (a[1] * 0.72 + b[1] * 0.28)
            cv.create_oval(px - 4, py - 4, px + 4, py + 4, fill="white", outline="#c0392b", width=2)
        if e["rj"]:
            px, py = (a[0] * 0.28 + b[0] * 0.72), (a[1] * 0.28 + b[1] * 0.72)
            cv.create_oval(px - 4, py - 4, px + 4, py + 4, fill="white", outline="#c0392b", width=2)
        # 分布荷载：沿杆件画一排小箭头
        for ld in (model.get("dloads") or []):
            try:
                same = int(float(str(ld.get("elem")).strip())) == e["no"]
            except (TypeError, ValueError):
                same = False
            if not same:
                continue
            q1 = fc._f(ld.get("q1"))
            q2 = fc._f(ld.get("q2"))
            if abs(q1) < 1e-12 and abs(q2) < 1e-12:
                continue
            d = str(ld.get("dir") or "y")
            dx, dy = (1.0, 0.0) if d == "x" else (0.0, -1.0)
            for k in range(1, 5):
                t = k / 5.0
                px = a[0] + (b[0] - a[0]) * t
                py = a[1] + (b[1] - a[1]) * t
                cv.create_line(px + dx * 26, py + dy * 26, px, py,
                               arrow="last", fill="#c0392b")
    # 节点
    for n in nodes:
        px, py = P(n["x"], n["y"])
        cv.create_oval(px - 5, py - 5, px + 5, py + 5, fill="#ffffff", outline="#22415c", width=1.6)
        cv.create_text(px + 9, py - 10, text="%d" % n["no"], anchor="w", fill="#22415c")
    # 支承
    for sp in (model.get("supports") or []):
        no = fc._int(sp.get("node"))
        if no not in xy:
            continue
        px, py = P(*xy[no])
        u, v, r = bool(sp.get("u")), bool(sp.get("v")), bool(sp.get("r"))
        if v or r:
            # 注意：Tk 里 fill="none" 不是合法颜色名，不填充要用空字符串 ""
            cv.create_polygon(px - 9, py + 9, px + 9, py + 9, px, py + 1,
                              fill="", outline="#c0392b", width=2)
        if u and not v:
            cv.create_line(px, py, px, py + 9, fill="#c0392b", width=2)
            cv.create_line(px - 9, py + 9, px + 9, py + 9, fill="#c0392b", width=2)
        if not (u or v or r):
            cv.create_text(px, py + 16, text="没约束!", fill="#c0392b")
    # 节点荷载
    for ld in (model.get("nloads") or []):
        no = fc._int(ld.get("node"))
        if no not in xy:
            continue
        fx = fc._f(ld.get("fx"))
        fy = fc._f(ld.get("fy"))
        px, py = P(*xy[no])
        if abs(fx) + abs(fy) > 1e-12:
            ln = max(abs(fx), abs(fy))
            ux, uy = fx / ln, -fy / ln
            cv.create_line(px - ux * 34, py - uy * 34, px - ux * 8, py - uy * 8,
                           arrow="last", fill="#2d7d46", width=2)
        mz = fc._f(ld.get("mz"))
        if abs(mz) > 1e-12:
            cv.create_arc(px - 13, py - 13, px + 13, py + 13, start=20, extent=290,
                          style="arc", outline="#2d7d46", width=2)
    cv.create_text(8, 8, anchor="nw", fill="#666666",
                   text="蓝线=杆件；红=荷载/支承；空心小圆=铰（杆端释放）；数字=节点编号")


# ======================================================================
# 流程
# ======================================================================
class Flow(FlowBase):
    code = fc.CODE
    label = fc.LABEL
    step_ids = ["frame_geom", "frame_section", "frame_loads", "frame_export"]
    step_titles = ["几何（节点 / 杆件）", "截面与材料", "荷载与支承", "生成成果"]
    step_hints = [
        "按自己的模型填节点表（编号、x、y，单位 m）和杆件表（编号、i 端、j 端、截面号）。"
        "「i 端铰 / j 端铰」勾上就是该端释放弯矩（MCT 里写 *FRAME-RLS）。"
        "懒得建可以先点「习题8(a)」或「习题8(b)」预设，再动手改。",
        "先填材料：弹性模量 E（kN/m^2 = kPa，C40 取 3.25e7）、容重、泊松比；"
        "再在下面加截面（默认给了一个 0.4x0.6 的矩形）。杆端力与 EI 的绝对值无关，"
        "截面尺寸只影响位移，不影响内力分配。",
        "支承按节点勾 u / v / θ（至少约束住结构，否则是机构）；节点荷载填 Fx / Fy / Mz；"
        "杆件分布荷载可选「整体X / 整体Y」并给 i 端、j 端两个强度（相等=均布，不等=线性变化）。"
        "下面的「矩阵位移法结果」会跟着自动重算。",
        "点「生成全部成果」写 4 个文件：MCT 命令文件、数据表、求解结果（杆端力+支反力+平衡校核）、"
        "以及可再打开的 json。MIDAS Civil NX 里用「文件 → 导入 → MCT 命令文件」。",
    ]
    last_hint = "文件已准备好：.mct 拿去 MIDAS Civil NX 导入；求解结果里有杆端力表和支反力，可直接对作业答案。"

    # ---------------- 弹窗（统一走本模块的 messagebox，测试可以整体替身） ----------------
    def warn(self, title, msg):
        messagebox.showwarning(title, msg)

    def info(self, title, msg):
        messagebox.showinfo(title, msg)

    def ask(self, title, msg):
        return bool(messagebox.askyesno(title, msg))

    # ---------------- 数据 ----------------
    def default_model(self):
        return fc.default_model()

    def reset_model(self):
        return fc.reset_model()

    def issues(self, sid):
        return fc.issues(self.m, sid)

    def data_report(self):
        return fc.data_report(self.m)

    def clear_title(self, sid):
        return {"frame_geom": "几何（节点 / 杆件）", "frame_section": "截面与材料",
                "frame_loads": "荷载与支承"}.get(sid, "")

    def clear(self, sid):
        m = self.m
        if sid == "frame_geom":
            m["nodes"] = [{"no": "1", "x": "0", "y": "0"}]
            m["elems"] = []
        elif sid == "frame_section":
            d = fc.blank_model()
            m["sections"] = [dict(d["sections"][0])]
            m["mat"] = dict(d["mat"])
        elif sid == "frame_loads":
            m["supports"] = []
            m["nloads"] = []
            m["dloads"] = []

    def export(self):
        """生成 4 个成果文件（外壳不强制调用，第 4 步的按钮也会走这里）。"""
        got = self._prepare()
        if not got:
            return []
        fname, out = got
        return self._write_all(fname, out)

    # ==================================================================
    # 第 1 步：几何（节点 / 杆件 / 预设）
    # ==================================================================
    def step_frame_geom(self):
        m = self.m
        nodes = m.setdefault("nodes", [])
        elems = m.setdefault("elems", [])

        box = self.card("节点表（单位 m；y 是竖向向上）")
        g = self.table(box, ["编号", "x (m)", "y (m)", "删除"])
        for i, r in enumerate(nodes):
            ttk.Label(g, text=str(i + 1)).grid(row=i + 1, column=0, sticky="w",
                                               padx=(0, 8), pady=3)
            self.entry(g, self.svar(r, "no", r.get("no")), width=6, row=i + 1, col=1)
            self.entry(g, self.svar(r, "x", r.get("x")), width=10, row=i + 1, col=2)
            self.entry(g, self.svar(r, "y", r.get("y")), width=10, row=i + 1, col=3)
            ttk.Button(g, text="删除", width=5,
                       command=lambda k=i: self._del_node(k)).grid(row=i + 1, column=4,
                                                                   sticky="w")
        ttk.Button(box, text="＋ 增加节点", command=self._add_node).pack(anchor="w", pady=(8, 0))

        box2 = self.card("杆件表（i 端 → j 端；「铰」= 该端释放弯矩）")
        g2 = self.table(box2, ["编号", "节点 i", "节点 j", "截面号", "备注", "i 端铰", "j 端铰", "删除"])
        for i, r in enumerate(elems):
            ttk.Label(g2, text=str(i + 1)).grid(row=i + 1, column=0, sticky="w",
                                                padx=(0, 8), pady=3)
            self.entry(g2, self.svar(r, "no", r.get("no")), width=6, row=i + 1, col=1)
            self.entry(g2, self.svar(r, "i", r.get("i")), width=6, row=i + 1, col=2)
            self.entry(g2, self.svar(r, "j", r.get("j")), width=6, row=i + 1, col=3)
            self.entry(g2, self.svar(r, "sec", r.get("sec", "1")), width=5, row=i + 1, col=4)
            self.entry(g2, self.svar(r, "label", r.get("label")), width=10, row=i + 1, col=5)
            tk.Checkbutton(g2, variable=self.bvar(r, "ri", bool(r.get("ri"))),
                           command=self.rerender).grid(row=i + 1, column=6, sticky="w")
            tk.Checkbutton(g2, variable=self.bvar(r, "rj", bool(r.get("rj"))),
                           command=self.rerender).grid(row=i + 1, column=7, sticky="w")
            ttk.Button(g2, text="删除", width=5,
                       command=lambda k=i: self._del_elem(k)).grid(row=i + 1, column=8,
                                                                   sticky="w")
        ttk.Button(box2, text="＋ 增加杆件", command=self._add_elem).pack(anchor="w", pady=(8, 0))
        ttk.Label(box2, text="「截面号」对应第 2 步里截面的顺序号（1、2、3…）；"
                             "默认只有 1 个截面，全填 1 就行。",
                  style="Hint.TLabel").pack(anchor="w", pady=(6, 0))

        # ---- 预设 ----
        box3 = self.card("预设（会覆盖现在填的几何 / 荷载，点之前会问一句）")
        bar = ttk.Frame(box3)
        bar.pack(fill="x", anchor="w")
        ttk.Button(bar, text="习题 8(a)（默认）",
                   command=lambda: self._apply_preset("8a")).pack(side="left")
        ttk.Button(bar, text="习题 8(b) 节点2铰接",
                   command=lambda: self._apply_preset("8b_h")).pack(side="left", padx=(8, 0))
        ttk.Button(bar, text="习题 8(b) 节点2刚接（对拍用）",
                   command=lambda: self._apply_preset("8b_r")).pack(side="left", padx=(8, 0))
        ttk.Button(bar, text="清空", command=self._clear_geom).pack(side="left", padx=(8, 0))
        ttk.Label(box3, text="习题 8(a)：1 固定、4 竖向滚轴；梁 10 kN/m 均布；左柱三角形 0→10 kN/m（整体X）；"
                             "节点 5 集中力 Fx=-100 kN。\n"
                             "习题 8(b)：5、4 固定；节点 6 Fy=-50 kN、节点 1 Mz=+30 kN·m；"
                             "铰接版把节点 2 处「梁右 j 端 + 柱右 i 端」释放弯矩。",
                  style="Hint.TLabel", justify="left").pack(anchor="w", pady=(8, 0))

        # ---- 简图 ----
        box4 = self.card("简图（改数字自动刷新）")
        cv = tk.Canvas(box4, height=250, bg="white", highlightthickness=1,
                       highlightbackground="#cccccc")
        cv.pack(fill="x")

        def refresh(*_e):
            # 画图出任何错都不能把整页渲染拖垮（Tk 颜色/坐标异常时退回一行提示）
            try:
                _draw_frame(cv, m)
            except Exception as exc:                           # noqa: BLE001
                try:
                    cv.delete("all")
                    cv.create_text(max(cv.winfo_width(), 400) / 2.0, 60,
                                   text="简图画不出来：%s" % exc, fill="#999999")
                except Exception:                              # noqa: BLE001
                    pass

        cv.bind("<Configure>", refresh)

        def bind_refresh(widget):
            widget.bind("<KeyRelease>", lambda _e: refresh())
            widget.bind("<FocusOut>", lambda _e: refresh())

        for child in g.winfo_children() + g2.winfo_children():
            if isinstance(child, ttk.Entry):
                bind_refresh(child)
        refresh()

    # ---------------- 增删行 ----------------
    def _next_no(self, rows, key="no"):
        used = set()
        for r in rows:
            v = fc._int(r.get(key))
            if v is not None:
                used.add(v)
        n = 1
        while n in used:
            n += 1
        return str(n)

    def _add_node(self):
        nodes = self.m.setdefault("nodes", [])
        last = nodes[-1] if nodes else {"x": "0", "y": "0"}
        nodes.append({"no": self._next_no(nodes),
                      "x": fc._fmt(fc._f(last.get("x")) + 5.0),
                      "y": fc._fmt(fc._f(last.get("y")))})
        self.save()
        self.rerender()

    def _del_node(self, idx):
        nodes = self.m.get("nodes") or []
        if not (0 <= idx < len(nodes)):
            return
        if len(nodes) <= 1:
            self.warn("删不了", "至少要保留 1 个节点。")
            return
        no = nodes[idx].get("no")
        if not self.ask("删除节点", "确定删除第 %d 行（节点 %s）吗？\n"
                                   "引用这个节点的杆件/支承/荷载也要一起改。" % (idx + 1, no)):
            return
        nodes.pop(idx)
        self.save()
        self.rerender()

    def _add_elem(self):
        elems = self.m.setdefault("elems", [])
        nodes = [n for n in (self.m.get("nodes") or []) if fc._int(n.get("no")) is not None]
        ni = nodes[0].get("no") if nodes else "1"
        nj = nodes[1].get("no") if len(nodes) > 1 else ni
        elems.append({"no": self._next_no(elems), "i": str(ni), "j": str(nj),
                      "sec": "1", "label": "", "ri": False, "rj": False})
        self.save()
        self.rerender()

    def _del_elem(self, idx):
        elems = self.m.get("elems") or []
        if not (0 <= idx < len(elems)):
            return
        no = elems[idx].get("no")
        if not self.ask("删除杆件", "确定删除第 %d 行（杆件 %s）吗？" % (idx + 1, no)):
            return
        elems.pop(idx)
        self.save()
        self.rerender()

    def _apply_preset(self, kind):
        if not self.ask("套用预设", "会用预设模型覆盖现在的全部输入（节点/杆件/支承/荷载），继续吗？"):
            return
        d = {"8a": fc.preset_8a(), "8b_h": fc.preset_8b(True),
             "8b_r": fc.preset_8b(False)}[kind]
        # 保留用户已经填好的截面/材料（预设里只是默认值）
        keep_sec = self.m.get("sections") or []
        keep_mat = self.m.get("mat") or {}
        self.m.clear()
        self.m.update(d)
        if keep_sec:
            self.m["sections"] = keep_sec
        if keep_mat:
            self.m["mat"] = keep_mat
        self.m["project"]["outdir"] = (self.m.get("project") or {}).get("outdir") or ""
        self.save()
        self.rerender()

    def _clear_geom(self):
        if not self.ask("清空几何", "确定清空节点表和杆件表吗？（截面/材料保留）"):
            return
        self.clear("frame_geom")
        self.save()
        self.rerender()

    # ==================================================================
    # 第 2 步：截面与材料
    # ==================================================================
    def step_frame_section(self):
        m = self.m
        mat = m.setdefault("mat", {})
        d = fc.default_model()["mat"]
        for k, v in d.items():
            mat.setdefault(k, v)

        box = self.card("材料（弹性模量 E 用 kN/m^2，也就是 kPa）")
        g = ttk.Frame(box)
        g.pack(fill="x", anchor="w")
        ttk.Label(g, text="弹性模量 E").grid(row=0, column=0, sticky="w", padx=(0, 4), pady=3)
        self.entry(g, self.svar(mat, "E", mat.get("E")), width=12, row=0, col=1)
        ttk.Label(g, text="kN/m^2").grid(row=0, column=2, sticky="w", padx=(0, 18))
        ttk.Label(g, text="容重 γ").grid(row=0, column=3, sticky="w", padx=(0, 4))
        self.entry(g, self.svar(mat, "gamma", mat.get("gamma")), width=8, row=0, col=4)
        ttk.Label(g, text="kN/m^3").grid(row=0, column=5, sticky="w", padx=(0, 18))
        ttk.Label(g, text="泊松比").grid(row=0, column=6, sticky="w", padx=(0, 4))
        self.entry(g, self.svar(mat, "nu", mat.get("nu")), width=8, row=0, col=7)
        ttk.Label(box, text="C40：E=3.25e7、γ=25、ν=0.2；Q345：E=2.06e8、γ=76.98、ν=0.3。"
                            "（本模型不含自重，容重只是写进 MCT 的材料参数。）",
                  style="Hint.TLabel").pack(anchor="w", pady=(6, 0))

        box2 = self.card("截面库（默认：0.4x0.6 实心矩形，C40 → EI = 234000 kN·m^2）")
        try:
            editor = __import__("sect_ui").SectionEditor(
                self, m, allow_cad=True, allow_taper=False, title="截面",
                note="高度 H 是截面高、B 是宽；内置求解器用 Iyy = B·H^3/12 作面内抗弯惯矩，"
                     "MCT 里写的是参数化截面，MIDAS 会自己算准确特性。")
            editor.on_change = self.rerender
            editor.render(box2)
        except Exception as exc:                               # noqa: BLE001
            ttk.Label(box2, text="截面编辑器加载失败：%s（可以直接在 json 里改 sections）" % exc,
                      style="Err.TLabel").pack(anchor="w")
            self.report_error("截面编辑器", exc)

        box3 = self.card("截面 → 各杆件的 EI（只用来算位移；杆端力与 EI 绝对值无关）")
        g3 = self.table(box3, ["截面号", "名称", "形状 / 尺寸", "A (m^2)", "Iyy (m^4)", "EI (kN·m^2)"])
        E = fc.material_E(m)
        for i, sec in enumerate(fc.read_sections(m), start=1):
            pr = fc.section_props(sec)
            ttk.Label(g3, text=str(i)).grid(row=i, column=0, sticky="w", padx=(0, 8), pady=2)
            ttk.Label(g3, text=str(sec.get("name") or "")).grid(row=i, column=1, sticky="w",
                                                                padx=(0, 8))
            ttk.Label(g3, text=fc.section_summary(sec)).grid(row=i, column=2, sticky="w",
                                                             padx=(0, 8))
            for k, v in enumerate((pr["A"], pr["Iyy"], E * pr["Iyy"]), start=3):
                ttk.Label(g3, text=fc._fmt(v, 6)).grid(row=i, column=k, sticky="w", padx=(0, 8))
        if not fc.read_sections(m):
            ttk.Label(g3, text="还没有截面 —— 点上面「＋ 添加参数化截面」加一个。",
                      style="Hint.TLabel").grid(row=1, column=0, columnspan=6, sticky="w", pady=4)
        ttk.Label(box3, text="举例：0.4x0.6 矩形、C40 → Iyy=0.0072 m^4、EI=234000 kN·m^2；"
                             "把 EI 整体放大或缩小，杆端力一个都不变（线性叠加的比例不变）。",
                  style="Hint.TLabel").pack(anchor="w", pady=(6, 0))

    # ==================================================================
    # 第 3 步：荷载与支承
    # ==================================================================
    def step_frame_loads(self):
        m = self.m
        sups = m.setdefault("supports", [])
        nls = m.setdefault("nloads", [])
        dls = m.setdefault("dloads", [])

        box = self.card("支承（按节点勾 u / v / θ；MCT 里会额外补平面外约束）")
        g = self.table(box, ["节点", "u（水平）", "v（竖向）", "θ（转动）", "删除"])
        for i, r in enumerate(sups):
            self.entry(g, self.svar(r, "node", r.get("node")), width=6, row=i + 1, col=0)
            tk.Checkbutton(g, variable=self.bvar(r, "u", bool(r.get("u")))).grid(
                row=i + 1, column=1, sticky="w")
            tk.Checkbutton(g, variable=self.bvar(r, "v", bool(r.get("v")))).grid(
                row=i + 1, column=2, sticky="w")
            tk.Checkbutton(g, variable=self.bvar(r, "r", bool(r.get("r")))).grid(
                row=i + 1, column=3, sticky="w")
            ttk.Button(g, text="删除", width=5,
                       command=lambda k=i: self._del_row("supports", k)).grid(
                row=i + 1, column=4, sticky="w")
        ttk.Button(box, text="＋ 增加支承", command=lambda: self._add_row(
            "supports", {"node": "1", "u": True, "v": True, "r": True})).pack(
            anchor="w", pady=(8, 0))
        ttk.Label(box, text="平面刚架：固定端勾 u+v+θ；竖向滚动（只约束竖向）只勾 v。"
                            "MCT 里程序会自动把平面外的 DY/RX/RZ 也约束住（2D 模型必需）。",
                  style="Hint.TLabel").pack(anchor="w", pady=(6, 0))

        box2 = self.card("节点荷载（Fx 水平、Fy 竖向向上为正、Mz 逆时针为正）")
        g2 = self.table(box2, ["节点", "Fx (kN)", "Fy (kN)", "Mz (kN·m)", "删除"])
        for i, r in enumerate(nls):
            self.entry(g2, self.svar(r, "node", r.get("node")), width=6, row=i + 1, col=0)
            self.entry(g2, self.svar(r, "fx", r.get("fx")), width=10, row=i + 1, col=1)
            self.entry(g2, self.svar(r, "fy", r.get("fy")), width=10, row=i + 1, col=2)
            self.entry(g2, self.svar(r, "mz", r.get("mz")), width=10, row=i + 1, col=3)
            ttk.Button(g2, text="删除", width=5,
                       command=lambda k=i: self._del_row("nloads", k)).grid(
                row=i + 1, column=4, sticky="w")
        ttk.Button(box2, text="＋ 增加节点荷载", command=lambda: self._add_row(
            "nloads", {"node": "1", "fx": "0", "fy": "0", "mz": "0"})).pack(
            anchor="w", pady=(8, 0))

        box3 = self.card("杆件分布荷载（整体坐标方向；i 端 / j 端强度不同 = 线性变化）")
        g3 = self.table(box3, ["杆件", "方向", "i 端强度 (kN/m)", "j 端强度 (kN/m)", "删除"])
        for i, r in enumerate(dls):
            self.entry(g3, self.svar(r, "elem", r.get("elem")), width=6, row=i + 1, col=0)
            v = tk.StringVar(value=_DIR_NAMES.get(str(r.get("dir") or "y"), "整体Y（竖直）"))

            def _w(*_a, _r=r, _v=v):
                _r["dir"] = _DIR_BACK.get(_v.get(), "y")

            v.trace_add("write", _w)
            cb = ttk.Combobox(g3, textvariable=v, values=list(_DIR_NAMES.values()),
                              width=16, state="readonly")
            cb.grid(row=i + 1, column=1, sticky="w", padx=(0, 12), pady=3)
            self.entry(g3, self.svar(r, "q1", r.get("q1")), width=10, row=i + 1, col=2)
            self.entry(g3, self.svar(r, "q2", r.get("q2")), width=10, row=i + 1, col=3)
            ttk.Button(g3, text="删除", width=5,
                       command=lambda k=i: self._del_row("dloads", k)).grid(
                row=i + 1, column=4, sticky="w")
        ttk.Button(box3, text="＋ 增加分布荷载", command=lambda: self._add_row(
            "dloads", {"elem": "1", "dir": "y", "q1": "0", "q2": "0"})).pack(
            anchor="w", pady=(8, 0))
        ttk.Label(box3, text="向下 10 kN/m → 方向「整体Y」、两端都填 -10；"
                             "三角形 0(底)→10(顶) 水平向右 → 方向「整体X」、i 端 0、j 端 10。",
                  style="Hint.TLabel").pack(anchor="w", pady=(6, 0))

        # ---- 矩阵位移法结果预览 ----
        box4 = self.card("矩阵位移法结果（改完自动重算；第 4 步会把它写进文件）")
        txt = tk.Text(box4, height=16, wrap="none", font=("Consolas", 9))
        sb = ttk.Scrollbar(box4, orient="vertical", command=txt.yview)
        txt.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        txt.pack(side="left", fill="both", expand=True)
        txt.insert("1.0", self._result_text())
        txt.configure(state="disabled")

    def _result_text(self):
        try:
            return fc.result_report(self.m)
        except Exception as exc:                               # noqa: BLE001
            return "算不了：%s\n\n（先把几何 / 截面 / 支承填完整）" % exc

    def _add_row(self, key, row):
        self.m.setdefault(key, []).append(dict(row))
        self.save()
        self.rerender()

    def _del_row(self, key, idx):
        rows = self.m.get(key) or []
        if not (0 <= idx < len(rows)):
            return
        rows.pop(idx)
        self.save()
        self.rerender()

    # ==================================================================
    # 第 4 步：生成成果
    # ==================================================================
    def step_frame_export(self):
        m = self.m
        prj = m.setdefault("project", {})
        if not hasattr(self, "_v_outdir"):
            self._v_outdir = tk.StringVar(value=prj.get("outdir") or self.out_dir())
        if not hasattr(self, "_v_fname"):
            self._v_fname = tk.StringVar(value=prj.get("name") or "平面刚架")

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
        ttk.Label(line1, text="（生成 <名字>.mct / _数据表.txt / _求解结果.txt / _数据.json）",
                  style="Hint.TLabel").pack(side="left")

        line2 = ttk.Frame(box)
        line2.pack(fill="x", pady=(10, 0))
        ttk.Button(line2, text="① 生成全部成果", command=self._gen_all).pack(side="left")
        ttk.Button(line2, text="② 预览 MCT", command=self._preview_mct).pack(
            side="left", padx=(8, 0))
        ttk.Button(line2, text="③ 预览求解结果", command=self._preview_result).pack(
            side="left", padx=(8, 0))
        ttk.Button(line2, text="④ 复制 MCT 文本", command=self._copy_mct).pack(
            side="left", padx=(8, 0))

        ttk.Label(box, text="MIDAS Civil NX：文件 → 导入 → MCT 命令文件，选 <名字>.mct。\n"
                            "平面刚架建在 X-Z 平面（Z 向上）：向导 x → X、向导 y → Z；"
                            "面内自由度 DX/DZ/RY，支座另把 DY/RX/RZ 固定（程序补的平面外约束）。\n"
                            "分析前先「另存为」，否则 MIDAS 会弹保存对话框。",
                  style="Hint.TLabel", justify="left").pack(anchor="w", pady=(8, 0))

        box2 = self.card("日志 / 预览")
        self.log = tk.Text(box2, height=20, wrap="none", font=("Consolas", 9))
        sb = ttk.Scrollbar(box2, orient="vertical", command=self.log.yview)
        self.log.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.log.pack(side="left", fill="both", expand=True)
        self.log.insert("1.0", "（先点 ① 生成全部成果，或点 ②③ 预览文本。）\n\n"
                               + self._solve_summary())
        self.log.configure(state="disabled")

    def _log(self, text):
        try:
            self.log.configure(state="normal")
            self.log.insert("end", str(text) + "\n")
            self.log.see("end")
            self.log.configure(state="disabled")
        except (AttributeError, tk.TclError):
            pass

    def _solve_summary(self):
        """一段简短的结果摘要（写进日志开头）。"""
        try:
            sol = fc.solve(self.m)
        except Exception as exc:                               # noqa: BLE001
            return "矩阵位移法：算不了（%s）" % exc
        lines = ["矩阵位移法结果摘要（完整表在「求解结果」文件里）：", ""]
        for no in sol["order"]:
            r = sol["elements"][no]
            lines.append("  杆件 %-8s N=%9.3f  Q_i=%8.3f  Q_j=%8.3f  "
                         "M_i(端)=%9.3f  M_j(端)=%9.3f  M_i(图)=%9.3f  M_j(图)=%9.3f"
                         % (fc._elem_name(r), r["N"], r["Qi"], r["Qj"],
                            r["Mi"], r["Mj"], r["Mi_sag"], r["Mj_sag"]))
        lines.append("")
        for no, (rx, ry, rm) in sol["reactions"].items():
            if abs(rx) + abs(ry) + abs(rm) > 1e-3:
                lines.append("  支反力 节点 %-3s Rx=%9.3f  Ry=%9.3f  M=%9.3f" % (no, rx, ry, rm))
        b = sol["balance"]
        lines.append("  平衡校核：ΣFx/ΣFy/ΣM 残差 = (%.3e, %.3e, %.3e)  →  %s"
                     % (b["resid"][0], b["resid"][1], b["resid"][2],
                        "通过" if b["ok"] else "不通过，检查约束/荷载"))
        return "\n".join(lines)

    # ---------------- 生成 ----------------
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
        probs = fc.all_issues(self.m)
        if not probs:
            try:
                fc.collect(self.m)
            except fc.ModelError as exc:
                probs = [str(exc)]
        if probs:
            messagebox.showwarning("先改完这些再生成",
                                   "下面这些问题会让 MIDAS 导入报错或算不了，先改好：\n\n"
                                   + "\n".join("· " + p for p in probs[:10])
                                   + ("\n…还有 %d 条" % (len(probs) - 10) if len(probs) > 10 else ""))
            return None
        fname = fc._safe_name(self._v_fname.get() or
                             (self.m.get("project") or {}).get("name") or "平面刚架")
        out = (self._v_outdir.get() or "").strip() or self.out_dir()
        try:
            os.makedirs(out, exist_ok=True)
        except OSError as exc:
            self.warn("文件夹建不了", str(exc))
            return None
        self.m.setdefault("project", {})
        self.m["project"]["name"] = fname
        self.m["project"]["outdir"] = out
        self._v_fname.set(fname)
        return fname, out

    def _write_all(self, fname, out):
        try:
            files = fc.write_outputs(self.m, out, fname)
        except Exception as exc:                               # noqa: BLE001
            self.warn("生成失败", "写文件出错（参数里可能有异常值）：\n%s" % exc)
            self.report_error("生成成果", exc)
            return []
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
        for label, path in files:
            self._log("√ %s：%s" % (label, path))
        self._log("")
        self._log(self._solve_summary())
        st = fc.mct_stats(self.m)
        self._log("")
        self._log("MCT 规模：*NODE %d 行、*ELEMENT %d 行、*BEAMLOAD %d 行、"
                  "*CONLOAD %d 行、*FRAME-RLS %d 行、*CONSTRAINT %d 行"
                  % (st["nodes"], st["elems"], st["beamload"], st["conload"],
                     st["rls"], st["constraint"]))
        messagebox.showinfo("已生成",
                            "已生成 %d 个文件：\n%s\n\nMIDAS：文件 → 导入 → MCT 命令文件。"
                            % (len(files), "\n".join("· %s" % p for _l, p in files)))

    def _preview_mct(self):
        probs = fc.all_issues(self.m)
        if probs:
            self.warn("先改完这些", "\n".join(probs[:8]))
            return
        try:
            mct = fc.build_mct(self.m)
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
        self._log("\n—— 上面是 MCT 全文（%d 行）；改参数后重新点「预览 MCT」会刷新。——"
                  % (mct.count("\n") + 1))

    def _preview_result(self):
        txt = fc.result_report(self.m)
        try:
            self.log.configure(state="normal")
            self.log.delete("1.0", "end")
            self.log.insert("1.0", txt)
            self.log.configure(state="disabled")
        except (AttributeError, tk.TclError):
            return
        self._log("\n—— 上面是求解结果全文（%d 行）。——" % (txt.count("\n") + 1))

    def _copy_mct(self):
        probs = fc.all_issues(self.m)
        if probs:
            self.warn("先改完这些", "\n".join(probs[:8]))
            return
        try:
            mct = fc.build_mct(self.m)
        except Exception as exc:                               # noqa: BLE001
            self.warn("生成失败", str(exc))
            return
        try:
            self.wiz.clipboard_clear()
            self.wiz.clipboard_append(mct)
        except tk.TclError:
            return
        self._log("√ MCT 全文已复制到剪贴板（可粘到 MIDAS 的 MCT 命令窗口运行）")
