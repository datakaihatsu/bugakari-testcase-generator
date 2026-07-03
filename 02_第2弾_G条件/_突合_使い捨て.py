# -*- coding: utf-8 -*-
"""使い捨て突合(統制版): 各工種の候補JSONからG条件を再生成し、合格TC(A)と突合。
候補JSONごとにC(G条件列)を作り、Aとの重なり最大のJSONを採用=provenance推定も兼ねる。"""
import csv, os, re, sys, glob, tempfile, io, contextlib
sys.stdout.reconfigure(encoding='utf-8')

ROOT = r"C:\Users\imoo\Desktop\ClaudeCode\14.歩掛Jsonからテストケース作成可能か【進行中】"
KOSHU = os.path.join(ROOT, "工種別")
GENDIR = os.path.join(ROOT, "02_第2弾_G条件")
sys.path.insert(0, GENDIR)
for p in ('engine', 'step2_proposals', 'step3_csv'):
    sys.path.insert(0, os.path.join(GENDIR, p))
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
import gen_gjoken  # build_g

FOLDERS = {
 "29": "29_605396_PC板敷設工【SP3→1】",
 "07": "07_608358_インターロッキングブロック工",
 "03": "03_607674_送気用設備運転費",
 "21": "21_623816_管(函)渠型側溝",
 "12": "12_619272_重建設機械分解組立",
 "40": "40_615380_小型不整地運搬車運搬",
 "38": "38_603592_波付硬質合成樹脂管(FEP)敷設(WE110500) 【条件",
 "09": "09_619550_養生マット(材料費)【施工P】",
 "22": "22_605866_路上路盤再生工",
}

def rd(p):
    with open(p, encoding='cp932', newline='') as f:
        return list(csv.reader(f))

def norm(s):
    return re.sub(r'\(固定\)$', '', (s or '')).strip()

def tc_axes(tc_path):
    rows = rd(tc_path)
    hdr = rows[0]
    data = [r for r in rows[1:] if r and r[0].startswith('TC')]
    axis = []
    for i, c in enumerate(hdr[2:], start=2):
        if c.startswith('期待:') or c in ('選択肢の適切さ確認', '規格数量'):
            break
        axis.append((i, norm(c)))
    return axis, data

def variance(data, ci):
    nz = [r[ci] for r in data if ci < len(r) and r[ci] not in ('', '-')]
    return len(set(nz))

def gen_cols(json_path):
    tmp = tempfile.mkdtemp()
    with contextlib.redirect_stdout(io.StringIO()):
        out = gen_gjoken.build_g(json_path, tmp, label='')
    rows = rd(out)
    return [c.strip() for c in rows[1][1:] if c.strip()]

print("="*100)
for k, folder in FOLDERS.items():
    tcpath = os.path.join(KOSHU, folder, "output", "step3.0_テストケース【合格】.csv")
    axis, data = tc_axes(tcpath)
    Aset = {c for _, c in axis}
    cands = sorted(glob.glob(os.path.join(KOSHU, folder, "input", "*.json")))
    best = None
    for j in cands:
        try:
            C = [norm(x) for x in gen_cols(j)]
        except Exception as e:
            C = None
        if C is None: continue
        Cset = set(C)
        inter = len(Aset & Cset)
        cand = {"json": os.path.basename(j), "C": C, "inter": inter,
                "miss": [(i,c) for i,c in axis if c not in Cset],
                "extra": [c for c in C if c not in Aset]}
        if best is None or cand["inter"] > best["inter"]:
            best = cand
    status = "OK" if not best["miss"] and not best["extra"] else "差分"
    print(f"[{k}] {folder.split('_')[1] if '_' in folder else folder}  … 採用JSON={best['json']}")
    print(f"    合格TC列={len(axis)} / G条件列={len(best['C'])} / TC行={len(data)} / 一致列={best['inter']}  => {status}")
    for i, c in best["miss"]:
        v = variance(data, i)
        print(f"      └ 欠落(合格TCのみ): {c}  [合格TCで変動{v}種{' ★真の入力軸' if v>1 else ' (定数疑い)'}]")
    for c in best["extra"]:
        print(f"      └ 余分(G条件のみ): {c}")
print("="*100)
