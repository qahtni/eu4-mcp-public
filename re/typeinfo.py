#!/usr/bin/env python3
"""Build class hierarchy from _ZTI typeinfo objects -> out/hierarchy.tsv (class, bases...)"""
import sys, struct
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from elfutil import *
ti = {a: n for a, s, t, n in symbols() if n.startswith('_ZTI')}
rows = []
for a, n in ti.items():
    s = byname()[n][1]
    bases = []
    if s == 24:
        b = q(a + 16)
        if b in ti: bases.append((ti[b], 0))
    elif s > 24:
        flags, cnt = struct.unpack('<II', read(a + 16, 8))
        for k in range(cnt):
            b = q(a + 24 + 16 * k); off = q(a + 32 + 16 * k)
            if b in ti: bases.append((ti[b], off >> 8))
    rows.append((n, bases))
names = set([r[0] for r in rows] + [b for r in rows for b, _ in r[1]])
d = dict(zip(names, demangle_many(list(names))))
with open(OUT + '/hierarchy.tsv', 'w') as f:
    for n, bases in sorted(rows):
        f.write(d[n].replace('typeinfo for ', '') + '\t' + '\t'.join(d[b].replace('typeinfo for ', '') + (f'@{o:#x}' if o else '') for b, o in bases) + '\n')
print(len(rows))
