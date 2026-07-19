#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""废片清扫:把 phototags.json 里 status=trash 的照片(JPG + 同名 RAW + `._` 伴生垃圾)
从各天文件夹**移动**到回收目录 <root>/_trash_bin/<天>/,tag 记录随迁。

    python3 sweep_trash.py --root "/Volumes/ZTSSD/Sony A7V"            # 默认 dry-run,只打印
    python3 sweep_trash.py --root "/Volumes/ZTSSD/Sony A7V" --apply    # 真的移动

本脚本【绝不删除任何文件】:确认无误后,由你自己手动清空 _trash_bin。
想反悔:把文件从 _trash_bin/<天>/ 挪回原天文件夹即可(tag 也可从 bin 的 phototags.json 拷回)。
"""

import argparse
import json
import os
import shutil
import sys
import time

TAGFILE = "phototags.json"
RAW_EXTS = (".arw", ".raw", ".dng")
SKIP_DIR_NAMES = {"_trash_bin", "node_modules", "Library"}


def is_photo_name(n):
    nl = n.lower()
    return (not n.startswith(".")) and (nl.endswith(".jpg") or nl.endswith(".jpeg"))


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


def save_tags(d, photos):
    path = os.path.join(d, TAGFILE)
    tmp = path + ".tmp"
    doc = {"version": 1, "app": "phototag", "updated": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "photos": photos}
    if os.path.exists(path):
        try:
            shutil.copy2(path, path + ".bak")
        except OSError:
            pass
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=1, sort_keys=True)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def day_dirs(root):
    for cur, dirs, files in os.walk(root):
        dirs[:] = sorted(x for x in dirs if not x.startswith(".") and x not in SKIP_DIR_NAMES)
        if any(is_photo_name(f) for f in files):
            yield cur, files


def companions(jpg_name, files):
    """一张废片要一起走的文件:JPG 本体 + 同名 RAW(不区分大小写)+ 各自的 ._ 垃圾。"""
    stem = os.path.splitext(jpg_name)[0]
    fset = set(files)
    out = [jpg_name]
    for f in files:
        if f.startswith("._"):
            continue
        fs, fe = os.path.splitext(f)
        if fs == stem and fe.lower() in RAW_EXTS:
            out.append(f)
    out += ["._" + f for f in list(out) if "._" + f in fset]
    return out


def uniq_dest(destdir, name):
    dst = os.path.join(destdir, name)
    if not os.path.exists(dst):
        return dst
    stem, ext = os.path.splitext(name)
    for i in range(1, 1000):
        dst = os.path.join(destdir, "%s_%d%s" % (stem, i, ext))
        if not os.path.exists(dst):
            return dst
    raise RuntimeError("uniq_dest exhausted: " + name)


def human(n):
    for u in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or u == "TB":
            return "%.1f %s" % (n, u) if u != "B" else "%d B" % n
        n /= 1024.0


def main():
    ap = argparse.ArgumentParser(description="把 status=trash 的照片移动到 _trash_bin(默认 dry-run)")
    ap.add_argument("--root", required=True, help="照片根目录,如 /Volumes/ZTSSD/Sony A7V")
    ap.add_argument("--bin", dest="bin_dir", default=None, help="回收目录(默认 <root>/_trash_bin)")
    ap.add_argument("--apply", action="store_true", help="真的移动(不加只打印清单)")
    args = ap.parse_args()

    root = os.path.realpath(os.path.expanduser(args.root))
    if not os.path.isdir(root):
        print("!! 根目录不存在:%s" % root, file=sys.stderr)
        return 2
    bin_dir = os.path.realpath(os.path.expanduser(args.bin_dir)) if args.bin_dir else os.path.join(root, "_trash_bin")

    total_files, total_bytes, total_photos = 0, 0, 0
    plan = []  # (day_dir, day_name, [(jpg_name, [files])])
    for d, files in day_dirs(root):
        tags = load_tags(d)
        names = {f for f in files if is_photo_name(f)}
        trash = sorted(n for n, t in tags.items()
                       if isinstance(t, dict) and t.get("status") == "trash" and n in names)
        if not trash:
            continue
        items = []
        for jpg in trash:
            fs = companions(jpg, files)
            items.append((jpg, fs))
            total_photos += 1
            for f in fs:
                total_files += 1
                try:
                    total_bytes += os.path.getsize(os.path.join(d, f))
                except OSError:
                    pass
        plan.append((d, os.path.basename(d), items))

    if not plan:
        print("没有 status=trash 的照片,无事可做 ✓")
        return 0

    print("废片清单(root=%s):" % root)
    for d, day_name, items in plan:
        print("  %s  %d 张废片:" % (day_name, len(items)))
        for jpg, fs in items:
            print("    %s  (%s)" % (jpg, ", ".join(fs[1:]) if len(fs) > 1 else "无伴生文件"))
    print("合计:%d 张废片,%d 个文件,%s" % (total_photos, total_files, human(total_bytes)))

    if not args.apply:
        print("\n[dry-run] 什么都没动。加 --apply 才会移动到:%s" % bin_dir)
        return 0

    moved, errors = 0, []
    for d, day_name, items in plan:
        destdir = os.path.join(bin_dir, day_name)
        os.makedirs(destdir, exist_ok=True)
        src_tags = load_tags(d)
        bin_tags = load_tags(destdir)
        changed = False
        for jpg, fs in items:
            ok = True
            for f in fs:
                src = os.path.join(d, f)
                try:
                    shutil.move(src, uniq_dest(destdir, f))
                    moved += 1
                except OSError as e:
                    ok = False
                    errors.append("%s/%s: %s" % (day_name, f, e))
            if ok and jpg in src_tags:
                bin_tags[jpg] = src_tags.pop(jpg)
                changed = True
        if changed:
            save_tags(d, src_tags)
            save_tags(destdir, bin_tags)

    print("\n已移动 %d 个文件到 %s" % (moved, bin_dir))
    if errors:
        print("有 %d 个错误:" % len(errors))
        for e in errors[:20]:
            print("  !! " + e)
    print("提示:没有任何文件被删除;确认无误后你可以手动清空 _trash_bin。")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
