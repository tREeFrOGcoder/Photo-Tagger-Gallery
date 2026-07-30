#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""phototag 共享核心(仅标准库)。

把过去在 serve.py 与 tools/*.py 里各抄一遍的东西收到一处:名称助手、tag 读写、
条件筛选、天目录遍历,以及四个批处理操作(collect / xmp / sync / sweep)统一成
`*_plan()`(只读,产出清单)+ `*_apply(on_progress)`(执行,带进度回调)。

设计约定:
  - plan 只读,绝不产生副作用;apply 内部**重新遍历**再执行,不信任陈旧 plan。
  - 每个操作自带的守卫(同名同大小跳过 / 不覆盖已有 .xmp / 只补空白 / 只移不删)
    才是真正的安全保证,预览只是给人看的。
  - 本模块保持无状态、无线程;并发/进度/日志由调用方(server 起 job、CLI 直接跑)负责。

入口脚本用法:把仓库根加入 sys.path 后 `import phototag_core as core`。
"""

import json
import os
import shutil
import time
from datetime import datetime
from xml.sax.saxutils import escape

# ---------- 常量 ----------

TAGFILE = "phototags.json"
RAW_EXTS = (".arw", ".raw", ".dng")
SKIP_DIR_NAMES = {"_trash_bin", "node_modules", "Library"}
DIMS = ("status", "type", "quality")
VALID_TAGS = {
    "status": {"sooc", "edit", "trash"},
    "type": {"scenery", "animal", "portrait", "insect"},
    "quality": {"best", "normal"},
}
# LrC 色标:大修=黄、直出=绿、废片=红
LABELS = {"edit": "Yellow", "sooc": "Green", "trash": "Red"}


# ---------- 名称助手 ----------

def is_photo_name(n):
    nl = n.lower()
    return (not n.startswith(".")) and (nl.endswith(".jpg") or nl.endswith(".jpeg"))


def is_raw_name(n):
    return (not n.startswith(".")) and n.lower().endswith(RAW_EXTS)


def is_media_name(n):
    """可展示/可打 tag 的文件:JPG,以及(所在目录无同名 JPG 的)孤 ARW。"""
    return is_photo_name(n) or is_raw_name(n)


def media_names(files):
    """照片名集合:全部 JPG + 无同名 JPG 的孤 ARW(如 6300 纯 RAW 库)。"""
    jpg = {f for f in files if is_photo_name(f)}
    stems = {os.path.splitext(f)[0] for f in jpg}
    return jpg | {f for f in files if is_raw_name(f) and os.path.splitext(f)[0] not in stems}


def stem_of(name):
    return os.path.splitext(name)[0]


# ---------- tag 读写 ----------

def now_iso():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def load_tags(d):
    """读取某文件夹的 tag 映射;主文件坏了自动留证并回退 .bak,再不行回空。"""
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
            if cand == path:
                try:
                    os.replace(path, path + ".corrupt.%d" % int(time.time()))
                except OSError:
                    pass
    return {}


def save_tags(d, photos):
    """原子写 + 写前 .bak;内容与 server/CLI 历史格式一致(indent=1, sort_keys)。"""
    path = os.path.join(d, TAGFILE)
    tmp = path + ".tmp"
    doc = {"version": 1, "app": "phototag", "updated": now_iso(), "photos": photos}
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


def clean_tags(t):
    """只保留合法维度取值;自由标签 x 数组透传。"""
    out = {}
    if not isinstance(t, dict):
        return out
    for k, vals in VALID_TAGS.items():
        v = t.get(k)
        if isinstance(v, str) and v in vals:
            out[k] = v
    if isinstance(t.get("x"), list):
        xs = [s for s in t["x"] if isinstance(s, str) and s.strip()][:32]
        if xs:
            out["x"] = xs
    return out


# ---------- 条件筛选 ----------

def parse_where(pairs):
    """['status=sooc,edit', 'quality=best'] -> {'status': {'sooc','edit'}, 'quality': {'best'}}。"""
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
    """维度内是"或",维度间是"且";'none' 表示该维度未打。"""
    for k, allowed in cond.items():
        v = (tags or {}).get(k) or "none"
        if v not in allowed:
            return False
    return True


def where_str(cond):
    return " AND ".join("%s∈{%s}" % (k, ",".join(sorted(v))) for k, v in cond.items()) or "(全部)"


# ---------- 遍历 / 杂项 ----------

def day_dirs(root):
    """遍历 root 下所有目录(剪掉隐藏与 SKIP_DIR_NAMES),逐个 yield (目录, 文件名列表)。"""
    for cur, dirs, files in os.walk(root):
        dirs[:] = sorted(x for x in dirs if not x.startswith(".") and x not in SKIP_DIR_NAMES)
        yield cur, files


def human(n):
    for u in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or u == "TB":
            return "%.1f %s" % (n, u) if u != "B" else "%d B" % n
        n /= 1024.0


def _safe_size(path):
    try:
        return os.path.getsize(path)
    except OSError:
        return 0


def find_raw(files, name):
    """tag 记在谁名下都行:本身是 RAW 用本身,是 JPG 找同名 RAW。"""
    if is_raw_name(name):
        return name
    stem = stem_of(name)
    for f in files:
        if not f.startswith("._") and is_raw_name(f) and stem_of(f) == stem:
            return f
    return None


def companions(name, files):
    """一张废片要一起走的文件:本体 + 同名 JPG/RAW 兄弟 + 各自的 ._ 垃圾。"""
    stem = stem_of(name)
    fset = set(files)
    out = [name]
    for f in files:
        if f == name or f.startswith("._"):
            continue
        fs, fe = os.path.splitext(f)
        if fs == stem and (fe.lower() in RAW_EXTS or is_photo_name(f)):
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


# ---------- XMP sidecar ----------

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


# ---------- 共享复制引擎(collect 与画廊导出共用) ----------

def copy_to_dest(paths, dest, on_progress=None):
    """把一批源文件复制到 dest/<源父目录名>/,同名同大小跳过,源目录的 tag 并入目标。

    paths: 源文件绝对路径列表。返回 {total, done, copied, skipped, errors}。
    这是画廊「导出当前筛选」与 collect 成片收集共用的唯一复制实现。
    """
    total = len(paths)
    res = {"total": total, "done": 0, "copied": 0, "skipped": 0, "errors": []}
    tag_cache = {}
    dest_tags = {}
    for i, raw in enumerate(paths):
        try:
            src = os.path.realpath(raw)
            base = os.path.basename(src)
            if not is_media_name(base) or not os.path.isfile(src):
                raise FileNotFoundError(raw)
            day = os.path.basename(os.path.dirname(src))
            destdir = os.path.join(dest, day)
            os.makedirs(destdir, exist_ok=True)
            dst = os.path.join(destdir, base)
            if os.path.exists(dst) and os.path.getsize(dst) == os.path.getsize(src):
                res["skipped"] += 1
            else:
                shutil.copy2(src, dst)
                res["copied"] += 1
            srcdir = os.path.dirname(src)
            if srcdir not in tag_cache:
                tag_cache[srcdir] = load_tags(srcdir)
            t = tag_cache[srcdir].get(base)
            if t:
                dest_tags.setdefault(destdir, {})[base] = t
        except Exception as e:  # noqa: BLE001 —— 单张失败记录后继续
            res["errors"].append("%s: %s" % (raw, e))
        finally:
            res["done"] += 1
            if on_progress:
                on_progress(i + 1, total)
    for destdir, updates in dest_tags.items():
        try:
            photos = load_tags(destdir)
            photos.update(updates)
            save_tags(destdir, photos)
        except OSError as e:
            res["errors"].append("tags %s: %s" % (destdir, e))
    return res


# ---------- 操作一:collect(成片收集) ----------

def collect_plan(root, dest, cond):
    """cond: parse_where 的结果。返回清单 dict(只读)。"""
    days, total, total_bytes = [], 0, 0
    for d, files in day_dirs(root):
        tags = load_tags(d)
        names = sorted(media_names(files))
        hit = [n for n in names if match(tags.get(n), cond)]
        if not hit:
            continue
        b = sum(_safe_size(os.path.join(d, n)) for n in hit)
        days.append({"day": os.path.basename(d), "path": d, "count": len(hit),
                     "bytes": b, "names": hit})
        total += len(hit)
        total_bytes += b
    return {"tool": "collect", "root": root, "dest": dest, "where": where_str(cond),
            "days": days, "total_count": total, "total_bytes": total_bytes,
            "extra": {}, "warnings": []}


def collect_apply(root, dest, cond, on_progress=None):
    os.makedirs(dest, exist_ok=True)
    plan = collect_plan(root, dest, cond)
    paths = [os.path.join(day["path"], n) for day in plan["days"] for n in day["names"]]
    res = copy_to_dest(paths, dest, on_progress)
    res["tool"] = "collect"
    return res


# ---------- 操作二:xmp(XMP 导出) ----------

def xmp_plan(root, cond):
    plan, no_raw, existing = [], [], []
    bydays = {}
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
            xmp = os.path.join(d, stem_of(raw) + ".xmp")
            if os.path.exists(xmp):
                existing.append(os.path.relpath(xmp, root))
                continue
            plan.append((d, raw, xmp, t))
            bydays.setdefault(os.path.basename(d), []).append(raw)
    days = [{"day": day, "count": len(names), "names": names}
            for day, names in sorted(bydays.items())]
    warnings = []
    if existing:
        warnings.append("%d 个已有 .xmp,将跳过不覆盖" % len(existing))
    if no_raw:
        warnings.append("%d 条 tag 找不到同名 RAW" % len(no_raw))
    return {"tool": "xmp", "root": root, "where": where_str(cond), "days": days,
            "total_count": len(plan), "total_bytes": 0,
            "extra": {"skip_existing": len(existing), "no_raw": len(no_raw),
                      "existing_sample": existing[:8], "no_raw_sample": no_raw[:8]},
            "warnings": warnings, "_plan": plan}


def xmp_apply(root, cond, on_progress=None):
    plan = xmp_plan(root, cond)
    items = plan["_plan"]
    total = len(items)
    res = {"tool": "xmp", "total": total, "done": 0, "written": 0, "errors": []}
    for i, (d, raw, xmp, t) in enumerate(items):
        try:
            tmp = xmp + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(xmp_body(t))
            os.replace(tmp, xmp)
            res["written"] += 1
        except OSError as e:
            res["errors"].append("%s: %s" % (os.path.relpath(xmp, root), e))
        finally:
            res["done"] += 1
            if on_progress:
                on_progress(i + 1, total)
    return res


# ---------- 操作三:sync(Tag 同步,只补空白) ----------

def _source_stem_tags(source_root, day):
    """源库同名天里,主名(stem)-> 非空 tag 的映射(兼容 ARW→JPG)。"""
    sday = os.path.join(source_root, day)
    out = {}
    if not os.path.isdir(sday):
        return out
    for k, t in load_tags(sday).items():
        ct = clean_tags(t)
        if ct:
            out[stem_of(k)] = ct
    return out


def sync_plan(dest, source_root):
    """遍历成片库各天,只统计"dest 里无 tag、且源库同天同主名有 tag"的将补项。"""
    days, fill_total, skip_tagged, not_found = [], 0, 0, 0
    for d, files in day_dirs(dest):
        dnames = sorted(media_names(files))
        if not dnames:
            continue
        day = os.path.basename(d)
        dtags = load_tags(d)
        sstem = _source_stem_tags(source_root, day)
        fills = []
        for n in dnames:
            if dtags.get(n):          # 已有 tag —— 只跳过,绝不比对/改写
                skip_tagged += 1
                continue
            st = sstem.get(stem_of(n))
            if st:
                fills.append(n)
            else:
                not_found += 1
        if fills:
            days.append({"day": day, "path": d, "count": len(fills), "names": fills})
            fill_total += len(fills)
    warnings = []
    if skip_tagged:
        warnings.append("%d 张已有 tag,跳过(成片二遍改的永远权威)" % skip_tagged)
    if not_found:
        warnings.append("%d 张在源库找不到同主名 tag" % not_found)
    return {"tool": "sync", "root": source_root, "dest": dest, "where": "(补空白)",
            "days": days, "total_count": fill_total, "total_bytes": 0,
            "extra": {"skip_tagged": skip_tagged, "not_found": not_found},
            "warnings": warnings}


def sync_apply(dest, source_root, on_progress=None):
    plan = sync_plan(dest, source_root)
    total = plan["total_count"]
    res = {"tool": "sync", "total": total, "done": 0, "filled": 0, "errors": []}
    done = 0
    for day in plan["days"]:
        d = day["path"]
        sstem = _source_stem_tags(source_root, day["day"])
        try:
            dtags = load_tags(d)
            changed = False
            for n in day["names"]:
                if dtags.get(n):        # apply 时再确认一遍:仍是空白才补
                    continue
                st = sstem.get(stem_of(n))
                if st:
                    st = dict(st)
                    st["t"] = int(time.time())
                    dtags[n] = st
                    res["filled"] += 1
                    changed = True
                done += 1
                res["done"] = done
                if on_progress:
                    on_progress(done, total)
            if changed:
                save_tags(d, dtags)
        except OSError as e:
            res["errors"].append("%s: %s" % (day["day"], e))
    res["done"] = total
    if on_progress:
        on_progress(total, total)
    return res


# ---------- 操作四:sweep(废片清扫,只移不删) ----------

def sweep_plan(root, bin_dir=None):
    bin_dir = bin_dir or os.path.join(root, "_trash_bin")
    days, total_photos, total_files, total_bytes = [], 0, 0, 0
    for d, files in day_dirs(root):
        tags = load_tags(d)
        names = media_names(files)
        trash = sorted(n for n, t in tags.items()
                       if isinstance(t, dict) and t.get("status") == "trash" and n in names)
        if not trash:
            continue
        items = []
        for jpg in trash:
            fs = companions(jpg, files)
            items.append({"name": jpg, "files": fs})
            total_photos += 1
            for f in fs:
                total_files += 1
                total_bytes += _safe_size(os.path.join(d, f))
        days.append({"day": os.path.basename(d), "path": d, "count": len(items),
                     "items": items, "names": trash})
    return {"tool": "sweep", "root": root, "dest": bin_dir, "where": "status∈{trash}",
            "days": days, "total_count": total_photos, "total_bytes": total_bytes,
            "extra": {"files": total_files}, "warnings": ["移动源库文件(可从 _trash_bin 捞回),绝不删除"]}


def sweep_apply(root, bin_dir=None, on_progress=None):
    bin_dir = bin_dir or os.path.join(root, "_trash_bin")
    plan = sweep_plan(root, bin_dir)
    total = plan["extra"]["files"]
    res = {"tool": "sweep", "total": total, "done": 0, "moved": 0, "errors": []}
    for day in plan["days"]:
        d = day["path"]
        destdir = os.path.join(bin_dir, day["day"])
        os.makedirs(destdir, exist_ok=True)
        src_tags = load_tags(d)
        bin_tags = load_tags(destdir)
        changed = False
        for it in day["items"]:
            jpg, fs = it["name"], it["files"]
            ok = True
            for f in fs:
                try:
                    shutil.move(os.path.join(d, f), uniq_dest(destdir, f))
                    res["moved"] += 1
                except OSError as e:
                    ok = False
                    res["errors"].append("%s/%s: %s" % (day["day"], f, e))
                finally:
                    res["done"] += 1
                    if on_progress:
                        on_progress(res["done"], total)
            if ok and jpg in src_tags:
                bin_tags[jpg] = src_tags.pop(jpg)
                changed = True
        if changed:
            save_tags(d, src_tags)
            save_tags(destdir, bin_tags)
    return res


# ---------- 统一调度(server 与 CLI 都走这里) ----------

PLANNERS = {"collect": collect_plan, "xmp": xmp_plan, "sync": sync_plan, "sweep": sweep_plan}
APPLIERS = {"collect": collect_apply, "xmp": xmp_apply, "sync": sync_apply, "sweep": sweep_apply}
