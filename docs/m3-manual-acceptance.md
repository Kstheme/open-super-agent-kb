# M3 业务级手动验收

依据 [M3 规格 #28](https://github.com/Kstheme/open-super-agent-kb/issues/28)。用真实 Section 508 原件交付一份 M2 清单，再通过模拟修订验证 M3 如何复核和更新已交付的清单。业务验收和 M0 基线比较分别记结果。

## 业务实测记录

2026-11-24，以 Python 3.12 和两份 SHA-256 与 M1 清单一致的 Section 508 原件完成六项验收。各场景使用独立账本。需要语义判断的场景使用 `deepseek-flash`。

| 用例 | 实际结果 |
|---|---|
| 规则真变 | 只有「数据表格」进入实质变化提案。新规则写明跨页条件，证据可回到新版的重复表头原文。 |
| 只改措辞 | 「数据表格」进入复核并记录「无实质影响」，修改提案为零。 |
| 插入无关段落 | Word 原件的 4 处证据均正确对应新版，受影响条目和修改提案均为零。 |
| 删除依据 | 只有「数据表格」待人工核对，可查原声明和旧版第 9 表第 4 行。没有生成修改提案。 |
| 接受提案 | 生成 v2，只有「数据表格」更新并引用新版证据。v1 四项及旧证据仍可查回。 |
| 拒绝提案 | 当前版本为 v1，无新版本。拒绝决定和填写的理由均已落盘。 |

审查后的连续修订复测从已接受表格修改的 v2 开始。提交下一版 Word 资料，只改标题样式规则。系统只找出「标题结构」，接受后生成 v3，保留改动后的样式依据和仍有效的导航窗格依据，v1 与 v2 原样可查。

本机验收报告保存于 `%TEMP%/openkb-m3-acceptance-20261124-170302-400`。复核报告与接受、拒绝记录可用于回查以上结果。

## 基线实测记录

使用 `deepseek-flash`、M0 原始 gold set 和相同模型配置各运行 1 次。两份报告的语料哈希均为 `f6293e10884feb7d66dd649bcece01c76f9c39be329010224874edd124efbaae`，价格表版本均为 `v1`。

| 指标 | M3 账本复核 | `regenerate` |
|---|---:|---:|
| 宏平均召回率 | 100% | 100% |
| 宏平均误报率 | 0% | 15.28% |
| 总墙钟时间 | 36.10 秒 | 68.05 秒 |
| 模型耗时 | 36.05 秒 | 68.01 秒 |
| 输入 token | 5,060 | 7,022 |
| 输出 token | 4,505 | 10,948 |
| 估算费用 | 不可比 | 不可比 |

本次达到 M3 质量门槛。配置中缺少该模型的单价，费用比较和基于费用的收缩判断无法完成。原始 `ledger.json`、`regenerate.json` 及 `compare.txt` 保存在同一报告目录。

## 准备测试资料

**测什么**　确认每个用例从同一份已交付清单开始，避免上一轮确认改动影响结果。

1. 按 [M2 手动验收](./m2-manual-acceptance.md#1-准备两份真实原件)核对并导入 `508-applicability-checklist.docx` 和 `section508-word.docx`。生成一份已交付的清单，确认状态为 `done`，记录清单标识、两份资料的版本和「数据表格」的证据位置。
2. 在 Word 中复制 `section508-word.docx`，为每种修订另存独立副本。原件保持原样。各副本标记为验收用模拟修订，提交时使用原资料的同一逻辑身份。
3. 每个用例使用独立的测试账本。可重新执行 M2 准备步骤建立账本，接受和拒绝用例分别从同一基线开始。

**基线应看到**　四项分别为适用范围、标题结构、数据表格和图片说明。「数据表格」的原依据是 **Data Tables** 中的 “Tables repeat their header row.”，在原件第 9 表第 4 行。

用例 1–6 均可从 M2 验收的 PowerShell 会话继续，每个独立账本重新设置 `$db`、`$done` 和原 Word 版本 `$word`，并把 `$newFile` 设为当前用例的模拟修订副本路径。用已有的模型配置文件路径作为 `$config`。

```powershell
$artifact = $done.artifact.id
$review = Invoke-M2Json @('review','run',$artifact,$word,$newFile,'--database',$db,'--config',$config)
Invoke-M2Json @('review','show',$review.id,'--database',$db)
```

查询提案证据时，将 `$proof` 设为提案 `evidence` 中的一项。位置移动用例可从 `$review.rebased_evidence` 取对应项的 `new_evidence`。

```powershell
Invoke-M2Json @('ingest','evidence-show',$proof.id,'--database',$db)
```

## 1. 规则真变时，只提出对应修改

**测什么**　系统能从变更证据找到依赖它的条目，并只对实际改变适用范围的条目出提案。

**怎么测**　打开第一份副本，把 “Tables repeat their header row.” 改为 “Only data tables that span multiple pages must repeat their header row.”。提交为原资料的新版本，查看复核结果，再查询提案的新版证据。

**通过条件**　恰好一条修改提案，条目为「数据表格」。提案显示旧规则和新的跨页适用条件，证据回到新版原文。适用范围、标题结构、图片说明均无提案。待确认时，当前清单仍显示旧规则。

## 2. 只改措辞时，记录判断

**测什么**　系统会复核改过文字的依据，也能识别检查规则的含义保持一致。

**怎么测**　用独立副本，把表头句改为 “Each data table repeats its header row.”。提交新版，检查受影响集合和复核结果。

**通过条件**　「数据表格」进入复核，有「无实质影响」记录。修改提案数为零，当前清单版本不变。

## 3. 插入无关段落时，证据仍对得上

**测什么**　原文位置变化后，系统仍能把同一证据定位到新版对应块。

**怎么测**　在独立副本的正常正文位置插入一段与检查规则无关的文字，例如编辑说明。保留原有标题、表格和规则文本。提交新版，查询对齐结果及表头依据所在块。

**通过条件**　表头依据和 Word 原件中的其他依据正确对应新版原文，适用范围仍引用未更新的适用性原件，四项均无修改提案。验收时以段落、块序或偏移确有变化为准，表格行号可能保持原值。

## 4. 删除依据时，列为待人工核对

**测什么**　系统能暴露证据断点，避免在依据不足时擅自更改规则。

**怎么测**　在独立副本中删除重复表头依据所在行，检查新版其他位置也没有替代依据。提交新版，再查看复核结果和当前清单。

**通过条件**　只有「数据表格」标记为「待人工核对」，能查看原声明和失去的旧证据。无正式修改提案，无新清单版本。当前清单仍可读取。

## 5. 接受提案后，新旧版本都可查

**测什么**　人工接受可以把一条提案落实到新版清单，历史版本及旧证据可复查。

**怎么测**　在单独账本重做用例 1，接受「数据表格」提案。分别查看当前新版和原清单，查询两版的「数据表格」证据。

```powershell
$proposal = $review.proposals[0]
Invoke-M2Json @('review','accept',$review.id,$proposal.id,'--database',$db)
Invoke-M2Json @('review','artifact',$artifact,'--database',$db)
Invoke-M2Json @('review','artifact',$artifact,'--version','1','--database',$db)
```

**通过条件**　当前清单生成新版本，仅「数据表格」更新为跨页条件并引用新版原文。其余三项内容和证据保持原样。旧版仍可查回四项，旧表格项仍指向原件。

## 6. 拒绝提案后，保留当前版本和理由

**测什么**　人工可以拒绝一次有依据的修改，决定和理由可供事后复查。

**怎么测**　另用独立账本重做用例 1，拒绝提案并填写理由，例如「先按现行内部流程保留原检查项」。重新查询提案状态、当前清单和拒绝记录。

```powershell
$proposal = $review.proposals[0]
Invoke-M2Json @('review','reject',$review.id,$proposal.id,'--reason','先按现行内部流程保留原检查项','--database',$db)
Invoke-M2Json @('review','show',$review.id,'--database',$db)
Invoke-M2Json @('review','artifact',$artifact,'--database',$db)
```

**通过条件**　拒绝决定和填写的理由均已落盘，当前清单版本和原证据保持原样，没有因拒绝生成新版本。

## 7. 用 M0 基线验证复核质量

**测什么**　同样输入下，M3 是否覆盖至少同样多的实质变化，并把误报控制在基线以内。

**怎么测**　用 `goldsets/m0-compliance.json`、同一模型配置和相同运行次数，分别通过评测入口运行 M3 与 `regenerate`。保存两份 JSON 报告，再用 M0 的 `openkb eval compare` 比较。核对语料哈希和模型配置匹配，按 M0 规则纳入失败和暂停案例。

```powershell
$gold = './goldsets/m0-compliance.json'
$reports = Join-Path $env:TEMP ('openkb-m3-eval-' + (Get-Date -Format 'yyyyMMdd-HHmmss-fff'))
$null = New-Item -ItemType Directory -Path $reports
$ledgerReport = Join-Path $reports 'ledger.json'
$baselineReport = Join-Path $reports 'regenerate.json'
uv run openkb eval run --goldset $gold --system ledger --config $config --runs 1 --out $ledgerReport
if ($LASTEXITCODE -ne 0) { throw 'M3 评测运行失败' }
uv run openkb eval run --goldset $gold --system regenerate --config $config --runs 1 --out $baselineReport --reference-report $ledgerReport
if ($LASTEXITCODE -ne 0) { throw '对手评测运行失败' }
uv run openkb eval compare --report-a $ledgerReport --report-b $baselineReport
if ($LASTEXITCODE -ne 0) { throw '两个报告无法比较' }
```

**通过条件**　M3 的宏平均召回率不低于 `regenerate`，宏平均误报率不高于 `regenerate`。记录总墙钟、模型耗时、token 和估算费用，缺少可比费用时填「不可比」。另外记录 M0 建议性收缩判断，留给人作产品取舍。 [M0 指标及比较口径](./specs/0001-m0-eval-harness.md#报告与判定)

## 记录结果

每个用例填写「通过、失败、未执行」之一，并保留实际结果。业务用例保存新旧资料版本、受影响条目、提案或判断记录、证据位置、确认结果和当前清单版本。基线用例保存两个原始报告、比较结果和使用的配置文件名。
