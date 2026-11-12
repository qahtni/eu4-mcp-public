#!/usr/bin/env python3
"""callers.py <mangled> [depth]: callers tree (unique functions) from out/calls.tsv"""
import os
import sys,collections
sys.path.insert(0,os.path.dirname(os.path.abspath(__file__)))
from elfutil import dem
by=collections.defaultdict(set)
for l in open(''+os.path.join(os.path.dirname(os.path.abspath(__file__)),'out')+'/calls.tsv'):
    s,c,mn,ta,tn=l.rstrip('\n').split('\t')
    if '+0x' in tn: continue
    by[tn].add(c)
def walk(n,d,maxd,seen):
    for c in sorted(by.get(n,())):
        print('  '*d+dem(c)[:140]+('  [seen]' if c in seen else ''))
        if c not in seen and d+1<maxd:
            seen.add(c); walk(c,d+1,maxd,seen)
walk(sys.argv[1],0,int(sys.argv[2]) if len(sys.argv)>2 else 3,set())
