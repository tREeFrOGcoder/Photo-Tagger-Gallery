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
SUPPORT_DIR = os.path.join(HOME, "Library", "Application Support", "phototag")
JOURNAL_PATH = os.path.join(SUPPORT_DIR, "journal.jsonl")

ALLOWED_PREFIXES = ["/Volumes", HOME]
TAGFILE = "phototags.json"
THUMB_WIDTHS = (240, 480, 960)
SKIP_DIR_NAMES = {"_trash_bin", "node_modules", "Library"}
DATE_RE = re.compile(r"(20\d{2})[.\-_ ]?(\d{2})[.\-_ ]?(\d{2})")
VALID_TAGS = {
    "status": {"sooc", "edit", "trash"},
    "type": {"scenery", "animal", "portrait", "insect"},
    "quality": {"best", "normal"},
}

DEFAULT_ROOT = ""
VERBOSE = False

_locks_guard = threading.Lock()
_dir_locks = {}
_thumb_sem = threading.BoundedSemaphore(6)
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


def is_photo_name(n):
    nl = n.lower()
    return (not n.startswith(".")) and (nl.endswith(".jpg") or nl.endswith(".jpeg"))


def list_photos(d):
    out = []
    with os.scandir(d) as it:
        for e in it:
            try:
                if e.is_file() and is_photo_name(e.name):
                    st = e.stat()
                    out.append({"name": e.name, "size": st.st_size, "mtime": int(st.st_mtime)})
            except OSError:
                continue
    out.sort(key=lambda x: x["name"])
    return out


def guess_date(name):
    m = DATE_RE.search(name)
    return "%s-%s-%s" % m.groups() if m else ""


def dir_lock(d):
    with _locks_guard:
        return _dir_locks.setdefault(d, threading.Lock())


# ---------- tag 存储 ----------

def _tagfile(d):
    return os.path.join(d, TAGFILE)


def load_tags(d):
    """读取某文件夹的 tag 映射;主文件坏了自动留证并回退 .bak,再不行回空。"""
    path = _tagfile(d)
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
    path = _tagfile(d)
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
    """只保留合法维度取值;未来的自由标签 x 数组透传。"""
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
            if not isinstance(name, str) or not is_photo_name(name):
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
        names = {f for f in files if is_photo_name(f)}
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


# ---------- 缩略图 ----------

def ensure_thumb(src, w):
    src = safe_path(src)
    if not is_photo_name(os.path.basename(src)):
        raise PermissionError(src)
    if w not in THUMB_WIDTHS:
        w = 480
    st = os.stat(src)
    key = sha1(("%s|%d|%d|%d" % (src, st.st_mtime_ns, st.st_size, w)).encode()).hexdigest()
    out = os.path.join(CACHE_DIR, key[:2], key + ".jpg")
    if os.path.exists(out):
        return out, key
    os.makedirs(os.path.dirname(out), exist_ok=True)
    tmp = "%s.tmp.%d.%d" % (out, os.getpid(), threading.get_ident())
    with _thumb_sem:
        if os.path.exists(out):
            return out, key
        r = subprocess.run(
            ["sips", "-s", "format", "jpeg", "-s", "formatOptions", "80",
             "--resampleHeightWidthMax", str(w), src, "--out", tmp],
            capture_output=True, timeout=60)
    if r.returncode != 0 or not os.path.exists(tmp) or os.path.getsize(tmp) == 0:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise RuntimeError("sips failed: " + r.stderr.decode(errors="replace")[:200])
    os.replace(tmp, out)
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
    tag_cache = {}
    dest_tags = {}
    for raw in paths:
        try:
            src = safe_path(raw)
            base = os.path.basename(src)
            if not is_photo_name(base) or not os.path.isfile(src):
                raise FileNotFoundError(raw)
            day = os.path.basename(os.path.dirname(src))
            destdir = os.path.join(dest, day)
            os.makedirs(destdir, exist_ok=True)
            dst = os.path.join(destdir, base)
            if os.path.exists(dst) and os.path.getsize(dst) == os.path.getsize(src):
                job["skipped"] += 1
            else:
                shutil.copy2(src, dst)
                job["copied"] += 1
            srcdir = os.path.dirname(src)
            if srcdir not in tag_cache:
                tag_cache[srcdir] = load_tags(srcdir)
            t = tag_cache[srcdir].get(base)
            if t:
                dest_tags.setdefault(destdir, {})[base] = t
        except Exception as e:  # noqa: BLE001 —— 单张失败记录后继续
            job["errors"].append("%s: %s" % (raw, e))
        finally:
            job["done"] += 1
    for destdir, updates in dest_tags.items():
        try:
            with dir_lock(destdir):
                photos = load_tags(destdir)
                photos.update(updates)
                save_tags(destdir, photos)
        except OSError as e:
            job["errors"].append("tags %s: %s" % (destdir, e))
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
            elif route == "/api/export_status":
                with _jobs_guard:
                    job = _jobs.get(q.get("job") or "")
                    job = dict(job) if job else None
                if job is None:
                    self._err(404, "no such job")
                else:
                    self._json(job)
            elif route == "/img":
                p = safe_path(q.get("path"))
                if not is_photo_name(os.path.basename(p)):
                    raise PermissionError(p)
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
                if not d or not isinstance(name, str) or not is_photo_name(name):
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
    default_root = "/Volumes/ZTSSD/Sony A7V"
    if not os.path.isdir(default_root):
        default_root = os.path.join(HOME, "Pictures")
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
