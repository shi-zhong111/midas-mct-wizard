# -*- coding: utf-8 -*-
r"""装配式预应力混凝土简支 T 梁桥（整桥 · 钢束 · 全流程）—— 界面流程插件（flow_psc_tbeam.py）。

结构（照 flow_tbeam.py 的写法）：
    * 纯计算 / MCT 生成都在 psc_tbeam_core.py（不依赖 tkinter，测试可以直接调）；
    * 本文件只做「一步一步填数值」的界面，按 flow_api.FlowBase 实现 4 步：
          1. 总体布置与截面  （跨径、梁数/梁距、桥宽、截面尺寸，右边实时画图 + 截面特性）
          2. 材料与钢束      （C50/钢绞线参数、每束根数/张拉控制应力/钢束高度）
          3. 荷载与施工阶段  （二期恒载、移动荷载公路-I级、施工阶段开关）
          4. 生成成果/直连 MIDAS（.mct + 数据表 + json + 导入说明；导入/分析/取结果/截图）

主程序 midas_wizard.py 的 FLOW_PLUGIN_MODULES 里登记 ("psc_tbeam", "flow_psc_tbeam")，
本文件放到程序目录就会出现「装配式预应力混凝土简支T梁桥（整桥·钢束·全流程）」这个流程。
"""

import json
import os
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import psc_tbeam_core as pc
from flow_api import FlowBase

CODE = pc.CODE
LABEL = pc.LABEL
default_model = pc.default_model
reset_model = pc.reset_model
issues = pc.issues
all_issues = pc.all_issues
data_report = pc.data_report
build_mct = pc.build_mct
geom = pc.geom
summary = pc.summary
section_summary = pc.section_summary

STEP_IDS = ["psc_geom", "psc_tendon", "psc_load", "psc_export"]
STEP_TITLES = ["总体布置与截面", "材料与钢束", "荷载与施工阶段", "生成成果 / 直连 MIDAS"]
STEP_HINTS = [
    "填跨径、主梁片数/梁距、桥面净宽和 T 梁截面尺寸；右边实时画横断面并给出截面特性"
    "（A、Iyy、Izz、形心）。改完数字可先看下面的检查项。",
    "选混凝土等级、填千斤顶张拉控制应力；每束钢绞线面积 = 根数 × 1.4 cm²。"
    "钢束高度是「距梁底」的高度，程序按节点线（T 梁形心）自动换算。",
    "二期恒载按每片梁的线荷载填；移动荷载按公路-I 级车道荷载；"
    "施工阶段暂不作分析（先做一次成桥静力 + 钢束），勾选后生成表备用。",
    "生成 .mct / 数据表 / json / 导入说明；MIDAS 开着时可直接点「导入 → 分析 → 取回结果 → 截图」，"
    "结果会存到成果目录。",
]
last_hint = "文件已准备好，可以保存 / 复制 / 直连 MIDAS。"


def _fmt(v, nd=3):
    try:
        return ("%." + str(nd) + "f") % float(v)
    except (TypeError, ValueError):
        return "-"


class Flow(FlowBase):
    code = CODE
    label = LABEL
    step_ids = STEP_IDS
    step_titles = STEP_TITLES
    step_hints = STEP_HINTS
    last_hint = last_hint

    def warn(self, title, msg):
        messagebox.showwarning(title, msg)

    def info(self, title, msg):
        messagebox.showinfo(title, msg)

    def ask(self, title, msg):
        return bool(messagebox.askyesno(title, msg))

    # ---------------- 数据 ----------------
    def default_model(self):
        return pc.default_model()

    def reset_model(self):
        return pc.reset_model()

    def issues(self, sid):
        return pc.issues(self.m, sid)

    def data_report(self):
        return pc.data_report(self.m)

    def clear_title(self, sid):
        return {"psc_geom": "总体布置与截面", "psc_tendon": "材料与钢束",
                "psc_load": "荷载与施工阶段"}.get(sid, "")

    def clear(self, sid):
        d = pc.default_model()
        if sid == "psc_geom":
            for k in ("geom", "project"):
                self.m[k] = dict(d[k])
        elif sid == "psc_tendon":
            for k in ("mat", "tendon"):
                self.m[k] = dict(d[k])
        elif sid == "psc_load":
            for k in ("load", "stages"):
                self.m[k] = dict(d[k])

    # ---------------- 工具 ----------------
    def _row(self, parent, items, row=0, padx=18):
        """一行放若干 (标签, dict, key) 输入框。"""
        col = 0
        for (label, d, key) in items:
            ttk.Label(parent, text=label).grid(row=row, column=col, sticky="w", padx=(0, 4), pady=3)
            e = ttk.Entry(parent, textvariable=self.svar(d, key, d.get(key)), width=9)
            e.grid(row=row, column=col + 1, sticky="w", padx=(0, padx), pady=3)
            col += 2
        return col

    # ==================================================================
    # 第 1 步：总体布置与截面
    # ==================================================================
    def step_psc_geom(self):
        m = self.m
        g = m.setdefault("geom", {})
        for k, v in pc.default_model()["geom"].items():
            g.setdefault(k, v)
        self._v = {}

        def sv(key):
            var = self.svar(g, key, g.get(key))
            self._v[key] = var
            return var

        def row(parent, items, row=0):
            col = 0
            out = {}
            for (label, key) in items:
                ttk.Label(parent, text=label).grid(row=row, column=col, sticky="w", padx=(0, 4), pady=3)
                e = ttk.Entry(parent, textvariable=sv(key), width=9)
                e.grid(row=row, column=col + 1, sticky="w", padx=(0, 18), pady=3)
                out[key] = e
                col += 2
            return out

        box = self.card("跨径与总体布置")
        grid = ttk.Frame(box)
        grid.pack(fill="x", anchor="w")
        E = {}
        E.update(row(grid, [("标准跨径 Lb (m)", "Lstd"), ("计算跨径 L (m)", "Lcalc"),
                            ("梁全长 (m)", "Lfull")], row=0))
        E.update(row(grid, [("主梁片数", "ng"), ("梁间距 (m)", "sg"),
                            ("桥面净宽 (m)", "deck"), ("人行道宽 (m)", "walk")], row=1))
        E.update(row(grid, [("纵向单元数", "ne"), ("横隔板道数", "ndia"),
                            ("横隔板厚 (m)", "tdia"), ("横隔板高 (m)", "hdia")], row=2))
        ttk.Label(grid, text="提示：改「标准跨径」会自动带出计算跨径（Lb−0.6）和梁全长（Lb−0.04）；"
                             "改下面任一参数，横断面图和平面布置图都会跟着变。",
                  style="Hint.TLabel").grid(row=3, column=0, columnspan=10, sticky="w", pady=(2, 0))

        # 标准跨径 → 计算跨径 / 梁全长 联动
        def on_lb(*_e):
            try:
                lb = float(self._v["Lstd"].get())
            except (TypeError, ValueError):
                return
            if lb <= 0:
                return
            self._v["Lcalc"].set("%.2f" % (lb - 0.6))
            self._v["Lfull"].set("%.2f" % (lb - 0.04))
        self._v["Lstd"].trace_add("write", on_lb)   # Lb 一改就带出 L/L全（trace 比 KeyRelease 可靠）

        box2 = self.card("T 梁截面尺寸（单位 m，改完自动刷新）")
        grid2 = ttk.Frame(box2)
        grid2.pack(fill="x", anchor="w")
        E.update(row(grid2, [("梁高 h", "h"), ("翼缘宽 bf", "bf"),
                             ("翼缘厚 tf", "tf"), ("腹板厚 tw", "tw")], row=0))
        E.update(row(grid2, [("马蹄宽 bm", "bm"), ("马蹄高 hm", "hm")], row=1))

        prev = self.card("横断面预览与截面特性（改数字自动刷新）")
        cv = tk.Canvas(prev, height=250, bg="white", highlightthickness=1,
                       highlightbackground="#cccccc")
        cv.pack(fill="x")
        info = ttk.Label(prev, text="", style="Ok.TLabel", justify="left")
        info.pack(anchor="w", pady=(6, 0))

        plan = self.card("平面布置图（跨径 / 单元划分 / 横隔板位置）")
        cv2 = tk.Canvas(plan, height=230, bg="white", highlightthickness=1,
                        highlightbackground="#cccccc")
        cv2.pack(fill="x")
        pinfo = ttk.Label(plan, text="", style="Ok.TLabel", justify="left")
        pinfo.pack(anchor="w", pady=(6, 0))

        def refresh(*_e):
            try:
                _draw_cross(cv, m)
                sm = pc.section_summary(m)
                gg = pc.geom(m)
                ne_elems, _ = pc.elements(m)
                info.configure(text=(
                    "T 梁：A = %.4f m2   Iyy = %.6f m4   Izz = %.6f m4   形心距梁底 %.4f m；"
                    "横隔板：A = %.4f m2\n"
                    "全宽 %.2f m（净宽 %.2f + 2×%.2f 人行道）；梁距 %.2f m，%d 片梁，%d 道横隔板"
                    % (sm["A"], sm["Iyy"], sm["Izz"], sm["yb"], sm["dA"],
                       _f(g, "deck") + 2 * _f(g, "walk"), _f(g, "deck"), _f(g, "walk"),
                       _f(g, "sg"), gg["ng"], gg["ndia"])))
            except Exception as exc:                      # noqa: BLE001
                info.configure(text="参数还不完整：%s" % exc)
            try:
                _draw_plan(cv2, m)
                gg = pc.geom(m)
                ne_elems, _ = pc.elements(m)
                dia_x = "、".join("%.2f" % (r * gg["dx"]) for r in gg["rows"])
                pinfo.configure(text=(
                    "计算跨径 L = %.2f m；%d 片主梁 × %d 个单元 = %d 个纵梁单元，单元长 %.4f m；"
                    "%d 道横隔板（x = %s m）\n模型合计：节点 %d 个，单元 %d 个"
                    % (gg["L"], gg["ng"], gg["ne"], gg["ng"] * gg["ne"], gg["dx"],
                       gg["ndia"], dia_x, len(pc.nodes(m)), len(ne_elems))))
            except Exception as exc:                      # noqa: BLE001
                pinfo.configure(text="参数还不完整：%s" % exc)

        cv.bind("<Configure>", refresh)
        cv2.bind("<Configure>", refresh)
        for _key, _var in list(self._v.items()):
            if _key == "Lstd":
                continue                      # Lstd 的 trace 里已经会触发 refresh（on_lb 之后）
            def _on_var(*_a, _r=refresh):
                # 变量 trace 的执行顺序：本回调可能先于「写回字典」的 trace 跑，
                # 所以用 after_idle 推迟到这一轮事件之后再画，保证读到的是新值。
                try:
                    self.wiz.after_idle(_r)
                except Exception:
                    _r()
            _var.trace_add("write", _on_var)
        refresh()

        ttk.Label(box2, text="提示：MCT 采用 *SECT-PSCVALUE 多边形一般截面（含马蹄），"
                             "截面特性由程序按多边形积分算出，和 MIDAS 显示值一致。",
                  style="Hint.TLabel", justify="left").pack(anchor="w", pady=(6, 0))

    # ==================================================================
    # 第 2 步：材料与钢束
    # ==================================================================
    def step_psc_tendon(self):
        m = self.m
        mat = m.setdefault("mat", {})
        for k, v in pc.default_model()["mat"].items():
            mat.setdefault(k, v)
        td = m.setdefault("tendon", {})
        for k, v in pc.default_model()["tendon"].items():
            td.setdefault(k, v)
        self._v2 = []

        def row(parent, items, row=0):
            col = 0
            for (label, d, key) in items:
                ttk.Label(parent, text=label).grid(row=row, column=col, sticky="w", padx=(0, 4), pady=3)
                var = self.svar(d, key, d.get(key))
                self._v2.append(var)
                ttk.Entry(parent, textvariable=var, width=10).grid(
                    row=row, column=col + 1, sticky="w", padx=(0, 18), pady=3)
                col += 2

        box = self.card("材料")
        grid = ttk.Frame(box)
        grid.pack(fill="x", anchor="w")
        row(grid, [("混凝土 E (kN/m²)", mat, "Ec"), ("泊松比 ν", mat, "nu"),
                   ("容重 γ (kN/m³)", mat, "gam")], row=0)
        row(grid, [("钢绞线 E (kN/m²)", mat, "Es"), ("fpk (kN/m²)", mat, "fpk"),
                   ("fpd (kN/m²)", mat, "fpd")], row=1)

        box2 = self.card("预应力钢束")
        grid2 = ttk.Frame(box2)
        grid2.pack(fill="x", anchor="w")
        row(grid2, [("每束根数", td, "ns"), ("单根面积 (m²)", td, "a1"),
                    ("张拉控制应力 (kN/m²)", td, "scon")], row=0)
        row(grid2, [("摩阻系数 μ", td, "mu"), ("偏差系数 k", td, "k"),
                    ("锚具回缩 (m)", td, "slip")], row=1)
        row(grid2, [("钢束1 距梁底 (m)", td, "z1"), ("钢束2", td, "z2"),
                    ("钢束3", td, "z3")], row=2)
        row(grid2, [("钢束4", td, "z4"), ("钢束5", td, "z5")], row=3)

        prev = self.card("钢束布置预览")
        cv = tk.Canvas(prev, height=240, bg="white", highlightthickness=1,
                       highlightbackground="#cccccc")
        cv.pack(fill="x")
        info = ttk.Label(prev, text="", style="Ok.TLabel", justify="left")
        info.pack(anchor="w", pady=(8, 0))

        def refresh(*_e):
            try:
                _draw_tendons(cv, m)
                levels = pc.tendon_levels(m)
                A = pc.tendon_area(m)
                N1 = pc.tendon_force(m)
                info.configure(text=(
                    "每梁 %d 束 × %s 根 φs15.2；单束面积 %.5f m²；单束张拉力 %.1f kN\n"
                    "单片梁张拉力合计 %.1f kN；钢束距梁底：%s m"
                    % (len(levels), td.get("ns"), A, N1, N1 * len(levels),
                       "、".join("%.2f" % z for z in levels))))
            except Exception as exc:                     # noqa: BLE001
                info.configure(text="参数还不完整：%s" % exc)

        cv.bind("<Configure>", refresh)
        for _var in self._v2:
            def _on_var2(*_a, _r=refresh):
                try:
                    self.wiz.after_idle(_r)
                except Exception:
                    _r()
            _var.trace_add("write", _on_var2)
        refresh()

    # ==================================================================
    # 第 3 步：荷载与施工阶段
    # ==================================================================
    def step_psc_load(self):
        m = self.m
        ld = m.setdefault("load", {})
        for k, v in pc.default_model()["load"].items():
            ld.setdefault(k, v)
        st = m.setdefault("stages", {})
        for k, v in pc.default_model()["stages"].items():
            st.setdefault(k, v)

        box = self.card("二期恒载（线荷载，kN/m）")
        grid = ttk.Frame(box)
        grid.pack(fill="x", anchor="w")
        self._row(grid, [("中梁 q2", ld, "q2c"), ("边梁 q2", ld, "q2e")], row=0)
        ttk.Label(box, text="二期恒载 = 桥面铺装（10 cm 沥青混凝土，24 kN/m³）+ 人行道板及栏杆；"
                            "边梁另担人行道+栏杆。", style="Hint.TLabel").pack(anchor="w")

        box2 = self.card("移动荷载（公路-I 级）")
        grid2 = ttk.Frame(box2)
        grid2.pack(fill="x", anchor="w")
        self._row(grid2, [("车道数", ld, "lanes"), ("车道宽 (m)", ld, "lane_w"),
                          ("qk (kN/m)", ld, "qk"), ("Pk (kN)", ld, "pk")], row=0)
        ttk.Label(box2, text="Pk 按计算跨径 29.4 m 由 JTG D60-2015 公式算得（Pk = 180 + (360-180)×(50-L)/(50-5)）。"
                            "移动荷载分析留到下一版；本版先出一次成桥恒载+预应力结果。",
                  style="Hint.TLabel").pack(anchor="w")

        box3 = self.card("施工阶段（本次先不做分析，仅生成阶段表）")
        line = ttk.Frame(box3)
        line.pack(fill="x", anchor="w")
        ttk.Label(line, text="启用施工阶段分析").grid(row=0, column=0, sticky="w", padx=(0, 6))
        ttk.Checkbutton(line, variable=self.svar(st, "on", st.get("on"))).grid(row=0, column=1, sticky="w")
        grid3 = ttk.Frame(box3)
        grid3.pack(fill="x", anchor="w")
        rows = st.get("rows") or []
        if isinstance(rows, (list, tuple)):
            for i, r in enumerate(rows[:5]):
                if not isinstance(r, dict):
                    continue
                ttk.Label(grid3, text="%s" % r.get("name", "CS%d" % (i + 1)), width=6).grid(row=i, column=0, sticky="w", pady=2)
                ttk.Entry(grid3, textvariable=self.svar(r, "days", r.get("days")), width=8).grid(row=i, column=1, sticky="w", pady=2)
                ttk.Label(grid3, text=r.get("desc", ""), style="Hint.TLabel").grid(row=i, column=2, sticky="w", padx=(8, 0), pady=2)

    # ==================================================================
    # 第 4 步：生成成果 / 直连 MIDAS
    # ==================================================================
    def step_psc_export(self):
        m = self.m
        box = self.card("成果目录")
        line = ttk.Frame(box)
        line.pack(fill="x", anchor="w")
        pr = m.setdefault("project", {})
        ttk.Label(line, text="目录").grid(row=0, column=0, sticky="w", padx=(0, 4))
        ttk.Entry(line, textvariable=self.svar(pr, "outdir", pr.get("outdir")),
                  width=52).grid(row=0, column=1, sticky="w")
        ttk.Button(line, text="选择…", command=self._pick_dir).grid(row=0, column=2, padx=(6, 0))
        ttk.Label(box, text="留空 = 程序目录/模型文件/%s" % CODE, style="Hint.TLabel").pack(anchor="w")

        box2 = self.card("生成与直连")
        line2 = ttk.Frame(box2)
        line2.pack(fill="x", anchor="w")
        for text, cmd in (("生成成果（MCT+数据表+json）", self.on_export),
                          ("保存 MCT…", self.on_save_mct),
                          ("复制 MCT", self.on_copy_mct)):
            ttk.Button(line2, text=text, command=cmd).pack(side="left", padx=(0, 8), pady=4)
        line3 = ttk.Frame(box2)
        line3.pack(fill="x", anchor="w")
        for text, cmd in (("① 导入到 MIDAS", self.on_import),
                          ("② 运行分析", self.on_analyse),
                          ("③ 取回结果", self.on_results),
                          ("④ 截图保存", self.on_capture)):
            ttk.Button(line3, text=text, command=cmd).pack(side="left", padx=(0, 8), pady=4)
        ttk.Label(box2, text="MIDAS 打开后 API 会自动连（若提示未连接：MIDAS 里 应用程序 → API Settings → 连接）。",
                  style="Hint.TLabel").pack(anchor="w")

        box3 = self.card("日志")
        self.log = tk.Text(box3, height=14, wrap="none", font=("Consolas", 9))
        sb = ttk.Scrollbar(box3, orient="vertical", command=self.log.yview)
        self.log.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.log.pack(side="left", fill="both", expand=True)
        self._log("（这里显示生成/导入/分析的交互过程）")
        self._log(pc.summary(m))

    # ---------------- 目录 / 写成果 ----------------
    def _pick_dir(self):
        d = filedialog.askdirectory(title="选择成果目录", initialdir=self.wiz.out_root() or os.getcwd())
        if d:
            self.m["project"]["outdir"] = d
            self.rerender()

    def _out_dir(self):
        d = (self.m.get("project") or {}).get("outdir") or ""
        if d:
            return d
        return os.path.join(self.wiz.out_root(), CODE)

    def _safe_name(self, s, fallback="30m简支T梁桥"):
        s = str(s or "").strip() or fallback
        for ch in '\\/:*?"<>|':
            s = s.replace(ch, "-")
        return s.strip().strip(".")

    def _bundle(self):
        bad = pc.all_issues(self.m)
        if bad:
            self.warn("参数有问题", "\n".join(bad[:8]))
            return None
        out = self._out_dir()
        os.makedirs(out, exist_ok=True)
        name = self._safe_name((self.m.get("project") or {}).get("name"))
        base = os.path.join(out, name)
        files = []
        try:
            with open(base + ".mct", "w", encoding="gbk", errors="replace", newline="") as fh:
                fh.write(pc.build_mct(self.m))
            files.append(("MIDAS 模型命令文件（导入用）", base + ".mct"))
            with open(base + "_数据.json", "w", encoding="utf-8") as fh:
                json.dump(self.m, fh, ensure_ascii=False, indent=2)
            files.append(("向导数据（可再打开继续改）", base + "_数据.json"))
            with open(base + "_数据表.txt", "w", encoding="gbk", errors="replace", newline="") as fh:
                fh.write(pc.data_report(self.m))
            files.append(("数据表（人可读）", base + "_数据表.txt"))
        except OSError as exc:
            self.warn("写文件失败", str(exc))
            return None
        self.saved_path = base + ".mct"
        self.saved_dir = out
        return files

    def on_export(self):
        files = self._bundle()
        if not files:
            return
        self._log("生成完成：")
        for _, p in files:
            self._log("  " + p)
        self.info("生成完成", "已写入：\n" + "\n".join(p for _, p in files))

    def on_save_mct(self):
        bad = pc.all_issues(self.m)
        if bad:
            self.warn("参数有问题", "\n".join(bad[:8]))
            return
        path = filedialog.asksaveasfilename(title="保存 MCT", defaultextension=".mct",
                                            filetypes=[("MCT 命令文件", "*.mct"), ("所有文件", "*.*")])
        if not path:
            return
        with open(path, "w", encoding="gbk", errors="replace", newline="") as fh:
            fh.write(pc.build_mct(self.m))
        self._log("已保存：" + path)

    def on_copy_mct(self):
        self.wiz.clipboard_clear()
        self.wiz.clipboard_append(pc.build_mct(self.m))
        self._log("MCT 文本已复制到剪贴板。")

    # ---------------- 直连 MIDAS ----------------
    def _mw(self):
        """取主程序模块（源码运行 / PyInstaller 单文件 exe 都要能拿到 api_call）。"""
        import sys as _sys
        mod = _sys.modules.get("midas_wizard")
        if mod is not None and hasattr(mod, "api_call"):
            return mod
        # 冻结运行：主程序是 __main__，不是 midas_wizard 模块
        mod = _sys.modules.get(getattr(self.wiz.__class__, "__module__", "") or "__main__")
        if mod is not None and hasattr(mod, "api_call"):
            return mod
        import midas_wizard as mod
        return mod

    def _log(self, text):
        if not hasattr(self, "log") or self.log is None:
            return
        try:
            self.log.configure(state="normal")
            self.log.insert("end", str(text) + "\n")
            self.log.see("end")
            self.log.configure(state="disabled")
        except Exception:                                 # noqa: BLE001
            pass

    def _ensure_mct(self):
        files = self._bundle()
        if not files:
            return None
        return files[0][1]

    def on_import(self):
        path = self._ensure_mct()
        if not path:
            return
        mw = self._mw()
        st, resp = mw.api_call("POST", "/doc/IMPORTMXT", {"Argument": path}, timeout=300)
        if st is None:
            self._log("✗ 导入失败：" + str(resp)); messagebox.showwarning("没能导入", str(resp)); return
        err = mw.api_error_text(resp)
        self._log(("✓ 状态 %s" % st) + ("  错误：%s" % err if err else "  已导入"))
        if err:
            messagebox.showerror("MIDAS 报错", err)
        else:
            messagebox.showinfo("已导入", "模型已送进 MIDAS，接下来点「② 运行分析」。")

    def on_analyse(self):
        mw = self._mw()
        self._log("→ 运行分析（大模型可能要几十秒）")
        st, resp = mw.api_call("POST", "/doc/ANAL", {"Assign": {}}, timeout=900)
        if st is None:
            self._log("✗ " + str(resp)); return
        err = mw.api_error_text(resp)
        self._log(("✓ 状态 %s" % st) + ("  错误：%s" % err if err else "  分析完成"))
        if err:
            messagebox.showerror("MIDAS 报错", err)

    def _table(self, mw, tt, lcs):
        body = {"Argument": {"TABLE_NAME": "SS_Table", "TABLE_TYPE": tt,
                             "STYLES": {"FORMAT": "Fixed", "PLACE": 4},
                             "LOAD_CASE_NAMES": [n + "(ST)" for n in lcs]}}
        st, resp = mw.api_call("POST", "/post/TABLE", body, timeout=120)
        tab = resp.get("SS_Table") if isinstance(resp, dict) else None
        return tab, mw.api_error_text(resp)

    def on_results(self):
        mw = self._mw()
        lcs = ["SW", "SDL", "PS"]
        tab, err = self._table(mw, "REACTIONG", lcs)
        if not tab:
            if "no analysis result" in (err or "").lower():
                self._log("· 还没有分析结果，先让 MIDAS 跑一次分析…")
                st, resp = mw.api_call("POST", "/doc/ANAL", {"Assign": {}}, timeout=900)
                e2 = mw.api_error_text(resp)
                self._log("  分析：%s %s" % (st, e2 or "ok"))
                if e2:
                    messagebox.showerror("分析失败", e2)
                    return
                import time as _t
                _t.sleep(3)
                tab, err = self._table(mw, "REACTIONG", lcs)
            if not tab:
                self._log("✗ 取不到结果：" + (err or "未知错误"))
                messagebox.showwarning("取不到结果", err or "MIDAS 没有返回结果")
                return
        chunks = []
        for tt, label in (("REACTIONG", "支反力"), ("DISPLACEMENTG", "位移"),
                          ("BEAMFORCE", "梁单元内力"), ("BEAMSTRESS", "梁单元应力")):
            body = {"Argument": {"TABLE_NAME": "SS_Table", "TABLE_TYPE": tt,
                                 "STYLES": {"FORMAT": "Fixed", "PLACE": 4},
                                 "LOAD_CASE_NAMES": [n + "(ST)" for n in lcs]}}
            st, resp = mw.api_call("POST", "/post/TABLE", body, timeout=120)
            tab = resp.get("SS_Table") if isinstance(resp, dict) else None
            if not tab:
                self._log("· %s：没有数据（%s）" % (label, mw.api_error_text(resp)[:80]))
                continue
            head = [str(h) for h in tab.get("HEAD", [])]
            rows = tab.get("DATA", [])
            self._log("✓ %s：%d 行" % (label, len(rows)))
            chunks.append("【%s】\n表头：" % label + " | ".join(head))
            for r in rows:
                chunks.append("  " + "  ".join(str(v) for v in r))
            chunks.append("")
        if not chunks:
            messagebox.showinfo("没有结果", "先确认分析已经跑完。")
            return
        out = self._out_dir()
        os.makedirs(out, exist_ok=True)
        name = self._safe_name((self.m.get("project") or {}).get("name"))
        path = os.path.join(out, name + "_MIDAS结果.txt")
        with open(path, "w", encoding="gbk", errors="replace", newline="") as fh:
            fh.write("MIDAS CIVIL NX 计算结果（由 MIDAS 分析，程序仅取回）\r\n")
            fh.write("工况：%s\r\n\r\n" % "、".join(lcs))
            fh.write("\r\n".join(chunks))
        self._log("→ 结果已存：" + path)

    def on_capture(self):
        """先截 post（不动分析结果），再截 pre（会清分析结果：所以先取结果再截图）。"""
        mw = self._mw()
        import base64
        out = self._out_dir()
        os.makedirs(out, exist_ok=True)
        name = self._safe_name((self.m.get("project") or {}).get("name"))
        shots = [("post", name + "_模型截图_结果视图.jpg", {"HORIZONTAL": 30, "VERTICAL": 20}),
                 ("pre", name + "_模型截图_3D视图.jpg", {"HORIZONTAL": 30, "VERTICAL": 20})]
        for mode, fn, angle in shots:
            arg = {"SET_MODE": mode, "SET_HIDDEN": True, "HEIGHT": 900, "WIDTH": 1440, "ANGLE": angle}
            st, resp = mw.api_call("POST", "/view/CAPTURE", {"Argument": arg}, timeout=180)
            if not isinstance(resp, dict) or "base64String" not in resp:
                self._log("· %s 截图失败（%s）：%s" % (mode, st, str(resp)[:120]))
                continue
            path = os.path.join(out, fn)
            with open(path, "wb") as fh:
                fh.write(base64.b64decode(resp["base64String"]))
            self._log("→ 截图已存：" + path + ("（注意：pre 视图会清空分析结果，要结果请再点②③）" if mode == "pre" else ""))


# ======================================================================
# 画图
# ======================================================================
def _f(d, key, default=0.0):
    try:
        return float(str(d.get(key)).strip())
    except (TypeError, ValueError):
        return default


def _draw_cross(cv, m):
    """画全桥横断面（5 片 T 梁 + 桥面）并在左边放大一根 T 梁。"""
    cv.delete("all")
    w = max(cv.winfo_width(), 620)
    h = max(int(cv.winfo_height()), 300)
    try:
        g = pc.geom(m)
    except Exception:                                     # noqa: BLE001
        cv.create_text(w / 2, h / 2, text="（参数还不完整）", fill="#999999")
        return
    sm = pc.section_summary(m)
    ng, sg, bf = g["ng"], g["sg"], g["bf"]
    total = ng * sg
    s = (w - 80) / max(total, 1e-9)
    base_y = h - 40
    # 全桥 T 梁（简化：矩形腹板 + 翼缘）
    for i in range(ng):
        cx = w / 2 + (i - (ng - 1) / 2.0) * sg * s
        bw = max(6, _f(g, "tw") * s * 6)
        hh = _f(g, "h") * s
        cv.create_rectangle(cx - bw / 2, base_y - hh, cx + bw / 2, base_y, fill="#b9c0c9", outline="#5a6270")
        cv.create_rectangle(cx - bf * s / 2, base_y - hh, cx + bf * s / 2, base_y - hh + _f(g, "tf") * s * 3,
                            fill="#e8c25a", outline="#c9a227")
        cv.create_text(cx, base_y + 12, text="%d" % (i + 1), fill="#666666")
    cv.create_text(w / 2, 14, text="全桥横断面（%d 片 T 梁，梁距 %.2f m，全宽 %.2f m）"
                   % (ng, sg, total), fill="#2c3e50")
    # 左边放大 T 梁
    ox, oy = 60, base_y
    ss = min(120.0 / max(sm["yb"] + sm.get("CzP", 1.0), 1e-9), 90.0 / max(bf, 1e-9))
    ss = max(ss, 1e-9)
    pts = [(ox + y * ss, oy - z * ss) for (y, z) in g["t_poly"]]
    cv.create_polygon(*[c for p in pts for c in p], fill="#d5dbe2", outline="#2c3e50")
    cv.create_text(ox, oy + 12, text="T 梁截面", fill="#666666")



def _draw_plan(cv, m):
    """画平面布置图：纵梁线、节点、横隔板位置、支座。"""
    cv.delete("all")
    w = max(cv.winfo_width(), 620)
    h = max(int(cv.winfo_height()), 230)
    try:
        g = pc.geom(m)
    except Exception:                                     # noqa: BLE001
        cv.create_text(w / 2, h / 2, text="（参数还不完整）", fill="#999999")
        return
    L = g["L"]; ng = g["ng"]; sg = g["sg"]; ne = g["ne"]; rows = g["rows"]
    x0, x1 = 70, w - 40
    y0 = 46
    xs = lambda x: x0 + (x1 - x0) * (x / L if L else 0)
    # 纵梁
    for i in range(ng):
        yy = y0 + i * 26
        cv.create_line(xs(0), yy, xs(L), yy, fill="#2c3e50", width=1.6)
        for k in range(ne + 1):
            px = xs(k * g["dx"])
            cv.create_oval(px - 2, yy - 2, px + 2, yy + 2, fill="#ffffff", outline="#2c3e50")
    ytop = y0 - 16
    ybot = y0 + (ng - 1) * 26 + 16
    # 横隔板
    for r in rows:
        px = xs(r * g["dx"])
        cv.create_line(px, ytop, px, ybot, fill="#c0392b", width=3)
        cv.create_text(px, ybot + 12, text="%.2f" % (r * g["dx"]), fill="#c0392b", font=("", 8))
    # 支座
    for sid, yy in ((0, y0 - 12), (L, y0 - 12)):
        cv.create_polygon(xs(sid) - 7, yy, xs(sid) + 7, yy, xs(sid), yy + 9,
                          fill="#2980b9", outline="#1f618d")
    # 尺寸线
    yd = ybot + 38
    cv.create_line(xs(0), yd, xs(L), yd, arrow="both", fill="#666666")
    cv.create_text((xs(0) + xs(L)) / 2, yd - 10, text="L = %.2f m" % L, fill="#444444")
    cv.create_text(10, 10, anchor="nw", fill="#2c3e50",
                   text="平面布置（红线 = 横隔板，蓝三角 = 支座；%d 片梁 × %d 单元）" % (ng, ne))


def _draw_tendons(cv, m):

    """画单片 T 梁立面：钢束（直线）+ 截面高度。"""
    cv.delete("all")
    w = max(cv.winfo_width(), 620)
    h = max(int(cv.winfo_height()), 240)
    try:
        g = pc.geom(m)
        levels = pc.tendon_levels(m)
    except Exception:                                     # noqa: BLE001
        cv.create_text(w / 2, h / 2, text="（参数还不完整）", fill="#999999")
        return
    x0, x1 = 60, w - 40
    y_base = h - 50
    scale_x = (x1 - x0) / max(g["L"], 1e-9)
    scale_z = (h - 110) / max(g["h"], 1e-9)
    # T 梁轮廓（立面）
    hh = g["h"] * scale_z
    bw = _f(g, "tw") * scale_z * 2.2
    cx = w / 2
    cv.create_rectangle(x0, y_base - hh, x1, y_base - hh + _f(g, "tf") * scale_z, fill="#e8c25a", outline="#c9a227")
    cv.create_rectangle(cx - bw, y_base - hh + _f(g, "tf") * scale_z, cx + bw, y_base, fill="#d5dbe2", outline="#5a6270")
    for k, z in enumerate(levels, start=1):
        yy = y_base - z * scale_z
        cv.create_line(x0 + 6, yy, x1 - 6, yy, fill="#c0392b", width=2)
        cv.create_text(x1 - 2, yy - 8, text="N%d (z=%.2f m)" % (k, z), anchor="e", fill="#c0392b")
    cv.create_text(x0, y_base + 14, text="0", fill="#666666")
    cv.create_text(x1, y_base + 14, text="L = %.2f m" % g["L"], fill="#666666")
    cv.create_text(w / 2, 14, text="钢束立面布置（每片梁 %d 束，直线布置）" % len(levels), fill="#2c3e50")
