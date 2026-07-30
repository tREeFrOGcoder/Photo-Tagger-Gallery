# Lightroom Classic 配合指南(XMP 桥)

目标:把 phototag 里标为**大修**的 RAW,在 LrC 里**零复制**地聚成一组来修;修完导出的成品回到成片目录,tag 不用重打。

## 一次性流程

### 1. 生成 sidecar

```bash
cd ~/Desktop/code/phototag
python3 tools/export_xmp.py --root "/Volumes/My Book/Sony A7V"            # 先看清单(dry-run)
python3 tools/export_xmp.py --root "/Volumes/My Book/Sony A7V" --apply    # 真的写入
```

默认给所有 `status=edit`(大修)的照片找到同名 RAW,在旁边写 `DSCxxxxx.xmp`。写入的内容:

| phototag | LrC 里看到的 |
|---|---|
| 大修 edit | 色标 **黄** + 关键词 `phototag\|status\|edit` |
| 直出 sooc(若也导) | 色标 **绿** |
| 废片 trash(若也导) | 色标 **红** |
| 绝美 best | ★★★★★(5 星) |
| 类型 | 关键词 `phototag\|type\|animal` 等 |

**绝不覆盖已存在的 .xmp**(那可能是 LrC 存的修图参数),只跳过并报告。

### 2. LrC 导入(添加,不复制)

1. LrC → `文件 > 导入照片和视频`(⇧⌘I)。
2. 左侧「源」选到 `/Volumes/My Book/Sony A7V`(勾上「包含子文件夹」)。
3. **顶部中间选「添加」(Add)** —— 这是关键:文件留在原地,不移动不复制。
4. 右侧「文件处理」勾「不导入可能重复的照片」,点「导入」。
5. LrC 会自动读取每个 RAW 旁边的 .xmp:色标、星级、关键词直接就位。

### 3. 一键聚出要修的片子

- 图库(Grid)模式按 `\` 打开顶部筛选栏 → **属性 → 色标选黄** = 全部大修片。
- 或筛选栏 → 元数据/关键字 → `phototag > status > edit`。
- 建议存成智能收藏夹:`图库 > 新建智能收藏夹` → 条件「标签颜色 是 黄色」→ 命名"待大修"。以后每次 export_xmp + 导入后自动更新。

### 4. 修完导出,tag 自动延续

导出设置里:

- **文件命名 = 文件名**(保持 `DSC01234` 原名,别加后缀)—— tag 是按文件名对应的,这是唯一纪律。
- 导出位置选成片目录对应的天文件夹,如 `~/Pictures/成片/2026.07.12/`。

然后打开工具页 `/tools` → **Tag 同步**:成片目录填成片库、源库填原库 → 预览(会显示「将补 N 张」)→ 确认。它按「天+主名」把源库该文件名的 tag 补进成片目录(**只补空白,绝不覆盖**;大修片带回 `edit` 当出身记录,兼容 6300 的 ARW→导出 JPG)。之后成片库就是自包含的高质量集锦(纯 JPG + 每天一个 phototags.json),用画廊指着它按 tag 切组、导出发布即可。

## 增量使用

以后每打完一批"大修"tag:重跑 `export_xmp.py --apply`(已有 .xmp 的自动跳过)→ LrC 里对新文件再走一次「添加」导入(勾了去重,旧的不会重复)→ 智能收藏夹里自动出现新片子。

## 注意

- LrC 的「留用/排除旗标」不写 XMP、只存 catalog;所以反向同步(LrC → phototag)目前不做,谁标注以 phototag 为准。
- 若想让 LrC 把自己的星级/色标改动写回 .xmp:`目录设置 > 元数据 > 将更改自动写入 XMP`(可选,略拖慢 LrC)。
- 6300 纯 RAW 库(`/Volumes/ZTSSD/Sony 6300`)同样适用:先在 phototag 里筛(ARW 直读),再 export_xmp。
