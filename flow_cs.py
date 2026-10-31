# -*- coding: utf-8 -*-
r"""施工阶段梁桥 —— 流程插件（界面部分）。

这个文件只负责「一步一步填数值」的界面；真正的建模计算在 cs_core.py（纯计算、不依赖 tkinter）。
两种子类型（第 1 步单选切换）：
  A) 连续刚构 · 悬臂浇筑：跨径布置 / 墩高 / 变截面主梁 / 每墩悬臂节段数 / 合龙段
  B) 简支变连续：跨径 / 预制 T 梁 / 湿接缝 / 临时体系转换

界面上的每一步都直接写进 self.m（主窗口的 model），存盘/切换流程不会丢。
"""
import math
import os
import subprocess
import sys
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import wiz_common as wc
import sect_ui
from flow_api import FlowBase

import cs_core as core

CODE = "cs"
LABEL = "施工阶段梁桥（连续刚构 / 简支变连续）"

STEP_IDS = ["cs_type", "cs_section", "cs_stage", "cs_check", "cs_export"]
STEP_TITLES = ["桥型与跨径布置", "截面与材料", "施工阶段", "自检（组 / 边界 / 阶段表）", "生成成果"]
STEP_HINTS = [
    "先选子类型，再填跨径和主要尺寸：连续刚构填悬臂节段数 / 0 号块 / 合龙段，简支变连续填湿接缝长度。",
    "填主梁和墩身的截面尺寸。连续刚构的主梁是变截面：只要填「墩顶」「跨中」两组尺寸，中间自动过渡。",
    "施工阶段的划分程序已经排好了，你只需要改每个阶段持续几天；湿重会按「新浇节段面积×容重」自动加、硬化时自动卸。",
    "生成前先在这里核对：施工阶段表、结构组、边界组的引用关系都列出来了，有问题会红字提示。",
    "点「生成 MCT」写出 .mct 和 _数据表.txt，然后在 MIDAS Civil NX 里：文件 → 导入 → MCT 命令文件。",
]

CLEAR_KEYS = {
    "cs_type": ("spans", "n_seg", "l_tab", "l_close", "n_div", "l_joint"),
    "cs_section": ("h_top", "h_mid", "b_girder", "t_top", "t_bot_top", "t_bot_mid",
                   "t_web_top", "t_web_mid", "h_pier", "b_pier", "t_pier", "n_pier",
                   "girder_mat", "pier_mat", "mat", "gamma",
                   "h_girder", "t_web", "t_flange"),
    "cs_stage": ("creep", "humidity", "q2nd"),
}
CLEAR_TITLES = {"cs_type": "桥型与跨径布置", "cs_section": "截面与材料", "cs_stage": "施工阶段"}


class Flow(FlowBase):
    code = CODE
    label = LABEL
    step_ids = STEP_IDS
    step_titles = STEP_TITLES
    step_hints = STEP_HINTS
    last_hint = "MCT 文件已准备好：可以生成到文件夹、复制文本，再到 MIDAS 里导入。"

    # ---------------- 数据 ----------------
    def default_model(self):
        return core.default_model()

    def reset_model(self):
        return core.default_model()

    def issues(self, sid):
        return core.issues(self.m, sid)

    def data_report(self):
        try:
            return core.data_report(self.m)
        except Exception as exc:                       # noqa: BLE001
            return "数据表生成失败：%s: %s" % (type(exc).__name__, exc)

    def clear_title(self, sid):
        return CLEAR_TITLES.get(sid, "")

    def clear(self, sid):
        keys = CLEAR_KEYS.get(sid) or ()
        if not keys:
            return
        sub = self.m.get("subtype") or core.SUB_FCM
        dflt = core.default_model()[sub]
        d = self.m.setdefault(sub, {})
        for k in keys:
            if k in dflt:
                d[k] = dflt[k]
        if sid == "cs_stage":
            d["days"] = dict(dflt.get("days") or {})

    # ---------------- 小工具 ----------------
    def _p(self):
        """当前子类型的参数字典（界面直接读写这里）。"""
        st = self.m.get("subtype") or core.SUB_FCM
        self.m["subtype"] = st
        return self.m.setdefault(st, dict(core.default_model()[st]))

    def _row(self, parent, row, label, var=None, width=10, unit="", hint=""):
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=3)
        if var is not None:
            ttk.Entry(parent, textvariable=var, width=width).grid(
                row=row, column=1, sticky="w", padx=(8, 6), pady=3)
        if unit:
            ttk.Label(parent, text=unit, style="Hint.TLabel").grid(row=row, column=2, sticky="w")
        if hint:
            ttk.Label(parent, text=hint, style="Opt.TLabel").grid(row=row, column=3, sticky="w", padx=(10, 0))

    def _row_combo(self, parent, row, label, var, values, width=10, unit="", hint=""):
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=3)
        self.combo(parent, var, values, width=width, row=row, col=1)
        if unit:
            ttk.Label(parent, text=unit, style="Hint.TLabel").grid(row=row, column=2, sticky="w", padx=(4, 0))
        if hint:
            ttk.Label(parent, text=hint, style="Opt.TLabel").grid(row=row, column=3, sticky="w", padx=(10, 0))

    def _canvas(self, box, height=210):
        c = tk.Canvas(box, height=height, background="white",
                      highlightthickness=1, highlightbackground="#cfcfcf")
        c.pack(fill="x", expand=False, pady=(2, 4))
        c.bind("<Configure>", lambda _e: self._draw(c))
        self.form.after(30, lambda: self._draw(c))
        return c

    @staticmethod
    def _safe_name(s):
        out = "".join(ch for ch in str(s) if ch not in '\\/:*?"<>|').strip()
        return out or "施工阶段梁桥"

    # ---------------- 第 1 步：桥型与跨径布置 ----------------
    def step_cs_type(self):
        p = self._p()

        box = self.card("子类型（二选一）")
        cur = self.m.get("subtype") or core.SUB_FCM
        var = tk.StringVar(value=cur)

        def pick():
            self.m["subtype"] = var.get()
            self.save()
            self.rerender()

        for code, lab in core.SUB_TYPES:
            ttk.Radiobutton(box, text=lab, variable=var, value=code, command=pick).pack(anchor="w", pady=2)
        ttk.Label(box, text="连续刚构：墩梁固结、悬臂浇筑、中跨/边跨合龙；简支变连续：先预制架设、再浇湿接缝、"
                            "撤临时体系完成体系转换。",
                  style="Hint.TLabel", wraplength=760, justify="left").pack(anchor="w", pady=(6, 0))

        c = self._canvas(self.card("桥型示意图（按当前参数自动画）"))

        if cur == core.SUB_FCM:
            box = self.card("跨径与悬臂划分")
            self._row(box, 0, "跨径布置", self.svar(p, "spans"), 22, "m",
                      "写成 60+100+60（边跨+主跨+边跨，至少 3 跨）")
            self._row(box, 1, "每个悬臂方向的节段数", self.svar(p, "n_seg"), 8, "个",
                      "每个节段 = 一个施工阶段（12 个就是 12 个 CS）")
            self._row(box, 2, "0 号块长度", self.svar(p, "l_tab"), 8, "m", "墩顶一次浇筑的块件，左右对称")
            self._row(box, 3, "合龙段长度", self.svar(p, "l_close"), 8, "m", "边跨、中跨合龙段长度")
            ttk.Label(box, text="边跨现浇段长度由程序反算（边跨 - 悬臂 - 0 号块半宽 - 合龙段），"
                                "算出来是负数会在这里提示。",
                      style="Opt.TLabel", wraplength=760, justify="left").grid(
                row=4, column=0, columnspan=4, sticky="w", pady=(6, 0))
        else:
            box = self.card("跨径与湿接缝")
            self._row(box, 0, "跨径布置", self.svar(p, "spans"), 22, "m",
                      "写成 20+20（至少 2 跨）")
            self._row(box, 1, "每跨预制梁划分单元数", self.svar(p, "n_div"), 8, "个",
                      "等截面预制梁，一般 2~6 个就够")
            self._row(box, 2, "湿接缝长度", self.svar(p, "l_joint"), 8, "m", "墩顶现浇连续段（纵桥向长度）")
            ttk.Label(box, text="预制梁长 = 跨径 - 湿接缝/2；程序自动在墩顶加临时支座，"
                                "张拉连续束后再撤掉、换永久支座，这就是「体系转换」。",
                      style="Opt.TLabel", wraplength=760, justify="left").grid(
                row=3, column=0, columnspan=4, sticky="w", pady=(6, 0))
        self._label_bottom("提示：本流程按 X-Z 平面建模（*STRUCTYPE 写 0，用支承把平面外自由度全部约束），"
                           "顶部「结构类型」下拉框对这里不起作用。")

    # ---------------- 第 2 步：截面与材料 ----------------
    def step_cs_section(self):
        p = self._p()
        cur = self.m.get("subtype") or core.SUB_FCM
        if cur == core.SUB_FCM:
            box = self.card("主梁截面（变截面单箱单室）")
            self._row(box, 0, "桥宽（箱宽）", self.svar(p, "b_girder"), 8, "m")
            self._row(box, 1, "墩顶梁高", self.svar(p, "h_top"), 8, "m", "根部最高")
            self._row(box, 2, "跨中梁高", self.svar(p, "h_mid"), 8, "m", "跨中最矮；中间按抛物线过渡")
            self._row(box, 3, "顶板厚（沿桥向不变）", self.svar(p, "t_top"), 8, "m")
            self._row(box, 4, "墩顶底板厚", self.svar(p, "t_bot_top"), 8, "m")
            self._row(box, 5, "跨中底板厚", self.svar(p, "t_bot_mid"), 8, "m")
            self._row(box, 6, "墩顶腹板厚", self.svar(p, "t_web_top"), 8, "m")
            self._row(box, 7, "跨中腹板厚", self.svar(p, "t_web_mid"), 8, "m")
            ttk.Label(box, text="底板厚、腹板厚随梁高线性过渡：根部厚、跨中薄。MCT 里按「多段等截面」输出"
                                "（每个单元取自己跨中的尺寸），节段数越多越接近真实变截面。",
                      style="Opt.TLabel", wraplength=760, justify="left").grid(
                row=8, column=0, columnspan=4, sticky="w", pady=(6, 0))

            box = self.card("墩身（薄壁箱形墩）")
            self._row(box, 0, "墩高", self.svar(p, "h_pier"), 8, "m")
            self._row(box, 1, "墩身横桥向宽", self.svar(p, "b_pier"), 8, "m")
            self._row(box, 2, "墩身顺桥向厚", self.svar(p, "t_pier"), 8, "m")
            self._row(box, 3, "墩身划分单元数", self.svar(p, "n_pier"), 8, "个", "一般 2~4 个")
            ttk.Label(box, text="墩底固结、墩顶与主梁共用节点（墩梁固结，这是连续刚构的力学特点）。",
                      style="Opt.TLabel", wraplength=760, justify="left").grid(
                row=4, column=0, columnspan=4, sticky="w", pady=(6, 0))

            box = self.card("材料")
            self._row_combo(box, 0, "主梁混凝土", self.svar(p, "girder_mat"), core.CONC_GRADES,
                            width=10, unit="（常规 C55）")
            self._row_combo(box, 1, "墩身混凝土", self.svar(p, "pier_mat"), core.CONC_GRADES,
                            width=10, unit="（常规 C40）")
        else:
            box = self.card("预制 T 梁截面")
            self._row(box, 0, "梁高", self.svar(p, "h_girder"), 8, "m")
            self._row(box, 1, "翼缘宽", self.svar(p, "b_girder"), 8, "m", "单根 T 梁的翼缘宽度")
            self._row(box, 2, "腹板厚", self.svar(p, "t_web"), 8, "m")
            self._row(box, 3, "翼缘（顶板）厚", self.svar(p, "t_flange"), 8, "m")
            ttk.Label(box, text="预制梁是等截面；湿接缝用同一个截面（简化，算湿重够用）。",
                      style="Opt.TLabel", wraplength=760, justify="left").grid(
                row=4, column=0, columnspan=4, sticky="w", pady=(6, 0))

            box = self.card("材料")
            self._row_combo(box, 0, "混凝土", self.svar(p, "mat"), core.CONC_GRADES,
                            width=10, unit="（课件算例 C40）")
        box = self.card("容重")
        self._row(box, 0, "混凝土容重", self.svar(p, "gamma"), 8, "kN/m³",
                  "自重和湿重都按它算；常规 25")

    # ---------------- 第 3 步：施工阶段 ----------------
    def step_cs_stage(self):
        p = self._p()
        cur = self.m.get("subtype") or core.SUB_FCM
        d = p.setdefault("days", dict(core.default_model()[cur]["days"]))
        box = self.card("每个施工阶段持续几天（阶段划分已自动排好）")
        for i, (key, zh) in enumerate(core.DAY_KEYS[cur]):
            self._row(box, i, zh, self.svar(d, key), 8, "d")
        ttk.Label(box, text="「成桥运营期」一般填 10000 天（≈27 年），让收缩徐变基本完成。",
                  style="Opt.TLabel", wraplength=760, justify="left").grid(
            row=len(core.DAY_KEYS[cur]), column=0, columnspan=4, sticky="w", pady=(6, 0))

        box = self.card("时间依存（收缩徐变）与二期恒载")
        v_creep = self.bvar(p, "creep", bool(p.get("creep")))
        ttk.Checkbutton(box, text="考虑混凝土收缩徐变（生成 *TDM-* 时间依存材料，施工阶段控制数据 bITD=YES）",
                        variable=v_creep, command=self.rerender).pack(anchor="w", pady=2)
        g = ttk.Frame(box)
        g.pack(fill="x", pady=(4, 0))
        self._row(g, 0, "环境年平均湿度", self.svar(p, "humidity"), 8, "%", "CEB-FIP 用，常规 70%")
        self._row(g, 1, "二期恒载（桥面系）", self.svar(p, "q2nd"), 8, "kN/m", "按成桥后桥面系重量折算的均布荷载")

        # 阶段表预览
        box = self.card("施工阶段预览（自动生成）")
        try:
            rows = core.stage_table(self.m)
        except core.LayoutError as exc:
            ttk.Label(box, text="参数还没填完整：%s" % exc, style="Err.TLabel",
                      wraplength=760, justify="left").pack(anchor="w")
            return
        grid = self.table(box, ["编号", "名称", "持续", "这一步激活", "这一步钝化 / 卸除"])
        for r, (name, desc, dur, act, rem) in enumerate(rows, start=1):
            ttk.Label(grid, text=name).grid(row=r, column=0, sticky="w", padx=(0, 10))
            ttk.Label(grid, text=desc).grid(row=r, column=1, sticky="w", padx=(0, 10))
            ttk.Label(grid, text=dur).grid(row=r, column=2, sticky="w", padx=(0, 10))
            ttk.Label(grid, text=act, style="Hint.TLabel").grid(row=r, column=3, sticky="w", padx=(0, 10))
            ttk.Label(grid, text=rem, style="Hint.TLabel").grid(row=r, column=4, sticky="w")
        ttk.Label(box, text="共 %d 个施工阶段。湿重的规律：第 k 个节段浇筑时，它的重量先压在已完成的悬臂端上；"
                            "下一阶段它硬化（单元激活）时再把湿重卸掉。"
                  % len(rows), style="Opt.TLabel", wraplength=760, justify="left").pack(anchor="w", pady=(6, 0))

    # ---------------- 第 4 步：自检 ----------------
    def step_cs_check(self):
        box = self.card("模型摘要")
        try:
            for line in core.summary(self.m):
                ttk.Label(box, text=line, wraplength=780, justify="left").pack(anchor="w", pady=1)
        except core.LayoutError as exc:
            ttk.Label(box, text="参数还没填完整：%s" % exc, style="Err.TLabel",
                      wraplength=780, justify="left").pack(anchor="w")
            return
        except Exception as exc:                       # noqa: BLE001
            ttk.Label(box, text="汇总出错：%s" % exc, style="Err.TLabel").pack(anchor="w")
            return

        probs = self.issues("cs_check")
        box2 = self.card("自检结果")
        if not probs:
            ttk.Label(box2, text="✓ 没发现问题：组 / 边界 / 荷载 / 施工阶段的引用都对得上，可以生成 MCT。",
                      style="Ok.TLabel").pack(anchor="w")
        else:
            for x in probs[:12]:
                ttk.Label(box2, text="· " + x, style="Err.TLabel", wraplength=780,
                          justify="left").pack(anchor="w")

        lay = core.layout_of(self.m)[2]
        box3 = self.card("边界组 / 支承（边界条件的阶段转换就靠它们）")
        grid = self.table(box3, ["边界组", "节点", "约束 Dx Dy Dz Rx Ry Rz"])
        for r, c in enumerate(lay["constraints"], start=1):
            ttk.Label(grid, text=c["group"]).grid(row=r, column=0, sticky="w", padx=(0, 12))
            ttk.Label(grid, text=wc.txt(c["nodes"] and core._ids(c["nodes"]))).grid(
                row=r, column=1, sticky="w", padx=(0, 12))
            ttk.Label(grid, text=c["code"]).grid(row=r, column=2, sticky="w")

        box4 = self.card("结构组（施工阶段激活 / 钝化的单位）")
        names = list(lay["groups"].keys())
        ttk.Label(box4, text="、".join(names), style="Hint.TLabel", wraplength=780,
                  justify="left").pack(anchor="w")
        ttk.Label(box4, text="共 %d 个结构组；每个悬臂节段一个组（和 MIDAS 教程里的 P1Seg1、P2Seg1 一样）。"
                  % len(names), style="Opt.TLabel").pack(anchor="w", pady=(4, 0))

    # ---------------- 第 5 步：生成成果 ----------------
    def _app_dir(self):
        """程序（exe / 源码）所在目录；frozen 运行时不能落到临时解包目录里。"""
        return wc.app_dir()

    def _default_outdir(self):
        return os.path.join(self._app_dir(), "模型文件", "施工阶段梁桥")

    def step_cs_export(self):
        m = self.m
        prj = m.setdefault("project", {})
        if not hasattr(self, "v_cs_outdir"):
            self.v_cs_outdir = tk.StringVar(value=prj.get("outdir") or self._default_outdir())
        if not hasattr(self, "v_cs_fname"):
            self.v_cs_fname = tk.StringVar(value=prj.get("name") or "施工阶段梁桥")
        if not hasattr(self, "v_cs_enc"):
            self.v_cs_enc = tk.StringVar(value="GBK（中文 ANSI，推荐）")

        box = self.card("生成成果")
        line0 = ttk.Frame(box)
        line0.pack(fill="x")
        ttk.Label(line0, text="输出文件夹").pack(side="left")
        ttk.Entry(line0, textvariable=self.v_cs_outdir, width=60).pack(side="left", padx=(6, 6))
        ttk.Button(line0, text="选择…", command=self._pick_dir).pack(side="left")
        ttk.Button(line0, text="打开", command=self._open_dir).pack(side="left", padx=(6, 0))

        line1 = ttk.Frame(box)
        line1.pack(fill="x", pady=(8, 0))
        ttk.Label(line1, text="文件名").pack(side="left")
        ttk.Entry(line1, textvariable=self.v_cs_fname, width=28).pack(side="left", padx=(6, 14))
        ttk.Label(line1, text="文件编码").pack(side="left")
        ttk.Combobox(line1, textvariable=self.v_cs_enc, width=20, state="readonly",
                     values=["GBK（中文 ANSI，推荐）", "UTF-8"]).pack(side="left", padx=(6, 0))

        line2 = ttk.Frame(box)
        line2.pack(fill="x", pady=(10, 0))
        ttk.Button(line2, text="① 生成 MCT + 数据表", command=self._gen_mct).pack(side="left")
        ttk.Button(line2, text="② 复制 MCT 文本", command=self._copy_mct).pack(side="left", padx=(8, 0))

        ttk.Label(box, text="生成后在 MIDAS Civil NX 里：文件 → 导入 → MCT 命令文件，选这个 .mct。\n"
                            "导入后先看「施工阶段」里的阶段个数和组名，再运行「分析 → 施工阶段分析」。",
                  style="Hint.TLabel", justify="left", wraplength=780).pack(anchor="w", pady=(8, 0))

        box2 = self.card("生成记录")
        self.cs_log = tk.Text(box2, height=14, wrap="none", font=("Consolas", 9))
        sb = ttk.Scrollbar(box2, orient="vertical", command=self.cs_log.yview)
        self.cs_log.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.cs_log.pack(side="left", fill="both", expand=True)
        self.cs_log.insert("1.0", "（这里显示生成结果。先点 ① 生成 MCT。)\n")
        self.cs_log.configure(state="disabled")

    def _log(self, text):
        if not hasattr(self, "cs_log"):
            return
        self.cs_log.configure(state="normal")
        self.cs_log.insert("end", text + "\n")
        self.cs_log.see("end")
        self.cs_log.configure(state="disabled")

    def _pick_dir(self):
        d = filedialog.askdirectory(title="选择成果文件夹", initialdir=self.v_cs_outdir.get() or self._app_dir())
        if d:
            self.v_cs_outdir.set(d)

    def _open_dir(self):
        d = self.v_cs_outdir.get()
        try:
            os.makedirs(d, exist_ok=True)
            subprocess.Popen(["explorer", os.path.normpath(d)])
        except OSError as exc:
            self.warn("打不开", str(exc))

    def _ready(self):
        """生成前把模型整理好，返回 (模型, 文件名, 目录) 或 None。"""
        probs = self.issues("cs_export")
        if probs:
            messagebox.showwarning("先改完这些再生成", "\n".join(probs[:10]))
            return None
        m = self.m
        fname = self._safe_name(self.v_cs_fname.get())
        out = self.v_cs_outdir.get() or self._default_outdir()
        m.setdefault("project", {})["name"] = fname
        m["project"]["outdir"] = out
        try:
            os.makedirs(out, exist_ok=True)
        except OSError as exc:
            messagebox.showerror("建不了文件夹", str(exc))
            return None
        return m, fname, out

    def _gen_mct(self):
        got = self._ready()
        if not got:
            return None
        m, fname, out = got
        enc = "utf-8" if self.v_cs_enc.get().startswith("UTF-8") else "gbk"
        try:
            files = core.write_bundle(m, out, fname, enc)
        except Exception as exc:                       # noqa: BLE001
            messagebox.showerror("生成失败", "生成成果时出错（参数里可能有异常值）：\n%s" % exc)
            return None
        self.save()
        info = core.summary(m)
        self._log("✔ 已生成工程文件夹（编码 %s）：%s" % (self.v_cs_enc.get(), out))
        for desc, path in files:
            self._log("   %-14s %s" % (desc, path))
        self._log("   %s" % info[0])
        for line in info[1:]:
            self._log("   " + line)
        self._log("   下一步：MIDAS Civil NX → 文件 → 导入 → MCT 命令文件 → 选上面的 .mct")
        return files[0][1]

    def _copy_mct(self):
        got = self._ready()
        if not got:
            return
        try:
            text = core.build_mct(got[0])
        except Exception as exc:                       # noqa: BLE001
            messagebox.showerror("生成失败", "MCT 生成出错：%s" % exc)
            return
        self.wiz.clipboard_clear()
        self.wiz.clipboard_append(text)
        self._log("✔ MCT 文本已复制到剪贴板（可直接粘到 MIDAS 的 MCT 命令窗口运行）")

    # ==================================================================
    # 桥型示意图
    # ==================================================================
    def _draw(self, canvas):
        try:
            w = canvas.winfo_width()
            h = canvas.winfo_height()
        except tk.TclError:
            return
        if w <= 1:
            w = 760
        if h <= 1:
            h = canvas.winfo_reqheight() or 210
        canvas.delete("all")
        try:
            st, p, lay = core.layout_of(self.m)
        except core.LayoutError as exc:
            canvas.create_text(12, 14, anchor="nw", text="参数还没填完整：%s" % exc, fill="#c0392b",
                               width=max(200, w - 24))
            return
        except Exception as exc:                       # noqa: BLE001
            canvas.create_text(12, 14, anchor="nw", text="画不出来：%s" % exc, fill="#c0392b")
            return
        if st == core.SUB_FCM:
            self._draw_fcm(canvas, w, h, p, lay)
        else:
            self._draw_s2c(canvas, w, h, p, lay)

    def _draw_fcm(self, canvas, W, H, p, lay):
        info = lay["info"]
        ml, mr, mt = 40, 40, 44
        total = info["total"]
        hp = info["h_pier"]
        sx = max(0.01, (W - ml - mr) / total)
        # 竖向比例：墩高 + 梁高 塞进画布下半部分
        sz = min(52.0 / max(info["h_top"], 1e-6), (H - mt - 34.0) / max(hp + info["h_top"], 1e-6))
        y0 = mt + info["h_top"] * sz * 0.5 + 6          # 梁顶线
        canvas.create_line(ml - 12, y0, W - mr + 12, y0, fill="#e8e8e8")

        def X(x):
            return ml + x * sx

        # 主梁（每个单元按自己的梁高画一个矩形，拼出变高效果）
        for el in lay["elems"]:
            if el["part"] != "girder":
                continue
            h = lay["sections"][el["sec"] - 1]["dims"][0]
            canvas.create_rectangle(X(el["x1"]), y0, X(el["x2"]), y0 + h * sz,
                                    fill="#cfe0f5", outline="#4f81bd")
        # 墩身
        for el in lay["elems"]:
            if el["part"] != "pier":
                continue
            canvas.create_rectangle(X(lay["nodes"][el["n1"] - 1]["x"]) - 4, y0 + 1,
                                    X(lay["nodes"][el["n1"] - 1]["x"]) + 4,
                                    y0 + hp * sz, fill="#d9d9d9", outline="#666666")
        # 墩底固结符号
        for i in range(1, info["nspan"]):
            xc = X(info["xsup"][i])
            yb = y0 + hp * sz
            canvas.create_line(xc - 14, yb + 8, xc + 14, yb + 8, fill="#333333", width=2)
            for k in range(-2, 3):
                canvas.create_line(xc + k * 5, yb + 8, xc + k * 5 - 5, yb + 15, fill="#333333")
        # 桥台
        for x in (0, total):
            canvas.create_line(X(x) - 10, y0 + 14, X(x) + 10, y0 + 14, fill="#333333", width=2)
            canvas.create_line(X(x), y0 + 1, X(x), y0 + 14, fill="#333333")
        # 跨径标注
        for i in range(info["nspan"]):
            xa, xb = info["xsup"][i], info["xsup"][i + 1]
            yy = min(y0 + hp * sz + 30, H - 14)
            canvas.create_line(X(xa), yy, X(xb), yy, fill="#888888")
            canvas.create_text((X(xa) + X(xb)) / 2, yy, text="%s m" % core._num(info["spans"][i]),
                               fill="#444444", font=("Consolas", 9))
        # 合龙段标一下
        canvas.create_text(W - mr - 4, 12, anchor="ne",
                           text="连续刚构：墩顶梁高 %.2f m → 跨中 %.2f m，每墩每方向 %d 个节段"
                                % (info["h_top"], info["h_mid"], info["n_seg"]),
                           fill="#666666", font=("Microsoft YaHei UI", 9))

    def _draw_s2c(self, canvas, W, H, p, lay):
        info = lay["info"]
        ml, mr, mt = 40, 40, 56
        total = info["total"]
        sx = max(0.01, (W - ml - mr) / total)
        hg = info["h_girder"]
        sz = min(56.0 / hg, (H - mt - 40.0) / max(hg * 2 + 8, 1e-6))
        y0 = mt + hg * sz * 0.5

        def X(x):
            return ml + x * sx

        for el in lay["elems"]:
            if el["part"] == "girder":
                canvas.create_rectangle(X(el["x1"]), y0, X(el["x2"]), y0 + hg * sz,
                                        fill="#cfe0f5", outline="#4f81bd")
            else:
                canvas.create_rectangle(X(el["x1"]), y0, X(el["x2"]), y0 + hg * sz,
                                        fill="#ffe9b0", outline="#d39e00")
        # 临时支座 / 永久支座 / 墩
        for c in lay["constraints"]:
            nid = c["nodes"][0]
            x = X(lay["nodes"][nid - 1]["x"])
            if c["group"].startswith("BCT"):
                canvas.create_polygon(x, y0 + hg * sz + 2, x - 7, y0 + hg * sz + 14,
                                      x + 7, y0 + hg * sz + 14, fill="#f0f0f0", outline="#999999")
            elif c["group"] == "BC_PIER":
                canvas.create_rectangle(x - 5, y0 + hg * sz + 2, x + 5, y0 + hg * sz + 26,
                                        fill="#d9d9d9", outline="#666666")
                canvas.create_line(x - 14, y0 + hg * sz + 26, x + 14, y0 + hg * sz + 26,
                                   fill="#333333", width=2)
            else:
                canvas.create_polygon(x, y0 + hg * sz + 2, x - 7, y0 + hg * sz + 14,
                                      x + 7, y0 + hg * sz + 14, fill="#cccccc", outline="#333333")
        for i in range(info["nspan"]):
            xa, xb = info["xsup"][i], info["xsup"][i + 1]
            yy = min(y0 + hg * sz + 38, H - 14)
            canvas.create_line(X(xa), yy, X(xb), yy, fill="#888888")
            canvas.create_text((X(xa) + X(xb)) / 2, yy, text="%s m" % core._num(info["spans"][i]),
                               fill="#444444", font=("Consolas", 9))
        canvas.create_text(W - mr - 4, 12, anchor="ne",
                           text="简支变连续：湿接缝 %.2f m，预制梁长 %.2f m，%d 跨"
                                % (info["l_joint"], info["spans"][0] - info["l_joint"],
                                   info["nspan"]),
                           fill="#666666", font=("Microsoft YaHei UI", 9))
        canvas.create_text(ml, H - 12, anchor="w",
                           text="灰三角=临时支座（架设用）  深灰=永久支座/墩  黄色=湿接缝（现浇连续段）",
                           fill="#888888", font=("Microsoft YaHei UI", 8))

    def _label_bottom(self, text):
        ttk.Label(self.form, text=text, style="Hint.TLabel", wraplength=820,
                  justify="left").pack(anchor="w", pady=(4, 0))
