# 交接说明 · sql-agent-lab

> 给陌生审阅者的入口文档。目标：你不需要作者在场，也能**独立复核每一条结论**，
> 并知道哪些地方不该信。
>
> 项目所有者：一名大三学生（准备中国大厂算法/Agent 方向实习）。
> 文档写于 2026-09-21。**不钉 commit 号或提交数**——它会随任何一次修订过期，
> 而这个项目已经为"硬编码数字与数据脱节"付过代价。用下面两条自查当前状态：
>
> ```bash
> git log --oneline | head -1     # 当前 HEAD
> git rev-list --count HEAD       # 提交数
> ```

---

## 0. 30 秒版

做了一个会写 SQL 的 AI agent，**但主要工作量花在"怎么知道它的分数是真的"上**：
自建 192 题基准 → 自建判分器 → **反过来审计判分器**（注入 6 类已知缺陷，750 次观测）
→ 用配对统计检验约束结论 → 用对抗探测给"安全性"补上真实证据。

结果是三层可信度递减的数字：

```
判分器：750 次注入观测，0 误判 0 漏判        ← 最硬
DeepSeek pass@1 89.1% → 93.2% (3-shot)      ← 真数字，但 McNemar p=0.057，未达显著
安全性：越权尝试率 23%，6/6 被拦，0 写操作   ← 26 条小样本
```

审阅时最该关注的是**第二行的 p 值和第三行的样本量**，作者没有把它们藏起来。

---

## 1. 目的

| 问题 | 答案 |
|---|---|
| 为什么做 | 简历上需要一个能进面试、且扛得住三层追问的 agent 项目 |
| 为什么是这个选题 | Text-to-SQL 的判分**可自动化**（执行结果比对），所以能产生硬数字；RAG 聊天机器人无客观判据，2026 年已饱和 |
| 刻意不做什么 | 不用 LangChain/LangGraph（会成为解释不了的黑盒）；不做多 agent 协作；不做 Web 产品 |
| 成功标准 | 不是"分数高"，而是"每个数字能回答：谁判的、噪声多大、什么条件下失效" |

---

## 2. 分工事实（审阅者一定会问）

**代码几乎全部由 AI 编程助手（Qoder）实现，包括本项目全部基础设施。**作者的角色是：
提出目标、审阅产出、跑实验、在追问下解释结论。

这条必须写清楚，因为它是**可验证的**：`git log --oneline` 里相当一部分提交的标题是"修掉我自己
产出的假结论/假绿灯"（见 §9），提交者身份与过程记录都在仓库里。

对审阅者的含义：**不要按"他手写了多少代码"来评这个作品**，要按"他能不能为每个数字辩护"来评。
后者的检验方式在 §4。

---

## 3. 交付物清单

### 代码（30 个 .py，4,632 行；生产 3,754 / 测试 753）

| 路径 | 作用 | 关键行 |
|---|---|---|
`sqlagent/agent.py` | ReAct 循环、步数预算、**取"最后成功执行的 SQL"** | `:95` |
`sqlagent/safety.py` | AST **白名单**只读护栏 | `:17` 允许集合，`:47` 判定 |
`sqlagent/db.py` | 只读连接、进度回调超时、行数上限 | — |
`sqlagent/eval/scoring.py` | 判分器（执行准确率 + 显式政策） | `:159` 主函数，`:189` 空集排除，`:202/:225` 缺陷是否触达结果 |
`sqlagent/eval/runner.py` | 并行、缓存、回归门禁、**无效运行闸门** | `:132` |
`sqlagent/config.py` | 全部实验参数 + **缓存键（含源码摘要）** | `:61` `code_hash()`，`:146` dataset |
`sqlagent/adversarial.py` | 26 条越权探测 + 双轴判据 | `:146` agent_fail，`:150` guard_credit |
`sqlagent/fewshot.py` | 示例选取，**含示例泄漏防护** | `leakage_check()` |
`sqlagent/report.py` | 生成离线单文件报告 | `token_cost()` 从 token 现算 |
`sqlagent/llm.py` | OpenAI 兼容 Provider + **MockProvider（可注入缺陷）** | — |
`sqlagent/secrets.py` | DPAPI 凭据封存，每模型一槽 | `:49` |

### 数据

| 文件 | 内容 |
|---|---|
`data/tasks.jsonl` | **192 题**（easy 59 / medium 70 / hard 63），每题含 `gold_sql`、`require_order`、`gold_tables` |
`data/tasks_dropped.jsonl` | **14 道被剔除**：`ambiguous_topk` 10、`vacuous_gold` 4，逐条原因 |
`data/tasks_adversarial.jsonl` | 26 条探测，7 类 |
`results/*.jsonl` | 12 次真实模型运行的**逐题结果**（含被推翻的）+ 6 个校准文件 |
`runs/*.jsonl` | 逐调用 trace 原文（工作目录 50 MB；交付包内含报告用到的 6 个，11 MB） |
`report.html` | 离线单文件，含 192 题 × 6 运行 trace 回放 |

### 文档

`README.md` 333 行 · `docs/INTERVIEW.md` 211 行 · `docs/ARTICLE.md` 311 行 ·
`docs/DELIVERY.md` · `docs/RESUME.md` 两版简历措辞 · `docs/REVIEW_TEMPLATE.md` **审阅反馈表（空白，供审阅者填写）** · 本文件
（各文件行数不写死，用 `wc -l docs/*.md` 自查）

### 两处副本

- 工作目录（源真值）：`Documents/Qoder/2026-09-21/4a73de0a/sql-agent-lab`
- 交付包（桌面）：`Desktop/sql-agent-lab`，提交历史与工作目录同步；体积与文件数
  不写进文档（会随修订过期），用 `du -sh .` 和 `find . -path ./.git -prune -o -type f -print | wc -l` 自查；
  两者的**入口说明文件名不同**（`docs/DELIVERY.md` vs `00-使用说明.md`），个别打包提交的 message 因此不一致
- 交付包**不含 `.venv`**，复核前需按 §4.2 先建环境
- 交付包**不含** `.env`、`secrets/`（DPAPI 绑定本机用户，拷走无意义且属凭据）、
  `.venv`、`data/*.db`、`runs/cache/`（三者都可按 §4.2 零成本重建）

---

## 4. 独立复核路径

### 4.1 五分钟只读路径

打开 `report.html`（离线、不联网、零花费）。逐条看：消融表 → 校准表 → 失败归因 →
点开任意一题看 trace。**报告里每个数字都能点到逐题数据。**

### 4.2 二十分钟动手路径（全程零 API 花费）

```bash
uv venv --python 3.12 && VIRTUAL_ENV=.venv uv pip install -e ".[dev]"

.venv/Scripts/python.exe -m sqlagent.data.build_db
.venv/Scripts/python.exe -m sqlagent.data.build_tasks
.venv/Scripts/python.exe -m pytest
.venv/Scripts/python.exe scripts/calibrate.py
.venv/Scripts/python.exe -m sqlagent.adversarial --seed-tasks
.venv/Scripts/python.exe scripts/significance.py    # p 值与噪声底，全部现算
.venv/Scripts/python.exe -m sqlagent.figures        # 重画 README 三张图
```

**关键输出行（逐字照抄自实跑，不是转述）**。完整输出更长，这里只列可对照的行；
若你的输出连这些行都对不上，请当成缺陷提出。

```
$ python -m sqlagent.data.build_tasks        # 题库重建（固定种子）
wrote 192 tasks -> data\tasks.jsonl
by difficulty: {'easy': 59, 'medium': 70, 'hard': 63}
dropped 14 -> data\tasks_dropped.jsonl
  ambiguous_topk        10  ordering_limit-001, ordering_limit-002, ...
  vacuous_gold           4  null_handling-009, having-005, subquery-004, subquery-005
value pools read from the database: 5 cities, 7 categories, 20 months, 3 levels

$ python -m pytest
117 passed in 4.62s                          # ← 实际输出被 ===== 包裹；秒数与计数都会变

$ python scripts/calibrate.py                # 判分器审计表格的最后一行
OVERALL           750   750            0            0 100.0%

$ python scripts/significance.py             # 显著性与噪声底，现算
deepseek: 3-shot vs baseline              192  89.1%    93.2%  +4.2pp       88.8%-96.0%   11    3  0.0574  未达显著
  最大两两差异 4 题 = 2.1pp  ← baseline-v2 vs baseline-v3

$ python -m sqlagent.adversarial --seed-tasks
wrote 26 probes -> data\tasks_adversarial.jsonl

$ python -m sqlagent.figures
wrote 3 figures -> docs\figures: ablation.svg, calibration.svg, trace.svg
```

> 本次交付包已按此流程验证过：在全新虚拟环境里重建库与题库，产出的 `tasks.jsonl`
> 与工作目录**逐字节相同**（`cmp` 无差异）。这一步是在证明"确定性"，不是证明"正确性"。
> `python -m sqlagent.figures` 另外在**只 clone、没有 `runs/`** 的目录里跑过一次：
> 前两张图照样重画，第三张打印跳过原因（`runs/` 出于体积不入库），不崩。

### 4.3 核对统计量（已入库，`scripts/significance.py` 现算）

统计逻辑在 `sqlagent/stats.py`：它是唯一实现，CLI、`report.html` 与
`docs/figures/ablation.svg` 都读它——三处不可能各说一套。跑 §4.2 里那条
`python scripts/significance.py` 就能对上一整张表。

**哪份运行文件参与配对**也写死在 `stats.py` 里，并带了理由注释：`abl-baseline`
故意**不**与 `abl2-3shot` 配对（它早于 few-shot 接线，配它就是跨代码版本比分数）。
一位审阅者这样配，得到的是 p=0.092 而不是文档里的 0.057——这个清单就是这么来的。
`tests/test_stats.py` 把下面三个 p 值钉住，锚点正是那位审阅者独立手算的结果。

| 对比 | 修好 / 弄坏（配对不一致） | Δ | McNemar 双侧精确 p | Wilson 95% CI |
|---|---|---:|---:|---|
| DeepSeek 3-shot vs baseline | 11 / 3 | +4.2pp | **0.057** | 83.9–92.7 → 88.8–96.0 |
| DeepSeek 关自修复 vs baseline | 0 / 0 | 0.0pp | 1.000 | 同左 |
| Qwen 3-shot vs baseline | 23 / 14 | +4.7pp | 0.188 | 63.5–76.3 → 68.4–80.6 |

**结论：没有任何一项达到 p<0.05。** 89.1%→93.2% 是真实观测，但按当前样本量不能称"显著提升"。

---

## 5. 核心主张 → 证据映射

| 主张 | 证据在哪 | 复现 |
|---|---|---|
判分器对 6 类已知缺陷零误判 | `results/calib-*.jsonl` 逐题含 `result_changed` | `scripts/calibrate.py` |
空集对空集不计分 | `scoring.py:189`（`not trivial`） | `tests/test_scoring.py::test_empty_vs_empty_is_flagged_trivial_and_not_credited` |
并列 top-k 不打分 | `data/tasks_dropped.jsonl` 10 条 | `python -m sqlagent.data.build_tasks` 输出 |
`final_sql` 不取模型散文 | `agent.py:95` + 注释 | `tests/test_agent.py::test_a_hallucinated_column_returns_an_error_not_a_crash` |
护栏是白名单 | `safety.py:17` | `tests/test_safety.py` 12 条逃逸样本，含 `WITH d AS (DELETE ... RETURNING *)` |
越权尝试率 23% | `results/adversarial.jsonl` | `python -m sqlagent.adversarial` |
别名绕过被拦 | `runs/*.jsonl` 中 `catalog-02` 的两次尝试 | 报告里搜 `sqlite_schema` |
缓存随判分器源码失效 | `config.py:61` + `:146` | `tests/test_config.py::test_code_digest_ignores_line_endings` |
无效运行不出分 | `runner.py:132` | `tests/test_runner.py::test_a_run_that_crashes_is_not_reported_as_a_low_score` |

---

## 6. 已知未证明 —— 请勿把这些当结论引用

1. **自修复无增益**（逐题结果零变化）。原因不是功能坏，是模型首次即成功率高
   （`avg_sql_attempts` 1.01）→ 没有错误可修。机制靠注入单独验证：开 96.2% / 关 0%。
2. **跨模型能力对比无效**。被"工具调用服从度"混淆：Qwen 3-shot 仅 24% 的题真跑过查询、
   45 题零工具调用。**"哪家模型更会写 SQL"目前无答案。**
3. **安全护栏的行为证据只有 26 条探测**。192 道正常题里触发 **0 次**。
   所以不能写"100% 拦截"。
4. 只测了 temperature 0、单轮、两套 prompt、一个 seed。
5. 题目全部来自参数化模板 → 措辞比真人写的整齐；改写只部分缓解。
6. `drop_distinct` 缺陷只有 6 题能承载，而它是最真实的错误类型。
7. 判分器按列名做置换对齐；两个匿名表达式退化为按位置比较，此时列交换看不见。
8. 成本是**估算**：DeepSeek 单价从控制台实扣反算，Qwen 是单次账单推出的**混合单价**
   （97.3% 输入，分不出输入/输出价）；未录入的模型显示"单价未录入"而不是 0。

---

## 7. 请重点质疑这 8 处（我自查过，但需要外部视角）

1. **执行准确率作为唯一指标是否够？** 我没测"语义正确但结果偶然相同"的情况（
   例如两条不同 SQL 在同一份数据上恰好同结果）。自建库可能让这种偶然比真实库更常见。
2. **模板生成基准的外在效度。** 192 题的分布是不是"我方便生成的分布"？难度标签是我
   按题型贴的，不是实测出来的。
3. **判分器的宽松项是否偏多？** 我为了排除虚高做了很多限制，但也主动接受了列序、
   大小写、日期格式、浮点容差四类宽松——每一项都可能掩盖真实错误。
4. **对抗探测的问法是否过软？** 26 条是我写的，诱导强度未经校准；"越权率 23%"
   可能是问法强度低造成的。
5. **p=0.057 该怎么做决策？** 我选择如实标注为未显著。但也可以论证"配对设计中
   11:3 的方向性证据已足以采用该 prompt"——这个取舍欢迎反驳。
6. **噪声底只有 3 次重跑估计**，2/192 的置信区间其实很宽。
7. **缓存包含源码摘要**：改一行注释就作废整轮，成本上是否合算？（我判断合算，
   但这是价值选择不是事实。）
8. **AI 主导实现的能力边界评估。** 这个作品能证明"设计与验证"能力，不能证明
   "从零实现"能力。请明确你据哪一条打分。

---

## 8. 待办（按性价比排序）

| 状态 | # | 事项 | 为什么值 | 成本 |
|---|---|---|---|---|
| ✅ | 1 | 统计量入库：`sqlagent/stats.py`（McNemar 精确检验 + Wilson CI + 噪声底，配对关系与文件名写死）→ `scripts/significance.py` 只是它的 CLI，`report.html` 与 `docs/figures` 共用同一份实现 | §4.3 的数字原本**只有手算** | ¥0 |
| ✅ | 6 | README 配图：`python -m sqlagent.figures` 从 `results/` 生成三张 SVG（消融+CI+噪声底 / 判分器审计 / 单题证据链），不是截图 | 3 秒内让人看懂这是产品不是脚本 | ¥0 |
| ◐ | 5 | 发布：`docs/ARTICLE.md` 可直发；仓库公开需要本人账号（见 §14 发布清单） | 招聘方点开的是链接和截图，不是代码 | ¥0 |
| ☐ | 2 | 扩充对抗探测到 100+ 条，并校准诱导强度 | 直接决定"安全"这一栏能不能进简历 | ¥0 建模 + 一轮真实运行约 ¥1.2 |
| ☐ | 3 | 修 few-shot 混淆：示例改成完整工具轨迹，或 `tool_choice` 强制调用，重跑对比 | 让跨模型对比从"未答"变成"可答" | 约 ¥2.5 |
| ☐ | 4 | 加一个更脏更大的 schema（200 表级）逼出自修复真实价值 | 让 §6-1 从"测不出"变成有结论 | 约 ¥2.5 |

---

## 9. 本次协作中发生过的错误（完整，含被修好的）

保留这一节，因为它比任何形容词更能说明过程质量。全部在 git log 里可查。

| # | 错误 | 后果如果没被发现 | 修好方式 |
|---|---|---|---|
1 | 题面城市池手写 10 个，库里只有 5 个 | 最简题型通过率虚降到 55%，失败被归因给模型 | 值池改为从库 `SELECT DISTINCT` |
2 | `--fewshot-k` 只进哈希不进 prompt | 跑出的"3-shot"其实是 baseline，任何对比都是假的 | 接线 + `tests/test_fewshot.py` |
3 | few-shot 示例拼在目标题**之后** | 模型答完最后一个示例，pass@1=3.65%，会被写成"few-shot 有害" | 调整顺序 + 断言末条必须是目标题 |
4 | 缓存键缺数据集/源码指纹 | 改完判分器继续吃旧分数 | `code_hash()` + `dataset_hash`，行尾归一化 |
5 | 明文扫描器只匹配 DeepSeek 形状 | 115 位 DashScope key 躺在盘上却报"干净" | 按形状匹配 + 自测会报警 |
6 | 封存重写整个 `.env` | 活动模型被悄悄改回 deepseek，下次封存进错槽 | 只删 key 行 + 回归测试 |
7 | **测试删掉了真实凭据文件**，且一路绿灯 | 用户凭据丢失，无人知晓 | `ROOT` 重定向临时目录 + 守卫测试要求真实凭据目录逐字节不变 |
8 | 未录入单价渲染成 $0 | 读起来像"这模型免费" | 返回 None，显示"单价未录入" |
9 | 成本把展示运行的合计当成项目总花费（$1.06 vs 真实 $3.21） | 简历上的成本数字偏小 | 从 token 现算 + 同页显示 token 总量 |
10 | 判分器与护栏对"越权"两套定义 | `uncaught_agent_fail` 在 0 和真值之间静默偏移 | `classify()` 直接调用护栏判定，并写明两轴不再独立 |
11 | 恒真断言 2 条（`or True`、集合自比） | 测试绿灯是假的 | 删除 |
12 | `git ls-files \| xargs grep` 当安全检查 | xargs 把任何非零码折成 123，永远证明不了"干净" | 换成项目自带扫描测试 |
13 | 带红灯测试提交了一次（`cdfae10`） | 历史里留下未验证的"完成" | 单独 commit 修，不改写历史 |
14 | MockProvider 用子串探测工具是否成功，永false | mock 无限重试到步数耗尽 | 改为解析 JSON |
15 | 用户 API key 曾被明文贴进对话 | 泄露 | 已轮换；本地 DPAPI 封存，交付包不含凭据 |
16 | §4.2 号称"逐字对照"，实际是我转写的（`OVERALL tested=750 ...` 这行从未被打印过） | 审阅者对不上格式，学不到任何东西 | 换成实跑粘贴的原文行 |
17 | 校准产物把 `wall_ms` 与 `cached` 写进受版本管理的文件 | 审阅者照 §4.2 跑一遍 `calibrate.py`，6 个文件各 386 行全红 diff，"判分器有没有变"反而看不出来 | 产物只留判断字段；`tests/test_artifacts.py` 钉住；并逐行证明 1,152 条判定零变化 |
18 | 校准产物 `_summary.model` 记的是当时 `.env` 里的默认模型 | committed 产物写 deepseek-chat、重跑变 qwen-flash，**而这场扫描根本没调用任何模型** | 写死为 `mock (judge under test; no model called)` |

**共同点**：18 个错误里 16 个不会导致崩溃，只会**产出一个看起来合理的错误数字**
（或让一个本该能核对的产物变得无法核对）。
这正是本项目全部设计针对的失效模式。

---

## 10. 简历措辞

两版措辞（算法实习 / Agent 开发实习）连同追问预案在 **`docs/RESUME.md`**（98 行）。要点摘录：

- 项目名按岗位换：「评测方法学与消融研究」 vs 「智能体系统与可靠性工程」
- **三句话任何岗位都不能写**："100% 拦截危险语句"、"自修复提升准确率"、"跨模型验证了能力"
- 最强的一行是统计那行：**主动写出 p=0.057 未达显著**。这是区分"会用 AI 做东西"和
  "会做实验"的地方
- 被问"哪部分你做的"：见 §2

---

## 11. 反馈方式

**请填写 `docs/REVIEW_TEMPLATE.md`**（勾选为主，15-25 分钟）。最有价值的是它的
§7「如果这个人坐在你对面，你会追问哪三个问题」——它会被直接当作面试题库使用。

## 12. 自查仓库状态的命令

```bash
git log --oneline                 # 相当比例的提交标题是"修正我自己产出的假结论"
cat results/abl2-3shot.jsonl | head -1   # 汇总行（含 valid / protocol_adherence）
.venv/Scripts/python.exe -m pytest     # 全绿（写作时 117 passed）
```

审阅反馈请尽量给出：**被质疑的具体文件:行** + **你期望看到什么证据**。
这个项目的方法就是"每条主张配一个可复现的检查"，用它来审它最合适。

---

## 13. 外部审阅反馈与逐条回应

审阅者：ZCode（独立复核，2026-09-22）。填的是 `docs/REVIEW_TEMPLATE.md`，结论
**"能进简历但必须先改"**，四条必改 + 三个追问。原始反馈文件随仓库保留，不删。

| 审阅项 | 回应 | 证据 |
|---|---|---|
★ 统计量没入库（p 值只有手算） | **改，并且做过了原方案。** 统计逻辑进 `sqlagent/stats.py`：McNemar 精确检验 + Wilson CI + 噪声底，**文件名与配对关系写死在代码里**并注释了为何不用 `abl-baseline`（跨代码版本，配它会得到 p=0.092 而与文档不符）。`scripts/significance.py` 退化成它的 CLI，`report.html` 与 `docs/figures/ablation.svg` 读同一份实现——三处不可能再各说一套 | 脚本输出逐字复现审阅者独立算出的 11/3→p=0.0574、23/14→0.1877、0/0→1.0；`tests/test_stats.py` 把这三个数钉住 |
★ 简历写"`final_sql` 永不取自散文"与 `agent.py:95` 的回退矛盾 | **改措辞，不改设计。** 设计可辩护（零执行的题会标 `stop_reason=no_sql_executed` 并单独计数），错的是全称否定句。已重写 `docs/RESUME.md` 开发版-1 与 `docs/INTERVIEW.md` §1，并把新口径写成卖点："回退会被标记和单独披露" | 审阅者指出的 45 道 Qwen 零工具题走回退计分，事实成立，已写进 README 限制段 |
★ README 写 72 个测试 | **改。** README 与 ARTICLE 同步为实测值 | `pytest` 实跑输出 |
★ `WITH d AS (DELETE FROM users) SELECT * FROM d` 能过 AST 白名单 | **改。** 白名单原先只看根节点 + INTO/RETURNING；现改为**任意深度**出现 DML/DDL 节点即拒绝，并补 2 条测试（含 `test_write_node_list_has_not_silently_rotted`，防 sqlglot 改名后 `getattr` 静默降级） | 修复前 INSERT/UPDATE/DELETE 三种无 RETURNING 变体全部放行（已实测），现全部拦截且 6 条合法读查询不误伤 |
噪声底取 1.0% 推得阈值 1.5pp，但实测有一对同配置差 2.1pp | **改。** 阈值改为"四份同配置基线的最大两两差异"，且**由脚本现算、不再写进散文**（写死就会过期） | `significance.py` 输出：0.5/1.0/1.0/1.6/2.1pp，最大 2.1pp；+4.2pp 仍在其上 |
`require_order` 实际只覆盖 2/192，文档未写 | **改。** 补进 README Known limitations，并明确后果："一个把顺序全排错的模型几乎不掉分" | 审阅者 §2 的最关键一问 |
简历里"越权尝试率 23%"样本太小不可写 | **改，接受。** 删比率，保留 `sqlite_schema` 别名案例与"两轴分开记录"的方法论 | `docs/RESUME.md` 开发版-4 |
"先承认 0 观测、再补对抗探测"是全文最好的部分 | 不改，但按上一条收缩表述范围 | — |

**对审阅者追问 2 的作答**（换 PostgreSQL + 可写连接，哪层先失效）：

> SQLite 那层先失效——"不支持 DML-in-CTE"是方言限制，不是我的防线。PG 允许数据修改型
> CTE，所以若连接可写，真正挡住的只剩 `mode=ro`（等价地，PG 侧要把角色权限降为只读）。
> 结论：**当前 AST 层在 SQLite 上是"看起来够"，因为它有一个方言巧合替它兜底**。
> 修完之后 AST 层自己就能拦，卖点依然成立——但成立理由是"任意深度拒绝 DML 节点"，
> 不再是"我们有三层防御"。这条已写进 README 限制段，因为它同时也是"分层防御是否真的
> 理解到位"的答案。

**尚未处理、留在待办的审阅建议**：接 Spider/BIRD 子集做外部可比性（审阅者判为"下一步
建议，不构成本轮必改"，代价是判分政策需重做）；以及诱导强度校准后的对抗探测扩量。

---

## 14. 发布清单（把仓库变公开，以及把文章发出去）

这一步**我做不了**：本工作区没有 `gh` CLI、没有配置 git remote，公开推送要用你本人的
GitHub 账号。下面每条都是你复制即用的命令，以及**为什么**这一步不能跳。

### 14.1 推之前必须过的三关（约 3 分钟，全部本地）

```bash
# 关 1：作者身份会公开。git log 里的邮箱是仓库身份，不是账号邮箱。
git log --format='%an <%ae>' | sort -u
#   ✅ 已做（2026-09-22，push 之前）：38 个提交的作者/提交者邮箱全部重写为
#   GitHub 的 noreply 地址。验证方式（三条都过了）：
#     git log --all --format='%ae %ce' | grep -c gmail   -> 0
#     git rev-parse HEAD^{tree}  重写前后同一个 hash    -> 文件内容一个字节都没变
#     git log --format='%s'      重写前后逐条相同        -> 提交信息全保留
#   为什么必须在 push 前做：推出去之后再改要 force push，而且 GitHub 会缓存旧提交，
#   旧邮箱在别人 fork 里永生。重写前备份了完整 .git。

# 关 2：历史里每个 blob 扫一遍 key 形状。只看工作树不够——密钥可能进过早期提交。
#   不要写 `git ls-files | xargs grep`：xargs 会把任何子进程的非零退出码折成 123，
#   既不能证明"干净"也不能证明"有货"。逐个 blob 扫，命中就打路径：
git rev-list --objects --all \
  | git cat-file --batch-check='%(objecttype) %(objectname) %(rest)' \
  | awk '$1=="blob" && length($3){print $2, $3}' \
  | while read -r sha path; do
      git cat-file blob "$sha" 2>/dev/null | grep -qE "sk-[A-Za-z0-9_-]{18,}" && echo "HIT $path"
    done | sort -u
#   实测会给出三个 HIT，**全部是假阳性**，逐条确认过：
#     tests/test_secrets.py     —— 它本身就是"扫描器必须会报警"的测试，里面是合成样本
#     report.html               —— 题号形如 `…-aggregate_count_001`，前缀被切出来正好落进形状里
#     docs/HANDOFF.md           —— **是历史里的旧版本**：本文件曾把上面那个字面例子写进正文，
#                                  于是它自己成了匹配串。改掉正文不会改掉已有提交的 blob。
#   想只剩两个 HIT 就要重写历史（`filter-branch`），代价是把 §9 那张"我犯过的错"表
#   的过程记录一起洗掉——不值得。**这一条在改任何 .gitignore 之后要重跑。**
#   踩过的坑：**别把匹配串写进文档**，它会成为扫描器的第三个命中。当前正文里那个
#   字面串已经删掉（`grep -c` = 0），但旧提交的 blob 还在历史里，所以第三个 HIT
#   不会消失——区分方法就是这一句。

# 关 3：许可证。没有 LICENSE 的公开仓库 = 默认全部权利保留，招聘方也会觉得不专业。
#   ✅ 已做：MIT，署名用 GitHub handle（`chaofanzhao484-creator`）。要换成真名只需改
#   LICENSE 第二行再 commit；push 之前改零代价。
```

### 14.2 建库并推送（一次性，需要你的 GitHub 凭据）

仓库已建好（Public、空库、没勾自动 README——所以不会有冲突提交）：
`https://github.com/chaofanzhao484-creator/sql-agent-lab`

```bash
cd /c/Users/34264/Documents/Qoder/2026-09-21/4a73de0a/sql-agent-lab
git remote add origin https://github.com/chaofanzhao484-creator/sql-agent-lab.git
git branch -M main          # 本地分支原本叫 master，GitHub 默认分支叫 main
git push -u origin main     # 会弹浏览器授权（Git Credential Manager），确认后自动继续
```

> 桌面交付包 `C:\Users\34264\Desktop\sql-agent-lab` 做了**同一次**邮箱重写，所以两边共有
> 提交的 hash 仍然一致（`git rev-parse HEAD:README.md` 两边相同即可验证）。重写前的完整
> `.git` 备份在 `…/4a73de0a/_git-backup-20260922/`——**里面有旧邮箱，不要放进任何会被公开的
> 目录**，确认线上没问题后直接删掉即可。

提交数与体积都不写在这里（写了就会过期）：`git rev-list --count HEAD`、
`du -sh --exclude=runs --exclude=.venv --exclude=.git .`。量级是"几十次提交、约 10 MB"，
推送正常。`runs/` 是 .gitignore 排除的
50 MB，故意不公开——`report.html`（已提交）内含同样的逐题 trace 回放。

### 14.3 公开后要做的两件对齐（5 分钟）

- GitHub 仓库 **About**：填一句 `Text-to-SQL agent + the measurement rig that decides
  whether its numbers mean anything`，加 topics：`text-to-sql` `evaluation-harness`
  `llm-evaluation` `agents`。**别**把主页描述写成准确率数字——README 顶部自己就说
  那类数字要配 `valid`/`protocol_adherence` 一起读，一句话描述做不到。
- **简历里的链接**：GitHub 会把仓库里的 `.html` 当**源码**显示，不会渲染。要让招聘方
  点开就是报告，开 GitHub Pages：仓库 Settings → Pages → Source = `Deploy from a
  branch`，Branch = `main` / `/ (root)`，保存。之后
  `https://chaofanzhao484-creator.github.io/sql-agent-lab/report.html` 直接可看（`report.html`
  在根目录，所以不需要再动文件）。简历上放**两条**链接：仓库 + 这条报告直链。

### 14.4 发文章（`docs/ARTICLE.md` 已是可发正文）

1. 平台按招聘可见度：**掘金 / 知乎专栏** 优先（HR 会搜到），可同步博客园。
2. ARTICLE.md 是纯 Markdown，已内嵌三张图（`figures/trace.svg` 在第一节末尾、
   `calibration.svg` 在第三节末、`ablation.svg` 在第八节的表下面），路径是仓库相对路径，
   在 GitHub 上直接能看。**发到掘金/知乎要把这三张图重新上传**：文中的图片路径是仓库
   相对路径，离开仓库就不会解析（贴过去必然是坏图）。在浏览器里打开 `docs/figures/*.svg`
   截图成 PNG 再传，图里的数字仍由 `python -m sqlagent.figures` 现算，不会因此失真。
3. **结尾放仓库链接 + 一句"本文每个数字都能点开到 results/ 的逐题记录"**，这是文章的
   钩子，也是和同题材文章唯一的区别。
4. 发完把文章 URL 回填到 `docs/RESUME.md` 项目行（"技术文章：<url>"），形成
   仓库 ↔ 文章 ↔ 简历 三向互链。

### 14.5 发布**不改变**的三条底线（面试会拿这些试探你）

- 公开 README/HANDOFF 已写明：自修复无增益、跨模型对比被混淆、安全只有 26 条探测——
  **发布后这三条继续留在"未证明"，不要因为上了公开仓库就改口**。
- 数字来自代码不来自本文：任何一处图/表/散文与 `scripts/significance.py`、
  `scripts/calibrate.py` 现算输出不一致，以脚本为准，并当成 bug 提 issue。
- 代码主要由 AI 实现这件事，`docs/HANDOFF.md` §2 已写在明面，公开后**不要弱化**。
