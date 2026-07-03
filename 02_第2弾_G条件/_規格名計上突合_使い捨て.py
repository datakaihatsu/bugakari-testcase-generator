# -*- coding: utf-8 -*-
import csv,sys,os,re,glob,io,contextlib,tempfile
sys.stdout.reconfigure(encoding='utf-8')
ROOT=r"C:\Users\imoo\Desktop\ClaudeCode\14.歩掛Jsonからテストケース作成可能か【進行中】"
GEN=os.path.join(ROOT,"02_第2弾_G条件"); sys.path.insert(0,GEN)
for p in ('engine','step2_proposals','step3_csv'): sys.path.insert(0,os.path.join(GEN,p))
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import gen_gjoken
def norm(s): return re.sub(r'\(固定\)$','',(s or '')).strip()
FOLDERS={"29":"29_605396_PC板敷設工【SP3→1】","07":"07_608358_インターロッキングブロック工",
 "03":"03_607674_送気用設備運転費","21":"21_623816_管(函)渠型側溝","12":"12_619272_重建設機械分解組立",
 "40":"40_615380_小型不整地運搬車運搬","38":"38_603592_波付硬質合成樹脂管(FEP)敷設(WE110500) 【条件",
 "09":"09_619550_養生マット(材料費)【施工P】","22":"22_605866_路上路盤再生工"}
def tc_kikaku_names(tc):
    rows=list(csv.reader(open(tc,encoding='cp932'))); h=rows[0]
    ci=[i for i,c in enumerate(h) if c=='規格名計上']
    if not ci: return None
    ci=ci[0]; names=set()
    for r in rows[1:]:
        if not (r and r[0].startswith('TC')): continue
        v=r[ci] if ci<len(r) else ''
        for line in v.split('\n'):
            m=re.match(r'・(.+?) の規格名計上',line.strip())
            if m: names.add(norm(m.group(1)))
    return names
def best_json(folder,tcnames):
    # pick json maximizing overlap with 合格TC axis names (同 突合方針)
    tc=os.path.join(ROOT,"工種別",folder,"output","step3.0_テストケース【合格】.csv")
    h=list(csv.reader(open(tc,encoding='cp932')))[0]
    A=set()
    for c in h[2:]:
        if c.startswith('期待:') or c in ('選択肢の適切さ確認','規格名計上'): break
        A.add(norm(c))
    best=None
    for j in sorted(glob.glob(os.path.join(ROOT,"工種別",folder,"input","*.json"))):
        tmp=tempfile.mkdtemp()
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                out=gen_gjoken.build_g(j,tmp,label='')
        except Exception: continue
        rows=list(csv.reader(open(out,encoding='cp932')))
        names=[norm(x) for x in rows[1][1:] if x.strip()]
        # 規格名計上 row = rows[2]
        kmarks=set()
        for k,cell in enumerate(rows[2][1:]):
            if str(cell).strip()=='○' and k<len(names): kmarks.add(names[k])
        inter=len(A & set(names))
        cand={'json':os.path.basename(j),'kmarks':kmarks,'inter':inter,'cols':set(names)}
        if best is None or cand['inter']>best['inter']: best=cand
    return best,A
print("="*90)
for k,folder in FOLDERS.items():
    tc=os.path.join(ROOT,"工種別",folder,"output","step3.0_テストケース【合格】.csv")
    tcn=tc_kikaku_names(tc)
    best,A=best_json(folder,tcn)
    gmark=best['kmarks']
    missing=(tcn or set())-gmark   # 合格TCが計上と言うのにG条件で○が無い = 矛盾
    extra=gmark-(tcn or set())     # G条件が○だが合格TC未言及 (fix列等の可能性・要精査)
    status="OK" if not missing else "★矛盾(欠落)"
    print(f"[{k}] {folder.split('_')[1]}  {status}")
    print(f"    合格TC規格名計上={sorted(tcn) if tcn else tcn} / G条件○={sorted(gmark)}")
    if missing: print(f"    ★欠落(合格TC計上なのにG条件○なし)={sorted(missing)}")
    if extra:   print(f"    +超過(G条件○だが合格TC未言及)={sorted(extra)}")
print("="*90)
