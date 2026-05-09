# AHNS

AHNS 是一个个人公开数据建模复盘项目，用于生成每日市场 RSI 图、海外/全球基金模型估算表、安全版公开发布图、海外基金节假日累计观察图，以及面向小白的说明类科普图。

> 本项目仅供个人学习记录，不构成任何投资建议；非实时净值，最终以基金公司公告和销售平台展示为准。

## 功能概览

- 生成纳斯达克、红利低波、上证指数 ETF 等市场 RSI 分析图。
- 基于公开披露持仓、指数/ETF 代理和行情数据，生成海外/全球基金模型估算观察表；国内基金估算业务线已停用。
- 生成 safe 系列公开展示图：不展示基金代码，基金名称脱敏，并保留模型观察限购信息。
- 支持运行前自检，快速检查 Python 环境、关键缓存、水印图片、邮箱配置、依赖和总入口配置。
- 支持按基金代码和估值日期打印完整估算拆解表，区分“股票自身涨跌幅”和“对基金贡献”。
- 自动识别海外基金节假日期间的累计观察场景。
- 在节后第 1 / 第 2 个 A 股交易日生成海外基金净值补更新观察图。
- 海外基准表支持配置化数据源，默认尽量使用国内更友好的新浪、东方财富和 AKShare 路径，减少对 Yahoo 的依赖。
- safe 系列公开图支持集中配置标题、字号、颜色、表头底色、表格行列间距、底部说明和水印样式。
- A 股、港股、美股持仓日收益按“涨跌幅列优先、复权/调整后价格其次、裸收盘价最后兜底”计算，降低除权、拆股日误算风险。
- A 股交易日历、市场日历和 RSI 行情均带缓存优先策略，尽量减少重复请求；限购缓存仍保持 7 天有效期。
- 每天生成海外基金限额科普图；每周日额外生成海外基金限额表。
- 支持 QQ 邮箱自动发送本次运行生成或更新的图片。
- 支持 GitHub Actions 定时运行、手动触发、缓存自动回推和失败图片 artifact。

## 目录结构

```text
.
├── git_main.py                  # 总控入口
├── check_project.py             # 运行前自检，只检查不修改
├── main.py                      # 主计算入口
├── fund_estimate_breakdown.py    # 基金估算完整拆解查询工具
├── safe_fund.py                 # safe 每日基金图
├── safe_holidays.py             # safe 节假日累计图
├── holidays.py                  # 详细版节假日累计图
├── sum_holidays.py              # 节后补更新观察图
├── stock_analysis.py            # 市场 RSI 图入口
├── kepu/                        # 科普图片脚本
├── tools/                       # 基金估算、缓存、绘图、邮件等模块
├── tools/configs/               # 常维护配置：基金池、代理、基准源、safe样式、映射、RSI、流程等
├── cache/                       # 运行缓存，会被提交并由 Actions 自动更新
└── output/                      # 运行输出图片，不提交
```

## 本地运行

推荐 Python 版本：3.10。

```powershell
& F:\anaconda\envs\py310\python.exe -m pip install -r requirements.txt
```

运行前自检，不联网、不出图、不发邮件：

```powershell
& F:\anaconda\envs\py310\python.exe .\check_project.py
```

完整预演，不发邮件：

```powershell
& F:\anaconda\envs\py310\python.exe .\git_main.py --no-send
```

正式运行并发送邮件：

```powershell
& F:\anaconda\envs\py310\python.exe .\git_main.py
```

临时指定收件人：

```powershell
& F:\anaconda\envs\py310\python.exe .\git_main.py --receiver someone@example.com
```

查看某只基金在指定估值日的完整估算拆解：

```powershell
& F:\anaconda\envs\py310\python.exe .\fund_estimate_breakdown.py
```

运行后按提示输入基金代码和估值日期。也可以直接传参：

```powershell
& F:\anaconda\envs\py310\python.exe .\fund_estimate_breakdown.py 022184
& F:\anaconda\envs\py310\python.exe .\fund_estimate_breakdown.py 022184 2026-05-06
& F:\anaconda\envs\py310\python.exe .\fund_estimate_breakdown.py 022184 --latest
& F:\anaconda\envs\py310\python.exe .\fund_estimate_breakdown.py 022184 2026-05-06 --save-txt
```

## 常用维护入口

- `tools/configs/workflow_configs.py`：维护 `git_main.py` 每天运行哪些脚本、运行顺序、失败后是否中断、图片是否进入邮件候选。
- `tools/configs/fund_universe_configs.py`：维护海外/全球基金池；新增基金代码优先改这里，基金代码请写 6 位字符串。
- `tools/configs/fund_proxy_configs.py`：维护代理型基金和海外有效披露持仓增强系数。
- `tools/configs/residual_benchmark_configs.py`：维护海外股票持仓型基金的补偿仓位基准；默认纳斯达克100，`007844` 当前使用 `XOP`。
- `tools/configs/market_benchmark_configs.py`：维护 safe 海外基金图底部基准表。这里决定展示哪些指数、ETF 或海外资产，以及使用新浪、AKShare、东方财富还是 Yahoo 路径。
- `tools/configs/safe_image_style_configs.py`：维护 safe 公开图样式。标题文字、标题和表格间距、表头底色、正文底色、表格线、行高、列宽、涨跌颜色、底部备注、水印文字和透明度都优先在这里改。
- `tools/configs/security_mappings.py`：维护美股 / 韩国证券映射。
- `tools/configs/rsi_configs.py`：维护 RSI 图标的列表。
- `tools/paths.py`：集中维护常用缓存和输出图片路径。

旧导入路径会尽量保留兼容，例如 `tools/fund_universe.py` 仍可导入基金池，但真实配置已移动到 `tools/configs/fund_universe_configs.py`。

## 海外基准表配置

海外基金 safe 图底部的基准表由 `tools/configs/market_benchmark_configs.py` 的 `MARKET_BENCHMARK_ITEMS` 控制。每一项都是一个字典，常用字段如下：

- `enabled`：是否启用。改成 `False` 后不会删除历史缓存，但新图会过滤该基准，也不会主动更新它。
- `label`：图片上显示的名称。
- `kind`：行情读取类型。
  - `us_index`：新浪美股指数，例如 `.NDX`、`.INX`、`.SOX`。
  - `us_security`：美股股票或 ETF，例如 `XOP`。
  - `foreign_futures`：新浪外盘期货 / 东方财富国际期货，例如 `XAU`、`GC00Y`。
  - `yahoo`：Yahoo Chart，例如 `^VIX`。
  - `vix_level`：VIX 恐慌指数点位，优先 CBOE 官方 CSV，失败后回退 FRED。
- `ticker`：主行情代码。
- `fallback_ticker`：备用行情代码；主源失败后才会尝试。
- `display_in_daily_fund`：是否显示在每日海外基金 safe 图底部。
- `display_in_holidays`：是否显示在节假日 / 节后观察图。
- `include_in_cumulative`：是否作为收益率参与区间复利累计；VIX 这类点位指标必须为 `False`。

当前默认配置：

| 名称 | kind | ticker | 默认数据源说明 |
| --- | --- | --- | --- |
| 纳斯达克100 | `us_index` | `.NDX` | 新浪美股指数 |
| 标普500 | `us_index` | `.INX` | 新浪美股指数 |
| 油气开采指数 | `us_security` | `XOP` | AKShare 美股 ETF 日线；XOP 是 ETF 代理，不是指数本体 |
| 费城半导体 | `us_index` | `.SOX` | 新浪美股指数 |
| 现货黄金 | `foreign_futures` | `XAU`，fallback `GC00Y` | 优先新浪外盘期货 XAU；失败后用东方财富国际期货 GC00Y |
| VIX恐慌指数 | `vix_level` | `VIX` | CBOE 官方历史 CSV 优先，FRED 兜底；每日图显示点位，不带 `%` |

注意：配置不会把所有失败基准自动兜到 Yahoo。只有 `kind="yahoo"` 的项目，或者代码里明确写了 Yahoo fallback 的证券路径，才会访问 Yahoo。VIX 当前不走 Yahoo，而是 CBOE/FRED CSV；它展示的是最新完整有效交易日收盘点位，不是涨跌幅。

基准记录会写入 `cache/fund_estimate_return_cache.json` 的 `benchmark_records`。收益率型基准写 `return_pct`；VIX 点位型指标写 `value_type="level"`、`value/display_value`，并保持 `return_pct=null`。如果某个基准失败，只影响该基准行，不会中断主流程，也不会影响基金主表生成。

## Safe 图样式配置

safe 公开图的样式集中在 `tools/configs/safe_image_style_configs.py`。这个文件只管“怎么画图”，不拉行情、不读缓存、不出图，适合后续日常微调。

常用配置项：

- `SAFE_TITLE_STYLE`：标题字号、颜色、粗细、标题和表格的间距。`cumulative_gap` 控制 `safe_holidays.png` / `safe_sum_holidays.png` 的标题到表格距离，数值越小越贴近。
- `SAFE_CANVAS_STYLE`：每日图导出外边距。最上方留白偏大时调 `daily_top_pad_inches`；底部留白偏大时调 `daily_bottom_pad_inches`；左右留白调 `daily_left_pad_inches` / `daily_right_pad_inches`。
- `SAFE_DAILY_TABLE_STYLE`：`safe_haiwai_fund.png` 和节后第 1 天单日观察图的表格样式，包括正文/表头字号、表头底色、表头文字色、正文底色、整图底色、网格线、行高、缩放。
- `SAFE_CUMULATIVE_TABLE_STYLE`：节假日累计图和节后第 2 天累计图的表格样式。
- `SAFE_RETURN_COLORS`：涨跌颜色。当前按国内习惯红涨绿跌，无法获取或无效数据为黑色。
- `SAFE_FOOTER_STYLE`：底部“个人模型……”合规提示和备注文字的颜色、字号、粗细。
- `SAFE_DAILY_COLUMN_WIDTHS`、`SAFE_CUMULATIVE_COLUMN_WIDTHS`、`SAFE_BENCHMARK_COLUMN_WIDTHS`：列宽配置。“列间距”主要靠这里调；每次建议小幅调整 `0.01` 到 `0.03`。
- `SAFE_WATERMARK_STYLE`：居中 `cache/mark.jpg` logo 水印和斜向“鱼师AHNS”文字水印。可改水印文字、字号、颜色、透明度、旋转角度、logo 透明度和大小比例。

修改后可用下面命令单独预览：

```powershell
& F:\anaconda\envs\py310\python.exe .\safe_fund.py
& F:\anaconda\envs\py310\python.exe .\safe_holidays.py
& F:\anaconda\envs\py310\python.exe .\sum_holidays.py --today 2026-05-07
```

如果只是想让标题和表格更近，优先改 `SAFE_TITLE_STYLE["cumulative_gap"]` 或每日图的 `daily_gap_ratio/daily_gap_min/daily_gap_max`。如果是图片边缘留白：顶部改 `SAFE_CANVAS_STYLE["daily_top_pad_inches"]`，底部改 `SAFE_CANVAS_STYLE["daily_bottom_pad_inches"]`。如果文字挤在一起，先调列宽，再考虑降低字号。

## 邮件配置

项目使用 QQ 邮箱 SMTP 发送图片邮件。公开仓库不保存真实授权码。

配置优先级：

1. 函数参数；
2. 环境变量；
3. 本地未跟踪文件 `tools/email_local_config.py`。

环境变量：

- `QQ_EMAIL_ACCOUNT`：发件 QQ 邮箱，必填。
- `QQ_EMAIL_AUTH_CODE`：QQ 邮箱 SMTP 授权码，必填。
- `QQ_EMAIL_RECEIVER`：收件邮箱，可选；缺失时默认发送给 `QQ_EMAIL_ACCOUNT`。

本地配置方式：

```powershell
Copy-Item .\tools\email_local_config.example.py .\tools\email_local_config.py
```

然后在 `tools/email_local_config.py` 中填入自己的邮箱和授权码。该文件已被 `.gitignore` 忽略，不应提交。

## GitHub Actions

workflow 文件：`.github/workflows/ahns-daily.yml`。

触发方式：

- 手动触发：`workflow_dispatch`
- 定时触发：
  - UTC `0 20 * * *`，北京时间 04:00
  - UTC `0 22 * * *`，北京时间 06:00
  - UTC `0 0 * * *`，北京时间 08:00

运行环境：

- `ubuntu-24.04`
- Python 3.10
- 安装 `requirements.txt`
- 安装中文字体包，保证图片中的中文正常显示

需要在 GitHub 仓库 Settings -> Secrets and variables -> Actions 中配置：

- `QQ_EMAIL_ACCOUNT`
- `QQ_EMAIL_AUTH_CODE`
- `QQ_EMAIL_RECEIVER` 可选

Actions 运行后如 `cache/` 或 `investment_quote_history.json` 发生变化，会自动提交回仓库，提交信息为：

```text
Update runtime cache [skip ci]
```

成功运行不会上传图片 artifact；失败时才上传 `output/*.png` 作为 debug artifact，保留 3 天，避免 Actions 存储持续膨胀。

## 输出图片

常见输出：

- `output/first_pic.png`
- `output/nasdaq_analysis.png`
- `output/nasdaq.png`
- `output/honglidibo_analysis.png`
- `output/honglidibo.png`
- `output/shangzheng_analysis.png`
- `output/shangzheng.png`
- `output/haiwai_fund.png`（详细版当前在主流程中暂不输出，旧文件可能仍存在）
- `output/safe_haiwai_fund.png`
- `output/safe_holidays.png`
- `output/haiwai_holidays.png`
- `output/sum_holidays.png`（详细版已停用，后续不再新生成/覆盖）
- `output/safe_sum_holidays.png`
- `output/kepu_sum_holidays.png`
- `output/kepu_xiane.png`
- `output/xiane.png`（海外基金限额表，仅北京时间周日生成）

Matplotlib 表格和 RSI 图默认使用 180 DPI，科普图使用 Pillow 固定像素并做 PNG 无损压缩。

旧的 `output/guonei*.png` 文件可能仍在本地目录中，但后续主流程不再生成或加入邮件。

## 计算与缓存说明

`cache/` 会提交到仓库，用于减少重复拉取行情和保留基金估算历史。

- 海外/全球基金使用统一 `valuation_anchor_date` 估值锚点；US/CN/HK/KR 都只能使用该锚点对应的完整日线。
- 每个市场先用交易日历判断开闭市，再校验行情接口返回的 `trade_date == valuation_anchor_date`。
- 市场交易日历在单次运行中会按 `(market, start_date, end_date)` 做内存缓存；同一估值日、同一市场不重复计算开闭市和收盘完成状态。
- A 股节假日判断优先读取 `cache/a_share_trade_calendar_cache.json`，缓存 7 天有效；过期才主动请求 AkShare，AkShare 失败时优先使用旧文件缓存，再退到本地行情 CSV 兜底。
- CN/HK 日线按“可信涨跌幅源优先早停、复权价其次、裸 close 最后兜底”执行，命中目标估值日后立即返回，不再无条件请求全部源。
- 个股收益已统一做除权/拆股防错：
  - A 股优先使用官方涨跌幅列；没有涨跌幅列时优先使用新浪 `qfq/hfq` 复权价；最后才用未复权 raw close。
  - 港股会同时尝试新浪 raw/qfq/hfq 和东方财富港股日线；优先涨跌幅列，其次 qfq/hfq，最后 raw close。
  - 美股保留新浪日线、东方财富和 Yahoo fallback 顺序；东方财富路径优先解析 kline 里的日涨跌幅字段，Yahoo fallback 优先用 `adjclose`，raw close 仅作兜底。
  - 如果 Yahoo fallback 也失败，只打印完整错误链，并把对应证券标记为 missing/stale；不会中断后续基金或整套流程。
  - 如果只剩 raw close 且单日绝对涨跌异常大，代码会继续尝试其他源；仍无法确认时宁愿标记为 missing/stale，也不写入明显可疑的大涨大跌。
  - 韩国当前 pykrx 已优先读取“涨跌率”列；指数、期货和黄金没有股票除权/拆股语义，仍用完整日线 close-to-close。
- RSI 行情优先使用本地 `cache/*_index_daily.csv`。如果缓存已经在当天检查过，或已经包含今日记录，会直接复用；国内 ETF 在历史缓存足够新时只补实时点，不重拉整段历史。
- 普通持仓型海外基金使用“有效持仓增强 + 配置基准补偿仓位”口径，`fund_estimate_breakdown.py` 可打印逐项明细。
- 默认补偿基准为纳斯达克100；单基金可在 `tools/configs/residual_benchmark_configs.py` 指定其他基准。`007844` 当前使用 `XOP` 作为美国油气开采方向代理，`XOP` 是 ETF 不是指数本身。
- `security_return_cache.json` 对锚点行情使用 `SECURITY:{market}:{ticker}:{valuation_anchor_date}` key，缓存 `traded/closed/pending/missing/stale` 状态。
- 旧 A 股裸收盘价来源 `ak_stock_zh_a_daily_sina_close_calc`、旧港股裸收盘价来源 `ak_stock_hk_daily_sina_close_calc` 不再视为新鲜缓存，命中后会自动刷新到更可靠口径。旧缓存文件不会被删除。
- `fund_estimate_return_cache.json` 只写海外/全球基金记录，key 为 `overseas:{fund_code}:{valuation_anchor_date}`。
- 基准表记录写在 `fund_estimate_return_cache.json` 的 `benchmark_records`；显示端会按 `market_benchmark_configs.py` 的 `enabled=True`、`display_in_holidays`、`include_in_cumulative` 过滤。VIX 只在每日海外基金图展示点位，不进入节假日累计图和区间复利。
- `a_share_trade_calendar_cache.json` 保存 A 股交易日历，字段包含 `fetched_at`、`source`、`trade_dates`。默认 7 天有效；这是节假日判断和节后补更新判断的重要降频缓存。
- `*_index_daily.csv` 是 RSI/指数行情 CSV 缓存。主流程会优先读缓存，只有缓存不满足当前运行需求时才联网刷新。
- 指数行情和基金估算历史保留 300 天。
- Actions 运行后会自动回推缓存变化。

## 估算拆解与排错

如果某只基金的估算结果看起来异常，先用拆解工具看缓存中的逐项贡献：

```powershell
& F:\anaconda\envs\py310\python.exe .\fund_estimate_breakdown.py
```

建议重点看这些字段：

- `行情交易日`：是否等于本次估值锚点。
- `状态`：`traded` 表示已使用完整日线，`pending` 表示市场收盘未确认或行情尚未更新，`missing/stale` 表示缺失或陈旧。
- `股票自身涨跌幅`：单只持仓自己的涨跌。
- `估算权重`：经过有效持仓增强后的模型权重。
- `对基金贡献`：这只持仓对基金估算收益率的贡献。
- `数据源`：用于判断口径。正常情况下，除权/拆股敏感的股票应优先看到 `pct`、`qfq`、`hfq`、`adjclose` 等来源，而不是裸 close 计算来源。

常见情况：

- 刚收盘或海外市场尚未完整收盘时，美股可能是 `pending`，贡献暂时为 0，后续重新运行会刷新。
- 如果 `fund_estimate_breakdown.py` 已显示某个持仓修复为正确涨跌幅，但基金合计仍是旧值，需要先运行 `main.py` 或 `git_main.py --no-send` 重新写入基金缓存。
- VIX 在每日图中显示的是点位，不是涨跌幅；如果节假日累计图里出现 VIX，先确认 `include_in_cumulative=False`、`display_in_holidays=False`，并重新运行对应出图脚本。

## 后续优化方向

当前比较值得继续优化的地方：

- 增加行情请求统计日志：记录每次运行各数据源的请求次数、缓存命中次数、失败原因和耗时，方便判断哪些接口最慢、最不稳定。
- 增加一个只读数据源健康检查脚本：集中探测新浪、东方财富、AkShare、CBOE/FRED、Yahoo fallback 是否可用，不写基金缓存，便于 Actions 或本地运行前快速判断网络状态。
- 把缓存有效期集中配置化：例如证券日缓存、指数缓存、A 股交易日历缓存、限购缓存等都放到一个配置入口；限购缓存仍应保持严格 7 天有效期。
- 对同一轮基金估算中的证券做批量汇总报告：虽然锚点缓存和运行期缓存已经减少重复请求，但仍可在日志里先列出本轮唯一证券、命中缓存数量、实际联网数量和失败证券，排查会更直观。
- 补强美股特殊代码和基金持仓映射：石油、能源、ADR、改名或退市证券更容易出现行情源滞后，后续可把常见问题 ticker 写入映射或替代代理配置。
- 给 safe 图增加自动视觉回归检查：对输出图片做基础尺寸、非空、水印存在、表格行数和 VIX/累计过滤检查，避免样式配置改动后才在发布时发现异常。

## 验证命令

全项目编译：

```powershell
$files = @('.\git_main.py','.\check_project.py','.\main.py','.\fund_estimate_breakdown.py','.\safe_fund.py','.\safe_holidays.py','.\holidays.py','.\sum_holidays.py','.\stock_analysis.py','.\kepu\first_pic.py','.\kepu\kepu_sum_holidays.py','.\kepu\kepu_xiane.py') + (Get-ChildItem .\tools -File -Filter *.py | ForEach-Object { $_.FullName }) + (Get-ChildItem .\tools\configs -File -Filter *.py | ForEach-Object { $_.FullName }); & F:\anaconda\envs\py310\python.exe -m py_compile @files
```

总入口预演：

```powershell
& F:\anaconda\envs\py310\python.exe .\git_main.py --no-send
```

单独检查 safe 系列图片：

```powershell
& F:\anaconda\envs\py310\python.exe .\safe_fund.py
& F:\anaconda\envs\py310\python.exe .\safe_holidays.py
& F:\anaconda\envs\py310\python.exe .\sum_holidays.py --today 2026-05-07
```

检查估算拆解：

```powershell
& F:\anaconda\envs\py310\python.exe .\fund_estimate_breakdown.py 017731 --latest
```

抽样检查行情口径和缓存降频：

```powershell
@'
from tools.fund_history_io import load_a_share_trade_dates
from tools.get_top10_holdings import fetch_cn_security_return_pct_daily_with_date, fetch_hk_return_pct_akshare_daily_with_date
from tools.rsi_data import get_index_akshare

trade_dates, source = load_a_share_trade_dates(use_akshare=True)
print("A股交易日历", len(trade_dates), source, "2026-05-08" in trade_dates)
print("寒武纪 688256", fetch_cn_security_return_pct_daily_with_date("688256", end_date="2026-05-08"))
print("腾讯控股 00700", fetch_hk_return_pct_akshare_daily_with_date("00700", end_date="2026-05-08"))
df = get_index_akshare(symbol="512890", days=30, cache_dir="cache", use_cache=True, include_realtime=True)
print("RSI缓存样本", df.tail(1).to_string(index=False))
'@ | & F:\anaconda\envs\py310\python.exe -
```

## 免责声明

本项目所有内容均为个人公开数据建模复盘和学习记录。模型估算不等于基金净值公告，不构成任何投资建议、收益承诺或交易依据。基金净值、申购规则、限额信息和公告日期均以基金公司公告及销售平台展示为准。
