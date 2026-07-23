# phototag 设计文档

日期:2026-07-18
状态:已定案(light[原 v1]/v2 首版实现依据)

## 1. 背景与需求

对象:Sony A7V 照片库,当前在 `/Volumes/ZTSSD/Sony A7V`,按天存储:

```
Sony A7V/
  2026.06.16/
    DSC01234.JPG   (~22MB, 直出)
    DSC01234.ARW   (~42MB, 与 JPG 一一对应)
  2026.07.17/
  ...
```

核心诉求(来自用户原话提炼):

1. **快速打 tag**:全键盘、Mac 顺手键位、不碰鼠标;支持撤销、跳回上一张;允许照片保持无 tag。
2. **只加载 JPG**:JPG/ARW 一一对应,看 JPG 即可;查看的是**原图**(非缩略图)。
3. **三个平行 tag 维度**(按重要级,第一遍优先打「状态」):
   - 状态:直出 jpg / 大修(保留 raw)/ 废片(jpg+raw 都该删)
   - 类型:风景 / 动物 / 人像 / 昆虫(2026-07-19 增补:昆虫不归入动物,后续处理与分享策略不同)
   - 质量:绝美 / 普通
4. **tag 存储极轻**,robust、好用。
5. **文件夹可搜索/选择**:ZTSSD、My Book、Mac 本地都可能是照片源。
6. **成片体系**:另建一个目录放成片(直出成片 + Lightroom 大修后导出的成片,两者都会打 tag);配一个**画廊 web app**,能快速加载大量带 tag 照片,按时间/tag 筛选浏览,支持按组导出。
7. 做几个版本对比;做好文档、开发流程、git。

## 2. 实地勘察结论(2026-07-18)

- 25 个天文件夹,共 **6627 张 JPG**;最大单日 835 张;JPG 约 22MB、ARW 约 42MB。
- `2026.06.08` 是**纯 ARW 天**(无 JPG)→ 扫描规则:只把「直接包含 ≥1 张 JPG 的文件夹」当作天文件夹,其余跳过。
- exFAT 上存在 **`._*` AppleDouble 垃圾文件**(如 `._2026.06.23`)→ 一切扫描必须过滤 `._` 与隐藏文件。
- 竖拍真实存在(某日 261 张 Orientation=8)。实测 `sips` 缩略图**保留 EXIF Orientation**,浏览器默认 `image-orientation: from-image` 能正确转正。
- `sips` 生成 480px 缩略图约 0.4s/张(USB 冷读)→ 画廊必须有**磁盘缓存 + 后台预热**,不能每次现算。
- 本机:Python 3.11(标准库即可)、sips/exiftool/node 齐全。

## 3. 方案对比

| 方案 | 说明 | 优点 | 缺点 | 结论 |
|---|---|---|---|---|
| **light(原 v1)纯单文件 HTML** | File System Access API(`showDirectoryPicker`),浏览器直接读写照片文件夹 | 零安装、单文件、双击即用;tag 直接写回照片文件夹 | 仅 Chrome/Edge;无缩略图缓存,画廊场景吃力;每次要重新授权一次目录 | **保留,作为轻量版打标器** |
| **v2 本地小服务器** | Python 标准库 HTTP server + 浏览器前端;sips 缩略图缓存;任意浏览器 | 文件夹自由浏览;缩略图快;画廊/导出/统计都好做;tag 原子写+日志 | 要跑一条命令启动 | **主力方案(打标器+画廊)** |
| Electron/Tauri 桌面应用 | 打包成 app | 体验最完整 | 构建链重、维护成本高,与"快速轻便"矛盾 | 否决 |

两版共用**同一套 tag 文件格式**与**同一套键位**,可互换使用、互不冲突。

## 4. Tag 数据结构

每个天文件夹里放一个 `phototags.json`(和照片同目录,**照片搬走时 tag 跟着走**):

```json
{
  "version": 1,
  "app": "phototag",
  "updated": "2026-07-18T23:50:00-07:00",
  "photos": {
    "DSC07117.JPG": { "status": "sooc", "type": "scenery", "quality": "best", "t": 1752900000 }
  }
}
```

取值(JSON 里用稳定的 ASCII id,界面显示中文):

| 维度 | id | 中文 | 语义 |
|---|---|---|---|
| status | `sooc` | 直出 | straight-out-of-camera,JPG 即成片(RAW 以后可清) |
| status | `edit` | 大修 | 保留 RAW,进 Lightroom 修;修完导出的新文件再单独打 tag |
| status | `trash` | 废片 | JPG+RAW 都该清理(由 sweep 工具**移动**到回收目录,绝不直接删) |
| type | `scenery` / `animal` / `portrait` / `insect` | 风景/动物/人像/昆虫 | 昆虫单列不归入动物(处理/分享策略不同) |
| quality | `best` / `normal` | 绝美/普通 | 绝美与是否直出无关 |

规则:

- 任意维度可缺省 = 未打 tag;三个维度全空时整条记录删除(文件里只存有 tag 的照片)。
- `t` 为最后修改的 Unix 秒,便于排查。
- 未来扩展:可加 `x: []` 自由标签数组(向前兼容:读取端忽略未知字段,写入端保留未知字段)。

### Robust 设计

- **原子写**:先写同目录临时文件,再 `os.replace`(light 版用 File System API 的 swap-file 提交,同样原子)。
- **写前备份**:覆盖前把旧文件复制为 `phototags.json.bak`。
- **损坏自愈**:JSON 解析失败时,把坏文件改名为 `phototags.json.corrupt.<ts>` 留证,从 `.bak` 恢复,再不行从空开始——绝不让一张照片的 tag 操作被坏文件卡死。
- **全局流水日志**(仅 v2):每次 tag 变更追加一行到 `~/Library/Application Support/phototag/journal.jsonl`,灾难时可整体重放恢复。
- **light 版额外镜像**:localStorage 里按天备份一份,JSON 丢失时可恢复。

## 5. 键位设计(两版一致)

原则:**左手一排 = 一个维度,列位置 = 选项**;右手只管方向键/空格。第一遍打「状态」在主行(ASD),手感最顺。同键再按 = 取消该 tag(回到未打状态)。

```
 类型   [Q] 风景   [W] 动物   [E] 人像   [R] 昆虫
 状态   [A] 直出   [S] 大修   [D] 废片      ← 第一遍主力行,D=Delete 好记
 质量   [Z] 绝美   [X] 普通
```

| 键 | 功能 |
|---|---|
| `Space` / `→` / `J` | 下一张(缩放时 `→` 改为平移,`Space`/`J` 仍翻页) |
| `←` / `K` | 上一张(缩放时 `←` 平移) |
| `A/S/D` `Q/W/E/R` `Z/X` | 打 tag(再按取消) |
| `U` 或 `⌘Z` | 撤销(自动跳回那张照片);`Shift+U` / `⇧⌘Z` 重做 |
| `⌫ Backspace` | 清空当前照片全部 tag |
| `F` | 切换「打完状态自动下一张」(默认开;第一遍一键一张) |
| `N` | 跳到下一张**未打状态**的照片 |
| `.` | 100% ↔ 适应屏幕;**触控板捏合 / 鼠标滚轮**可任意倍率(锚定指针位置,拦截浏览器全局缩放;靠 ±120 步进特征区分滚轮与双指滚动);放大时双指滚动/拖拽/方向键平移 |
| `Tab` | 缩略图条 / 照片列表 |
| `G` | 整面总览网格(v2 缩略图 / light 版文件名卡片):方向键选择、`Enter`/点击跳转、`N` 选下一张未打的、`Esc`/`G` 关闭 |
| `[` / `]` | 上一天 / 下一天 |
| `H` / `?` | 键位帮助 |
| `O` | 选择/切换照片根目录 |

自动前进只在**设置**状态时触发(取消不触发),延迟 ~120ms 让 tag 徽章闪一下可见;到最后一张停住。

## 6. 架构

### light `light/tagger.html`(原 v1)

单文件,无依赖。`showDirectoryPicker({mode:'readwrite'})` 选根目录 → 递归找天文件夹 → 全分辨率 `<img>`(objectURL,前后 ±2 张预取,LRU 释放)→ tag 变更 400ms 防抖写回该天的 `phototags.json`。目录句柄存 IndexedDB,下次一键续用。要求 Chrome/Edge。

### v2 `v2_server/`

```
serve.py            # 纯标准库;127.0.0.1:8787;线程池 HTTP
web/tagger.html     # 打标器(与 light 版同键位,多缩略图条)
web/gallery.html    # 画廊:时间线 + 筛选 + 灯箱 + 导出
```

HTTP API(全部 JSON,仅本机):

| 端点 | 作用 |
|---|---|
| `GET /api/config` | 启动参数(默认根目录等) |
| `GET /api/roots` | 可选根:`/Volumes/*`、~/Pictures 等 |
| `GET /api/browse?path=` | 文件夹浏览(子目录 + JPG 数) |
| `GET /api/scan?root=` | 找出所有天文件夹 + 每天 tag 统计 |
| `GET /api/photos?dir=` | 某天照片列表 + 该天全部 tag |
| `GET /img?path=` | 原图(ETag 缓存) |
| `GET /thumb?path=&w=240\|480\|960` | 缩略图(sips,磁盘缓存 `~/Library/Caches/phototag/`) |
| `POST /api/tag` | 单张 tag 写入(原子) |
| `POST /api/tags` | 批量写入(页面关闭时 sendBeacon 兜底) |
| `POST /api/prewarm` | 后台预热某天缩略图 |
| `POST /api/export` + `GET /api/export_status` | 异步导出选中照片到成片目录(复制+skip 已存在+tag 随行) |

安全边界:只绑 127.0.0.1;所有路径 realpath 后必须落在 `/Volumes` 或 `$HOME` 之下;只回照片扩展名文件。

### 画廊(gallery.html)

- 启动:scan → 并行拉全部天的照片名+tag(6627 条纯 JSON,毫秒级)→ 时间线按天分节。
- 缩略图懒加载(IntersectionObserver + `loading=lazy`),滚到哪天预热哪天。
- 筛选:三个维度多选 chips(含「未定」),维度内 OR、维度间 AND;日期范围;文件名搜索;实时计数。
- 灯箱:原图查看,同一套翻页/缩放键;**打 tag 键在灯箱里同样生效**(成片库的二次整理就在这做)。
- 导出:当前筛选结果 → 目标目录(默认 `~/Pictures/成片`),按天建子文件夹,复制 + 跳过同尺寸已存在 + `phototags.json` 子集随行;异步任务 + 进度条。

### 工具脚本(tools/)

- `sweep_trash.py`:把 status=trash 的 JPG+ARW(含 `._` 伴生垃圾)**移动**到 `<root>/_trash_bin/<天>/`,tag 记录随迁。**默认 dry-run**,加 `--apply` 才动手;全程零删除,确认无误后由人手清空回收目录。
- `collect_picks.py`:按 tag 条件把照片**复制**汇入成片目录(画廊导出的命令行版)。

## 7. 成片工作流(建议)

1. 第一遍(打标器,自动前进开):每张一键定状态 A/S/D,顺手 Z 标绝美。
2. `sweep_trash.py` dry-run 看清单 → `--apply` 把废片挪进 `_trash_bin`(想反悔随时挪回来)。
3. 大修片进 Lightroom;导出的成品放进成片目录(按天子文件夹)。
4. 直出成片用画廊「导出」或 `collect_picks.py` 复制进成片目录;成片目录本身也能被打标器/画廊打开,继续打类型/质量 tag。
5. 平时浏览用画廊指向成片目录;按时间/tag 随意切组、导出分享。

## 8. 已知限制与路线图

- light 版仅 Chrome/Edge(File System Access API);Safari 请用 v2。
- 画廊首次浏览某天要现做缩略图(0.4s/张,后台并行预热),之后走缓存。
- 缩略图缓存无自动清理(key 含 mtime,不会脏,只会占空间;`~/Library/Caches` 可随时整体删)。
- 未做:多选(非整组)导出、星级评分、EXIF 面板、RAW 直出对比、成片目录去重、备份策略(用户明确说还没想好,留待后续)。
- 删除永远不自动:sweep 只移动;真正 rm 由人来。
