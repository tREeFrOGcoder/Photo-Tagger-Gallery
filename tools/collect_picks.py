#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""成片收集:按 tag 条件把照片【复制】到成片目录(按天建子文件夹,tag 随行)。

    # 看看会复制什么(默认 dry-run)
    python3 collect_picks.py --root "/Volumes/ZTSSD/Sony A7V" --dest ~/Pictures/成片 --where status=sooc
    # 直出 + 绝美,真的复制
    python3 collect_picks.py --root ... --dest ... --where status=sooc quality=best --apply
    # 值可以用逗号表示"或",none 表示"未打":
    #   --where status=sooc,edit quality=best
    #   --where status=sooc quality=none

多个 --where 条件之间是 AND;同名文件同大小时跳过(可反复增量运行)。只复制,绝不动源文件。
"""

import argparse
import json
import os
import shutil
import sys
import time

TAGFILE = "phototags.json"
SKIP_DIR_NAMES = {"_trash_bin", "node_modules", "Library"}
DIMS = ("status", "type", "quality")


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
            yield cur, sorted(f for f in files if is_photo_name(f))


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


def human(n):
    for u in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or u == "TB":
            return "%.1f %s" % (n, u) if u != "B" else "%d B" % n
        n /= 1024.0


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
        cond = parse_where(args.where)
    except ValueError as e:
        print("!! %s" % e, file=sys.stderr)
        return 2

    plan = []  # (day_dir, day_name, [names])
    total, total_bytes = 0, 0
    for d, files in day_dirs(root):
        tags = load_tags(d)
        hit = [n for n in files if match(tags.get(n), cond)]
        if not hit:
            continue
        plan.append((d, os.path.basename(d), hit, tags))
        total += len(hit)
        for n in hit:
            try:
                total_bytes += os.path.getsize(os.path.join(d, n))
            except OSError:
                pass

    cond_str = " AND ".join("%s∈{%s}" % (k, ",".join(sorted(v))) for k, v in cond.items())
    print("条件:%s" % cond_str)
    for d, day_name, hit, _tags in plan:
        print("  %s  %d 张" % (day_name, len(hit)))
    print("合计:%d 张,%s → %s" % (total, human(total_bytes), dest))
    if not total:
        return 0
    if not args.apply:
        print("\n[dry-run] 什么都没复制。加 --apply 执行。")
        return 0

    copied, skipped, errors = 0, 0, []
    for d, day_name, hit, tags in plan:
        destdir = os.path.join(dest, day_name)
        os.makedirs(destdir, exist_ok=True)
        dtags = load_tags(destdir)
        changed = False
        for n in hit:
            src, dst = os.path.join(d, n), os.path.join(destdir, n)
            try:
                if os.path.exists(dst) and os.path.getsize(dst) == os.path.getsize(src):
                    skipped += 1
                else:
                    shutil.copy2(src, dst)
                    copied += 1
                t = tags.get(n)
                if t and dtags.get(n) != t:
                    dtags[n] = t
                    changed = True
            except OSError as e:
                errors.append("%s/%s: %s" % (day_name, n, e))
        if changed:
            save_tags(destdir, dtags)

    print("\n复制 %d,跳过(已存在) %d,错误 %d" % (copied, skipped, len(errors)))
    for e in errors[:20]:
        print("  !! " + e)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
