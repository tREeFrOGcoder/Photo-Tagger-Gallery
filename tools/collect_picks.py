#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""成片收集(薄壳):按 tag 条件把照片【复制】到成片目录(按天建子文件夹,tag 随行)。

真正逻辑在仓库根的 phototag_core;本文件只负责解析命令行与打印。

    python3 collect_picks.py --root "/Volumes/My Book/Sony A7V" --dest ~/Pictures/成片 --where status=sooc
    python3 collect_picks.py --root ... --dest ... --where status=sooc quality=best --apply
    # 值可用逗号表示"或",none 表示"未打":--where status=sooc,edit  /  --where quality=none

多个 --where 条件之间是 AND;同名同大小跳过(可反复增量运行)。只复制,绝不动源文件。
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import phototag_core as core  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description="按 tag 条件把照片复制到成片目录(默认 dry-run)")
    ap.add_argument("--root", required=True)
    ap.add_argument("--dest", required=True, help="成片目录,如 ~/Pictures/成片")
    ap.add_argument("--where", nargs="+", default=["status=sooc"],
                    help="筛选条件,如 status=sooc quality=best;逗号=或,none=未打")
    ap.add_argument("--apply", action="store_true", help="真的复制(不加只打印)")
    args = ap.parse_args()

    root = os.path.realpath(os.path.expanduser(args.root))
    dest = os.path.realpath(os.path.expanduser(args.dest))
    if not os.path.isdir(root):
        print("!! 根目录不存在:%s" % root, file=sys.stderr)
        return 2
    try:
        cond = core.parse_where(args.where)
    except ValueError as e:
        print("!! %s" % e, file=sys.stderr)
        return 2

    plan = core.collect_plan(root, dest, cond)
    print("条件:%s" % core.where_str(cond))
    for day in plan["days"]:
        print("  %s  %d 张" % (day["day"], day["count"]))
    print("合计:%d 张,%s → %s" % (plan["total_count"], core.human(plan["total_bytes"]), dest))
    if not plan["total_count"]:
        return 0
    if not args.apply:
        print("\n[dry-run] 什么都没复制。加 --apply 执行。")
        return 0

    res = core.collect_apply(root, dest, cond)
    print("\n复制 %d,跳过(已存在) %d,错误 %d" % (res["copied"], res["skipped"], len(res["errors"])))
    for e in res["errors"][:20]:
        print("  !! " + e)
    return 1 if res["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
