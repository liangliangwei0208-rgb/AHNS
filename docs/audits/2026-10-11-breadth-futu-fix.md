# 50D 90%门槛与富途收盘兜底修复验收

用户批准方案：全市场统一90%有效成分门槛；补齐富途盘后更新时间下的常规收盘价校验；新有效临时收盘恢复旧正式空值。保留300秒预算、10只历史额度预留、有效正式历史保护及现有算法。

前置核查：纳指10月9日88/101有效，旧结果为空；道指29/30、51.7241%已经是当日正式值。富途13只缺口返回快照，原15:55–16:00限制全部拒绝；12只有可衔接的10月8日日线。旧正式空值又会压住新有效临时结果。

共享接口：快照适配器保留原更新时间与常规/盘后价格字段 → 收盘校验 → 临时价格标识 → 结果合并 → 正式完成状态与图表显示状态分别诊断。

实施记录：
- 已添加覆盖90%边界、双市场完成状态、快照字段/时间/提前收市、合并顺序、88/101复现与正式替换的离线回归，先执行验证失败。
- 不修改基金、RSI及其它指标公式，不自动提交、同步或发邮件。

## 修改与回归

- `tools/configs/market_breadth_configs.py`：全市场共用门槛0.90，预算300秒及历史额度预留10保持原值。
- `tools/market_breadth.py`：历史/逐日成员分段/盘中计算默认参数复用共享门槛；`merge_document`按UTC时间处理旧正式空值和新有效临时值，保护有效正式历史；`save_results`记录90%口径。
- `tools/breadth_sources.py::FutuBreadth.quotes`：保留原始时间、常规价、盘后价和盘后涨跌额。
- `tools/breadth_engine.py::prepare_market_close_quotes`：同日、已收盘15分钟、非未来报价、前一交易日复权基准、常规价与盘后价关系校验；原常规收盘末五分钟及A股路径保留。
- `close_completion/refresh_market`：分别报告正式完成和可展示收盘的日期/状态，记录90%门槛、快照逐证券校验、原时间、历史额度阻断原因。
- 缓存说明、README和AGENTS同步现行口径；历史测试中显式传入95%的覆盖仍保留，确保参数兼容。
- 新增11项回归，首次运行6失败、2错误，均来自未修复行为或测试使用不存在的缓存常量（已改用现有root参数）。修复后相关163项全通过。
- 全项目：`python -B -m unittest discover -s tests -q`，471项通过，52.035秒。

## 真实缓存及富途验收（2026-10-11北京时间）

复制122只共用证券及两个市场成员/结果到隔离目录，原始副本与报告保存在本机忽略目录`output/breadth_close_acceptance/`。全流程先在副本通过，再按现有合并函数将已验证分片合回正式缓存。

| 验收 | 结果 |
|---|---|
| 纳指目标日 | 2026-10-09 |
| 原始缺口 | 88/101，87.13%，旧正式空值 |
| 富途快照 | 12/12常规价验证通过，保留盘后原时间 |
| 仅快照故障注入 | 从原始副本关闭普通日线源；真实富途恢复100/101、49.0%，状态snapshot_provisional；正式状态仍stale，可展示日期为10月9日 |
| 历史额度 | remaining=10、used=90；12只新证券历史请求按预留策略阻断，快照仍可补齐；未消耗新证券额度 |
| 正常全流程 | 普通正式日线随后补齐同12只，最终100/101、49.0%，状态official/complete |
| 剩余证券 | WBD缺10月7、8、9日；富途历史止于10月5日，被拒绝以保护原10月6日缓存；未补零 |
| 道指 | 保留10月9日已发布51.7241%（29/30）正式值；共用MSFT价格补齐后不追改已发布值 |
| 历史保护 | 纳指8条、道指361条已有有效正式收盘逐字段保持一致；其它市场结果未修改 |
| 图像 | 原正式绘图入口读取真实指数/VIX日线缓存重绘，均截止10月9日；纳指右侧49.0%，道指51.7%，已视觉检查 |

验收日志：`output/breadth_live_acceptance.log`、`output/breadth_snapshot_only_acceptance.log`、`output/breadth_chart_acceptance.log`。正常流程已更新纳指正式结果及13个行情分片；道指结果文件未修改。输出图片路径仍为`output/nasdaq_analysis.png`与`output/dow_jones_analysis.png`。

重复运行验收：禁用联网日线及富途，仅复用真实缓存再次协调两个市场；5976个行情与结果文件SHA256逐一相同，两个市场均为10月9日official/complete。旧Composite、三个A股市场及道指结果与HEAD内容一致、无Git差异（工作区与Git换行编码可不同）。

## 独立审查及修复

独立只读审查发现2项Important、无Critical或Minor：重算旧临时数据会用新运行时间推翻较新正式否决；同类空值按字符串排序会造成不同时区三方合并次序不一致。

两项均已按RED→GREEN修复，新增6项针对性测试：正式否决后的无新证据重算、协调器重复运行、新证据到达后恢复、旧响应不得刷新证据、临时值之间按证据排序、未来交易日分片更新不得刷新旧日证据，以及三文档所有排列/结合顺序（包含在同组测试中）。

- 复用既有结果字段`observed_at`记录有效行情窗口的证据时间，不增加缓存文件或采集系统；重算不能刷新该时间。
- `save_prices`遇到没有价格或质量变化的正式旧响应不改文件/证据时间；真实新快照与正式核验仍可更新。
- 结果文档、同类结果和正式/临时优先级均按UTC时间及确定性规则合并；有效正式值继续保护。
- 新增回归累计17项；相关169项全部通过。最终代码再次完成真实富途“仅快照”验收：100/101、49.0%、snapshot_provisional，历史额度仍10只。
- 审查未重复核查的外部行情/额度与视觉内容，已由本轮真实行情、副本对照及图片检查覆盖；其它适配器和图表原行为不改动，纳入全项目回归。

最终全项目回归：`python -B -m unittest discover -s tests -q`，477/477通过，48.423秒。并发请求测试只比较所需证券集合和次数，不依赖线程执行顺序。最终代码下再次核验5976个正式行情/结果文件重复运行SHA256不变，图片读取均为10月9日当前正式值。

`git diff --stat`（已跟踪文件，含真实验收缓存）：

```text
 AGENTS.md                                   | 14 +++++--
 README.md                                   | 18 +++++---
 cache/README.md                             | 10 ++++-
 cache/market_breadth/prices/US.LITE.json    |  2 +-
 cache/market_breadth/prices/US.LRCX.json    |  2 +-
 cache/market_breadth/prices/US.MAR.json     |  2 +-
 cache/market_breadth/prices/US.MCHP.json    |  2 +-
 cache/market_breadth/prices/US.MDLZ.json    |  2 +-
 cache/market_breadth/prices/US.MELI.json    |  2 +-
 cache/market_breadth/prices/US.META.json    |  2 +-
 cache/market_breadth/prices/US.MNST.json    |  2 +-
 cache/market_breadth/prices/US.MPWR.json    |  2 +-
 cache/market_breadth/prices/US.MRVL.json    |  2 +-
 cache/market_breadth/prices/US.MSFT.json    |  2 +-
 cache/market_breadth/prices/US.MSTR.json    |  2 +-
 cache/market_breadth/prices/US.WBD.json     |  2 +-
 cache/market_breadth/results/nasdaq100.json |  2 +-
 tests/test_breadth_membership.py            | 12 +++---
 tests/test_breadth_update.py                | 12 +++---
 tools/breadth_engine.py                     | 51 ++++++++++++++++++----
 tools/breadth_sources.py                    |  6 ++-
 tools/cache_metadata.py                     |  6 ++-
 tools/configs/market_breadth_configs.py     |  3 +-
 tools/market_breadth.py                     | 65 +++++++++++++++++++++++------
 24 files changed, 161 insertions(+), 64 deletions(-)
```

另新增测试 `tests/test_breadth_close_fallback.py` 和本报告，尚未暂存。未提交或推送。
