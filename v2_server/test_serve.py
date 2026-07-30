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
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.normpath(os.path.join(HERE, "..", "tools"))
sys.path.insert(0, HERE)
import serve  # noqa: E402
import phototag_core as core  # noqa: E402


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


def build_tools_fixture(base):
    """独立的干净库,用于工具四操作(不受前面测试对主 fixture 的改动影响)。"""
    src = os.path.join(base, "lib2")
    da = os.path.join(src, "2026.02.01")
    db = os.path.join(src, "2026.02.02")
    for n in ("DSC1", "DSC2", "DSC3", "DSC4"):
        write(os.path.join(da, n + ".JPG"))
        write(os.path.join(da, n + ".ARW"), b"raw")
    write(os.path.join(db, "DSC10.ARW"), b"raw")                   # 孤 ARW(6300 场景)
    core.save_tags(da, {"DSC1.JPG": {"status": "sooc", "quality": "best"},
                        "DSC2.JPG": {"status": "edit", "type": "animal"},
                        "DSC3.JPG": {"status": "trash"}})
    core.save_tags(db, {"DSC10.ARW": {"status": "edit"}})
    return src, da, db


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
        check("list_photos 过滤 ._ 与成对 RAW", [p["name"] for p in ps] ==
              ["DSC00001.JPG", "DSC00002.JPG", "DSC00003.JPG"], str(ps))
        rawday = os.path.join(root, "rawonly")
        check("孤 ARW 视为照片(6300 场景)", [p["name"] for p in serve.list_photos(rawday)] ==
              ["DSC00099.ARW"], str(serve.list_photos(rawday)))
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
        serve.set_tags_many(d1, {"DSC00002.JPG": {"status": "trash"}, "DSC00003.JPG": {"type": "insect"}})
        serve.set_tags_many(d1, {"DSC00001.JPG": {}})
        t = serve.load_tags(d1)
        check("清空即删除条目", "DSC00001.JPG" not in t and len(t) == 2, str(t))
        check("insect 为合法类型", t["DSC00003.JPG"]["type"] == "insect", str(t))
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
        check("识别天文件夹(含纯RAW天)/跳过回收站", names == ["2026.01.01", "2026.01.02", "rawonly"], str(names))
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
        check("GET /api/scan", st == 200 and len(json.loads(data)["days"]) == 3)
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
        st, ct, data = http(port, "/light")
        check("GET /light 页面", st == 200 and b"showDirectoryPicker" in data)
        st, ct, data = http(port, "/v1")
        check("GET /v1 旧名兼容", st == 200 and b"showDirectoryPicker" in data)
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

        print("== tools: export_xmp ==")
        serve.set_tags_many(rawday, {"DSC00099.ARW": {"status": "edit", "type": "animal"}})
        xmp99 = os.path.join(rawday, "DSC00099.xmp")
        r = subprocess.run([sys.executable, os.path.join(TOOLS, "export_xmp.py"),
                            "--root", root, "--where", "status=edit,sooc"],
                           capture_output=True, text=True, env=env)
        check("xmp dry-run 不写文件", r.returncode == 0 and "待写" in r.stdout and not os.path.exists(xmp99),
              r.stdout + r.stderr)
        pre = os.path.join(d1, "DSC00001.xmp")   # 预置"LrC 已有"的 sidecar,验证不覆盖
        with open(pre, "w") as f:
            f.write("LR-OWNED")
        r = subprocess.run([sys.executable, os.path.join(TOOLS, "export_xmp.py"),
                            "--root", root, "--where", "status=edit,sooc", "--apply"],
                           capture_output=True, text=True, env=env)
        check("xmp apply 写入", r.returncode == 0 and os.path.exists(xmp99) and
              os.path.exists(os.path.join(d1, "DSC00003.xmp")), r.stdout + r.stderr)
        body = open(xmp99).read()
        check("xmp 含色标/星级映射与层级关键词", 'xmp:Label="Yellow"' in body and
              "phototag|status|edit" in body and "phototag|type|animal" in body, body[:300])
        check("绝不覆盖已有 xmp", open(pre).read() == "LR-OWNED")

        print("== 工具:core 四操作 ==")
        src2, da, db = build_tools_fixture(tmp)

        picks3 = os.path.join(tmp, "cheng")
        cond_sooc = core.parse_where(["status=sooc"])
        cp = core.collect_plan(src2, picks3, cond_sooc)
        check("collect_plan 只命中直出", cp["total_count"] == 1 and cp["days"][0]["names"] == ["DSC1.JPG"], str(cp))
        core.collect_apply(src2, picks3, cond_sooc)
        cheng_a = os.path.join(picks3, "2026.02.01")
        check("collect_apply 复制+tag随行", os.path.exists(os.path.join(cheng_a, "DSC1.JPG")) and
              core.load_tags(cheng_a).get("DSC1.JPG", {}).get("status") == "sooc")
        r2 = core.collect_apply(src2, picks3, cond_sooc)
        check("collect 幂等重跑跳过", r2["copied"] == 0 and r2["skipped"] == 1, str(r2))

        cond_edit = core.parse_where(["status=edit"])
        xp = core.xmp_plan(src2, cond_edit)
        check("xmp_plan 命中成对+孤 ARW", xp["total_count"] == 2, str(xp["days"]))
        core.xmp_apply(src2, cond_edit)
        x2, x10 = os.path.join(da, "DSC2.xmp"), os.path.join(db, "DSC10.xmp")
        check("xmp_apply 写 sidecar+映射", os.path.exists(x2) and os.path.exists(x10) and
              'xmp:Label="Yellow"' in open(x2).read() and "phototag|type|animal" in open(x2).read())
        r3 = core.xmp_apply(src2, cond_edit)
        check("xmp 不覆盖已有(重跑写 0)", r3["written"] == 0, str(r3))

        # sync:成片库补空白,绝不覆盖,stem 匹配 ARW→JPG
        core.save_tags(cheng_a, {"DSC1.JPG": {"status": "sooc"}})   # 已有 tag(模拟直出,故意只留 sooc)
        write(os.path.join(cheng_a, "DSC2.JPG"))                    # LrC 大修成品,无 tag
        write(os.path.join(cheng_a, "DSC99.JPG"))                   # 源库无对应
        write(os.path.join(picks3, "2026.02.02", "DSC10.JPG"))      # 6300 大修成品(源是 ARW)
        sp = core.sync_plan(picks3, src2)
        check("sync_plan 只数将补(空白且源有)", sp["total_count"] == 2 and
              sp["extra"]["skip_tagged"] >= 1 and sp["extra"]["not_found"] >= 1, str(sp["extra"]))
        core.sync_apply(picks3, src2)
        ta = core.load_tags(cheng_a)
        tb = core.load_tags(os.path.join(picks3, "2026.02.02"))
        check("sync 补大修 tag(edit/animal)", ta.get("DSC2.JPG", {}).get("status") == "edit" and
              ta.get("DSC2.JPG", {}).get("type") == "animal", str(ta))
        check("sync 绝不覆盖已有(DSC1 仍只 sooc)", ta["DSC1.JPG"].get("status") == "sooc" and
              "quality" not in ta["DSC1.JPG"], str(ta.get("DSC1.JPG")))
        check("sync stem 匹配 ARW→JPG(DSC10)", tb.get("DSC10.JPG", {}).get("status") == "edit", str(tb))
        check("sync 不给查无项写空", "DSC99.JPG" not in ta)

        wp = core.sweep_plan(src2)
        check("sweep_plan 命中废片(含伴生)", wp["total_count"] == 1 and wp["extra"]["files"] >= 2, str(wp))
        core.sweep_apply(src2)
        check("sweep_apply 移动+源 tag 移除",
              os.path.exists(os.path.join(src2, "_trash_bin", "2026.02.01", "DSC3.JPG")) and
              "DSC3.JPG" not in core.load_tags(da))

        print("== 工具:HTTP ==")
        serve.DEFAULT_ROOT = src2
        srv2 = serve.ThreadingHTTPServer(("127.0.0.1", 0), serve.Handler)
        srv2.daemon_threads = True
        port2 = srv2.server_address[1]
        threading.Thread(target=srv2.serve_forever, daemon=True).start()
        st, ct, data = http(port2, "/tools")
        check("GET /tools 页面", st == 200 and b"/api/tool/plan" in data)
        dest_http = os.path.join(tmp, "cheng_http")
        st, ct, data = http(port2, "/api/tool/plan",
                            {"tool": "collect", "params": {"root": src2, "dest": dest_http, "where": ["status=sooc"]}})
        check("POST /api/tool/plan collect", st == 200 and json.loads(data)["total_count"] == 1, data)
        st, ct, data = http(port2, "/api/tool/plan", {"tool": "sync", "params": {"dest": picks3, "source_root": src2}})
        check("POST /api/tool/plan sync", st == 200 and "total_count" in json.loads(data))
        st, ct, data = http(port2, "/api/tool/apply",
                            {"tool": "collect", "params": {"root": src2, "dest": dest_http, "where": ["status=sooc"]}})
        job = json.loads(data)["job"]
        js = {}
        for _ in range(300):
            st, ct, data = http(port2, "/api/job_status?job=" + job)
            js = json.loads(data)
            if js.get("finished"):
                break
            time.sleep(0.03)
        check("POST /api/tool/apply 异步完成", js.get("finished") and js["result"]["copied"] == 1, str(js))
        check("apply 结果落地", os.path.exists(os.path.join(dest_http, "2026.02.01", "DSC1.JPG")))
        try:
            http(port2, "/api/tool/plan", {"tool": "collect", "params": {"root": "/etc", "dest": dest_http}})
            check("工具路径越界 403", False)
        except urllib.error.HTTPError as e:
            check("工具路径越界 403", e.code == 403)
        srv2.shutdown()

        print("\n全部通过:%d 项 ✓" % len(PASS))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
