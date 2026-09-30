# AHNS cache 目录说明

本文件由 `tools/cache_metadata.py` 生成，用于说明各缓存文件的用途、刷新策略和读取方。
不要在 CSV 缓存或 key-map JSON 顶层手工添加注释字段，可能破坏现有读取逻辑。

## 当前缓存文件

### `159561_index_daily.csv`
- 用途：RSI 和指数分析图使用的本地日线行情 CSV 缓存。
- 生成：tools/rsi_data.py 从 AkShare、Yahoo、腾讯或新浪等数据源拉取后写入。
- 读取：stock_analysis.py, tools/rsi_data.py
- 刷新：当天已检查或包含今日记录时优先复用；国内 ETF 可在历史缓存较新时只补实时点。
- 保留：写入前按调用参数保留最近 days 行，常见为 15、180 或 1200 行。
- 结构：CSV 表，常见列为 date、open、high、low、close、volume、amount。
- 说明位置：本 README
- 注意：不要在 CSV 文件头部添加说明行，避免 pandas.read_csv() 把说明当成数据。

### `510210_index_daily.csv`
- 用途：RSI 和指数分析图使用的本地日线行情 CSV 缓存。
- 生成：tools/rsi_data.py 从 AkShare、Yahoo、腾讯或新浪等数据源拉取后写入。
- 读取：stock_analysis.py, tools/rsi_data.py
- 刷新：当天已检查或包含今日记录时优先复用；国内 ETF 可在历史缓存较新时只补实时点。
- 保留：写入前按调用参数保留最近 days 行，常见为 15、180 或 1200 行。
- 结构：CSV 表，常见列为 date、open、high、low、close、volume、amount。
- 说明位置：本 README
- 注意：不要在 CSV 文件头部添加说明行，避免 pandas.read_csv() 把说明当成数据。

### `512890_index_daily.csv`
- 用途：RSI 和指数分析图使用的本地日线行情 CSV 缓存。
- 生成：tools/rsi_data.py 从 AkShare、Yahoo、腾讯或新浪等数据源拉取后写入。
- 读取：stock_analysis.py, tools/rsi_data.py
- 刷新：当天已检查或包含今日记录时优先复用；国内 ETF 可在历史缓存较新时只补实时点。
- 保留：写入前按调用参数保留最近 days 行，常见为 15、180 或 1200 行。
- 结构：CSV 表，常见列为 date、open、high、low、close、volume、amount。
- 说明位置：本 README
- 注意：不要在 CSV 文件头部添加说明行，避免 pandas.read_csv() 把说明当成数据。

### `a_share_mc_gdp.json`
- 用途：仅用于159943、560220走势图的沪深市价总值/中国名义GDP(TTM)估值背景。
- 生成：tools/a_share_valuation.py，stock_analysis整轮共享一次加载。
- 读取：tools/rsi_data.py
- 刷新：市值48小时，GDP96小时；失败24小时退避；宏观子进程总预算15秒。
- 保留：保留来源历史、固定初始化修订历史及后续观测；不批量删除文件。
- 结构：version/unit/sources/history/observations/historical_cutoff容器；金额亿元，比例不缩放。
- 说明位置：本 README
- 注意：上海市价总值+深圳市价总值，不含北交所；累计名义GDP转单季后，连续四季求TTM。
- 注意：初始化历史是historical revised series，统计期不是发布日期，不承诺无前视回测。
- 注意：后续新值或修订使用首次成功观测时间available_at；不回写历史基线或旧观测。
- 注意：来源异常继续用可信旧值，无值则N/A；不参与基金收益预估、benchmark或实时基金观察。
- 注意：strategy/gdp.py旧CSV只读校验迁入；不更新其文件，不相信旧派生ratio。


### `a_share_trade_calendar_cache.json`
- 用途：A 股交易日历文件缓存，用于判断普通交易日、周末和节假日累计窗口。
- 生成：tools/fund_history_io.py 从 AkShare 交易日历刷新后写入。
- 读取：safe_holidays.py, holidays.py, sum_holidays.py, kepu/kepu_sum_holidays.py
- 刷新：缓存新鲜期默认 7 天；过期后才尝试联网刷新，失败时允许用旧缓存兜底。
- 保留：整份交易日历覆盖写入，不按每日追加。
- 结构：顶层包含 fetched_at、source、trade_dates；trade_dates 是 YYYY-MM-DD 字符串列表。
- 说明位置：本文件内嵌 `_cache_info` + 本 README
- 注意：本文件适合内嵌 _cache_info，因为读取方只读取固定字段。
- 注意：trade_dates 通常覆盖多年历史和当年未来已公布交易日。

### `afterhours_quote_cache.json`
- 用途：盘后观察用的实时行情短缓存，避免同一早反复运行时反复请求重复持仓股和盘后基准。
- 生成：tools/premarket_estimator.py 在生成盘后观察图时写入可展示的实时涨跌幅或点位。
- 读取：afterhours_fund.py, tools/premarket_estimator.py
- 刷新：15 分钟内复用；过期后重新请求接口。失败结果不跨运行缓存。
- 保留：写入时删除超过 1 天的记录，并按 fetched_at_bj 只保留最新 500 条。
- 结构：顶层是 market:ticker -> 行情记录的映射，例如 US:NVDA、HK:00700、VIX_LEVEL:VIX。
- 说明位置：本 README
- 注意：只服务盘后观察，不写入也不替代正式基金估算缓存。
- 注意：不要在顶层内嵌 _cache_info，避免遍历逻辑把说明误认为行情记录。

### `dot_INX_index_daily.csv`
- 用途：RSI 和指数分析图使用的本地日线行情 CSV 缓存。
- 生成：tools/rsi_data.py 从 AkShare、Yahoo、腾讯或新浪等数据源拉取后写入。
- 读取：stock_analysis.py, tools/rsi_data.py
- 刷新：当天已检查或包含今日记录时优先复用；国内 ETF 可在历史缓存较新时只补实时点。
- 保留：写入前按调用参数保留最近 days 行，常见为 15、180 或 1200 行。
- 结构：CSV 表，常见列为 date、open、high、low、close、volume、amount。
- 说明位置：本 README
- 注意：不要在 CSV 文件头部添加说明行，避免 pandas.read_csv() 把说明当成数据。

### `dot_IXIC_index_daily.csv`
- 用途：RSI 和指数分析图使用的本地日线行情 CSV 缓存。
- 生成：tools/rsi_data.py 从 AkShare、Yahoo、腾讯或新浪等数据源拉取后写入。
- 读取：stock_analysis.py, tools/rsi_data.py
- 刷新：当天已检查或包含今日记录时优先复用；国内 ETF 可在历史缓存较新时只补实时点。
- 保留：写入前按调用参数保留最近 days 行，常见为 15、180 或 1200 行。
- 结构：CSV 表，常见列为 date、open、high、low、close、volume、amount。
- 说明位置：本 README
- 注意：不要在 CSV 文件头部添加说明行，避免 pandas.read_csv() 把说明当成数据。

### `dot_NDX_index_daily.csv`
- 用途：RSI 和指数分析图使用的本地日线行情 CSV 缓存。
- 生成：tools/rsi_data.py 从 AkShare、Yahoo、腾讯或新浪等数据源拉取后写入。
- 读取：stock_analysis.py, tools/rsi_data.py
- 刷新：当天已检查或包含今日记录时优先复用；国内 ETF 可在历史缓存较新时只补实时点。
- 保留：写入前按调用参数保留最近 days 行，常见为 15、180 或 1200 行。
- 结构：CSV 表，常见列为 date、open、high、low、close、volume、amount。
- 说明位置：本 README
- 注意：不要在 CSV 文件头部添加说明行，避免 pandas.read_csv() 把说明当成数据。

### `dot_SOX_index_daily.csv`
- 用途：RSI 和指数分析图使用的本地日线行情 CSV 缓存。
- 生成：tools/rsi_data.py 从 AkShare、Yahoo、腾讯或新浪等数据源拉取后写入。
- 读取：stock_analysis.py, tools/rsi_data.py
- 刷新：当天已检查或包含今日记录时优先复用；国内 ETF 可在历史缓存较新时只补实时点。
- 保留：写入前按调用参数保留最近 days 行，常见为 15、180 或 1200 行。
- 结构：CSV 表，常见列为 date、open、high、low、close、volume、amount。
- 说明位置：本 README
- 注意：不要在 CSV 文件头部添加说明行，避免 pandas.read_csv() 把说明当成数据。

### `fund_estimate_return_cache.json`
- 用途：海外/全球基金每日估算收益和海外基准结果缓存，供 safe 图、节假日累计图和拆解工具只读复用。
- 生成：tools/get_top10_holdings.py 在海外基金估算表生成后写入。
- 读取：safe_fund.py, safe_holidays.py, holidays.py, sum_holidays.py, fund_estimate_breakdown.py
- 刷新：同一基金或基准、同一 valuation_anchor_date 使用固定 key，按数据质量覆盖。
- 保留：records 与 benchmark_records 默认保留最近 300 天；国内历史记录会被裁剪。
- 结构：顶层包含 version、updated_at、records、benchmark_records；基金记录在 records，基准记录在 benchmark_records。
- 说明位置：本文件内嵌 `_cache_info` + 本 README
- 注意：只有本文件适合内嵌 _cache_info，因为真实缓存项不在顶层直接枚举。
- 注意：正式基金缓存只由完整日线主流程写入；盘前、盘中、盘后、富途夜盘实时观察入口不写本文件。
- 注意：VIX 这类点位记录使用 value/value_type/display_value，不参与累计收益。

### `fund_holding_change_batch_state.json`
- 用途：基金前十大持仓变化图的披露批次状态缓存，用于维护 latest/ 下 1_基金代码.png 的本轮编号和上一轮图片清理状态。
- 生成：fund_holding_change.py --auto 在自动检测到持仓变化并生成图片后写入。
- 读取：fund_holding_change.py, git_main.py, service_main.py
- 刷新：同一披露批次内基金首次生成图片时分配递增序号；新季度批次开始时把当前批次归档为上一轮。
- 保留：当前批次满基金池数量且首张图生成超过 3 天后，按缓存中记录的明确图片路径逐个清理上一轮图片。
- 结构：顶层包含 current、previous、last_cleaned_previous；current.funds 按基金代码记录 index、image、quarter_key、generated_at。
- 说明位置：本 README
- 注意：不要在顶层内嵌 _cache_info，避免遍历逻辑把说明误认为批次状态。
- 注意：本缓存只记录图片批次和编号，不保存完整持仓明细。

### `fund_holding_change_state.json`
- 用途：基金前十大持仓变化图的已处理状态缓存，用于判断持仓缓存是否发生新披露或内容变化。
- 生成：fund_holding_change.py --auto 在自动检测持仓变化后写入。
- 读取：fund_holding_change.py, git_main.py, service_main.py
- 刷新：首次运行只初始化当前持仓指纹；后续季度或真实披露字段指纹变化时生成持仓变化图并更新状态。
- 保留：每个 fund_code:topN 一个 key，指纹未变时不改写检查时间；基金池外且明确超过 365 天的手动 key 在下次写入时回收。
- 结构：顶层是 fund_code:topN -> 状态记录，包含 latest_quarter_key、fingerprint、last_checked_at、last_image。
- 说明位置：本 README
- 注意：不要在顶层内嵌 _cache_info，避免遍历逻辑把说明误认为基金状态。
- 注意：该文件只记录是否已经处理过持仓变化，不保存完整持仓明细。

### `fund_holdings_cache.json`
- 用途：基金最近披露前 N 大股票持仓缓存，用于估算海外/全球基金持仓贡献。
- 生成：tools/get_top10_holdings.py 在首次缺失或披露窗口低频试探时写入。
- 读取：tools/get_top10_holdings.py, fund_estimate_breakdown.py
- 刷新：非披露窗口直接复用；披露窗口内每只基金约 3 天最多试探一次。
- 保留：每个 fund_code:topN 一个 key，更新时覆盖同 key；基金池外且明确超过 365 天的手动 key 在下次写入时回收。
- 结构：顶层是 fund_code:topN -> 持仓记录的映射；data_json 内保存持仓表。
- 说明位置：本 README
- 注意：不要在顶层内嵌 _cache_info，避免遍历逻辑把说明误认为基金持仓。

### `fund_purchase_limit_cache.json`
- 用途：基金限购金额缓存，用于每日基金图展示模型观察限购信息。
- 生成：tools/get_top10_holdings.py 解析公开网页限购文本后写入。
- 读取：tools/get_top10_holdings.py, kepu/kepu_xiane.py
- 刷新：按上次成功刷新时间满 3 天更新；失败或未知不写入成功时间，保留旧有效值。
- 保留：每个基金代码一个 key，更新时覆盖同 key；基金池外且明确超过 365 天的手动 key 在下次写入时回收。
- 结构：顶层是 fund_code -> {fetched_at, value} 的映射。
- 说明位置：本 README
- 注意：不要在顶层内嵌 _cache_info，避免遍历逻辑把说明误认为限购记录。

### `fund_region_allocation_cache.json`
- 用途：晨星海外基金股票地区分布缓存，保存披露日期、父级区域和子区域权重。
- 生成：fund_region_allocation.py 从晨星公开基金页直连解析后写入。
- 读取：fund_region_allocation.py, git_main.py, service_main.py
- 刷新：默认 7 天检查一次；手动使用 --refresh 可强制直连刷新。
- 保留：按基金代码保留最近一次有效地区分布；请求失败时保留旧有效记录；基金池外且明确超过 365 天的手动 key 在下次写入时回收。
- 结构：顶层是基金代码 -> 地区记录的映射，记录 report_date、primary_regions、subregions、fingerprint 与抓取时间。
- 说明位置：本 README
- 注意：地区权重属于晨星股票地区分布，不包含基金现金、债券等资产。
- 注意：晨星请求使用 trust_env=False，不继承 HTTP/SOCKS 环境代理。

### `fund_region_allocation_state.json`
- 用途：晨星地区分布图片的已发送状态，用于判断哪些分页图片需要重新生成。
- 生成：fund_region_allocation.py --auto 在检测披露日期或地区权重变化后写入。
- 读取：fund_region_allocation.py, git_main.py, service_main.py
- 刷新：每次自动检测后更新；只记录数据指纹和分页图片状态。
- 保留：数据指纹未变时不改写检查时间；基金池外且明确超过 365 天的状态 key 在后续自动检测时回收。
- 结构：顶层包含 funds、pages、updated_at；pages 按稳定页码记录聚合指纹和图片路径。
- 说明位置：本 README
- 注意：同一基金无地区数据变化时不会重复生成或发送图片。

### `futu_night_return_cache.json`
- 用途：富途夜盘观察用的持仓股和基准涨跌幅结果缓存，避免 15 分钟内重复请求相同证券。
- 生成：tools/futu_night_observation.py 通过 tools/futu_night_quotes.py 写入已校验估值日的涨跌幅结果。
- 读取：futu_night_fund.py, tools/futu_night_observation.py, tools/futu_night_quotes.py
- 刷新：15 分钟内复用；过期、估值日不匹配、报价时间过旧或过新的记录必须重新请求。
- 保留：写入时删除超过 1 天的记录，并按 fetched_at_bj 只保留最新 500 条。
- 结构：顶层是 market:ticker:target_us_date -> 行情记录的映射，例如 US:NVDA:2026-05-14。
- 说明位置：本 README
- 注意：只服务富途夜盘观察，不写入也不替代正式基金估算缓存。
- 注意：缓存保存的是已计算涨跌幅结果，不保存全量 K 线或 CSV。
- 注意：不要在顶层内嵌 _cache_info，避免遍历逻辑把说明误认为行情记录。

### `intraday_quote_cache.json`
- 用途：盘中观察用的实时行情短缓存，避免同一晚反复运行时反复请求重复持仓股和盘中基准。
- 生成：tools/premarket_estimator.py 在生成盘中观察图时写入可展示的实时涨跌幅或点位。
- 读取：intraday_fund.py, tools/premarket_estimator.py
- 刷新：15 分钟内复用；过期后重新请求接口。失败结果不跨运行缓存。
- 保留：写入时删除超过 1 天的记录，并按 fetched_at_bj 只保留最新 500 条。
- 结构：顶层是 market:ticker -> 行情记录的映射，例如 US:NVDA、HK:00700、VIX_LEVEL:VIX。
- 说明位置：本 README
- 注意：只服务盘中观察，不写入也不替代正式基金估算缓存。
- 注意：不要在顶层内嵌 _cache_info，避免遍历逻辑把说明误认为行情记录。

### `mark.jpg`
- 用途：safe 公开图使用的居中 logo 水印素材。
- 生成：人工维护。
- 读取：tools/safe_display.py, safe_fund.py, safe_holidays.py, sum_holidays.py
- 刷新：需要更换水印素材时人工替换。
- 保留：固定资源文件，不由运行脚本裁剪。
- 结构：JPEG 图片。
- 说明位置：本 README
- 注意：不是行情缓存；保留在 cache/ 下是为了 GitHub Actions 和本地运行共用路径。

### `night_quote_cache.json`
- 用途：旧 HTTP/Yahoo 夜盘观察短缓存，当前已停用，仅保留文件说明以避免误删旧缓存。
- 生成：legacy：旧 tools/premarket_estimator.py 夜盘分支；当前代码不再写入。
- 读取：legacy only
- 刷新：不再刷新。富途夜盘使用 futu_night_return_cache.json。
- 保留：不由清理脚本主动删除；如需清理请人工确认后单文件处理。
- 结构：顶层是 market:ticker -> 行情记录的映射，例如 US:QQQ、HK:00700、KR:005930。
- 说明位置：本 README
- 注意：legacy 缓存不再有活跃生产者或读取方。
- 注意：保留 cache/night_quote_cache.json 文件本身，不自动删除 cache/ 下旧文件。
- 注意：不要在顶层内嵌 _cache_info，避免遍历逻辑把说明误认为行情记录。

### `premarket_quote_cache.json`
- 用途：盘前观察用的实时行情短缓存，避免同一晚重复运行时反复请求重复持仓股和盘前基准。
- 生成：tools/premarket_estimator.py 在生成盘前观察图时写入可展示的实时涨跌幅或点位。
- 读取：premarket_fund.py, tools/premarket_estimator.py
- 刷新：15 分钟内复用；过期后重新请求接口。失败结果不跨运行缓存。
- 保留：写入时删除超过 1 天的记录，并按 fetched_at_bj 只保留最新 500 条。
- 结构：顶层是 market:ticker -> 行情记录的映射，例如 US:NVDA、HK:00700、VIX_LEVEL:VIX。
- 说明位置：本 README
- 注意：只服务盘前观察，不写入也不替代正式基金估算缓存。
- 注意：不要在顶层内嵌 _cache_info，避免遍历逻辑把说明误认为行情记录。

### `security_return_cache.json`
- 用途：证券、指数和锚点行情收益缓存，降低重复行情请求并保护已确认完整交易日结果。
- 生成：tools/get_top10_holdings.py 在拉取 CN/HK/US/KR/指数/期货等行情后写入。
- 读取：tools/get_top10_holdings.py, fund_estimate_breakdown.py
- 刷新：traded/closed 稳定记录优先保留；pending/missing/stale 只短期复用后重试。
- 保留：小时桶 15 天，普通证券日线 30 天，指数和稳定锚点 300 天。
- 结构：顶层是缓存 key -> 行情记录的映射，例如 SECURITY:US:NVDA:2026-05-08。
- 说明位置：本 README
- 注意：不要在顶层内嵌 _cache_info，避免遍历逻辑把说明误认为行情记录。

### `vix_index_daily.csv`
- 用途：RSI 图 VIX 风险状态带使用的 VIX 日线历史，供 200/20 日均线计算预热。
- 生成：tools/vix_history.py 从 Yahoo Chart 明确 1d 区间拉取后写入。
- 读取：tools/rsi_data.py, strategy/nasdaq100_vix_spread.py
- 刷新：缓存已覆盖最近完整美股交易日时不联网；落后时最多每 2 小时尝试一次，失败继续使用已验证旧日线。
- 保留：始终只保留最近 1000 条交易日日线，满足 200/20 日均线预热且不会无限增长。
- 结构：CSV 表，固定包含 date、close 两列。
- 说明位置：本 README
- 注意：美股完整交易日按 NYSE 日历和收盘后缓冲判断；GitHub UTC 运行环境会先换算为北京时间。
- 注意：策略试验使用 strategy/cache/vix_index_daily.csv，主程序使用 cache/vix_index_daily.csv，二者互不覆盖。

## 50DMA广度缓存

- `market_breadth/prices/<市场.证券>.json`：同一复权基准下最多400个交易日，多个指数共用；`provisional_dates` 记录富途快照接续的临时收盘价，正式日线到达后逐日核对清除。来源/复权基准不兼容须重取窗口。
- `market_breadth/members/<市场>.json`：`rows` 记录成分版本的发现、验证、生效日及来源证据；`pending_membership` 保存生效日未核实的变化；`pit_start` 划定逐日成分口径首日。成分约每15天复核，富途指数板块可用时优先；NDX 当前使用 Nasdaq 官方 JSON 的 101 只证券并与总览核对，富途 NDX 可用时允许最多5只差异。国内富途不可用时沿用官方成分文件；道指可用 DIA 持仓候选并与 S&P DJI 核对。旧 `nasdaq` 是独立的 COMP 近似池。
- `market_breadth/membership_events/<市场>.json`：每次确认生效的调样事件及增删证券，跨主机按事件ID合并。
- `market_breadth/results/<市场>.json`：最多400个广度记录，`kind=close/intraday` 区分收盘和盘中；`finality=snapshot_provisional` 不等于已核实正式日线；缺失值为null，不是0。旧结果标记 `current_members_backcast`，启用后结果标记 `point_in_time_membership`；已发布正式值只有显式 `--repair` 可更正。
- `market_breadth/benchmarks/nasdaq_stockcharts.json` 和 `nasdaq100_stockcharts.json`：保存既有 StockCharts `$NAA50R`、`$NDXA50R` 独立参照数据；不再自动请求，不参与绘图优先级。旧 `results/nasdaq_stockcharts.json` 仅保留。
- `market_breadth/direct_indicators/<市场>.json`：预留获准自动使用的同成分口径直接广度，文件需含 `type=direct_indicators`、`source_id`、`universe`、`metric=percent_members_above_sma50` 及逐日 `date/percent/kind/source`；盘中行还需 `observed_at`。只有配置中登记 `access_approved=True` 的来源才会供绘图优先读取，当前无已登记来源；正式自算结果不受其覆盖。
- NDX 在 `pit_start` 前的历史曲线从首次核验名单和真实价格分片即时回算，缺口及覆盖率不足95%的日期留空，图内标明历史口径；不写入 `results/nasdaq100.json`。
- A股15:15之后可用富途15:00快照、美股按实际收市时间可用富途常规时段末尾快照，均按昨收比例接续原复权基准；后续正式日线核对。图表最多提示5个交易日内可信旧收盘值与日期，不复活过期盘中点。
- 美股日线依次尝试 Yahoo、新浪美股前复权和东方财富直连；换源时重取完整窗口，不把不同复权口径拼接。限流或连接故障按本轮来源熔断，未补证券留待续跑。
- `market_breadth/snapshots/<市场>.json`：最近一批快照，`nasdaq100` 与旧 `nasdaq` 分开；默认40分钟复用，原始报价时间控制有效性，不以文件修改时间替代。
- 新发布值要求有效成分覆盖率至少95%；旧正式收盘值不因阈值改变而追改。未来或待确认调入证券可提前预热真实价格，生效前不进入分母，调出证券分片保留。
- 生产者：`market_breadth.py`；读取：RSI绘图、`check_project.py`和`sync_repos.py`。
- 所有业务分片参加Git三边同步；本机锁、连接配置和临时文件不提交。`market_breadth_local/`仅用于本机协调。
- 合并价格先校验复权基准与重叠值；临时价和正式价冲突时正式价优先，不兼容时取较新完整快照。正式收盘结果优先于盘中值，不按后来成分版本回写既有历史。
- 日线/结果仅裁剪文件内记录，不批量删除分片；停用证券保留但不再主动抓取。体检只读报告容量。
