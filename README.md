# OKX Semi-Auto Quant

一个面向 OKX 的半自动量化交易系统骨架。默认以 paper 模式运行，生成交易计划、执行风控、记录日志，不会直接动真实资金。

## 目标

- 构建可验证、可解释、可审计的市场分析与辅助决策流程。
- 所有交易信号统一经过风控引擎，并明确展示依据、适用条件和失效条件。
- 默认采用人工确认机制，降低异常行情、模型偏差或配置错误带来的执行风险。
- 完整记录信号、计划、风控结果和账户快照，为评估与持续优化提供数据基础。

## 快速开始

```bash
cp .env.example .env
bash scripts/start.sh
```

启动后访问：

http://127.0.0.1:8787/

主界面分为两个专业工作区：

- **机会雷达**：按多周期概率、收益、置信度和趋势一致性生成机会分并排序；深度分析使用独立详情地址。
- **持仓哨兵**：只监控手动登记的买入记录，输出动态保护位、风险分、减仓与退出建议。
- **决策小精灵**：顶部按钮和右下角悬浮入口均可打开；支持当前标的与全局市场两种范围，流式关联预测、策略学习、持仓风险和历史命中率。

深度看盘支持真实 `1D` 与 `1H` OHLC 蜡烛图。`1H` 数据在用户切换时按需从
OKX 获取最近 168 根小时 K，避免后台刷新时对全部标的重复请求。

持仓哨兵使用 `volatility_trailing_regime_exit_v1` 退出模型，综合波动率动态止损、
持仓高点回撤、趋势状态、上涨概率衰减、目标达成和预测周期到期。系统不会自动卖出。

页面价格、登记默认价和持仓盈亏使用 OKX ticker 最新成交价；预测会把最新成交价作为
当前观测重新计算趋势、动量、收益率、上涨概率和目标价，已收盘日 K 仅提供历史特征。
页面会显示行情时间与“实时特征重算”状态，行情默认每 5 秒更新。

查看运行状态：

```bash
bash scripts/status.sh
```

停止全部服务：

```bash
bash scripts/stop.sh
```

默认扫描池现在包含 10 个主流加密货币永续和 7 个 OKX 股票类 USDT 永续：

```text
BTC ETH SOL XRP DOGE ADA LINK AVAX BNB LTC
AAPL AMZN GOOGL META MSFT NVDA TSLA
```

分析盈利机会和估算成功率：

```bash
python3 -m qis analyze --top 10
```

系统默认只展示成功率不低于 70% 的候选：

```bash
python3 -m qis analyze --top 10 --min-success 0.70
```

查看全部候选，包括未达 70% 门槛的：

```bash
python3 -m qis analyze --top 10 --show-all
```

记录你手动执行后的真实交易结果：

```bash
python3 -m qis trade-add --inst ETH-USDT-SWAP --side buy --entry 1802 --exit 1820 --size 0.1 --stop 1790 --tp 1829 --prob 0.49 --model walkforward_calibrated_macro_intel_v4 --notes "manual breakout"
```

查看真实胜率和模型校准误差：

```bash
python3 -m qis trade-stats
```

输出文件：

```text
data/analysis.html
```

当前机会分析模型：

- `walkforward_calibrated_macro_intel_v4`
- 特征：突破强度、EMA 趋势差、短周期动量、ATR 波动率、RSI、成交量偏离。
- 宏观：美股风险偏好、纳指、美元代理、VIX、10 年期收益率。
- 外部资讯：CoinDesk、Cointelegraph、Decrypt RSS 标题，提取 ETF、监管、黑客、诉讼、资金流、机构采用等事件风险。
- 验证：历史预测严格按时间前推，只使用预测时点之前的数据，避免未来数据泄漏。
- 概率：用样本外预测结果做可靠性校准，再用宏观风险环境和外部资讯做小幅方向校准。
- 模型健康度：输出 Brier 分数、校准误差、前推样本量和漂移状态。
- 评分：综合成功率、期望 R、样本质量、距离入场区间、趋势环境、宏观环境、外部资讯。
- 准入：默认要求校准概率不低于 70%、前推样本不少于 20、Brier 不高于 0.24、模型状态稳定。
- 模型改进建议来自历史预测与预测周期到期后的真实行情对照，评估方向命中率、收益误差、Brier 分数和预测区间覆盖率。
- 手动交易记录只用于持仓复盘，不参与模型优化建议。
- 系统按 UTC 自然小时冻结一次自身预测；到期后使用近期样本权重更高的有界回归，反推收益偏差、概率偏差和区间覆盖并校准后续预测。
- 每个周期至少积累 30 个到期预测才启用自动校准；方向可靠性不足时会把收益与上涨概率主动拉回中性，避免模型自我放大。
- 每小时学习运行会写入 `forecast_learning_runs`：记录本轮新增评估数、累计与待到期样本、启用优化的周期、参数快照和改进建议。
- 后续预测自动应用收益偏移/缩放、概率偏移/缩放和区间宽度调整；长期动量不再随预测天数线性放大，趋势与动量冲突时降低动量权重，收益使用保序软边界而非硬截断，避免不同标的显示相同涨幅。
- 每次预测算法升级都会使用独立模型版本重新积累样本；冷启动阶段将收益和概率向中性收缩，跨市场校准只调整置信度，不能反转单个标的方向。
- `market_microstructure_macro_v6` 将 20 档盘口买卖深度、永续资金费率、持仓量变化、近 10 日量价结构和宏观风险状态直接纳入收益与概率公式。
- 盘口影响集中在 1 天/1 周，宏观与持仓变化在 3 月/6 月权重更高；每类因子均归一化到 `[-1, 1]`，单周期的多因子收益修正最多约 3.5%–6%。
- 资金费率按拥挤度反向处理；持仓量只有在存在前一轮快照时才参与，并结合价格方向判断增仓确认或去杠杆。接口不可用时对应因子自动回到中性。
- “市场大环境”进一步汇总全市场 24 小时涨跌广度、站上 30 日均价的比例、BTC 30/90 日趋势锚、横截面波动压力和成交量参与度，划分为风险扩张、过渡震荡或风险收缩。
- 大环境对 1 周至 6 月预测的权重约 24%–30%，但仍受单周期收益修正上限约束；它只调节个股/币种信号强度，不允许覆盖单标的全部证据。
- 机会分与策略列使用同一套后端规则，并在实时价格重算、历史校准后同步更新：70 分以下绝不显示“分批关注买入”；60–69 分最多为“观察等待触发”，45–59 分为中性观察，低于 45 分等待趋势企稳。
- 深度分析支持四套可切换、可横向对比的预测策略：综合自适应用于全周期平衡；趋势跟随针对 1 月–6 月单边行情；突破确认针对 1 天–1 月的量价、盘口与持仓共振；均值回归针对 1 天–1 月的过度偏离修复。每套策略独立输出收益、概率、机会分、目标价和适用风险。
- 新增策略在积累各自到期样本前采用保守冷启动，不借用综合策略的历史校准参数，并统一显示“模拟观察”，不能直接给出实盘买入准入；默认综合自适应仍沿用已验证的独立学习记录。

### DeepSeek V4 资讯研究员

在 `.env` 中配置：

```ini
DEEPSEEK_API_KEY=你的_API_Key
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-v4-flash
DEEPSEEK_TIMEOUT_SECONDS=45
DEEPSEEK_CACHE_TTL_SECONDS=1800
```

说明：

- 默认使用 `deepseek-v4-flash`，可切换为 `deepseek-v4-pro`。
- DeepSeek 只负责将新闻标题转换为结构化事件因子，不允许直接下单或绕过风控。
- 输出包含影响资产、方向、影响强度、置信度、有效期和理由。
- 新闻标题被视为不可信数据，提示词明确禁止执行标题中的指令。
- API 输出经过 JSON、资产白名单和数值范围校验。
- 结果按模型、标题和资产池指纹缓存 30 分钟，降低费用。
- 未配置 Key 或接口失败时自动降级到关键词资讯模型。
- API Key 只保存在 `.env`，该文件已被 `.gitignore` 排除。

### 决策小精灵与可切换 LLM

决策问答使用 OpenAI-compatible 的 `/chat/completions` 协议。默认复用
`DEEPSEEK_*` 配置，也可以单独配置任意兼容底座：

```ini
LLM_PROVIDER=DeepSeek
LLM_API_KEY=你的_API_Key
LLM_BASE_URL=https://api.deepseek.com
LLM_MODEL=deepseek-v4-flash
LLM_TIMEOUT_SECONDS=45
```

切换其他服务时只需替换 `LLM_PROVIDER`、`LLM_BASE_URL`、`LLM_MODEL` 和 Key，
无需修改业务代码。每次问答会注入当前页面所选标的与周期，并关联：

- 多周期价格预测、上涨概率、置信度、买入区间和失效位；
- 历史预测命中率、Brier 等模型评估与受限自动校准参数；
- 手动登记持仓、动态保护位、风险分与退出建议；
- 基于历史预测误差生成的模型改进建议。

小精灵只输出解释、风险和下一步建议，不具备下单权限；未配置 Key 时界面会明确提示。
“全局市场”模式会汇总全部标的的强弱分布、主要机会、弱势资产、当前持仓风险和最近一轮策略学习结果。

### 算法依据

- 时间序列验证必须保持时间顺序，避免用未来数据训练过去预测：
  https://scikit-learn.org/stable/modules/generated/sklearn.model_selection.TimeSeriesSplit.html
- 概率需要用独立于训练数据的预测结果校准，并使用 Brier 等指标评估：
  https://scikit-learn.org/stable/modules/calibration.html
- 在线模型可使用 ADWIN 一类方法检测数据分布漂移：
  https://riverml.xyz/latest/api/drift/ADWIN/
- 后续金融机器学习验证可参考 MlFinLab 的标签和交叉验证方法：
  https://github.com/hudson-and-thames/mlfinlab

查看最近记录：

```bash
python3 -m qis status
```

生成本地 HTML 风控控制台：

```bash
python3 -m qis dashboard
```

输出文件：

```text
data/dashboard.html
```

使用 OKX 最近 K 线回测当前策略：

```bash
python3 -m qis backtest --limit 300
```

实盘前自检：

```bash
python3 -m qis doctor
```

紧急暂停交易循环：

```bash
python3 -m qis pause
```

恢复：

```bash
python3 -m qis resume
```

初始化数据库：

```bash
python3 -m qis init-db
```

## 真实交易前必须配置

编辑 `.env`：

```ini
OKX_API_KEY=你的key
OKX_API_SECRET=你的secret
OKX_API_PASSPHRASE=你的passphrase
OKX_SIMULATED=1
QIS_MODE=paper
QIS_INST_IDS=BTC-USDT-SWAP,ETH-USDT-SWAP,SOL-USDT-SWAP,XRP-USDT-SWAP,DOGE-USDT-SWAP,ADA-USDT-SWAP,LINK-USDT-SWAP,AVAX-USDT-SWAP,BNB-USDT-SWAP,LTC-USDT-SWAP
QIS_STOCK_INST_IDS=AAPL-USDT-SWAP,AMZN-USDT-SWAP,GOOGL-USDT-SWAP,META-USDT-SWAP,MSFT-USDT-SWAP,NVDA-USDT-SWAP,TSLA-USDT-SWAP
```

说明：

- `QIS_MODE=paper`：只记录计划，不真实下单。
- `QIS_MODE=live`：允许真实下单，但仍需要命令行确认。
- `OKX_SIMULATED=1`：请求 OKX 模拟盘。
- `OKX_SIMULATED=0`：请求 OKX 实盘。

## 默认风控

- 单笔风险：0.75%
- 单日最大亏损：2.5%
- 最大回撤：12%
- 最大杠杆：2x
- 最大名义仓位：权益的 35%
- 每日最多交易：6 次
- `data/PAUSE` 存在时交易循环自动停机

这些参数在 `.env` 里可调。

## 当前策略

`DonchianBreakoutStrategy`：

- 使用 OKX K 线。
- 向上突破最近 N 根高点，生成做多计划。
- 向下跌破最近 N 根低点，生成做空计划。
- 可选 EMA 趋势过滤，默认关闭，需在 `.env` 设置 `QIS_EMA_FAST` 和 `QIS_EMA_SLOW`。
- 止损用 ATR 倍数。
- 仓位由风控引擎按止损距离计算。

这是第一版“可解释、可复盘”的策略，不追求神奇，只追求可验证。

## 项目结构

```text
qis/
  __main__.py        CLI 入口
  config.py          环境配置
  okx.py             OKX REST 客户端
  strategy.py        策略信号
  risk.py            风控和仓位
  storage.py         SQLite 记录
  runner.py          主循环
  backtest.py        简易历史回测
  analyzer.py        行情机会分析和成功率估算
  analysis_report.py HTML 机会报告
  dashboard.py       本地 HTML 风控控制台
  models.py          数据模型
```

## 重要提醒

这不是投资建议。数字货币波动极大，杠杆会快速放大亏损。系统默认保护本金，目标是先做出真实曲线，再谈放大。
