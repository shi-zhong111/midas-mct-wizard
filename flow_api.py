# -*- coding: utf-8 -*-
"""建模流程插件接口（FlowBase）。

主程序 midas_wizard.py 只保留公共外壳：
    顶部「流程」下拉、左边步骤栏、上一步/下一步、会话存盘、成果生成按钮。
每一种桥型的「一步一步填数值」都写在各自的 flow_*.py 里，按本文件的约定实现即可挂上去。

一个流程模块的固定结构（以 flow_cable.py 为例）::

    # ---- 纯计算部分（不依赖 tkinter，测试可以直接调用）----
    CODE  = "cable"
    LABEL = "斜拉桥（鱼骨刺·成桥）"
    def default_model(): ...          # 返回本流程的初始数据 dict
    def issues(model, sid): ...       # 这一步的问题，空列表 = 可以下一步
    def build_mct(model): ...         # 返回 MCT 文本（写文件用）
    def data_report(model): ...       # 返回人可读的数据表文本

    # ---- 界面部分 ----
    class Flow(FlowBase):
        code = CODE
        label = LABEL
        step_ids = [...]
        step_titles = [...]
        step_hints = [...]
        def default_model(self): return default_model()
        def issues(self, sid): return issues(self.m, sid)
        def step_xxx(self): ...       # 渲染这一步
        def clear(self, sid): ...     # 清空本步（可选）

约定：
  * step_ids 的最后一项是「生成成果」步：主程序会自动把倒数第二步的按钮文字改成
    「生成成果」，并在最后一步禁用「下一步」。
  * 每一步的渲染都往 self.form 里画（用 self.card()/self.entry()/... 即可）。
  * 所有输入都直接写进 self.m（= 主窗口的 self.model），别另存一份，
    否则存盘/切换流程会丢。
  * 数值一律存成字符串（跟梁桥流程一致），写 MCT 时用 wiz_common.num()/numg()。
"""

import os

import wiz_common as wc


class FlowBase(object):
    # ---- 子类必须覆盖 ----
    code = ""                 # 英文代码，也是存盘/切流程的键
    label = ""                # 顶部下拉显示的中文
    step_ids = []
    step_titles = []
    step_hints = []

    def __init__(self, wiz):
        self.wiz = wiz

    # ---------------- 数据 ----------------
    @property
    def m(self):
        """当前流程的模型数据（就是主窗口的 self.model）。"""
        return self.wiz.model

    def default_model(self):
        raise NotImplementedError

    def issues(self, sid):
        return []

    def data_report(self):
        """人可读的数据表（『生成成果』里写进 *_数据表.txt）。可选。"""
        return ""

    def export(self):
        """最后一步「生成成果」的钩子：由各流程自己实现写文件。可选。"""
        return []

    # ---------------- 渲染 ----------------
    def build(self, sid):
        fn = getattr(self, "step_" + sid, None)
        if fn is None:
            raise NotImplementedError("%s 流程没有实现 %s 这一步" % (self.code, sid))
        fn()

    def clear(self, sid):
        """清空本步输入（不带确认框）。返回一句中文说明；不支持就返回 ""。"""
        return ""

    def on_enter(self):
        """从别的流程切过来时调用（可选）。"""

    # ---------------- 常用界面快捷方式 ----------------
    @property
    def form(self):
        return self.wiz.form

    def card(self, title=None):
        return self.wiz._card(title)

    def table(self, box, headers):
        return self.wiz._table(box, headers)

    def entry(self, parent, var, width=10, row=0, col=0, **kw):
        return self.wiz.entry(parent, var, width=width, row=row, col=col, **kw)

    def combo(self, parent, var, values, width=14, row=0, col=0, on_change=None, **kw):
        return self.wiz.combo(parent, var, values, width=width, row=row, col=col,
                              on_change=on_change, **kw)

    def svar(self, container, key, initial=""):
        return self.wiz.svar(container, key, initial)

    def bvar(self, container, key, initial=False):
        return self.wiz.bvar(container, key, initial)

    def save(self):
        self.wiz.save_session()

    def rerender(self):
        self.wiz.render()

    def warn(self, title, msg):
        from tkinter import messagebox
        messagebox.showwarning(title, msg)

    def info(self, title, msg):
        from tkinter import messagebox
        messagebox.showinfo(title, msg)

    def ask(self, title, msg):
        from tkinter import messagebox
        return messagebox.askyesno(title, msg)

    # ---------------- 成果输出 ----------------
    def out_dir(self):
        """本流程的成果目录（默认 程序目录/模型文件/<流程英文名>）。"""
        d = self.m.get("project", {}).get("outdir") or ""
        if d:
            return d
        return os.path.join(self.wiz.out_root(), self.code)

    def write_text(self, path, text, enc="gbk"):
        d = os.path.dirname(path)
        if d:
            os.makedirs(d, exist_ok=True)
        with open(path, "w", encoding=enc, newline="") as fh:
            fh.write(text)
        return path

    def report_error(self, title, exc):
        import traceback
        self.warn(title, "%s\n\n（详细信息已打印到控制台）" % exc)
        traceback.print_exc()
