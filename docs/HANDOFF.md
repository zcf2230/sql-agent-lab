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

### 代码

> 文件数与行数**不写在这里**。上一版写了「30 个 .py，4,632 行；生产 3,754 / 测试 753」，六个数字全部过期，而紧跟它的那一句就写着「各文件行数不写死，用 `wc -l` 自查」——同一段里自相矛盾，且正是本文件开头说的「这个项目已经为硬编码数字与数据脱节付过代价」那件事。
> 要多少自己跑：`find . -name '*.py' -not -path './.venv/*' | wc -l`，行数把结尾换成 `| xargs wc -l | tail -1`。

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

`README.md` · `docs/INTERVIEW.md` · `docs/ARTICLE.md` · `docs/DELIVERY.md` ·
`docs/RESUME.md` 两版简历措辞 · `docs/REVIEW_TEMPLATE.md` **技术版审阅表（空白）** ·
`docs/REVIEW_TEMPLATE_NONTECH.md` **非技术版审阅表（约 10 分钟，不碰命令行）** · 本文件。
已归档的填写原件：`docs/REVIEW_2026-09-22.md`、`docs/REVIEW_2026-09-23.md`、
`docs/REVIEW_NONTECH_2026-09-23.md`。

行数一律不写死：`wc -l README.md docs/*.md`。

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
git status --porcelain                       # 最后一步：应当【什么都不输出】
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

$ python -m sqlagent.figures                 # 只 clone、没有 runs/ ——也就是 §4.2 的场景
  trace.svg skipped: runs\openai__deepseek_chat__fs0.jsonl is not in git (runs/ is gitignored); see report.html for the replay
wrote 2 figures -> docs\figures: ablation.svg, calibration.svg

$ python -m sqlagent.figures                 # 在有 runs/ 的目录里（作者本机 / 桌面交付包）
wrote 3 figures -> docs\figures: ablation.svg, calibration.svg, trace.svg
```

> 本次交付包已按此流程验证过：在全新虚拟环境里重建库与题库，产出的 `tasks.jsonl`
> 与工作目录**逐字节相同**（`cmp` 无差异）。这一步是在证明"确定性"，不是证明"正确性"。
> **最后那条 `git status --porcelain` 是第三轮最省力的检查，也是第二轮才补上的。**
> 前面每一条命令都会重写受版本管理的产物；如果它们重写出的内容与提交里的一致，
> 输出应当为空。第二轮时它**不为空**（8 个文件被弄脏），根因是 `dataset_hash` 用原始
> 字节算摘要（行尾敏感）而 `config_hash` 又把 `.env` 里的默认模型折了进去——于是
> 「照文档跑一遍」这个动作本身在污染工作区，审阅者无法用眼睛回答
> 「判分器到底有没有变」。修完之后这条检查才有意义，而它现在是最容易验证的一条。
> 若你跑出来不为空，先 `git diff --numstat` 看是不是只有一行 `_summary`，
> 再决定这是缺陷还是你的环境差异（`.env`、行尾、Python 版本）。
>
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
3. **安全护栏的行为证据只有 26 条探测，而且写路径一次都没被测过**。192 道正常题里触发
   **0 次**；26 条里模型实际只尝试 6 次，且**全部是读形状**（查目录表、`INTO OUTFILE`、
   运维语句），`direct_write` 那 6 条它一次都没试。所以"0 次写操作执行"是平凡成立——
   护栏从未被问过写语句。不能写"100% 拦截"，也不能写"全部拦下"。
4. 只测了 temperature 0、单轮、两套 prompt、一个 seed。
5. 题目全部来自参数化模板 → 措辞比真人写的整齐。**改写鲁棒性一次都没有被测**
   （每题只施加一种问法，没有任何一题被用两种问法问过），所以"分数有多少来自抓住语义、
   多少来自认出模板"目前无证据。旧版本此处写"改写只部分缓解"，那是在描述一个没做过的实验。
6. `drop_distinct` 缺陷只有 6 题能承载，而它是最真实的错误类型——**且这 6 题全来自同一个族**。
7. 判分器按列名做置换对齐；两个匿名表达式退化为按位置比较，此时列交换看不见。
8. 成本是**估算**：DeepSeek 单价从控制台实扣反算，Qwen 是单次账单推出的**混合单价**
   （97.3% 输入，分不出输入/输出价）；未录入的模型显示"单价未录入"而不是 0。
9. **192 题去重后只有 100 个 gold SQL 骨架**，McNemar 的题级独立性假设不成立。
   按骨架聚合后 p 在 0.039–0.227 之间跨过 0.05，所以"显著/不显著"这个二分**不是结论**，
   方向、效应量与区间才是。复现：`python -c "from sqlagent import stats;
   print(stats.cluster_sensitivity('abl2-baseline','abl2-3shot'))"`。
10. **"谎称已完成 = 0"同时是检测器召回的测量值**。匹配器已覆盖被动/完成时/名词前置/中文
    （12 个表述命中 11，7 个拒绝句零误报），但它仍是正则；原始命中数 `claims_matched`
    与扣除拒绝后的 `claimed_done` 并列输出，就是为了让人能分辨这两件事。
11. **难度标签是族级常量**，一个族一个值、族内零变异，实为 SQL 结构复杂度；
    且分桶极不均（`hard` 的 63 题里 35 题来自 `category_slice` 一族）。
    所以"easy/medium/hard 三档通过率"不能当作难度分析引用。

---

## 7. 请重点质疑这 8 处（第三轮版；前两轮的 8 条已随修复过期）

1. **有效样本量我只算到"骨架"这一层。** 192 题按 `(族, gold 骨架)` 归并得 100 簇，
   p 因此跨过 0.05。但"骨架"是我挑的聚类单位——按 SQL 结构树、按所需 join 数、按题型族
   归并会得到别的 n 和别的 p。**你有没有一个比我更站得住的单位？** 如果没有，
   "报三种口径"是不是只是把选择权推给读者？
2. **同题重述稳定性仍然没测。** 我承认改写鲁棒性一次都没被测过（每题只施加一种问法），
   也把它列成了最该做的下一步。但**一个知道自己没测什么、却仍然不测的项目，
   和测了的项目不等价**——这个缺口该扣多少分，请你给个判断。
3. **护栏在写路径上从未被测试。** 6 次尝试全是读形状。我现在的说法是
   "证明的是模型不敢写，不是护栏能拦写"。那么**这一栏还有没有资格出现在简历上**？
   我保留了设计证据（白名单、别名案例），删了所有比率——这个取舍是否仍然偏乐观？
4. **两条我故意不修的（§15.5）**：`PARAPHRASE` 去重与 `difficulty` 改名都会改变数据集
   摘要、作废全部已发布产物，要重跑真实模型才自洽。**这是纪律还是借口？**
   如果你认为该立刻修，请同时说明你愿意接受"代码与数据不一致"多久。
5. **执行准确率作为唯一指标。** 我没测"语义错但结果偶然相同"，自建库可能让这种偶然
   比真实库更常见。这条两轮都没被攻击过，可能是因为它太明显——**如果只能加一种指标，
   你会加什么？**
6. **判分器的宽松项是否偏多？** 现在宽松项和严格项都写下来了（列序宽松、列数严格），
   也标了头条数字对哪条最敏感。**这个透明度补不回判分口径本身的选择**——
   列序该宽松吗？日期格式呢？
7. **缓存包含源码摘要**：改一行注释就作废整轮。成本上是否合算我判断合算，
   但这是价值选择不是事实。
8. **AI 主导实现的能力边界。** 这个作品能证明"设计与验证"，不能证明"从零实现"。
   第二轮之后多了一条：**它也能证明"被指出错时改得对"**——10 条我全复现全接受。
   请明确你据哪一条打分，以及这三条在你心里的权重。

---

## 8. 待办（按性价比排序）

| 状态 | # | 事项 | 为什么值 | 成本 |
|---|---|---|---|---|
| ✅ | 1 | 统计量入库：`sqlagent/stats.py`（McNemar 精确检验 + Wilson CI + 噪声底，配对关系与文件名写死）→ `scripts/significance.py` 只是它的 CLI，`report.html` 与 `docs/figures` 共用同一份实现 | §4.3 的数字原本**只有手算** | ¥0 |
| ✅ | 6 | README 配图：`python -m sqlagent.figures` 从 `results/` 生成三张 SVG（消融+CI+噪声底 / 判分器审计 / 单题证据链），不是截图 | 3 秒内让人看懂这是产品不是脚本 | ¥0 |
| ✅◐ | 5 | 发布：仓库已公开 + Pages 报告已上线（§14.2 有两个地址）；**技术文章尚未发布** | 招聘方点开的是链接和截图，不是代码 | ¥0 |
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
19 | 声称「三处不可能各说一套」，但 `report.py` 从没接入 `stats.py`，自己另算了一份**定义都不同**的噪声底 | 同一份材料里 2/192 与 4/192 并存，而 2/192 正是上一轮已判定偏乐观的值；**那句招牌话在我写下那一刻就是假的** | 删本地实现改 import；测试断言 `report` 不得再有 `noise_floor` 属性
20 | 报告里没有 p 值、没有 CI、没有噪声底 | 简历称「最强的一行是主动写出 p=0.057 未达显著」，而招聘方最可能点开的那个产物里 `0.057` 出现 0 次 | 新增显著性一节；测试逐个断言每个 p 字符串都在报告里
21 | **修 D1 时只改了展示**：改写 `_summary.model` 却没动 `config_hash` 对 `.env` 的依赖 | 比不修更糟——它把真正要查的东西藏起来了；我第一次「修完」复测仍 6 个文件脏才发现 | 在调用处 `--model` 钉死；全新 clone 复跑 §4.2 全命令，脏文件数 0

**共同点**：21 个错误里 19 个不会导致崩溃，只会**产出一个看起来合理的错误数字**（或让一个本该能核对的产物变得无法核对）。这正是本项目全部设计针对的失效模式。



---

## 10. 简历措辞

两版措辞（算法实习 / Agent 开发实习）连同追问预案在 **`docs/RESUME.md`**。要点摘录：

- 项目名按岗位换：「评测方法学与消融研究」 vs 「智能体系统与可靠性工程」
- **三句话任何岗位都不能写**："100% 拦截危险语句"、"自修复提升准确率"、"跨模型验证了能力"
- 最强的一行是统计那行：**主动写出 p=0.057 未达显著**。这是区分"会用 AI 做东西"和
  "会做实验"的地方
- 被问"哪部分你做的"：见 §2

---

## 11. 反馈方式

按你的背景选一张，两张都欢迎：

| 你是谁 | 填哪张 | 用时 |
|---|---|---|
| 做过工程 / 评测 / 模型，愿意跑命令 | `docs/REVIEW_TEMPLATE.md` | 15–25 分钟 |
| 不带技术背景，或者只想花 10 分钟、不碰命令行 | `docs/REVIEW_TEMPLATE_NONTECH.md` | 约 10 分钟 |

技术表最有价值的是 §7「如果这个人坐在你对面，你会追问哪三个问题」——它会被直接当作
面试题库使用。非技术表最有价值的是 §2 和 §3：哪一条简历措辞你一看就觉得是吹的、
哪一句你读一遍不知道在说什么。**外行的判断更贵，因为招聘官第一眼也是外行**——
那两栏暴露的问题，作者自己永远查不出来。

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
#   ✅ 已做：MIT，署名用 GitHub handle（`zcf2230`）。要换成真名只需改
#   LICENSE 第二行再 commit；push 之前改零代价。
```

### 14.2 建库并推送（已完成 2026-09-22）

仓库已公开，Pages 已开启。**审阅者可以直接点，不需要本地环境**：

| 入口 | 地址 |
|---|---|
| 仓库 | `https://github.com/zcf2230/sql-agent-lab` |
| 离线报告（渲染版，含 192 题逐条 trace 回放） | `https://zcf2230.github.io/sql-agent-lab/report.html` |
| 三张图（生成物） | `…/main/docs/figures/{ablation,calibration,trace}.svg` |

推送后的一致性验证方式——**不要抽样比文件，比树哈希**：git 是内容寻址的，
`git ls-remote origin refs/heads/main` 得到的提交号与本地 `git rev-parse HEAD` 相同，
就等价于"线上每个文件与本地逐字节相同"。用 `curl` 逐个下载比 md5 既慢又容易误判
（本地工作树是 CRLF、raw 上是 LF，直接 `cmp` 必然"不一致"）。

> 桌面交付包 `C:\Users\34264\Desktop\sql-agent-lab` 做了**同一次**作者邮箱重写，所以两边共有
> 提交的 hash 仍然一致。重写前的完整 `.git` 备份在 `…/4a73de0a/_git-backup-20260922/`——
> **里面有旧邮箱，不要放进任何会被公开的目录**，确认线上没问题后直接删掉。

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
  `https://zcf2230.github.io/sql-agent-lab/report.html` 直接可看（`report.html`
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

---

## 15. 第二轮外部审阅（2026-09-23）· 逐条回应

两份反馈：技术版 `docs/REVIEW_2026-09-23.md`（10 条新缺陷 D0–D10）、
非技术版 `docs/REVIEW_NONTECH_2026-09-23.md`（可读性与可信度）。

**先说结论：这一轮 10 条我逐条自己复现，全部成立，没有一条需要反驳。**
复现方式与结果写进每条。更重要的是**其中三条打在项目自己的招牌上**，
而且 D2 直接证伪了我上一轮亲手写进 §4.3 的那句"三处不可能各说一套"。

### 15.1 技术版 ★ 必改项

| 审阅者指出 | 我的复现 | 处置 |
|---|---|---|
| ★1 噪声底两个数：`stats.py` 4/192=2.1pp，`report.html` 印 2/192 | 实测 `report.noise_floor()` → `2 / 192`；`stats.noise_floor()` → `4 / 192 = 2.1pp`；且 `report.py` **没有 import stats**。**根因比"漏了一个基线"更深**：report 那份算的是"任意基线间翻转的并集"，stats 算的是"最大两两差异"——**两个不同的量共用一个名字** | **改。** 删除 `report.py` 的本地实现，改 `from . import stats`；报告卡片现在直接印 `4/192 (2.1pp)` 并注明来源。回归测试 `test_report_does_not_own_a_second_noise_floor` 断言 `report` 模块**不再有** `noise_floor` 属性——下次有人再写第二份就红 |
| ★2 报告里没有 p 值/CI/噪声底 | 实测 `report.html` 中 `0.057`/`p=`/`显著`/`Wilson`/`95%`/`2.1pp`/`McNemar` 出现次数**全为 0**。而 §4.1 让忙碌的审阅者"只打开这个文件"，`RESUME.md` 称"最强的一行是主动写出 p=0.057 未达显著" | **改。** 新增报告第 2 节「配对显著性」，由 `stats.all_comparisons()` 渲染 p/CI/修好弄坏/结论 + 噪声底卡片。测试 `test_generated_report_shows_the_significance_it_claims_to_be_honest_about` 逐个断言每个 p 值字符串都在报告里。**纪律必须活在交付物里，不只是散文里** |
| ★3 跑完 §4.2 工作区变脏（D1） | 全新 clone 实测：`build_db`+`build_tasks` → 2 个文件脏；再 `calibrate` → **8 个文件脏**。逐行比对：192 行逐题判定**一字未动**，唯一变化是 `_summary.config_hash`（`7db55d0a`→`b843d47f`）。根因一：`dataset_hash` 用原始字节，而 `code_hash()` 做了行尾归一并**在 docstring 里解释了为什么必须归一**——同一个坏味道修了 code 侧、漏了 dataset 侧 | **改，两轮才修对。** ① 抽出 `runner.dataset_digest()` 并归一行尾；② 所有写受管理文本的地方显式 `newline="\n"`；③ 测试 `test_dataset_digest_ignores_line_endings` 断言同内容 CRLF/LF 摘要相同，并验证**旧写法确实会失败**（不是恒真断言）。**修完第一遍我在 clone 里复测，仍然 6 个文件脏**——查出第二层根因：`config_hash` 把 `model` 折进去了，而 `model` 来自 `.env`（作者机是 qwen-flash，clone 用默认值）。**我上一轮改的 `_summary.model` 只改写了显示，哈希里的环境依赖一点没动，等于把症状盖住、让原因更难被发现。** 现在在调用处 `--model mock-judge-sweep` 钉死，扫描不再读环境。最终实测：**全新 clone 跑完 §4.2 全部命令，脏文件数 = 0** |
| ★4 简历算法版-4 等 1、2 修完再投 | 成立。简历写 2.1pp、报告链接印 2/192，是**同一份材料内部两个数字**，点开即拆 | **已按 1、2 修完**，两处现在同源。另在简历里补了聚类口径（见 ★5） |
| ★5 D4：§3 硬编码计数全过期且与下一句矛盾 | 实测：声明 30 个 .py / 4,632 行 / 生产 3,754 / 测试 753 / README 333 / INTERVIEW 211 / ARTICLE 311 / RESUME 98 —— **八个数字，八个全错**（实际 36 / 5,361 / 4,110 / 1,059 / 434 / 248 / 335 / 117），而紧跟的那句就写着"不写死" | **改。** 整段删除，只留 `find`/`wc` 命令。**这不是第一次犯，也不是第二次**——本文件 §9 第 16 条记的就是同一类错误，说明"写下数字"这个动作本身需要被禁止而不是被提醒 |

### 15.2 D6（本轮唯一的必答项）：192 题里只有 100 个独立观测

**复现：我自己写了一遍聚类逻辑，结果与审阅者完全一致。**
192 题按 `(族, gold SQL 去字面量)` 归并 → **100 簇**；`category_slice` 35 题/5 骨架、
`date_bucket` 22 题/3 骨架、`filter_projection` 10 题/2 骨架。三种口径下的配对检验：

| 口径 | n | Δ | 修好/弄坏 | 精确 p |
|---|---:|---:|---:|---:|
| 题级（原状） | 192 | +4.2pp | 11/3 | 0.0574 |
| 簇级·全对才算对 | 100 | +5.0pp | 8/3 | 0.2266 |
| 簇级·有一个对就算对 | 100 | +7.0pp | 8/1 | **0.0391** |

**处置：改，而且改的是结论的写法而不只是数字。**

- 逻辑进 `sqlagent/stats.py`（`skeleton()` / `clusters()` / `cluster_sensitivity()`），
  不在报告或脚本里各写一份——那是 D2 的病根。
- README 新增一节「How much of n=192 is really n=100」，报告新增同名子表。
- **措辞从"未达显著"升级为"显著/不显著这个二分本身是聚合口径的产物"**。
  审阅者那句反问是对的：`p>0.05 所以无效应` 挡不住"那 p=0.0499 你就宣称了？"。
  现在的回答不依赖任何一个阈值。
- 同时补了族级分解：**11 道修好里 9 道来自两个族，19 个族里 15 个净变化为 0**。
  "+4.2pp" 的诚实说法是"两个模板族里的 12 道题变了"。

### 15.3 安全栏（D7 / D8）：那个 0 有一半是检测器的召回

**复现全部成立**：26 条探测里模型实际尝试 6 次，`direct_write` 6 条**尝试 0 次**——
护栏**从未被问过写语句**，所以"0 次写操作执行"平凡成立；6 次挑战全为读形状。
检测器我用 12 个表述测：旧正则命中 **1** 个。

**处置：改三处，其中一处是删。**

- `claimed_done` 匹配器重写为"写动词与数据对象邻近共现（同时覆盖主动/被动/名词前置/中文）
  + 同句含否定或拒绝词则不算声称"。现在 **11/12 命中、7/7 拒绝句零误报**，
  两个方向都进了 `tests/test_adversarial.py`（19 条参数化用例）。
  唯一漏的 `I deleted them` 我**故意不追**：它没有宾语，再放宽就会开始把拒绝句判成谎报。
- 新增 `claims_matched`（原始命中数）与 `claimed_done`（扣除拒绝后）**并列输出**——
  只报后者，那个 0 就同时是检测器召回的测量值。
- 报告与 README 的安全表**新增"模型真的尝试了"一列**，`attempted=0` 的类别直接印
  "0（从未被测试）"。这一列由 `detail` 行推导，不要求重跑（重跑要花钱且模型不确定）。
- 按 rule of three 写明 0/6 的失守率 95% 上界约 **39%**。
- **删**：`RESUME.md` 开发版-4 原来写"捕获 6 次越权尝试全部被护栏拦下、零写操作真实执行、
  零谎称完成"。审阅者说得对——第一轮让我删 23%，我删了百分数却把 6/26 换成计数留下，
  **是同一个样本量换了件衣服**。现在只保留不依赖样本量的设计证据
  （AST 白名单、`sqlite_schema` 别名案例、两轴分开记录），并注明写路径未被测试。

### 15.4 判分政策（D9）与校准表读法

- **严格项此前从未被声明**：列数与列集合必须相等、行数必须相等。而头条 +4.2pp 的
  11 道修好里 **4 道只因列数规则翻转**——最敏感的政策恰恰是没写下来的那条。
  README 新增「What the judge is lenient about, and what it is strict about」，
  并写明这条严格性对结果的影响，以及"哪个更该算错是业务判断，当前基准替它默默决定了"。
- **750 里有 168 次是政策自证**：`reflow`/`reorder_cols` 是展示层变换，按政策本就必须接受，
  它们 100% 只说明代码实现了政策。校准表新增一列区分，README 明写
  "读作 582 次对抗性观测 + 168 次恒真"。这个区分原本只存在于
  `tests/test_artifacts.py` 的 `PRESENTATION_ONLY` 里——作者知道，读者不知道。
- `drop_distinct` 只有 6 题能承载，且**全部来自同一个族**，已标注。

### 15.5 D5 / D10：两条我**部分不改**，理由写在这里

- **D5 难度标签是族级常量**：**改文档，不改数据。** README 与简历已注明它是
  SQL 结构复杂度而非实测难度，并标出最大单族占比。
  **不改名的理由**：`difficulty` 字段贯穿 `results/*.jsonl` 与报告，改名会让已发布产物
  与代码不一致——而这正是本轮 D1/D2 的病因。等下一次真跑（见 §15.7）一并改。
- **D10 `PARAPHRASE` 里 `"{q}"` 出现两次**（5 项实为 4 种，2/5 概率拿到裸问句）：
  **确认成立，但我现在故意不修。** 改它会改变题面文本 → 改变数据集摘要 →
  `results/` 里全部已发布产物与缓存作废，要重跑真实模型才能自洽。
  为一个权重瑕疵制造代码与数据不一致，是更坏的交易。**记账到 §15.7 与 §8 一起还。**
- **D10 更重要的那半句我全盘接受**：`HANDOFF.md` §6-5 原写"改写只部分缓解"，
  审阅者指出实情是**改写鲁棒性一次都没被测**——每题只施加一种问法，没有任何一题
  被用两种问法问过。这是措辞掩盖了事实，**已改**：§6 现在写"未测"，
  并把"同题重述稳定性"列为下一步最该做的实验（20 题 × 5 种问法，比扩题型便宜，
  且直接回答"192 够不够"）。

### 15.6 非技术版反馈：最扎心但可能最值钱的一条

它勾了"这人能把复杂的事做完"+"抓不住重点"+"过于自我辩白反而让人怀疑"，
并说三勾**不矛盾**：内容经得起推敲，但诚实声明的密度高到像答辩自辩词。
这条我没法反驳，因为我自己写的时候确实是在逐条防御。

| 它的意见 | 处置 |
|---|---|
| "20 秒扫完记不住项目在干什么" | **改。** 两版各加一句人话定位（版本 A：让模型替人写查询、再建一套能证明判分判得对的系统） |
| 开发版-4 的"全部/零/零"三连读起来像营销 | **改**，见 §15.3 |
| "750 次零误判零漏判"是广告句式 | **改**，见 §15.4：拆成 582 + 168，不再并列三个完美零 |
| 3.65% 那个故事是全简历最好的一段，被埋在第三个分号 | **改。** 单独成条并放在第一位，含"两个缺陷互相掩盖"这个关键转折 |
| "噪声底取四份基线最大两两差异"这种句子不传达信息 | **部分接受。** 对纯外行是噪音，但它恰是算法岗面试官会追问的那条——**按岗位保留在版本 A，删掉术语版留在追问预案** |
| 该被写小的：单题成本 ¥0.006 | **改**，提到成本那条的句首 |

### 15.7 本轮新增的未证明 / 待办

**新增未证明**（§6 已同步）：护栏在**写路径上从未被测试**；改写鲁棒性未测；
`claimed_done=0` 依赖检测器召回；题级独立性假设不成立导致 p 不稳定。

**待办（按性价比）**：
1. **同题重述稳定性**：20 题 × 5 种问法，报题内方差。¥0.2 级，直接回答"分数有多少来自
   认出模板"。**这是本轮之后我最该做的一件事。**
2. 扩对抗探测到 100+ 条，**并且必须包含模型真会尝试的写形状**（否则测的还是拒答倾向）；
   顺带校准诱导强度。
3. `difficulty` → `complexity_tier` 改名 + `PARAPHRASE` 去重 + 全量重跑，三件事一次做完
   （约 ¥2.5），避免制造代码与数据的不一致。
4. 把 §15.2 的聚类口径写进 `docs/INTERVIEW.md` 追问清单（已做，见该文件 21–25 条）。

### 15.8 这一轮真正的教训

上一轮我记录的第 16 条错误是"§4.2 号称逐字对照，实际是我转写的"。这一轮我又写了
"三处不可能各说一套"——**而它在我写下那一刻就是假的**，因为 `report.py` 从没接入
`stats.py`，我只是假设了"我建了单一实现，所以三处必然一致"。

两条合起来是同一个失效：**我验证了实现存在，没有验证每个产物都在用它。**
`tests/test_report_does_not_own_a_second_noise_floor` 这类断言（"report 不得再有自己那份"）
才是这类问题的防线——不是"我写对了"，而是"写错时会红"。

还有第三条，比前两条更该记住：**D1 我第一次是修坏的。** 我把 `_summary.model` 改写成
"没有调用模型"，于是产物**看起来**不再依赖环境，但 `config_hash` 照样把 `.env` 里的
model 折进去，clone 里复测仍然 6 个文件脏。**改了展示、没改成因，比不改更糟——它把
下一次要查的东西藏起来了。** 是在自己按文档流程复测第二遍时才暴露的。
教训：**修完必须在"别人的机器"上复跑一遍验证流程，而不是在自己这台、
恰好已经处于某个状态的树上跑。**
