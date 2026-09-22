# sql-agent-lab · 交付说明

一个带**自审能力**的 Text-to-SQL agent 项目。核心不是"我做了个 agent"，而是
"我做的 agent 的每一个分数，我都能证明它是怎么来的、以及在什么情况下会失效"。

- 位置：`C:\Users\34264\Desktop\sql-agent-lab`
- 体积与提交数：不写死。用 `du -sh .` 和 `git rev-list --count HEAD` 自查
  （交付包不含 `.venv`、`data/*.db`、`runs/cache/`，都可用 §4.2 的命令零成本重建）
- 已验证：全新虚拟环境 → 装依赖 → 重建库 → 重新生成题库，产出与原目录**逐字节相同**；
  82 个测试通过；报告可离线生成

---

## 一、三种用法，按你想花的力气排

### ① 只看，不装任何东西（30 秒）

**双击 `report.html`**。离线单文件，不需要网络、不需要 API key、不花钱。

里面有：消融结果、判分器校准表、失败归因、26 条对抗探测、以及 **192 题 × 6 次运行的
完整 trace 回放**——每一题都能展开看模型每轮说了什么、调了哪个工具、数据库回了什么。

### ② 本地跑通，仍然一分钱不花（约 5 分钟）

```bash
cd C:\Users\34264\Desktop\sql-agent-lab
uv venv --python 3.12
VIRTUAL_ENV=.venv uv pip install -e ".[dev]"

.venv/Scripts/python.exe -m sqlagent.data.build_db       # 建 20 表业务库
.venv/Scripts/python.exe -m sqlagent.data.build_tasks    # 生成 192 题 + 剔除 14 题并说明原因
.venv/Scripts/python.exe -m pytest                       # 82 个测试
.venv/Scripts/python.exe scripts/calibrate.py            # 判分器校准：750 次观测
.venv/Scripts/python.exe -m sqlagent.report              # 重新生成 report.html
```

这六条**全部不联网**。库里和题库都是固定种子生成的，重跑结果字节级一致。

### ③ 跑真实模型（要 API key）

复制 `.env.example` 为 `.env`，填 `SQLAGENT_API_KEY=sk-...`，然后
`.venv/Scripts/python.exe -m sqlagent.secrets` 封存（Windows DPAPI，明文会被擦掉）。

```bash
.venv/Scripts/python.exe -m sqlagent.eval.runner --provider openai --tag my-baseline
.venv/Scripts/python.exe -m sqlagent.eval.runner --provider openai --fewshot-k 3 --tag my-3shot
.venv/Scripts/python.exe -m sqlagent.adversarial        # 26 条安全探测
```

一轮 192 题：DeepSeek 约 ¥1.2、Qwen-flash 约 ¥0.6。同配置重跑命中缓存，**不再花钱**。

---

## 二、目录

```
report.html          ← 先看这个，离线单文件
README.md            ← 项目主页，写清楚政策与限制
docs/INTERVIEW.md    ← 面试准备主材料：逐模块讲解 + 17 条追问
docs/ARTICLE.md      ← 可直接发知乎/掘金的技术文章
sqlagent/            ← agent 循环、护栏、判分器、runner、mock、trace、报告
  adversarial.py     ← 26 条邀请模型越权的探测
  secrets.py         ← DPAPI 凭据封存（每个模型一个槽位）
data/                ← 题库 + 被剔除题目及原因
results/             ← 每次运行的逐题结果（所有数字的出处）
runs/                ← trace 原文（报告的数据源）
tests/               ← 82 个测试
```

**没有** `.env` 和 `secrets/`——凭据是 Windows DPAPI 加密并绑定当前用户/机器的，
复制过去也解不开，所以交付包里不含任何密钥。要用就在本机重新生成。

---

## 三、简历可以直接用的四条（每个数字都能回溯到 results/）

> **面向 Text-to-SQL 的自修复 Agent 与可审计评测框架**（Python · DeepSeek / Qwen API）
>
> - 构建 4 工具 ReAct Agent（schema 探查 / 值采样 / 只读执行 / 报错自修复）与自建 20 表·含
>   7 类脏数据陷阱的业务库；产出 **192 道可自动判分题目**，在 DeepSeek 上
>   **pass@1 89.1% → 93.2%（3-shot 上下文注入）**，单题成本 ¥0.006
> - 设计**判分器审计协议**：注入 6 类已知缺陷并由"是否实质改变结果集"自动推导应然裁决，
>   **750 次观测 0 false-accept / 0 false-reject**；识别并剔除 14 道不可判定题目
>   （10 道并列 top-k 无唯一解、4 道 gold 答案为零）
> - 用**实测噪声底（同配置重跑 1.0% 判定翻转）**约束结论，并定位 3 个会伪造实验结论的缺陷：
>   题面引用不存在的数据、few-shot 配置项从未进入 prompt、结果缓存缺少数据集与判分器指纹
> - 实现 26 条**对抗性安全探测**：agent 越权尝试率 23%，全部被 AST 白名单护栏拦截，
>   0 次写操作执行、0 次谎报完成；发现模型在 `sqlite_master` 被拦后改用官方别名
>   `sqlite_schema` 绕过，白名单默认拒绝使其仍然失败

四条的规则：① 都有数字且数字对得上 README 的表；② 动词只用"构建/设计/实现/定位"；
③ **不要加任何你没逐行读过的内容**。

---

## 四、面试准备（你说最后统一补知识，这是为那时准备的）

顺序很重要，**别从第一节开始顺读**：

1. **`docs/INTERVIEW.md` 第 8、9 节先读**——few-shot 从 89.6% 崩到 3.7%、以及 Qwen 3-shot
   那个假提升。这两节是"我能分辨 bug 和发现"的证据，最难被问倒，也最出彩。
2. 再读第 3、4、5 节（判分政策、require_order、校准扫描）——能撑起 20 分钟技术追问。
3. 最后看第 1、2、6、7 节，能讲结论即可，不必吃透实现。
4. **合上文档答 INTERVIEW.md 末尾 17 条追问**。答不出的那条，说明对应章节还没过。
5. 特别记住第 16 条的答案：护栏在 192 道正常题里**拦截 0 次**，是 26 条对抗探测
   才把它变成有数字的结论。**"我不拿零次观测冒充结论"** 比 "拦截率 100%" 值钱。

三档检验：能跑通 → 能合上代码复述 2 分钟 → **能预测改动后果并说对**。只有到第三档才算准备好。

---

## 五、这个项目已经证明的 / 还没证明的

**已证明**：判分器对 6 类已知缺陷零漏判；结果集比对政策明确且被 82 个测试锁定；
护栏拦下 12 类语法逃逸 + 真实模型 6 次越权尝试；全流程零 key 可复现且字节级确定。

**未证明（README 的 Known limitations 里逐条写着，别在面试里越界）**：
自修复在此数据集上测不出增益（模型 99% 首次即成功，没错误可修）；跨模型对比被
"工具调用服从度"混淆，所以**"哪家模型更会写 SQL"仍是未答**；安全护栏的行为证据
只有 26 条探测；只测了 temperature=0 的单轮设定；gold 来自模板，措辞比人写的整齐。

---

## 六、关于这份包和原目录

- 原目录（`Documents/Qoder/2026-09-21/4a73de0a/sql-agent-lab`）仍在，含 `.env`、
  完整 `runs/`（50 MB）和 `secrets/`。
- **建议只把桌面这份当交付/展示快照**，继续开发就改原目录；两边都改必然漂移，
  而这个项目一整轮都在处理"两处真值来源漂移"造成的错误。
- 要上传 GitHub：桌面这份可以直接 `git push`（历史完整，含我自己搞坏又修好的记录——
  这部分别删，它是你最硬的素材）。上传前确认一次：
  ```bash
  .venv/Scripts/python.exe -m pytest tests/test_secrets.py -k plaintext -v
  ```
  这条会扫整棵工作树，按 key 的形状匹配（同时覆盖 DeepSeek 和 DashScope 两种格式）。
  **别用 `git ls-files | xargs grep ...`**：xargs 会把任何子进程的非零退出码统一折成 123，
  于是它既不能证明"干净"也不能证明"有货"——我在这份文档里写过一次，是错的。
