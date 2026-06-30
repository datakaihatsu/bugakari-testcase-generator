"""
第1弾 ③ 乖離チェックランナー

【役割】
  2つの差分を取り、質問名をキーに一致度(乖離)を判定する。

  - 差分A (TC側)  : 20_叩き台TC(改定前JSON由来) → 30_人作成TC(改定後) の変化
                    = 人がTCに込めた「改定で変わるはず」の表現
  - 差分B (実装側): 10_改定前JSON → 40_改定後JSON の実装差分 (extract_diff を再利用)

  乖離チェック = A と B の一致度
    - B にあり A に無い → 未カバー (実装は変わったのにTCが取りこぼし)
    - A にあり B に無い → 過剰     (TCが触れたが実装は変わっていない疑い)
    - 両方にある         → 合致

  キー: 質問名 (人はTC作成時に質問Noを決めきれないため、名前で紐づける)。
        期待値系の変更(計算表/代価表)は質問名を持たないため、S変数で副次照合する。

  出力: 50_乖離チェック/乖離チェック結果.csv
        ① 一致度サマリ
        ② 質問名 × 判定 (関連TC-IDを紐づけ)
        ③ 期待値(S変数) × 判定
        ④ 人作成TC 1件ごとの合致/不合致と食い違い箇所

【使い方】
  python3 01_第1弾_運用化/run_phase3.py <案件ディレクトリ>
"""
import sys
import os
import csv
import glob
import re

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(BASE, 'engine'))
sys.path.insert(0, os.path.join(BASE, 'step1_diff'))
sys.path.insert(0, os.path.join(BASE, 'step2_proposals'))

from bugakari_json import BugakariJSON, KeisanHyo            # noqa: E402
from extract_diff import DiffExtractor, HEADER as DIFF_HEADER  # noqa: E402
from generate_proposals import TestPlanGenerator             # noqa: E402
from flow_walker import FlowWalker                           # noqa: E402
from route_finder import find_route_to_sitsumon                # noqa: E402

DIR_OLD, DIR_BASE_TC = '10_改定前', '20_叩き台TC'
DIR_HUMAN_TC, DIR_NEW = '30_人作成TC', '40_改定後'
DIR_OUT = '50_乖離チェック'
OUT_CSV = '乖離チェック結果.csv'

# TC CSV のうち入力軸(=質問名)でない列
META_PREFIX = ('期待:',)
META_EXACT = {'テストID', 'テスト区分', '選択肢の適切さ確認', '規格名計上'}
NOT_REACHED = {'', '-'}


# ---------------------------------------------------------------- 入出力
def _single(dir_path, pattern, label):
    fs = sorted(glob.glob(os.path.join(dir_path, pattern)))
    if not fs:
        raise SystemExit(f'[ERROR] {label} が {dir_path} に見つかりません')
    if len(fs) > 1:
        raise SystemExit(f'[ERROR] {label} は1本にしてください ({len(fs)}本検出): '
                         + ', '.join(os.path.basename(f) for f in fs))
    return fs[0]


def _read_tc(path):
    """TC CSV を (header, rows[dict]) で返す。cp932/utf-8 両対応。"""
    for enc in ('cp932', 'utf-8-sig'):
        try:
            with open(path, encoding=enc, newline='') as f:
                rows = list(csv.reader(f))
            break
        except UnicodeDecodeError:
            continue
    else:
        raise SystemExit(f'[ERROR] TC CSV を読めません: {path}')
    if not rows:
        return [], []
    header = rows[0]
    body = [dict(zip(header, r)) for r in rows[1:]
            if any(c.strip() for c in r)
            and (r and (r[0] or '').strip())
            and not (r[0] or '').strip().startswith(('#', '※'))]
    return header, body


def _axis_cols(header):
    """ヘッダから入力軸(質問名)の列だけ抽出。"""
    return [h for h in header
            if h not in META_EXACT and not h.startswith(META_PREFIX)]


def _s_cols(header):
    return [h for h in header if h.startswith('期待:')]


def _lcs_len(a, b):
    """最長共通部分文字列長 (質問名の近さ判定用)。"""
    if not a or not b:
        return 0
    prev = [0] * (len(b) + 1)
    best = 0
    for i in range(len(a)):
        cur = [0] * (len(b) + 1)
        for j in range(len(b)):
            if a[i] == b[j]:
                cur[j + 1] = prev[j] + 1
                if cur[j + 1] > best:
                    best = cur[j + 1]
        prev = cur
    return best


def _closest_cols(name, candidates, n=2, minlen=3):
    """name に最も近い候補列(共通部分文字列が長い順)を返す。乖離時のTC側候補提示用。"""
    scored = [(_lcs_len(name, c), c) for c in candidates if c and c != name]
    scored = [(sc, c) for sc, c in scored if sc >= minlen]
    scored.sort(reverse=True)
    return [c for _, c in scored[:n]]


# ---------------------------------------------------------------- 差分B (JSON)
_S_RE = re.compile(r'S\d+[a-zA-Z]?$')


def _eval_S_under(bj, selections):
    """指定selectionでフロー評価し、各S変数の値(またはエラー種別)を返す。
       selections の質問NoがそのJSONに無ければ FlowWalker が無視し既定経路になる。"""
    try:
        fw = FlowWalker(bj, vary_selections=selections)
        fw.walk()
        inputs = dict(fw.hyo._user_inputs)
    except Exception:
        inputs = {}
    hyo = KeisanHyo(bj.data.get('KeisanItem', []))
    for vn, vv in inputs.items():
        try:
            hyo.set_input(vn, vv)
        except Exception:
            pass
    out = {}
    for k in bj.data.get('KeisanItem', []):
        vn = (k.get('VarName') or '').strip()
        if not vn:   # 期待列の変数名は S\d+ に限らない(A2/S1'/SAm 等)ため全変数を評価
            continue
        try:
            out[vn] = ('val', hyo.value(vn))
        except Exception as e:
            out[vn] = ('err', e.__class__.__name__)
    return out


def _candidate_selections(bj, cap=14):
    """多経路評価用の代表selection集合: 既定 + 各Kind19質問の非既定行を単独逸脱。"""
    combos = [{}]
    for sit in bj.data.get('SitsumonItem', []):
        if sit.get('SitsumonKind') != 19:
            continue
        no = sit.get('SitsumonNo')
        s019 = bj.sitsumon019_by_no.get(no)
        if not s019:
            continue
        rows = [r['RowID'] for r in s019.get('SitRows', [])
                if r.get('Visible', True) and not r.get('IsFixed', False)]
        for rid in rows[:2]:
            combos.append({no: rid})
            if len(combos) >= cap:
                return combos
    return combos


def _route_aware_combos(new_bj, vary_sits=(), cap=24):
    """S評価用の代表selection集合を経路化して作る。
       既定 + 単軸逸脱 に加え、運転費(Sitsumon014)/子代価(Sitsumon011)/vary質問への
       route_finder到達経路を足し、深い分岐先の値改定(例 #43 潜水士船運転費)に到達する。"""
    combos = _candidate_selections(new_bj, cap=cap // 2)
    targets = set()
    for key in ('Sitsumon014', 'Sitsumon011'):
        for e in new_bj.data.get(key, []) or []:
            no = e.get('SitsumonNo')
            if no is not None:
                targets.add(no)
    targets |= set(vary_sits)
    for t in list(targets)[:14]:
        try:
            route = find_route_to_sitsumon(new_bj, int(t))
        except Exception:
            route = None
        if route:
            route = {int(k): int(v) for k, v in route.items()}
            if route not in combos:
                combos.append(route)
        if len(combos) >= cap:
            break
    return combos


def _expected_change_by_eval(old_json_path, new_json_path, combos=None):
    """改定前/後で、複数の代表経路のいずれかでS値が変わったS変数 -> 変更内容 を返す。
       combos未指定時は経路化した代表selectionを用いる。深い分岐の値改定
       (例 #05 計算式・#43 潜水士船運転費)を取りこぼさないため経路を増やす。"""
    old_bj, new_bj = BugakariJSON(old_json_path), BugakariJSON(new_json_path)
    if combos is None:
        combos = _route_aware_combos(new_bj)

    def _show(t):
        return '(無)' if t is None else (str(t[1]) if t[0] == 'val' else f'({t[1]})')

    b_s = {}
    for sel in combos:
        so = _eval_S_under(old_bj, sel)
        sn = _eval_S_under(new_bj, sel)
        for s in set(so) | set(sn):
            ov, nv = so.get(s), sn.get(s)
            if ov != nv and s not in b_s:
                tag = '既定経路' if not sel else '経路依存'
                b_s[s] = [f'計上値変更({tag}/JSON評価: {_show(ov)}->{_show(nv)})']
    return b_s


def diff_b(old_json_path, new_json_path, work_dir):
    """質問名→[変更内容](step2フィルタ済みvary軸), S変数→[変更内容](計算表/代価表)。
       版間再採番ノイズを避けるため、質問プレーンは生の差分レポートではなく
       step2(TestPlanGenerator)が選んだ vary 軸=「本当に変わった質問」を使う。"""
    rows = DiffExtractor(BugakariJSON(old_json_path),
                         BugakariJSON(new_json_path)).extract_all()
    # 質問プレーンは step2 のフィルタ済み vary 軸から
    diff_csv = os.path.join(work_dir, '_差分B_step1差分レポート.csv')
    BugakariJSON.write_csv([list(DIFF_HEADER)] + rows, diff_csv)
    plan = TestPlanGenerator(diff_csv, new_json_path, old_json_path).generate()
    b_q = {}
    vary_sits = []
    for r in plan:   # [軸ID,軸名,種別,SitsumonNo,列ラベル,変更理由,備考,強制行ID]
        if r[2] == 'vary':
            key = (r[1] or r[4] or '').strip()
            if key:
                b_q.setdefault(key, []).append((r[5] or '').strip())
            try:
                vary_sits.append(int(r[3]))
            except (ValueError, TypeError):
                pass
    # 期待は計上行(構造)で見る方針へ移行したため、ここでの数値S評価は廃止。
    #   計上行の裏付けは run_case 側で代価表の追加/削除(構造)で確認する。
    return b_q, {}


def _looks_like_var(name):
    """A=10 / 0.4=... / 代価表1枚目5行目=lease / 終点 等のフロー再採番トークンを
       質問名から除外する。実質問名 例「代価表当り単位の選択(標準=10m3)」は = が
       日本語の後ろにあるため誤除外しない。"""
    n = (name or '').strip()
    if not n:
        return True
    if re.match(r'^[A-Za-z0-9_~.\-]+=', n):   # 先頭がASCIIトークン+= (A=10, 0.4=...)
        return True
    if '行目' in n and '代価表' in n:          # 代価表N枚目N行目=... のフロー参照
        return True
    return n in ('終点', '始点')


# ---------------------------------------------------------------- 差分A (TC)
def diff_a(base_path, human_path):
    """質問名→種別(列追加/選択変化), 期待S列→変化フラグ, を返す。
       併せて human TC の各行が踏む質問名(reached)も返す。"""
    b_head, b_body = _read_tc(base_path)
    h_head, h_body = _read_tc(human_path)

    b_axis, h_axis = set(_axis_cols(b_head)), set(_axis_cols(h_head))

    def value_set(body, col):
        return {r.get(col, '').strip() for r in body
                if r.get(col, '').strip() not in NOT_REACHED}

    a_q = {}   # 質問名 -> 変更種別
    for q in h_axis - b_axis:
        a_q[q] = 'TC列追加(新規軸)'
    for q in h_axis & b_axis:
        if value_set(b_body, q) != value_set(h_body, q):
            a_q[q] = 'TC選択変化'
    a_removed = b_axis - h_axis    # 参考: 叩き台にあり人作成で消えた軸

    # 計上行プレーン: 各TCで計上される代価表行(=非空のS列)集合の増減。
    #   数値は比較しない(手計算一致の観点で手動確認)。
    def _keijo(head, body):
        out = set()
        for sc in _s_cols(head):
            if any((r.get(sc, '') or '').strip() not in NOT_REACHED for r in body):
                out.add(re.sub(r'\(.*\)$', '', sc[len('期待:'):]).strip())   # タイトル除去でS名統一
        return out
    b_keijo, h_keijo = _keijo(b_head, b_body), _keijo(h_head, h_body)
    a_keijo_added = h_keijo - b_keijo
    a_keijo_removed = b_keijo - h_keijo

    # human TC 各行が踏む質問名
    reached = []
    for r in h_body:
        touched = [q for q in h_axis if r.get(q, '').strip() not in NOT_REACHED]
        reached.append((r.get('テストID', ''), touched, r))
    return a_q, a_removed, a_keijo_added, a_keijo_removed, reached, h_axis


# ---------------------------------------------------------------- 照合・出力
def run_case(case_dir):
    old_json = _single(os.path.join(case_dir, DIR_OLD), '*.json', '改定前JSON')
    new_json = _single(os.path.join(case_dir, DIR_NEW), '*.json', '改定後JSON')
    base_tc = _single(os.path.join(case_dir, DIR_BASE_TC), 'step3.0*.csv', '叩き台TC')
    human_tc = _single(os.path.join(case_dir, DIR_HUMAN_TC), '*.csv', '人作成TC')

    name = os.path.basename(case_dir.rstrip(os.sep))
    out_dir = os.path.join(case_dir, DIR_OUT)
    os.makedirs(out_dir, exist_ok=True)
    print('=' * 56)
    print(f'③ 乖離チェック  案件: {name}')
    print('=' * 56)

    b_q, _ = diff_b(old_json, new_json, out_dir)
    a_q, a_removed, a_keijo_add, a_keijo_rm, reached, h_axis = diff_a(base_tc, human_tc)
    # 計上行の裏付け: 代価表行の追加/削除(構造)
    _raw = DiffExtractor(BugakariJSON(old_json), BugakariJSON(new_json)).extract_all()
    daika_add = any(c == '代価表' and k == '追加' for c, k, *_ in _raw)
    daika_del = any(c == '代価表' and k == '削除' for c, k, *_ in _raw)

    # 関連TC-ID: 質問名 q を踏む人作成TCのID
    def tcs_touching(q):
        return [tid for tid, touched, _ in reached if q in touched]

    # --- ② 質問名 × 判定 ---
    q_rows = []
    for q in sorted(set(b_q) | set(a_q)):
        in_b, in_a = q in b_q, q in a_q
        verdict = '合致' if (in_b and in_a) else ('未カバー(実装変更がTC未反映)'
                                                  if in_b else '過剰(TC独自・実装変更なし)')
        # 未カバー(乖離)時は、人作成TCにある近い列を候補提示(別名/別バリアントの判断材料)
        cand = ''
        if in_b and not in_a:
            cols = _closest_cols(q, h_axis)
            cand = ('TC側の近い列: ' + ' / '.join(cols)) if cols else 'TC側に類似列なし(真の未反映の疑い)'
        q_rows.append([q, 'あり' if in_b else '', 'あり' if in_a else '',
                       verdict, ';'.join(tcs_touching(q)),
                       ' | '.join(b_q.get(q, [])), a_q.get(q, ''), cand])

    # --- ③ 計上行(代価表) × 判定 ---  (数値は手計算一致の観点で手動確認)
    s_rows = []
    for s in sorted(a_keijo_add):
        v = '合致' if daika_add else '過剰(TCで計上行追加・実装に代価表追加なし)'
        s_rows.append([s, '追加', v])
    for s in sorted(a_keijo_rm):
        v = '合致' if daika_del else '過剰(TCで計上行削除・実装に代価表削除なし)'
        s_rows.append([s, '削除', v])

    # --- ④ 人作成TC 1件ごと ---
    tc_rows = []
    b_q_set = set(b_q)
    for tid, touched, r in reached:
        problems = []
        for q in touched:
            if q in a_q and q not in b_q_set:
                problems.append(f'{q}=過剰(実装に対応変更なし)')
        # この行が「改定軸」を全く踏んでいない場合は回帰TC扱い
        diff_axes = [q for q in touched if q in a_q]
        if not diff_axes:
            judge = '合致(回帰TC・改定軸に非該当)'
        elif problems:
            judge = '不合致'
        else:
            judge = '合致'
        tc_rows.append([tid, judge, ';'.join(diff_axes), ' / '.join(problems)])

    # 未カバー(どのTCも踏まないB質問)
    uncovered_q = [q for q in b_q if q not in a_q]
    keijo_over = sum(1 for r in s_rows if r[2].startswith('過剰'))

    # --- 一致度サマリ ---
    matched_q = sum(1 for r in q_rows if r[3] == '合致')
    total_b_q = len(b_q)
    rate = f'{matched_q}/{total_b_q}' if total_b_q else '—'
    pct = f'{100*matched_q/total_b_q:.0f}%' if total_b_q else '—'

    # ---------------- CSV 組み立て ----------------
    out = []
    out.append(['# 乖離チェック結果', name])
    out.append(['# 改定前', os.path.basename(old_json),
                '改定後', os.path.basename(new_json)])
    out.append([])
    out.append(['【① 一致度サマリ】'])
    out.append(['実装変更(質問)', total_b_q, '内 TC反映(合致)', matched_q,
                '一致度', f'{rate} ({pct})'])
    out.append(['未カバー質問', len(uncovered_q),
                '過剰質問', sum(1 for r in q_rows if r[3].startswith('過剰'))])
    out.append(['計上行 増減', len(a_keijo_add) + len(a_keijo_rm), '内 過剰(実装裏付けなし)', keijo_over])
    out.append([])
    out.append(['【② 質問名 × 判定】'])
    out.append(['質問名', '実装変更B', 'TC反映A', '判定', '関連TC-ID', 'B変更内容', 'A変更種別', 'TC側の近い列候補(乖離時)'])
    out.extend(q_rows)
    out.append([])
    out.append(['【③ 計上行(代価表) × 判定】 ※数値は手計算一致を手動確認'])
    out.append(['計上行(S列)', '増減', '判定'])
    out.extend(s_rows)
    out.append(['(観点)', '手計算一致', '計算結果は手計算と一致すること(手動確認)'])
    out.append([])
    out.append(['【④ 人作成TC 1件ごとの判定】'])
    out.append(['TC-ID', '判定', '改定に関係する軸(質問名)', '食い違い箇所'])
    out.extend(tc_rows)
    if a_removed:
        out.append([])
        out.append(['【参考: 叩き台にあり人作成で消えた軸】'] + sorted(a_removed))

    out_path = os.path.join(out_dir, OUT_CSV)
    BugakariJSON.write_csv(out, out_path)

    # ---------------- コンソール要約 ----------------
    print(f'  実装変更(質問) {total_b_q}件 / TC反映(合致) {matched_q}件  一致度 {rate} ({pct})')
    if uncovered_q:
        print(f'  [未カバー] 実装は変わったがTC未反映の質問: {", ".join(uncovered_q)}')
    over = [r[0] for r in q_rows if r[3].startswith('過剰')]
    if over:
        print(f'  [過剰] TCが触れたが実装変更なしの質問: {", ".join(over)}')
    ng = [r[0] for r in tc_rows if r[1] == '不合致']
    if ng:
        print(f'  [不合致TC] {", ".join(ng)}')
    print(f'  -> {out_path}')
