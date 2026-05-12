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
- 注意：VIX 这类点位记录使用 value/value_type/display_value，不参与累计收益。

### `fund_holdings_cache.json`
- 用途：基金最近披露前 N 大股票持仓缓存，用于估算海外/全球基金持仓贡献。
- 生成：tools/get_top10_holdings.py 在首次缺失或披露窗口低频试探时写入。
- 读取：tools/get_top10_holdings.py, fund_estimate_breakdown.py
- 刷新：非披露窗口直接复用；披露窗口内每只基金约 3 天最多试探一次。
- 保留：每个 fund_code:topN 一个 key，更新时覆盖同 key，不按日期追加。
- 结构：顶层是 fund_code:topN -> 持仓记录的映射；data_json 内保存持仓表。
- 说明位置：本 README
- 注意：不要在顶层内嵌 _cache_info，避免遍历逻辑把说明误认为基金持仓。

### `fund_purchase_limit_cache.json`
- 用途：基金限购金额缓存，用于每日基金图展示模型观察限购信息。
- 生成：tools/get_top10_holdings.py 解析公开网页限购文本后写入。
- 读取：tools/get_top10_holdings.py, kepu/kepu_xiane.py
- 刷新：默认 7 天刷新一次；新结果为未知且旧值明确时保留旧值。
- 保留：每个基金代码一个 key，更新时覆盖同 key，不按日期追加。
- 结构：顶层是 fund_code -> {fetched_at, value} 的映射。
- 说明位置：本 README
- 注意：不要在顶层内嵌 _cache_info，避免遍历逻辑把说明误认为限购记录。

### `mark.jpg`
- 用途：safe 公开图使用的居中 logo 水印素材。
- 生成：人工维护。
- 读取：tools/safe_display.py, safe_fund.py, safe_holidays.py, sum_holidays.py
- 刷新：需要更换水印素材时人工替换。
- 保留：固定资源文件，不由运行脚本裁剪。
- 结构：JPEG 图片。
- 说明位置：本 README
- 注意：不是行情缓存；保留在 cache/ 下是为了 GitHub Actions 和本地运行共用路径。

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
