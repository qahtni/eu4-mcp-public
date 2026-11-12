"""Minimal ELF helpers for static RE of the eu4 binary (no pyelftools needed).
Usage: import sys; sys.path.insert(0, '<repo>/re'); from elfutil import *
"""
import struct, subprocess, bisect, os, functools

BIN = os.path.join(os.path.expanduser(os.environ.get('EU4_GAME_DIR', '~/games/eu4')), 'eu4')   # your own copy of the game binary
OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'out')   # gitignored: generated from YOUR game binary
OUT = OUT_DIR

_data = None
def data():
    global _data
    if _data is None:
        with open(BIN, 'rb') as f:
            _data = f.read()
    return _data

@functools.lru_cache(None)
def sections():
    d = data()
    e_shoff, = struct.unpack_from('<Q', d, 0x28)
    e_shentsize, e_shnum, e_shstrndx = struct.unpack_from('<HHH', d, 0x3a)
    secs = []
    for i in range(e_shnum):
        o = e_shoff + i * e_shentsize
        name, typ, flags, addr, off, size = struct.unpack_from('<IIQQQQ', d, o)
        secs.append([name, typ, flags, addr, off, size])
    so = secs[e_shstrndx][4]
    res = []
    for name, typ, flags, addr, off, size in secs:
        n = d[so + name: d.index(b'\0', so + name)].decode()
        res.append((n, typ, flags, addr, off, size))
    return res

def v2o(va):
    for n, typ, flags, addr, off, size in sections():
        if addr and addr <= va < addr + size and typ != 8:  # not NOBITS
            return off + (va - addr)
    return None

def read(va, n):
    o = v2o(va)
    if o is None:
        return None
    return data()[o:o + n]

def q(va):
    b = read(va, 8)
    return struct.unpack('<Q', b)[0] if b else None

def i32(va):
    b = read(va, 4)
    return struct.unpack('<i', b)[0] if b else None

def cstr(va, maxn=256):
    b = read(va, maxn)
    if b is None:
        return None
    return b.split(b'\0')[0].decode('latin1')

@functools.lru_cache(None)
def symbols():
    """list of (addr, size, type, mangled) sorted by addr; from nm -S --defined-only"""
    res = []
    with open(os.path.join(OUT, 'idx_raw.txt')) as f:
        for line in f:
            p = line.split()
            if len(p) == 4:
                res.append((int(p[0], 16), int(p[1], 16), p[2], p[3]))
            elif len(p) == 3:
                res.append((int(p[0], 16), 0, p[1], p[2]))
    res.sort()
    return res

@functools.lru_cache(None)
def byname():
    d = {}
    for a, s, t, n in symbols():
        d.setdefault(n, (a, s, t))
    return d

@functools.lru_cache(None)
def _addrs():
    return [s[0] for s in symbols()]

@functools.lru_cache(None)
def plt():
    d = {}
    for l in open(os.path.join(OUT, 'plt.tsv')):
        a, n = l.rstrip('\n').split('\t'); d[int(a, 16)] = n
    return d

def sym_at(va):
    """symbol containing va -> (mangled, offset) or (None, None)"""
    if va in plt():
        return plt()[va], 0
    syms = symbols(); A = _addrs()
    i = bisect.bisect_right(A, va) - 1
    while i >= 0:
        a, s, t, n = syms[i]
        if a <= va < a + max(s, 1):
            return n, va - a
        if a < va - 0x100000:
            break
        i -= 1
    return None, None

_dem_cache = {}
def demangle_many(names):
    todo = [n for n in names if n not in _dem_cache]
    if todo:
        p = subprocess.run(['c++filt'], input='\n'.join(todo) + '\n', capture_output=True, text=True)
        for a, b in zip(todo, p.stdout.split('\n')):
            _dem_cache[a] = b
    return [_dem_cache[n] for n in names]

def dem(n):
    return demangle_many([n])[0] if n else n

def disasm(va, size, md=None):
    import capstone
    if md is None:
        md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_64)
    return list(md.disasm(read(va, size), va))

def fmt_insns(insns, annotate=True):
    out = []
    for i in insns:
        s = f'{i.address:x}: {i.mnemonic} {i.op_str}'
        if annotate:
            import re
            for m in re.findall(r'0x[0-9a-f]{6,}', i.op_str):
                v = int(m, 16)
                if i.mnemonic.startswith('call') or i.mnemonic.startswith('j') or 'rip' not in i.op_str:
                    n, off = sym_at(v)
                    if n:
                        s += f'    ; {dem(n)}' + (f'+{off:#x}' if off else '')
            if 'rip' in i.op_str:
                import re as _r
                mm = _r.search(r'rip ([+-]) (0x[0-9a-f]+)', i.op_str)
                if mm:
                    v = i.address + i.size + (int(mm.group(2), 16) * (1 if mm.group(1) == '+' else -1))
                    n, off = sym_at(v)
                    s += f'    ; [{v:#x}] ' + (dem(n) + (f'+{off:#x}' if off else '') if n else '')
        out.append(s)
    return out

def func(name_or_addr, maxlen=None):
    if isinstance(name_or_addr, int):
        n, off = sym_at(name_or_addr)
        a, s, t = byname()[n]
    else:
        a, s, t = byname()[name_or_addr]
    if maxlen:
        s = min(s, maxlen)
    return a, s, fmt_insns(disasm(a, s))
