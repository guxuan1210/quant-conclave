# 🛡️ QuantConclave — AI 多智能体主力资金雷达

> **打破 A 股信息不对称：8 位 AI 智能体协同工作，追踪主力资金真实动向，并形成持续进化的分析闭环。**
>
> 衍生自 [TradingAgents](https://github.com/TauricResearch/TradingAgents)（Xiao et al. 2025），深度定制了资金流分析、经验萃取闭环、智慧选股与回测模块。

---

## 为什么需要 QuantConclave？

A 股市场最大的矛盾是 **信息不对称**：机构掌握资金、数据、研究团队；散户看 K 线、听消息、凭感觉。

QuantConclave 用 **8 个 AI 智能体** 组成分析流水线，从资金流向、技术面、情绪、新闻、基本面、预测等维度独立研判，再通过多空辩论、风险辩论和组合经理综合决策，输出可验证的买卖建议。**每次分析的结果会被自动回测验证，经验被萃取存入经验库，经过你的审核后注入未来的分析，形成持续进化的学习闭环。**

---

## 架构总览

```
用户界面 (FastAPI + Vanilla JS)
|
├── Deep Analysis     ───  7 分析师 → 裁决 → 辩论 → 交易员 → 风控 → PM
├── AI Pick Agent     ───  自然语言选股引擎
├── Prediction Agent  ───  ML+LLM 价格方向预测
├── Strategy Agent    ───  LLM 生成 backtrader 策略
├── Backtest Agent    ───  backtrader 回测引擎
├── Advisory Agent    ───  AI 投资顾问（对话 + 经验库）
├── History Agent     ───  历史复盘 → 经验萃取（对话式）
│
└── System Calibration ───  自动学习闭环
     Phase 1: Resolve     → 回测历史分析，获取实际收益率
     Phase 2: Meta-Eval   → 统计准确率，提出阈值调整
     Phase 3: Extract     → 模式检测，生成经验 (pending_review)
     Phase 4: Skill Gen   → 经验 → 版本化 SKILL.md → 注入下次分析
```

---

## 8 个智能体详解

| 智能体 | 职责 | 技术栈 |
|--------|------|--------|
| **Deep Analysis** | 全栈深度分析流水线：7 位分析师 → 裁决器 → 多空辩论 → 交易员 → 风控辩论 → PM 最终决策。每次分析自动携带历史经验上下文 (SKILL.md) | LangGraph, 7 agents, 3-round debate |
| **AI Pick Agent** | 自然语言选股引擎。支持口语化查询，结合四门资金评分 (Scale→Persist→Align→Cross)，5 个快捷按钮一键运行 | LLM, SSE streaming, smart_money_score |
| **Prediction Agent** | ML + LLM 混合价格方向预测。为 PM 决策提供时间维度支撑 (短/中/长期) | XGBoost + LLM, 历史回测, 行为预测 |
| **Strategy Agent** | LLM 生成 backtrader 回测策略代码，可独立运行或由顾问调用 | LLM 代码生成, backtrader 策略模板 |
| **Backtest Agent** | backtrader 回测引擎，7 套内置策略模板，支持自定义 + 报告输出 | backtrader, 收益率统计, 回撤分析 |
| **History Agent** | **对话式历史复盘**：读取历史深度分析记录，LLM 自动萃取经验和教训，存入经验库 (pending_review)，支持审核后激活 | SSE 流式对话, experience_store, 去重检测 |
| **Advisory Agent** | AI 投资顾问：对话中引用经验库、查询历史、实时触发深度分析 | SSE 流式对话, 经验注入, 历史查询 |
| **System Calibration** | **自动学习闭环核心**：4 阶段循环 (Resolve→Meta-Eval→Extract→Skill Gen)，在后台自动运行 | resolver, meta_evaluator, experience_extractor, skill_generator |

---

## 学习闭环：系统如何自动进化

这是 QuantConclave 区别于普通分析工具的核心能力：

```
每次深度分析完成
        │
        ▼
System Calibration (自动或手动触发)
        │
        ├── Phase 1: Resolve (回测验证)
        │   用实际历史价格计算每条分析的持有期收益率
        │   标记 BUY/HOLD/SELL 的准确率
        │
        ├── Phase 2: Meta-Eval (元评估)
        │   统计分析准确率趋势
        │   提出阈值调整建议 (如 "当主力/散户比 > 3 时准确率 78%")
        │
        ├── Phase 3: Extract (经验萃取)
        │   LLM 扫描回测结果，识别模式与偏差
        │   生成结构化经验 (存入经验库，status=pending_review)
        │
        └── Phase 4: Skill Gen (技能生成)
            将你审核通过的经验编译为版本化 SKILL.md
            下次 Deep Analysis 自动加载该 SKILL.md 作为上下文
        │
        ▼
build_instrument_context()
→ 分析师 / PM / 辩论全部受益于历史经验
```

**经验管理**：所有萃取的经验先标记为 `pending_review`，你可以在 Web UI 中逐一审核、激活或拒绝。已激活的经验编译为 SKILL.md 版本，并支持随时回溯和取消注入。

---

## 资金流分析引擎

### 四门检测管线 (Smart Money Detection)

用 4 道逻辑门验证机构资金真实参与度，而非简单的数值打分：

| 门 | 检查 | 条件 |
|----|------|------|
| **Gate 1: Scale** | 规模足够大？ | 日均净流 > 大单流量的 5% |
| **Gate 2: Persist** | 方向持续？ | 最近 5 天中 >= 3 天同方向，且量在增长 |
| **Gate 3: Align** | 量价一致？ | 资金方向与价格方向匹配（否则是背离/操纵） |
| **Gate 4: Cross** | 多源验证？ | >= 1 个其他数据源（大单/北向/融资）确认方向 |

**输出**：`confirmed` / `divergence` / `unconfirmed` / `no_signal`，附带生命周期阶段（建仓/蓄力/发力/分布/退出）和中/英文摘要。

### 五维资金评分 (Pick Agent 选股排名用)

| 维度 | 分值 | 数据源 |
|------|------|--------|
| 主力强度 | 0-30 | `main_force_net` (超大单+大单净额) |
| 趋势质量 | 0-20 | `trend` (accelerating/stable/weakening) |
| 主散比 | 0-20 | `mf_ratio` (主力 vs 散户) |
| 超大单活跃度 | 0-15 | `buy_elg_ratio` (超大单成交占比) |
| 增仓比 | 0-15 | `net_inflow_ratio` (净流入占比) |

---

## 数据源架构

多供应商自动回退与交叉验证：

```
tushare Pro (主力数据源)
├── 个股资金流 (moneyflow, doc_id=25)
├── 沪深港通 · 大盘资金流 · 融资融券
├── 龙虎榜 · 限售解禁 · 股权质押 · 财报
└── TUSHARE_TOKEN 在 .env 中配置

东方财富妙想 API - mx-data (可选, 交叉验证)
├── 自然语言查询：主力资金流 / 实时行情
├── 财务数据 / 板块数据 / 自定义查询
└── MX_APIKEY 在 .env 中配置

yfinance / akshare (兜底)
└── K线数据 · 技术指标 · 美股数据
```

### 资金流数据字段

`get_stock_moneyflow()` 返回：

| 字段 | 说明 | 使用场景 |
|------|------|----------|
| `net_amount` | 主力净流入(万元) | 核心信号 |
| `main_force_net` | 超大单+大单净额 | Gate 4 交叉验证 |
| `retail_net` | 散户方向净额 | 主散对比 |
| `mf_ratio` | 主散比 (>2=机构主导) | Gate 2 持续性 |
| `net_inflow_ratio` | 主力净流入占比% | Gate 1 规模 |
| `buy_elg_ratio` | 超大单成交占比% | Gate 4 辅助 |
| `net_1d/5d/20d` | 不同周期净额 | 趋势判断 |
| `trend` | 资金流趋势 | accelerating / stable / weakening |

---

## Web 仪表盘

两个服务同时启动：

| 地址 | 功能 |
|------|------|
| `http://127.0.0.1:8003` | 主面板：深度分析、选股、预测、策略、回测、顾问、历史复盘、校准 |
| `http://127.0.0.1:8005` | K 线图：Lightweight Charts，含 MA/MACD/RSI 指标，日/周/月 |

### 标签页功能

| 标签 | 功能 |
|------|------|
| **Deep Analysis** | 选股 → 7 分析师流水线 → 裁决 → 辩论 → 交易员 → 风控 → PM 决策 |
| **AI Pick** | 自然语言选股 + 5 个快捷按钮（主力加速流入/科技+主力/逆势抄底/净流入占比/持续流入） |
| **Prediction** | ML+LLM 价格预测，为 PM 决策提供时间维度 |
| **Strategy** | 对话式生成 backtrader 策略代码 |
| **Backtest** | backtrader 回测引擎，7 套模板，支持自定义 |
| **History Agent** | 复盘智能体：历史记录列表 + 对话式复盘 + 经验萃取 + 系统校准面板 |
| **Advisory** | AI 投资顾问对话，可查历史、拉实时、触发分析、引用经验库 |
| **Rotation** | RRG 板块旋转图 + 资金流模式 |
| **Shortlist** | 自选股列表，一键批量分析 |

---

## 快速开始

```bash
# 1. 克隆
git clone https://github.com/guxuan1210/quant-conclave.git
cd quant-conclave

# 2. 安装依赖（推荐 uv 或 pip）
uv pip install -e .
# 或: pip install -e .

# 3. 配置环境变量
cp .env.example .env
# 编辑 .env:
#   DEEPSEEK_API_KEY=sk-xxx       # LLM API Key（必须）
#   TUSHARE_TOKEN=xxx             # tushare Pro token（必须，资金流数据）
#   MX_APIKEY=mkt_xxx             # 东方财富妙想（可选，交叉验证）

# 4. 启动
python run_web.py
#   -> 主面板: http://127.0.0.1:8003
#   -> K线图:  http://127.0.0.1:8005
```

---

## Python API 示例

```python
# 完整深度分析
from quantconclave.graph.trading_graph import QuantConclaveGraph
from quantconclave.default_config import DEFAULT_CONFIG

config = DEFAULT_CONFIG.copy()
config["llm_provider"] = "deepseek"
config["deep_think_llm"] = "deepseek-v4-flash"

ta = QuantConclaveGraph(debug=True, config=config)
_, decision = ta.propagate("600519.SH", "2026-06-30")
print(decision)

# 资金流数据
from quantconclave.dataflows.eastmoney_sector import get_stock_moneyflow
mf = get_stock_moneyflow("600519.SH", days=5)
print(mf["main_force_net"], mf["mf_ratio"], mf["trend"])

# AI 选股
from quantconclave.sector_scan.smart_scanner import run_smart_scan
result = run_smart_scan(["半导体", "软件开发"], top_n=5)
print(result)

# 经验萃取（校准循环）
from quantconclave.graph.loop_coordinator import run_full_loop
summary = run_full_loop(DEFAULT_CONFIG, trigger="manual")
print(summary)
```

---

## 配置 (.env)

| 变量 | 必需 | 说明 |
|------|------|------|
| `DEEPSEEK_API_KEY` | ✅ | LLM 提供商（推荐 DeepSeek） |
| `TUSHARE_TOKEN` | ✅ | tushare Pro token，资金流数据源 |
| `MX_APIKEY` | 可选 | 东方财富妙想，交叉验证 |
| `HTTP_PROXY` | 可选 | 境外数据代理（如 yfinance） |
| `NO_PROXY` | 推荐 | 绕过代理的国内数据源 |

支持的 LLM 提供商：
DeepSeek / OpenAI / Anthropic / Google / xAI / DashScope / 智谱 GLM / MiniMax / Ollama / OpenRouter

运行时覆盖（环境变量）：
`QUANTCONCLAVE_LLM_PROVIDER`、`QUANTCONCLAVE_DEEP_THINK_LLM`、`QUANTCONCLAVE_QUICK_THINK_LLM` 等可覆盖 `default_config.py`。

---

## 项目结构

```
QuantConclave/
├── quantconclave/
│   ├── agents/                   # AI 分析师智能体
│   │   ├── analysts/             #   7 位分析师（资金流/市场/情绪/新闻/基本面...）
│   │   ├── managers/             #   Research Manager + Portfolio Manager
│   │   ├── researchers/          #   多空辩论 (Bull/Bear)
│   │   ├── risk_mgmt/            #   风险管理（激进/保守/中立）
│   │   ├── trader/               #   交易员
│   │   └── utils/                #   工具函数 + 资金流工具
│   ├── graph/                    # LangGraph 流水线 + 学习闭环
│   │   ├── trading_graph.py      #   主图定义 + 工具节点 + 经验注入
│   │   ├── adjudicator.py        #   裁决器 (4 条规则)
│   │   ├── resolver.py           #   批量回测解析 (Phase 1)
│   │   ├── meta_evaluator.py     #   元评估 + 阈值调优 (Phase 2)
│   │   ├── experience_extractor.py # 经验提取 (Phase 3)
│   │   ├── skill_generator.py    #   SKILL.md 生成 (Phase 4)
│   │   ├── loop_coordinator.py   #   4 阶段自动循环编排
│   │   └── reflection.py         #   反思注入
│   ├── dataflows/                # 多供应商数据路由
│   │   ├── tushare_data.py       #   tushare 资金流/财务
│   │   ├── eastmoney_sector.py   #   东方财富行业/板块/资金流
│   │   ├── interface.py          #   多 vendor 切换
│   │   ├── akshare_data.py       #   akshare 数据源
│   │   └── y_finance.py          #   yfinance 数据源
│   ├── advisory/                 # 经验库 + History Agent
│   │   ├── experience_store.py   #   SQLite CRUD + 审核
│   │   ├── extraction.py         #   模式检测
│   │   ├── history_agent.py      #   LLM 历史分析 + 经验解析
│   │   └── calibration_tool.py   #   校准工具类
│   ├── backtest/                 # backtrader 回测引擎
│   │   ├── engine.py             #   回测执行
│   │   ├── strategies.py         #   7 套内置策略
│   │   ├── templates.py          #   策略模板
│   │   └── report.py             #   回测报告
│   ├── strategy/                 # LLM 策略代码生成
│   │   ├── generator.py          #   策略代码生成器
│   │   └── manager.py            #   策略管理
│   ├── prediction/               # ML+LLM 混合预测
│   ├── sector_scan/              # 板块扫描 + 智慧选股
│   │   ├── smart_scanner.py      #   智能评分扫描
│   │   ├── smart_money_score.py  #   四门检测管线
│   │   ├── rotation.py           #   RRG 板块旋转
│   │   └── pick_tracker.py       #   选股跟踪
│   └── llm_clients/              # 多 LLM 提供商工厂
├── web/                          # Web 仪表盘
│   ├── app.py                    #   FastAPI 主应用
│   ├── history_chat.py           #   Advisory/Pick Agent SSE 流式引擎
│   ├── history_agent.py          #   History Agent SSE 流式引擎
│   ├── ai_pick_agent.py          #   Pick Agent 对话引擎
│   ├── strategy_agent.py         #   Strategy Agent
│   ├── backtest_agent.py         #   Backtest Agent
│   ├── prediction_chat.py        #   Prediction Agent
│   ├── calibration_ui.py         #   校准面板 API
│   ├── advisory_experience_ui.py #   经验库管理 API
│   ├── skill_ui.py               #   SKILL.md 管理 API
│   └── static/                   #   前端 (Vanilla JS + Lightweight Charts)
│       ├── app.js                #   主应用逻辑
│       ├── history_agent.js      #   History Agent UI
│       ├── calibration_panel.js  #   校准面板 UI
│       └── style.css             #   样式
├── chart_app.py                  # K 线图独立服务 (8007)
├── run_web.py                    # 启动入口（双线程）
├── .env                          # 配置
└── pyproject.toml                # 项目元数据 (v0.2.5)
```

---

## LLM 服务商

| 服务商 | 环境变量 | 推荐模型 |
|--------|---------|----------|
| DeepSeek | `DEEPSEEK_API_KEY` | `deepseek-v4-flash` / `deepseek-v4-pro` |
| OpenAI | `OPENAI_API_KEY` | `gpt-4o` / `o3-mini` |
| Anthropic | `ANTHROPIC_API_KEY` | `claude-sonnet-4` / `claude-haiku-3` |
| Google | `GOOGLE_API_KEY` | `gemini-2.0-flash` |
| xAI | `XAI_API_KEY` | `grok-2` |
| DashScope | `DASHSCOPE_API_KEY` | `qwen-plus` |
| 智谱 GLM | `ZHIPU_API_KEY` | `glm-4-plus` |
| MiniMax | `MINIMAX_API_KEY` | `minimax-m1-0508` |
| Ollama | — | 本地部署 |
| OpenRouter | `OPENROUTER_API_KEY` | 统一多模型接口 |

运行时 LLM 模型通过环境变量 `QUANTCONCLAVE_DEEP_THINK_LLM` / `QUANTCONCLAVE_QUICK_THINK_LLM` 切换，无需改代码。

---

## 学习闭环生命周期

```
                Deep Analysis
                （含 SKILL.md 上下文注入）
                        │
                        │ 分析完成
                        ▼
          System Calibration（后台自动触发）
                        │
           1. Resolve → 回测计算实际收益率
           2. Meta-Eval → 统计准确率趋势
           3. Extract → 生成经验（pending）
           4. Skill Gen → 编译 SKILL.md v+N
                        │
                        ▼
              experience_store (SQLite)
                        │
              ┌──────────┐    ┌──────────┐
              │ pending  │───→│  active  │  ← 你在 UI 审核
              │ _review  │    │          │
              └──────────┘    └────┬─────┘
                                  │
                                  ▼
               SKILL.md v+N
               → build_instrument_context()
               → 下次 Deep Analysis 自动加载
               → 分析师/PM/辩论全部受益
```

**你始终掌控**：每条经验默认为 `pending_review`，必须经你在 UI 中审核激活后才会进入 SKILL.md。已激活的经验支持随时取消注入。

---

## 免责声明

QuantConclave 仅供研究和教育目的。AI 生成的分析结果不构成任何投资、交易或财务建议。投资有风险，入市需谨慎。

---

## 致谢

本项目基于 [TradingAgents](https://github.com/TauricResearch/TradingAgents) — Xiao et al. (2025) 的多智能体金融交易框架，大幅增强了主力资金分析、智慧选股引擎、经验萃取闭环、回测/策略智能体。

```bibtex
@misc{xiao2025tradingagentsmultiagentsllmfinancial,
      title={TradingAgents: Multi-Agents LLM Financial Trading Framework},
      author={Yijia Xiao and Edward Sun and Di Luo and Wei Wang},
      year={2025},
      eprint={2412.20138},
      archivePrefix={arXiv},
      primaryClass={q-fin.TR},
      url={https://arxiv.org/abs/2412.20138},
}
```
