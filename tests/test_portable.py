# -*- coding: utf-8 -*-
"""跨平台单元测试（纯函数，不启动 Tk、不连 MIDAS、不写工程目录）。

    python -m unittest discover -s tests -v
    python midas_wizard.py --selftest      # 同一批默认模型的轻量版

覆盖：
  * 版本号、9 条流程都能被发现、插件加载没有错误；
  * 每条流程的默认模型都能生成 MCT，含必需命令块，过 mct_lint 结构检查；
  * 关键数值：预制 T 梁联合截面内力/应力、两跨钢管混凝土两种湿重方式的差异；
  * 截面特性纯函数（矩形截面的 A / Iyy / Izz 对解析解）；
  * 全仓库 .py 都能被 Python 3.9 解析（CI 跑 3.9，防止用了 3.10+ 语法）。
"""
import ast
import io
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import midas_wizard as mw          # noqa: E402
import mct_lint                     # noqa: E402

FLOW_CODES = ["beam", "arch", "cable", "susp", "cs", "cfst", "frame", "tbeam", "cfst2"]

# 每条流程默认 MCT 里必须有这些命令块
REQUIRED_BLOCKS = {
    "beam": ["*NODE", "*ELEMENT", "*MATERIAL", "*SECTION", "*STLDCASE", "*ENDDATA"],
    "arch": ["*NODE", "*ELEMENT", "*STAGE", "*CPOSECT4CS", "*ENDDATA"],
    "cable": ["*NODE", "*ELEMENT", "*PRETENSION", "*ENDDATA"],
    "susp": ["*NODE", "*ELEMENT", "*PRETENSION", "*ENDDATA"],
    "cs": ["*NODE", "*ELEMENT", "*STAGE", "*ENDDATA"],
    "cfst": ["*NODE", "*ELEMENT", "*STAGE", "*CPOSECT4CS", "*ENDDATA"],
    "frame": ["*NODE", "*ELEMENT", "*CONSTRAINT", "*ENDDATA"],
    "tbeam": ["*NODE", "*ELEMENT", "*STAGE", "*CPOSECT4CS", "*ENDDATA"],
    "cfst2": ["*NODE", "*ELEMENT", "*STAGE", "*CPOSECT4CS", "*ENDDATA"],
}


def _build(flow):
    inst, build, _report = mw.flow_module_build_mct(flow)
    assert inst is not None, "流程 %s 没注册" % flow
    assert build is not None, "流程 %s 没有 build_mct" % flow
    return build(inst.default_model())


def _block(text, tag):
    """取第一条以 tag 开头的命令块的数据行（不含注释/空行）。"""
    out, started = [], False
    for raw in text.splitlines():
        s = raw.strip()
        if not started:
            if s.startswith(tag):
                started = True
            continue
        if s.startswith("*"):
            break
        if s and not s.startswith(";"):
            out.append(s)
    assert started, "MCT 里没有 %s 命令块" % tag
    return out


def _py_files():
    for base, dirs, files in os.walk(ROOT):
        dirs[:] = [d for d in dirs if d not in (".git", "__pycache__", "docs", "examples")]
        for name in files:
            if name.endswith(".py"):
                yield os.path.join(base, name)


class TestVersionAndFlows(unittest.TestCase):
    def test_version(self):
        self.assertRegex(mw.__version__, r"^\d+\.\d+\.\d+$")

    def test_all_nine_flows_discovered(self):
        flows = mw.discover_flows()
        self.assertEqual(sorted(flows), sorted(FLOW_CODES))
        self.assertEqual(mw.FLOW_LOAD_ERRORS, {}, "有流程插件加载失败：%s" % mw.FLOW_LOAD_ERRORS)
        for code, cls in flows.items():
            self.assertTrue(getattr(cls, "label", ""), "流程 %s 没有 label" % code)


class TestDefaultModels(unittest.TestCase):
    """每条流程的默认模型都要能生成结构合法的 MCT。"""

    def test_every_flow_builds_mct(self):
        for code in FLOW_CODES:
            with self.subTest(flow=code):
                text = _build(code)
                self.assertGreater(len(text.splitlines()), 10, "%s 的 MCT 太短" % code)
                for tag in REQUIRED_BLOCKS[code]:
                    self.assertIn(tag, text, "%s 的 MCT 缺少 %s" % (code, tag))
                mct_lint.assert_ok(text)          # 引用检查 + 字段数对照（有样本时）

    def test_cposect4cs_has_37_fields(self):
        """施工阶段联合截面的部件行必须是 37 个字段（照 MIDAS 真跑过的样本）。"""
        for code in ("arch", "cfst", "cfst2", "tbeam"):
            with self.subTest(flow=code):
                text = _build(code)
                rows = _block(text, "*CPOSECT4CS")
                data = [r for r in rows if "," in r and not r.upper().startswith("SEC=")]
                self.assertTrue(data, "%s 的 *CPOSECT4CS 没有部件行" % code)
                for row in data:
                    self.assertEqual(len(row.split(",")), 37,
                                     "%s：*CPOSECT4CS 字段数不是 37：%r" % (code, row))


class TestTBeamNumbers(unittest.TestCase):
    """习题 4 的数值：跨中总弯矩、主梁/板分担、跨中应力。"""

    def test_hand_calc_matches(self):
        import tbeam_core as tc
        res = tc.results(tc.default_model())
        self.assertAlmostEqual(res["M_total"], 2000.0, places=6)
        self.assertAlmostEqual(res["M_pre"], 1000.0, places=6)
        self.assertAlmostEqual(res["M_beam"], 1637.61, places=2)
        self.assertAlmostEqual(res["M_slab"], 362.39, places=2)
        self.assertAlmostEqual(res["share_beam"], 0.63761, places=5)
        st = res["stress"]["total"]
        self.assertAlmostEqual(st["beam_bot"], 22.16, places=2)
        self.assertAlmostEqual(st["beam_top"], -16.65, places=2)
        self.assertAlmostEqual(st["slab_top"], -3.85, places=2)
        once = res["stress"]["one_step"]
        self.assertAlmostEqual(once["beam_bot"], 14.31, places=2)
        self.assertAlmostEqual(once["slab_top"], -7.71, places=2)


class TestFrameSolver(unittest.TestCase):
    """平面刚架：内置矩阵位移法求解器 + 习题 8 的 (a)/(b) 两个预设。"""

    def test_edub_8a(self):
        import frame_core as fc
        r = fc.solve(fc.default_model())
        react = r["reactions"]
        self.assertAlmostEqual(react[1][0], 50.0, places=3)
        self.assertAlmostEqual(react[1][1], 25.0, places=3)
        self.assertAlmostEqual(react[1][2], -416.667, places=2)
        self.assertAlmostEqual(react[4][1], 75.0, places=3)
        el = {e["no"]: e for e in r["elements"].values()}
        self.assertAlmostEqual(el[1]["Mi"], -416.667, places=2)   # 柱 1 底
        self.assertAlmostEqual(el[1]["Mj"], -250.0, places=2)     # 柱 1 顶
        self.assertAlmostEqual(el[2]["Mj"], -500.0, places=2)     # 梁 2-3 右端
        self.assertAlmostEqual(el[3]["Mi"], 500.0, places=2)      # 柱 2 上段顶端

    def test_preset_8b_hinge(self):
        import frame_core as fc
        model = fc.preset_8b()
        r = fc.solve(model)
        react = r["reactions"]
        self.assertAlmostEqual(react[5][0], 1.663, places=2)
        self.assertAlmostEqual(react[5][1], 30.217, places=2)
        self.assertAlmostEqual(react[5][2], 5.543, places=2)
        self.assertAlmostEqual(react[4][0], -1.663, places=2)
        self.assertAlmostEqual(react[4][1], 19.783, places=2)
        self.assertAlmostEqual(react[4][2], 16.630, places=2)
        # 铰在节点 2：梁右端、柱右顶的弯矩都必须是 0
        el = {e["no"]: e for e in r["elements"].values()}
        self.assertAlmostEqual(el[3]["Mj"], 0.0, places=6)        # 梁右 j 端
        self.assertAlmostEqual(el[4]["Mi"], 0.0, places=6)        # 柱右 i 端
        # MCT 里必须有端部释放命令块
        text = fc.build_mct(model)
        rls = [ln for ln in text.splitlines() if ln.startswith("*FRAME-RLS")]
        self.assertEqual(len(rls), 1, "8(b) 应该写一条 *FRAME-RLS 命令块")

    def test_edub_8a_has_no_release(self):
        import frame_core as fc
        text = fc.build_mct(fc.default_model())
        rls = [ln for ln in text.splitlines() if ln.startswith("*FRAME-RLS")]
        self.assertEqual(rls, [], "8(a) 没有铰，不应该写 *FRAME-RLS 命令块")


class TestCfst2TwoMethods(unittest.TestCase):
    """两跨钢管混凝土：同一模型要能用两种湿重方式各生成一套 MCT。"""

    def test_two_methods_differ_in_activation(self):
        import cfst2_core as cc
        model = cc.default_model()
        a = cc.build_mct(model, "A")
        b = cc.build_mct(model, "B")
        mct_lint.assert_ok(a)
        mct_lint.assert_ok(b)
        # 方法 A：混凝土部件在 CS3 激活、材龄 30 d；方法 B：CS2 激活、材龄 0 d
        rows_a = _block(a, "*CPOSECT4CS")
        rows_b = _block(b, "*CPOSECT4CS")
        part_a = [r for r in rows_a if r.strip().startswith("2,")]
        part_b = [r for r in rows_b if r.strip().startswith("2,")]
        self.assertEqual(len(part_a), 1)
        self.assertEqual(len(part_b), 1)
        self.assertIn("CS3", part_a[0])
        self.assertIn("CS2", part_b[0])
        fa, fb = part_a[0].split(","), part_b[0].split(",")
        self.assertEqual(fa[4].strip(), "30")
        self.assertEqual(fb[4].strip(), "0")
        # 方法 A 有湿重荷载工况，方法 B 靠单元自重、没有湿重工况
        self.assertIn("WET1", a)
        self.assertNotIn("WET1", b)


class TestSectionProps(unittest.TestCase):
    """截面特性纯函数：矩形 0.4 × 0.6 对解析解。"""

    def test_rectangle(self):
        b, h = 0.4, 0.6
        loop = [[0, 0], [b, 0], [b, h], [0, h]]
        p = mw.compute_section_props([loop])
        self.assertIsNotNone(p)
        self.assertAlmostEqual(p["A"], b * h, places=9)
        # 坐标是 (y, z)，Iyy 对应绕 y 轴（高度方向取 z）
        self.assertAlmostEqual(p["Iyy"], b * h ** 3 / 12.0, places=9)
        self.assertAlmostEqual(p["Izz"], h * b ** 3 / 12.0, places=9)
        self.assertGreater(p["Ixx"], 0)


class TestPython39Syntax(unittest.TestCase):
    """CI 的 Python 3.9 也要能跑：全仓库 .py 不允许出现 3.10+ 语法。"""

    def test_all_py_parse_with_39(self):
        bad = []
        for path in _py_files():
            with io.open(path, encoding="utf-8") as fh:
                src = fh.read()
            try:
                ast.parse(src, filename=path, feature_version=(3, 9))
            except SyntaxError as exc:
                bad.append("%s: %s" % (os.path.relpath(path, ROOT), exc))
        self.assertEqual(bad, [], "这些文件用了 Python 3.9 不支持的语法：\n" + "\n".join(bad))


if __name__ == "__main__":
    unittest.main(verbosity=2)
