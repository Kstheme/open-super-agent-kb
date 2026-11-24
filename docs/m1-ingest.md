# M1 本地建库与块对齐

从仓库根目录同步项目依赖，然后通过本地命令操作 SQLite 数据库。为文件名不同的前后两版传入相同的资料身份。省略 `--document` 时，资料身份取自文件路径。

```powershell
uv sync
uv run openkb ingest add before.pdf --database kb.sqlite --document demo
uv run openkb ingest add after.pdf --database kb.sqlite --document demo
uv run openkb ingest show <版本ID> --database kb.sqlite
uv run openkb ingest align <旧版本ID> <新版本ID> --database kb.sqlite
uv run openkb ingest original <版本ID> --database kb.sqlite --out retained.pdf
```

`add` 返回版本 ID、原文 SHA-256、块数量和定位警告。同一个资料身份下的相同原文复用原版本。每个版本保存原文与独立的块 ID；`original` 可在导入源文件移动或删除后导出该版本的字节，且拒绝覆盖已有文件。

`align` 返回归一化规则版本、已匹配的前后对照、明确新增和移除的块，以及无法一对一确认的候选组。块稳定键依据标题路径、可识别的条款编号和归一化文本。块 ID 与页码、表格行或文本行单独保留，供具体版本的证据引用。跨资料、跨归一化规则版本的比较会报错。

Markdown 支持常见标题、段落、列表项、表格行和代码块，保留原文中的行号。PDF 从文本层取文字并保留页码、页面矩形，检测可明确识别的双栏与条款标题。PDF 的结构来自可抽取版面信息，复杂版式仍以原文人工核对。没有文字层的扫描 PDF 作为一个资料块导入，报告定位受限，且保留原始字节。

## 公开样本门槛

通过 [准备说明](../goldsets/m1-public-samples/README.md) 下载并生成受控样本后，可通过本地 CLI 的自动验收复跑。先将环境变量指向样本目录：

```powershell
$env:OPENKB_M1_SAMPLES = "$env:TEMP/openkb-m1-public"
uv run python -m unittest discover -s tests -p test_ingest_cli.py -v
```

| 用例 | 实测 |
|---|---|
| GovInfo PDF 受控插入 | 69/69 原有块保持稳定键，位置按预期移动，1 块新增，零错配 |
| Kubernetes Markdown 受控插入 | 27/27 原有块保持稳定键，行号按预期移动，1 块新增，零错配 |
| GovInfo 真实年版 | 6/6 人工标注的正文条款标题对应到正确 PDF 页 |
| Kubernetes 真实修订 | 5/5 人工标注的正文锚点对应到正确 Markdown 行 |

真实版本对中经人工标注的锚点是计分范围。报告另列新增、移除和候选歧义。无法确认一对一关系的重复文本不会被自动配对。

## Word 资料

将原生 `.docx` 传给同一个 `ingest add` 命令。划块保留标题路径，按正文中的物理段落计数，空段落仍占原文位置。表格中的每个含文字的物理行是一个块。`table`、`row` 及网格列号从 1 开始。

在表格行的 `locator.table_cells` 中，`column_span` 记录横向跨度，`vertical_start_row` 标记纵向合并的起始行。合并区域的文字只保留在起始单元格。续行保留空文本槽和起始行位置，便于查阅原文。正文没有可提取文字时，导入记录会提示定位受限。

使用同一公开样本目录可验收 Section508.gov 原始 Word 检查清单的标题、段落和 17 张表格。离线小样本覆盖合并单元格、重复导入和旧版原文取回。

```powershell
uv run python -m unittest discover -s tests -p test_ingest_docx.py -v
```

## 证据回点

使用建库时的 SQLite 数据库。从 `openkb ingest show` 的版本结果选一个块，使用 `openkb ingest evidence-add VERSION BLOCK --quote QUOTE --database PATH` 保存原文片段。片段必须精确匹配该版本的选定块。若同一片段在块内出现多次，用 `--quote-start` 指明零起始的字符偏移。保存相同片段和偏移会返回相同证据 ID。

用 `openkb ingest evidence-show EVIDENCE --database PATH` 返回原文件名、资料和版本 ID、原文件哈希、引文、块内字符范围及定位信息。PDF 使用页号及 PDF 块范围，DOCX 使用正文段落或表格行，Markdown 使用原始行号。旧版证据可结合返回的版本 ID 用 `openkb ingest original` 取回原始文件。

验收使用固定公开资料，以 PDF、DOCX、Markdown 各 4 条人工预标注引文验证来源、版本和位置。将 Markdown 原文从第 106 行移到第 112 行后，旧证据仍指向原版本第 106 行，导出的旧文件可核对原文。
