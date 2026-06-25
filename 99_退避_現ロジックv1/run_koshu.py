"""
工種フォルダ単位 テストケース生成ランナー

【ルール】
1. input/修正方針.txt が無い/空 の工種は生成しない (スキップ)。
2. 参考工種JSONは input/参考/ サブフォルダに置く。
   旧/新の比較対象は input 直下の *.json のみ (参考/ は再帰せず除外)。
   → ファイル名の文字ブレに依存せず、置き場所で確実に除外できる。
   (保険) input 直下にファイル名へ「参考」を含む json があれば、誤配置として
          除外しつつ警告する。
3. 参考を除いた直下 json が
     1件 → 新規工種モード (全パターン型)
     2件以上 → 差分型 (改定日が古い方=旧, 新しい方=新)
   旧/新の判定はファイル名中の最後の8桁日付 (括弧内改定日を含む) で行う。

【使い方】
  python3 99_退避_現ロジックv1/run_koshu.py                # 工種別/ 配下すべて
  python3 99_退避_現ロジックv1/run_koshu.py 16 18          # 先頭一致で工種を指定
"""
import sys, os, re, glob, subprocess

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KOSHU = os.path.join(ROOT, '工種別')


def _datekey(path):
    ds = re.findall(r'(\d{8})', os.path.basename(path))
    return ds[-1] if ds else ''


def _intent_ok(input_dir):
    p = os.path.join(input_dir, '修正方針.txt')
    if not os.path.isfile(p):
        return False
    try:
        with open(p, encoding='utf-8-sig') as f:
            return bool(f.read().strip())
    except Exception:
        return False


def _target_jsons(input_dir):
    """input 直下の json のみを旧/新対象にする。
    参考工種は input/参考/ に置けば再帰しないため自動的に除外される。
    直下に「参考」を含む名前の json があれば誤配置とみなし除外する。"""
    js, misplaced = [], []
    for p in sorted(glob.glob(os.path.join(input_dir, '*.json')), key=_datekey):
        if '参考' in os.path.basename(p):
            misplaced.append(p)
        else:
            js.append(p)
    return js, misplaced


def run_folder(folder):
    name = os.path.basename(folder.rstrip(os.sep))
    input_dir = os.path.join(folder, 'input')
    out_dir = os.path.join(folder, 'output')
    warn = ''
    if not os.path.isdir(input_dir):
        return (name, 'SKIP', 'input/ なし', warn)
    if not _intent_ok(input_dir):
        return (name, 'SKIP', '修正方針.txt が無い/空 → 生成中止', warn)
    js, misplaced = _target_jsons(input_dir)
    if misplaced:
        warn = ('警告: 参考工種は input/参考/ に置いてください (直下で検出し除外): '
                + ', '.join(os.path.basename(m) for m in misplaced))
    if not js:
        return (name, 'SKIP', '対象json なし (参考のみ/空)', warn)
    os.makedirs(out_dir, exist_ok=True)
    if len(js) >= 2:
        old, new = js[0], js[-1]
        cmds = [[sys.executable, os.path.join(ROOT, '99_退避_現ロジックv1/pipeline.py'), old, new, out_dir,
                 '--external-scenarios']]
        mode = f'差分型 (旧={os.path.basename(old)} / 新={os.path.basename(new)})'
    else:
        new = js[0]
        plan = os.path.join(out_dir, 'step2.0_テスト計画.csv')
        step3_cmd = [sys.executable, os.path.join(ROOT, '99_退避_現ロジックv1/step3_csv/generate_csv.py'),
                     plan, new, os.path.join(out_dir, 'step3.0_テストケース.csv')]
        # 参考JSON (input/参考/) があれば「文字のみの修正」の比較元として渡す
        #   (#14: 複写元との文字比較観点。複数あれば日付が最新のもの)
        refs = sorted(glob.glob(os.path.join(input_dir, '参考', '*.json')), key=_datekey)
        mode = f'全パターン型 (新={os.path.basename(new)})'
        if refs:
            step3_cmd += ['--ref', refs[-1]]
            mode += f' / 参考比較={os.path.basename(refs[-1])}'
        cmds = [
            [sys.executable, os.path.join(ROOT, '99_退避_現ロジックv1/step2_proposals/generate_proposals_new.py'), new, plan],
            step3_cmd,
        ]
    for cmd in cmds:
        r = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8',
                           errors='replace', cwd=ROOT)
        if r.returncode != 0:
            tail = '\n      '.join((r.stderr or r.stdout or '').strip().splitlines()[-4:])
            return (name, 'NG', f'{mode}\n      {tail}', warn)
    return (name, 'OK', mode, warn)


def main():
    args = sys.argv[1:]
    folders = sorted(glob.glob(os.path.join(KOSHU, '*')))
    if args:
        folders = [f for f in folders if any(os.path.basename(f).startswith(a) for a in args)]
    results = [run_folder(f) for f in folders if os.path.isdir(f)]
    print('=' * 64)
    for name, st, msg, warn in results:
        print(f'[{st:4}] {name}')
        print(f'        {msg}')
        if warn:
            print(f'        {warn}')
    n_ok = sum(1 for r in results if r[1] == 'OK')
    n_skip = sum(1 for r in results if r[1] == 'SKIP')
    n_ng = sum(1 for r in results if r[1] == 'NG')
    print('=' * 64)
    print(f'OK={n_ok} / SKIP={n_skip} / NG={n_ng}')


if __name__ == '__main__':
    main()
