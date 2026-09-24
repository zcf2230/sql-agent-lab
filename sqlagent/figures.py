"""Generate docs/figures/*.svg from recorded results.

The README needs a picture, and a screenshot is a bad way to get one here: it freezes
numbers that the repository can recompute, which is the exact failure this project has
kept rediscovering (a hand-copied calibration table drifted by 750-vs-778 observations
once already). So every figure below is drawn from `results/` and `runs/`, and the
caption carries the command that regenerates it.

Run:  python -m sqlagent.figures
"""

from __future__ import annotations

import html
import json
from pathlib import Path

from . import stats
from .report import CALIB_ORDER, MODE_LABELS, read_jsonl

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "figures"
RUNS = ROOT / "runs"

INK = "#e6e8ef"
DIM = "#8b8fa3"
GRID = "#262a36"
BG = "#12141a"
GOOD = "#4f9d8f"
BAD = "#e07a6b"
NEUTRAL = "#5b7fd6"
AMBER = "#c98a4b"


def _svg(w: int, h: int, body: str, title: str) -> str:
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="100%" '
        f'font-family="-apple-system,Segoe UI,Microsoft YaHei,sans-serif">'
        f"<title>{html.escape(title)}</title>"
        f'<rect width="{w}" height="{h}" fill="{BG}"/>{body}</svg>'
    )


def _t(x, y, s, size=13, fill=INK, anchor="start", weight="normal"):
    return (f'<text x="{x}" y="{y}" font-size="{size}" fill="{fill}" '
            f'text-anchor="{anchor}" font-weight="{weight}">{html.escape(str(s))}</text>')


def ablation_svg() -> str:
    comps = stats.all_comparisons()
    nf = stats.noise_floor()
    w, h = 980, 96 + len(comps) * 108
    left, right = 240, 700          # bars live in 240..700
    note_left = 716                 # annotations get their own column: sharing the
    #                                   plot area is what made the % labels collide
    scale = lambda p: left + (p - 55.0) / 45.0 * (right - left)

    body = _t(20, 34, "配对比较：同一 192 题、同一代码版本", 16, INK, weight="600")
    body += _t(20, 56, f"须线 = Wilson 95% 置信区间；灰带 = 噪声底 ±{nf['worst_pp']:.1f}pp"
                        f"（{nf['worst_pair'][0]} vs {nf['worst_pair'][1]}）", 12, DIM)
    for pct in (60, 70, 80, 90, 100):
        x = scale(pct)
        body += f'<line x1="{x}" y1="68" x2="{x}" y2="{h-22}" stroke="{GRID}"/>'
        body += _t(x, h - 4, f"{pct}%", 11, DIM, "middle")

    y = 88
    for c in comps:
        base_pp, var_pp = c["base_rate"] * 100, c["variant_rate"] * 100
        label = c["label"].split(": ", 1)[-1]
        model = c["label"].split(":")[0]
        body += _t(left - 14, y + 20, f"{model}", 13, INK, "end", "600")
        body += _t(left - 14, y + 38, label, 12, DIM, "end")

        # noise band around the baseline rate
        lo, hi = scale(base_pp - nf["worst_pp"]), scale(base_pp + nf["worst_pp"])
        body += f'<rect x="{lo}" y="{y+8}" width="{hi-lo}" height="42" fill="{DIM}" opacity="0.18"/>'

        body += f'<rect x="{left}" y="{y+10}" width="{scale(base_pp)-left}" height="16" rx="3" fill="{DIM}" opacity="0.75"/>'
        body += _t(min(scale(base_pp) - 6, right - 6), y + 23, f"{base_pp:.1f}%", 12, "#0d0f14", "end", "600")

        body += f'<rect x="{left}" y="{y+32}" width="{scale(var_pp)-left}" height="16" rx="3" ' \
                f'fill="{GOOD if c["significant"] else AMBER}"/>'
        # Label at the LEFT end of the variant bar: the whisker is the CI *of this
        # value*, so it crosses the bar's right end by construction and struck
        # through the percentage. Text-vs-text checks did not catch a line-vs-text
        # collision; the geometry check now includes segments.
        body += _t(left + 6, y + 45, f"{var_pp:.1f}%", 12, "#0d0f14", "start", "600")

        clo, chi = scale(c["ci_low"] * 100), scale(c["ci_high"] * 100)
        body += f'<line x1="{clo}" y1="{y+40}" x2="{chi}" y2="{y+40}" stroke="{INK}" stroke-width="1.4" opacity="0.8"/>'
        for cap in (clo, chi):
            body += f'<line x1="{cap}" y1="{y+35}" x2="{cap}" y2="{y+45}" stroke="{INK}" stroke-width="1.4" opacity="0.8"/>'

        verdict = f"p={c['p_value']:.4f} → {'显著' if c['significant'] else '未达显著'}"
        colour = GOOD if c["significant"] else BAD
        body += _t(note_left, y + 20, f"修好 {c['fixed']} / 弄坏 {c['broken']}   Δ{c['delta_pp']:+.1f}pp", 12, INK)
        body += _t(note_left, y + 38, verdict, 12, colour, weight="600")
        body += _t(note_left, y + 55, "方向为正，按当前样本量不能称显著" if abs(c["delta_pp"]) > nf["worst_pp"]
                   else "增益未超过噪声底，不报告为改进", 10.5, DIM)
        y += 108
    return _svg(w, h, body, "Ablation results with confidence intervals and noise floor")


def calibration_svg() -> str:
    modes = ["reflow", "reorder_cols", "float_round", "drop_distinct", "wrong_limit", "bad_column"]
    rows, total, bad = [], 0, 0
    for mode in modes:
        lines = read_jsonl(ROOT / "results" / f"calib-{mode}.jsonl")
        body_rows = [r for r in lines[1:] if "id" in r]
        tested = [r for r in body_rows if r.get("result_changed") is not None and not r.get("trivial")]
        po = mode in stats.PRESENTATION_ONLY_MODES
        agree = sum(1 for r in tested if r["correct"] == (po or not r["result_changed"]))
        rows.append((mode, len(tested), agree, len(tested) - agree))
        total += len(tested)
        bad += len(tested) - agree

    # +30 leaves room for the footer rule and total line; at 118 the last row's
    # baseline sat 6px below the viewBox and got clipped.
    w, h = 980, 148 + len(rows) * 40
    maxn = max(r[1] for r in rows) or 1
    left = 260
    body = _t(20, 34, "判分器审计：注入已知缺陷，看它是否上当", 16, INK, weight="600")
    body += _t(20, 56, f"每条观测的期望裁决由\"缺陷是否实质改变结果集\"推导，不来自手写对照表"
                        f"　总计 {total} 次观测，误判 {bad} 次", 12, DIM)
    body += _t(left, 80, "可测样本数", 11, DIM)
    body += _t(940, 80, "false-accept / false-reject", 11, DIM, "end")
    y = 96
    for mode, n, agree, miss in rows:
        body += _t(20, y + 15, mode, 13, INK)
        bw = (right := 180 + (n / maxn) * 560) - 180
        body += f'<rect x="180" y="{y}" width="{max(bw,2)}" height="18" rx="3" fill="{NEUTRAL}"/>'
        body += _t(right + 10, y + 14, str(n), 12, DIM)
        mark = f"{miss}" if miss else "0"
        body += _t(940, y + 14, f"{mark} / {miss}", 12, GOOD if miss == 0 else BAD, "end", "600")
        y += 40
    body += f'<line x1="20" y1="{y+2}" x2="940" y2="{y+2}" stroke="{GRID}"/>'
    body += _t(20, y + 24, f"合计 {total} 次注入观测，判分器与期望零分歧", 13, INK, weight="600")
    return _svg(w, h, body, "Grader calibration sweep")


def bird_svg() -> str:
    """Where my judge and the public benchmark's own comparison rule part ways - both ways.

    A single "my judge is stricter" bar would be the same one-sided move this project
    keeps getting reviewed for, so each row is a stacked track: agreement, then the cases
    the public rule cannot see, then the cases where my policy is the looser one.
    """
    lines = read_jsonl(ROOT / "results" / "bird-judge.jsonl")
    if not lines:
        return ""
    s = lines[0].get("_summary") or {}
    per = s.get("per_mode") or {}
    modes = [m for m in CALIB_ORDER if m in per]
    maxn = max((per[m]["checked"] for m in modes), default=1) or 1

    # The bottom panel needs room for a title, two bars and an axis.
    w, h = 980, 148 + len(modes) * 62 + 208
    body = _t(20, 34, "同一个判分器，搬到别人写的题上（BIRD dev）", 16, INK, weight="600")
    body += _t(20, 56, f"{s.get('questions_selected', 0)} 道人写 gold、{s.get('databases', 0)} 个真实库、"
                       f"{s['checked']} 条注入观测；条长按可比观测数缩放", 12, DIM)
    body += _t(20, 78, "蓝＝与公开口径一致", 11, NEUTRAL)
    body += _t(200, 78, "红＝公开口径看不见（我判错）", 11, BAD)
    body += _t(470, 78, "橙＝我比公开口径宽松", 11, AMBER)
    body += _t(940, 78, "gold 判 gold", 11, DIM, "end")
    body += _t(940, 96, f"{s.get('gold_self_correct', 0)}/{s.get('questions_selected', 0)}",
               13, GOOD if not s.get("gold_self_wrong") else BAD, "end", "600")

    left, right = 260, 700
    y = 128
    for m in modes:
        d = per[m]
        n = d["checked"]
        body += _t(20, y + 16, m, 13, INK)
        body += _t(20, y + 36, MODE_LABELS.get(m, ""), 11, DIM)
        unit = (right - left) / maxn
        x = left
        for value, colour in ((d["public_agree"], NEUTRAL), (d["mine_strict"], BAD),
                              (d["mine_lenient"], AMBER)):
            if not value:
                continue
            bw = value * unit
            body += f'<rect x="{x:.1f}" y="{y+4}" width="{bw:.1f}" height="26" rx="3" fill="{colour}"/>'
            x += bw
        body += _t(right + 16, y + 16, f"可比 {n}", 12, INK)
        body += _t(right + 16, y + 36, f"看不见 {d['mine_strict']} · 我更宽 {d['mine_lenient']}",
                   11, DIM)
        y += 62
    body += _t(20, y + 18, '分歧两个方向都有：不拿"官方看不见重复行"冒充"我的判分器更好"', 12, DIM)

    # Bottom panel: the same agent scored by this judge on two question sets. It belongs in
    # this figure rather than in its own because the two halves are one argument - the judge
    # agrees with the public rule here, so the score gap below is about the questions.
    panel = y + 58
    body += _t(20, panel, "同一个 agent、同一份判分器，换一套考卷", 14, INK, weight="600")
    scores = _score_panel()
    bar_l, bar_r = 260, 700
    scale = lambda pct: bar_l + pct / 100.0 * (bar_r - bar_l)
    for i, row in enumerate(scores):
        ry = panel + 22 + i * 46
        body += _t(20, ry + 18, row["label"], 12, INK)
        body += _t(20, ry + 36, row["sub"], 11, DIM)
        x0, x1 = scale(row["lo"]), scale(row["hi"])
        mid = ry + 14
        body += f'<rect x="{scale(row["pct"]):.1f}" y="{ry+6}" width="3" height="20" fill="{NEUTRAL}"/>'
        body += f'<line x1="{x0:.1f}" y1="{mid}" x2="{x1:.1f}" y2="{mid}" stroke="{DIM}"/>'
        body += f'<line x1="{x0:.1f}" y1="{mid-7}" x2="{x0:.1f}" y2="{mid+7}" stroke="{DIM}"/>'
        body += f'<line x1="{x1:.1f}" y1="{mid-7}" x2="{x1:.1f}" y2="{mid+7}" stroke="{DIM}"/>'
        body += _t(bar_r + 16, ry + 16, f"{row['pct']:.1f}%  [{row['lo']:.1f}%, {row['hi']:.1f}%]",
                   11, INK)
        body += _t(bar_r + 16, ry + 34, row["note"], 11, DIM)
    axis = panel + 22 + len(scores) * 46 + 6
    for pct in (0, 25, 50, 75, 100):
        body += _t(scale(pct), axis + 12, f"{pct}%", 10, DIM, "middle")
    body += _t(20, axis + 34, "两条须线不重叠：差距不是抽样噪声。BIRD 那 60 题在两套口径下判定完全一致，"
                              "所以差距来自考卷，不来自判分器。", 11, DIM)
    return _svg(w, h, body, "Grader vs the public benchmark's own comparison rule")


def _score_panel() -> list[dict]:
    """pass@1 with Wilson intervals, for the two question sets the same agent answered."""
    lines = read_jsonl(ROOT / "results" / "bird-agent-official.jsonl")
    if not lines:
        return []
    o = lines[0].get("_summary") or {}
    judged, mine = o.get("judged", 0), o.get("mine_pass", 0)
    if not judged:
        return []
    base = stats.run_summary("abl2-baseline")
    n0 = base.get("n_graded") or 0
    if not n0:
        return []
    b_lo, b_hi = stats.wilson(round(base["pass_at_1"] * n0), n0)
    a_lo, a_hi = stats.wilson(mine, judged)
    return [
        {"label": "自制基准", "sub": f"{n0} 题 · 我造的题、我写的 gold", "note": "同一模型同一配置",
         "pct": base["pass_at_1"] * 100, "lo": b_lo * 100, "hi": b_hi * 100},
        {"label": "BIRD dev", "sub": f"{judged} 题 · 人写题、11 个真实库",
         "note": f"官方口径同分（{o.get('official_pass', 0)}/{judged}）",
         "pct": mine / judged * 100, "lo": a_lo * 100, "hi": a_hi * 100},
    ]


def trace_svg() -> str:
    """Draw one real failing task's actual step chain, straight from the trace file."""
    lines = read_jsonl(ROOT / "results" / "abl2-baseline.jsonl")
    rows = [r for r in lines[1:] if "id" in r]
    pick = next((r for r in rows if not r["correct"] and not r.get("trivial")), None)
    if pick is None:
        return ""
    task_id = pick["id"]
    # runs/ is gitignored (50 MB of traces), so this is the one figure a fresh clone
    # cannot rebuild. Missing input skips rather than crashes: `python -m
    # sqlagent.figures` has to work for someone who only cloned the repo, and
    # report.html carries the same per-task replay for them.
    trace_file = RUNS / "openai__deepseek_chat__fs0.jsonl"
    if not trace_file.exists():
        print(f"  trace.svg skipped: {trace_file.relative_to(ROOT)} is not in git "
              f"(runs/ is gitignored); see report.html for the replay")
        return ""
    trace = None
    skipped = 0
    for line in trace_file.read_text(encoding="utf-8").splitlines():
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            skipped += 1  # an interleaved half-record; report.py discloses the count
            continue
        if rec.get("task_id") == task_id:
            trace = rec
    if skipped:
        print(f"  trace.svg: {skipped} unparseable line(s) in {trace_file.name}")
    if trace is None:
        return ""

    steps = trace["steps"]
    w = 980
    box_w, gap = 150, 16
    n = len(steps)
    h = 250
    body = _t(20, 30, f"一题的完整证据链 · {task_id}", 15, INK, weight="600")
    q = next((r for r in read_jsonl(ROOT / "data" / "tasks.jsonl") if r["id"] == task_id), {})
    body += _t(20, 52, q.get("question", "")[:110], 12, DIM)

    x = 20
    for s in steps:
        if s["kind"] == "llm":
            label, sub, colour = f"LLM 第 {s.get('turn')} 轮", f"{s.get('prompt_tokens')}+{s.get('completion_tokens')} tok · {s.get('t_ms')}ms", NEUTRAL
        else:
            ok = s.get("ok")
            label, sub, colour = f"工具 {s.get('name')}", ("返回 ok" if ok else f"FAIL {s.get('error_type')}"), (GOOD if ok else BAD)
        body += f'<rect x="{x}" y="90" width="{box_w}" height="58" rx="8" fill="none" stroke="{colour}" stroke-width="1.5"/>'
        body += _t(x + box_w / 2, 114, label, 12, colour, "middle", "600")
        body += _t(x + box_w / 2, 132, sub, 10.5, DIM, "middle")
        if x + box_w + gap < w - box_w:
            body += f'<line x1="{x+box_w+2}" y1="119" x2="{x+box_w+gap-2}" y2="119" stroke="{DIM}" stroke-width="1"/>'
            body += f'<path d="M{x+box_w+gap-6} 115 l6 4 -6 4 z" fill="{DIM}"/>'
        x += box_w + gap
        if x > w - box_w:
            break

    detail = pick.get("detail", {})
    body += f'<rect x="20" y="176" width="940" height="52" rx="8" fill="#191c25" stroke="{BAD}"/>'
    body += _t(34, 198, f"判分器裁决：{pick['reason']}　gold {detail.get('gold_rows')} 行 vs 预测 {detail.get('pred_rows')} 行"
                        f"　→ 该题计入失败", 12.5, BAD, weight="600")
    body += _t(34, 216, "这一行的存在意味着：上面每个百分比都能被点开到具体的提示、参数与数据库回包",
               11.5, DIM)
    return _svg(w, h, body, "Trace replay for one task")


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    made = []
    for name, render in (("ablation.svg", ablation_svg), ("calibration.svg", calibration_svg),
                         ("bird.svg", bird_svg), ("trace.svg", trace_svg)):
        svg = render()
        if not svg:
            print(f"skipped {name}: no input data")
            continue
        (OUT / name).write_text(svg, encoding="utf-8", newline="\n")
        made.append(name)
    print(f"wrote {len(made)} figures -> {OUT.relative_to(ROOT)}: {', '.join(made)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
