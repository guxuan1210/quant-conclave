[English](README.md) | **简体中文**

# 🛡️ QuantConclave — AI 多角色主力资金研究平台

QuantConclave 是面向股票研究的多角色 AI 系统。它把资金流、市场技术、情绪、新闻、基本面、竞争关系和产业合作分析组织到一条可审计的研究流水线中，并通过多空辩论、风险讨论和组合经理形成最终结论。

> [!IMPORTANT]
> QuantConclave 仅供研究和教育使用。模型输出可能错误、过时或不完整，不构成投资、交易、法律或财务建议，也不代表已经验证的投资收益。

## 为什么需要 QuantConclave？

金融研究通常散落在行情、资金、财报、新闻和不同模型之间。QuantConclave 的目标不是让一个模型给出更长的回答，而是让不同职责的角色在统一数据上下文中工作，并保留分析、工具调用、决策和后续复盘记录。

项目特别关注中国 A 股场景，同时保留多市场代码和多数据源扩展能力。

## 核心能力

“能力”“角色”和“节点”在项目中含义不同：

- **Capability（能力）**：用户在界面中使用的完整工作流。
- **Role（角色）**：承担特定研究职责的 LLM 身份。
- **Node（节点）**：LangGraph 中实际执行角色、工具或控制逻辑的运行单元。

| 能力 | 说明 |
|---|---|
| Deep Analysis | 七类分析角色、裁决、多空辩论、交易计划、风险讨论与 Portfolio Manager 决策 |
| AI Pick | 基于自然语言、资金流和筛选条件构建候选股票列表 |
| Prediction | XGBoost 与 LLM 结合的方向、区间和机构行为研判 |
| Strategy | 从研究意图生成并管理可回测的策略定义 |
| Backtest | 向量化回测、交易成本、收益曲线和风险指标 |
| Advisory | 查询实时数据、历史结果和经验库的对话式研究入口 |
| History | 历史分析检索、比较、复盘与经验提炼 |
| Calibration | Resolve → Meta-Eval → Extract → Skill Gen 的人工审核学习闭环 |

## 深度研究流水线

```text
用户 / 定时任务
      │
      ▼
Capital Flow Analyst（锚点）
      │
      ├── Market Analyst
      ├── Sentiment Analyst
      ├── News Analyst
      ├── Fundamentals Analyst
      ├── Competitor Analyst
      └── Partner Analyst
      │
      ▼
规则裁决 → Bull / Bear Debate → Research Manager
      │
      ▼
Trader → Aggressive / Conservative / Neutral Risk Debate
      │
      ▼
Portfolio Manager → Buy / Overweight / Hold / Underweight / Sell
```

资金流角色始终先运行，为后续角色提供交易时点和机构行为锚点。不同角色输出独立报告，最终节点必须说明短期与中期观点、置信度、风险和关键证据。

## 资金流与数据可靠性

资金研究包含规模、持续性、量价一致性和多源交叉验证：

| 检查 | 目的 |
|---|---|
| Scale | 排除规模不足的噪声流量 |
| Persist | 判断资金方向是否持续 |
| Align | 检查资金方向与价格是否一致 |
| Cross | 使用其他资金或市场数据验证结论 |

数据按配置通过多个提供商获取。典型来源包括 Tushare、东方财富相关接口、AKShare 和 yfinance。系统支持回退与交叉验证，但不保证第三方接口始终可用、完整或无延迟。

历史建议在有足够交易日和价格数据后可以结算 5/20/60 日结果，用于复盘与校准。该过程是评测工具，不是盈利证明。

## 人工审核的学习闭环

```text
历史分析
   │
   ├── Resolve：补充可获得的实际市场结果
   ├── Meta-Eval：统计模式和偏差
   ├── Extract：提炼候选经验
   └── Skill Gen：生成版本化分析规则
                         │
                         ▼
                  pending_review
                         │ 人工审核
                         ▼
                       active
```

经验默认不会自动生效。只有审核通过的经验才会进入后续分析上下文，并且可以撤销或回溯版本。

## Web 界面

`python run_web.py` 同时启动两个本地服务：

| 地址 | 功能 |
|---|---|
| `http://127.0.0.1:8003` | 主界面：深度分析、选股、预测、策略、回测、顾问、历史和校准 |
| `http://127.0.0.1:8005` | K 线图表服务 |

端口由 `quantconclave/runtime_manifest.py` 统一管理，并可通过环境变量覆盖。

## 快速开始

要求 Python 3.10 或更高版本。

```bash
git clone https://github.com/guxuan1210/quant-conclave.git
cd quant-conclave

# 使用 uv
uv pip install -e .

# 或使用 pip
pip install -e .

cp .env.example .env
# 在 .env 中填写所选 LLM 的 API Key；A 股资金研究建议配置 TUSHARE_TOKEN。

python run_web.py
```

Windows PowerShell 可使用：

```powershell
Copy-Item .env.example .env
python run_web.py
```

## 配置

| 环境变量 | 用途 | 是否必需 |
|---|---|---|
| `DEEPSEEK_API_KEY` | DeepSeek 示例提供商 | 选择 DeepSeek 时必需 |
| `OPENAI_API_KEY` | OpenAI 提供商 | 选择 OpenAI 时必需 |
| `ANTHROPIC_API_KEY` | Anthropic 提供商 | 选择 Anthropic 时必需 |
| `GOOGLE_API_KEY` | Google 提供商 | 选择 Google 时必需 |
| `TUSHARE_TOKEN` | A 股资金流、财务及指数数据 | 推荐 |
| `HTTP_PROXY` / `HTTPS_PROXY` | 部分境外数据源代理 | 可选 |
| `NO_PROXY` | 国内数据源代理绕过 | 推荐 |

运行时设置使用 `QUANTCONCLAVE_*` 前缀，例如：

```dotenv
QUANTCONCLAVE_LLM_PROVIDER=deepseek
QUANTCONCLAVE_DEEP_THINK_LLM=deepseek-v4-pro
QUANTCONCLAVE_QUICK_THINK_LLM=deepseek-v4-flash
QUANTCONCLAVE_OUTPUT_LANGUAGE=Chinese
```

完整示例见 [.env.example](.env.example)。

## Python API

```python
from quantconclave.default_config import DEFAULT_CONFIG
from quantconclave.graph.trading_graph import QuantConclaveGraph

config = DEFAULT_CONFIG.copy()
config["llm_provider"] = "deepseek"
config["deep_think_llm"] = "deepseek-v4-pro"
config["quick_think_llm"] = "deepseek-v4-flash"

graph = QuantConclaveGraph(debug=True, config=config)
state, decision = graph.propagate("600519.SH", "2026-06-30")
print(decision)
```

## 项目结构

```text
quant-conclave/
├── quantconclave/
│   ├── agents/          # 分析、研究、交易、风险与管理角色
│   ├── graph/           # LangGraph 编排、裁决与学习闭环
│   ├── dataflows/       # 多提供商数据路由
│   ├── prediction/      # ML + LLM 预测
│   ├── backtest/        # 向量化回测适配层
│   ├── quant/           # 向量化计算、成本和指标
│   ├── sector_scan/     # 板块扫描与候选筛选
│   ├── advisory/        # 历史经验与校准工具
│   └── workspace/       # 统一持久化入口
├── web/                 # FastAPI 与 Web 界面
├── tests/               # 单元、集成与冒烟测试
├── chart_app.py         # 图表服务（默认 8005）
├── run_web.py           # 本地双服务启动入口
└── pyproject.toml       # Python 包元数据
```

## 兼容迁移

QuantConclave 在一个发布周期内保留旧版兼容入口：

- `capitalradar` CLI 是 `quantconclave` CLI 的兼容别名。
- `CAPITALRADAR_*` 环境变量在对应的 `QUANTCONCLAVE_*` 未设置时仍可读取。
- 旧数据目录和数据库可由迁移层发现并迁移到 QuantConclave 工作区。

新部署应统一使用 `quantconclave` 包、`quantconclave` 命令和 `QUANTCONCLAVE_*` 环境变量。

## 项目状态与限制

- 当前包版本：`0.2.5`。
- 回测引擎为向量化实现；部分接口保留旧返回结构以兼容已有调用者。
- 实时和历史数据质量取决于第三方提供商及用户权限。
- LLM 输出不具备确定性；重要结论应核对原始数据和公开披露。
- 历史回测和校准可能受到数据偏差、交易成本假设及过拟合影响。

## 许可证

项目使用 [Apache License 2.0](LICENSE)。

## 致谢与引用

QuantConclave 衍生自 [TradingAgents](https://github.com/TauricResearch/TradingAgents) 的多智能体金融交易研究框架，并针对资金流、多数据源、历史经验、预测、选股和回测工作流进行了扩展。

```bibtex
@misc{xiao2025tradingagentsmultiagentsllmfinancial,
  title={TradingAgents: Multi-Agents LLM Financial Trading Framework},
  author={Yijia Xiao and Edward Sun and Di Luo and Wei Wang},
  year={2025},
  eprint={2412.20138},
  archivePrefix={arXiv},
  primaryClass={q-fin.TR},
  url={https://arxiv.org/abs/2412.20138}
}
```
