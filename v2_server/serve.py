#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""phototag v2 本地服务器(仅 Python 标准库,macOS 缩略图走 sips)。

    python3 serve.py --root "/Volumes/ZTSSD/Sony A7V" [--port 8787] [--open]

只绑定 127.0.0.1;所有文件访问限制在 /Volumes 与 $HOME 之下。
tag 存储:每个天文件夹一个 phototags.json(原子写 + .bak 备份 + 损坏自愈),
另有全局流水日志 ~/Library/Application Support/phototag/journal.jsonl。
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.parse
import webbrowser
from hashlib import sha1
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from datetime import datetime

APP_DIR = os.path.dirname(os.path.abspath(__file__))
WEB_DIR = os.path.join(APP_DIR, "web")
LIGHT_HTML = os.path.normpath(os.path.join(APP_DIR, "..", "light", "tagger.html"))
HOME = os.path.realpath(os.path.expanduser("~"))
CACHE_DIR = os.path.join(HOME, "Library", "Caches", "phototag", "thumbs")
PREV_DIR = os.path.join(HOME, "Library", "Caches", "phototag", "previews")
EXIFTOOL = shutil.which("exiftool")
SUPPORT_DIR = os.path.join(HOME, "Library", "Application Support", "phototag")
JOURNAL_PATH = os.path.join(SUPPORT_DIR, "journal.jsonl")

sys.path.insert(0, os.path.dirname(APP_DIR))   # 仓库根,便于 import 共享核心
import phototag_core as core  # noqa: E402

ALLOWED_PREFIXES = ["/Volumes", HOME]
THUMB_WIDTHS = (240, 480, 960, 1600, 2560, 3840)   # 大档给全屏幻灯片用(按屏幕长边取,避免解 24MP 原图)
DATE_RE = re.compile(r"(20\d{2})[.\-_ ]?(\d{2})[.\-_ ]?(\d{2})")
# tag 词表 / 文件名 / 跳过目录统一取自 phototag_core(单一来源,避免漂移)
TAGFILE = core.TAGFILE
SKIP_DIR_NAMES = core.SKIP_DIR_NAMES
VALID_TAGS = core.VALID_TAGS

DEFAULT_ROOT = ""
VERBOSE = False

_locks_guard = threading.Lock()
_dir_locks = {}
_thumb_sem = threading.BoundedSemaphore(6)
_full_sem = threading.BoundedSemaphore(2)   # RAW 全解很重,限并发
_pw_guard = threading.Lock()
_prewarms = {}  # (dir, w) -> {done, total, active}
_jobs_guard = threading.Lock()
_jobs = {}  # id -> export job dict
_job_seq = [0]


# ---------- 基础 ----------

def now_iso():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def safe_path(p):
    """expanduser + realpath,并强制落在允许的前缀内,否则 PermissionError。"""
    if not p or not isinstance(p, str):
        raise PermissionError("empty path")
    p = os.path.realpath(os.path.expanduser(p))
    for pref in ALLOWED_PREFIXES:
        if p == pref or p.startswith(pref.rstrip(os.sep) + os.sep):
            return p
    raise PermissionError(p)


# 名称助手统一到 phototag_core(server / CLI / 工具共用一份实现)
is_photo_name = core.is_photo_name
is_raw_name = core.is_raw_name
is_media_name = core.is_media_name
media_names = core.media_names


def list_photos(d):
    entries = {}
    with os.scandir(d) as it:
        for e in it:
            try:
                if e.is_file() and is_media_name(e.name):
                    st = e.stat()
                    entries[e.name] = {"name": e.name, "size": st.st_size, "mtime": int(st.st_mtime)}
            except OSError:
                continue
    out = [entries[n] for n in media_names(entries.keys())]
    out.sort(key=lambda x: x["name"])
    return out


def guess_date(name):
    m = DATE_RE.search(name)
    return "%s-%s-%s" % m.groups() if m else ""


def dir_lock(d):
    with _locks_guard:
        return _dir_locks.setdefault(d, threading.Lock())


# ---------- tag 存储 ----------

# tag 原子读写(损坏自愈 + .bak)统一到 phototag_core
load_tags = core.load_tags
save_tags = core.save_tags


clean_tags = core.clean_tags   # 合法值过滤统一到 phototag_core


def journal(entry):
    try:
        os.makedirs(SUPPORT_DIR, exist_ok=True)
        with open(JOURNAL_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass


def set_tags_many(d, updates):
    """updates: {文件名: tags dict(空=清除)}。单锁批量原子写。"""
    d = safe_path(d)
    applied = {}
    with dir_lock(d):
        photos = load_tags(d)
        for name, tags in updates.items():
            if not isinstance(name, str) or not is_media_name(name):
                continue
            ct = clean_tags(tags)
            if ct:
                ct["t"] = int(time.time())
                photos[name] = ct
            else:
                photos.pop(name, None)
            applied[name] = ct
        save_tags(d, photos)
    for name, ct in applied.items():
        journal({"ts": now_iso(), "dir": d, "file": name, "tags": ct})
    return applied


# ---------- 扫描 ----------

def scan_root(root):
    """找出 root 下所有直接含 JPG 的文件夹(=天文件夹)+ 每天 tag 统计。"""
    root = safe_path(root)
    days = []
    visited = 0
    truncated = False
    for cur, dirs, files in os.walk(root):
        visited += 1
        if visited > 20000 or len(days) >= 800:
            truncated = True
            break
        dirs[:] = sorted(x for x in dirs if not x.startswith(".") and x not in SKIP_DIR_NAMES)
        names = media_names(files)
        if not names:
            continue
        tags = load_tags(cur)
        counts = {k: {} for k in VALID_TAGS}
        tagged = 0
        for n, t in tags.items():
            if n not in names or not isinstance(t, dict):
                continue
            if any(t.get(k) for k in VALID_TAGS):
                tagged += 1
            for k in VALID_TAGS:
                v = t.get(k)
                if v:
                    counts[k][v] = counts[k].get(v, 0) + 1
        base = os.path.basename(cur)
        days.append({
            "path": cur,
            "name": os.path.relpath(cur, root) if cur != root else base,
            "date": guess_date(base) or guess_date(cur),
            "count": len(names),
            "tagged": tagged,
            "counts": counts,
        })
    days.sort(key=lambda x: (x["date"] or "0000-00-00", x["name"]))
    return {"root": root, "days": days, "truncated": truncated}


def shallow_jpg_count(d, cap=3000):
    n = 0
    try:
        with os.scandir(d) as it:
            for i, e in enumerate(it):
                if i >= cap:
                    return n
                try:
                    if e.is_file() and is_photo_name(e.name):
                        n += 1
                except OSError:
                    continue
    except OSError:
        pass
    return n


def browse_dir(path):
    p = safe_path(path)
    if not os.path.isdir(p):
        raise FileNotFoundError(p)
    subs = []
    with os.scandir(p) as it:
        for e in it:
            try:
                if e.is_dir(follow_symlinks=False) and not e.name.startswith(".") and e.name not in SKIP_DIR_NAMES:
                    subs.append(e.name)
            except OSError:
                continue
    subs.sort(key=str.lower)
    dirs = [{"name": n, "path": os.path.join(p, n), "jpg": shallow_jpg_count(os.path.join(p, n))} for n in subs]
    parent = os.path.dirname(p)
    try:
        parent = safe_path(parent)
    except PermissionError:
        parent = None
    return {"path": p, "parent": parent, "jpg": shallow_jpg_count(p), "dirs": dirs}


def list_roots():
    roots = []
    seen = set()

    def add(path, name=None):
        try:
            rp = safe_path(path)
        except (PermissionError, OSError):
            return
        if rp in seen or not os.path.isdir(rp):
            return
        seen.add(rp)
        roots.append({"name": name or os.path.basename(rp) or rp, "path": rp})

    if DEFAULT_ROOT:
        add(DEFAULT_ROOT)
    try:
        for e in sorted(os.listdir("/Volumes")):
            if e.startswith(".") or e == "Macintosh HD":
                continue
            for sub in ("Sony A7V", ""):
                cand = os.path.join("/Volumes", e, sub) if sub else os.path.join("/Volumes", e)
                if os.path.isdir(cand):
                    add(cand, (e + "/" + sub).rstrip("/"))
    except OSError:
        pass
    for d in ("~/Pictures/成片", "~/Pictures", "~/Desktop", "~/Downloads"):
        add(d, d.replace("~/", ""))
    return roots


# ---------- 缩略图 / ARW 预览 ----------

def _cache_slot(base_dir, src, st, tag):
    key = sha1(("%s|%d|%d|%s" % (src, st.st_mtime_ns, st.st_size, tag)).encode()).hexdigest()
    out = os.path.join(base_dir, key[:2], key + ".jpg")
    return out, key


def _finish_tmp(tmp, out):
    if not os.path.exists(tmp) or os.path.getsize(tmp) == 0:
        try:
            os.remove(tmp)
        except OSError:
            pass
        return False
    os.replace(tmp, out)
    return True


def ensure_arw_preview(src):
    """抽取 ARW 内嵌 JPEG 预览(A6300 为 1920x1080),并把方向标记带过去;缓存。"""
    src = safe_path(src)
    st = os.stat(src)
    out, key = _cache_slot(PREV_DIR, src, st, "prev")
    if os.path.exists(out):
        return out, key
    os.makedirs(os.path.dirname(out), exist_ok=True)
    tmp = "%s.tmp.%d.%d" % (out, os.getpid(), threading.get_ident())
    with _thumb_sem:
        if os.path.exists(out):
            return out, key
        done = False
        if EXIFTOOL:
            for tag in ("-PreviewImage", "-JpgFromRaw"):
                r = subprocess.run([EXIFTOOL, "-b", tag, src], capture_output=True, timeout=60)
                if r.returncode == 0 and len(r.stdout) > 10000:
                    with open(tmp, "wb") as f:
                        f.write(r.stdout)
                    subprocess.run(  # 预览块不带方向,把原片的 Orientation 拷过去让浏览器转正
                        [EXIFTOOL, "-q", "-overwrite_original", "-TagsFromFile", src,
                         "-Orientation", tmp], capture_output=True, timeout=60)
                    done = True
                    break
        if not done:  # 没有 exiftool 或无预览块:sips 全解(慢但兜底)
            subprocess.run(["sips", "-s", "format", "jpeg", "-s", "formatOptions", "85",
                            src, "--out", tmp], capture_output=True, timeout=180)
    if not _finish_tmp(tmp, out):
        raise RuntimeError("ARW preview failed: " + os.path.basename(src))
    return out, key


def ensure_arw_full(src):
    """sips 全尺寸解 RAW(首次约数秒,之后走缓存);失败回退内嵌预览。"""
    src = safe_path(src)
    st = os.stat(src)
    out, key = _cache_slot(PREV_DIR, src, st, "full")
    if os.path.exists(out):
        return out, key
    os.makedirs(os.path.dirname(out), exist_ok=True)
    tmp = "%s.tmp.%d.%d" % (out, os.getpid(), threading.get_ident())
    with _full_sem:
        if os.path.exists(out):
            return out, key
        subprocess.run(["sips", "-s", "format", "jpeg", "-s", "formatOptions", "92",
                        src, "--out", tmp], capture_output=True, timeout=300)
    if not _finish_tmp(tmp, out):
        return ensure_arw_preview(src)
    return out, key


def ensure_thumb(src, w):
    src = safe_path(src)
    base = os.path.basename(src)
    if not is_media_name(base):
        raise PermissionError(src)
    if w not in THUMB_WIDTHS:
        w = 480
    st = os.stat(src)
    key = sha1(("%s|%d|%d|%d" % (src, st.st_mtime_ns, st.st_size, w)).encode()).hexdigest()
    out = os.path.join(CACHE_DIR, key[:2], key + ".jpg")
    if os.path.exists(out):
        return out, key
    pixels_src = ensure_arw_preview(src)[0] if is_raw_name(base) else src
    os.makedirs(os.path.dirname(out), exist_ok=True)
    tmp = "%s.tmp.%d.%d" % (out, os.getpid(), threading.get_ident())
    with _thumb_sem:
        if os.path.exists(out):
            return out, key
        r = subprocess.run(
            ["sips", "-s", "format", "jpeg", "-s", "formatOptions", "80",
             "--resampleHeightWidthMax", str(w), pixels_src, "--out", tmp],
            capture_output=True, timeout=60)
    if r.returncode != 0 or not _finish_tmp(tmp, out):
        raise RuntimeError("sips failed: " + r.stderr.decode(errors="replace")[:200])
    return out, key


def start_prewarm(d, w):
    d = safe_path(d)
    if w not in THUMB_WIDTHS:
        w = 480
    key = (d, w)
    with _pw_guard:
        st = _prewarms.get(key)
        if st and st["active"]:
            return
        _prewarms[key] = {"done": 0, "total": 0, "active": True}

    def run():
        try:
            photos = list_photos(d)
            with _pw_guard:
                _prewarms[key]["total"] = len(photos)
            for p in photos:
                try:
                    ensure_thumb(os.path.join(d, p["name"]), w)
                except Exception:
                    pass
                with _pw_guard:
                    _prewarms[key]["done"] += 1
        finally:
            with _pw_guard:
                _prewarms[key]["active"] = False

    threading.Thread(target=run, daemon=True).start()


# ---------- 导出 ----------

def run_export(job, paths, dest):
    """画廊「导出当前筛选」:复用 core 的复制引擎,结果回填到 job(与 collect 同一实现)。"""
    def prog(done, total):
        job["done"] = done
    res = core.copy_to_dest(paths, dest, prog)
    job["copied"] = res["copied"]
    job["skipped"] = res["skipped"]
    job["done"] = res["done"]
    job["errors"] = res["errors"]
    job["finished"] = True


def start_export(paths, dest):
    dest = safe_path(dest)
    os.makedirs(dest, exist_ok=True)
    if not isinstance(paths, list) or not paths:
        raise ValueError("paths empty")
    paths = [p for p in paths if isinstance(p, str)][:20000]
    with _jobs_guard:
        _job_seq[0] += 1
        job_id = "job%d" % _job_seq[0]
        job = {"id": job_id, "total": len(paths), "done": 0, "copied": 0,
               "skipped": 0, "errors": [], "finished": False, "dest": dest}
        _jobs[job_id] = job
    threading.Thread(target=run_export, args=(job, paths, dest), daemon=True).start()
    return job_id


# ---------- 工具(collect / xmp / sync / sweep) ----------

def _tool_cond(params, default):
    w = params.get("where")
    if isinstance(w, list) and w:
        return core.parse_where(w)
    if isinstance(w, str) and w.strip():
        return core.parse_where(w.split())
    return core.parse_where(default)


def _tool_posargs(tool, params):
    """校验 + safe_path 后,返回该工具 plan/apply 的位置参数(不含 on_progress)。"""
    def sp(key):
        v = params.get(key)
        if not v or not isinstance(v, str):
            raise ValueError("缺少参数 %s" % key)
        return safe_path(v)
    if tool == "collect":
        return (sp("root"), sp("dest"), _tool_cond(params, ["status=sooc"]))
    if tool == "xmp":
        return (sp("root"), _tool_cond(params, ["status=edit"]))
    if tool == "sync":
        src = params.get("source_root") or params.get("root")
        if not isinstance(src, str) or not src:
            raise ValueError("缺少参数 source_root")
        return (sp("dest"), safe_path(src))
    if tool == "sweep":
        root = sp("root")
        b = params.get("bin")
        return (root, safe_path(b) if b else os.path.join(root, "_trash_bin"))
    raise ValueError("unknown tool: %s" % tool)


def tool_plan(body):
    tool = body.get("tool")
    if tool not in core.PLANNERS:
        raise ValueError("unknown tool: %s" % tool)
    pos = _tool_posargs(tool, body.get("params") or {})
    plan = core.PLANNERS[tool](*pos)
    return {k: v for k, v in plan.items() if not k.startswith("_")}   # 去掉内部字段


def start_tool(body):
    tool = body.get("tool")
    if tool not in core.APPLIERS:
        raise ValueError("unknown tool: %s" % tool)
    pos = _tool_posargs(tool, body.get("params") or {})
    with _jobs_guard:
        _job_seq[0] += 1
        job_id = "job%d" % _job_seq[0]
        job = {"id": job_id, "tool": tool, "total": 0, "done": 0,
               "finished": False, "result": None, "errors": []}
        _jobs[job_id] = job

    def run():
        def prog(done, total):
            with _jobs_guard:
                job["done"], job["total"] = done, total
        try:
            res = core.APPLIERS[tool](*pos, on_progress=prog)
            with _jobs_guard:
                job["result"] = res
                job["errors"] = res.get("errors", [])
                job["total"] = res.get("total", job["total"])
                job["done"] = res.get("done", job["total"])
        except Exception as e:  # noqa: BLE001
            with _jobs_guard:
                job["errors"] = ["%s: %s" % (type(e).__name__, e)]
        finally:
            with _jobs_guard:
                job["finished"] = True

    threading.Thread(target=run, daemon=True).start()
    return job_id


# ---------- HTTP ----------

class Handler(BaseHTTPRequestHandler):
    server_version = "phototag/1.0"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        if VERBOSE:
            sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    # -- 响应助手 --
    def _send(self, code, body, ctype, extra=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj, ensure_ascii=False).encode(),
                   "application/json; charset=utf-8", {"Cache-Control": "no-store"})

    def _err(self, code, msg):
        self._json({"error": msg}, code)

    def _not_modified(self):
        self.send_response(304)
        self.end_headers()

    def _file(self, path, ctype, etag=None, cache="no-store"):
        if etag and self.headers.get("If-None-Match") == etag:
            self._not_modified()
            return
        with open(path, "rb") as f:
            body = f.read()
        extra = {"Cache-Control": cache}
        if etag:
            extra["ETag"] = etag
        self._send(200, body, ctype, extra)

    def _body_json(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0 or n > 50 * 1024 * 1024:
            return {}
        try:
            return json.loads(self.rfile.read(n).decode("utf-8") or "{}")
        except (json.JSONDecodeError, UnicodeDecodeError):
            return {}

    # -- 路由 --
    def do_GET(self):
        try:
            u = urllib.parse.urlsplit(self.path)
            q = {k: v[0] for k, v in urllib.parse.parse_qs(u.query).items()}
            route = u.path
            if route in ("/", "/index.html"):
                body = b'<meta http-equiv="refresh" content="0;url=/tagger">'
                self._send(200, body, "text/html; charset=utf-8")
            elif route == "/tagger":
                self._file(os.path.join(WEB_DIR, "tagger.html"), "text/html; charset=utf-8")
            elif route == "/gallery":
                self._file(os.path.join(WEB_DIR, "gallery.html"), "text/html; charset=utf-8")
            elif route == "/tools":
                self._file(os.path.join(WEB_DIR, "tools.html"), "text/html; charset=utf-8")
            elif route in ("/light", "/v1"):     # /v1 为旧名兼容
                self._file(LIGHT_HTML, "text/html; charset=utf-8")
            elif route == "/api/config":
                self._json({"root": DEFAULT_ROOT, "home": HOME, "version": 1})
            elif route == "/api/roots":
                self._json({"roots": list_roots()})
            elif route == "/api/browse":
                self._json(browse_dir(q.get("path") or HOME))
            elif route == "/api/scan":
                self._json(scan_root(q.get("root") or DEFAULT_ROOT))
            elif route == "/api/photos":
                d = safe_path(q.get("dir"))
                self._json({"dir": d, "photos": list_photos(d), "tags": load_tags(d)})
            elif route == "/api/prewarm_status":
                d = safe_path(q.get("dir"))
                w = int(q.get("w") or 480)
                with _pw_guard:
                    st = dict(_prewarms.get((d, w)) or {})
                self._json(st)
            elif route in ("/api/export_status", "/api/job_status"):
                with _jobs_guard:
                    job = _jobs.get(q.get("job") or "")
                    job = dict(job) if job else None
                if job is None:
                    self._err(404, "no such job")
                else:
                    self._json(job)
            elif route == "/img":
                p = safe_path(q.get("path"))
                base = os.path.basename(p)
                if not is_media_name(base):
                    raise PermissionError(p)
                if is_raw_name(base):   # ARW:默认回内嵌预览,&full=1 时全尺寸解 RAW
                    fp, key = ensure_arw_full(p) if q.get("full") else ensure_arw_preview(p)
                    self._file(fp, "image/jpeg", '"%s"' % key, "private, max-age=604800")
                else:
                    st = os.stat(p)
                    etag = '"%d-%d"' % (st.st_mtime_ns, st.st_size)
                    self._file(p, "image/jpeg", etag, "private, max-age=86400")
            elif route == "/thumb":
                p, key = ensure_thumb(q.get("path"), int(q.get("w") or 480))
                self._file(p, "image/jpeg", '"%s"' % key, "private, max-age=604800")
            else:
                self._err(404, "not found")
        except PermissionError as e:
            self._err(403, "forbidden: %s" % e)
        except (FileNotFoundError, NotADirectoryError) as e:
            self._err(404, "not found: %s" % e)
        except Exception as e:  # noqa: BLE001
            self._err(500, "%s: %s" % (type(e).__name__, e))

    def do_POST(self):
        try:
            u = urllib.parse.urlsplit(self.path)
            body = self._body_json()
            route = u.path
            if route == "/api/tag":
                d, name = body.get("dir"), body.get("file")
                if not d or not isinstance(name, str) or not is_media_name(name):
                    raise ValueError("bad dir/file")
                d = safe_path(d)
                if not os.path.isfile(os.path.join(d, name)):
                    raise FileNotFoundError(name)
                applied = set_tags_many(d, {name: body.get("tags") or {}})
                self._json({"ok": True, "tags": applied.get(name, {})})
            elif route == "/api/tags":
                d = body.get("dir")
                updates = body.get("updates")
                if not d or not isinstance(updates, dict):
                    raise ValueError("bad dir/updates")
                applied = set_tags_many(safe_path(d), updates)
                self._json({"ok": True, "n": len(applied)})
            elif route == "/api/prewarm":
                start_prewarm(body.get("dir"), int(body.get("w") or 480))
                self._json({"ok": True})
            elif route == "/api/export":
                job_id = start_export(body.get("paths"), body.get("dest"))
                self._json({"ok": True, "job": job_id})
            elif route == "/api/tool/plan":
                self._json(tool_plan(body))
            elif route == "/api/tool/apply":
                self._json({"ok": True, "job": start_tool(body)})
            else:
                self._err(404, "not found")
        except PermissionError as e:
            self._err(403, "forbidden: %s" % e)
        except FileNotFoundError as e:
            self._err(404, "not found: %s" % e)
        except ValueError as e:
            self._err(400, str(e))
        except Exception as e:  # noqa: BLE001
            self._err(500, "%s: %s" % (type(e).__name__, e))


def main(argv=None):
    global DEFAULT_ROOT, VERBOSE
    ap = argparse.ArgumentParser(description="phototag v2 server")
    default_root = os.path.join(HOME, "Pictures")
    for cand in ("/Volumes/My Book/Sony A7V", "/Volumes/ZTSSD/Sony A7V",
                 "/Volumes/ZTSSD/Sony 6300", "/Volumes/My Book/Sony 6300"):
        if os.path.isdir(cand):
            default_root = cand
            break
    ap.add_argument("--root", default=default_root, help="默认照片根目录")
    ap.add_argument("--port", type=int, default=8787)
    ap.add_argument("--open", action="store_true", help="启动后自动打开浏览器")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args(argv)
    try:
        DEFAULT_ROOT = safe_path(args.root)
    except PermissionError:
        print("!! --root 必须在 /Volumes 或家目录之下", file=sys.stderr)
        return 2
    VERBOSE = args.verbose
    os.makedirs(CACHE_DIR, exist_ok=True)
    srv = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    srv.daemon_threads = True
    url = "http://127.0.0.1:%d" % args.port
    print("phototag v2  根目录: %s" % DEFAULT_ROOT)
    print("  打标器  %s/tagger" % url)
    print("  画廊    %s/gallery" % url)
    print("  light 版 %s/light   (Ctrl+C 退出)" % url)
    if args.open:
        webbrowser.open(url + "/tagger")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nbye")
    return 0


if __name__ == "__main__":
    sys.exit(main())
