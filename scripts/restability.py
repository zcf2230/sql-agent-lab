"""Test-retest reliability: ask the same question a different way and see if the
score survives.

The benchmark is template-generated, and every published number in this repository
rests on each question being asked exactly **one** way. That makes the headline
pass@1 a measurement over a single surface form per task - so it cannot, by
construction, distinguish "the model understood the question" from "the model
recognised the sentence". The third review put the point exactly:

    现在没有任何一个数字能回答"模型是在解题还是在认模板"

This produces that number. It is the cheapest experiment in the project that answers
a question nobody has answered yet, and it answers a question the whole repository
asks about itself: 192 questions might not be 192 independent observations, and one
phrasing per question is not one measurement of competence.

Method
------
Recover each task's bare question by inverting the generator's own paraphrase
templates, then re-ask it in **every** form the generator can produce. The inversion
is checked by round-trip: re-applying the template that produced the stored question
must reproduce it byte for byte, so a task whose phrasing cannot be recovered is
reported rather than silently mangled.

Scoring is the same `score_execution` the benchmark uses, on the same gold SQL, with
the same `require_order` - only the wording of the question changes.

Run:  python scripts/restability.py --pick <families|failures|correct-representative|correct-boundary>
Cost: ~$0.001 per question. The four measured runs cost $0.0655 / $0.0793 / $0.0770 /
$0.0794 - see results/restability-deepseek-chat-*.jsonl, and `--analyse-only` reprint
them for nothing. A finished run is also in runs/cache/, so re-printing never re-bills.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

TASKS = ROOT / "data" / "tasks.jsonl"
# The pick is part of every output name. Two runs that differ only in which
# tasks they sampled previously shared a results file, and the second silently
# overwrote the first - an artifact that had been committed and published.
OUT_SET_STEM = "tasks_restability"

# The generator's own surface forms, deduplicated. `build_tasks.PARAPHRASE` lists five
# entries but "{q}" appears twice, so only four distinct forms exist and two fifths of
# the benchmark gets the bare question. Both facts are measured here rather than
# assumed, because the duplicate is itself a finding this script can confirm.
from sqlagent import stats  # noqa: E402
from sqlagent.data.build_tasks import PARAPHRASE  # noqa: E402


def distinct_forms() -> list[str]:
    seen: list[str] = []
    for p in PARAPHRASE:
        if p not in seen:
            seen.append(p)
    return seen


def lower_first(text: str) -> str:
    return text[0].lower() + text[1:]


def recover_base(question: str) -> tuple[str, str] | None:
    """(bare question, the template that produced the stored wording) or None."""
    for tpl in distinct_forms():
        if tpl == "{q}":
            continue
        head, _, tail = tpl.partition("{q_lower}")
        if question.startswith(head) and question.endswith(tail):
            middle = question[len(head):len(question) - len(tail)]
            if middle:
                return middle[0].upper() + middle[1:], tpl
    if all(not question.startswith(t.partition("{q_lower}")[0]) for t in distinct_forms() if t != "{q}"):
        return question, "{q}"
    return None


def used_index(template: str) -> int:
    """Position of the template that produced the stored wording, among the distinct forms."""
    return distinct_forms().index(template)


def variants(base: str) -> list[str]:
    return [t.format(q=base, q_lower=lower_first(base)) for t in distinct_forms()]


def load_tasks() -> list[dict]:
    return [json.loads(l) for l in TASKS.read_text(encoding="utf-8").splitlines() if l.strip()]


def baseline_correct() -> set[str]:
    rows = [json.loads(l) for l in (ROOT / "results" / "abl2-baseline.jsonl")
            .read_text(encoding="utf-8").splitlines() if l.strip()]
    return {r["id"] for r in rows[1:] if "id" in r and r["correct"] and not r.get("trivial")}


def three_shot_fixed() -> set[str]:
    rows = lambda tag: {r["id"]: r["correct"] for r in
                        [json.loads(l) for l in (ROOT / "results" / f"{tag}.jsonl")
                         .read_text(encoding="utf-8").splitlines() if l.strip()][1:] if "id" in r}
    b, v = rows("abl2-baseline"), rows("abl2-3shot")
    return {i for i in v if v[i] and not b.get(i)}


def baseline_failures() -> set[str]:
    """The tasks the published baseline got wrong.

    Selecting by family's first task - what `select` does - produced a run with zero
    information: every one of those 19 tasks was answered correctly in all four
    phrasings, so there was nothing that could have destabilised. A reliability test
    on items nobody fails measures nothing, the same way a calibration cell whose
    defect cannot reach the answer does. The failures are where the signal can be.
    """
    rows = [json.loads(l) for l in (ROOT / "results" / "abl2-baseline.jsonl")
            .read_text(encoding="utf-8").splitlines() if l.strip()]
    return {r["id"] for r in rows[1:] if "id" in r and not r["correct"] and not r.get("trivial")}


def select(tasks: list[dict], per_family: int = 1, pick: str = "families") -> list[dict]:
    """Which tasks to re-ask, per `pick`. Deterministic, no RNG.

    One task per family (`families`) is the selector the first run used and it was a
    design error, not just a weak one: every one of those 19 tasks was correct in all
    four phrasings, so the experiment could not have produced a fragile item at any
    rate. Stratifying by family controls for the benchmark's uneven families
    (`category_slice` alone is 35 of 192 questions) but it also guarantees that the
    tasks measured are the easiest member of each family, which is where stability is
    not informative. `correct-representative` samples in proportion to family size
    instead - the only set here whose rate may be multiplied back up to 192.
    """
    if pick == "failures":
        bad = baseline_failures()
        return [t for t in tasks if t["id"] in bad]

    if pick in ("correct-representative", "correct-boundary"):
        good = baseline_correct()
        by_fam: dict[str, list[dict]] = defaultdict(list)
        for t in tasks:
            if t["id"] in good:
                by_fam[t["id"].rsplit("-", 1)[0]].append(t)

        if pick == "correct-representative":
            # Proportional to how each family actually appears in the benchmark. This is
            # the only sample here whose result may be multiplied up to the full 192;
            # the boundary set below deliberately over-samples instability and must not
            # be extrapolated, which is the mistake the first version of this script made
            # in the opposite direction (it over-sampled *stability*).
            quota = {f: max(1, round(20 * len(ts) / len(good))) for f, ts in by_fam.items()}
            picked = [ts[:quota[f]] for f, ts in sorted(by_fam.items())]
            chosen = [t for group in picked for t in group]
            return chosen[:22]

        # The first draft of this selector intersected "tasks 3-shot fixed" with
        # "tasks the baseline got right" and produced 8 rows - those two sets are
        # disjoint by construction, since a fixed task is a baseline failure by
        # definition. The real boundary signal on the success side is:
        #   (a) tasks sharing one gold skeleton with a task that failed - same query
        #       shape, different parameter value, so the family is exactly on the line;
        #   (b) correct tasks in a family that contains both outcomes.
        # The second draft took (b) in file order and filled 13 of its 20 slots from
        # `distinct_count`, because that family happens to be listed early and has 13
        # correct tasks - i.e. it measured one family and labelled it a boundary
        # sample. (b) is therefore drawn round-robin, two per family, busiest family
        # first, and the composition is printed so the same mistake is visible.
        good = baseline_correct()
        bad = baseline_failures()
        all_rows = [json.loads(l) for l in TASKS.read_text(encoding="utf-8").splitlines() if l.strip()]
        sk_group: dict[tuple, list[str]] = defaultdict(list)
        for t in all_rows:
            sk_group[(t["id"].rsplit("-", 1)[0], stats.skeleton(t["gold_sql"]))].append(t["id"])
        sharp = [i for k, ids in sorted(sk_group.items())
                 for i in ids if i in good and any(j in bad for j in ids)]
        fam_has_bad: dict[str, int] = defaultdict(int)
        for t in all_rows:
            if t["id"] in bad:
                fam_has_bad[t["id"].rsplit("-", 1)[0]] += 1
        fam_order = sorted(fam_has_bad, key=lambda f: (-fam_has_bad[f], f))
        by_id = {t["id"]: t for t in all_rows}
        by_fam_correct = {f: [i for i in baseline_correct() if i.rsplit("-", 1)[0] == f]
                          for f in fam_order}
        picked, seen = [], set(sharp)
        for i in sharp:
            picked.append(i)
        for _ in range(2):                            # two per family, spread over rounds
            for f in fam_order:
                if len(picked) >= 20:
                    break
                avail = [i for i in sorted(by_fam_correct[f]) if i not in seen]
                if avail:
                    picked.append(avail[0])
                    seen.add(avail[0])
        out = [by_id[i] for i in picked]
        comp = defaultdict(int)
        for i in picked:
            comp[i.rsplit("-", 1)[0]] += 1
        print(f"  选题构成：同骨架内有对有错 {len(sharp)} 道 + 边界族每族 ≤2 道补足 = {len(out)} 道")
        print(f"  族的分布：{dict(sorted(comp.items(), key=lambda kv: -kv[1]))}")
        print(f"  含失败题的族共 {len(fam_has_bad)} 个：{dict(sorted(fam_has_bad.items(), key=lambda kv: -kv[1]))}")
        return out

    by_family: dict[str, list[dict]] = defaultdict(list)
    for t in tasks:
        by_family[t["id"].rsplit("-", 1)[0]].append(t)
    picked = [ts[:per_family] for _, ts in sorted(by_family.items())]
    return [t for group in picked for t in group]


def build_set(chosen: list[dict]) -> tuple[list[dict], list[str]]:
    out, skipped = [], []
    for t in chosen:
        rec = recover_base(t["question"])
        if rec is None:
            skipped.append(t["id"])
            continue
        base, used = rec
        # The round-trip check: the recovered base must regenerate the stored wording
        # exactly. Without this, an inversion bug would quietly change the question
        # being asked and the whole experiment would measure something else.
        if used.format(q=base, q_lower=lower_first(base)) != t["question"]:
            skipped.append(f"{t['id']} (round-trip mismatch)")
            continue
        for i, text in enumerate(variants(base)):
            out.append({**t, "id": f"{t['id']}#r{i}", "question": text,
                        # Which of these four is the wording the published benchmark
                        # actually used. Taking r0 instead would compare the mean
                        # against a phrasing nobody ran, and the difference would be
                        # an artifact of template ordering rather than of luck.
                        "restability_base": t["id"],
                        "is_published_form": (i == used_index(used))})
    return out, skipped


def tag_of(model: str) -> str:
    """Results are keyed by model *and* sampling mode; see OUT_SET_STEM."""
    return f"restability-{model}-{_CURRENT_PICK}"


_CURRENT_PICK = "families"


def run_model(model: str, workers: int) -> dict:
    cmd = [sys.executable, "-m", "sqlagent.eval.runner", "--provider", "openai",
           "--model", model, "--tasks", str(OUT_SET), "--tag", tag_of(model),
           "--workers", str(workers)]
    proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        print(proc.stdout[-1500:], proc.stderr[-1500:])
        raise SystemExit("runner failed")
    return json.loads((ROOT / "results" / f"{tag_of(model)}.jsonl").read_text(
        encoding="utf-8").splitlines()[0])["_summary"]


def analyse(model: str) -> int:
    """Print what `sqlagent.stats.restability_report` computed - and nothing else.

    Deliberately no arithmetic in this file. The same rates are rendered by
    `report.html`, and a second implementation of one statistic is the exact defect
    three rounds of review kept naming (the report once printed its own noise floor
    with a different definition than `stats.py`).
    """
    r = stats.restability_report(_CURRENT_PICK)
    if r["model"] != model:
        print(f"  [警告] 产物里的模型是 {r['model']}，与 --model {model} 不一致")
    n, forms = r["n_tasks"], r["n_forms"]
    fragile, lucky = r["fragile"], r["lucky"]
    flips = len(fragile) + len(lucky)

    print("")
    print(f"=== 同题重述稳定性（{model}，{n} 题 × {forms} 种问法 = {r['n_runs']} 次运行）===")
    print(f"  {forms} 种问法结果完全一致的题 : {r['agree']}/{n}  ({r['agree']/n:.0%})")
    if r["always_wrong"] == n:
        print("  注意：这批题四种问法下全错——**本组同样没有区分力**，它只说明这些失败与措辞无关。")
    print(f"    其中全对                     : {r['always_right']}")
    print(f"    其中全错                     : {r['always_wrong']}")
    print(f"  **换一种问法就会翻脸的题       : {flips}/{n}  ({flips/n:.0%})**")
    print(f"  问法平均通过率                 : {r['mean_rate']:.1%}")
    print(f"  基准实际采用的那一种问法       : {r['published_rate']:.1%}")
    print(f"  ⇒ 单次抽样与「平均而言」的差   : "
          f"{(r['published_rate'] - r['mean_rate']) * 100:+.1f}pp")
    print(f"\n  基准问法判对、换个说法就错的题 : {len(fragile)}  {fragile[:6]}")
    print(f"  基准问法判错、换个说法反而对的题 : {len(lucky)}  {lucky[:6]}")

    if fragile:
        print(f"\n  在这 {n} 题里，{len(fragile)} 题（{len(fragile)/n:.0%}）属于「基准那种问法判对了，"
              "换一种问法就判错」。")
        if not r["extrapolatable"]:
            print(f"  注意：这 {n} 题是按条件刻意挑的（pick={_CURRENT_PICK}），这个比例**不可外推**；"
                  "能外推的只有按族占比抽的那一组。")
        print(f"  样本只有 {n} 题、每题 {forms} 种问法，区间很宽——**它只用于判断量级，"
              "不足以给出一个可发布的修正系数。**")
    if lucky and not fragile:
        print("")
        print("  这批题是按「baseline 判错」选的，所以基准问法 0% 是选择方式决定的，不是发现。")
        print(f"  有意义的是那 {len(lucky)} 道：**换一种问法它就做对了 —— "
              f"即 {len(lucky)/n:.0%} 的「失败」不是能力缺口，是这一种措辞造成的。**")
        print("  反过来读 pass@1：它低估的部分就在这儿。而低估与高估不是同一批题，"
              "所以不能靠「两边都有一点，抵消了」了事。")
        print(f"  {forms} 种里有 {forms-1} 种都能做对、只有基准那一种失败的题："
              f"{r['robust_lucky']} —— 这些是措辞直接造成的误判。")
        if "understatement_pp" in r:
            print(f"  这 {n} 道是 baseline 全部非 trivial 失败题的**普查**、不是抽样，所以外推不用乘系数："
                  f"{r['understatement_numerator']}/{r['benchmark_tasks']} = "
                  f"{r['understatement_pp']:.2f}pp 是 pass@1 **低估**的量。")
    elif fragile and not lucky:
        print("")
        print(f"  这批题基准问法全对，但 {len(fragile)} 道换个说法就错：pass@1 高估的部分。")

    if "fragile_share" in r:
        lo, hi = r["fragile_ci"]
        a, b = r["overstatement_pp_any"], r["overstatement_pp_mean"]
        alo, ahi = r["overstatement_pp_any_ci"]
        head = r["headline_pass1"]
        print("")
        print("  外推到全卷（只有 correct-representative 这一组可以这么做：它按族占比抽样，"
              "其余各组都是刻意过采样的）")
        print(f"    措辞依赖的判对题占比 {len(fragile)}/{n} = {r['fragile_share']:.1%}   "
              f"Wilson 95% [{lo:.1%}, {hi:.1%}]")
        print(f"    读法 A「换一种说法即判丢」：{head:.1%} × {r['fragile_share']:.1%} = "
              f"**{a:.1f}pp**（区间 {alo:.1f}–{ahi:.1f}pp）")
        print(f"    读法 B「每种问法都算一遍取平均」：{head:.1%} × "
              f"{(r['published_rate']-r['mean_rate'])*100:.1f}pp = **{b:.1f}pp**")
        print(f"    两种读法差 {abs(a-b):.1f}pp——差在「一道题四种问法里错几种」，"
              "所以必须说清问的是哪一个。")
    return 0



def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="deepseek-chat")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--pick", choices=list(stats.RESTABILITY_PICKS), default="families",
                    help="which tasks to re-ask. `families` is the first attempt and it "
                         "measured nothing; `failures` is a census of every baseline "
                         "failure; `correct-representative` is the only proportional "
                         "sample; `correct-boundary` deliberately over-samples instability")
    ap.add_argument("--dry-run", action="store_true", help="build the set, spend nothing")
    ap.add_argument("--analyse-only", action="store_true",
                    help="re-read results/restability-<model>.jsonl and reprint the "
                         "conclusions. Free: a reviewer re-checking these numbers "
                         "should never have to pay for a model run to see them.")
    args = ap.parse_args()

    global OUT_SET, _CURRENT_PICK
    _CURRENT_PICK = args.pick
    OUT_SET = ROOT / "data" / f"{OUT_SET_STEM}-{args.pick}.jsonl"

    tasks = load_tasks()
    chosen = select(tasks, pick=args.pick)
    set_rows, skipped = build_set(chosen)
    print(f"题库问法模板：{len(PARAPHRASE)} 条，去重后 {len(distinct_forms())} 种"
          f"（重复项：{[p for p in set(PARAPHRASE) if PARAPHRASE.count(p) > 1]}）")
    what, extrapolatable = stats.RESTABILITY_PICK_LABELS[args.pick]
    print(f"选中 {len(chosen)} 题（{args.pick} = {what}，无随机）→ 展开 {len(set_rows)} 条")
    print(f"  可外推性：{extrapolatable}")
    if skipped:
        print(f"无法还原裸题面、已跳过：{skipped}")
    OUT_SET.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in set_rows) + "\n",
                       encoding="utf-8", newline="\n")
    if args.dry_run:
        for r in set_rows[:4]:
            print(f"  {r['id']:34} {r['question'][:70]}")
        return 0

    if args.analyse_only:
        if not (ROOT / "results" / f"{tag_of(args.model)}.jsonl").exists():
            raise SystemExit(f"no results/{tag_of(args.model)}.jsonl yet")
        return analyse(args.model)

    summary = run_model(args.model, args.workers)
    print(f"运行完成：{summary['n_tasks']} 题，成本 ${summary['total_cost_usd']}")
    return analyse(args.model)


if __name__ == "__main__":
    raise SystemExit(main())
