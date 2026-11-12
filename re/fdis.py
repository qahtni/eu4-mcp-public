#!/usr/bin/env python3
"""dis.py <mangled-name|0xaddr> [outfile] [maxbytes] : annotated disassembly of one function."""
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from elfutil import *

t = sys.argv[1]
arg = int(t, 16) if t.startswith('0x') else t
mx = int(sys.argv[3]) if len(sys.argv) > 3 else None
a, s, lines = func(arg, mx)
n, _ = sym_at(a)
hdr = f'# {n}\n# {dem(n)}\n# addr {a:#x} size {s:#x}'
txt = hdr + '\n' + '\n'.join(lines) + '\n'
if len(sys.argv) > 2 and sys.argv[2] != '-':
    open(sys.argv[2], 'w').write(txt)
    print(hdr, f'\n# {len(lines)} insns -> {sys.argv[2]}')
else:
    print(txt)
