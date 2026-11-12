#!/usr/bin/env python3
"""rng.py <file> <start hex> <end hex>: print lines in addr range with string annotation"""
import sys,re;sys.path.insert(0,os.path.dirname(os.path.abspath(__file__)))
from elfutil import *
lo,hi=int(sys.argv[2],16),int(sys.argv[3],16)
for l in open(sys.argv[1]).read().split('\n'):
    m=re.match(r'([0-9a-f]+):',l)
    if m and lo<=int(m.group(1),16)<hi:
        for s in re.findall(r'0x2[5-9][0-9a-f]{5}\b',l):
            v=int(s,16)
            if sym_at(v)[0] is None:
                c=cstr(v)
                if c: l+=f'  "{c[:60]}"'
        print(l)
