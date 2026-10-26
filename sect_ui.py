# -*- coding: utf-8 -*-
"""新流程共用的「截面库」：参数化截面 + DXF 自定义截面 + 变截面 + MCT 输出。

梁桥流程里那套截面界面是写死在主程序里的；新增的桥型流程（斜拉桥 / 悬索桥 /
施工阶段 / 钢管混凝土拱 …）统一调用本模块，保证：
  * 填数值的方式和梁桥一致（选形状→按顺序填尺寸）；
  * 「导入自定义截面」和梁桥用的是同一套 DXF 解析（wiz_common.read_dxf_loops），
    算出来的面积/形心/惯矩一致，写进 MCT 的 *SECT-PSCVALUE 也一致。

界面入口：
    editor = SectionEditor(flow, m, allow_cad=True, allow_taper=False, title="截面")
    editor.render(box)          # 在 box（一个 Frame/Labelframe）里画出整块截面编辑区
    editor.on_change = lambda: ...   # 增删改后回调（一般传 flow.rerender）

纯函数（引擎/测试可直接用）：
    param_section(m, code) / taper_section(m, i, j) / cad_section(m, path, unit)
    section_mct_lines(sections)  -> ["*SECTION ...", ...]
    section_summary(sec) -> "H=2 B=1.5 …"
"""

import os
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import wiz_common as wc

SHAPES = wc.SHAPES
SHAPE_BY_CODE = wc.SHAPE_BY_CODE
CAD_SHAPE = wc.CAD_SHAPE
TS_SHAPE = wc.TS_SHAPE
DIM_SLOTS = wc.DIM_SLOTS

UNIT_CHOICES = [("mm", 0.001), ("cm", 0.01), ("dm", 0.1), ("m", 1.0), ("in", 0.0254), ("ft", 0.3048)]
UNIT_BY_LABEL = dict(UNIT_CHOICES)
UNIT_LABEL = {v: k for k, v in UNIT_CHOICES}


# ======================================================================
# 数据层
# ======================================================================
def new_section(m, name=None, shape="SB"):
    secs = m.setdefault("sections", [])
    sec = {"name": name or ("截面%d" % (len(secs) + 1)), "shape": shape,
           "dims": [], "offset": "CC", "shear": "YES", "warp": "NO"}
    secs.append(sec)
    return sec


def param_section(m, code):
    return new_section(m, shape=code)


def taper_section(m, i_sect, j_sect):
    secs = m.setdefault("sections", [])
    sec = {"name": "变截面%d" % (len(secs) + 1), "shape": TS_SHAPE, "dims": [],
           "offset": "CT", "shear": "YES", "warp": "NO",
           "i_sect": str(i_sect), "j_sect": str(j_sect)}
    secs.append(sec)
    return sec


def cad_section(m, path, unit_scale=0.001):
    """解析 DXF 并算截面特性；返回截面 dict（失败抛异常）。"""
    loops = wc.read_dxf_loops(path, unit_scale)
    props = wc.compute_section_props(loops)
    secs = m.setdefault("sections", [])
    name = os.path.splitext(os.path.basename(path))[0][:16] or ("自定义%d" % (len(secs) + 1))
    sec = {"name": name, "shape": CAD_SHAPE, "dims": [], "offset": "CC",
           "shear": "YES", "warp": "NO", "file": path,
           "unit": str(unit_scale), "props": props}
    secs.append(sec)
    return sec


def recalc_cad(sec, unit_scale=None):
    """按（可能换过的）单位重新解析 DXF，更新 props。"""
    path = sec.get("file") or ""
    if not path or not os.path.isfile(path):
        raise IOError("找不到 DXF 文件：%s" % path)
    if unit_scale is None:
        try:
            unit_scale = float(sec.get("unit") or 0.001)
        except (TypeError, ValueError):
            unit_scale = 0.001
    loops = wc.read_dxf_loops(path, unit_scale)
    sec["unit"] = str(unit_scale)
    sec["props"] = wc.compute_section_props(loops)
    return sec["props"]


def section_summary(sec):
    """一句话描述，给表格/数据表用。"""
    code = sec.get("shape") or "SB"
    if code == CAD_SHAPE:
        pr = sec.get("props") or {}
        try:
            a = float(pr.get("A") or 0)
            iy = float(pr.get("Iyy") or 0)
            iz = float(pr.get("Izz") or 0)
            return "DXF：A=%.4g m²  Iyy=%.4g  Izz=%.4g" % (a, iy, iz)
        except (TypeError, ValueError):
            return "DXF（还没算截面特性）"
    if code == TS_SHAPE:
        return "变截面：i=%s → j=%s" % (sec.get("i_sect", "?"), sec.get("j_sect", "?"))
    shape = SHAPE_BY_CODE.get(code)
    if not shape:
        return code
    parts = []
    for k, (key, zh) in enumerate(shape[2]):
        v = (sec.get("dims") or [])[k] if k < len(sec.get("dims") or []) else ""
        parts.append("%s=%s" % (key, v if str(v).strip() else "?"))
    return " ".join(parts)


# ======================================================================
# MCT 输出（与梁桥 build_mct 的写法保持一致）
# ======================================================================
def section_mct_lines(sections):
    """把截面列表翻译成 MCT 行（含 *SECT-PSCVALUE 与 *SECTION 两块）。"""
    L = []
    add = L.append
    cad = [(i, s) for i, s in enumerate(sections, start=1) if s.get("shape") == CAD_SHAPE]
    fallback = []
    if cad:
        add("*SECT-PSCVALUE    ; PSC Value, General Section（DXF 自定义截面）")
        add("; SECT=iSEC, TYPE, SNAME, [OFFSET], bSD, bWE, SHAPE, bBU, bEQ, SNAME2(PSC VAL)")
        add(";      AREA, ASy, ASz, Ixx, Iyy, Izz")
        add(";      CyP, CyM, CzP, CzM, QyB, QzB, PERI_OUT, PERI_IN, Cy, Cz")
        add(";      Y1, Y2, Y3, Y4, Z1, Z2, Z3, Z4")
        add(";      HT, BT, T1, T2")
        add(";      bSHEARCHK, Z1, Z3, bAUTO_QY1, QY1, ..., TOR, bAUTO_SHR1, SHR1, ...")
        add(";      OPOLY=X1, Y1, X2, Y2, ...  外轮廓（逆时针）")
        add(";      IPOLY=X1, Y1, X2, Y2, ...  孔洞（顺时针）")
        for i, sec in cad:
            name = wc.aname(sec.get("name"), "SEC%d" % i)
            off = sec.get("offset", "CC")
            pr = sec.get("props") or {}
            outs = [wc.orient_loop(lp, True) for lp in (pr.get("OUTER_LOOPS") or [])
                    if len(lp) >= 3 and abs(wc.loop_area(lp)) > 1e-12]
            ins = [wc.orient_loop(lp, False) for lp in (pr.get("HOLE_LOOPS") or [])
                   if len(lp) >= 3 and abs(wc.loop_area(lp)) > 1e-12]
            if len(outs) != 1:
                fallback.append((i, sec))
                continue
            try:
                A = float(pr.get("A") or 0); iyy = float(pr.get("Iyy") or 0)
                izz = float(pr.get("Izz") or 0); ixx = float(pr.get("Ixx") or 0)
                cyp = float(pr.get("CyP") or 0); cym = float(pr.get("CyM") or 0)
                czp = float(pr.get("CzP") or 0); czm = float(pr.get("CzM") or 0)
                po = float(pr.get("PERI_OUT") or 0); pi = float(pr.get("PERI_IN") or 0)
                uyy = A * 5.0 / 6.0; uzz = A * 5.0 / 6.0
            except (TypeError, ValueError):
                A = iyy = izz = ixx = cyp = cym = czp = czm = po = pi = 0.0
                uyy = uzz = 0.0
            add(" SECT=%4d, PSC       , %-17s , %s, 0, 0, 0, 0, 0, 0, %s, %s, VALU, NO, NO, "
                % (i, name, off, sec.get("shear", "YES"), sec.get("warp", "NO")))
            add("        %s, %s, %s, %s, %s, %s"
                % (wc.numg(A), wc.numg(uyy), wc.numg(uzz), wc.numg(ixx), wc.numg(iyy), wc.numg(izz)))
            add("        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s"
                % (wc.numg(cyp), wc.numg(cym), wc.numg(czp), wc.numg(czm),
                   wc.numg(A * czp / 8.0), wc.numg(A * cyp / 8.0), wc.numg(po), wc.numg(pi),
                   wc.numg(cyp), wc.numg(czp)))
            add("        %s, %s, %s, %s, %s, %s, %s, %s"
                % (wc.numg(-cym), wc.numg(cyp), wc.numg(cyp), wc.numg(-cym),
                   wc.numg(czp), wc.numg(czp), wc.numg(-czm), wc.numg(-czm)))
            add("        0.1, 0.1, 0.1, 0.1")
            add("        YES, 0, 0, YES, , YES, , YES, , 0, YES, , YES, , YES, ")
            for lp in outs:
                add("       OPOLY=" + ", ".join(wc.numg(v) for p in lp for v in (p[0], p[1])))
            for lp in ins:
                add("       IPOLY=" + ", ".join(wc.numg(v) for p in lp for v in (p[0], p[1])))
        add("")

    std = sorted([(i, s) for i, s in enumerate(sections, start=1)
                  if s.get("shape") != CAD_SHAPE] + fallback, key=lambda t: t[0])
    if std:
        add("*SECTION    ; Section Properties")
        add("; iSEC, TYPE, SNAME, [OFFSET], bSD, bWE, SHAPE, [DATA1], [DATA2]")
        add("; iSEC, TYPE, SNAME, [OFFSET], bSD, bWE, SHAPE, BLT, D1, ..., D8, iCEL")
        add(";       AREA, ASy, ASz, Ixx, Iyy, Izz")
        add(";       CyP, CyM, CzP, CzM, QyB, QzB, PERI_OUT, PERI_IN, Cy, Cz")
        add(";       Y1, Y2, Y3, Y4, Z1, Z2, Z3, Z4")
        for i, sec in std:
            name = wc.aname(sec.get("name"), "SEC%d" % i)
            off = sec.get("offset", "CC")
            code = wc.shape_code_of(sec)
            if code == TS_SHAPE:
                def pad8(o):
                    if not o:
                        return ["0"] * 8
                    c = wc.shape_code_of(o)
                    n = len(SHAPE_BY_CODE.get(c, SHAPES[0])[2])
                    d = [wc.num(o.get("dims", [])[k]) if k < len(o.get("dims", [])) else "0"
                         for k in range(n)]
                    return (d + ["0"] * 8)[:8]
                try:
                    ia = int(float(sec.get("i_sect") or 0)) - 1
                    ja = int(float(sec.get("j_sect") or 0)) - 1
                except (TypeError, ValueError):
                    ia = ja = -1
                si = sections[ia] if 0 <= ia < len(sections) else None
                sj = sections[ja] if 0 <= ja < len(sections) else None
                ci = wc.shape_code_of(si) if si else "SB"
                add("   %d, TAPERED, %s, %s, 0, 0, 0, 0, 0, 0, 0, 0, %s, %s, NO, %s, 1, 1, USER"
                    % (i, name, off, sec.get("shear", "YES"), sec.get("warp", "NO"), ci))
                di, dj = pad8(si), pad8(sj)
                if sec.get("mirror"):
                    di, dj = dj, di
                add("      %s, %s" % (", ".join(di), ", ".join(dj)))
                continue
            if code == CAD_SHAPE:      # 多外轮廓等放不进 PSCVALUE 的，退回数值截面
                pr = sec.get("props") or {}
                try:
                    h = float(pr.get("CzP") or 0) + float(pr.get("CzM") or 0)
                    b = float(pr.get("CyP") or 0) + float(pr.get("CyM") or 0)
                except (TypeError, ValueError):
                    h = b = 0.0
                add("   %d, VALUE, %s, %s, 0, 0, 0, 0, 0, 0, %s, %s, SB, BUILT, %s, %s, 0, 0, 0, 0, 0, 0, 0"
                    % (i, name, off, sec.get("shear", "YES"), sec.get("warp", "NO"),
                       wc.numg(h), wc.numg(b)))
                add("   %s, %s, %s, %s, %s, %s" % (
                    wc.numg(pr.get("A")), wc.numg(pr.get("ASy")), wc.numg(pr.get("ASz")),
                    wc.numg(pr.get("Ixx")), wc.numg(pr.get("Iyy")), wc.numg(pr.get("Izz"))))
                add("   %s, %s, %s, %s, 0, 0, %s, %s, %s, %s" % (
                    wc.numg(pr.get("CyP")), wc.numg(pr.get("CyM")), wc.numg(pr.get("CzP")),
                    wc.numg(pr.get("CzM")), wc.numg(pr.get("PERI_OUT")), wc.numg(pr.get("PERI_IN")),
                    wc.numg(pr.get("Cy")), wc.numg(pr.get("Cz"))))
                continue
            shape = SHAPE_BY_CODE.get(code, SHAPES[0])
            dims = [wc.num(sec.get("dims", [])[k]) if k < len(sec.get("dims", [])) else "0"
                    for k in range(len(shape[2]))]
            vals = dims + ["0"] * max(0, DIM_SLOTS - len(dims))
            add("   %d, DBUSER, %s, %s, 0, 0, 0, 0, 0, 0, %s, %s, %s, 2, %s"
                % (i, name, off, sec.get("shear", "YES"), sec.get("warp", "NO"), code,
                   ", ".join(vals)))
        add("")
    return L


# ======================================================================
# 界面层
# ======================================================================
class SectionEditor(object):
    """在一块区域里画出「截面库」编辑界面。

    allow_cad   : 允不允许导入 DXF 自定义截面（默认允许）
    allow_taper : 允不允许加变截面（默认不允许）
    note        : 顶部一行灰字说明
    """

    def __init__(self, flow, m, allow_cad=True, allow_taper=False, title="截面", note=""):
        self.flow = flow
        self.m = m
        self.allow_cad = allow_cad
        self.allow_taper = allow_taper
        self.title = title
        self.note = note
        self.on_change = None
        self.unit_var = tk.StringVar(value="mm")
        self.taper_i = tk.StringVar(value="1")
        self.taper_j = tk.StringVar(value="2")

    # ---------------- 外部入口 ----------------
    def render(self, box):
        self.box = box
        if self.note:
            ttk.Label(box, text=self.note, style="Hint.TLabel", wraplength=720,
                      justify="left").pack(anchor="w", pady=(0, 6))
        self._rows(box)
        bar = ttk.Frame(box)
        bar.pack(fill="x", pady=(6, 2))
        shape_names = ["%s · %s" % (s[0], s[1]) for s in SHAPES]
        self.v_new = tk.StringVar(value=shape_names[0])
        ttk.Combobox(bar, textvariable=self.v_new, values=shape_names, width=18,
                     state="readonly").pack(side="left", padx=(0, 6))
        ttk.Button(bar, text="＋ 添加参数化截面", command=self._add_param).pack(side="left", padx=(0, 14))
        if self.allow_taper:
            ttk.Label(bar, text="变截面：i 端").pack(side="left")
            ttk.Combobox(bar, textvariable=self.taper_i, width=4, values=self._sect_numbers(),
                         state="readonly").pack(side="left", padx=(2, 2))
            ttk.Label(bar, text="j 端").pack(side="left")
            ttk.Combobox(bar, textvariable=self.taper_j, width=4, values=self._sect_numbers(),
                         state="readonly").pack(side="left", padx=(2, 8))
            ttk.Button(bar, text="＋ 添加变截面", command=self._add_taper).pack(side="left", padx=(0, 14))
        if self.allow_cad:
            bar2 = ttk.Frame(box)
            bar2.pack(fill="x", pady=(2, 2))
            ttk.Label(bar2, text="图纸单位").pack(side="left")
            ttk.Combobox(bar2, textvariable=self.unit_var, width=6,
                         values=[u[0] for u in UNIT_CHOICES], state="readonly").pack(side="left", padx=(2, 8))
            ttk.Button(bar2, text="＋ 导入自定义截面（DXF）…", command=self._add_cad).pack(side="left", padx=(0, 8))
            ttk.Button(bar2, text="重新计算自定义截面", command=self._recalc_cad).pack(side="left")
            ttk.Label(box, text="只认 DXF（DWG 请先在 CAD 里另存为 DXF）。轮廓要闭合；有孔洞照常画内轮廓。",
                      style="Opt.TLabel").pack(anchor="w", pady=(2, 0))
        ttk.Button(box, text="清空截面", command=self._clear).pack(anchor="w", pady=(6, 0))

    # ---------------- 内部 ----------------
    def _changed(self):
        self.flow.save()
        if self.on_change:
            self.on_change()

    def _sect_numbers(self):
        return [str(i) for i in range(1, max(2, len(self.m.get("sections", [])) + 1))]

    def _rows(self, box):
        grid = self.flow.table(box, ["编号", "名称", "形状", "尺寸 / 说明", "偏心", "删除"])
        secs = self.m.setdefault("sections", [])
        if not secs:
            ttk.Label(grid, text="还没有截面 —— 先在上面加一个。", style="Hint.TLabel").grid(
                row=1, column=0, columnspan=6, sticky="w", pady=4)
            return
        for idx, sec in enumerate(secs):
            r = idx + 1
            ttk.Label(grid, text=str(idx + 1)).grid(row=r, column=0, sticky="w", padx=(0, 8))
            name_var = tk.StringVar(value=sec.get("name", ""))
            name_var.trace_add("write", lambda *_a, _s=sec, _v=name_var: _s.__setitem__("name", _v.get()))
            ttk.Entry(grid, textvariable=name_var, width=12).grid(row=r, column=1, sticky="w", padx=(0, 8))
            code = sec.get("shape") or "SB"
            if code in (CAD_SHAPE, TS_SHAPE):
                zh = "DXF 自定义" if code == CAD_SHAPE else "变截面"
                ttk.Label(grid, text=zh).grid(row=r, column=2, sticky="w", padx=(0, 8))
                ttk.Label(grid, text=section_summary(sec), style="Hint.TLabel",
                          wraplength=320, justify="left").grid(row=r, column=3, sticky="w", padx=(0, 8))
            else:
                shape_names = ["%s · %s" % (s[0], s[1]) for s in SHAPES]
                cur = SHAPE_BY_CODE.get(code)
                vshape = tk.StringVar(value=("%s · %s" % (cur[0], cur[1])) if cur else shape_names[0])
                cb = ttk.Combobox(grid, textvariable=vshape, values=shape_names, width=14, state="readonly")
                cb.grid(row=r, column=2, sticky="w", padx=(0, 8))
                cb.bind("<<ComboboxSelected>>", lambda _e, _i=idx, _v=vshape: self._switch_shape(_i, _v))
                dims = ttk.Frame(grid)
                dims.grid(row=r, column=3, sticky="w")
                for k, (key, zh) in enumerate(SHAPE_BY_CODE.get(code, SHAPES[0])[2]):
                    ttk.Label(dims, text=zh).pack(side="left")
                    dv = tk.StringVar(value=(sec.get("dims") or [""])[k]
                                      if k < len(sec.get("dims") or []) else "")
                    dv.trace_add("write", lambda *_a, _s=sec, _k=k, _v=dv: self._set_dim(_s, _k, _v.get()))
                    ttk.Entry(dims, textvariable=dv, width=6).pack(side="left", padx=(1, 6))
            off_var = tk.StringVar(value=sec.get("offset", "CC"))
            off_var.trace_add("write", lambda *_a, _s=sec, _v=off_var: _s.__setitem__("offset", _v.get()))
            ttk.Combobox(grid, textvariable=off_var, values=["CC", "CT", "CB", "LC", "RC"],
                         width=5, state="readonly").grid(row=r, column=4, sticky="w", padx=(0, 8))
            ttk.Button(grid, text="删除", width=5,
                       command=lambda _i=idx: self._del(_i)).grid(row=r, column=5, sticky="w")

    @staticmethod
    def _set_dim(sec, k, value):
        dims = sec.setdefault("dims", [])
        while len(dims) <= k:
            dims.append("")
        dims[k] = value

    def _switch_shape(self, idx, shape_var):
        try:
            sec = self.m["sections"][idx]
        except (KeyError, IndexError):
            return
        code = shape_var.get().split(" ")[0]
        sec["shape"] = code
        sec["dims"] = []
        self._changed()
        self.flow.rerender()

    def _add_param(self):
        code = self.v_new.get().split(" ")[0]
        param_section(self.m, code)
        self._changed()
        self.flow.rerender()

    def _add_taper(self):
        try:
            i = int(float(self.taper_i.get())); j = int(float(self.taper_j.get()))
        except (TypeError, ValueError):
            self.flow.warn("参数不对", "变截面的 i/j 端都要选截面号。")
            return
        n = len(self.m.get("sections", []))
        if not (1 <= i <= n and 1 <= j <= n):
            self.flow.warn("参数不对", "i/j 端要选 1~%d 之间的截面。" % n)
            return
        if i == j:
            self.flow.warn("参数不对", "i 端和 j 端不能是同一个截面。")
            return
        taper_section(self.m, i, j)
        self._changed()
        self.flow.rerender()

    def _add_cad(self):
        path = filedialog.askopenfilename(title="选择 DXF 截面文件",
                                          filetypes=[("DXF 图形", "*.dxf"), ("所有文件", "*.*")])
        if not path:
            return
        unit = UNIT_BY_LABEL.get(self.unit_var.get(), 0.001)
        try:
            sec = cad_section(self.m, path, unit)
        except Exception as exc:                                    # noqa: BLE001
            messagebox.showerror("读不了这个 DXF", "%s\n\n常见原因：\n"
                                 "· 文件是 DWG（请先另存为 DXF）；\n"
                                 "· 轮廓没闭合、或者用了样条/图块；\n"
                                 "· 轮廓不在 XY 平面。" % exc)
            return
        props = sec.get("props") or {}
        try:
            area = float(props.get("A") or 0)
        except (TypeError, ValueError):
            area = 0.0
        msg = "已读入：%s\n\n面积 A = %.6g m²\nIyy = %.6g m⁴\nIzz = %.6g m⁴" % (
            os.path.basename(path), area, float(props.get("Iyy") or 0), float(props.get("Izz") or 0))
        if not (0.005 <= area <= 50):
            msg += "\n\n⚠ 面积不像梁截面。图纸单位可能选错了：%s" % wc.unit_advice(sec)
        self.info("自定义截面", msg)
        self._changed()
        self.flow.rerender()

    def _recalc_cad(self):
        n = 0
        for sec in self.m.get("sections", []):
            if sec.get("shape") == CAD_SHAPE:
                try:
                    recalc_cad(sec)
                    n += 1
                except Exception as exc:                            # noqa: BLE001
                    messagebox.showwarning("重新计算失败", "%s：%s" % (sec.get("name"), exc))
        self.info("重新计算", "重新解析了 %d 个自定义截面。" % n if n else "没有自定义截面需要重算。")
        self._changed()
        self.flow.rerender()

    def _del(self, idx):
        try:
            self.m["sections"].pop(idx)
        except (KeyError, IndexError):
            return
        self._changed()
        self.flow.rerender()

    def _clear(self):
        if not self.flow.ask("清空截面", "确定清空所有截面吗？"):
            return
        self.m["sections"] = []
        self._changed()
        self.flow.rerender()
