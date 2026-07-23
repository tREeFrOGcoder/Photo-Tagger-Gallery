#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""XMP 桥:给打了 tag 的 RAW 生成 .xmp sidecar,让 Lightroom Classic 原位识别,零复制。

    # 默认:给所有 status=edit(大修)的 RAW 生成 sidecar(dry-run 只打印)
    python3 export_xmp.py --root "/Volumes/My Book/Sony A7V"
    python3 export_xmp.py --root "/Volumes/My Book/Sony A7V" --apply
    # 也可以换条件(语法同 collect_picks):
    python3 export_xmp.py --root "/Volumes/ZTSSD/Sony 6300" --where status=edit,sooc --apply

写入内容(LrC 都认):
  - 色标 xmp:Label:大修=Yellow,直出=Green,废片=Red(在 LrC 里按色标一键筛出)
  - 星级 xmp:Rating:绝美=5(普通/未打不写)
  - 层级关键词 lr:hierarchicalSubject:phototag|status|edit、phototag|type|animal 等

安全规则:
  - 只给 RAW 写 sidecar(LrC 对 JPG 不读 .xmp sidecar);tag 记在 JPG 上时自动找同名 ARW。
  - **绝不覆盖已存在的 .xmp**(那可能是 Lightroom 写的修图参数),只会跳过并报告。
"""

import argparse
import json
import os
import sys
from xml.sax.saxutils import escape

TAGFILE = "phototags.json"
RAW_EXTS = (".arw", ".raw", ".dng")
SKIP_DIR_NAMES = {"_trash_bin", "node_modules", "Library"}
DIMS = ("status", "type", "quality")
LABELS = {"edit": "Yellow", "sooc": "Green", "trash": "Red"}


def is_photo_name(n):
    nl = n.lower()
    return (not n.startswith(".")) and (nl.endswith(".jpg") or nl.endswith(".jpeg"))


def is_raw_name(n):
    nl = n.lower()
    return (not n.startswith(".")) and nl.endswith(RAW_EXTS)


def load_tags(d):
    path = os.path.join(d, TAGFILE)
    for cand in (path, path + ".bak"):
        if not os.path.exists(cand):
            continue
        try:
            with open(cand, "r", encoding="utf-8") as f:
                doc = json.load(f)
            photos = doc.get("photos")
            if isinstance(photos, dict):
                return photos
        except (json.JSONDecodeError, OSError, UnicodeDecodeError):
            pass
    return {}


def day_dirs(root):
    for cur, dirs, files in os.walk(root):
        dirs[:] = sorted(x for x in dirs if not x.startswith(".") and x not in SKIP_DIR_NAMES)
        yield cur, files


def parse_where(pairs):
    cond = {}
    for p in pairs:
        if "=" not in p:
            raise ValueError("条件要写成 key=value 形式:%s" % p)
        k, v = p.split("=", 1)
        if k not in DIMS:
            raise ValueError("未知维度 %s(可用:%s)" % (k, "/".join(DIMS)))
        cond[k] = set(x.strip() for x in v.split(",") if x.strip())
    return cond


def match(tags, cond):
    for k, allowed in cond.items():
        v = (tags or {}).get(k) or "none"
        if v not in allowed:
            return False
    return True


def find_raw(files, name):
    """tag 记在谁名下都行:本身是 RAW 用本身,是 JPG 找同名 RAW。"""
    if is_raw_name(name):
        return name
    stem = os.path.splitext(name)[0]
    for f in files:
        if not f.startswith("._") and is_raw_name(f) and os.path.splitext(f)[0] == stem:
            return f
    return None


def xmp_body(tags):
    attrs = []
    label = LABELS.get(tags.get("status") or "")
    if label:
        attrs.append('xmp:Label="%s"' % label)
    if tags.get("quality") == "best":
        attrs.append('xmp:Rating="5"')
    subjects = ["phototag|%s|%s" % (k, tags[k]) for k in DIMS if tags.get(k)]
    lis = "\n      ".join("<rdf:li>%s</rdf:li>" % escape(s) for s in subjects)
    return """<?xpacket begin="﻿" id="W5M0MpCehiHzreSzNTczkc9d"?>
<x:xmpmeta xmlns:x="adobe:ns:meta/" x:xmptk="phototag">
 <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
  <rdf:Description rdf:about=""
    xmlns:xmp="http://ns.adobe.com/xap/1.0/"
    xmlns:dc="http://purl.org/dc/elements/1.1/"
    xmlns:lr="http://ns.adobe.com/lightroom/1.0/"
    %s>
   <dc:subject><rdf:Bag>
      %s
   </rdf:Bag></dc:subject>
   <lr:hierarchicalSubject><rdf:Bag>
      %s
   </rdf:Bag></lr:hierarchicalSubject>
  </rdf:Description>
 </rdf:RDF>
</x:xmpmeta>
<?xpacket end="w"?>
""" % (" ".join(attrs), lis, lis)


def main():
    ap = argparse.ArgumentParser(description="给打 tag 的 RAW 生成 LrC 可读的 .xmp sidecar(默认 dry-run)")
    ap.add_argument("--root", required=True)
    ap.add_argument("--where", nargs="+", default=["status=edit"],
                    help="默认 status=edit(大修);语法同 collect_picks,逗号=或")
    ap.add_argument("--apply", action="store_true", help="真的写入(不加只打印)")
    args = ap.parse_args()

    root = os.path.realpath(os.path.expanduser(args.root))
    if not os.path.isdir(root):
        print("!! 根目录不存在:%s" % root, file=sys.stderr)
        return 2
    try:
        cond = parse_where(args.where)
    except ValueError as e:
        print("!! %s" % e, file=sys.stderr)
        return 2

    plan, no_raw, existing = [], [], []
    for d, files in day_dirs(root):
        tags = load_tags(d)
        if not tags:
            continue
        fset = sorted(set(files))
        for name in sorted(tags):
            t = tags[name]
            if not isinstance(t, dict) or not match(t, cond):
                continue
            raw = find_raw(fset, name)
            if not raw or not os.path.exists(os.path.join(d, raw)):
                no_raw.append(os.path.join(os.path.basename(d), name))
                continue
            xmp = os.path.join(d, os.path.splitext(raw)[0] + ".xmp")
            if os.path.exists(xmp):
                existing.append(os.path.relpath(xmp, root))
                continue
            plan.append((d, raw, xmp, t))

    cond_str = " AND ".join("%s∈{%s}" % (k, ",".join(sorted(v))) for k, v in cond.items())
    print("条件:%s" % cond_str)
    bydays = {}
    for d, raw, xmp, t in plan:
        bydays.setdefault(os.path.basename(d), []).append(raw)
    for day, names in sorted(bydays.items()):
        print("  %s  %d 个 sidecar:%s%s" % (day, len(names), ", ".join(names[:4]), " …" if len(names) > 4 else ""))
    print("合计:待写 %d;跳过 %d(已有 .xmp,不覆盖);%d 条 tag 找不到 RAW" %
          (len(plan), len(existing), len(no_raw)))
    for x in existing[:8]:
        print("  跳过已存在:%s" % x)
    for x in no_raw[:8]:
        print("  无 RAW:%s" % x)

    if not args.apply:
        if plan:
            print("\n[dry-run] 什么都没写。加 --apply 生成 sidecar。")
        return 0

    written = 0
    for d, raw, xmp, t in plan:
        tmp = xmp + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(xmp_body(t))
        os.replace(tmp, xmp)
        written += 1
    print("\n已写入 %d 个 .xmp sidecar ✓" % written)
    print("LrC 用「添加(Add)」方式导入原文件夹,然后按色标/关键词筛选即可(见 docs/LIGHTROOM.md)。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
