#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""XMP 桥(薄壳):给打了 tag 的 RAW 生成 .xmp sidecar,让 Lightroom Classic 原位识别,零复制。

真正逻辑在仓库根的 phototag_core;本文件只负责解析命令行与打印。

    python3 export_xmp.py --root "/path/to/photos"            # dry-run
    python3 export_xmp.py --root "/path/to/photos" --apply
    python3 export_xmp.py --root "/path/to/raw-photos" --where status=edit,sooc --apply

写入内容:色标 xmp:Label(大修=黄/直出=绿/废片=红)、绝美=5 星、层级关键词 phototag|维度|值。
安全:只给 RAW 写 sidecar;**绝不覆盖已存在的 .xmp**(那可能是 LrC 的修图参数),只跳过并报告。
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import phototag_core as core  # noqa: E402


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
        cond = core.parse_where(args.where)
    except ValueError as e:
        print("!! %s" % e, file=sys.stderr)
        return 2

    plan = core.xmp_plan(root, cond)
    ex = plan["extra"]
    print("条件:%s" % core.where_str(cond))
    for day in plan["days"]:
        names = day["names"]
        print("  %s  %d 个 sidecar:%s%s" %
              (day["day"], day["count"], ", ".join(names[:4]), " …" if len(names) > 4 else ""))
    print("合计:待写 %d;跳过 %d(已有 .xmp,不覆盖);%d 条 tag 找不到 RAW" %
          (plan["total_count"], ex["skip_existing"], ex["no_raw"]))
    for x in ex["existing_sample"]:
        print("  跳过已存在:%s" % x)
    for x in ex["no_raw_sample"]:
        print("  无 RAW:%s" % x)

    if not args.apply:
        if plan["total_count"]:
            print("\n[dry-run] 什么都没写。加 --apply 生成 sidecar。")
        return 0

    res = core.xmp_apply(root, cond)
    print("\n已写入 %d 个 .xmp sidecar ✓" % res["written"])
    for e in res["errors"][:20]:
        print("  !! " + e)
    print("LrC 用「添加(Add)」方式导入原文件夹,然后按色标/关键词筛选即可(见 docs/LIGHTROOM.md)。")
    return 1 if res["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
