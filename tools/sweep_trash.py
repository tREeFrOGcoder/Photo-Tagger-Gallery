#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""废片清扫(薄壳):把 status=trash 的照片(JPG + 同名 RAW + `._` 伴生垃圾)
从各天文件夹**移动**到回收目录 <root>/_trash_bin/<天>/,tag 记录随迁。

真正逻辑在仓库根的 phototag_core;本文件只负责解析命令行与打印。

    python3 sweep_trash.py --root "/path/to/photos"            # dry-run,只打印
    python3 sweep_trash.py --root "/path/to/photos" --apply    # 真的移动

【绝不删除任何文件】:确认无误后由你自己手动清空 _trash_bin;想反悔就从 bin 挪回原天文件夹。
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import phototag_core as core  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description="把 status=trash 的照片移动到 _trash_bin(默认 dry-run)")
    ap.add_argument("--root", required=True, help="照片根目录,如 /path/to/photos")
    ap.add_argument("--bin", dest="bin_dir", default=None, help="回收目录(默认 <root>/_trash_bin)")
    ap.add_argument("--apply", action="store_true", help="真的移动(不加只打印清单)")
    args = ap.parse_args()

    root = os.path.realpath(os.path.expanduser(args.root))
    if not os.path.isdir(root):
        print("!! 根目录不存在:%s" % root, file=sys.stderr)
        return 2
    bin_dir = os.path.realpath(os.path.expanduser(args.bin_dir)) if args.bin_dir \
        else os.path.join(root, "_trash_bin")

    plan = core.sweep_plan(root, bin_dir)
    if not plan["days"]:
        print("没有 status=trash 的照片,无事可做 ✓")
        return 0

    print("废片清单(root=%s):" % root)
    for day in plan["days"]:
        print("  %s  %d 张废片:" % (day["day"], day["count"]))
        for it in day["items"]:
            fs = it["files"]
            print("    %s  (%s)" % (it["name"], ", ".join(fs[1:]) if len(fs) > 1 else "无伴生文件"))
    print("合计:%d 张废片,%d 个文件,%s" %
          (plan["total_count"], plan["extra"]["files"], core.human(plan["total_bytes"])))

    if not args.apply:
        print("\n[dry-run] 什么都没动。加 --apply 才会移动到:%s" % bin_dir)
        return 0

    res = core.sweep_apply(root, bin_dir)
    print("\n已移动 %d 个文件到 %s" % (res["moved"], bin_dir))
    if res["errors"]:
        print("有 %d 个错误:" % len(res["errors"]))
        for e in res["errors"][:20]:
            print("  !! " + e)
    print("提示:没有任何文件被删除;确认无误后你可以手动清空 _trash_bin。")
    return 1 if res["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
