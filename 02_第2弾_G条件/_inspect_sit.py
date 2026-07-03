# -*- coding: utf-8 -*-
"""指定JSONの指定Sitsumon番号ノードを詳細ダンプ。usage: python3 _inspect_sit.py <json> <sitno>[,<sitno>...]"""
import json, sys, io
sys.stdout.reconfigure(encoding='utf-8')

jpath = sys.argv[1]
sitnos = [int(x) for x in sys.argv[2].split(',')]
with open(jpath, encoding='utf-8-sig') as f:
    bj = json.load(f)

# locate sitsumon list
def find_sits(obj):
    found = []
    def rec(o):
        if isinstance(o, dict):
            if 'SitsumonNo' in o and ('SitsumonKind' in o or 'Mesho' in o):
                found.append(o)
            for v in o.values(): rec(v)
        elif isinstance(o, list):
            for v in o: rec(v)
    rec(obj)
    return found

sits = find_sits(bj)
byno = {}
for s in sits:
    byno.setdefault(s.get('SitsumonNo'), s)

for sn in sitnos:
    s = byno.get(sn)
    print("="*100)
    if not s:
        print(f"Sit {sn}: NOT FOUND"); continue
    print(f"Sit {sn}: {s.get('Mesho')}")
    for k in ('SitsumonNo','SitsumonKind','SitsumonExecuteKind','LevelVarName','SitsumonFlags',
              'DefaultSelect','AutoSelectJoken','Biko','VarName','ExpVarName','Keisan','KeisanShiki'):
        if k in s:
            print(f"  {k} = {json.dumps(s[k], ensure_ascii=False)}")
    # choices
    for ck in ('SentakushiList','Sentakushi','Choices','SitsumonSentakushiList'):
        if ck in s and isinstance(s[ck], list):
            print(f"  --- {ck} ({len(s[ck])}) ---")
            for i, c in enumerate(s[ck]):
                if isinstance(c, dict):
                    label = c.get('Mesho') or c.get('Name') or c.get('Label')
                    joken = c.get('AutoSelectJoken') or c.get('Joken')
                    selflag = {kk:c[kk] for kk in ('SelectFlag','CanSelect','Selectable','Flags','ExpValue','Value') if kk in c}
                    print(f"    [{i}] {label}  {json.dumps(selflag, ensure_ascii=False)}  joken={json.dumps(joken, ensure_ascii=False)}")
    # dump all keys for reference
    print(f"  (all keys: {sorted(s.keys())})")
