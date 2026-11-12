#!/usr/bin/env python3
"""Build docs/re/commands.tsv: one row per CCommand-derived class (from RTTI) plus other classes named *Command with ctors."""
import os
import sys,re,collections
sys.path.insert(0,os.path.dirname(os.path.abspath(__file__)))
from elfutil import *
OUTF=os.path.join(OUT_DIR,'commands.tsv')
desc={}; 
for l in open(OUT+'/command_descendants.txt'):
    c,p=l.rstrip('\n').split('\t'); desc[c]=p
# all function symbols, demangled
funcs=[(a,s,t,n) for a,s,t,n in symbols() if t in 'TtWw' and n.startswith('_Z')]
dems=demangle_many([n for a,s,t,n in funcs])
ctors=collections.defaultdict(list)   # class -> [(addr, mangled, demangled)]
methods=collections.defaultdict(dict)
for (a,s,t,n),d in zip(funcs,dems):
    m=re.match(r'^((?:[\w<>, :()&*\-]+?::)?)(\w+)::(\w+)\((.*)\)( const)?$',d)
    if not m: continue
    ns,cls,meth,args,cn=m.groups()
    full=(ns+cls)
    if meth==cls:
        ctors[full].append((a,n,d,args))
    elif False:
        pass
    elif meth in ('Execute','IsValid','Clone','GetSpecificTokenType','MustSynch'):
        methods[full][meth]=a
tmpl_ctor=collections.defaultdict(list); tmpl_clone={}
bases=set(desc.values())
for (a,s_,t,n),d in zip(funcs,dems):
    if d.startswith('CCommandTemplate<'):
        for b in bases:
            if d.startswith(b+'::CCommandTemplate(') and 'std::tuple' not in d and not d.endswith(b+' const&)'):
                tmpl_ctor[b].append((a,n,d,d[len(b)+len('::CCommandTemplate('):-1]))
            elif d.startswith(b+'::Clone('):
                tmpl_clone[b]=a
            elif d.startswith(b+'::Execute()'):
                methods[b]['Execute']=a
            elif d.startswith(b+'::IsValid()'):
                methods[b]['IsValid']=a
# sizes: from new-sites whose next call is a ctor; and from Clone()
size_by_ctor=collections.defaultdict(collections.Counter)
for l in open(OUT+'/newsites.tsv'):
    site,caller,sz,nxt=l.rstrip('\n').split('\t')
    if sz!='None': size_by_ctor[nxt][int(sz)]+=1
classes=set(desc)|{c for c in ctors if re.search(r'Command$',c.split('::')[-1])}
rows=[]
for c in sorted(classes):
    if c.startswith('CCommandTemplate<'): continue
    b=desc.get(c,'')
    for k in ('Execute','IsValid'):
        if k not in methods.get(c,{}) and k in methods.get(b,{}): methods[c][k]=methods[b][k]
    cs=ctors.get(c,[]) or tmpl_ctor.get(desc.get(c,''),[])
    # dedupe C1/C2 at same addr and same signature
    seen={}
    for a,n,d,args in cs:
        key=args
        if key not in seen or 'C1E' in n: seen[key]=(a,n,d,args)
    sizes=collections.Counter()
    for a,n,d,args in cs: sizes.update(size_by_ctor.get(n,{}))
    clone_sz=None
    ca=methods.get(c,{}).get('Clone') or tmpl_clone.get(desc.get(c,''))
    if ca:
        nm,_=sym_at(ca); s=byname()[nm][1]
        ins=disasm(ca,min(s,0x80))
        for i in ins:
            if i.mnemonic=='mov' and i.op_str.startswith('edi, '):
                try: clone_sz=int(i.op_str.split(', ')[1],16)
                except: pass
            if i.mnemonic=='call': break
    base=desc.get(c,'-')
    nondef=[v for k,v in sorted(seen.items()) if k not in ('', f'{c.split("::")[-1]} const&', f'{c} const&')]
    show=nondef or list(seen.values())
    for a,n,d,args in (show or [(0,'-','(no ctor symbol: inlined at call sites)','-')]):
        sz = sizes.most_common(1)[0][0] if sizes else None
        rows.append((c, base, n, d, f'{a:#x}' if a else '-', 
                     f'{sz:#x}' if sz else '-', f'{clone_sz:#x}' if clone_sz else '-',
                     f'{methods[c]["Execute"]:#x}' if 'Execute' in methods.get(c,{}) else '-',
                     f'{methods[c]["IsValid"]:#x}' if 'IsValid' in methods.get(c,{}) else '-'))
with open(OUTF,'w') as f:
    f.write('# class\tdirect_base(RTTI; - = not CCommand-derived by RTTI)\tctor_mangled\tctor_demangled\tctor_addr\tsize_from_new_site\tsize_from_Clone\tExecute_addr\tIsValid_addr\n')
    f.write('# One row per (class, non-default/non-copy ctor signature); default/copy ctors only listed when no other ctor exists. C1/C2 deduped (C1 kept).\n')
    for r in rows: f.write('\t'.join(r)+'\n')
print(len(classes),'classes',len(rows),'rows', len(set(r[0] for r in rows)),'distinct')
print(sum(1 for c in classes if c in desc),'in CCommand tree')
