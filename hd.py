from pathlib import Path
p = Path("docs/HANDOFF.md"); s = p.read_text(encoding="utf-8")

old = "### 代码（30 个 .py，4,632 行；生产 3,754 / 测试 753）"
new = ("### 代码\n\n"
       "> 文件数与行数**不写在这里**。上一版写了「30 个 .py，4,632 行；生产 3,754 / 测试 753」，"
       "六个数字全部过期，而紧跟它的那一句就写着「各文件行数不写死，用 `wc -l` 自查」——"
       "同一段里自相矛盾，且正是本文件开头说的「这个项目已经为硬编码数字与数据脱节付过代价」那件事。\n"
       "> 要多少自己跑：`find . -name '*.py' -not -path './.venv/*' | wc -l` 与 "
       "`... | xargs wc -l | tail -1`。")
assert s.count(old) == 1; s = s.replace(old, new)

old = ("`README.md` 333 行 · `docs/INTERVIEW.md` 211 行 · `docs/ARTICLE.md` 311 行 ·\n"
       "`docs/DELIVERY.md` · `docs/RESUME.md` 两版简历措辞 · `docs/REVIEW_TEMPLATE.md` **审阅反馈表（空白，供审阅者填写）** · 本文件\n"
       "（各文件行数不写死，用 `wc -l docs/*.md` 自查）")
new = ("`README.md` · `docs/INTERVIEW.md` · `docs/ARTICLE.md` · `docs/DELIVERY.md` ·\n"
       "`docs/RESUME.md` 两版简历措辞 · `docs/REVIEW_TEMPLATE.md` **技术版审阅表（空白）** ·\n"
       "`docs/REVIEW_TEMPLATE_NONTECH.md` **非技术版审阅表（约 10 分钟，不碰命令行）** · 本文件。\n"
       "已归档的填写原件：`docs/REVIEW_2026-09-22.md`、`docs/REVIEW_2026-09-23.md`、\n"
       "`docs/REVIEW_NONTECH_2026-09-23.md`。\n\n"
       "行数一律不写死：`wc -l README.md docs/*.md`。")
assert s.count(old) == 1; s = s.replace(old, new)

old = "两版措辞（算法实习 / Agent 开发实习）连同追问预案在 **`docs/RESUME.md`**（98 行）。要点摘录："
new = "两版措辞（算法实习 / Agent 开发实习）连同追问预案在 **`docs/RESUME.md`**。要点摘录："
assert s.count(old) == 1; s = s.replace(old, new)

old = "$ python -m sqlagent.figures\nwrote 3 figures -> docs\figures: ablation.svg, calibration.svg, trace.svg"
new = ("$ python -m sqlagent.figures          # 有 runs/ 的目录（作者本机 / 交付包）\n"
       "wrote 3 figures -> docs\figures: ablation.svg, calibration.svg, trace.svg\n"
       "\n"
       "$ python -m sqlagent.figures          # 只 clone、没有 runs/ 的目录，也就是 §4.2 的场景\n"
       "  trace.svg skipped: runs\openai__deepseek_chat__fs0.jsonl is not in git (runs/ is gitignored); see report.html for the replay\n"
       "wrote 2 figures -> docs\figures: ablation.svg, calibration.svg")
assert s.count(old) == 1; s = s.replace(old, new)

p.write_text(s, encoding="utf-8", newline=chr(10))
print("D4 + D0 fixed")
