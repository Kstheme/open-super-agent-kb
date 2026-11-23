# M1 公开资料小样本

本目录为 #13 冻结了两组真实版本对和一份原生 Word 候选。原文件由发布方提供，仓库保存 [清单](./manifest.json) 和 [生成步骤](./prepare.py)。PDF 与 Word 只提供上游下载地址，遵循发布者注明的复用限制。Markdown 受 CC BY 4.0 约束，由 Kubernetes 作者提供。本项目在受控变体里插入了一段标明用途的测试文字。

| 资料 | 已核验的原文件 |
|---|---|
| 美国联邦贸易委员会广告代言指南，16 CFR Part 255 | GPO 的 2022 年版和 2024 年版 PDF，分别为 9 页和 18 页，均可提取文本。2024 年版增加 §255.6，其他条款也有修订。该 Part PDF 的边缘页带有相邻法规，标注范围以 Part 255 的正文为准。[GovInfo 年版说明](https://www.govinfo.gov/help/cfr) · [版权说明](https://www.govinfo.gov/about) · [2024 年版正文](https://www.govinfo.gov/content/pkg/CFR-2024-title16-vol1/xhtml/CFR-2024-title16-vol1-part255.html) |
| Kubernetes 中文组件概览 | 原生 Markdown，取同一文件的 `4155ff4` 和 `53ef1f1` 两个 Git 修订，后者是前者的后继。后者修改了控制平面小节和若干定义，并改了插图路径。[修订记录](https://github.com/kubernetes/website/commit/53ef1f148fc41673d8181afadc0faaa803c9eff6) · [许可](https://github.com/kubernetes/website/blob/53ef1f148fc41673d8181afadc0faaa803c9eff6/LICENSE) |
| Microsoft Word 2016 可访问性检查清单 | Section508.gov 发布的原生 DOCX。文档正文有 17 个 Word 表格，首个表格有 3 行。样本无横向或纵向合并单元格，相关验收另选样本。[下载目录](https://www.section508.gov/create/documents/) · [复用政策](https://www.section508.gov/website-policies/#reuse-and-copyright) |

## 生成受控插入变体

从仓库根目录将输入下载到临时目录。脚本在保存原文件前核对 SHA-256。已有文件的哈希不符时会报错，保留该文件以便查明下载差异。PyMuPDF 仅用于准备 PDF 测试输入。验证使用 PyMuPDF 1.27.2.2。

```powershell
uv run --with 'pymupdf==1.27.2.2' python goldsets/m1-public-samples/prepare.py "$env:TEMP/openkb-m1-public"
```

| 对照 | 手工预标注与期望 |
|---|---|
| `cfr-255-2022.pdf` → `cfr-255-controlled-insertion.pdf` | 在原第 1 页和第 2 页之间插入一页，仅含一段测试附注。原第 1 页仍为第 1 页，原第 2–9 页对应新第 3–10 页。原页内正文和结构未编辑。测试附注单独识别为新增块。 |
| `k8s-components-before.md` → `k8s-controlled-insertion.md` | 在 Node 组件标题之前插入一段附注和两个换行。原第 104 行 Node 标题对应新第 106 行。下游原文逐字节保留。测试附注单独识别为新增块。 |

双方均以人工标出的原有未改动块为分母，要求 100% 一致且零错配。PDF 页号变化与 Markdown 行号变化用于验证位置漂移。

## 真实版本对手工标注

PDF 页号以**下载文件第 1 页**计。标注使用法律正文的条款标题，目录、页眉和跨页重复标题排除在这些预期对应关系外。

| 预期保持稳定的 PDF 锚点 | 2022 PDF 页 | 2024 PDF 页 |
|---|---:|---:|
| §255.0 正文标题 | 1 | 1 |
| §255.1 正文标题 | 3 | 5 |
| §255.2 正文标题 | 4 | 8 |
| §255.3 正文标题 | 6 | 12 |
| §255.4 正文标题 | 7 | 14 |
| §255.5 正文标题 | 7 | 14 |

- §255.6 是新增正文。2024 年版目录中同名条目不可算作这段正文。
- §255.3(a) 的条款位置对应，但正文发生真实改写，不计入未改动块。跨条款复用的示例编号和标题可能重复。单靠这些文字无法确认对应时，列为歧义。

Markdown 行号从原始 UTF-8 文本第 1 行计。下表的标题路径与正文在两次修订里均保持不变。

| 预期保持稳定的 Markdown 锚点 | 旧修订行 | 新修订行 |
|---|---:|---:|
| 导言正文 | 33 | 33 |
| Node 组件开头说明 | 106 | 105 |
| Node 组件中的 kubelet 定义项 | 108–109 | 107–108 |
| Node 组件中的 kube-proxy 定义项 | 111–112 | 110–111 |
| 架构灵活性首段 | 172 | 171 |

- 控制平面小节改了标题和开头说明。该处以章节锚点供人工查看变更，不计入稳定键一致率。相同锚点不能证明正文未变。
- Markdown 源文件含英文 HTML 注释，正文定位应回到中文可见内容，不能把注释中的同义段当作正文对应块。

## 候选门槛

建议 P1 确认：两对真实版本各要求全部固定标注锚点对齐，即 PDF 6/6、Markdown 5/5，且零静默错配。分开报两种格式的结果。样本少且仅包含人工确认未改动的锚点，漏一个必须可见。真实改写与歧义单列，避免把重写正文计入未改动分母。该数字是对这组固定样本的建议门槛，尚无导入解析器的实测得分。
