#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""serve.py 与 tools 的自测(不依赖 pytest,直接 python3 test_serve.py)。

在临时 fixture 上验证:tag 原子读写/损坏自愈、扫描规则(过滤 ._ 与无 JPG 目录)、
安全路径、缩略图生成(sips)、导出复制、HTTP 冒烟、sweep/collect 工具 dry-run 与 apply。
"""

import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
import urllib.error
import urllib.parse
import urllib.request
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.normpath(os.path.join(HERE, "..", "tools"))
sys.path.insert(0, HERE)
import serve  # noqa: E402


def make_test_jpg(tmp):
    """纯 Python 造 16x16 PNG,再用 sips 转成货真价实的 JPG 字节。"""
    def chunk(t, d):
        c = t + d
        return struct.pack(">I", len(d)) + c + struct.pack(">I", zlib.crc32(c))
    w = h = 16
    raw = b"".join(b"\x00" + bytes([120, 160, 200] * w) for _ in range(h))
    png = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))
    p, j = os.path.join(tmp, "_t.png"), os.path.join(tmp, "_t.jpg")
    with open(p, "wb") as f:
        f.write(png)
    subprocess.run(["sips", "-s", "format", "jpeg", p, "--out", j], capture_output=True, check=True)
    with open(j, "rb") as f:
        return f.read()


TINY_JPG = b""  # main() 里生成

PASS = []


def check(name, cond, extra=""):
    if cond:
        PASS.append(name)
        print("  ok  %s" % name)
    else:
        print("FAIL  %s  %s" % (name, extra))
        sys.exit(1)


def write(path, data=None):
    if data is None:
        data = TINY_JPG
    os.makedirs(os.path.dirname(path), exist_ok=True)
    mode = "wb" if isinstance(data, bytes) else "w"
    with open(path, mode) as f:
        f.write(data)


def build_fixture(base):
    root = os.path.join(base, "lib")
    d1 = os.path.join(root, "2026.01.01")
    d2 = os.path.join(root, "2026.01.02")
    for n in ("DSC00001.JPG", "DSC00002.JPG", "DSC00003.JPG"):
        write(os.path.join(d1, n))
        write(os.path.join(d1, n.replace(".JPG", ".ARW")), b"raw")
    write(os.path.join(d1, "._DSC00001.JPG"), b"junk")       # AppleDouble 垃圾
    write(os.path.join(d1, "._DSC00001.ARW"), b"junk")
    write(os.path.join(d2, "DSC00010.JPG"))
    write(os.path.join(root, "rawonly", "DSC00099.ARW"), b"raw")   # 无 JPG,应跳过
    write(os.path.join(root, "_trash_bin", "x", "DSC0.JPG"))       # 回收目录,应跳过
    return root, d1, d2


def http(port, path, body=None):
    url = "http://127.0.0.1:%d%s" % (port, path)
    if body is None:
        req = urllib.request.Request(url)
    else:
        req = urllib.request.Request(url, json.dumps(body).encode(),
                                     {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        data = r.read()
        ct = r.headers.get("Content-Type", "")
        return r.status, ct, data


def main():
    global TINY_JPG
    tmp = tempfile.mkdtemp(prefix="phototag_test_")
    try:
        TINY_JPG = make_test_jpg(tmp)
        serve.ALLOWED_PREFIXES.append(os.path.realpath(tmp))
        serve.CACHE_DIR = os.path.join(tmp, "cache")
        serve.JOURNAL_PATH = os.path.join(tmp, "journal.jsonl")
        root, d1, d2 = build_fixture(tmp)

        print("== 基础 ==")
        ps = serve.list_photos(d1)
        check("list_photos 过滤 ._ 与 RAW", [p["name"] for p in ps] ==
              ["DSC00001.JPG", "DSC00002.JPG", "DSC00003.JPG"], str(ps))
        try:
            serve.safe_path("/etc/passwd")
            check("safe_path 拒绝越界", False)
        except PermissionError:
            check("safe_path 拒绝越界", True)

        print("== tag 读写 ==")
        serve.set_tags_many(d1, {"DSC00001.JPG": {"status": "sooc", "quality": "best", "bogus": "x", "type": "nope"}})
        t = serve.load_tags(d1)
        check("写入+非法值过滤", t["DSC00001.JPG"]["status"] == "sooc" and
              t["DSC00001.JPG"]["quality"] == "best" and
              "bogus" not in t["DSC00001.JPG"] and "type" not in t["DSC00001.JPG"], str(t))
        serve.set_tags_many(d1, {"DSC00002.JPG": {"status": "trash"}, "DSC00003.JPG": {"type": "animal"}})
        serve.set_tags_many(d1, {"DSC00001.JPG": {}})
        t = serve.load_tags(d1)
        check("清空即删除条目", "DSC00001.JPG" not in t and len(t) == 2, str(t))
        check("journal 有流水", os.path.exists(serve.JOURNAL_PATH) and
              len(open(serve.JOURNAL_PATH).read().strip().splitlines()) == 4)
        # 损坏自愈:主文件写坏,应回退 .bak(上一次成功写入前的状态)
        tagfile = os.path.join(d1, "phototags.json")
        with open(tagfile, "w") as f:
            f.write("{broken json!!")
        t2 = serve.load_tags(d1)
        check("损坏回退 .bak", isinstance(t2, dict) and "DSC00002.JPG" in t2, str(t2))
        check("坏文件留证 .corrupt", any(".corrupt." in f for f in os.listdir(d1)))
        serve.set_tags_many(d1, {"DSC00002.JPG": {"status": "trash"}})   # 重写主文件恢复正常

        print("== 扫描 ==")
        sc = serve.scan_root(root)
        names = sorted(x["name"] for x in sc["days"])
        check("识别天文件夹/跳过无JPG与回收站", names == ["2026.01.01", "2026.01.02"], str(names))
        day1 = next(x for x in sc["days"] if x["name"] == "2026.01.01")
        check("日期解析 2026.01.01→2026-01-01", day1["date"] == "2026-01-01")
        check("tag 计数", day1["tagged"] >= 1 and day1["counts"]["status"].get("trash") == 1, str(day1))
        br = serve.browse_dir(root)
        check("browse 列子目录含计数", any(d["name"] == "2026.01.01" and d["jpg"] == 3 for d in br["dirs"]), str(br["dirs"]))

        print("== 缩略图(sips) ==")
        out, key = serve.ensure_thumb(os.path.join(d1, "DSC00002.JPG"), 240)
        check("生成缩略图", os.path.exists(out) and os.path.getsize(out) > 0)
        out2, key2 = serve.ensure_thumb(os.path.join(d1, "DSC00002.JPG"), 240)
        check("缓存命中同 key", out == out2 and key == key2)

        print("== 导出 ==")
        dest = os.path.join(tmp, "picks")
        job = {"total": 2, "done": 0, "copied": 0, "skipped": 0, "errors": [], "finished": False, "dest": dest}
        serve.run_export(job, [os.path.join(d1, "DSC00002.JPG"), os.path.join(d2, "DSC00010.JPG")], dest)
        check("导出复制到按天子目录", os.path.exists(os.path.join(dest, "2026.01.01", "DSC00002.JPG")) and
              os.path.exists(os.path.join(dest, "2026.01.02", "DSC00010.JPG")) and job["copied"] == 2, str(job))
        dtags = serve.load_tags(os.path.join(dest, "2026.01.01"))
        check("导出 tag 随行", dtags.get("DSC00002.JPG", {}).get("status") == "trash", str(dtags))
        job2 = {"total": 1, "done": 0, "copied": 0, "skipped": 0, "errors": [], "finished": False, "dest": dest}
        serve.run_export(job2, [os.path.join(d1, "DSC00002.JPG")], dest)
        check("重复导出跳过", job2["skipped"] == 1 and job2["copied"] == 0, str(job2))

        print("== HTTP 冒烟 ==")
        serve.DEFAULT_ROOT = root
        srv = serve.ThreadingHTTPServer(("127.0.0.1", 0), serve.Handler)
        srv.daemon_threads = True
        port = srv.server_address[1]
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        st, ct, data = http(port, "/api/scan?root=" + urllib.parse.quote(root))
        check("GET /api/scan", st == 200 and len(json.loads(data)["days"]) == 2)
        st, ct, data = http(port, "/api/photos?dir=" + urllib.parse.quote(d1))
        j = json.loads(data)
        check("GET /api/photos", st == 200 and len(j["photos"]) == 3 and "DSC00002.JPG" in j["tags"])
        st, ct, data = http(port, "/img?path=" + urllib.request.quote(os.path.join(d1, "DSC00002.JPG")))
        check("GET /img", st == 200 and ct.startswith("image/jpeg") and data == TINY_JPG)
        st, ct, data = http(port, "/thumb?path=" + urllib.request.quote(os.path.join(d1, "DSC00002.JPG")) + "&w=240")
        check("GET /thumb", st == 200 and ct.startswith("image/jpeg") and len(data) > 0)
        st, ct, data = http(port, "/api/tag", {"dir": d1, "file": "DSC00003.JPG", "tags": {"status": "sooc"}})
        check("POST /api/tag", st == 200 and json.loads(data)["tags"]["status"] == "sooc")
        st, ct, data = http(port, "/tagger")
        check("GET /tagger 页面", st == 200 and b"phototag" in data)
        st, ct, data = http(port, "/gallery")
        check("GET /gallery 页面", st == 200 and b"phototag" in data)
        st, ct, data = http(port, "/v1")
        check("GET /v1 页面", st == 200 and b"showDirectoryPicker" in data)
        try:
            http(port, "/img?path=/etc/passwd")
            check("GET /img 越界 403", False)
        except urllib.error.HTTPError as e:
            check("GET /img 越界 403", e.code == 403)
        srv.shutdown()

        print("== tools: sweep_trash ==")
        env = dict(os.environ)
        r = subprocess.run([sys.executable, os.path.join(TOOLS, "sweep_trash.py"), "--root", root],
                           capture_output=True, text=True, env=env)
        check("sweep dry-run 列出废片", r.returncode == 0 and "DSC00002.JPG" in r.stdout and "dry-run" in r.stdout, r.stdout + r.stderr)
        check("sweep dry-run 没动文件", os.path.exists(os.path.join(d1, "DSC00002.JPG")))
        r = subprocess.run([sys.executable, os.path.join(TOOLS, "sweep_trash.py"), "--root", root, "--apply"],
                           capture_output=True, text=True, env=env)
        binday = os.path.join(root, "_trash_bin", "2026.01.01")
        check("sweep apply 移动 JPG+RAW+._", r.returncode == 0 and
              os.path.exists(os.path.join(binday, "DSC00002.JPG")) and
              os.path.exists(os.path.join(binday, "DSC00002.ARW")) and
              not os.path.exists(os.path.join(d1, "DSC00002.JPG")) and
              not os.path.exists(os.path.join(d1, "DSC00002.ARW")), r.stdout + r.stderr)
        check("sweep 后源 tag 移除", "DSC00002.JPG" not in serve.load_tags(d1))
        check("sweep 后 bin tag 记录", serve.load_tags(binday).get("DSC00002.JPG", {}).get("status") == "trash")

        print("== tools: collect_picks ==")
        # 此时 sooc 的有:DSC00001.JPG(损坏自愈从 .bak 复活)与 DSC00003.JPG(HTTP 冒烟写入)
        picks = os.path.join(tmp, "picks2")
        r = subprocess.run([sys.executable, os.path.join(TOOLS, "collect_picks.py"),
                            "--root", root, "--dest", picks, "--where", "status=sooc"],
                           capture_output=True, text=True, env=env)
        check("collect dry-run", r.returncode == 0 and "2 张" in r.stdout and not os.path.exists(picks), r.stdout + r.stderr)
        r = subprocess.run([sys.executable, os.path.join(TOOLS, "collect_picks.py"),
                            "--root", root, "--dest", picks, "--where", "status=sooc", "--apply"],
                           capture_output=True, text=True, env=env)
        check("collect apply 复制+tag随行", r.returncode == 0 and
              os.path.exists(os.path.join(picks, "2026.01.01", "DSC00001.JPG")) and
              os.path.exists(os.path.join(picks, "2026.01.01", "DSC00003.JPG")) and
              serve.load_tags(os.path.join(picks, "2026.01.01")).get("DSC00003.JPG", {}).get("status") == "sooc",
              r.stdout + r.stderr)

        print("\n全部通过:%d 项 ✓" % len(PASS))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
