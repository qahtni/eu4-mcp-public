#!/usr/bin/env python3
"""Dump gConsoleCmdData (0x17a entries x 0xa0) -> docs/re/console_commands.txt"""
import sys;sys.path.insert(0,os.path.dirname(os.path.abspath(__file__)))
from elfutil import *
base,_,_=byname()['gConsoleCmdData']
rows=[]
for i in range(0x17a):
    e=base+i*0xa0
    rel=read(e,1)[0]; name=cstr(q(e+8)) if q(e+8) else None
    nargs=i32(e+0x10)
    h=q(e+0x38); hn=dem(sym_at(h)[0]) if h else ''
    desc=cstr(q(e+0x30)) if q(e+0x30) else ''
    rows.append(f'{name}\trelease_ok={rel}\tfield10={nargs}\thandler={h:#x} {hn}\tdesc={(desc or "")[:80]}')
hdr = [f'# gConsoleCmdData @ {base:#x} (.data), 0x17a (378) entries x 0xa0 bytes (CConsoleCmd::SOldCommandData).',
 '# Source: CConsoleCmdImpl::GetDataCount()@0x1a32e9c returns 0x17a; CConsoleCmdImpl::GetData(int)@0x1a32ea2 returns 0x2f95030+i*0xa0.',
 '# Registered by CEU3Idler::RestoreDeviceObjects()@0x137c8b8 (+0xbf call AddCommands).',
 '# release_ok = byte +0. Execute@0x253f825: if 0 and manager+0x18 (SetIsRelease) set -> "Command available only for developers."',
 '# field10 = int +0x10 (meaning not established). handler = +0x38, old signature SResult(*)(CArray<CString> const&) (via TheGlueFunction).',
 '# Not listed: commands added at runtime with AddCommand(SCommandData const&) by RegisterSocialConsoleCommands, CPdxCrashReporter, CPopsLogin, NetInit, SRenderTypeConsoleCommand.',
 '# name\trelease_ok\tfield10\thandler\tdesc']
open(os.path.join(OUT_DIR,'console_commands.txt'),'w').write('\n'.join(hdr)+'\n'+'\n'.join(rows)+'\n')
print(sum(1 for r in rows if "release_ok=1" in r),'release_ok of',len(rows))
