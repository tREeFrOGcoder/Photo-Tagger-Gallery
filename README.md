# phototag

**Keyboard-first photo tagging & a finished-photo gallery** — for large JPEG+RAW libraries stored day-by-day (built around a Sony A-series workflow, but works with any camera that pairs JPEG + RAW).

English · [中文说明](README.zh-CN.md)

Tags live in one small `phototags.json` per day-folder (plain JSON, atomic writes — move the photos and the tags travel with them). No database, no cloud, no build step. The server binds to `127.0.0.1` only.

---

## Table of contents

- [What you get](#what-you-get)
- [Platform support (macOS / Windows / Linux)](#platform-support)
- [Requirements](#requirements)
- [Quick start (v2 server edition)](#quick-start)
- [The two editions](#the-two-editions)
- [The tag model](#the-tag-model)
- [Tagger page](#tagger-page)
- [Gallery page](#gallery-page)
- [Slideshow](#slideshow)
- [Tools page](#tools-page)
- [Light edition (browser-only, cross-platform)](#light-edition)
- [Command-line tools](#command-line-tools)
- [Data, storage & safety](#data-storage--safety)
- [The finished-photo workflow](#the-finished-photo-workflow)
- [FAQ & troubleshooting](#faq--troubleshooting)
- [Project layout](#project-layout)

---

## What you get

- **Tagger** — full-keyboard, one-key-per-photo tagging across three dimensions (status / type / quality), with auto-advance, filters, an overview grid, and pinch/scroll zoom. Reads your JPEGs; RAW-only libraries work too.
- **Gallery** — a fast browser for thousands of tagged photos: day timeline, tag filters with live counts, a lightbox (you can re-tag inside it), multi-select, batch export, and a **fullscreen slideshow** that auto-collages portrait shots to fill a widescreen.
- **Tools** — four batch operations with a **preview-then-confirm** GUI: collect finished shots, export XMP sidecars for Lightroom, back-fill tags, and sweep trash. Nothing is ever deleted.
- **Light edition** — a single HTML file that runs in Chrome/Edge with no Python at all (great for Windows or a borrowed machine).

Everything is plain Python standard library + native single-file HTML/CSS/JS. No frameworks, no `npm`, no external services.

---

## Platform support

| Edition | macOS | Windows | Linux |
|---|:--:|:--:|:--:|
| **v2 server** (tagger + gallery + tools) | ✅ | ❌ (see below) | ⚠️ needs a `sips` replacement |
| **light** (single-file tagger) | ✅ | ✅ | ✅ |

**Why the v2 server is macOS-only right now:** thumbnails, RAW embedded-preview extraction, and full-size RAW decoding all shell out to **`sips`**, Apple's built-in image tool. Python itself is cross-platform; `sips` is not. On Windows there is no `sips`, so thumbnails, the gallery, and RAW previews won't work.

**On Windows today:** use the **[light edition](#light-edition)** — open `light/tagger.html` in Chrome or Edge, pick your photo folder, and tag. No install. Limitations: it shows **JPEGs only** (no RAW preview), and there's no gallery/tools/thumbnail-strip.

**Want the full v2 on Windows/Linux?** Replace the `sips` calls in `v2_server/serve.py` (`ensure_thumb`, `ensure_arw_preview`, `ensure_arw_full`) with a cross-platform decoder such as **Pillow** or **ImageMagick**. That's the only OS-specific dependency; the rest of the server is portable. PRs welcome.

---

## Requirements

- **macOS** with its built-in **Python 3** and **`sips`** (both preinstalled — nothing to install).
- **Optional:** [`exiftool`](https://exiftool.org) (`brew install exiftool`). If present, RAW embedded previews open instantly; without it, phototag falls back to a slower `sips` decode.
- Any modern browser for the UI. The **light edition** needs Chrome or Edge (it uses the File System Access API).

---

## Quick start

> Replace `/path/to/photos` with your library's top folder — the one that **contains the day sub-folders**.

Your library is expected to be organized one folder per day:

```
/path/to/photos/
├── 2026.01.01/           ← a "day" folder (name is used as the date)
│   ├── DSC00001.JPG
│   ├── DSC00001.ARW      ← optional RAW companion
│   └── phototags.json    ← created & maintained by phototag
├── 2026.01.02/
│   └── ...
```

Start the server:

```bash
cd v2_server
python3 serve.py --root "/path/to/photos" --open
```

- `--root PATH` — default library to open. Must live under `/Volumes` or your home folder. If omitted, phototag auto-detects likely photo drives and you pick a folder in the UI.
- `--port N` — default `8787`.
- `--open` — open your browser automatically.

Then open **http://127.0.0.1:8787** — you land on the **tagger** (`/tagger`). The gallery is at `/gallery`, tools at `/tools`, and a small **click-only switcher** in the top corner jumps between the three (it carries your current root along).

Run it in the background if you like:

```bash
nohup python3 serve.py --root "/path/to/photos" >/dev/null 2>&1 &
# ...or just open a tmux/screen window and run it there.
```

---

## The two editions

|  | **light** (single file) | **v2 server** (main) |
|---|---|---|
| Launch | Open `light/tagger.html` in Chrome/Edge | `python3 v2_server/serve.py` then open a browser |
| Needs | Nothing (Chrome/Edge only) | macOS Python 3 + `sips`; any browser |
| Tagger | ✅ (filename cards, no image thumbnails; JPEG only) | ✅ (image thumbnails, RAW-only libraries supported) |
| Gallery | ❌ | ✅ (thumbnail cache, filters, export) |
| Tools | ❌ | ✅ (collect / XMP / sync / sweep) |
| Best for | A borrowed machine, Windows, zero-config quick tagging | Everyday driver |

Both editions read and write the **same `phototags.json`** format, so you can mix and match.

---

## The tag model

Every photo can carry up to three independent tags. Each dimension is optional — leave it **未定 (unset)** if you're unsure. The left hand covers all three; **press the same key again to clear** that dimension.

| Dimension | Keys | Values (label · code) | Meaning |
|---|---|---|---|
| **Type** | `Q W E R T` | Scenery `scenery` · Animal `animal` · Portrait `portrait` · Insect `insect` · Food `food` | Insect & Food are split out on purpose — you process/share them differently. |
| **Status** | `A S D` | SOOC `sooc` · Edit `edit` · Trash `trash` | SOOC = the JPEG is already the keeper. Edit = keep the RAW, take it to Lightroom. Trash = JPEG+RAW should go (tools only **move** it, never delete). |
| **Quality** | `Z X` | Best `best` · Normal `normal` | "Best" is independent of whether it's a straight JPEG or an edit. `best` shows a ★. |

Each value has a fixed color that's used consistently everywhere (green SOOC, blue Edit, red Trash, yellow Best, etc.).

**First pass tip:** tag only **status** with auto-advance on — one key, one photo, blazing fast. Then hide the trash with a filter and do a calm second pass for type/quality.

---

## Tagger page

`/tagger` — the full-keyboard tagging surface. You look at the JPEG at full size and fly through your library.

```
┌─ Sidebar (250px) ──┬─ Photo stage ─────────────────────────┐
│ 打标·画廊·工具 nav │ [tag badges]                  [zoom%] │
│ 📁 root name  (O)  │                                       │
│ ── Filters ──      │                                       │
│  类型 / 状态 / 质量 │            (current photo)            │
│ ── Day list ──     │                                       │
│  2026.01.02  12/40 │                             [spinner] │
│  2026.01.01  ▓▓░ 8 │  [thumbnail strip — Tab]              │
│ footer: N days     │  HUD: file · 12/40 · keycaps · saved  │
└────────────────────┴───────────────────────────────────────┘
```

### The panels

- **Sidebar (left).** Top: the **page switcher** (`打标 · 画廊 · 工具`), the current **root name** (click `📁 换目录` or press `O` to change), and a **Filters** panel. Below that, the **day list** — newest first, each row showing `tagged / total` and a green progress bar. The footer shows the library totals.
- **Photo stage (center).** The current JPEG on black. Overlays: **tag badges** (top-left, colored chips for whatever you've set), a **zoom %** pill (top-right, only when zoomed), and a **loading spinner** (bottom-right, e.g. while a RAW decodes).
- **Thumbnail strip.** A horizontal filmstrip of nearby photos; toggle with `Tab`.
- **HUD (bottom bar).** Filename (with a `RAW·preview` badge for ARW files) · position `12 / 40` (and `· N in filter` when filtering) · day · a center cluster of **clickable key-caps** (the active tag lights up in its color) · an **auto-advance** indicator · a **save-state** indicator (`saved` / `saving…` / `retrying`).

### The Filters panel (your second-pass superpower)

Each dimension shows one chip per value, plus a **未定 (unset)** chip. **Click a chip to cycle it:**

1. first click → **✕ don't show** (hide/skip these),
2. second click → **✓ only show** (show only these),
3. third click → off.

"Only" within a dimension is OR; across dimensions it's AND. Navigation (Space / arrows / `N` / auto-advance) then only moves between matching photos; filtered-out photos are dimmed in the strip and grid but still clickable. The filter is remembered between sessions.

*Classic use:* full-speed `D` on the trash in pass one → click the **Trash** chip to `✕` → trash disappears from your flow → calmly tag type/quality on what's left.

### Overview grid — `G`

Press `G` for a full-screen contact sheet of the current day. Each tile shows the status dot, ★ if best, and the type label. Arrow keys move the selection, `N` jumps to the next untagged, `Enter`/click opens that photo, `Esc`/`G` closes.

### Zoom & pan — `.`

`.` toggles 100% ↔ fit. You can also **pinch on a trackpad** or **scroll the mouse wheel** for any zoom level (anchored at the pointer). While zoomed: two-finger scroll / drag / arrow keys pan. For a RAW file, zooming automatically swaps to a full-size decode for real pixel-peeping.

### Keyboard reference (tagger)

| Key | Action |
|---|---|
| `Q W E R T` | Type: Scenery / Animal / Portrait / Insect / Food (same key again = clear) |
| `A S D` | Status: SOOC / Edit / Trash |
| `Z X` | Quality: Best / Normal |
| `Space` · `J` | Next photo (within filter) |
| `K` | Previous photo |
| `←` `→` | Prev/next photo — or **pan** when zoomed |
| `↑` `↓` | Pan (only while zoomed) |
| `U` · `⌘Z` | Undo (jumps back to that photo); `⇧U` · `⌘⇧Z` = redo |
| `⌫` | Clear **all** tags on the current photo |
| `F` | Toggle "auto-advance after setting status" (default on) |
| `N` | Jump to the next photo with no status set |
| `.` | Toggle 100% / fit zoom |
| `Tab` | Show/hide the thumbnail strip |
| `G` | Overview grid |
| `[` · `]` | Previous / next day |
| `H` · `?` | Help |
| `O` | Change root folder |
| `Esc` | Exit zoom / close grid / close a dialog |

---

## Gallery page

`/gallery` — a scrollable **timeline** of every tagged photo, newest day first, for browsing, filtering, selecting, exporting, and the slideshow.

```
┌ root · 换目录 · matched 320/1200 ·  [search] [▶ Slideshow] [Export…] · nav ┐
│ Filters:  状态[…] 类型[…] 质量[…] 时间[全部|近7|近30|近90]   ✕ clear       │
├─────────────────────────────────────────────────────────────────────────────┤
│ 2026.01.02 · 40 photos ·  去打标 ↗   全选本天                                 │
│  [▦][▦][▦][▦][▦][▦][▦]  ← tiles: status dot · ★ · type label · ✓ checkbox    │
│ 2026.01.01 · …                                                               │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Header & filters

- **Header:** root name, `📁 换目录`, a match counter (`matched N / total M`), a **filename search**, `▶ 幻灯片` (slideshow), `导出当前筛选…` (export), and the page switcher.
- **Filter chips:** one row per dimension (状态 / 类型 / 质量), each chip showing a **live facet count** (how many photos it would match). Selecting chips is additive within a dimension (union) and AND across dimensions. A **时间 (date)** row offers `全部 / 近7天 / 近30天 / 近90天`. `✕ 清除筛选` resets everything.

### Tiles & lightbox

Each tile is a 3:2 thumbnail with a status dot, ★ (if best), the type label, and a **selection checkbox** (bottom-right, appears on hover). **Click a tile** to open the **lightbox** — a fullscreen viewer with the same zoom/pan engine as the tagger. Inside the lightbox you can **re-tag** with the exact same keys (`A S D`, `Q W E R T`, `Z X`, `U` to undo) — this is where a lot of second-pass cleanup happens. `去打标器 ↗` jumps to the tagger at that exact photo.

### Selection & export

- **Checkbox** click = select; **Shift-click** selects the range from the last one. **Shift-click a tile body** also range-selects (without opening the lightbox).
- **`全选本天`** (per day header) selects that whole day; **`全选`** selects everything currently filtered.
- When anything is selected, a floating bar appears: **`已选 N 张`**, `▶ 播放选中`, `导出选中…`, `全选`, `清除`. Your selection is kept even as you change filters.
- **Export** (selection, or the current filter if nothing is selected) copies into a destination folder, **one sub-folder per day**, skipping files that already exist at the same size, and writes a matching `phototags.json` so tags travel with the copies. It runs as a background job with a progress bar.

### Keyboard (gallery)

Timeline: `H` / `?` reminds you to click a photo for the lightbox. **In the lightbox:** `Q W E R T` / `A S D` / `Z X` tag · `Space`/`J` next · `K` prev · `←`/`→` prev/next or pan when zoomed · `.` zoom · `U` undo (`⇧U` redo) · `Esc` exit zoom then close.

---

## Slideshow

Reached from `▶ 幻灯片` (plays the current filter) or `▶ 播放选中` (plays your selection). It goes **truly fullscreen** (black background) and packs photos into "screens": portrait shots are **auto-collaged** side-by-side to fill a widescreen, landscapes keep their side margins, everything sits flush to screen height with thin black gaps. On a normal 16:9/16:10 display that's **1–2 photos per screen** (an ultrawide fits more).

**Controls** (press `Esc` once to reveal the control bar — it's hidden by default so it never pops up while you watch):

- **⏮ ⏸ ⏭** — previous / play-pause / next. Counter reads **`current/total 屏 · N 张`** (which screen of how many · total photos — collaging means fewer screens than photos, which is normal).
- **Shuffle:** `顺序` (in order) · `按日乱` (keep day order, shuffle within each day) · `全乱` (full shuffle).
- **Fit:** `适应` (fit the whole photo, letterboxed) · `铺满` (cover/fill each slot, cropped, thin gaps kept).
- **停留 (dwell):** `2s` · `8s` (default) · `30s` · or type any number of seconds in the inline box.
- **淡变 (fade):** `硬切` (hard cut, no fade) · `0.3s` (default) · `0.6s` · or any number of seconds inline. Fade-out → black → fade-in, no crossfade.
- **🔒** re-lock & return to fullscreen · **✕** exit.

**Lock / Esc flow:** it starts **locked** and fullscreen. Press **Esc** once → the browser leaves fullscreen and the control bar appears (the show keeps running). Press **Esc again** → exit. Click **🔒** to re-lock.

**Keys:** `Space` play/pause · `→`/`J` next · `←`/`K` prev · `Esc` unlock → exit.

**Deep link:** open `/gallery?ss=1` to auto-start the slideshow. Add `&fill=1` to start in 铺满, and `&fade=<ms>` to set the fade duration (e.g. `&fade=600` softer, `&fade=0` hard cut).

*Under the hood:* photos are shown at full resolution; the next ~5 are pre-decoded during the dwell and each transition waits for the real image `decode()` before fading in, so the fade is smooth and the black gap is tiny. Aspect ratios are measured from thumbnails so packing is correct before anything loads.

---

## Tools page

`/tools` — four batch operations as cards. **Every card previews first (read-only) and only acts when you confirm.** Paths are chosen with a folder-browser popup. Except for the trash sweep, nothing touches your source originals. All three pages share one background process; the top-corner switcher jumps between them.

| Card | Badge | What it does | CLI equivalent |
|---|---|---|---|
| **成片收集 (Collect)** | copy | Copy photos matching a tag condition into your finished-photos folder (tags travel; same-name+size skipped). Default condition: status = SOOC. | `collect_picks.py` |
| **XMP 导出 (Export XMP)** | write sidecar | Write a Lightroom-readable `.xmp` next to each Edit RAW (color label + keywords). **Never overwrites** an existing `.xmp`. | `export_xmp.py` |
| **Tag 同步 (Sync tags)** | fill blanks | Claim finished photos that Lightroom exported into your finished folder but that have no tags yet: back-fill from the source library by **day + basename**. **Only fills blanks, never overwrites.** | *(web only)* |
| **废片清扫 (Sweep trash)** | move | Move `status=trash` JPEG+RAW+sidecars into `<root>/_trash_bin/`. **Moves only, never deletes** — you can fish them back out. | `sweep_trash.py` |

**Flow for every card:** fill the path(s) → **预览 (Preview)** shows exactly what would happen (counts, size, a per-day breakdown, any warnings) → the apply button enables only if there's something to do → **confirm** → a progress bar runs the job (the trash sweep asks a second time first).

---

## Light edition

`light/tagger.html` — the whole tagger in **one HTML file**, no server. Open it in **Chrome or Edge**, click **选择照片根目录**, and pick your library folder. It uses the browser's File System Access API to read your photos and write `phototags.json` straight into each day folder (with a `localStorage` mirror as backup), and remembers your folder for next time.

It's the same tagger UI and the **same keyboard shortcuts** as v2, with these differences:

- **JPEG only** — no RAW preview, no full-size decode.
- The thumbnail strip and overview grid show **filename cards** (name + status dot + ★ + type label), not image thumbnails (there's no thumbnail server).
- No gallery, no tools, no page switcher.
- Needs Chrome/Edge (Safari/Firefox lack the API). This is the recommended path on **Windows**.

You can also reach it from a running v2 server at `/light`.

---

## Command-line tools

The tools page is the GUI for these; the scripts exist too (all default to a **dry run** that only prints — add `--apply` to act).

```bash
# Sweep trash → <root>/_trash_bin/<day>/ (moves JPEG+RAW+sidecars; never deletes)
python3 tools/sweep_trash.py --root "/path/to/photos"
python3 tools/sweep_trash.py --root "/path/to/photos" --apply

# Collect finished photos by tag condition into a destination folder
python3 tools/collect_picks.py --root "/path/to/photos" --dest "/path/to/selects" --where status=sooc
python3 tools/collect_picks.py --root "/path/to/photos" --dest "/path/to/selects" --where status=sooc quality=best --apply

# XMP bridge: write .xmp sidecars for Edit RAWs so Lightroom can import & filter by color label
python3 tools/export_xmp.py --root "/path/to/photos"          # dry run
python3 tools/export_xmp.py --root "/path/to/photos" --apply  # never overwrites an existing .xmp
```

See [`docs/LIGHTROOM.md`](docs/LIGHTROOM.md) for the Lightroom import recipe.

**Safety principle: no tool ever deletes anything.** Trash is only *moved* into `_trash_bin`; you empty it yourself once you're sure.

---

## Data, storage & safety

- **Tags** are stored as `phototags.json` in each day folder — one small JSON object keyed by filename. Move/copy a day folder and its tags come along.
- **Writes are safe:** atomic write + `fsync`, a `.bak` backup before the first write, automatic healing of a corrupted file, and a global append-only journal at `~/Library/Application Support/phototag/journal.jsonl`.
- **Thumbnail cache** lives in `~/Library/Caches/phototag/`. It's disposable — delete it any time; it just regenerates. No photo data lives there.
- **RAW (ARW):** a JPEG+RAW pair shows the JPEG. A lone RAW (e.g. a RAW-only body) is shown via its embedded ~1920px preview (instant) and decoded full-size on zoom (a few seconds the first time, then cached).
- The server binds to **`127.0.0.1`** only and refuses any path outside `/Volumes` or your home folder.

---

## The finished-photo workflow

A suggested pipeline (all four batch steps are on the tools page, or as CLI scripts). Mental model: **two libraries + one Lightroom detour**.

1. **Tag pass 1** in the tagger: set status with auto-advance (one key per photo); hide trash with a filter, then tag type/quality.
2. **Sweep trash** → moved into `_trash_bin` (recoverable).
3. **SOOC keepers** → **Collect** (condition = SOOC) copies them into your finished folder, tags and all — or use the gallery's "export current filter".
4. **Edits** → **Export XMP** writes sidecars for the Edit RAWs → import into Lightroom by color label, edit, and **export keeping the original filename** into the finished folder.
5. **Sync tags** → back-fill tags onto those Lightroom exports (by day + basename; only fills blanks).
6. **Browse/publish:** point the gallery at the finished folder and slice by date/tag, export subsets, run slideshows.

---

## FAQ & troubleshooting

- **`sips: command not found` / no thumbnails.** You're not on macOS — the v2 server needs `sips`. Use the [light edition](#light-edition), or swap `sips` for Pillow/ImageMagick.
- **A day has only RAW, no JPEG.** Supported — lone RAWs show their embedded preview, can be tagged, and decode full-size on zoom (v2 only; light is JPEG-only).
- **What are `._`-prefixed files?** macOS metadata junk on exFAT drives; every tool ignores them automatically.
- **Where's the thumbnail cache?** `~/Library/Caches/phototag/` — safe to delete entirely.
- **Can my tags get corrupted?** Atomic writes + a `.bak` backup + self-healing + a journal (see [Data, storage & safety](#data-storage--safety)).
- **Port already in use / change the port.** `--port 9000`.

---

## Project layout

```
phototag/
├── v2_server/
│   ├── serve.py            # the server (stdlib only; sips for images)
│   ├── web/
│   │   ├── tagger.html     # /tagger
│   │   ├── gallery.html    # /gallery  (+ lightbox, selection, slideshow)
│   │   └── tools.html      # /tools
│   └── test_serve.py       # self-tests
├── phototag_core.py        # shared engine: the four batch operations (plan/apply)
├── tools/
│   ├── collect_picks.py    # CLI: collect finished photos
│   ├── export_xmp.py       # CLI: write Lightroom .xmp sidecars
│   └── sweep_trash.py      # CLI: move trash into _trash_bin
├── light/
│   └── tagger.html         # standalone browser-only tagger (no server)
└── docs/                   # DESIGN.md, LIGHTROOM.md, specs
```

Run the self-tests with `python3 v2_server/test_serve.py`.
