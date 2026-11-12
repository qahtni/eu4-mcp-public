#!/usr/bin/env python3
"""docs/re/diplomatic_actions.tsv: CDiplomaticAction subclasses with ctor, Clone size, IsValid, GetAIAcceptanceDerived, GetToken"""
import os
import sys,re,collections
sys.path.insert(0,os.path.dirname(os.path.abspath(__file__)))
from elfutil import *
subs=[l.split('\t')[0] for l in open(OUT+'/hierarchy.tsv') if l.rstrip('\n').split('\t')[1:2]==['CDiplomaticAction']]
funcs=[(a,s,t,n) for a,s,t,n in symbols() if t in 'TtWw' and n.startswith('_Z')]
dems=demangle_many([n for *_,n in funcs])
info=collections.defaultdict(lambda: collections.defaultdict(list))
for (a,s,t,n),d in zip(funcs,dems):
    m=re.match(r'^(\w+)::(\w+)\((.*)\)( const)?$',d)
    if m: info[m.group(1)][m.group(2)].append((a,n,d))
rows=[]
for c in sorted(subs):
    I=info[c]
    ct=[x for x in I.get(c,[]) if not x[2].endswith(f'({c} const&)')]
    seen={}
    for a,n,d in ct: seen.setdefault(d,(a,n,d))
    sz='-'
    if I.get('Clone'):
        for i in disasm(I['Clone'][0][0],0x40):
            if i.mnemonic=='mov' and i.op_str.startswith('edi, 0x'): sz=i.op_str.split(', ')[1]; break
            if i.mnemonic=='call': break
    g=lambda k: f"{I[k][0][0]:#x}" if I.get(k) else '-'
    for a,n,d in (list(seen.values()) or [(0,'-','(no ctor symbol)')]):
        rows.append([c,d,n,f'{a:#x}' if a else '-',sz,g('IsValid'),g('IsSelectable'),g('GetAIAcceptanceDerived'),g('GetAIAcceptance'),g('ExecuteDerived'),g('GetToken')])
with open(os.path.join(OUT_DIR,'diplomatic_actions.tsv'),'w') as f:
    f.write('# class\tctor_demangled\tctor_mangled\tctor_addr\tsize(Clone new)\tIsValid(CString*,bool)\tIsSelectable(CString*,bool)\tGetAIAcceptanceDerived(CArray<CDiploChance>*,bool)\tGetAIAcceptance()\tExecuteDerived()\tGetToken()\n# "-" in a method column = not overridden (inherits CDiplomaticAction default)\n')
    for r in rows: f.write('\t'.join(r)+'\n')
print(len(subs),len(rows))
