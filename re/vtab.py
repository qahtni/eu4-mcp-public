#!/usr/bin/env python3
"""vtab.py <mangled _ZTV...> : print slots"""
import sys
sys.path.insert(0,os.path.dirname(os.path.abspath(__file__)))
from elfutil import *
for nm in sys.argv[1:]:
    a,s,t=byname()[nm]
    print('#',dem(nm),hex(a),hex(s))
    for i in range(0,s,8):
        v=q(a+i); n,o=sym_at(v) if v else (None,None)
        print(f'+{i:#05x} slot{(i-16)//8:3d} {v:#x}', dem(n) if n else '', f'+{o:#x}' if o else '')
