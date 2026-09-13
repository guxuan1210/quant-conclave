# Design: 新增竞争对手 & 上下游新闻分析师 + 统一资本流向分析框架

Date: 2026-05-22

## 背景

现有 TradingAgents 项目包含 5 个分析智能体（Market、Sentiment、News、Fundamentals、Capital Flow），按先后顺序执行后进入辩论和决策环节。

产品定位：为散户提供的反向博弈分析工具。**主力资金是博弈对手**，所有分析模块必须围绕"主力在做什么、是否为散户设陷阱"这个核心。

## 统一逻辑框架

### 核心命题

主力资金（机构/游资/北向资金）是市场的定价者。散户的盈亏不取决于"公司好不好"，而取决于"是否站在主力的对立面"。**每个分析模块最终要回答：主力资金看到这些信息后会怎么操作？散户应该跟还是反？**

### 执行顺序调整

Capital Flow 从最后一个调整为**第一个执行**，作为后续所有分析的"真相基准锚"：

```
Capital Flow → Market → News → Sentiment → Fundamentals → Competitor → Partner
```

### 每个分析师输出的统一格式

每个分析师报告的末尾必须包含：

- **与资金流向的对照**：该领域数据是否与 Capital Flow 一致？不一致就是信号
- **主力操纵风险评估**：该领域是否存在主力设陷阱的信号（HIGH / MEDIUM / LOW）
- **主力最可能操作**：主力看到这些信息后的下一步动作
- **散户应对建议**：跟还是反？具体操作建议

---

## 7 个分析师设计

### 1. Capital Flow Analyst（主力资金分析）— 基准锚，第一个执行

定位：确定主力资金的真实动向，是所有后续分析的"真相基准"，不参照其他报告。

核心分析：
- 超大单/大单净流向（主力吸筹/洗盘/拉升/出货）
- 北向/南向资金趋势
- 融资融券变化
- 机构持仓变化
- 操纵手法识别（对倒、诱多、诱空、压盘吸筹等）

### 2. Market Analyst（市场技术分析）

围绕资本流：
- 技术形态是否与资金流向一致？不一致→主力可能在画线
- 关键突破是真突破还是假突破？（对照资金流）
- 主力是否利用技术分析制造散户跟风盘？

### 3. News Analyst（自身新闻分析）

围绕资本流：
- 利好时主力在出货还是加仓？利空时主力在砸盘还是吸筹？
- 新闻时间线与资金流向对照→是否存在"利好出货、利空吸筹"？
- 新闻是信息传递还是主力操作的工具？

### 4. Sentiment Analyst（市场情绪分析）

围绕资本流：
- 散户情绪与主力资金是否背离？背离越大陷阱越深
- 社交媒体舆论是否有人为引导痕迹？
- 极度乐观+资金流出=主力出货给散户；极度悲观+资金流入=主力吸筹

### 5. Fundamentals Analyst（基本面分析）

围绕资本流：
- 基本面数据是否被主力选择性解读？
- 基本面变化与资金流向的时间先后（主力是否提前知道？）
- 基本面叙事是主力讲故事用的素材还是真实价值锚？

### 6. Competitor Analyst（竞争对手分析）— **NEW**

围绕资本流：
- 竞争对手利好→资金是否从标的流向竞争对手？还是板块受益？
- 主力是否在板块内做轮动？（从A撤出进入B）
- 竞争对手负面是否被主力利用来打压标的吸筹？
- LLM 自动推断主要竞争对手
- 工具：复用 `get_news`、`get_global_news`
- 必须注入 Capital Flow 报告作为分析上下文

### 7. Partner/Supply Chain Analyst（上下游分析）— **NEW**

围绕资本流：
- 上游价格变化是否被主力提前交易？（信息不对称）
- 下游需求变化是否已在资金流中体现？
- 产业链上资金是整体流入还是流出？
- 主力是否利用产业链某个环节的新闻在另一个环节设陷阱？
- LLM 自动推断主要合作伙伴和上下游
- 工具：复用 `get_news`、`get_global_news`
- 必须注入 Capital Flow 报告作为分析上下文

---

## 技术实现清单

### 新增文件

| 文件 | 内容 |
|---|---|
| `tradingagents/agents/analysts/competitor_analyst.py` | 竞争对手分析师节点 |
| `tradingagents/agents/analysts/partner_analyst.py` | 上下游合作分析师节点 |

### 修改文件

| 文件 | 变更 |
|---|---|
| `tradingagents/agents/utils/agent_states.py` | AgentState 新增 `competitor_report`、`partner_report` |
| `tradingagents/agents/__init__.py` | 导出 `create_competitor_analyst`、`create_partner_analyst` |
| `tradingagents/graph/analyst_execution.py` | 新增 `competitor`、`partner` 的 `AnalystNodeSpec` |
| `tradingagents/graph/conditional_logic.py` | 新增 `should_continue_competitor`、`should_continue_partner` |
| `tradingagents/graph/setup.py` | 注册新 factory、调整 graph 边、调整默认顺序（capital_flow 最先） |
| `tradingagents/graph/propagation.py` | 初始化 `competitor_report`、`partner_report` |
| `tradingagents/graph/trading_graph.py` | 新增 tool_nodes、更新 `_log_state`、更新默认 `selected_analysts` |
| `web/stream.py` | 更新 `ANALYST_REPORT_KEYS`、`ANALYST_NAMES`、`_STAGE_COLORS`、`generate_markdown` |
| `web/app.py` | 更新 API 默认 analysts 参数为新的 7 项顺序 |
| `tradingagents/agents/analysts/market_analyst.py` | 注入 Capital Flow 报告上下文，改造 prompt 围绕资金流 |
| `tradingagents/agents/analysts/news_analyst.py` | 注入 Capital Flow 报告上下文，改造 prompt 围绕资金流 |
| `tradingagents/agents/analysts/sentiment_analyst.py` | 注入 Capital Flow 报告上下文，改造 prompt 围绕资金流 |
| `tradingagents/agents/analysts/fundamentals_analyst.py` | 注入 Capital Flow 报告上下文，改造 prompt 围绕资金流 |
| `tradingagents/agents/analysts/capital_flow_analyst.py` | 调整 prompt 定位为"基准锚"（不参照其他报告但为后续提供基准） |

---

## 风险点

1. **Prompt 长度膨胀**：注入 Capital Flow 报告全文可能导致 token 超限 —— 对策：注入摘要而非全文，或只注入关键判断
2. **LLM 推断竞争对手/合作伙伴准确度**：可能不够精准 —— 对策：在 prompt 中明确要求 LLM 先列出推断的名单，再做分析
3. **执行时间增加**：从 5 个分析扩展到 7 个 —— 用户可接受
