# OKX Semi-Auto Quant

一个面向 OKX 的半自动量化交易系统骨架。默认以 paper 模式运行，生成交易计划、执行风控、记录日志，不会直接动真实资金。

## 目标

- 用 5000 启动金先验证可重复的交易流程，而不是盲目追求高杠杆暴富。
- 所有交易信号必须经过风控引擎。
- 初期使用半自动确认，避免机器人在异常行情或配置错误时连续下单。
- 保存每次信号、计划、风控拒绝原因和账户快照，方便复盘。

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
- **决策小精灵**：顶部按钮和右下角悬浮入口均可打开，自动关联当前标的预测、策略校准、持仓风险、历史命中率和模型改进建议。

决策小精灵的对话气泡、输入状态、错误提示和会话历史由 NLUX 聊天组件管理。
前端使用稳定的批量问答接口；Enter 发送，Shift+Enter 换行。NLUX 静态资源在
首次启动时从 npm 镜像缓存到 `data/vendor`，浏览器运行时不依赖 CDN。

持仓哨兵使用 `volatility_trailing_regime_exit_v1` 退出模型，综合波动率动态止损、
持仓高点回撤、趋势状态、上涨概率衰减、目标达成和预测周期到期。系统不会自动卖出。

页面价格、登记默认价和持仓盈亏使用 OKX ticker 最新成交价；趋势与波动指标仍只使用
已收盘日K，避免未完成K线污染模型。页面会显示行情时间与延迟状态，默认每 5 分钟刷新。

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
- 系统按 UTC 自然小时冻结一次自身预测；到期后反推收益偏差、概率偏差和区间覆盖，并用有上下限的参数校准后续预测。
- 每个周期至少积累 30 个到期预测才启用自动校准；方向可靠性不足时会压低收益修正，避免模型自我放大。

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

- 初始权益：5000 USDT
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
