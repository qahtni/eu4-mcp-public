#!/usr/bin/env python3
"""Parse out/text.dis -> out/calls.tsv (site, caller, target_addr, target_name) for call/jmp-to-symbol.
Also out/newsites.tsv: for each call to _Znwm, the last 'mov edi,N' within previous 6 insns and the next call target after it (within 40 insns)."""
import os
import re
OUT=os.path.join(os.path.dirname(os.path.abspath(__file__)),'out')
hdr=re.compile(r'^([0-9a-f]+) <(.+)>:$')
ins=re.compile(r'^\s*([0-9a-f]+):\s+(\S+)\s*(.*)$')
cur=None
calls=open(OUT+'/calls.tsv','w'); news=open(OUT+'/newsites.tsv','w')
window=[]; pending=[]
for line in open(OUT+'/text.dis'):
    m=hdr.match(line)
    if m:
        cur=m.group(2); window=[]; pending=[]; continue
    m=ins.match(line)
    if not m: continue
    a,mn,ops=m.groups()
    window.append((a,mn,ops)); window=window[-8:]
    if mn in('call','jmp') or mn.startswith('call'):
        t=re.match(r'([0-9a-f]+) <(.+)>',ops)
        if t:
            tgt=t.group(2)
            calls.write(f'{a}\t{cur}\t{mn}\t{t.group(1)}\t{tgt}\n')
            # resolve pending new-sites: first call after new that is not new
            for p in pending:
                if p[3] is None and tgt not in ('_Znwm@plt','_Znwm'):
                    p[3]=tgt
            done=[p for p in pending if p[3] is not None]
            for p in done:
                news.write(f'{p[0]}\t{p[1]}\t{p[2]}\t{p[3]}\n')
            pending=[p for p in pending if p[3] is None]
            if tgt.startswith('_Znwm'):
                size=None
                for wa,wmn,wops in reversed(window[:-1]):
                    mm=re.match(r'mov\s+\$0x([0-9a-f]+),%edi$',wmn+' '+wops) or re.match(r'\$0x([0-9a-f]+),%edi$',wops) if wmn=='mov' else None
                    if mm: size=int(mm.group(1),16); break
                pending.append([a,cur,size,None])
calls.close(); news.close()
