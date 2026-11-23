# M1 批量建库验收资料

[固定清单](manifest.json)包含 100 份不同的公开原始资料，合计约 34 MB。清单记录每份资料的发布方、文献地址、原文件地址、版次、复用条款和 SHA-256。仓库保存清单和校验工具，原文件由发布方提供。

| 格式 | 数量 | 来源及核验方式 |
|---|---:|---|
| PDF | 84 | GovInfo 发布的第 118 届美国国会公法公报，每项法律只取一个原始版本。下载后检查公报首页和每一页的文本层。[公报目录](https://www.govinfo.gov/help/plaw) · [复用说明](https://www.govinfo.gov/about) |
| DOCX | 16 | Section508.gov 原生 Word 文件，涵盖无障碍操作指南、检查清单、模板和表格工作表。下载后检查原生 Word 包及正文结构。[资料库](https://www.section508.gov/content-library/) · [复用说明](https://www.section508.gov/website-policies/#reuse-and-copyright) |

筛选时逐份下载并核验文件哈希，也对抽出的正文做空白归一化并核验文本哈希。PDF 按法律编号去重。DOCX 按资料身份筛选，查看正文区分同一资料的不同版次。清单的 `excluded_examples` 记录排除的重复入口、其他版本和 Markdown。复用说明中涉及第三方材料的限制依各自发布方政策处理。

## 准备一批可复现的资料

在仓库根目录执行。输出目录应专用于验收资料，原文件已经存在且哈希正确时可复用。哈希不一致会报错并保留原文件。

```powershell
$batch = Join-Path $env:TEMP 'openkb-m1-batch'
uv run --no-project python goldsets/m1-batch-samples/prepare.py $batch
uv run --no-project python goldsets/m1-batch-samples/prepare.py $batch --verify-only
```

第一条命令下载缺失的资料并校验全部 100 份，第二条只验证本地资料。下游的批量建库、中断和恢复验收使用同一目录。坏文件另用临时副本，不计入 100 份。
