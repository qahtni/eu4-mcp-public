#!/usr/bin/env python3
"""ctx.py <before> <after> addr... : print annotated instructions around call sites"""
import sys;sys.path.insert(0,os.path.dirname(os.path.abspath(__file__)))
from elfutil import *
B,A=int(sys.argv[1]),int(sys.argv[2])
for t in sys.argv[3:]:
    va=int(t,16); n,off=sym_at(va); a,s,_=byname()[n]
    ins=disasm(a,s); idx=[i for i,x in enumerate(ins) if x.address==va]
    print(f'## {va:#x} in {dem(n)[:120]} +{off:#x}')
    if not idx: print('  (not on insn boundary)'); continue
    k=idx[0]
    for l in fmt_insns(ins[max(0,k-B):k+A+1]): print('  '+l[:170])
