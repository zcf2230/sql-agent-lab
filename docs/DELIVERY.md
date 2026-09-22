# sql-agent-lab · 交付包说明

只讲**这个目录本身**：里面有什么、不含什么、怎么验证。

不涉及结论与数字。一份内容只放一个地方——这个文件曾经同时装着安装步骤、简历措辞、
面试顺序和"噪声底 1.0%"，而项目后续把噪声底改成实测最大两两差异、把测试数从 82 加到
95、把"越权尝试率 23%"从简历里删掉。**它每一处都过期了，却没有一处报错**。现在正文在：

| 你要的东西 | 正文在哪 |
|---|---|
| 项目是什么、政策、结果、限制 | `README.md`（顶部有给审阅者的 15 分钟路径） |
| 主张 → 证据映射、未证明清单、复核命令与预期输出 | `docs/HANDOFF.md`（§4.2 命令、§5 映射、§6 别引用为结论、§8 待办、§13 外部审阅与逐条回应、§14 发布清单） |
| 简历措辞（算法实习版 / Agent 开发版）与追问预案 | `docs/RESUME.md` |
| 面试准备：逐模块讲解 + 20 条追问 | `docs/INTERVIEW.md`（建议顺序 §8-9 → §2.5 → 其余） |
| 可直发的技术文章 | `docs/ARTICLE.md` |
| 不装环境只看结果 | 双击仓库根目录 `report.html`（离线单文件，不联网、不要 key） |
| 外部审阅意见原件 | `docs/REVIEW_2026-09-22.md`；空白模板 `docs/REVIEW_TEMPLATE.md` |

---

## 这个目录里有什么

```
report.html          离线报告：指标卡、消融表、判分器校准、失败归因、26 条对抗探测、
                     192 题 × 6 次运行的逐题 trace 回放
README.md            项目主页
docs/                上表那几份
sqlagent/            agent 循环、护栏、判分器、runner、mock、trace、报告、统计、图
data/                题库 + 被剔除题目及原因（库和题都用固定种子，可零成本重建）
results/             每次运行的逐题结果 —— README 里每个百分比的出处
runs/                trace 原文（report.html 与 docs/figures/trace.svg 的数据源）
scripts/ tests/      校准扫描、显著性输出 / 全部测试
```

## 不含什么，以及为什么

| 不含 | 原因 | 怎么办 |
|---|---|---|
| `.env`、`secrets/` | 凭据用 Windows DPAPI 封存并绑定当前用户+机器，复制过去也解不开；不外发是唯一稳妥的做法 | 需要跑真实模型就在本机重新生成：`SQLAGENT_API_KEY=... python -m sqlagent.secrets` |
| `.venv/` | 22 MB 里绝大部分是它 | `uv venv --python 3.12 && VIRTUAL_ENV=.venv uv pip install -e ".[dev]"` |
| `data/*.db` | 派生物 | `python -m sqlagent.data.build_db`，固定种子，字节级一致 |
| `runs/cache/` | 结果缓存，含本机绝对路径 | 不用重建；重新跑真实模型才会生成，同配置命中缓存不重复计费 |

体积与提交数**不写在这里**（写了就会过期）：`du -sh .`、`git rev-list --count HEAD`。

---

## 验证这个包是真的

前四条零 API 花费、零联网，约 5 分钟。命令与**逐字预期输出**在
`docs/HANDOFF.md` §4.2；对不上就当成缺陷提出。

```bash
uv venv --python 3.12 && VIRTUAL_ENV=.venv uv pip install -e ".[dev]"
.venv/Scripts/python.exe -m sqlagent.data.build_db
.venv/Scripts/python.exe -m sqlagent.data.build_tasks
.venv/Scripts/python.exe -m pytest                # 全绿
.venv/Scripts/python.exe scripts/calibrate.py     # 判分器：750 观测 / 0 / 0
.venv/Scripts/python.exe scripts/significance.py  # p 值与噪声底，现算
.venv/Scripts/python.exe -m sqlagent.figures      # 重画 README 三张图
```

已经做过的更强一次验证：在**全新虚拟环境**里装依赖 → 重建库 → 重新生成题库，产出的
`tasks.jsonl` 与原目录**逐字节相同**。

## 上传公开之前

见 `docs/HANDOFF.md` §14。其中两条不要跳过：历史里每个 blob 都扫过 key 形状（干净），
以及 **git 作者邮箱会随仓库公开**——要用隐私邮箱或重建单提交历史再推。
