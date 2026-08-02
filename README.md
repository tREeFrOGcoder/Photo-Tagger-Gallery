# phototag — 键盘流照片打标 & 成片画廊

给 Sony A7V 按天存储的 JPG+ARW 照片库做的两件套:

1. **打标器(tagger)**:全键盘快速过片、打三维 tag(状态/类型/质量),看的是 JPG 原图。
2. **画廊(gallery)**:大量带 tag 照片的快速浏览器 —— 时间线、tag 筛选、灯箱、选择、整组导出、**全屏幻灯片(竖屏自动拼图)**。

tag 存在每个天文件夹里的一个 `phototags.json`(轻量 JSON,原子写入,照片搬走 tag 跟着走)。
详细设计见 [docs/DESIGN.md](docs/DESIGN.md)。

## 两个版本

| | light 单文件版 | v2 服务器版(主力) |
|---|---|---|
| 启动 | Chrome 直接打开 `light/tagger.html` | `python3 v2_server/serve.py` 后开浏览器 |
| 依赖 | 无(仅 Chrome/Edge) | 仅 macOS 自带 python3 + sips,任意浏览器 |
| 打标器 | ✅(无缩略图条,仅 JPG) | ✅(带缩略图条,支持纯 RAW 库) |
| 画廊 | ❌ | ✅ 缩略图缓存 / 筛选 / 导出 |
| 适合 | 临时机器、零配置快速过片 | 日常主力 |

## 快速开始(v2)

```bash
cd ~/Desktop/code/phototag/v2_server
python3 serve.py --root "/Volumes/My Book/Sony A7V" --open     # A7V 库(JPG+ARW)
python3 serve.py --root "/Volumes/ZTSSD/Sony 6300" --open      # 6300 纯 RAW 库,直接可筛
# 打标器 /tagger   画廊 /gallery   工具 /tools   —— 三页共用同一进程,右上角小切换器互跳
# 不带 --root 时自动探测常用照片盘;挂后台:nohup python3 v2_server/serve.py >/dev/null 2>&1 &
```

light 版:用 Chrome 打开 `light/tagger.html`(或访问 `/light`),点「选择照片根目录」,选到 `Sony A7V` 那一层即可。

## 键位(两版一致)

左手三排 = 三个维度,同键再按 = 取消;右手方向键翻页。

```
 类型   [Q] 风景   [W] 动物   [E] 人像   [R] 昆虫   [T] 美食
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
| 类型 | Q/W/E/R/T | 风景/动物/人像/昆虫/美食 | 昆虫单列不归入动物;美食单列:处理/分享策略不同 |
| 质量 | Z/X | 绝美 `best` / 普通 `normal` | 绝美与是否直出无关 |

任何维度都可以不打(未定);第一遍建议只打状态,开着自动前进,一键一张。

## 过滤器(第二遍神器)

侧栏「过滤」区,每个 tag 值一个小 chip:**点一下 = ✕不看,再点 = ✓只看,再点取消**(维度内"只看"是或,维度间是且,含「未定」)。典型用法:第一遍全速 D 标废片 → 点一下「废片」→ 废片从翻页/N/自动前进里消失(总览和缩略图条里变暗但可点),安心打类型/质量。过滤条件会记住,HUD 显示「筛中 n 张」。

## 画廊:选择 & 幻灯片

- **选择**:缩略图右下角勾选框;单击勾选、**shift 点两张选中间全部**、「全选」(当前筛选)、每天表头「全选本天」。选中集跨 filter 保留,可整组**导出**(没选就导当前筛选)。
- **幻灯片**(顶栏「▶ 幻灯片」,没选就播当前筛选):
  - **真全屏**(Fullscreen API,占满整屏黑底);淡出→全黑→淡入,不重叠(各 0.3s);停留**默认 8s**(2/4/6/8s 可选);空格暂停、方向键切换、Esc 退出;控件自动隐藏。
  - **shuffle**:全乱(big)/ 按日乱(small=天序不变、天内打乱)。
  - **适应 / 铺满**:适应=贴满屏高留侧边(默认);铺满=每张放大 cover 铺满各自那份屏幕(横屏铺满全屏),仅留细黑缝。
  - **竖屏拼图**:遇竖屏自动拼 1-2 张竖屏填满宽屏(超宽屏更多),横屏留侧边不放大铺满、超宽时旁边也拼张竖屏;照片贴满屏高、之间留细黑缝、纯黑底。
  - 深链 `?ss=1` 打开即自动播放。

## RAW(ARW)支持

- 没有同名 JPG 的 ARW(如 6300 库)会被当作照片直接显示:浏览用**内嵌 1920 预览**(秒开),**放大时自动换全尺寸解码**(首次几秒,之后走缓存),HUD 显示 `RAW·预览` 徽章。
- JPG+ARW 成对时仍然只看 JPG;light 版不支持 RAW。

## 工具脚本

```bash
# 废片清扫:把 status=trash 的 JPG+ARW 移进 <root>/_trash_bin/<天>/(默认 dry-run,只打印)
python3 tools/sweep_trash.py --root "/Volumes/My Book/Sony A7V"
python3 tools/sweep_trash.py --root "/Volumes/My Book/Sony A7V" --apply   # 真的移动

# 成片收集:按 tag 条件复制进成片目录(默认 dry-run)
python3 tools/collect_picks.py --root "/Volumes/My Book/Sony A7V" --dest ~/Pictures/成片 --where status=sooc
python3 tools/collect_picks.py --root "/Volumes/My Book/Sony A7V" --dest ~/Pictures/成片 --where status=sooc quality=best --apply

# XMP 桥:给「大修」的 RAW 生成 .xmp sidecar,LrC 零复制导入按色标筛(详见 docs/LIGHTROOM.md)
python3 tools/export_xmp.py --root "/Volumes/My Book/Sony A7V"          # dry-run
python3 tools/export_xmp.py --root "/Volumes/My Book/Sony A7V" --apply  # 绝不覆盖已有 .xmp
```

安全原则:**任何工具都不执行删除**。废片只是移进 `_trash_bin`,确认无误后由你自己清空。

## 成片工作流(建议)

心智模型:**两个库 + 一次 LrC 绕道**。**源库**(JPG+ARW)打完 tag,直出与大修的成品都汇进**成片库**(纯 JPG,可发布),废片进 `_trash_bin`。四个批处理都在 **工具页 `/tools`**(填路径→预览→确认→进度;也有等价 CLI):

1. 打标器第一遍:A/S/D 定状态(自动前进,一键一张),顺手 Z 标绝美;废片用过滤器隐藏后继续打类型/质量。
2. **废片清扫**:预览清单 → 确认,移进 `_trash_bin`(只移不删,可捞回)。
3. **直出**成片:**成片收集**(条件=直出)复制进成片库,tag 随行;或画廊「导出当前筛选」发临时子集。
4. **大修**:**XMP 导出**给大修 RAW 写 sidecar → LrC「添加」导入按黄标聚组来修(见 [docs/LIGHTROOM.md](docs/LIGHTROOM.md))→ 导出成品**保持原文件名**进成片库对应天文件夹。
5. **Tag 同步**:把 LrC 导出、成片库里还没 tag 的大修成品,按「天+主名」回源库补 tag(**只补空白,绝不覆盖**;大修片带回 `edit` 当出身记录,兼容 6300 的 ARW→JPG)。
6. 日常浏览/发布:画廊指向成片库,按时间/tag 切组、导出子集。

## 工具页 /tools

一页四张卡片,把上面第 2–5 步做成图形版,**每个都先预览(只读)、确认才执行**;路径用目录浏览弹窗选,除废片清扫外都不动源库原图。三个页面(打标 / 画廊 / 工具)由同一个后台进程提供,右上角小切换器点一下即互跳(带当前根目录,不占键位)。

| 卡片 | 作用 | CLI 等价 |
|---|---|---|
| 成片收集 | 按 tag 条件复制进成片库(tag 随行) | `collect_picks.py` |
| XMP 导出 | 给大修 RAW 写 LrC 可读 .xmp | `export_xmp.py` |
| Tag 同步 | 给成片库无 tag 的 LrC 成品补 tag | (仅网页版) |
| 废片清扫 | 废片移进 `_trash_bin`(二次确认) | `sweep_trash.py` |

## FAQ

- **`._` 开头的文件是什么?** exFAT 上 macOS 的元数据垃圾,所有工具都会自动忽略。
- **某天只有 ARW 没有 JPG?** 现在直接支持:孤 ARW 按内嵌预览显示、可打 tag、放大自动全尺寸解码(v2;light 版仍仅 JPG)。
- **缩略图缓存在哪?** `~/Library/Caches/phototag/`,可随时整体删除,不影响任何数据。
- **tag 会被写坏吗?** 原子写 + 写前 `.bak` 备份 + 损坏自愈 + 全局流水日志(`~/Library/Application Support/phototag/journal.jsonl`),详见设计文档。
