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
