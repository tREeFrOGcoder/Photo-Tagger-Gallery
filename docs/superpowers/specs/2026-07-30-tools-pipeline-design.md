# phototag 工具页 & 成片流水线设计(spec)

日期:2026-07-30 · 分支:`feature/tools-page` · 稳定锚点 tag:`stable-pre-tools-20260730`

## 1. 目标

把四个批处理操作(成片收集 / XMP 导出 / Tag 同步 / 废片清扫)集成进一个独立的
网页工具页 `/tools`,每个都走"填路径 → 预览(dry-run)→ 确认执行 → 进度"的统一节奏;
同时把三个页面(打标 / 画廊 / 工具)由同一个后台 server 提供,并加一个纯点击的小切换器。
**对现有 tagger 内部零功能改动**(只在角落加一个可单独回退的切换器)。

## 2. 心智模型(两个库 + 一次外部绕道 + 三种去向)

```
  源库 /Volumes/My Book/Sony A7V        [ LrC 外部修图 ]        成片库 ~/Pictures/成片
  JPG+ARW,每天一个 phototags.json                              纯 JPG,每天一个 phototags.json
        │                                                                ▲
  ①废片 └─► sweep(移到 _trash_bin/<天>,只移不删,可捞回)                    │
  ②直出 └───────────── collect(复制 JPG,tag 随行)──────────────────────────┤
  ③大修 └─► xmp(写 .xmp)─► LrC 认黄标→修→导出原名 JPG ──────────────────────┤
                                                       (新 JPG 无 tag)      │
                                             sync(按 天+文件名 回源库补空白)──┘
```

status 三值 → 三种命运:trash 不进成片(挪进回收);sooc 相机 JPG 即成品,复制进成片;
edit 走 LrC 绕道,成品回流成片再补 tag。四个工具 = 图上四条箭头,没有第五件事。

## 3. 四个操作的精确契约

统一为纯函数 `*_plan()`(只读,产出清单)+ `*_apply(progress)`(执行,带进度回调)。
`apply` 内部**重新计算 plan 再执行**,不信任陈旧清单;每个操作自带的幂等/防覆盖守卫
才是真正的安全保证,预览只是给人看的。

### 3.1 collect(成片收集)
- 入参:`root`(源库)、`dest`(成片目录,默认 `~/Pictures/成片`)、`where`(默认 `status=sooc`)。
- plan:遍历 root 各天;命中 = 该天 `media_names` 中 tag 满足 where 的文件;报告每天张数+体积、合计。
- apply:逐张 `copy2` 到 `dest/<天>/`,**同名同大小跳过**(幂等);源库该文件的 tag 条目并入 `dest/<天>/phototags.json`。
- 文件选择沿用现状:A7V 天 = JPG;纯 RAW 天(6300)= 孤 ARW。**大修的相机 JPG 不会被收**(where=sooc 天然排除),避免与 LrC 成品冲突。

### 3.2 xmp(XMP 导出)
- 入参:`root`、`where`(默认 `status=edit`)。
- plan:遍历;每条命中 tag 用 `find_raw` 找同名 RAW,分三类:待写 / 已有 .xmp 跳过 / 找不到 RAW;报告每天 sidecar 数 + 两类合计。
- apply:给"待写"集合写 `<RAW 同名>.xmp`(色标 Label + 绝美 5 星 + `phototag|维度|值` 层级关键词)。**绝不覆盖已存在 .xmp**。

### 3.3 sync(Tag 同步)—— 新增
- 入参:`dest`(成片目录)、`source_root`(源库,默认当前 root)。
- 唯一职责:认领 LrC 导出到成片、尚无 tag 的成品。
- 匹配:`dest/<天>` ↔ `source_root/<同名天>`,天内按**文件主名(stem)**配对(**不做全库同名匹配**,避免 DSC 跨天重号张冠李戴);用 stem 而非全名,才能兼容 6300"源库 `DSC1234.ARW` 的 tag → LrC 导出 `DSC1234.JPG`"这种扩展名不同的回流。
- plan:遍历 `dest/<天>` 磁盘上的文件,分三类:
  - **将补**:该文件在 `dest/<天>/phototags.json` 里**无条目**,且源库同天同名有非空 tag。
  - **已有 tag 跳过**:dest 里已有条目(直出复制进来的、或你第二遍改过的)——**只跳过,绝不比对、绝不改写**。
  - **源库查无**:dest 无条目但源库也找不到同名 tag。
- apply:仅把"将补"写入 `dest/<天>/phototags.json`(合并,只新增空白项)。**不存在覆盖这条代码路径**。

### 3.4 sweep(废片清扫)
- 入参:`root`、`bin`(默认 `<root>/_trash_bin`)。
- plan:遍历;每天 `status=trash` 的照片,`companions()` 收本体 + 同名 JPG/RAW 兄弟 + 各自 `._` 垃圾;报告每天废片数、文件数、体积、目标目录。
- apply:`shutil.move` 到 `bin/<天>/`(`uniq_dest` 防撞名),tag 条目从源天 `phototags.json` 迁到 bin 天的;**绝不删除**,可从 `_trash_bin` 挪回。
- 唯一会动源文件的操作 → 网页上多一道二次确认。

## 4. 共享核心重构(代码整洁,消除四重复制)

- 新建仓库根模块 `phototag_core.py`(仅标准库)。每个入口脚本把仓库根加入 `sys.path` 后 `import phototag_core`。
- 迁入:常量(`TAGFILE / RAW_EXTS / SKIP_DIR_NAMES / DIMS / VALID_TAGS / LABELS`)、
  名称助手(`is_photo_name / is_raw_name / is_media_name / media_names`)、
  tag IO(`load_tags / save_tags` 原子写+`.bak` / `clean_tags`)、
  查询(`parse_where / match`)、遍历(`day_dirs`)、
  以及四操作的 `*_plan / *_apply`。
- `tools/collect_picks.py`、`export_xmp.py`、`sweep_trash.py` 改为**薄壳**:解析 argv → 调 core 的 plan/apply → 打印。CLI 用法与输出保持不变。
- `serve.py` 也 import core;`start_export`/`run_export` 的复制循环抽成 core 的复制引擎,collect 与画廊「导出当前筛选」共用它(一个吃条件、一个吃显式清单)。**画廊 `/api/export` 对外行为不变**。
- 结果:每个操作逻辑只有一处,CLI 与网页跑同一段代码,不会两边漂移。

## 5. 服务器端点(复用现有异步任务机制)

- `POST /api/tool/plan`,body `{tool, params}` → 返回 plan JSON(只读,秒回)。
- `POST /api/tool/apply`,body `{tool, params}` → 起异步 job,返回 `job_id`;job 内跑 plan+执行,写进度。
- 进度沿用现有 `_jobs` + 轮询端点(把 `/api/export_status` 泛化为 `/api/job_status`,保留旧路径别名给画廊)。
- `tool ∈ {collect, xmp, sync, sweep}`;`params` 各带自己的路径/条件字段,服务端全部走 `safe_path`(仍锁定 `/Volumes` 与家目录)。

## 6. `/tools` 页面(`v2_server/web/tools.html`)

- 顶部:切换器(见 §7),当前高亮"工具"。
- 四张卡片,统一交互:**填路径 → 预览 → 确认执行 → 进度条 → 结果小结**。
  - 路径字段配「浏览…」按钮,复用画廊那套目录浏览弹窗;默认值来自 `/api/config`(root)与 `~/Pictures/成片`(dest)。
  - collect 卡:状态/类型/质量点选 chips(默认「直出」);xmp 卡:状态 chips(默认「大修」)。
  - [确认执行] 仅在一次成功预览后可点;预览是纯只读。
  - sweep 卡:[确认执行] 前弹二次确认「这会移动源库文件(可从 _trash_bin 捞回)」。
- 每张卡的预览渲染成"每天一行 + 合计 + 警告"的清单表。

## 7. 页面切换器(纯点击,不绑键盘)

- 一小段一致的片段注入 `tagger.html / gallery.html / tools.html`:三个文字按钮 `打标 · 画廊 · 工具`,当前页高亮。
- 固定在右上角,低调、只有按钮响应点击,**不注册任何键盘处理**(tagger 键位完全不受影响)。
- 跳转时把当前 `root` 带进 `?root=`,换页免重选库。
- 一个后台 `serve.py` 进程即提供三页,共用 `127.0.0.1:8787`(即"共用 url")。

## 8. 四个保证如何落地

- **安全**:除 sweep(只移不删、可捞回、二次确认)外,其余只复制/只写 sidecar,源库原图零改动;网页全部先 dry-run 预览、点确认才执行;所有路径经 `safe_path` 沙箱。
- **不冗余**:只有直出走 collect,大修相机 JPG 永不进成片;所有复制同名同大小跳过(幂等);xmp 绝不覆盖;sync 只补空白(成片二遍改的 tag 永远权威)。
- **性能**:四者只读 `phototags.json` + 文件操作,**不解码图像**,秒级;复制大 JPG 复用异步 job+进度;同盘移动/写 sidecar 瞬时。
- **整洁**:共享核心一处逻辑;`/tools` 一种交互 × 四工具;tagger 内部零改动。

## 9. 测试(扩展 `v2_server/test_serve.py`)

- core 单元:在临时 fixture 树上跑四组 `*_plan/*_apply`,断言计数、幂等重跑、防覆盖/防删语义。
- sync 专项:源库天(带 tag)+ 成片天(部分已 tag 模拟直出、部分无 tag 模拟 LrC 成品)→ sync 只补空白、跳过已有、正确报告"源库查无";天不匹配时不写。
- HTTP smoke:`GET /tools` 200;四工具各 `POST /api/tool/plan`;`POST /api/tool/apply` 起 job 并轮询至完成。
- 回归:现有 39 项(含 CLI 薄壳)全绿。

## 10. Git / 回滚

- 已打 tag `stable-pre-tools-20260730`(开发前的稳定 tagger/gallery)。
- 全部工作在 `feature/tools-page`,`main` 保持原样。
- 分粒度提交,便于单独回退:spec → core 抽取+CLI 薄壳(行为不变,测试绿)→ server 端点 → tools.html → 切换器 → 测试 → 文档。
- 回滚整批:`git checkout main`;回滚单点(如只退切换器):`git revert <该 commit>`。

## 11. 非目标(YAGNI)

- 不做 LrC→phototag 的反向同步(除按文件名补 tag 外);不做任何删除;不加新 tag 词表;
  切换器不加快捷键;sync **永不提供**覆盖模式。
