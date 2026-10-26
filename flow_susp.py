# -*- coding: utf-8 -*-
"""悬索桥流程的界面（第 1~5 步）。

引擎在 susp_core.py（纯计算、不依赖 tkinter）；这里只负责把参数画成「一步一步填数值」的界面。

步骤：
    1 桥型与几何   主跨/边跨/矢跨比/吊杆间距/塔高/加劲梁分段 + 实时摘要 + 桥型示意图
    2 截面         加劲梁、桥塔（sect_ui.SectionEditor，支持 DXF 自定义截面）
    3 材料与索     梁/塔材料、主缆/吊杆面积与弹模、二期恒载、初拉力估算（H = wL²/(8f)）
    4 边界与荷载   塔底固结、塔梁连接、主缆过塔顶、锚固点约束、自重/二期/初始索力
    5 生成成果     MCT + 数据 json + 数据表 + 导入说明，预览/复制
"""

import os
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import sect_ui
import susp_core as sc
from flow_api import FlowBase

CODE = sc.CODE
LABEL = sc.LABEL

STEP_IDS = ["susp_geo", "susp_sect", "susp_mat", "susp_bc", "susp_export"]
STEP_TITLES = ["桥型与几何", "截面（加劲梁 / 桥塔）", "材料与索（含初拉力）", "边界与荷载", "生成成果"]
STEP_HINTS = [
    "先定跨径、矢跨比、吊杆间距和塔高：下面的摘要会立刻算出节点数、单元数、吊杆数和主缆长度。",
    "第 1 个截面给加劲梁、第 2 个给桥塔（可以导入 DXF 自定义截面）；主缆和吊杆只用面积，在第 3 步填。",
    "填梁/塔材料、主缆/吊杆面积和弹模；初拉力按恒载水平分力 H = wL²/(8f) 估算，也可以手填 H 或逐根改吊杆力。",
    "塔底固结、塔梁怎么连、主缆怎么过塔顶、锚固点怎么约束——边界定错，后面的内力全错。",
    "点「生成整套成果」写出 MCT，然后在 MIDAS 里：文件 → 导入 → MCT 命令文件。",
]

# 每一步「清空本步」要恢复的键
CLEAR_KEYS = {
    "susp_geo": ["L", "Ls", "fr", "ds", "ns", "z_base", "z_deck", "z_top", "n_tower", "n_girder"],
    "susp_sect": ["sections"],
    "susp_mat": ["girder_E", "girder_nu", "girder_den", "tower_E", "tower_nu", "tower_den",
                 "cable_A", "cable_E", "cable_nu", "cable_den",
                 "hanger_A", "hanger_E", "hanger_nu", "hanger_den",
                 "q2", "auto_H", "H", "hanger_factor", "hanger_T"],
    "susp_bc": ["tower_fix", "anchor_fix", "tower_beam", "saddle", "cable_elem",
                "selfweight", "dl2", "pretension", "hanger_ps", "lc_sw", "lc_dl2", "lc_ps"],
}


def default_model():
    return sc.default_model()


def issues(model, sid):
    return sc.issues(model, sid)


def build_mct(model):
    return sc.build_mct(model)


def data_report(model):
    return sc.data_report(model)


class Flow(FlowBase):
    code = CODE
    label = LABEL
    step_ids = STEP_IDS
    step_titles = STEP_TITLES
    step_hints = STEP_HINTS
    last_hint = "文件已准备好：点「生成整套成果」写出 MCT，再到 MIDAS 里「文件 → 导入 → MCT 命令文件」。"

    # ---------------- 数据 ----------------
    def _mb(self):
        """弹窗统一走主程序的 messagebox 模块：测试里把它替身化就不会真弹框卡住。"""
        try:
            import midas_wizard as mw
            return getattr(mw, "messagebox", messagebox)
        except Exception:                                   # noqa: BLE001
            return messagebox

    def default_model(self):
        return default_model()

    def reset_model(self):
        return default_model()

    def issues(self, sid):
        return issues(self.m, sid)

    def data_report(self):
        return data_report(self.m)

    def on_enter(self):
        pass

    def clear_title(self, sid):
        return {"susp_geo": "桥型与几何", "susp_sect": "截面",
                "susp_mat": "材料与索（含初拉力）", "susp_bc": "边界与荷载"}.get(sid, "")

    def clear(self, sid):
        keys = CLEAR_KEYS.get(sid) or []
        d = sc.default_model()
        if sid == "susp_sect":
            self.m["sections"] = d["sections"]
            return
        s = self._s()
        ds = d["susp"]
        for k in keys:
            v = ds.get(k)
            s[k] = list(v) if isinstance(v, list) else v

    # ---------------- 小工具 ----------------
    def _s(self):
        """本流程的参数 dict（缺键自动补默认值）。"""
        sc.ensure_model(self.m)
        return self.m["susp"]

    def _refresh(self):
        """参数变了：刷新摘要文字 + 重画桥型图（不重建整页，避免输入时跳焦点）。"""
        try:
            info = sc.summary(self.m)
        except Exception:                                       # noqa: BLE001
            info = {"ok": False, "errors": ["参数还读不出来"]}
        for name in ("_sum_geo", "_sum_mat", "_sum_bc"):
            var = getattr(self, name, None)
            if var is None:
                continue
            try:
                if name == "_sum_geo":
                    var.set(self._text_geo(info))
                elif name == "_sum_mat":
                    var.set(self._text_mat(info))
                else:
                    var.set(self._text_bc(info))
            except tk.TclError:
                pass
        self._draw()

    def _field(self, grid, row, label, key, unit="", width=10, note=""):
        ttk.Label(grid, text=label).grid(row=row, column=0, sticky="w", pady=3)
        var = self.svar(self._s(), key, self._s().get(key, ""))
        ttk.Entry(grid, textvariable=var, width=width).grid(row=row, column=1, sticky="w", padx=(6, 6), pady=3)
        if unit:
            ttk.Label(grid, text=unit, style="Hint.TLabel").grid(row=row, column=2, sticky="w")
        if note:
            ttk.Label(grid, text=note, style="Opt.TLabel").grid(row=row, column=3, sticky="w", padx=(8, 0))
        var.trace_add("write", lambda *_: self._refresh())
        return var

    def _check(self, grid, row, label, key, colspan=4):
        var = tk.BooleanVar(value=sc._bool(self._s().get(key), False))
        var.trace_add("write", lambda *_: (self._s().__setitem__(key, var.get()), self._refresh()))
        ttk.Checkbutton(grid, text=label, variable=var).grid(row=row, column=0, columnspan=colspan,
                                                             sticky="w", pady=3)
        return var

    def _combo(self, grid, row, label, key, choices, width=46):
        reverse = dict((disp, code) for code, disp in choices)
        cur = sc._txt(self._s().get(key) or choices[0][0]).strip()
        disp = dict((code, d) for code, d in choices).get(cur, choices[0][1])
        var = tk.StringVar(value=disp)

        def on_pick(*_a):
            code = reverse.get(var.get(), choices[0][0])
            self._s()[key] = code
            self._refresh()

        ttk.Label(grid, text=label).grid(row=row, column=0, sticky="w", pady=3)
        cb = ttk.Combobox(grid, textvariable=var, values=[d for _c, d in choices],
                          width=width, state="readonly")
        cb.grid(row=row, column=1, columnspan=3, sticky="w", padx=(6, 6), pady=3)
        cb.bind("<<ComboboxSelected>>", on_pick)
        return var

    @staticmethod
    def _fmt(v, nd=2):
        try:
            return ("%%.%df" % nd) % float(v)
        except (TypeError, ValueError):
            return str(v)

    # ---------------- 摘要文字 ----------------
    def _text_geo(self, info):
        if not info.get("ok"):
            return "参数还没填对：\n" + "\n".join("· " + e for e in (info.get("errors") or [])[:6])
        G = info.get("G")
        return (
            "跨径：主跨 %.1f m × 矢跨比 %.3f（矢高 f = %.1f m），每侧边跨 %.1f m\n"
            "桥塔 / 桥面：塔底 %.1f m、桥面 %.1f m、塔顶 %.1f m（塔高 %.1f m，桥面以上 %.1f m）\n"
            "主缆：跨中比桥面高 %.1f m；主缆几何总长 %.1f m\n"
            "吊杆：主跨间距 %.2f m（%d 根）+ 边跨每侧 %d 根 = 共 %d 根；吊杆长 %.1f ~ %.1f m（跨中最短 %.1f m）\n"
            "统计：节点 %d 个；单元 %d 个（加劲梁 %d / 主缆 %d / 吊杆 %d / 桥塔 %d）"
            % (info["L"], info["fr"], info["f"], info["Ls"],
               info["z_base"], info["z_deck"], info["z_top"], info["tower_h"], info["tower_above_deck"],
               info["clearance"], info["cable_length"],
               info["ds"], max(0, info["n_main"] - 1), info["ns"], info["hangers"],
               info["hanger_len_min"], info["hanger_len_max"], info["hanger_mid"],
               info["nodes"], info["elements"],
               info["counts"]["girder"], info["counts"]["cable"],
               info["counts"]["hanger"], info["counts"]["tower"])
            + ("" if G is None else "")
        )

    def _text_mat(self, info):
        if not info.get("ok"):
            return "第 1 步的几何参数还没填对，先回去把跨径/塔高填好。"
        est = info.get("est")
        if not est:
            return "（还算不出初拉力：第 2 步的加劲梁截面可能还没填好）"
        lines = [
            "加劲梁自重 q = %.2f kN/m（截面面积 %.4f m² × 容重 %s kN/m³）"
            % (est["q_deck"], est["A_deck"], self._fmt(self._s().get("girder_den"), 2)),
            "二期 q2 = %.2f；主缆自重 %.2f；吊杆自重折算 %.2f → 合计 w = %.2f kN/m"
            % (est["q2"], est["q_cable"], est["q_hanger"], est["w"]),
            "矢高 f = %.2f m → 恒载水平分力 H = wL²/(8f) = %.1f kN%s"
            % (est["f"], est["H_auto"],
               "（现在用手填值 %.1f kN）" % est["H"] if est.get("manual_H") else ""),
            "塔顶主缆倾角 tanθ = 4f/L = %.4f，索力 T = H/cosθ = %.1f kN；主缆应力 H/A = %.1f MPa"
            % (est["slope_tower"], est["T_tower"], est["cable_stress"] / 1000.0),
        ]
        if est["hanger_T"]:
            main_t = [t for h, t in zip(info["G"]["hangers"], est["hanger_T"]) if h["main"]]
            side_t = [t for h, t in zip(info["G"]["hangers"], est["hanger_T"]) if not h["main"]]
            lines.append("吊杆初拉力：%s%s"
                         % ((("主跨 %.1f kN/根" % (main_t[0] if main_t else 0.0)) if main_t else ""),
                            (("；边跨 %.1f kN/根" % side_t[0]) if side_t else "")))
        return "\n".join(lines)

    def _text_bc(self, info):
        if not info.get("ok"):
            return ""
        s = self._s()
        G = info.get("G")
        tb = {"fixed": "塔梁固结（*RIGIDLINK）", "bearing": "塔梁竖向支承（*CONSTRAINT）",
              "floating": "塔梁漂浮（不连接）"}.get(sc._txt(s.get("tower_beam")), "")
        sd = {"same": "主缆与塔顶共用节点", "rigid": "塔顶节点 + *RIGIDLINK 主从（鞍座）"
              }.get(sc._txt(s.get("saddle")), "")
        n_sup = info.get("n_supports", 0)
        n_link = info.get("n_links", 0)
        lcs = []
        if sc._bool(s.get("selfweight"), True):
            lcs.append("%s（自重）" % sc._txt(s.get("lc_sw")))
        if sc._bool(s.get("dl2"), True):
            lcs.append("%s（二期 %.1f kN/m）" % (sc._txt(s.get("lc_dl2")), sc._parse_float(s.get("q2"), 0.0)))
        if sc._bool(s.get("pretension"), True):
            n_ps = info["counts"]["cable"] + (info["counts"]["hanger"]
                                              if sc._bool(s.get("hanger_ps"), True) else 0)
            lcs.append("%s（初始索力，%d 根索）" % (sc._txt(s.get("lc_ps")), n_ps))
        return ("边界：约束 %d 个节点；刚性连接 %d 处\n"
                "塔梁连接：%s\n主缆过塔顶：%s\n工况：%s"
                % (n_sup, n_link, tb, sd, "、".join(lcs) if lcs else "（还没勾荷载）"))

    # ---------------- 桥型示意图 ----------------
    def _draw(self):
        cv = getattr(self, "_cv", None)
        if cv is None:
            return
        try:
            if not cv.winfo_exists():
                return
        except tk.TclError:
            return
        try:
            cv.delete("all")
            info = sc.summary(self.m)
            if not info.get("ok"):
                cv.create_text(12, 12, anchor="nw", fill="#c0392b",
                               text="参数还没填对，先看下面的提示。")
                return
            G = info["G"]
            wpx = cv.winfo_width()
            if wpx < 80:
                wpx = cv.winfo_reqwidth() or 820
            hpx = cv.winfo_height()
            if hpx < 80:
                hpx = cv.winfo_reqheight() or 260
            L, Ls = info["L"], info["Ls"]
            x0, x1 = -(L / 2.0 + Ls), (L / 2.0 + Ls)
            z0 = min(info["z_base"], info["z_deck"])
            z1 = info["z_top"]
            dx = max(x1 - x0, 1e-6)
            dz = max(z1 - z0, 1e-6)
            pad_l, pad_r, pad_t, pad_b = 46, 30, 34, 34
            sca = min((wpx - pad_l - pad_r) / dx, (hpx - pad_t - pad_b) / dz)
            ox = pad_l + ((wpx - pad_l - pad_r) - dx * sca) / 2.0
            oy = hpx - pad_b - ((hpx - pad_t - pad_b) - dz * sca) / 2.0
            cv.create_rectangle(0, 0, wpx, hpx, fill="#fbfcfe", outline="")

            def PX(x):
                return ox + (x - x0) * sca

            def PY(z):
                return oy - (z - z0) * sca

            nodes = G["nodes"]
            # 地面 / 塔底
            cv.create_line(PX(x0) - 20, PY(z0), PX(x1) + 20, PY(z0), fill="#999999")
            # 吊杆
            for h in G["hangers"]:
                na, nb = nodes[h["cable"] - 1], nodes[h["deck"] - 1]
                cv.create_line(PX(na["x"]), PY(na["z"]), PX(nb["x"]), PY(nb["z"]),
                               fill="#9ec5e8", width=1)
            # 主缆
            pts = []
            for nid in G["chain"]:
                nd = nodes[nid - 1]
                pts += [PX(nd["x"]), PY(nd["z"])]
            if len(pts) >= 4:
                cv.create_line(*pts, fill="#1f4e79", width=3, smooth=False)
            # 加劲梁
            deck = sorted((nd for nd in nodes if nd["role"] == "deck"), key=lambda n: n["x"])
            pts = []
            for nd in deck:
                pts += [PX(nd["x"]), PY(nd["z"])]
            if len(pts) >= 4:
                cv.create_line(*pts, fill="#2d7d46", width=4)
            # 桥塔
            for sym in ("L", "R"):
                tw = G["towers"][sym]
                cv.create_line(PX(tw["x"]), PY(info["z_base"]), PX(tw["x"]), PY(info["z_top"]),
                               fill="#7a4a1e", width=5)
            # 支座/锚固点（小三角）
            for sup in G["supports"]:
                for nid in sup["nodes"]:
                    nd = nodes[nid - 1]
                    X, Y = PX(nd["x"]), PY(nd["z"])
                    cv.create_polygon(X - 5, Y + 2, X + 5, Y + 2, X, Y + 11,
                                      fill="#c0392b", outline="")
            # 标注
            cxk = (PX(x0) + PX(x1)) / 2.0
            cv.create_text(cxk, PY(info["z_top"]) - 14, text="主跨 L = %.1f m，矢高 f = %.1f m"
                           % (L, info["f"]), fill="#1f4e79")
            cv.create_text(PX(-L / 2.0) - 8, PY(info["z_top"]) - 14, anchor="e",
                           text="塔顶 %.1f m" % info["z_top"], fill="#7a4a1e")
            cv.create_text(PX(x0) + 4, PY(info["z_deck"]) - 12, anchor="w",
                           text="桥面 %.1f m" % info["z_deck"], fill="#2d7d46")
            cv.create_text(PX(x0) + 4, PY(info["z_deck"]) - 26, anchor="w",
                           text="边跨 %.1f m" % Ls, fill="#555555")
            cv.create_text(PX(x1) - 4, PY(info["z_mid"]) - 12, anchor="e",
                           text="跨中主缆比桥面高 %.1f m" % info["clearance"], fill="#1f4e79")
        except Exception:                                       # noqa: BLE001
            pass

    # ==================================================================
    # 第 1 步：桥型与几何
    # ==================================================================
    def step_susp_geo(self):
        box = self.card("主跨、边跨与主缆线形")
        g = ttk.Frame(box)
        g.pack(fill="x")
        self._field(g, 0, "主跨 L", "L", "m", note="悬索桥的主跨，例：500")
        self._field(g, 1, "单侧边跨长 Ls", "Ls", "m", note="两侧各一个边跨（常规是主跨的 1/4 ~ 1/2）")
        self._field(g, 2, "矢跨比 f/L", "fr", "—", note="主缆矢高 / 主跨，常用 1/10（填 0.10）")
        self._field(g, 3, "主跨吊杆间距", "ds", "m", note="程序按它把主跨均匀分成整数个节间")
        self._field(g, 4, "边跨吊杆数（每侧）", "ns", "根", note="沿边跨均匀布置，填 0 = 边跨不设吊杆")

        box = self.card("桥塔与桥面标高")
        g = ttk.Frame(box)
        g.pack(fill="x")
        self._field(g, 0, "塔底（基础顶）标高", "z_base", "m")
        self._field(g, 1, "桥面（加劲梁轴线）标高", "z_deck", "m")
        self._field(g, 2, "塔顶标高", "z_top", "m", note="塔顶必须高于「桥面标高 + 矢高」，否则跨中吊杆是负的")
        self._field(g, 3, "桥塔分段数", "n_tower", "段", note="程序会自动在桥面标高处加一个节点，用来做塔梁连接")

        box = self.card("加劲梁")
        g = ttk.Frame(box)
        g.pack(fill="x")
        self._field(g, 0, "加劲梁分段数", "n_girder", "段/节间", width=6,
                    note="每个吊杆间距内再分几段（1 = 按吊杆位置分段）")

        box = self.card("实时摘要")
        self._sum_geo = tk.StringVar(value="")
        ttk.Label(box, textvariable=self._sum_geo, justify="left", style="Ok.TLabel").pack(anchor="w")
        cv = tk.Canvas(box, height=250, bg="#fbfcfe", highlightthickness=1,
                       highlightbackground="#d0d7e2")
        cv.pack(fill="x", pady=(8, 0))
        self._cv = cv
        cv.bind("<Configure>", lambda _e: self._draw())

        box = self.card("说明")
        ttk.Label(box, justify="left", wraplength=860, style="Hint.TLabel",
                  text="· 主缆用抛物线：主跨 z = 塔顶标高 - f·(1-(2x/L)²)；边跨取与主跨在塔顶相切、"
                       "并落到桥面标高锚固点的抛物线。\n"
                       "· 主缆锚在加劲梁两端（锚碇），吊杆竖直，上端接主缆节点、下端接同一位置的主梁节点。\n"
                       "· 改任何一个数，上面的摘要和图会立刻跟着变，可以先看图确认形状再往下走。").pack(anchor="w")
        self._refresh()

    # ==================================================================
    # 第 2 步：截面
    # ==================================================================
    def step_susp_sect(self):
        ttk.Label(self.form, style="Hint.TLabel", justify="left", wraplength=860,
                  text="第 1 个截面 = 加劲梁，第 2 个截面 = 桥塔；主缆和吊杆不用画截面，"
                       "只在第 3 步填面积（程序会自动补两个只有面积的索截面）。").pack(anchor="w", pady=(0, 8))
        box = self.card("截面库")
        ed = sect_ui.SectionEditor(self, self.m, allow_cad=True, allow_taper=False,
                                   title="加劲梁 / 桥塔截面",
                                   note="加劲梁：钢箱梁可以选「B · 箱形」；桥塔：混凝土塔柱可以选「SB · 实心矩形」。"
                                        "也可以「导入自定义截面（DXF）」画真实截面。")
        ed.on_change = lambda: None
        ed.render(box)
        secs = self.m.get("sections") or []
        box = self.card("本流程用到的截面编号")
        g = ttk.Frame(box)
        g.pack(fill="x")
        rows = [
            ("1 · 加劲梁", secs[0].get("name", "") if len(secs) > 0 else "（缺）", "BEAM 单元 1"),
            ("2 · 桥塔", secs[1].get("name", "") if len(secs) > 1 else "（缺）", "BEAM 单元 2"),
            ("%d · 主缆索截面" % (len(secs) + 1), "CableMain（只有面积）", "TENSTR 主缆，面积在第 3 步填"),
            ("%d · 吊杆索截面" % (len(secs) + 2), "HangerRod（只有面积）", "TENSTR 吊杆，面积在第 3 步填"),
        ]
        for r, (a, b, c) in enumerate(rows, start=1):
            ttk.Label(g, text=a).grid(row=r, column=0, sticky="w", padx=(0, 14), pady=2)
            ttk.Label(g, text=b or "（未命名）").grid(row=r, column=1, sticky="w", padx=(0, 14), pady=2)
            ttk.Label(g, text=c, style="Hint.TLabel").grid(row=r, column=2, sticky="w", pady=2)

    # ==================================================================
    # 第 3 步：材料与索
    # ==================================================================
    def step_susp_mat(self):
        box = self.card("加劲梁材料（钢材 Q345）")
        g = ttk.Frame(box)
        g.pack(fill="x")
        self._field(g, 0, "弹性模量 E", "girder_E", "kN/m²", note="钢材 2.06e8")
        self._field(g, 1, "泊松比 ν", "girder_nu", "—")
        self._field(g, 2, "容重", "girder_den", "kN/m³")

        box = self.card("桥塔材料（混凝土 C50）")
        g = ttk.Frame(box)
        g.pack(fill="x")
        self._field(g, 0, "弹性模量 E", "tower_E", "kN/m²", note="C50 约 3.45e7")
        self._field(g, 1, "泊松比 ν", "tower_nu", "—")
        self._field(g, 2, "容重", "tower_den", "kN/m³")

        box = self.card("主缆与吊杆（索）")
        g = ttk.Frame(box)
        g.pack(fill="x")
        self._field(g, 0, "主缆面积 A", "cable_A", "m²", note="常规 0.1 ~ 0.5 m²")
        self._field(g, 1, "主缆弹性模量 E", "cable_E", "kN/m²", note="钢丝 2.0e8")
        self._field(g, 2, "主缆泊松比 ν", "cable_nu", "—")
        self._field(g, 3, "主缆容重", "cable_den", "kN/m³")
        self._field(g, 4, "吊杆面积 A", "hanger_A", "m²", note="常规 0.002 ~ 0.02 m²")
        self._field(g, 5, "吊杆弹性模量 E", "hanger_E", "kN/m²")
        self._field(g, 6, "吊杆泊松比 ν", "hanger_nu", "—")
        self._field(g, 7, "吊杆容重", "hanger_den", "kN/m³")

        box = self.card("二期恒载与初拉力")
        g = ttk.Frame(box)
        g.pack(fill="x")
        self._field(g, 0, "二期恒载 q2（桥面铺装、栏杆等）", "q2", "kN/m", note="常规 30 ~ 70 kN/m")
        self._check(g, 1, "自动估算主缆初拉力：H = wL²/(8f)（w = 加劲梁自重 + 二期 + 主缆/吊杆自重折算）",
                    "auto_H")
        self._field(g, 2, "手填水平分力 H（不勾自动估算时才用）", "H", "kN",
                    note="按课程算例或成桥目标值填")
        self._field(g, 3, "吊杆初拉力系数", "hanger_factor", "—",
                    note="吊杆初拉力 = 分担恒载 × 系数；1.0 = 按恒载")

        # 吊杆初拉力逐根调整（可选）
        box = self.card("吊杆初拉力（逐根调整，可选）")
        ttk.Label(box, style="Hint.TLabel", justify="left", wraplength=860,
                  text="留空 = 用上面算出的估算值；想改哪根就在「采用值」里填数字（kN）。"
                       "改了几何（跨径/吊杆数）以后按「清空自定义值」重新来。").pack(anchor="w", pady=(0, 6))
        info = sc.summary(self.m)
        G = info.get("G") if info.get("ok") else None
        self._hanger_vars = []
        if G:
            grid = ttk.Frame(box)
            grid.pack(fill="x")
            for c, htext in enumerate(("编号", "位置 x (m)", "估算值 (kN)", "采用值 (kN，可改)")):
                ttk.Label(grid, text=htext, style="Title.TLabel").grid(
                    row=0, column=c, sticky="w", padx=(0, 18), pady=(0, 4))
            est = info.get("est") or {}
            over = self._s().get("hanger_T") or []
            for i, h in enumerate(G["hangers"]):
                r = i + 1
                ttk.Label(grid, text="第 %d 根" % r).grid(row=r, column=0, sticky="w", padx=(0, 18))
                ttk.Label(grid, text="%.2f" % h["x"]).grid(row=r, column=1, sticky="w", padx=(0, 18))
                tv = est.get("hanger_T") or []
                ttk.Label(grid, text=("%.1f" % tv[i]) if i < len(tv) else "-").grid(
                    row=r, column=2, sticky="w", padx=(0, 18))
                cur = over[i] if i < len(over) else ""
                var = tk.StringVar(value=sc._txt(cur))
                var.trace_add("write", lambda *_a, _i=i, _v=var: self._set_hanger_T(_i, _v))
                ttk.Entry(grid, textvariable=var, width=10).grid(row=r, column=3, sticky="w")
                self._hanger_vars.append(var)
            ttk.Button(box, text="清空自定义值（全部回到估算值）", command=self._clear_hanger_T).pack(
                anchor="w", pady=(8, 0))
        else:
            ttk.Label(box, text="（第 1 步的几何参数还没填对，先回去填好。）",
                      style="Hint.TLabel").pack(anchor="w")

        box = self.card("初拉力估算摘要")
        self._sum_mat = tk.StringVar(value="")
        ttk.Label(box, textvariable=self._sum_mat, justify="left").pack(anchor="w")
        self._refresh()

    def _set_hanger_T(self, i, var):
        try:
            over = list(self._s().get("hanger_T") or [])
        except Exception:                                       # noqa: BLE001
            over = []
        while len(over) <= i:
            over.append("")
        over[i] = var.get()
        self._s()["hanger_T"] = over
        self._refresh()

    def _clear_hanger_T(self):
        self._s()["hanger_T"] = []
        self.rerender()

    # ==================================================================
    # 第 4 步：边界与荷载
    # ==================================================================
    def step_susp_bc(self):
        box = self.card("边界条件")
        g = ttk.Frame(box)
        g.pack(fill="x")
        self._check(g, 0, "塔底固结（必须：不固结桥塔是机构）", "tower_fix")
        self._check(g, 1, "主缆锚固点固结（= 加劲梁两端节点，必须）", "anchor_fix")
        self._combo(g, 2, "塔梁连接方式", "tower_beam", sc.TOWER_BEAM_CHOICES)
        self._combo(g, 3, "主缆过塔顶（鞍座）", "saddle", sc.SADDLE_CHOICES)
        self._combo(g, 4, "索单元类型", "cable_elem", sc.CABLE_ELEM_CHOICES)

        box = self.card("荷载与工况名")
        g = ttk.Frame(box)
        g.pack(fill="x")
        self._check(g, 0, "计入自重 *SELFWEIGHT", "selfweight")
        self._field(g, 1, "自重工况名", "lc_sw", "", width=10, note="ASCII，例：SW")
        self._check(g, 2, "计入二期恒载 *BEAMLOAD（加劲梁均布）", "dl2")
        self._field(g, 3, "二期恒载工况名", "lc_dl2", "", width=10, note="ASCII，例：DL2")
        self._check(g, 4, "施加主缆/吊杆初始索力 *PRETENSION", "pretension")
        self._check(g, 5, "吊杆也给初拉力（不勾 = 吊杆只当弹性受拉杆，力由分析算出）", "hanger_ps")
        self._field(g, 6, "初始索力工况名", "lc_ps", "", width=10, note="ASCII，例：PS")

        box = self.card("边界设置结果")
        self._sum_bc = tk.StringVar(value="")
        ttk.Label(box, textvariable=self._sum_bc, justify="left").pack(anchor="w")

        box = self.card("说明")
        ttk.Label(box, style="Hint.TLabel", justify="left", wraplength=860,
                  text="· 塔梁固结：在塔的桥面标高处节点和主梁节点之间加 *RIGIDLINK（全 6 个自由度）。\n"
                       "· 塔梁竖向支承：只给主梁节点加竖向约束（Dz，另加 Dy 防侧向），纵向可以滑动。\n"
                       "· 鞍座刚接：塔顶主节点 + 主缆从节点，用 *RIGIDLINK 连起来（真实的鞍座是主从关系）。\n"
                       "· 工况名用英文字母/数字，导进 MIDAS 后最好认。").pack(anchor="w")
        self._refresh()

    # ==================================================================
    # 第 5 步：生成成果
    # ==================================================================
    def _default_out(self):
        try:
            return self.out_dir()
        except Exception:                                       # noqa: BLE001
            return os.path.join(os.path.dirname(os.path.abspath(__file__)), "模型文件", CODE)

    def _prefix(self):
        s = self._s()
        name = sc._txt(s.get("fname")).strip()
        if name:
            return sc.safe_name(name)
        prj = self.m.get("project") or {}
        return sc.safe_name(sc._txt(prj.get("name")).strip() or "悬索桥")

    def step_susp_export(self):
        prj = self.m.get("project") or {}
        s = self._s()
        box = self.card("成果输出（一个工程一个文件夹）")
        line = ttk.Frame(box)
        line.pack(fill="x")
        ttk.Label(line, text="输出文件夹").pack(side="left")
        self._v_out = tk.StringVar(value=sc._txt(prj.get("outdir")) or self._default_out())
        ttk.Entry(line, textvariable=self._v_out, width=62).pack(side="left", padx=(6, 8))
        ttk.Button(line, text="选择…", command=self._pick_out).pack(side="left")

        line2 = ttk.Frame(box)
        line2.pack(fill="x", pady=(6, 0))
        ttk.Label(line2, text="文件名").pack(side="left")
        self._v_name = tk.StringVar(value=self._prefix())
        ttk.Entry(line2, textvariable=self._v_name, width=26).pack(side="left", padx=(6, 14))
        ttk.Label(line2, text="编码").pack(side="left")
        self._v_enc = tk.StringVar(value="GBK（中文 ANSI，推荐）")
        ttk.Combobox(line2, textvariable=self._v_enc, width=22, state="readonly",
                     values=["GBK（中文 ANSI，推荐）", "UTF-8"]).pack(side="left", padx=(6, 0))

        line3 = ttk.Frame(box)
        line3.pack(fill="x", pady=(10, 0))
        ttk.Button(line3, text="★ 生成整套成果（模型 + 数据）",
                   command=lambda: self.on_export(False)).pack(side="left")
        ttk.Button(line3, text="只存 .mct", command=lambda: self.on_export(True)).pack(side="left", padx=(8, 0))
        ttk.Button(line3, text="复制 MCT 文本", command=self.on_copy).pack(side="left", padx=(8, 0))
        ttk.Button(line3, text="刷新预览", command=self.rerender).pack(side="left", padx=(8, 0))
        ttk.Button(line3, text="打开成果文件夹", command=self.on_open_dir).pack(side="left", padx=(8, 0))

        ttk.Label(box, style="Hint.TLabel", justify="left", wraplength=860,
                  text="「生成整套成果」一次写出 4 个文件：<文件名>.mct（导入 MIDAS 用）、"
                       "<文件名>_数据.json（可再打开接着改）、<文件名>_数据表.txt（核对用）、导入说明.txt。"
                  ).pack(anchor="w", pady=(8, 0))
        if getattr(self, "_files", None):
            made = ttk.Frame(box)
            made.pack(fill="x", pady=(8, 0))
            ttk.Label(made, text="上次生成：", style="Title.TLabel").pack(anchor="w")
            for label, path in self._files:
                ttk.Label(made, text="    %s → %s" % (label, path), style="Ok.TLabel").pack(anchor="w")

        box = self.card("MCT 预览")
        wrap = ttk.Frame(box)
        wrap.pack(fill="both", expand=True)
        self._txt = tk.Text(wrap, height=24, wrap="none", font=("Consolas", 9))
        ysb = ttk.Scrollbar(wrap, orient="vertical", command=self._txt.yview)
        xsb = ttk.Scrollbar(wrap, orient="horizontal", command=self._txt.xview)
        self._txt.configure(yscrollcommand=ysb.set, xscrollcommand=xsb.set)
        ysb.pack(side="right", fill="y")
        xsb.pack(side="bottom", fill="x")
        self._txt.pack(side="left", fill="both", expand=True)
        try:
            self._txt.insert("1.0", sc.build_mct(self.m))
        except ValueError as exc:
            self._txt.insert("1.0", "（还不能生成 MCT）\n\n%s" % exc)
        except Exception as exc:                                # noqa: BLE001
            self._txt.insert("1.0", "（生成出错）%s: %s" % (type(exc).__name__, exc))
        self._txt.configure(state="disabled")

        box = self.card("接下来在 MIDAS 里")
        ttk.Label(box, style="Hint.TLabel", justify="left", wraplength=860,
                  text="1) MIDAS Civil NX → 文件 → 导入 → MCT 命令文件，选中刚生成的 .mct。\n"
                       "2) 先看结构类型是不是「二维 X-Z 平面」、节点/单元数对不对（MCT 预览里都有）。\n"
                       "3) 算恒载工况（自重 + 二期）看主缆线形、吊杆力、主梁弯矩是否合理；"
                       "初始索力工况是成桥状态的起步值。\n"
                       "4) 成桥真实索力要按课程方法迭代（或做施工阶段分析），初拉力 H 只是估算。").pack(anchor="w")

    def _enc(self):
        return "utf-8" if "UTF" in self._v_enc.get().upper() else "gbk"

    def _pick_out(self):
        d = filedialog.askdirectory(title="选择成果输出文件夹",
                                    initialdir=self._v_out.get() or self._default_out())
        if d:
            self._v_out.set(d)

    def _apply_out(self):
        out = sc._txt(self._v_out.get()).strip() or self._default_out()
        name = sc.safe_name(self._v_name.get())
        self.m.setdefault("project", {})["outdir"] = out
        self._s()["fname"] = name
        return out, name

    def on_export(self, only_mct=False):
        probs = sc.all_issues(self.m)
        if probs:
            self._mb().showwarning("先改完这些再生成",
                                   "下面这些问题会让 MIDAS 导入报错，先改好：\n\n"
                                   + "\n".join("· " + p for p in probs[:10]))
            return
        out, name = self._apply_out()
        enc = self._enc()
        try:
            os.makedirs(out, exist_ok=True)
            if only_mct:
                path = os.path.join(out, name + ".mct")
                text = sc.build_mct(self.m)
                if enc.startswith("gbk"):
                    text = sc.gbk_safe(text)
                with open(path, "w", encoding=enc, errors="replace", newline="") as fh:
                    fh.write(text)
                files = [("MIDAS 模型命令文件（导入用）", path)]
            else:
                files = sc.write_bundle(self.m, out, name, enc)
        except ValueError as exc:
            self._mb().showwarning("参数还有问题", str(exc))
            return
        except OSError as exc:
            self._mb().showerror("写文件失败", "%s\n\n（文件夹是不是只读？或者被占用？）" % exc)
            return
        except Exception as exc:                                # noqa: BLE001
            self._mb().showerror("生成失败（程序内部错误）",
                                 "%s: %s\n\n把这段信息发给老师/助教。" % (type(exc).__name__, exc))
            return
        self._files = files
        try:
            self.wiz.saved_path = files[0][1]
        except Exception:                                       # noqa: BLE001
            pass
        self.save()
        self.rerender()
        self._mb().showinfo("已生成",
                            "模型文件（在 MIDAS 里导入它）：\n%s\n\n这个文件夹里还有：\n%s\n\n"
                            "下一步：MIDAS → 文件 → 导入 → MCT 命令文件。"
                            % (files[0][1], "\n".join("· %s" % os.path.basename(f[1]) for f in files)))

    def on_copy(self):
        try:
            text = sc.build_mct(self.m)
        except ValueError as exc:
            self._mb().showwarning("还不能生成", str(exc))
            return
        try:
            self.wiz.clipboard_clear()
            self.wiz.clipboard_append(text)
        except tk.TclError:
            return
        self.wiz.v_status.set("MCT 文本已复制到剪贴板（%d 行），可以直接粘到 MIDAS 的命令窗口运行。" % text.count("\n"))
        self.wiz.lbl_status.configure(style="Ok.TLabel")

    def on_open_dir(self):
        out, _name = self._apply_out()
        try:
            os.makedirs(out, exist_ok=True)
            os.startfile(out)                                   # noqa: S606  (Windows)
        except Exception as exc:                                # noqa: BLE001
            self._mb().showwarning("打不开文件夹", "%s\n\n%s" % (out, exc))
