# sql-agent-lab · 交付包说明

只讲**这个目录本身**：里面有什么、不含什么、怎么验证。

不涉及结论与数字。一份内容只放一个地方——这个文件曾经同时装着安装步骤、简历措辞、
面试顺序和"噪声底 1.0%"，而项目后续把噪声底改成实测最大两两差异、把测试数从 82 加到
95、把"越权尝试率 23%"从简历里删掉。**它每一处都过期了，却没有一处报错**。现在正文在：

| 你要的东西 | 正文在哪 |
|---|---|
| 项目是什么、政策、结果、限制 | `README.md`（顶部有给审阅者的 15 分钟路径） |
| 主张 → 证据映射、未证明清单、复核命令与预期输出、待办、错误全表 | `docs/HANDOFF.md`（§4.2 命令、§5 映射、§6 别引用为结论、§7 请重点质疑、§8 唯一一份待办、§9 犯过的错、§13/§15/§16 三轮审阅逐条回应、§14 发布清单、§17 同题重述稳定性） |
| 简历措辞（算法实习版 / Agent 开发版）与追问预案 | `docs/RESUME.md` |
| 面试准备：逐模块讲解 + 追问清单（条数以文件为准，别写死） | `docs/INTERVIEW.md`（建议顺序 §8-9 → §2.5 → 其余） |
| 可直发的技术文章 | `docs/ARTICLE.md`（2026-09-26 已发：`https://juejin.cn/post/7689219046839336998`，线上与本文的四处差异记在 `HANDOFF.md` §23） |
| 想亲手问一句 | `python -m sqlagent.ask "你的问题" --provider mock`（$0）；去掉 `--provider mock` 就是真实模型，约 ¥0.01/题 |
| 不装环境只看结果 | 双击仓库根目录 `report.html`（离线单文件，不联网、不要 key） |
| 外部审阅意见**原件**（四轮已归档） | `docs/REVIEW_2026-09-22.md`（第一轮）、`docs/REVIEW_2026-09-23.md`（第二轮技术版）、`docs/REVIEW_NONTECH_2026-09-23.md`（第二轮非技术版）、`docs/REVIEW_2026-09-23_R3.md`（第三轮）、`docs/REVIEW_2026-09-24_R4.md`（第四轮） |
| 空白审阅表（供你填） | `docs/REVIEW_TEMPLATE.md`（技术版，15–25 分钟）、`docs/REVIEW_TEMPLATE_NONTECH.md`（非技术版，约 10 分钟，不碰命令行） |

---

## 这个目录里有什么

```
report.html          离线报告，九节：消融表、配对显著性（p 值/CI/噪声底/聚合口径敏感性）、
                     同题重述稳定性（措辞噪声）、判分器校准、判分器在别人造的题上(BIRD dev)、
                     失败归因、分类通过率、对抗性安全探测、逐题 trace 回放
README.md            项目主页
docs/                上表那几份
sqlagent/            agent 循环、护栏、判分器、runner、mock、trace、报告、统计、图
data/                题库 + 被剔除题目及原因 + 四组重述实验的题面（库和题都用固定种子，可零成本重建）
results/             每次运行的逐题结果 —— README 里每个百分比的出处
runs/                trace 原文（report.html 与 docs/figures/trace.svg 的数据源）
scripts/             calibrate.py 校准扫描、significance.py 显著性、guard_corpus.py 护栏双向测量、
                     restability.py 同题重述实验、bird_tasks.py 把 BIRD dev 出题面、
                     bird_judge.py 在 BIRD 上测判分器（--answers 可把已存答案在两套口径下重打）、
                     rejudge.py 用当前判分器重判全部已存答案（回答"改了判分器，数字动没动"）、
                     bird_hint.py 只改一句 prompt 重跑同样 60 题，检验 §20 的诊断是否因果
tests/               全部测试，含"文档不得复述已撤回的主张"这一类一致性断言
```

## 不含什么，以及为什么

| 不含 | 原因 | 怎么办 |
|---|---|---|
| `.env`、`secrets/` | 凭据用 Windows DPAPI 封存并绑定当前用户+机器，复制过去也解不开；不外发是唯一稳妥的做法 | 需要跑真实模型就在本机重新生成：`SQLAGENT_API_KEY=... python -m sqlagent.secrets` |
| `.venv/` | 22 MB 里绝大部分是它 | `uv venv --python 3.12 && VIRTUAL_ENV=.venv uv pip install -e ".[dev]"`；想精确复现我这棵依赖树就用锁文件：`uv sync --frozen --extra dev` |
| `data/*.db` | 派生物 | `python -m sqlagent.data.build_db`，固定种子，字节级一致 |
| `runs/cache/` | 结果缓存，含本机绝对路径 | 不用重建；重新跑真实模型才会生成，同配置命中缓存不重复计费 |

体积与提交数**不写在这里**（写了就会过期）：`du -sh .`、`git rev-list --count HEAD`。

---

## 验证这个包是真的

下面每一条都零 API 花费、零联网，约 5 分钟（条数不写死，命令本身就在那儿）。
命令与**逐字关键输出行**在 `docs/HANDOFF.md` §4.2；对不上就当成缺陷提出。

```bash
uv venv --python 3.12 && VIRTUAL_ENV=.venv uv pip install -e ".[dev]"
# 依赖树在 uv.lock 里（护栏判定用的是 sqlglot 的 AST，所以量具版本要有出处）：
# uv sync --frozen --extra dev 是精确复现那条路，CI 走的就是它。
.venv/Scripts/python.exe -m sqlagent.data.build_db
.venv/Scripts/python.exe -m sqlagent.data.build_tasks
.venv/Scripts/python.exe -m pytest                # 全绿（计数以输出为准，别信文档里的数）
.venv/Scripts/python.exe scripts/calibrate.py     # 判分器：750 观测 / 0 / 0
.venv/Scripts/python.exe scripts/significance.py  # p 值与噪声底，现算
.venv/Scripts/python.exe scripts/guard_corpus.py  # 护栏双向测量：120 写 0 穿透 / 192 读 0 误拒
.venv/Scripts/python.exe scripts/restability.py --pick failures --analyse-only
.venv/Scripts/python.exe scripts/restability.py --pick correct-representative --analyse-only
                                                  # 措辞噪声：读已存结果重印结论
.venv/Scripts/python.exe -m sqlagent.figures      # 重画 README 四张图
git status --porcelain                            # 最后一步：应当【什么都不输出】
```

已经做过的更强一次验证：在**全新虚拟环境**里装依赖 → 重建库 → 重新生成题库，产出的
`tasks.jsonl` 与原目录**逐字节相同**。

同步之后**按 blob 核对**，不要按"我以为复制对了"核对。镜像的是**全部跟踪路径**，
不是"自上次以来改动的那几个"——只复制改动清单，就查不出包里有文件被漏掉或多余：

```bash
# 把 SRC 换成你本地的仓库目录；不要把自己的绝对路径写进这份文档（它会进公开仓库）
SRC="$HOME/Documents/Qoder/<workspace>/sql-agent-lab"
DST="$HOME/Desktop/sql-agent-lab"

# 1) 镜像：按原相对路径逐个复制（`cp 文件 目录/` 只取文件名，会把它丢到根目录）
cd "$SRC" && git ls-files -z | while IFS= read -r -d '' f; do
  mkdir -p "$DST/$(dirname "$f")" && cp -p "$f" "$DST/$f"
done

# 2) 清单核对：两棵树的跟踪文件差异，只允许出现包独有的那一个入口文档
git -C "$SRC" ls-tree -r HEAD --name-only | sort > .src_files
git -C "$DST" ls-tree -r HEAD --name-only | sort > .dst_files
comm -3 .src_files .dst_files          # 期望只输出 00-使用说明.md

# 3) 内容核对：同名路径必须 blob 相同，应当【什么都不输出】
while read -r f; do
  a=$(git -C "$SRC" rev-parse "HEAD:$f"); b=$(git -C "$DST" rev-parse "HEAD:$f" 2>/dev/null)
  [ "$a" = "$b" ] || echo "DIFFER $f"
done < .src_files
```

为什么用 blob 而不是 `cmp`：`cmp` 只能比"你手上那两个文件"，比不出"上游有、包里没跟进去"
这一类缺失，也发现不了根目录多出一个同名副本——`cp docs/x.md <目标>/` 就造出过这个事故
（提交 `Remove a duplicate HANDOFF.md I created at the bundle root`）。

**镜像时不要顺手删除。** 包根目录的 `00-使用说明.md` 是交付物自己的入口文档，上游仓库
不跟踪它；按"凡是我没跟踪的一律删掉"处理就会把它删了——这个也真发生过（当天复原），
所以现在镜像脚本里它被写成显式的保留名单，而不是靠人记住。

## 上传公开之前

见 `docs/HANDOFF.md` §14。其中两条不要跳过：历史里每个 blob 都扫过 key 形状（干净），
以及 **git 作者邮箱会随仓库公开**——要用隐私邮箱或重建单提交历史再推。
