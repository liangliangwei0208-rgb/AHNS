AHNS 所有 A 股假期节后海外基金补更新修复验收报告

验收日期：2026-10-09；环境：主机 G:\AHNS，F:\anaconda\envs\py310\python.exe。
本规则是项目的基金模型估算观察口径，不代表所有基金公司的正式净值披露规则。

1. **未出图根因**：已复现总入口的任务筛选错误。旧逻辑在 10 月 9 日 19:00 命中盘前窗口后，GitHub、Service 均过滤 `sum_holidays.py`；21:30 非实时窗口会选中。Service 原有特例仅覆盖首个复市日。修改前首先独立执行 `sum_holidays.py --today 2026-10-09` 即能读取真实缓存并出图，说明本机缺图问题不能归因于当前缓存不足。未取得当时服务器的实际运行日志，以上是确定可复现的代码路径原因。

2. **问题分类**：直接触发原因是任务调度；同时修复第二天日期窗口、基金与基准日期强耦合、记录质量选择和应出图失败仍成功退出。当前真实基金缓存不缺失。原邮件配置没有截断图片的问题，新增状态校验使出图失败及收集失败可见。

3. **修改文件与函数**：

   | 文件 | 对应内容 |
   | --- | --- |
   | `tools/fund_history_io.py` | `AShareHolidayContext`、`detect_a_share_holiday_context` 增加共享节后序号及第一复市日；`_filter_valid_close_records` 校验真实收盘；`_deduplicate_estimate_records` 复用写入方质量与北京时间语义；两个历史读取函数及两个累计函数防御性去重。 |
   | `sum_holidays.py` | `detect_post_holiday_context` 复用共享上下文；`_target_valuation_window` 修正第二天终点；`_load_overseas_daily_records` 独立读取基金/基准；`_publish_image` 校验并原子发布、相同像素不改写；`_update_data_diagnostics`、`run` 输出完整状态及失败原因。用户后续要求由 `_save_safe_image` 开启已有隐藏有效天数列参数，使上下表格对齐。 |
   | `git_main.py` | `WorkflowStep`、`resolve_workflow_steps`、`select_workflow_steps_for_time`、`main` 保留节后第 1/2 日必要任务；`_run_script` 校验结构化状态与实际图片，记录邮件收集状态。 |
   | `tools/configs/workflow_configs.py` | `sum_holidays.py` 加入共享 `post_holiday_update_group`，保留旧 `first_reopen_group` 兼容。 |
   | `tests/test_post_holiday_updates.py` | 新增 28 项回归，包含日期、质量、累计、出图、重复运行及邮件控制流。 |
   | `README.md`、`AGENTS.md` | 更新所有假期适用范围和补更新行为。 |

4. **第一天统计规则**：仅使用假期前最后一个 A 股交易日所对应的有效海外基金单日模型估算。10 月 8 日真实验收只读取 9 月 30 日，36 只基金；不累计假期收益。第一天验收图单独保存，不覆盖正式第二天图。

5. **第二天统计规则**：从假前最后 A 股交易日的下一自然日开始，截止第一复市交易日；纳入各基金自身真实有效的海外估值日期。交易日序号按共享交易日历计算，适用于所有含工作日休市的连续假期，支持跨周末、跨年及不同假期长度。普通周末不触发；第三个交易日起不选补更新任务。

6. **2026-10-09 目标窗口**：`2026-10-01～2026-10-08`。排除 `2026-09-30`、`2026-10-09`，回放还在去重前排除运行日期晚于回放日的记录。

7. **实际参与日期**：`2026-10-01、2026-10-02、2026-10-05、2026-10-06、2026-10-07、2026-10-08`。真实基金 217 条、37 只；36 只有全部六日，`018853` 仅有 10 月 8 日。该基金只累计已有的一日，图中起止日期均为 10 月 8 日；日志和验收 JSON 逐基金保留日期与缺口，不填零。真实基准 30 条，5 项各六日。

8. **累计方法**：沿用 `(product(1 + daily_return_pct / 100) - 1) * 100`。以独立 `math.prod` 对真实缓存的每只基金逐一核对，误差小于 `1e-10`。同基金、同估值日只计一次。正式记录优先，再按现有写入方质量、完整性选择，同质量按北京时间运行时间选择。

9. **基金与基准解耦**：已修复。基准全缺及缺单日的测试均保留正常基金收益并能生成 PNG；基准按现有“无有效数据”显示，不虚构收益。基金全缺明确失败，不用指数替代。合法部分收盘仍保留：市场聚合 `missing/pending/stale` 可能包含真实当日持仓贡献，不能仅凭状态删除；全无当日行情或补偿贡献的未收盘占位、全闭市、明确旧行情、实时观察、非有限收益均不进入正式补更新。

10. **实时窗口与两套入口**：GitHub/Service 在 10 月 8 日 19:00、10 月 9 日 19:00、10 月 9 日 21:30 六个组合中均恰好选中一次 `sum_holidays.py`。17:30–21:00 的盘前窗口保留补更新任务及原实时任务；普通交易日不扩大执行范围。Service 第二天没有启用整套假期流程，正式估算环境开关仍仅限原 Service 假期/首日范围。测试模拟总入口时间并拦截无关业务及 SMTP；未实际重跑所有联网市场分析脚本。

11. **图片生成**：正式文件 `output/safe_sum_holidays.png` 已生成、PNG 校验及视觉检查通过，尺寸仍为 `3062 × 4386`。后续按用户要求删除基准展示的“有效估值日”列，复用已有五列对齐机制，实际核对五列的 x 坐标和列宽全部一致。有效天数仍保留在数据层。配色、名称遮罩、基金排序、涨跌颜色、水印及备注保持原样。重复运行像素相同，正式文件修改时间不变；绘图失败保留旧图并返回非零，其他步骤继续。

12. **邮件候选与真实发送**：真实运行 `run_script → changed_images → unique_images` 后，正式图片进入候选，`collect_images=True` 生效；16/17 张候选测试均保留该附件，17 张只切换为仅附件。按原收件配置于北京时间 `2026-10-09 22:43:56` 发送一次验收邮件，SMTP 接受成功，包含正文内嵌和附件；不等同于已确认收件箱展示。用户随后提出的五列对齐版也已由总入口识别为更新候选，但没有重复发送验收邮件。

13. **回归和缓存保护**：最终全项目 `460` 项通过，其中新增补更新 `28` 项；包含已有假期、基准、邮件、Service 回归。独立审查指出的无行情占位和混合时区问题均先用失败测试复现，再修复；真实验收还防止了误删合法部分收盘记录。`git diff --check` 通过。正式基金缓存执行前后 SHA-256 完全一致：`620b725c6bcb007da58657966d47826acf77dd8f93f238af8702a7de3e219a71`。没有修改 `main.py`、基金模型、持仓权重、基准公式、技术指标或限购逻辑，没有批量删除文件。

14. **git diff --stat**（已跟踪文件；未暂存的新测试和本报告不会出现在此命令中）：

    ```text
     AGENTS.md                         |   6 +-
     README.md                         |   2 +
     git_main.py                       |  54 +++++-
     sum_holidays.py                   | 355 ++++++++++++++++++--------------------
     tools/configs/workflow_configs.py |   2 +
     tools/fund_history_io.py          | 158 +++++++++++++++--
     6 files changed, 363 insertions(+), 214 deletions(-)
    ```

    另新增 `tests/test_post_holiday_updates.py`（360 行）及本报告。改动保留在当前工作区，尚未提交或运行三边同步。

可复核命令：

```powershell
$env:PYTHONIOENCODING='utf-8'
& F:\anaconda\envs\py310\python.exe -B sum_holidays.py --today 2026-10-09
& F:\anaconda\envs\py310\python.exe -B -m unittest discover -s tests
git diff --check
git diff --stat
```

本机验收产物（运行输出，不作为源码提交依据）：

- `output/post_holiday_acceptance.json`：真实日期、逐基金日期及独立复利、缓存哈希、候选、SMTP 时间、五列对齐坐标。
- `output/post_holiday_final_cache_verification.log`：真实第二天出图和邮件收集日志。
- `output/post_holiday_repeat_acceptance.log`：相同图片不改写、不重复收集。
- `output/post_holiday_first_day_acceptance.log`、`output/post_holiday_acceptance_first_day.png`：第一天真实缓存验收。
- `output/post_holiday_layout_acceptance.log`：用户要求的五列对齐版更新及收集日志。
- `output/post_holiday_full_regression.log`：最终 460 项测试，45.256 秒，全部通过。
