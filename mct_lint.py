# -*- coding: utf-8 -*-
r"""MCT 结构自检工具（不需要 MIDAS，也不需要界面）。

用途：各流程生成的 .mct 在交付前先过一遍结构检查，把「引用了不存在的节点/截面/组」
「字段个数和 MIDAS 自己导出的样本对不上」这类错误在本地就抓出来。

用法：
    import mct_lint
    msgs = mct_lint.lint(text)                 # 返回 [(级别, 说明), ...]  级别 = 错误/警告
    mct_lint.assert_ok(text)                   # 有「错误」就抛 AssertionError
    mct_lint.lint_file(r"C:\path\xxx.mct")

参考样本自动从这些目录收集（有就用，没有就只做引用检查）：
    C:\Midas\work\samples\*.mct         MIDAS 教程导出的真实 MCT
    C:\Midas\wiz\out\**\*.mct           本程序以前生成、已在 MIDAS 里导入过的成果
"""
import io
import os
import re

REF_DIRS = [r"C:\Midas\work\samples",          # MIDAS 自己导出的教程样本（最权威）
            r"C:\Midas\wiz\out\fixed",        # 梁桥回归成果（以前导入过）
            r"C:\Midas\wiz\out\ui_test",      # 拱桥界面成果（以前导入过）
            r"C:\Midas\wiz\out\creep"]        # 收缩徐变成果（以前导入过）
# 多行/自由格式的命令：字段个数本来就不固定，不做字段数比对
FREE_CMDS = {"*STAGE", "*STAGE-CTRL", "*CPOSECT4CS", "*SECT-PSCVALUE", "*ELEMENT", "*UNIT",
             "*STRUCTYPE", "*MATERIAL", "*SECTION", "*TDN-PROFILE", "*TDN-PRESTRESS",
             "*ANAL-CTRL", "*ENDDATA", "*VERSION", "*PROJINFO", "*CONSTRAINT", "*GROUP",
             # 移动荷载/分析控制是多行或实验写法，本机没有真实样本可对照，字段数由 tests\test_analysis.py 里
             # 按官方 MCT 手册逐条断言，这里不做「和样本比字段数」。
             "*LINELANE", "*SURFLANE", "*VEHICLE", "*VCLASS", "*MVLDCASE", "*MOVE-CTRL",
             "*EIGEN-CTRL", "*BUCK-CTRL", "*LSUPPORT", "*LSUPPORT2"}
_CMD_RE = re.compile(r"^\*([A-Z][A-Z0-9_\-]*)")


# ----------------------------------------------------------------------
# 解析
# ----------------------------------------------------------------------
def parse(text):
    """返回 [(命令, [数据行, ...]), ...]，注释行（以 ; 开头）和空行已剔除。"""
    blocks = []
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = raw.strip()
        if not line or line.startswith(";"):
            continue
        if line.startswith("*"):
            blocks.append([line.split(";")[0].strip(), []])
        elif blocks:
            blocks[-1][1].append(line)
    return [(c, d) for c, d in blocks]


def _split(line):
    return [x.strip() for x in line.split(",")]


def _num(v):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def _range_ids(text):
    """把 '1to10 20 30to32' 展开成 int 集合。"""
    out = set()
    for tok in str(text).replace(",", " ").split():
        if "to" in tok:
            a, _, b = tok.partition("to")
            x, y = _num(a), _num(b)
            if x is not None and y is not None:
                out.update(range(min(x, y), max(x, y) + 1))
        else:
            v = _num(tok)
            if v is not None:
                out.add(v)
    return out


# ----------------------------------------------------------------------
# 参考样本的字段个数
# ----------------------------------------------------------------------
_REF_CACHE = None
_REF_ELEM = None


def reference_elem_field_counts():
    """{单元类型: {字段数, ...}}，从真实 MIDAS 样本的 *ELEMENT 里统计。"""
    global _REF_ELEM
    if _REF_ELEM is not None:
        return _REF_ELEM
    ref = {}
    files = []
    for d in REF_DIRS:
        if os.path.isdir(d):
            for root, _dirs, names in os.walk(d):
                for n in names:
                    if n.lower().endswith(".mct"):
                        files.append(os.path.join(root, n))
    for p in files:
        text = None
        for enc in ("gbk", "utf-8", "utf-8-sig"):
            try:
                text = io.open(p, encoding=enc).read()
                break
            except (UnicodeDecodeError, OSError):
                continue
        if text is None:
            continue
        for cmd, data in parse(text):
            if not cmd.startswith("*ELEMENT"):
                continue
            for line in data:
                f = _split(line)
                if len(f) >= 2:
                    ref.setdefault(f[1].upper(), set()).add(len(f))
    _REF_ELEM = ref
    return ref


def reference_field_counts():
    """{命令: {字段数, ...}}，从真实 MIDAS 导出样本里统计。"""
    global _REF_CACHE
    if _REF_CACHE is not None:
        return _REF_CACHE
    ref = {}
    files = []
    for d in REF_DIRS:
        if os.path.isdir(d):
            for root, _dirs, names in os.walk(d):
                for n in names:
                    if n.lower().endswith(".mct"):
                        files.append(os.path.join(root, n))
    for p in files:
        text = None
        for enc in ("gbk", "utf-8", "utf-8-sig"):
            try:
                text = io.open(p, encoding=enc).read()
                break
            except UnicodeDecodeError:
                continue
            except OSError:
                break
        if text is None:
            continue
        for cmd, data in parse(text):
            base = cmd.split(",")[0].strip()
            if base in FREE_CMDS:
                continue
            for line in data:
                ref.setdefault(base, set()).add(len(_split(line)))
    _REF_CACHE = ref
    return ref


# ----------------------------------------------------------------------
# 检查
# ----------------------------------------------------------------------
def lint(text, check_fields=True):
    msgs = []
    blocks = parse(text)
    if not blocks:
        return [("错误", "文件是空的（没有任何 * 命令行）")]

    names = {}
    for cmd, data in blocks:
        names.setdefault(cmd.split(",")[0].strip(), []).extend(data)

    nodes, elems, mats, sects = set(), {}, set(), set()
    for line in names.get("*NODE", []):
        v = _num(_split(line)[0]) if line else None
        if v is not None:
            nodes.add(v)
    for line in names.get("*ELEMENT", []):
        f = _split(line)
        if len(f) < 6:
            msgs.append(("错误", "*ELEMENT 行字段不足：%s" % line[:80]))
            continue
        eid = _num(f[0])
        if eid is None:
            continue
        elems[eid] = {"type": f[1].strip().upper(), "mat": _num(f[2]), "sec": _num(f[3]),
                      "n1": _num(f[4]), "n2": _num(f[5])}
    for line in names.get("*MATERIAL", []):
        v = _num(_split(line)[0])
        if v is not None:
            mats.add(v)
    for line in names.get("*SECTION", []):
        v = _num(_split(line)[0])
        if v is not None:
            sects.add(v)
    for line in names.get("*SECT-PSCVALUE", []):
        m = re.match(r"\s*SECT\s*=\s*(\d+)", line)
        if m:
            sects.add(int(m.group(1)))

    for eid, e in elems.items():
        for k in ("n1", "n2"):
            if e[k] is not None and nodes and e[k] not in nodes:
                msgs.append(("错误", "单元 %d 的节点 %d 在 *NODE 里不存在" % (eid, e[k])))
        if e["mat"] is not None and mats and e["mat"] not in mats:
            msgs.append(("错误", "单元 %d 的材料 %d 在 *MATERIAL 里不存在" % (eid, e["mat"])))
        if e["sec"] is not None and sects and e["sec"] not in sects:
            msgs.append(("错误", "单元 %d 的截面 %d 在 *SECTION/*SECT-PSCVALUE 里不存在" % (eid, e["sec"])))

    for line in names.get("*CONSTRAINT", []):
        f = _split(line)
        if not f:
            msgs.append(("错误", "*CONSTRAINT 有空行"))
            continue
        for nid in _range_ids(f[0]):
            if nodes and nid not in nodes:
                msgs.append(("错误", "*CONSTRAINT 引用了不存在的节点 %d" % nid))
        if len(f) < 2 or re.fullmatch(r"[01]{6}", f[1] or "") is None:
            msgs.append(("错误", "*CONSTRAINT 第二列应是 6 位 0/1：%s" % line[:80]))

    groups, bndrs, loadgrps, lcs = set(), set(), set(), set()
    for line in names.get("*GROUP", []):
        f = _split(line)
        if f and f[0]:
            groups.add(f[0])
    for line in names.get("*BNDR-GROUP", []):
        f = _split(line)
        if f and f[0]:
            bndrs.add(f[0])
    for line in names.get("*LOAD-GROUP", []):
        f = _split(line)
        if f and f[0]:
            loadgrps.add(f[0])
    for line in names.get("*STLDCASE", []):
        f = _split(line)
        if f and f[0]:
            lcs.add(f[0])

    for line in names.get("*GROUP", []):
        f = _split(line)
        ids = _range_ids(f[1]) if len(f) > 1 else set()
        for nid in ids:
            if nodes and nid not in nodes:
                msgs.append(("错误", "*GROUP「%s」里的节点 %d 不存在" % (f[0], nid)))
        eids = _range_ids(f[2]) if len(f) > 2 else set()
        for eid in eids:
            if elems and eid not in elems:
                msgs.append(("错误", "*GROUP「%s」里的单元 %d 不存在" % (f[0], eid)))

    for line in names.get("*PRETENSION", []):
        f = _split(line)
        for eid in _range_ids(f[0] if f else ""):
            if elems and eid not in elems:
                msgs.append(("错误", "*PRETENSION 引用了不存在的单元 %d" % eid))
            elif eid in elems and elems[eid]["type"] not in ("TENSTR", "TRUSS", "CABLE"):
                msgs.append(("警告", "*PRETENSION 单元 %d 的类型是 %s（初拉力一般加在 TENSTR/TRUSS 上）"
                             % (eid, elems[eid]["type"])))

    # *STAGE 行是「组名, 参数」成对出现的：AELEM=组, 龄期 / ABNDR=组, 位置 / ALOAD=组, 天数
    for line in names.get("*STAGE", []):
        for tag, pool, zh in (("AELEM=", groups, "结构组"), ("DELEM=", groups, "结构组"),
                              ("ABNDR=", bndrs, "边界组"), ("DBNDR=", bndrs, "边界组"),
                              ("ALOAD=", loadgrps, "荷载组"), ("DLOAD=", loadgrps, "荷载组")):
            if line.startswith(tag):
                body = line[len(tag):]
                parts = [x.strip() for x in body.split(",")]
                for k in range(0, len(parts) - 1, 2):
                    gname = parts[k]
                    if not gname:
                        continue
                    if pool and gname not in pool:
                        msgs.append(("错误", "*STAGE 的 %s 里引用了不存在的%s「%s」" % (tag[:-1], zh, gname)))

    for cmd, data in blocks:
        if cmd.startswith("*USE-STLD"):
            name = cmd.split(",", 1)[1].strip() if "," in cmd else ""
            if lcs and name not in lcs:
                msgs.append(("错误", "*USE-STLD 引用的工况「%s」不在 *STLDCASE 里" % name))

    for line in names.get("*SECT-PSCVALUE", []):
        if line.strip().startswith("OPOLY="):
            vals = [x for x in line.split("=", 1)[1].split(",") if x.strip()]
            if len(vals) < 6 or len(vals) % 2:
                msgs.append(("错误", "OPOLY 不是成对的 X,Y 坐标：%s" % line[:80]))

    # *ELEMENT 按单元类型对照真实样本的字段数（BEAM 8 / TENSTR 10 …）
    if check_fields:
        eref = reference_elem_field_counts()
        for line in names.get("*ELEMENT", []):
            f = _split(line)
            if len(f) < 2:
                continue
            t = f[1].upper()
            if t in eref and len(f) not in eref[t]:
                msgs.append(("警告", "*ELEMENT 的 %s 单元字段数是 %d，真实样本里是 %s：%s"
                             % (t, len(f), sorted(eref[t]), line[:70])))

    if check_fields:
        ref = reference_field_counts()
        for cmd, data in blocks:
            base = cmd.split(",")[0].strip()
            if base in FREE_CMDS or base not in ref or not data:
                continue
            counts = ref[base]
            bad = [(len(_split(line)), line) for line in data if len(_split(line)) not in counts]
            if bad:
                n0, line0 = bad[0]
                msgs.append(("警告", "%s 有 %d 行字段数是 %d，真实 MIDAS 样本里是 %s：%s"
                             % (base, len(bad), n0, sorted(counts), line0[:70])))
    return msgs


def lint_file(path):
    for enc in ("gbk", "utf-8", "utf-8-sig"):
        try:
            return lint(io.open(path, encoding=enc).read())
        except UnicodeDecodeError:
            continue
    return [("错误", "读不了文件（编码不对）：%s" % path)]


def assert_ok(text, allow_warn=True):
    msgs = lint(text)
    errs = [m for m in msgs if m[0] == "错误"]
    warns = [m for m in msgs if m[0] == "警告"]
    assert not errs, "MCT 结构错误：\n" + "\n".join("  · %s" % m for _l, m in errs[:12])
    if not allow_warn:
        assert not warns, "MCT 结构警告：\n" + "\n".join("  · %s" % m for _l, m in warns[:12])
    return len(errs), len(warns)


if __name__ == "__main__":
    import sys
    for p in sys.argv[1:]:
        print("=" * 20, p)
        for lvl, m in lint_file(p):
            print("  [%s] %s" % (lvl, m))
