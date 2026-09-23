"""Paired statistics for run comparison.

Lives in the package, not in a script, because two consumers must never disagree:
`scripts/significance.py` prints these numbers, `figures.py` draws them, and the
README quotes them. Duplicating the maths across those three is exactly the
"two sources of truth" failure this project keeps rediscovering.

Which runs get paired is declared once, here, with the reason for each pin.
"""

from __future__ import annotations

import json
import math
import random
import re
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RESULTS = ROOT / "results"

# (label, baseline tag, variant tag). Names are pinned, not globbed.
#
# `abl-baseline` is deliberately excluded even though it is the same nominal config:
# it predates the few-shot wiring, so pairing it with `abl2-3shot` compares two code
# versions and calls the difference an ablation. A reviewer who made that pairing got
# p=0.092 instead of 0.057, which is how this list came to exist.
COMPARISONS = [
    ("deepseek: 3-shot vs baseline", "abl2-baseline", "abl2-3shot"),
    ("deepseek: self-repair OFF vs baseline", "abl2-baseline", "abl2-norepair"),
    ("qwen-flash: 3-shot vs baseline", "qwen-baseline", "qwen-3shot"),
]

# Nominally identical runs. Their spread is the noise floor; nothing smaller than it
# is a result. Four entries because the config digest changed twice mid-project for
# documented reasons, and each earlier baseline is still a same-config re-measurement
# of the same 192 tasks.
BASELINES = ["baseline-v2", "baseline-v3", "abl-baseline", "abl2-baseline"]


def load_verdicts(tag: str) -> dict[str, bool]:
    path = RESULTS / f"{tag}.jsonl"
    if not path.exists():
        raise FileNotFoundError(f"results/{tag}.jsonl is missing; run the eval before quoting it")
    rows = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
    out = {r["id"]: bool(r["correct"]) for r in rows[1:] if "id" in r}
    if not out:
        raise ValueError(f"results/{tag}.jsonl has no per-task rows")
    return out


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score interval for a binomial proportion.

    Not the normal approximation: at n=192 near 0.9 the Wald interval has poor
    coverage, which is the mistake being critiqued elsewhere in this project.
    """
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    denom = 1 + z * z / n
    centre = p + z * z / (2 * n)
    margin = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return ((centre - margin) / denom, (centre + margin) / denom)


def mcnemar_exact(a: dict[str, bool], b: dict[str, bool]) -> tuple[int, int, float]:
    """Two-sided exact McNemar on paired per-task correctness.

    Returns (fixed, broken, p). Exact rather than chi-square: the discordant count
    can be small, where the chi-square approximation is unreliable.
    """
    common = set(a) & set(b)
    fixed = sum(1 for i in common if b[i] and not a[i])
    broken = sum(1 for i in common if a[i] and not b[i])
    n = fixed + broken
    if n == 0:
        return 0, 0, 1.0
    tail = sum(math.comb(n, i) for i in range(min(fixed, broken) + 1)) / 2**n
    return fixed, broken, min(1.0, 2 * tail)


def comparison(label: str, base_tag: str, variant_tag: str) -> dict:
    a, b = load_verdicts(base_tag), load_verdicts(variant_tag)
    if set(a) != set(b):
        raise ValueError(f"{base_tag} and {variant_tag} do not cover the same task ids")
    n = len(b)
    kb = sum(b.values())
    fixed, broken, p = mcnemar_exact(a, b)
    lo, hi = wilson(kb, n)
    return {
        "label": label, "base_tag": base_tag, "variant_tag": variant_tag,
        "n": n, "base_k": sum(a.values()), "base_rate": sum(a.values()) / n,
        "variant_k": kb, "variant_rate": kb / n, "ci_low": lo, "ci_high": hi,
        "delta_pp": (kb / n - sum(a.values()) / n) * 100,
        "fixed": fixed, "broken": broken, "p_value": p,
        "significant": p < 0.05,
    }


def all_comparisons() -> list[dict]:
    return [comparison(*c) for c in COMPARISONS]


def noise_floor() -> dict:
    """Largest disagreement between nominally identical baseline runs, in pp."""
    sets = {t: load_verdicts(t) for t in BASELINES}
    pairs = []
    names = list(sets)
    for i, x in enumerate(names):
        for y in names[i + 1:]:
            if set(sets[x]) != set(sets[y]):
                continue
            d = sum(1 for k in sets[x] if sets[x][k] != sets[y][k])
            pairs.append((d, x, y))
    worst = max(pairs) if pairs else (0, "", "")
    n = len(sets[BASELINES[-1]])
    return {"pairs": pairs, "worst_tasks": worst[0], "worst_pair": (worst[1], worst[2]),
            "worst_pp": worst[0] / n * 100, "n": n}


# ---------------------------------------------------------------------------
# Effective sample size: the assumption McNemar does not say out loud
#
# McNemar treats the 192 tasks as 192 independent paired observations. They are not:
# the benchmark is template-generated, so `category_slice` is 5 SQL skeletons with 7
# substitutions each and `date_bucket` is 3 skeletons, 20 of its 22 questions being one
# skeleton with a different month. A model that can write that JOIN gets all of them;
# one that cannot gets none. Verdicts are correlated inside a skeleton, which is exactly
# what the test assumes away - so n=192 overstates the evidence, and the direction of the
# error is the flattering one.
#
# Collapsing to skeletons is a defensible alternative, not the true n: it is also the
# unit the generator was parameterised over. Both are reported because the choice of
# aggregation moves p across 0.05, and a claim that survives only under one of two
# reasonable aggregations is not a claim.
# ---------------------------------------------------------------------------

TASKS = ROOT / "data" / "tasks.jsonl"
_LITERAL_STRING = re.compile(r"'[^']*'")
_NUMBER = re.compile(r"\b\d+\b")
_WS = re.compile(r"\s+")


def skeleton(gold_sql: str) -> str:
    """The gold SQL with every literal value removed: what varies between template instances."""
    s = _LITERAL_STRING.sub("?", gold_sql)
    s = _NUMBER.sub("#", s)
    return _WS.sub(" ", s).strip().lower()


def clusters(tasks_path: Path = TASKS) -> dict[tuple[str, str], list[str]]:
    """(family, gold skeleton) -> task ids. The unit a template benchmark really samples."""
    rows = [json.loads(l) for l in tasks_path.read_text(encoding="utf-8").splitlines() if l.strip()]
    out: dict[tuple[str, str], list[str]] = {}
    for r in rows:
        tid = r["id"]
        out.setdefault((tid.rsplit("-", 1)[0], skeleton(r["gold_sql"])), []).append(tid)
    return out


def cluster_sensitivity(base_tag: str, variant_tag: str,
                        tasks_path: Path = TASKS) -> list[dict]:
    """The same comparison under task-level and two cluster-level aggregations."""
    base, variant = load_verdicts(base_tag), load_verdicts(variant_tag)
    groups = clusters(tasks_path)
    n_tasks = len(variant)

    def row(label: str, a: dict, b: dict) -> dict:
        n = len(b)
        ka, kb = sum(a.values()), sum(b.values())
        fixed, broken, p = mcnemar_exact(a, b)
        return {"label": label, "n": n, "base_rate": ka / n, "variant_rate": kb / n,
                "delta_pp": (kb - ka) / n * 100, "fixed": fixed, "broken": broken,
                "p_value": p, "significant": p < 0.05}

    task_level = {tid: (tid in base and base[tid]) for tid in variant}
    out = [row("题级（McNemar 的默认口径）", task_level, {t: bool(variant[t]) for t in variant})]
    for agg_name, agg in (("全对才算对", all), ("有一个对就算对", any)):
        cb = {k: agg(bool(base.get(t)) for t in ts) for k, ts in groups.items()}
        cv = {k: agg(bool(variant.get(t)) for t in ts) for k, ts in groups.items()}
        out.append(row(f"簇级·{agg_name}（{len(groups)} 簇）", cb, cv))
    out[0]["note"] = f"{n_tasks} 题去重后只有 {len(groups)} 个骨架"
    return out


def cluster_bootstrap(base_tag: str, variant_tag: str, iterations: int = 10_000,
                      seed: int = 20260921) -> dict:
    """Paired bootstrap CI for the cluster-level accuracy difference.

    This exists because it is the only *positive* statement this data supports that
    does not depend on picking a threshold or an aggregation: the p-values move across
    0.05 depending on how the questions are grouped, so they cannot carry the claim -
    but an interval on the mean per-template improvement can, and it is worth reporting
    that the interval excludes zero while the sign test on the same clusters does not.
    The two disagree because the difference distribution is wildly skewed (7 of the 12
    non-zero clusters are single-question clusters that jump a whole 100pp), and a
    report that showed only the favourable one would be cherry-picking by accident.

    Deterministic: seeded, and resampling clusters (not questions), which is the whole
    point of the exercise.
    """
    base, variant = load_verdicts(base_tag), load_verdicts(variant_tag)
    groups = clusters()
    rng = random.Random(seed)
    keys = list(groups)
    per_cluster = [
        (sum(1 for t in groups[k] if variant[t]) / len(groups[k]),
         sum(1 for t in groups[k] if base[t]) / len(groups[k]))
        for k in keys
    ]
    n = len(per_cluster)
    deltas = []
    for _ in range(iterations):
        s = 0.0
        for _ in range(n):                     # sample with replacement, paired within a cluster
            v, b = per_cluster[rng.randrange(n)]
            s += v - b
        deltas.append(s / n)
    deltas.sort()
    lo = deltas[int(0.025 * iterations)]
    hi = deltas[int(0.975 * iterations)]
    point = sum(v - b for v, b in per_cluster) / n
    return {"point_pp": point * 100, "ci_low_pp": lo * 100, "ci_high_pp": hi * 100,
            "excludes_zero": lo > 0 or hi < 0, "iterations": iterations, "clusters": n}


def family_sensitivity(base_tag: str, variant_tag: str) -> list[dict]:
    """The coarsest aggregation nobody can be accused of choosing: the 19 template
    families, fixed in the generator before any model was called.

    Reported because it is the one scale on which the effect vanishes cleanly
    (one family changed, none reversed, p=1.0) - and the honest reading is not
    "therefore nothing happened" but "the signal lives in two families, so describe
    it as that rather than as a benchmark-wide improvement".
    """
    base, variant = load_verdicts(base_tag), load_verdicts(variant_tag)
    out = []
    for name, how in (("全对才算对", all), ("有一个对就算对", any)):
        groups: dict[str, list[str]] = {}
        for t in variant:
            groups.setdefault(t.rsplit("-", 1)[0], []).append(t)
        b = {k: how(bool(base.get(t)) for t in ts) for k, ts in groups.items()}
        v = {k: how(bool(variant[t]) for t in ts) for k, ts in groups.items()}
        n = len(v)
        fixed, broken, p = mcnemar_exact(b, v)
        out.append({"label": f"族级·{name}", "n": n,
                    "base_rate": sum(b.values()) / n, "variant_rate": sum(v.values()) / n,
                    "delta_pp": (sum(v.values()) - sum(b.values())) / n * 100,
                    "fixed": fixed, "broken": broken, "p_value": p, "significant": p < 0.05})
    return out


def cluster_ratio_tests(base_tag: str, variant_tag: str) -> dict:
    """Sign test and Wilcoxon on per-cluster accuracy *ratios*.

    These exist because the all-or-nothing cluster aggregation is McNemar again - the
    sign test and the paired test coincide on binary outcomes, so it adds no
    independent evidence. Ratios keep the magnitude, and magnitude is where these two
    disagree: the bootstrap interval excludes zero while the sign test does not reject.
    That disagreement is the finding, not an inconvenience to average away.
    """
    base, variant = load_verdicts(base_tag), load_verdicts(variant_tag)
    diffs = []
    for _k, ts in clusters().items():
        rb = sum(bool(base.get(t)) for t in ts) / len(ts)
        rv = sum(bool(variant.get(t)) for t in ts) / len(ts)
        if rv != rb:
            diffs.append((rv - rb) * 100)
    pos = sum(1 for x in diffs if x > 0)
    neg = sum(1 for x in diffs if x < 0)
    n = pos + neg
    tail = sum(math.comb(n, i) for i in range(min(pos, neg) + 1)) / 2 ** n if n else 1.0
    sign_p = min(1.0, 2 * tail)

    vals = sorted(abs(x) for x in diffs)
    def rank(v: float) -> float:
        lo = vals.index(v)
        hi = lo
        while hi + 1 < len(vals) and vals[hi + 1] == v:
            hi += 1
        return (lo + 1 + hi + 1) / 2
    w_pos = sum(rank(abs(x)) for x in diffs if x > 0)
    tie = sum(t ** 3 - t for t in Counter(vals).values())
    sd = math.sqrt(len(vals) * (len(vals) + 1) * (2 * len(vals) + 1) / 24 - tie / 48)
    z = (w_pos - len(vals) * (len(vals) + 1) / 4) / sd if sd else 0.0
    return {"nonzero_clusters": n, "up": pos, "down": neg, "sign_p": sign_p,
            "wilcoxon_w": w_pos, "wilcoxon_z": z,
            "wilcoxon_p": math.erfc(abs(z) / math.sqrt(2)),
            "differences_pp": sorted(round(x, 1) for x in diffs),
            "method_note": "Wilcoxon 用正态近似 + 结校正，未加连续性校正；加与不加会在 0.023/0.031 之间移动"}
