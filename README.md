# phototag — 键盘流照片打标 & 成片画廊

给 Sony A7V 按天存储的 JPG+ARW 照片库做的两件套:

1. **打标器(tagger)**:全键盘快速过片、打三维 tag(状态/类型/质量),看的是 JPG 原图。
2. **画廊(gallery)**:大量带 tag 照片的快速浏览器 —— 时间线、tag 筛选、灯箱、整组导出成片。

tag 存在每个天文件夹里的一个 `phototags.json`(轻量 JSON,原子写入,照片搬走 tag 跟着走)。
详细设计见 [docs/DESIGN.md](docs/DESIGN.md)。

## 两个版本

| | light 单文件版 | v2 服务器版(主力) |
|---|---|---|
| 启动 | Chrome 直接打开 `light/tagger.html` | `python3 v2_server/serve.py` 后开浏览器 |
| 依赖 | 无(仅 Chrome/Edge) | 仅 macOS 自带 python3 + sips,任意浏览器 |
| 打标器 | ✅(无缩略图条) | ✅(带缩略图条) |
| 画廊 | ❌ | ✅ 缩略图缓存 / 筛选 / 导出 |
| 适合 | 临时机器、零配置快速过片 | 日常主力 |

## 快速开始(v2)

```bash
cd ~/Desktop/code/phototag/v2_server
python3 serve.py --root "/Volumes/ZTSSD/Sony A7V"
# 打标器  http://127.0.0.1:8787/tagger
# 画廊    http://127.0.0.1:8787/gallery
# 加 --open 自动开浏览器
```

light 版:用 Chrome 打开 `light/tagger.html`(或访问 `/light`),点「选择照片根目录」,选到 `Sony A7V` 那一层即可。

## 键位(两版一致)

左手三排 = 三个维度,同键再按 = 取消;右手方向键翻页。

```
 类型   [Q] 风景   [W] 动物   [E] 人像   [R] 昆虫
 状态   [A] 直出   [S] 大修   [D] 废片
 质量   [Z] 绝美   [X] 普通
```

| 键 | 功能 |
|---|---|
| `Space` / `→` / `J` | 下一张 |
| `←` / `K` | 上一张 |
| `U` / `⌘Z` | 撤销(跳回那张);`Shift+U` 重做 |
| `⌫` | 清空当前照片全部 tag |
| `F` | 「打完状态自动下一张」开关(默认开) |
| `N` | 跳到下一张未打状态的照片 |
| `.` | 100% 放大 ↔ 适应屏幕;触控板**捏合**或**鼠标滚轮**任意倍率(跟随指针位置),放大时双指滚动/拖拽/方向键平移 |
| `Tab` | 缩略图条 / 列表 |
| `G` | 整面总览网格,方向键选择、`Enter`/点击跳转到那张开打 |
| `[` / `]` | 上一天 / 下一天 |
| `H` / `?` | 帮助 |
| `O` | 换目录 |

## Tag 含义

| 维度 | 键 | 值 | 说明 |
|---|---|---|---|
| 状态 | A | 直出 `sooc` | JPG 即成片 |
| 状态 | S | 大修 `edit` | 保留 RAW,进 Lightroom |
| 状态 | D | 废片 `trash` | JPG+RAW 都该清理(工具只移动,不删) |
| 类型 | Q/W/E/R | 风景/动物/人像/昆虫 | 昆虫单列不归入动物:后续处理/分享策略不同 |
| 质量 | Z/X | 绝美 `best` / 普通 `normal` | 绝美与是否直出无关 |

任何维度都可以不打(未定);第一遍建议只打状态,开着自动前进,一键一张。

## 工具脚本

```bash
# 废片清扫:把 status=trash 的 JPG+ARW 移进 <root>/_trash_bin/<天>/(默认 dry-run,只打印)
python3 tools/sweep_trash.py --root "/Volumes/ZTSSD/Sony A7V"
python3 tools/sweep_trash.py --root "/Volumes/ZTSSD/Sony A7V" --apply   # 真的移动

# 成片收集:按 tag 条件复制进成片目录(默认 dry-run)
python3 tools/collect_picks.py --root "/Volumes/ZTSSD/Sony A7V" --dest ~/Pictures/成片 --where status=sooc
python3 tools/collect_picks.py --root "/Volumes/ZTSSD/Sony A7V" --dest ~/Pictures/成片 --where status=sooc quality=best --apply
```

安全原则:**任何工具都不执行删除**。废片只是移进 `_trash_bin`,确认无误后由你自己清空。

## 成片工作流(建议)

1. 打标器第一遍:A/S/D 定状态(自动前进,一键一张),顺手 Z 标绝美。
2. `sweep_trash.py` dry-run 看清单 → `--apply` 挪走废片。
3. 大修片进 Lightroom,导出成品放进成片目录(按天子文件夹);成片目录同样可被打标器打开继续补 tag。
4. 直出成片用画廊「导出当前筛选」或 `collect_picks.py` 复制进成片目录。
5. 日常浏览:画廊指向成片目录,按时间/tag 切组、导出。

## FAQ

- **`._` 开头的文件是什么?** exFAT 上 macOS 的元数据垃圾,所有工具都会自动忽略。
- **某天只有 ARW 没有 JPG?**(如 2026.06.08)会被自动跳过,打标器只认含 JPG 的文件夹。
- **缩略图缓存在哪?** `~/Library/Caches/phototag/`,可随时整体删除,不影响任何数据。
- **tag 会被写坏吗?** 原子写 + 写前 `.bak` 备份 + 损坏自愈 + 全局流水日志(`~/Library/Application Support/phototag/journal.jsonl`),详见设计文档。
