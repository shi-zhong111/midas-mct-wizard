# -*- coding: utf-8 -*-
"""midas_wizard 的单元测试。

只测纯函数（不启动 Tk、不连 MIDAS、不写工程目录），所以 Windows / Linux / macOS
和 CI 上都能跑：

    python -m unittest discover -s tests -v
    python midas_wizard.py --selftest      # 同一批断言的轻量版

重点覆盖「算错了但看起来正常」的那几处：截面特性、扭转常数、工况名一致性、
非有限数输入。
"""
import math
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import midas_wizard as mw   # noqa: E402


def box_loops(b, h, t):
    """外轮廓 b×h、壁厚 t 的矩形管（两根闭合环）。"""
    outer = [[0, 0], [b, 0], [b, h], [0, h]]
    inner = [[t, t], [b - t, t], [b - t, h - t], [t, h - t]]
    return [outer, inner]


class TestNumbers(unittest.TestCase):
    def test_is_num_rejects_non_finite(self):
        for bad in ("nan", "inf", "-inf", "1e999", "", "  ", "abc", None):
            self.assertFalse(mw.is_num(bad), "is_num(%r) 应为 False" % (bad,))

    def test_is_num_accepts_numbers(self):
        for good in ("0", "1", "-2.5", " 3 ", "1e-5", "+7"):
            self.assertTrue(mw.is_num(good), "is_num(%r) 应为 True" % (good,))

    def test_num_never_raises(self):
        """nan/1e999 以前会在 int() 那一步抛 OverflowError，而 pythonw 下看不到报错。"""
        self.assertEqual(mw.num("nan"), "0")
        self.assertEqual(mw.num("1e999"), "0")
        self.assertEqual(mw.num(None), "0")
        self.assertEqual(mw.num("abc"), "0")

    def test_num_formatting(self):
        self.assertEqual(mw.num("2.0"), "2")
        self.assertEqual(mw.num("-3.50"), "-3.5")
        self.assertEqual(mw.num(0), "0")

    def test_numg_significant_digits(self):
        self.assertEqual(mw.numg(0.39612500000000017), "0.396125")
        self.assertEqual(mw.numg(None), "0")


class TestNaming(unittest.TestCase):
    def test_aname_keeps_non_ascii(self):
        """中文名要原样保留 —— 实测 MIDAS 能导入中文名。

        早先的版本把非 ASCII 全删掉，用户填「自重」「二期」会被抹成兜底名 LC，
        几个工况撞成同一个名字，荷载被合并。那是错的。
        """
        self.assertEqual(mw.aname("主梁"), "主梁")
        self.assertEqual(mw.aname("自重"), "自重")
        self.assertEqual(mw.aname("GIRDER"), "GIRDER")
        self.assertEqual(mw.aname(" 二期 "), "二期")

    def test_aname_falls_back_when_empty(self):
        self.assertEqual(mw.aname("", "MAT1"), "MAT1")
        self.assertEqual(mw.aname("   ", "SEC2"), "SEC2")

    def test_aname_removes_separators(self):
        """逗号必须清掉：MCT 用逗号分隔字段，名称里带逗号会让整行错列。"""
        self.assertNotIn(",", mw.aname("a,b"))
        self.assertNotIn("\n", mw.aname("a\nb"))
        self.assertNotIn("\t", mw.aname("a\tb"))
        self.assertEqual(mw.aname("a  b"), "a b")

    def test_aascii_for_groups(self):
        """边界组名仍然清非 ASCII —— 作者实测组名带中文会让整份导入失败。"""
        self.assertEqual(mw.aascii("中支座"), "")
        self.assertEqual(mw.aascii("GRP-A"), "GRP-A")


class TestSectionProps(unittest.TestCase):
    def test_solid_square(self):
        p = mw.compute_section_props([[[0, 0], [2, 0], [2, 2], [0, 2]]])
        self.assertAlmostEqual(p["A"], 4.0, places=9)
        self.assertAlmostEqual(p["Iyy"], 4.0 / 3.0, places=9)
        self.assertAlmostEqual(p["Izz"], 4.0 / 3.0, places=9)

    def test_rectangle_axes(self):
        # 宽 b=4 (along y), 高 h=2 (along z): Iyy = b*h^3/12, Izz = h*b^3/12
        p = mw.compute_section_props([[[0, 0], [4, 0], [4, 2], [0, 2]]])
        self.assertAlmostEqual(p["A"], 8.0, places=9)
        self.assertAlmostEqual(p["Iyy"], 4 * 2 ** 3 / 12.0, places=9)
        self.assertAlmostEqual(p["Izz"], 2 * 4 ** 3 / 12.0, places=9)
        self.assertAlmostEqual(p["Cy"], 2.0, places=9)
        self.assertAlmostEqual(p["Cz"], 1.0, places=9)

    def test_hollow_box_matches_bredt(self):
        """薄壁矩形管的自由扭转常数有解析解 t(b-t)³(h-t)³/((b-t)+(h-t))。

        旧版本对空心截面套实心近似 A⁴/(40·Ip)，这个工况下会低估 200 倍以上。
        """
        b, h, t = 2.0, 1.5, 0.05
        a_m = (b - t) * (h - t)
        s_m = 2.0 * ((b - t) + (h - t))
        want = 4.0 * a_m * a_m * t / s_m
        p = mw.compute_section_props(box_loops(b, h, t))
        self.assertAlmostEqual(p["A"], b * h - (b - 2 * t) * (h - 2 * t), places=9)
        self.assertLess(abs(p["Ixx"] - want) / want, 0.06,
                        "J=%.5f vs thin-wall %.5f" % (p["Ixx"], want))

        # and it must not fall back to the old solid approximation (200x off here)
        ip = p["Iyy"] + p["Izz"]
        self.assertGreater(p["Ixx"], (p["A"] ** 4) / (40.0 * ip) * 50,
                           "torsion constant regressed to the solid approximation")

    def test_hollow_box_area_and_inertia(self):
        p = mw.compute_section_props(box_loops(2.0, 1.5, 0.1))
        outer_a = 2.0 * 1.5
        inner_a = 1.8 * 1.3
        self.assertAlmostEqual(p["A"], outer_a - inner_a, places=9)
        self.assertAlmostEqual(p["Iyy"], (2.0 * 1.5 ** 3 - 1.8 * 1.3 ** 3) / 12.0, places=9)

    def test_shear_areas_present(self):
        """*SECTION VALUE 那一路要读 ASy/ASz；以前没有这两个键，写出去是 0。"""
        p = mw.compute_section_props([[[0, 0], [2, 0], [2, 2], [0, 2]]])
        self.assertIn("ASy", p)
        self.assertIn("ASz", p)
        self.assertGreater(p["ASy"], 0)

    def test_orientation_invariance(self):
        # same loops traversed the other way round, and with a rotated start point
        base = box_loops(2.0, 1.5, 0.2)
        ref = mw.compute_section_props(base)
        both_ccw = mw.compute_section_props([list(base[0]), list(base[1])])
        both_cw = mw.compute_section_props([base[0][::-1], base[1][::-1]])
        rotated = mw.compute_section_props([[base[0][2], base[0][3], base[0][0], base[0][1]], base[1]])
        for got in (both_ccw, both_cw, rotated):
            self.assertAlmostEqual(got["A"], ref["A"], places=9)
            self.assertAlmostEqual(got["Iyy"], ref["Iyy"], places=9)
            self.assertAlmostEqual(got["Izz"], ref["Izz"], places=9)
            self.assertAlmostEqual(got["Ixx"], ref["Ixx"], places=9)
    def test_far_from_origin(self):
        """CAD 里截面常画在离原点很远的地方；结果不能被大坐标抹平。"""
        far = mw.compute_section_props([[[317321, 0], [317323, 0], [317323, 2], [317321, 2]]])
        self.assertAlmostEqual(far["A"], 4.0, places=6)
        self.assertAlmostEqual(far["Iyy"], 4.0 / 3.0, places=6)

    def test_degenerate_returns_none(self):
        self.assertIsNone(mw.compute_section_props([]))
        self.assertIsNone(mw.compute_section_props([[[0, 0], [1, 0]]]))
        self.assertIsNone(mw.compute_section_props([[[0, 0], [1, 0], [2, 0]]]))

    def test_absurd_coordinates_fail_cleanly(self):
        """1e308 这种坐标以前在 _perimeter() 里抛 OverflowError。

        OverflowError 不是 ValueError，接不住，界面上只会显示一句看不懂的报错。
        现在应当在算几何之前就判失败、返回 None（界面提示"解析不出来"）。
        """
        for lp in ([[1e308, 1e308], [-1e308, 0.0], [0.0, -1e308]],
                   [[float("nan"), 0], [1, 0], [1, 1]],
                   [[float("inf"), 0], [1, 0], [1, 1]],
                   [[1e160, 0], [1e160 + 2, 0], [1e160 + 2, 2]]):
            self.assertIsNone(mw.compute_section_props([lp]), "应返回 None：%r" % (lp,))

    def test_large_but_sane_coordinates_still_work(self):
        """正常的"画在图纸中间"（几万~几十万）不能受上面那条影响。"""
        for base in (317321, 1e6, 1e8):
            lp = [[base, base], [base + 2, base], [base + 2, base + 2], [base, base + 2]]
            p = mw.compute_section_props([lp])
            self.assertIsNotNone(p, "坐标 %g 应该能算出来" % base)
            self.assertAlmostEqual(p["A"], 4.0, places=4)

    def test_unit_scaling_hint(self):
        """面积离谱时要能反推出图纸单位选错了。"""
        sec = {"unit": "0.001", "props": {"A": 1.35e6}}
        self.assertTrue(mw.suggest_unit(sec))
        self.assertTrue(mw.unit_advice(sec))


class TestBuildMct(unittest.TestCase):
    def setUp(self):
        m = mw.default_model()
        m["selfweight"]["lc"] = m["loadcases"][0]["name"]
        m["beamloads"][0]["lc"] = m["loadcases"][0]["name"]
        self.model = m

    def test_chinese_names_survive_to_the_mct(self):
        """给中文工况名/材料名/截面名，写出去必须还是中文，不能被抹掉或撞车。"""
        m = mw.default_model()
        m["loadcases"] = [{"name": "自重", "type": "D", "desc": "自身重力"},
                          {"name": "二期", "type": "L", "desc": "二期恒载"}]
        m["materials"][0]["name"] = "混凝土"
        m["sections"][0]["name"] = "主梁"
        m["selfweight"] = {"on": True, "lc": "自重", "x": "0", "y": "0", "z": "-1", "group": ""}
        m["beamloads"][0]["lc"] = "二期"
        text = mw.build_mct(m)
        self.assertIn("自重", text)
        self.assertIn("二期", text)
        self.assertIn("混凝土", text)
        self.assertIn("主梁", text)
        # 两个工况必须各写一行、各挂各的荷载
        self.assertIn("*USE-STLD, 自重", text)
        self.assertIn("*USE-STLD, 二期", text)
        # 不能再退回兜底名
        self.assertNotIn(", LC,", text)

    def test_ends_with_enddata(self):
        self.assertIn("*ENDDATA", mw.build_mct(self.model))

    def test_loadcase_names_match_between_stldcase_and_use_stld(self):
        """*STLDCASE 与 *USE-STLD 必须写同一个名字，否则 MIDAS 找不到工况、荷载全丢。"""
        m = mw.default_model()
        m["loadcases"] = [{"name": "恒载,1", "type": "D", "desc": ""}]
        m["beamloads"][0]["lc"] = "恒载,1"
        m["selfweight"] = {"on": False, "lc": "", "x": "0", "y": "0", "z": "-1", "group": ""}
        text = mw.build_mct(m)
        declared, in_case = [], False
        for line in text.splitlines():
            if line.startswith("*"):
                in_case = line.startswith("*STLDCASE")
                continue
            if in_case and line.startswith("   "):
                declared.append(line.split(",")[0].strip())
        used = [l.split(",", 1)[1].strip() for l in text.splitlines() if l.startswith("*USE-STLD,")]
        self.assertTrue(declared)
        self.assertTrue(used)
        for name in used:
            self.assertIn(name, declared)

    def test_compat_unit_has_two_fields(self):
        """Civil 2022 的 *UNIT 只有力/长度两个字段。"""
        self.model["project"]["compat"] = True
        line = [l for l in mw.build_mct(self.model).splitlines()
                if l.startswith("   ") and "KN" in l][0]
        self.assertEqual(len(line.split(",")), 2)

    def test_nx_unit_has_four_fields(self):
        line = [l for l in mw.build_mct(self.model).splitlines()
                if l.startswith("   ") and "KN" in l][0]
        self.assertEqual(len(line.split(",")), 4)

    def _material_line(self, model):
        return [l for l in mw.build_mct(model).splitlines()
                if l.startswith("   1, ") and (", CONC," in l or ", STEEL," in l)][0]

    def test_material_line_field_positions(self):
        """*MATERIAL 的字段顺序必须和 MIDAS 一致。

        出过一次真实事故：PLAST 的空字段被省掉，整行往前串一位，MIDAS 连报
        三个错——TUNIT 收到 C、bMASS 收到 NO、DAMPRATIO 收到 0.05：
            「TUNIT值有错误」/「必须输入是或否」/「整数值错误」
        期望顺序：iMAT, TYPE, MNAME, SPHEAT, HEATCO, PLAST, TUNIT, bMASS, DAMPRATIO
        """
        line = self._material_line(self.model)
        f = [p.strip() for p in line.split(",")]
        self.assertEqual(f[0], "1", "iMAT")
        self.assertEqual(f[1], "CONC", "TYPE")
        self.assertEqual(f[2], "C50", "MNAME")
        self.assertEqual(f[3], "0", "SPHEAT")
        self.assertEqual(f[4], "0", "HEATCO")
        self.assertEqual(f[5], "", "PLAST 必须是空字段")
        self.assertEqual(f[6], "C", "TUNIT")
        self.assertEqual(f[7], "NO", "bMASS")
        self.assertEqual(f[8], "0.05", "DAMPRATIO")

    def test_material_line_matches_verified_export(self):
        """这一行是照 MIDAS 自己导出、且导入实测通过的原文写的，不能改。"""
        verified = "   1, CONC, C50, 0, 0, , C, NO, 0.05, 2, 34500000, 0.2, 1e-05, 25, 0"
        self.assertEqual(self._material_line(self.model), verified)

    def test_material_db_mode_field_positions(self):
        """规范数据库模式（DATA1 mode=1）同样不能错位。"""
        m = mw.default_model()
        m["materials"][0].update({"mode": "db", "standard": "JTG3362-18(RC)", "dbname": "C50"})
        f = [p.strip() for p in self._material_line(m).split(",")]
        self.assertEqual(f[5], "", "PLAST 必须是空字段")
        self.assertEqual(f[6], "C", "TUNIT")
        self.assertEqual(f[7], "NO", "bMASS")
        self.assertEqual(f[8], "0.05", "DAMPRATIO")
        self.assertEqual(f[9], "1", "DATA1 mode=1 表示走数据库")
        self.assertEqual(f[10], "JTG3362-18(RC)", "STANDARD")
        self.assertEqual(f[12], "C50", "DB name")

    def test_compat_material_has_no_dampratio(self):
        """Civil 2022 没有 DAMPRATIO，但 PLAST 的空字段仍然要在。"""
        m = mw.default_model()
        m["project"]["compat"] = True
        f = [p.strip() for p in self._material_line(m).split(",")]
        self.assertEqual(f[5], "", "PLAST 必须是空字段")
        self.assertEqual(f[6], "C", "TUNIT")
        self.assertEqual(f[7], "NO", "bMASS")
        self.assertEqual(f[8], "2", "compat 下第 9 位直接是 DATA1 mode")

    def test_section_writer_emits_shear_areas(self):
        """CAD 数值截面那一路必须写出非 0 的 ASy/ASz。"""
        m = mw.default_model()
        m["sections"] = [{"name": "BOX", "shape": "CAD", "dims": [], "offset": "CC",
                          "shear": "YES", "warp": "NO", "file": "x.dxf", "unit": "0.001",
                          "props": mw.compute_section_props(box_loops(2.0, 1.5, 0.1))}]
        m["elements"][0]["sect"] = "1"
        text = mw.build_mct(m)
        self.assertIn("*SECT-PSCVALUE", text)
        # second row is AREA, ASy, ASz, Ixx, Iyy, Izz -- shear areas must not be 0
        area_line = [l for l in text.splitlines()
                     if l.startswith("        ") and l.count(",") == 5][0]
        area, asy, asz = [float(x) for x in area_line.split(",")[:3]]
        self.assertAlmostEqual(area, 0.66, places=6)
        self.assertGreater(asy, 0.0, "ASy written as 0")
        self.assertGreater(asz, 0.0, "ASz written as 0")

    def test_group_names_are_ascii(self):
        """边界组名是唯一的例外：作者实测组名带中文会让整份导入失败，所以清掉。"""
        m = mw.default_model()
        m["supports"][0]["group"] = "中支座"
        m["selfweight"]["lc"] = m["loadcases"][0]["name"]
        body = "\n".join(l for l in mw.build_mct(m).splitlines()
                         if not l.lstrip().startswith(";"))
        self.assertNotIn("中支座", body)


class TestValidation(unittest.TestCase):
    def test_default_model_is_valid(self):
        m = mw.default_model()
        m["selfweight"]["lc"] = m["loadcases"][0]["name"]
        for step in ("nodes", "elements", "materials", "sections", "supports", "loadcases", "loads"):
            self.assertEqual(mw.issues_for(step, m), [], "默认模型在第 %s 步就有问题" % step)

    def test_sec_assign_out_of_range_is_caught(self):
        """分段表以前完全不校验，能写出引用不存在截面的 *ELEMENT。"""
        m = mw.default_model()
        m["sec_assign"] = [{"from": "1", "to": "999", "sect": "99"}]
        self.assertTrue(mw.issues_for("sections", m))

    def test_two_chinese_loadcase_names_are_allowed(self):
        """两个不同的中文工况名是合法的 —— 名称原样写进 MCT，不会撞车。

        这里曾经拦过：早先版本把中文名清洗成兜底名 LC，两个工况撞成一个，
        MIDAS 报「数据 *STLDCASE 被修改」并合并荷载。修法是**保留中文名**，
        而不是让用户改名。
        """
        m = mw.default_model()
        m["loadcases"] = [{"name": "自重", "type": "D", "desc": ""},
                          {"name": "二期", "type": "L", "desc": ""}]
        m["beamloads"][0]["lc"] = "二期"
        m["selfweight"] = {"on": True, "lc": "自重", "x": "0", "y": "0", "z": "-1", "group": ""}
        self.assertEqual(mw.issues_for("loadcases", m), [])
        text = mw.build_mct(m)
        self.assertIn("*USE-STLD, 自重", text)
        self.assertIn("*USE-STLD, 二期", text)

    def test_duplicate_chinese_loadcase_names_still_caught(self):
        """真正重名（一个字都不差）仍然要拦。"""
        m = mw.default_model()
        m["loadcases"] = [{"name": "自重", "type": "D", "desc": ""},
                          {"name": "自重", "type": "L", "desc": ""}]
        self.assertTrue(mw.issues_for("loadcases", m))

    def test_comma_in_name_is_caught(self):
        """名称里带逗号会让 MCT 整行错列，必须拦。"""
        m = mw.default_model()
        m["loadcases"] = [{"name": "恒载,1", "type": "D", "desc": ""}]
        issues = mw.issues_for("loadcases", m)
        self.assertTrue(issues)
        self.assertIn("逗号", " ".join(issues))

    def test_comma_in_material_and_section_name_is_caught(self):
        m = mw.default_model()
        m["materials"][0]["name"] = "C50,高强"
        self.assertTrue(mw.issues_for("materials", m))
        m2 = mw.default_model()
        m2["sections"][0]["name"] = "主梁,加宽"
        self.assertTrue(mw.issues_for("sections", m2))

    def test_distinct_ascii_loadcase_names_stay_clean(self):
        m = mw.default_model()
        m["loadcases"] = [{"name": "DL", "type": "D", "desc": ""},
                          {"name": "LL", "type": "L", "desc": ""}]
        m["beamloads"][0]["lc"] = "DL"
        m["selfweight"]["lc"] = "DL"
        self.assertEqual(mw.issues_for("loadcases", m), [])

    def test_chinese_material_names_are_allowed(self):
        m = mw.default_model()
        m["materials"] = [{"name": "混凝土", "type": "CONC", "mode": "user", "spec": "自定义",
                           "elast": "3e7", "poisn": "0.2", "den": "25", "thermal": "1e-5"},
                          {"name": "钢材", "type": "STEEL", "mode": "user", "spec": "自定义",
                           "elast": "2e8", "poisn": "0.3", "den": "76.98", "thermal": "1.2e-5"}]
        self.assertEqual(mw.issues_for("materials", m), [])

    def test_sec_assign_reversed_range_is_caught(self):
        m = mw.default_model()
        m["sec_assign"] = [{"from": "3", "to": "1", "sect": "1"}]
        self.assertTrue(mw.issues_for("sections", m))

    def test_sec_assign_valid_is_clean(self):
        m = mw.default_model()
        m["sec_assign"] = [{"from": "1", "to": "1", "sect": "1"}]
        self.assertEqual(mw.issues_for("sections", m), [])

    def test_nan_node_is_caught(self):
        m = mw.default_model()
        m["nodes"] = [{"x": "nan", "y": "0", "z": "0", "note": ""}]
        self.assertTrue(mw.issues_for("nodes", m))

    def test_duplicate_loadcase_name_is_caught(self):
        m = mw.default_model()
        m["loadcases"] = [{"name": "DL", "type": "D", "desc": ""},
                          {"name": "DL", "type": "L", "desc": ""}]
        self.assertTrue(mw.issues_for("loadcases", m))

    def test_missing_loadcase_reference_is_caught(self):
        m = mw.default_model()
        m["beamloads"][0]["lc"] = "NOPE"
        self.assertTrue(mw.issues_for("loads", m))


class TestModelSchema(unittest.TestCase):
    def test_ensure_model_fills_missing_keys(self):
        m = mw.ensure_model({"nodes": [{"x": "1", "y": "0", "z": "0", "note": ""}]})
        for key in ("project", "nodes", "elements", "materials", "sections",
                    "supports", "loadcases", "selfweight", "beamloads"):
            self.assertIn(key, m)

    def test_ensure_model_keeps_old_extra_keys(self):
        """旧工程 JSON 里有已经不用的键（如 resdir），不能因此读不了。"""
        m = mw.ensure_model({"nodes": [], "project": {"name": "old", "resdir": "x"}})
        self.assertEqual(m["project"]["name"], "old")

    def test_ensure_model_handles_garbage(self):
        for junk in (None, [], "x", 42):
            m = mw.ensure_model(junk)
            self.assertIn("project", m)

    def test_ensure_model_drops_non_dict_rows(self):
        """列表里混进 null / 数字 / 字符串时，必须把它们剔掉。

        以前只保证顶层键和"是个列表"，行本身没管；之后任何 row.get()
        都直接 AttributeError —— 正是 ensure_model 本来要防的那类崩溃。
        """
        m = mw.ensure_model({"nodes": [None, 5, "x", {"x": "1", "y": "0", "z": "0"}]})
        self.assertEqual(len(m["nodes"]), 1)
        self.assertIsInstance(m["nodes"][0], dict)
        # 然后必须真的能跑通
        self.assertTrue(mw.build_mct(m))
        self.assertTrue(mw.build_data_report(m))
        self.assertTrue(mw.all_issues(m) is not None)

    def test_ensure_model_drops_non_dict_rows_in_every_list(self):
        junk = {k: [None, 7, "s", {}] for k in
                ("nodes", "elements", "materials", "sections", "supports",
                 "loadcases", "nodalloads", "beamloads")}
        m = mw.ensure_model(junk)
        for k in junk:
            for row in m[k]:
                self.assertIsInstance(row, dict, "%s 里混进了非字典行" % k)
        self.assertTrue(mw.build_mct(m))

    def test_partial_model_can_be_rendered_to_mct(self):
        self.assertTrue(mw.build_mct(mw.ensure_model({"nodes": []})))

    def test_data_report_survives_tapered_section(self):
        """build_data_report 以前引用未定义的 out，会 NameError。"""
        m = mw.default_model()
        m["sections"] = [{"name": "S1", "shape": "SB", "dims": ["1", "1"], "offset": "CC",
                          "shear": "YES", "warp": "NO", "file": "", "unit": "0.001", "props": None},
                         {"name": "TS1", "shape": "TS", "dims": [], "offset": "CC",
                          "shear": "YES", "warp": "NO", "file": "", "unit": "0.001", "props": None,
                          "i_sect": "1", "j_sect": "2"}]
        self.assertTrue(mw.build_data_report(m))


class TestDxfReading(unittest.TestCase):
    def _write(self, body):
        import tempfile
        fd, path = tempfile.mkstemp(suffix=".dxf")
        os.close(fd)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(body)
        self.addCleanup(os.remove, path)
        return path

    def test_lwpolyline_closed(self):
        dxf = ("0\nSECTION\n2\nENTITIES\n"
               "0\nLWPOLYLINE\n8\n0\n90\n4\n70\n1\n"
               "10\n0\n20\n0\n10\n2\n20\n0\n10\n2\n20\n2\n10\n0\n20\n2\n"
               "0\nENDSEC\n0\nEOF\n")
        loops, notes = mw.read_dxf_loops(self._write(dxf))
        self.assertEqual(len(loops), 1)

    def test_classic_polyline_vertex_records(self):
        """经典 DXF 把顶点放在后面的 VERTEX 实体里 —— 以前整条轮廓会被丢掉。"""
        pts = [(0, 0), (3, 0), (3, 3), (0, 3)]
        body = "0\nSECTION\n2\nENTITIES\n0\nPOLYLINE\n8\n0\n66\n1\n70\n1\n"
        for x, y in pts:
            body += "0\nVERTEX\n8\n0\n10\n%g\n20\n%g\n" % (x, y)
        body += "0\nSEQEND\n8\n0\n0\nENDSEC\n0\nEOF\n"
        loops, notes = mw.read_dxf_loops(self._write(body))
        self.assertEqual(len(loops), 1, "经典 POLYLINE 没被读出来；notes=%s" % notes)
        p = mw.compute_section_props(loops)
        self.assertAlmostEqual(p["A"], 9.0, places=6)

    def test_circle(self):
        dxf = ("0\nSECTION\n2\nENTITIES\n"
               "0\nCIRCLE\n8\n0\n10\n0\n20\n0\n40\n500\n"
               "0\nENDSEC\n0\nEOF\n")
        loops, _ = mw.read_dxf_loops(self._write(dxf), unit_scale=0.001)
        p = mw.compute_section_props(loops)
        # the circle is tessellated into a polygon, so its area is slightly under pi*r^2
        self.assertLess(abs(p["A"] - math.pi * 0.5 ** 2) / (math.pi * 0.25), 0.002)

    def test_missing_file_reports_error(self):
        loops, notes = mw.read_dxf_loops(os.path.join(os.path.dirname(__file__), "nope.dxf"))
        self.assertEqual(loops, [])
        self.assertTrue(notes)

    def test_shipped_sample(self):
        """仓库里带的示例箱形截面：A=1.35 m²，Iyy/Izz 与手算一致。"""
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "examples", "box-2000x1500.dxf")
        if not os.path.isfile(path):
            self.skipTest("示例 DXF 不在")
        loops, _ = mw.read_dxf_loops(path, unit_scale=0.001)
        p = mw.compute_section_props(loops)
        self.assertAlmostEqual(p["A"], 1.35, places=6)
        self.assertAlmostEqual(p["Iyy"], 0.396125, places=6)
        self.assertAlmostEqual(p["Izz"], 0.690625, places=6)


class TestSelftest(unittest.TestCase):
    def test_selftest_passes(self):
        self.assertEqual(mw.selftest(verbose=False), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
