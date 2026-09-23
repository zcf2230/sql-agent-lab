import math
from collections import defaultdict
from sqlagent.stats import load_verdicts, clusters, mcnemar_exact

base, var = load_verdicts("abl2-baseline"), load_verdicts("abl2-3shot")
fam = lambda i: i.rsplit("-", 1)[0]
cl = clusters()
to_cluster = {t: k for k, ts in cl.items() for t in ts}

def agg(keyfn, how):
    g = defaultdict(list)
    for t in var:
        g[keyfn(t)].append(t)
    return ({k: how(bool(base[t]) for t in ts) for k, ts in g.items()},
            {k: how(bool(var[t]) for t in ts) for k, ts in g.items()})

print("=== 族级（19 个族，生成器定义、无人事后挑）===")
for name, how in (("全对才算对", all), ("有一个对就算对", any)):
    b, v = agg(fam, how)
    n = len(v); kb, kv = sum(b.values()), sum(v.values())
    fx, br, p = mcnemar_exact(b, v)
    print(f"  {name:10} n={n}  {kb/n:.1%} -> {kv/n:.1%}  Δ={100*(kv-kb)/n:+.2f}pp  修/坏={fx}/{br}  p={p:.4f}")

print("=== 簇级符号检验（全对才算对）===")
b, v = agg(lambda t: to_cluster[t], all)
d = [(v[k] - b[k]) * 100 for k in v]
pos, neg = sum(1 for x in d if x > 0), sum(1 for x in d if x < 0)
n2 = pos + neg
tail = sum(math.comb(n2, i) for i in range(min(pos, neg) + 1)) / 2 ** n2
print(f"  {pos}↑ {neg}↓ (n={n2})  双侧精确 p={min(1.0, 2*tail):.4f}")
nz = sorted(round(x, 1) for x in d if x)
print(f"  非零簇差异: {nz}")
print(f"  其中跳满 100pp 的单题簇: {sum(1 for x in nz if abs(x) == 100)} 个")

print("=== 簇级比率的 Wilcoxon 符号秩（正态近似，含结校正）===")
absd = sorted(((abs(x), i) for i, x in enumerate(d) if x))
ranks = {}
i = 0
vals = [a for a, _ in absd]
while i < len(vals):
    j = i
    while j + 1 < len(vals) and vals[j + 1] == vals[i]:
        j += 1
    avg = (i + 1 + j + 1) / 2
    for k in range(i, j + 1):
        ranks[absd[k][1]] = avg
    i = j + 1
W = sum(r for (a, idx), r in ranks.items() if d[idx] > 0)
nn = len(ranks)
mu = nn * (nn + 1) / 4
tie_sum = sum(t ** 3 - t for t in [vals.count(v) for v in set(vals)])
sig = math.sqrt(nn * (nn + 1) * (2 * nn + 1) / 24 - tie_sum / 48)
z = (W - mu) / sig
p2 = math.erfc(abs(z) / math.sqrt(2))
print(f"  W={W:.1f}  n={nn}  z={z:+.2f}  双侧 p≈{p2:.4f}")
