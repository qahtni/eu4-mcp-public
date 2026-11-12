import sys, json
from eu4mcp.hookclient import HookClient, HookError
from eu4mcp.diplo import Diplo
h = HookClient(timeout=10); d = Diplo(h)
for expr in sys.argv[1:]:
    try:
        r = eval(expr)
        s = json.dumps(r, default=str, ensure_ascii=False)
        print(f">> {expr}\n{s[:int(900)]}{' …('+str(len(s))+')' if len(s)>900 else ''}")
    except HookError as e:
        print(f">> {expr}\nHOOKERROR {e}")
    except Exception as e:
        print(f">> {expr}\nEXC {type(e).__name__} {e}"); break
print('alive', h.info()['date'])
