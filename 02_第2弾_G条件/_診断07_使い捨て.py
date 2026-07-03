# -*- coding: utf-8 -*-
import csv, os, re, sys, io, contextlib
sys.stdout.reconfigure(encoding='utf-8')
ROOT = r"C:\Users\imoo\Desktop\ClaudeCode\14.歩掛Jsonからテストケース作成可能か【進行中】"
GENDIR = os.path.join(ROOT, "02_第2弾_G条件")
sys.path.insert(0, GENDIR)
for p in ('engine', 'step2_proposals', 'step3_csv'):
    sys.path.insert(0, os.path.join(GENDIR, p))
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
from generate_proposals_new import NewKotsuPlanGenerator

def rd(p):
    with open(p, encoding='cp932', newline='') as f:
        return list(csv.reader(f))
def norm(s): return re.sub(r'\(固定\)$', '', (s or '')).strip()
def tc_axis_names(tc):
    h = rd(tc)[0]; out = []
    for c in h[2:]:
        if c.startswith('期待:') or c in ('選択肢の適切さ確認', '規格数量'): break
        out.append(norm(c))
    return set(out)

def diag(folder, jsonname):
    tc = os.path.join(ROOT, "工種別", folder, "output", "step3.0_テストケース【合格】.csv")
    jpath = os.path.join(ROOT, "工種別", folder, "input", jsonname)
    A = tc_axis_names(tc) if os.path.exists(tc) else set()
    gen = NewKotsuPlanGenerator(jpath)
    with contextlib.redirect_stdout(io.StringIO()):
        order = gen._discover_reachable()
    bj = gen.bj
    print(f"\n{'='*110}\n[{folder}] JSON={jsonname}\n合格TC軸={sorted(A)}\n{'-'*110}")
    print(f"{'種別':<5}{'inTC':<5}{'Sit':<5}{'Lv':<7}{'EK':<3}{'Flags':<14}{'#行':<4} 名称 / 判定")
    for sn in order:
        sit = bj.sitsumon_by_no.get(sn)
        if not sit: continue
        kind = sit.get('SitsumonKind')
        if kind not in (17, 19): continue
        name = sit.get('Mesho', f'No{sn}')
        lv = sit.get('LevelVarName') or ''
        ek = sit.get('SitsumonExecuteKind')
        flags = sit.get('SitsumonFlags') or []
        rows = gen._selectable_rows(sn)
        if gen._is_default_exec(sit):
            kb, why = 'auto', 'default_exec'
        elif kind == 17:
            kb, why = 'fix', 'Kind17数値'
        elif len(rows) >= 2:
            if gen._auto._is_autodetermined(sit):
                kb, why = 'auto', '自動確定(AutoSelect/LevelVar)'
            else:
                kb, why = 'vary', 'vary'
        else:
            kb, why = 'auto', '選択肢1件'
        intc = '○' if norm(name) in A else '×'
        mark = ''
        if kb == 'vary' and intc == '×': mark = '  <<余分vary(合格TCに無い)'
        if kb != 'vary' and intc == '○': mark = f'  <<欠落({kb}だが合格TCにあり)'
        print(f"{kb:<5}{intc:<5}{sn:<5}{lv:<7}{str(ek):<3}{str(flags):<14}{len(rows):<4} {name} [{why}]{mark}")

diag("07_608358_インターロッキングブロック工", "32-2344.20250401.20250401.json")
