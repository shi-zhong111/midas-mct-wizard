# -*- coding: utf-8 -*-
"""斜拉桥（鱼骨刺 · 成桥）流程插件 —— 界面部分。

引擎（几何 / 初拉力 / MCT / 数据表 / 成果输出）都在 cable_core.py，
那边不 import tkinter，测试和命令行可以直接调用；本文件只画五步界面 + 写成果。

挂接方式：midas_wizard.FLOW_PLUGIN_MODULES 里已经有 ("cable", "flow_cable")，
主程序 import 本模块后取 Flow 类放进「流程」下拉，不用改主程序。

五步：
  1 桥型与几何 —— 三跨、塔高、索面/索数/索距、分段（实时示意图 + 规模摘要）
  2 截面       —— sect_ui.SectionEditor（参数化截面 + DXF 自定义截面）+ 角色对应
  3 材料与斜拉索 —— 材料规格、索面积、初拉力（恒载平衡估算 / 统一值 / 逐索输入）
  4 边界与荷载 —— 塔底、桥台、塔梁连接、辅助墩、二期恒载
  5 生成成果   —— .mct + 数据.json + 数据表 + 导入说明，可预览/复制 MCT
"""

import math
import os
import tkinter as tk
from tkinter import filedialog, ttk

import cable_core as cc
import sect_ui
import wiz_common as wc
from flow_api import FlowBase

CODE = cc.CODE
LABEL = cc.LABEL
STEP_IDS = list(cc.STEP_IDS)
STEP_TITLES = list(cc.STEP_TITLES)
STEP_HINTS = list(cc.STEP_HINTS)

_HERE = os.path.dirname(os.path.abspath(__file__))
WORK_DIR = os.path.join(_HERE, "模型文件")


# ---- 引擎的薄包装：测试 / 主程序也可以直接 from flow_cable import build_mct ----
def default_model():
    return cc.default_model()


def issues(model, sid):
    return cc.issues(model, sid)


def build_mct(model):
    return cc.build_mct(model)


def data_report(model):
    return cc.data_report(model)


def export_bundle(model, out_dir, prefix, enc="gbk"):
    return cc.export_bundle(model, out_dir, prefix, enc)


class Flow(FlowBase):
    code = CODE
    label = LABEL
    step_ids = STEP_IDS
    step_titles = STEP_TITLES
    step_hints = STEP_HINTS
    last_hint = "成果已生成：.mct 直接在 MIDAS 里「文件 → 导入 → MCT 命令文件」即可。"

    # ------------------------------------------------------------------
    # 数据
    # ------------------------------------------------------------------
    def default_model(self):
        return default_model()

    def reset_model(self):
        return default_model()

    def issues(self, sid):
        return issues(self.m, sid)

    def data_report(self):
        try:
            return cc.data_report(self.m)
        except Exception:                                      # noqa: BLE001
            return ""

    def clear_title(self, sid):
        return {"geom": "桥型与几何", "section": "截面", "material": "材料与斜拉索",
                "boundary": "边界与荷载"}.get(sid, "")

    def clear(self, sid):
        d = default_model()
        m = self.m
        if sid == "geom":
            m["geom"] = dict(d["geom"])
        elif sid == "section":
            m["sections"] = [dict(s) for s in d["sections"]]
        elif sid == "material":
            m["materials"] = {k: dict(v) for k, v in d["materials"].items()}
            m["cable"] = dict(d["cable"])
        elif sid == "boundary":
            m["bnd"] = dict(d["bnd"])
            m["loads"] = dict(d["loads"])

    # ------------------------------------------------------------------
    # 小工具
    # ------------------------------------------------------------------
    def _hint(self, parent, text, wrap=800):
        ttk.Label(parent, text=text, style="Hint.TLabel", wraplength=wrap,
                  justify="left").pack(anchor="w", pady=(4, 0))

    def _param_row(self, parent, label, holder, key, unit="", width=10,
                   label_width=22, on_key=None):
        row = ttk.Frame(parent)
        row.pack(fill="x", pady=1)
        ttk.Label(row, text=label, width=label_width, anchor="w").pack(side="left")
        var = self.svar(holder, key, holder.get(key, ""))
        ent = ttk.Entry(row, textvariable=var, width=width)
        ent.pack(side="left")
        if unit:
            ttk.Label(row, text=unit, style="Hint.TLabel").pack(side="left", padx=(4, 0))
        if on_key is not None:
            ent.bind("<KeyRelease>", lambda _e: on_key())
        return ent

    def _check_row(self, parent, text, holder, key, default=False):
        var = self.bvar(holder, key, holder.get(key, default))

        def _changed():
            holder[key] = bool(var.get())
            self.save()
            self.rerender()

        chk = ttk.Checkbutton(parent, text=text, variable=var, command=_changed)
        chk.pack(anchor="w", pady=1)
        return chk

    def _combo_row(self, parent, label, holder, key, mapping, width=28,
                   label_width=22, on_change=None):
        row = ttk.Frame(parent)
        row.pack(fill="x", pady=1)
        ttk.Label(row, text=label, width=label_width, anchor="w").pack(side="left")
        cur = str(holder.get(key) or "")
        if cur not in mapping:
            cur = list(mapping.keys())[0]
        var = tk.StringVar(value=mapping[cur])
        inv = {v: k for k, v in mapping.items()}
        cb = ttk.Combobox(row, textvariable=var, values=list(mapping.values()),
                          width=width, state="readonly")
        cb.pack(side="left")

        def _apply(_e=None):
            holder[key] = inv.get(var.get(), cur)
            self.save()
            if on_change:
                on_change()
            else:
                self.rerender()

        cb.bind("<<ComboboxSelected>>", _apply)
        return cb

    # ==================================================================
    # 第 1 步：桥型与几何
    # ==================================================================
    def step_geom(self):
        m = self.m
        g = m.setdefault("geom", {})
        redraw = self._redraw

        box = self.card("三跨布置（单位：m）")
        self._param_row(box, "左边跨", g, "L_side1", "m", on_key=redraw)
        self._param_row(box, "主跨", g, "L_main", "m", on_key=redraw)
        self._param_row(box, "右边跨", g, "L_side2", "m", on_key=redraw)
        self._param_row(box, "桥面（主梁轴线）标高", g, "z_deck", "m", on_key=redraw)
        self._hint(box, "标高只是把整座桥抬高放低，取 0 就是桥面标高 0；塔高按相对桥面填。")

        box = self.card("桥塔（单位：m）")
        self._param_row(box, "桥面以上塔高", g, "H_above", "m", on_key=redraw)
        self._param_row(box, "桥面以下塔高", g, "H_below", "m", on_key=redraw)
        self._hint(box, "塔底标高 = 桥面标高 − 桥面以下塔高（默认 −24 m，塔底固结）。")

        box = self.card("斜拉索布置")
        self._combo_row(box, "索面", g, "plane", cc.PLANE_NAMES, width=30,
                        on_change=self.rerender)
        if str(g.get("plane", "double")) == "double":
            self._param_row(box, "索面横向间距", g, "B_cable", "m", on_key=redraw)
            self._hint(box, "双索面：主梁轴线上的节点先用「鱼骨刺刚性横梁」横向伸到索面位置，"
                            "索再连到横梁端部。单索面：索直接连主梁轴线节点。")
        self._param_row(box, "每塔主跨侧索数", g, "n_main", "根", width=8, on_key=redraw)
        self._param_row(box, "每塔边跨侧索数", g, "n_side", "根", width=8, on_key=redraw)
        self._param_row(box, "主跨侧索距", g, "ds", "m", on_key=redraw)
        self._param_row(box, "边跨侧索距", g, "ds_side", "m", on_key=redraw)
        self._param_row(box, "塔上首根索锚固高度", g, "h0", "m（桥面以上）", width=8, on_key=redraw)
        self._param_row(box, "塔上索锚固竖向间距", g, "hz", "m", on_key=redraw)

        box = self.card("主梁与塔柱分段")
        self._param_row(box, "主跨主梁分段数", g, "n_g_main", "段", width=8, on_key=redraw)
        self._param_row(box, "边跨主梁分段数", g, "n_g_side", "段", width=8, on_key=redraw)
        self._param_row(box, "塔柱竖向分段数", g, "n_t", "段", width=8, on_key=redraw)
        self._hint(box, "索锚点、塔位、支承位置会自动插进主梁分段里，实际单元数会比这里多一点。")

        prev = self.card("桥型示意图")
        cv = tk.Canvas(prev, height=320, bg="white", highlightthickness=1,
                       highlightbackground="#cccccc")
        cv.pack(fill="x")
        self.sketch_cv = cv
        cv.bind("<Configure>", lambda _e: redraw())

        box = self.card("模型规模（自动统计，填完上面自动更新）")
        self.sum_var = tk.StringVar(value="")
        ttk.Label(box, textvariable=self.sum_var, style="Hint.TLabel",
                  justify="left", wraplength=860).pack(anchor="w")
        self._redraw()

    def _redraw(self):
        """只重画示意图和摘要（不整页重建，免得输入框丢焦点）。"""
        try:
            self._draw_sketch()
        except Exception:                                      # noqa: BLE001
            pass
        try:
            sm = cc.summary(self.m)
            if hasattr(self, "sum_var"):
                self.sum_var.set(self._summary_text(sm))
        except Exception:                                      # noqa: BLE001
            if hasattr(self, "sum_var"):
                self.sum_var.set("（参数还没填全）")

    def _summary_text(self, sm):
        p = cc.params(self.m)
        return ("节点 %d 个，单元 %d 个：主梁 BEAM %d、桥塔 BEAM %d、鱼骨刺虚拟横梁 %d、斜拉索 TENSTR %d。\n"
                "斜拉索 %d 根（%s，每塔主跨侧 %d 根 / 边跨侧 %d 根）；塔上索锚点刚性连接 %d 处。\n"
                "跨中无索区 %.1f m；左边跨尾索到梁端 %.1f m，右边跨 %.1f m。\n"
                "主梁恒载 q = %.1f kN/m（自重 %.1f + 二期 %.1f）；初拉力估算 %.0f ~ %.0f kN。\n"
                "全长 %.1f m，塔顶标高 %.1f m，塔底标高 %.1f m。"
                % (sm["n_node"], sm["n_elem"], sm["n_girder"], sm["n_tower"], sm["n_fish"],
                   sm["n_cable_el"], sm["n_cable_item"],
                   "双索面" if p["plane"] == "double" else "单索面",
                   p["n_main"], p["n_side"], sm["n_rigid"],
                   sm["mid_gap"], sm["tail1"], sm["tail2"],
                   sm["dead"][2], sm["dead"][0], sm["dead"][1], sm["Tmin"], sm["Tmax"],
                   sm["x_end"], p["z_top"], p["z_base"]))

    def _draw_sketch(self):
        cv = getattr(self, "sketch_cv", None)
        if cv is None or not cv.winfo_exists():
            return
        cv.delete("all")
        sk = cc.sketch(self.m)
        p = sk["p"]
        x_end = max(1.0, p["x_end"])
        z_lo = min(p["z_base"], p["z_deck"])
        z_hi = max(p["z_top"], p["z_deck"])
        if z_hi - z_lo < 1e-6:
            z_hi = z_lo + 1.0

        w = cv.winfo_width()
        if w <= 1:
            w = 820
        h = cv.winfo_height()
        if h <= 1:
            h = cv.winfo_reqheight() or 320
        # 右边留出「塔高以上/以下」标注的位置，别被画布边缘切掉
        pad_l, pad_r, pad_t, pad_b = 46, 132, 34, 40
        s = min((w - pad_l - pad_r) / x_end, (h - pad_t - pad_b) / (z_hi - z_lo))

        def P(x, z):
            return (pad_l + (x - 0.0) * s, h - pad_b - (z - z_lo) * s)

        # 地基/塔底
        cv.create_line(P(0, z_lo)[0], P(0, z_lo)[1], P(x_end, z_lo)[0], P(x_end, z_lo)[1],
                       fill="#bbbbbb", dash=(4, 4))
        # 主梁
        cv.create_line(*P(0, p["z_deck"]), *P(x_end, p["z_deck"]), fill="#1f4e79", width=3)
        # 桥塔
        for (xt, zb, zt) in sk["towers"]:
            cv.create_line(*P(xt, zb), *P(xt, zt), fill="#7f3f00", width=4)
            cv.create_rectangle(P(xt, zb)[0] - 8, P(xt, zb)[1] - 4,
                                P(xt, zb)[0] + 8, P(xt, zb)[1] + 4,
                                fill="#7f3f00", outline="")
        # 斜拉索
        for (xt, zt, xg, zg) in sk["cables"]:
            cv.create_line(*P(xt, zt), *P(xg, zg), fill="#9a9a9a")
        # 支座（桥台）
        for x in (0.0, x_end):
            if abs((self.m.get("bnd") or {}).get("abut_dz", True)):
                px, py = P(x, p["z_deck"])
                cv.create_polygon(px, py, px - 7, py + 12, px + 7, py + 12,
                                  outline="#444", fill="#e8f0f8")
        # 跨径标注
        ylab = h - pad_b + 18
        ydeck = P(0, p["z_deck"])[1]
        for (x1, x2, label) in ((0.0, p["xL"], "边跨 %.1f m" % p["Ls1"]),
                                (p["xL"], p["xR"], "主跨 %.1f m" % p["Lm"]),
                                (p["xR"], x_end, "边跨 %.1f m" % p["Ls2"])):
            if x2 <= x1:
                continue
            pa, pb = P(x1, p["z_deck"]), P(x2, p["z_deck"])
            yy = max(ylab, min(ydeck + 22, h - 6))
            cv.create_line(pa[0], yy, pb[0], yy, arrow="both", fill="#666")
            cv.create_text((pa[0] + pb[0]) / 2, yy - 8, text=label, fill="#666")
        # 塔高标注
        tx = P(p["xR"], p["z_top"])[0]
        cv.create_line(tx + 16, P(0, p["z_top"])[1], tx + 16, P(0, p["z_deck"])[1],
                       arrow="both", fill="#7f3f00")
        cv.create_text(tx + 22, (P(0, p["z_top"])[1] + P(0, p["z_deck"])[1]) / 2,
                       text="塔高以上 %.1f m" % p["H_above"], anchor="w", fill="#7f3f00")
        if p["H_below"] > 0:
            cv.create_line(tx + 16, P(0, p["z_deck"])[1], tx + 16, P(0, p["z_base"])[1],
                           arrow="both", fill="#7f3f00")
            cv.create_text(tx + 22, (P(0, p["z_deck"])[1] + P(0, p["z_base"])[1]) / 2,
                           text="塔高以下 %.1f m" % p["H_below"], anchor="w", fill="#7f3f00")
        # 左上角说明
        p_label = cc.PLANE_NAMES.get(p["plane"], "")
        cv.create_text(pad_l, 12, anchor="w", fill="#333",
                       text="%s ｜ 每塔主跨侧 %d 根 / 边跨侧 %d 根 ｜ 索距 %.1f m"
                            % (p_label.split("（")[0], p["n_main"], p["n_side"], p["ds"]))

    # ==================================================================
    # 第 2 步：截面
    # ==================================================================
    def step_section(self):
        m = self.m
        m.setdefault("sections", [])
        box = self.card("截面角色（哪个截面给主梁 / 桥塔用）")
        secs = m.get("sections") or []
        names = ["%d · %s" % (i + 1, wc.txt(s.get("name")) or "截面") for i, s in enumerate(secs)]
        for role, label in (("girder", "主梁截面"), ("tower", "桥塔截面")):
            row = ttk.Frame(box)
            row.pack(fill="x", pady=1)
            ttk.Label(row, text=label, width=14, anchor="w").pack(side="left")
            cur = cc.role_index(m, role)
            var = tk.StringVar(value=(names[cur - 1] if 0 < cur <= len(names) else "（未指定）"))
            cb = ttk.Combobox(row, textvariable=var, values=names, width=30, state="readonly")
            cb.pack(side="left")

            def _apply(_e=None, _role=role, _var=var):
                if _var.get() not in names:
                    return
                idx = names.index(_var.get())
                for s in m.get("sections") or []:
                    if s.get("role") == _role:
                        s.pop("role", None)
                m["sections"][idx]["role"] = _role
                self.save()
                self.rerender()

            cb.bind("<<ComboboxSelected>>", _apply)
        row = ttk.Frame(box)
        row.pack(fill="x", pady=1)
        ttk.Label(row, text="鱼骨刺横梁", width=14, anchor="w").pack(side="left")
        fi = cc.role_index(m, "fishbone")
        ttk.Label(row, text=("截面 %d · %s（虚拟截面，程序自动加，不用填）"
                             % (fi, wc.txt(m["sections"][fi - 1].get("name")))) if fi
                  else "（缺：点下面的按钮补一个）",
                  style="Hint.TLabel").pack(side="left")
        bar = ttk.Frame(box)
        bar.pack(fill="x", pady=(8, 0))
        ttk.Button(bar, text="＋ 补齐缺失的默认截面（主梁/桥塔/鱼骨刺）",
                   command=self._ensure_sections).pack(side="left")
        ttk.Button(bar, text="把主梁/桥塔/鱼骨刺恢复成默认尺寸",
                   command=self._reset_role_sections).pack(side="left", padx=(8, 0))
        self._hint(box, "改截面形状/尺寸在下面的截面库里；改完「流体」会自动重算面积与恒载。"
                        "鱼骨刺横梁是虚拟梁：弹模取主梁 1e4 倍，容重和线膨胀系数为 0（课件 3.2.4）。")

        box2 = self.card("截面库（参数化截面 + DXF 自定义截面）")
        ed = sect_ui.SectionEditor(
            self, self.m, allow_cad=True, allow_taper=False,
            note="主梁、桥塔用真实截面：例如主梁选「B · 箱形」，按 高/宽/腹板厚/上翼厚/倒角/下翼厚 填。"
                 "也可以「导入自定义截面（DXF）」：轮廓画在 CAD 的 XY 平面上、要闭合，孔洞照常画内轮廓。")
        ed.on_change = self.rerender
        ed.render(box2)

        box3 = self.card("斜拉索截面（自动生成，不用在这里填）")
        cs = cc.cable_section(m)
        a = cc.cable_area(m)
        ttk.Label(box3, text="斜拉索等效圆截面：面积 %.6g m²，等效直径 %.1f mm"
                             % (a, (math.sqrt(4.0 * a / math.pi) * 1000.0) if a > 0 else 0.0),
                  style="Ok.TLabel").pack(anchor="w")
        self._hint(box3, "索用 TENSTR 索单元，单元刚度只用面积 A，所以程序按面积自动生成一个等效圆截面；"
                         "要改索面积到第 3 步「斜拉索截面面积」里改。")

    def _ensure_sections(self):
        added = cc.ensure_roles(self.m)
        self.save()
        self.rerender()
        if added:
            self.info("已补截面", "补了：%s" % "、".join(
                {"girder": "主梁", "tower": "桥塔", "fishbone": "鱼骨刺横梁"}[k] for k in added))

    def _reset_role_sections(self):
        if not self.ask("恢复默认截面", "把主梁 / 桥塔 / 鱼骨刺三个截面恢复成默认尺寸吗？\n"
                                       "（自己加的其它截面不动）"):
            return
        secs = self.m.setdefault("sections", [])
        for role, d in cc.DEFAULT_ROLE_SECTIONS.items():
            i = cc.role_index(self.m, role)
            if i:
                secs[i - 1].update({k: (list(v) if isinstance(v, list) else v)
                                    for k, v in d.items()})
            else:
                secs.append({k: (list(v) if isinstance(v, list) else v) for k, v in d.items()})
        self.save()
        self.rerender()

    # ==================================================================
    # 第 3 步：材料与斜拉索
    # ==================================================================
    def step_material(self):
        m = self.m
        ms = m.setdefault("materials", {})
        for key in ("girder", "tower", "cable"):
            ms.setdefault(key, dict(default_model()["materials"][key]))
        cfg = m.setdefault("cable", dict(default_model()["cable"]))

        box = self.card("材料（选规格会自动填弹模 / 泊松比 / 容重 / 线膨胀系数）")
        self._mat_row(box, "girder", "主梁", cc.CONC_SPECS)
        self._mat_row(box, "tower", "桥塔", cc.CONC_SPECS)
        self._mat_row(box, "cable", "斜拉索", cc.CABLE_SPECS)
        self._hint(box, "混凝土按 JTG 3362-18 弹模；斜拉索按 1860 级钢绞线 E=1.95×10⁵ MPa。"
                        "也可以直接改右边的数值（改完规格那一栏就不再代表实际值）。")

        box = self.card("斜拉索截面面积")
        row = ttk.Frame(box)
        row.pack(fill="x")
        ttk.Label(row, text="单根索面积 A", width=16, anchor="w").pack(side="left")
        ttk.Entry(row, textvariable=self.svar(cfg, "A", cfg.get("A", "")), width=12).pack(side="left")
        ttk.Label(row, text="m²").pack(side="left", padx=(4, 16))
        a = cc.cable_area(m)
        ttk.Label(row, text="等效直径 %.1f mm" % ((math.sqrt(4.0 * a / math.pi) * 1000.0) if a > 0 else 0.0),
                  style="Hint.TLabel").pack(side="left")
        row2 = ttk.Frame(box)
        row2.pack(fill="x", pady=(4, 0))
        ttk.Label(row2, text="或按直径输入", width=16, anchor="w").pack(side="left")
        self.v_dia = tk.StringVar(value="")
        ttk.Entry(row2, textvariable=self.v_dia, width=12).pack(side="left")
        ttk.Label(row2, text="mm").pack(side="left", padx=(4, 8))
        ttk.Button(row2, text="按直径换算面积", command=self._apply_dia).pack(side="left")
        self._hint(box, "默认 8000 mm²（≈61 股 φ15.2 钢绞线）；37 股 ≈ 5000 mm² = 0.005 m²。"
                        "按默认恒载估出来的索力在 2000~5000 kN 量级，8000 mm² 对应 250~620 MPa，量级合理。")

        box = self.card("斜拉索初拉力")
        row = ttk.Frame(box)
        row.pack(fill="x")
        vm = tk.StringVar(value=str(cfg.get("mode") or "balance"))
        for key, label in cc.TENSION_NAMES.items():
            ttk.Radiobutton(row, text=label, value=key, variable=vm,
                            command=lambda: self._set_tension_mode(vm.get())).pack(side="left", padx=(0, 16))
        row2 = ttk.Frame(box)
        row2.pack(fill="x", pady=(6, 0))
        ttk.Label(row2, text="统一初拉力", width=16, anchor="w").pack(side="left")
        ttk.Entry(row2, textvariable=self.svar(cfg, "T_uniform", cfg.get("T_uniform", "")),
                  width=12).pack(side="left")
        ttk.Label(row2, text="kN（「统一值」方式才用）").pack(side="left", padx=(4, 20))
        ttk.Label(row2, text="估算系数").pack(side="left")
        ttk.Entry(row2, textvariable=self.svar(cfg, "factor", cfg.get("factor", "")),
                  width=8).pack(side="left", padx=(4, 0))
        bar = ttk.Frame(box)
        bar.pack(fill="x", pady=(8, 0))
        ttk.Button(bar, text="刷新索表", command=self._refresh_cables).pack(side="left")
        ttk.Button(bar, text="把估算值写成逐索输入",
                   command=self._fill_manual).pack(side="left", padx=(8, 0))
        self._hint(box, "「按恒载平衡估算」：第 k 根索承担的分仓恒载 q·l_trib 由索的竖向分力平衡，"
                        "T = q·l_trib /（索面数 × sinα）。这是成桥索力的初值，精调请在 MIDAS 里做"
                        "（未知荷载系数法/调索，本向导不做索力优化）。")

        recs = cc.sync_cable_items(m)
        box = self.card("每根索的初拉力（共 %d 处索）" % len(recs))
        calc = {it["key"]: it for it in cc.cable_items(m)}
        mode = str(cfg.get("mode") or "balance")
        tb = self.table(box, ["编号", "位置 x (m)", "塔上锚固 (m)", "倾角 (°)", "索长 (m)",
                              "估算 (kN)", "采用 (kN)"])
        for r, rec in enumerate(recs, start=1):
            it = calc.get(rec["key"], {})
            ttk.Label(tb, text=str(rec.get("key"))).grid(row=r, column=0, sticky="w", padx=(0, 10), pady=1)
            for c, v in enumerate((rec.get("x"), rec.get("z_t"),
                                   math.degrees(float(it.get("alpha") or 0.0)),
                                   rec.get("length")), start=1):
                ttk.Label(tb, text="%.2f" % float(v or 0.0)).grid(row=r, column=c, sticky="w", padx=(0, 10))
            ttk.Label(tb, text="%.1f" % float(it.get("t_est") or 0.0)).grid(
                row=r, column=5, sticky="w", padx=(0, 10))
            if mode == "manual":
                ttk.Entry(tb, textvariable=self.svar(rec, "T", rec.get("T", "")),
                          width=10).grid(row=r, column=6, sticky="w", padx=(0, 10))
            else:
                ttk.Label(tb, text="%.1f" % float(it.get("T") or 0.0)).grid(
                    row=r, column=6, sticky="w", padx=(0, 10))
        # 标题里的数量补上（card 已经画好了，这里改标签文本更省事，所以直接用提示行）
        self._hint(box, "现在一共 %d 处索。选「逐索输入」时上表最后一列可以改；"
                        "双索面每一处是左右对称的 2 个索单元，共用一个初拉力。"
                   % len(recs))

    def _mat_row(self, parent, key, label, specs):
        ms = self.m.setdefault("materials", {})
        mat = ms.setdefault(key, {})
        row = ttk.Frame(parent)
        row.pack(fill="x", pady=2)
        ttk.Label(row, text=label, width=8, anchor="w").pack(side="left")
        names = list(specs.keys())
        cur = wc.txt(mat.get("spec"))
        var = tk.StringVar(value=cur if cur in names else names[0])
        cb = ttk.Combobox(row, textvariable=var, values=names, width=12, state="readonly")
        cb.pack(side="left")

        def _apply(_e=None):
            vals = specs.get(var.get())
            if not vals:
                return
            mat["spec"] = var.get()
            mat["elast"], mat["poisn"], mat["den"], mat["thermal"] = vals
            self.save()
            self.rerender()

        cb.bind("<<ComboboxSelected>>", _apply)
        for lab, k, wd in (("E (kN/m²)", "elast", 12), ("ν", "poisn", 6),
                           ("容重 (kN/m³)", "den", 7), ("α (1/℃)", "thermal", 10)):
            ttk.Label(row, text=lab).pack(side="left", padx=(12, 2))
            ttk.Entry(row, textvariable=self.svar(mat, k, mat.get(k, "")), width=wd).pack(side="left")

    def _apply_dia(self):
        try:
            d_mm = float(wc.txt(self.v_dia.get()).strip())
        except ValueError:
            self.warn("直径不对", "直径要填数字（单位 mm）。")
            return
        if d_mm <= 0:
            self.warn("直径不对", "直径要大于 0。")
            return
        a = math.pi * (d_mm / 1000.0) ** 2 / 4.0
        self.m.setdefault("cable", {})["A"] = wc.numg(a)
        self.save()
        self.rerender()

    def _set_tension_mode(self, mode):
        self.m.setdefault("cable", {})["mode"] = mode
        self.save()
        self.rerender()

    def _refresh_cables(self):
        cc.sync_cable_items(self.m)
        self.save()
        self.rerender()

    def _fill_manual(self):
        m = self.m
        cfg = m.setdefault("cable", {})
        cfg["mode"] = "manual"
        est = {it["key"]: it["t_est"] for it in cc.cable_items(m)}
        recs = cc.sync_cable_items(m)
        for rec in recs:
            if rec["key"] in est:
                rec["T"] = wc.num(round(est[rec["key"]], 1))
        self.save()
        self.rerender()

    # ==================================================================
    # 第 4 步：边界与荷载
    # ==================================================================
    def step_boundary(self):
        m = self.m
        bnd = m.setdefault("bnd", dict(default_model()["bnd"]))
        loads = m.setdefault("loads", dict(default_model()["loads"]))

        box = self.card("塔底与桥台")
        self._check_row(box, "塔底固结（六个自由度全约束 111111）", bnd, "tower_fix", True)
        self._check_row(box, "两端桥台竖向支承（Dz）", bnd, "abut_dz", True)
        self._check_row(box, "两端桥台横向约束（Dy）—— 单索面/飘浮体系建议勾上", bnd, "abut_dy", False)

        box = self.card("塔梁连接")
        self._combo_row(box, "连接方式", bnd, "deck_link", cc.LINK_NAMES, width=30,
                        on_change=self.rerender)
        mode = str(bnd.get("deck_link") or "rigid")
        if mode in ("semi", "float"):
            row = ttk.Frame(box)
            row.pack(fill="x", pady=(4, 0))
            ttk.Label(row, text="竖向/横向刚度", width=16, anchor="w").pack(side="left")
            ttk.Entry(row, textvariable=self.svar(bnd, "kd", bnd.get("kd", "")), width=12).pack(side="left")
            ttk.Label(row, text="kN/m（给大值 = 刚性连接）").pack(side="left", padx=(4, 0))
            if mode == "semi":
                row2 = ttk.Frame(box)
                row2.pack(fill="x", pady=(2, 0))
                ttk.Label(row2, text="纵向弹簧刚度", width=16, anchor="w").pack(side="left")
                ttk.Entry(row2, textvariable=self.svar(bnd, "kx", bnd.get("kx", "")),
                          width=12).pack(side="left")
                ttk.Label(row2, text="kN/m（半飘浮）").pack(side="left", padx=(4, 0))
        self._hint(box, "固结 = *RIGIDLINK（塔梁节点六个自由度刚接）；"
                        "半飘浮/飘浮 = *ELASTICLINK（竖向横向给大刚度，纵向按弹簧或完全释放）。")

        box = self.card("辅助墩")
        self._check_row(box, "在边跨设辅助墩", bnd, "aux_on", False)
        if bnd.get("aux_on"):
            self._param_row(box, "距梁端距离", bnd, "aux_a", "m", width=8, label_width=16)
            self._check_row(box, "辅助墩同时约束横向（Dy）", bnd, "aux_dy", False)

        box = self.card("荷载")
        row = ttk.Frame(box)
        row.pack(fill="x")
        ttk.Label(row, text="二期恒载 q₂", width=16, anchor="w").pack(side="left")
        ttk.Entry(row, textvariable=self.svar(loads, "q2", loads.get("q2", "")), width=12).pack(side="left")
        ttk.Label(row, text="kN/m（填正值，程序按向下施加在主梁全部单元）").pack(side="left", padx=(4, 0))
        self._check_row(box, "计入自重（*SELFWEIGHT，Z = −1）", loads, "sw_on", True)

        box = self.card("边界与荷载汇总")
        try:
            sm = cc.summary(m)
            geo = cc.build_geometry(m)
            txt_ = []
            txt_.append("塔底：%s" % ("固结 111111 × 2" if bnd.get("tower_fix", True) else "未约束"))
            txt_.append("塔梁连接：%s" % cc.LINK_NAMES.get(str(bnd.get("deck_link") or "rigid"), "?"))
            txt_.append("桥台：%s%s" % ("竖向" if bnd.get("abut_dz", True) else "无竖向",
                                        " + 横向" if bnd.get("abut_dy") else ""))
            txt_.append("辅助墩：%s" % (("距梁端 %s m" % bnd.get("aux_a")) if bnd.get("aux_on") else "不设"))
            txt_.append("约束条数：%d 条（*CONSTRAINT）" % len(geo["supports"]))
            txt_.append("刚性连接 %d 处，弹性连接 %d 处" % (len(geo["rigid_links"]), len(geo["elastic_links"])))
            txt_.append("二期恒载 %.1f kN/m；自重 %s" % (float(cc._f(loads.get("q2"), 0.0)),
                                                       "计入" if loads.get("sw_on", True) else "不计入"))
            txt_.append("主梁恒载合计 q = %.1f kN/m（自重 %.1f + 二期 %.1f）"
                        % (sm["dead"][2], sm["dead"][0], sm["dead"][1]))
            ttk.Label(box, text="\n".join(txt_), style="Hint.TLabel",
                      justify="left", wraplength=820).pack(anchor="w")
        except Exception:                                      # noqa: BLE001
            ttk.Label(box, text="（参数还不完整，先回前面的步骤把几何填好）",
                      style="Hint.TLabel").pack(anchor="w")

    # ==================================================================
    # 第 5 步：生成成果
    # ==================================================================
    def step_export(self):
        m = self.m
        prj = m.setdefault("project", {})
        if not hasattr(self, "v_outdir"):
            self.v_outdir = tk.StringVar(
                value=prj.get("outdir") or os.path.join(WORK_DIR, "斜拉桥成果"))
        if not hasattr(self, "v_fname"):
            self.v_fname = tk.StringVar(value=prj.get("name") or "斜拉桥模型")

        box = self.card("生成成果")
        line0 = ttk.Frame(box)
        line0.pack(fill="x")
        ttk.Label(line0, text="输出文件夹").pack(side="left")
        ttk.Entry(line0, textvariable=self.v_outdir, width=58).pack(side="left", padx=(6, 6))
        ttk.Button(line0, text="选择…", command=self._pick_dir).pack(side="left")
        ttk.Button(line0, text="打开", command=self._open_dir).pack(side="left", padx=(6, 0))

        line1 = ttk.Frame(box)
        line1.pack(fill="x", pady=(8, 0))
        ttk.Label(line1, text="文件名").pack(side="left")
        ttk.Entry(line1, textvariable=self.v_fname, width=28).pack(side="left", padx=(6, 14))

        line2 = ttk.Frame(box)
        line2.pack(fill="x", pady=(10, 0))
        ttk.Button(line2, text="① 生成成果（MCT + 数据 + 说明）",
                   command=self.on_export).pack(side="left")
        ttk.Button(line2, text="② 预览 MCT", command=self.on_preview).pack(side="left", padx=(8, 0))
        ttk.Button(line2, text="③ 复制 MCT 文本", command=self.on_copy_mct).pack(side="left", padx=(8, 0))

        self._analysis_card()
        self._hint(box, "生成 4 个文件：<文件名>.mct（GBK）、<文件名>_数据.json、"
                        "<文件名>_数据表.txt、导入说明.txt。\n"
                        "MIDAS 里导入：文件 → 导入 → MCT 命令文件；也可以复制 MCT 文本贴到「工具 → 命令窗口」运行。")

        box = self.card("模型摘要")
        try:
            sm = cc.summary(m)
            ttk.Label(box, text=("节点 %d、单元 %d（主梁 %d / 塔 %d / 鱼骨刺 %d / 索 %d）；"
                                 "索 %d 根，初拉力 %.0f~%.0f kN；塔梁连接：%s"
                                 % (sm["n_node"], sm["n_elem"], sm["n_girder"], sm["n_tower"],
                                    sm["n_fish"], sm["n_cable_el"], sm["n_cable_item"],
                                    sm["Tmin"], sm["Tmax"],
                                    cc.LINK_NAMES.get(str((m.get("bnd") or {}).get("deck_link")), ""))),
                      style="Ok.TLabel", wraplength=860, justify="left").pack(anchor="w")
        except Exception:                                      # noqa: BLE001
            pass

        box = self.card("生成记录 / MCT 预览")
        self.out_log = tk.Text(box, height=18, wrap="none", font=("Consolas", 9))
        sb = ttk.Scrollbar(box, orient="vertical", command=self.out_log.yview)
        self.out_log.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.out_log.pack(side="left", fill="both", expand=True)
        self.out_log.insert("1.0", "（这里显示生成结果。先点 ① 生成成果；点 ② 可以看要导入的 MCT 文本。）")
        self.out_log.configure(state="disabled")

    # ---------------- 分析设置（实验功能，默认不写） ----------------
    def _an_get(self, kind, key, default=""):
        d = ((self.m.get("analysis") or {}).get(kind) or {})
        v = d.get(key)
        return default if v is None else v

    def _an_var(self, kind, key, default=""):
        v = tk.StringVar(value=wc.txt(self._an_get(kind, key, default)))

        def write(*_):
            an = self.m.setdefault("analysis", {})
            an.setdefault(kind, {})[key] = v.get()

        v.trace_add("write", write)
        return v

    def _an_bvar(self, kind, key, default=False):
        v = tk.BooleanVar(value=bool(self._an_get(kind, key, default)))

        def write(*_):
            an = self.m.setdefault("analysis", {})
            an.setdefault(kind, {})[key] = bool(v.get())

        v.trace_add("write", write)
        return v

    def _analysis_card(self):
        box = self.card("分析设置（可选）：模态 / 屈曲 —— 实验功能")
        ttk.Label(box, text="照 MIDAS 官方 MCT 手册字段写；本机没有可对照的导出样本，所以默认不勾："
                            "不勾就一个字也不写，生成结果和以前完全一样。导入报错请把命令行号发作者。",
                  style="Hint.TLabel", wraplength=860, justify="left").pack(anchor="w", pady=(0, 6))
        r3 = ttk.Frame(box); r3.pack(fill="x", pady=(4, 0))
        ttk.Checkbutton(r3, text="模态分析（特征值，第 07 讲的斜拉桥模态算例）",
                        variable=self._an_bvar("eigen", "on", False)).pack(side="left")
        ttk.Label(r3, text="提取阶数").pack(side="left", padx=(10, 2))
        ttk.Entry(r3, textvariable=self._an_var("eigen", "n", "30"), width=6).pack(side="left", padx=(0, 10))
        ttk.Checkbutton(r3, text="旧版 4 字段写法", variable=self._an_bvar("eigen", "legacy", False)).pack(side="left")
        r4 = ttk.Frame(box); r4.pack(fill="x", pady=(6, 0))
        ttk.Checkbutton(r4, text="屈曲分析（第 07 讲）",
                        variable=self._an_bvar("buckle", "on", False)).pack(side="left")
        ttk.Label(r4, text="模态数").pack(side="left", padx=(10, 2))
        ttk.Entry(r4, textvariable=self._an_var("buckle", "n", "3"), width=6).pack(side="left", padx=(0, 10))
        ttk.Label(r4, text="可变荷载（工况名 系数，例：%s 1.0）" % cc.LC_DL).pack(side="left")
        ttk.Entry(r4, textvariable=self._an_var("buckle", "lcs", ""), width=26).pack(side="left", padx=(6, 0))

    def _log(self, text):
        if not hasattr(self, "out_log"):
            return
        self.out_log.configure(state="normal")
        self.out_log.delete("1.0", "end")
        self.out_log.insert("1.0", text)
        self.out_log.configure(state="disabled")

    def _pick_dir(self):
        d = filedialog.askdirectory(title="选择成果文件夹", initialdir=self.v_outdir.get() or WORK_DIR)
        if d:
            self.v_outdir.set(d)
            self.m.setdefault("project", {})["outdir"] = d

    def _open_dir(self):
        d = self.v_outdir.get().strip()
        if not d:
            return
        os.makedirs(d, exist_ok=True)
        try:
            os.startfile(d)                                    # noqa: S606  （Windows）
        except Exception as exc:                               # noqa: BLE001
            self.warn("打不开文件夹", str(exc))

    def on_export(self):
        m = self.m
        errs = cc.all_issues(m)
        if errs:
            self.warn("还不能生成", "先修正这些问题：\n\n" + "\n".join("· " + e for e in errs[:10]))
            return
        outdir = wc.txt(self.v_outdir.get()).strip() or os.path.join(WORK_DIR, "斜拉桥成果")
        prefix = wc.txt(self.v_fname.get()).strip() or "斜拉桥模型"
        prj = m.setdefault("project", {})
        prj["outdir"] = outdir
        prj["name"] = prefix
        try:
            files = cc.export_bundle(m, outdir, prefix, enc="gbk")
        except Exception as exc:                               # noqa: BLE001
            self.report_error("生成失败", exc)
            return
        self.save()
        lines = ["已生成 %d 个文件：" % len(files), ""]
        for label, path in files:
            lines.append("  %s" % label)
            lines.append("      %s" % path)
        lines.append("")
        lines.append("下一步：在 MIDAS Civil NX 里「文件 → 导入 → MCT 命令文件」，选上面的 .mct。")
        lines.append("导入后核对：斜拉索是「索」单元（TENSTR）、初拉力工况 %s 的力都是正值、"
                     "鱼骨刺横梁弹模是主梁的 1e4 倍且容重为 0。" % cc.LC_PRE)
        self._log("\n".join(lines))
        self.info("已生成", "写好 %d 个文件：\n%s\n\n在 MIDAS 里：文件 → 导入 → MCT 命令文件。"
                  % (len(files), "\n".join(path for _l, path in files)))

    def on_preview(self):
        try:
            self._log(cc.build_mct(self.m))
        except Exception as exc:                               # noqa: BLE001
            self.report_error("生成 MCT 失败", exc)

    def on_copy_mct(self):
        try:
            text = cc.build_mct(self.m)
        except Exception as exc:                               # noqa: BLE001
            self.report_error("生成 MCT 失败", exc)
            return
        try:
            self.wiz.clipboard_clear()
            self.wiz.clipboard_append(text)
        except Exception as exc:                               # noqa: BLE001
            self.warn("复制失败", str(exc))
            return
        self.info("已复制", "MCT 文本（%d 字符）已复制到剪贴板。\n\n"
                            "在 MIDAS 里打开「工具 → 命令窗口」，粘贴后点运行即可。" % len(text))
