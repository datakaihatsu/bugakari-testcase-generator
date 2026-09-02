"""
第2弾 ③ G条件 → 改定後TC生成 (gen_tc_from_gjoken.py)

入力:
  ① 20_叩き台G条件CSV (改定前(商品)JSON から gen_gjoken で生成)
  ② 30_人作成G条件CSV (人が改定内容を反映したもの)
  ③ 改定前JSON (期待値ベースライン算出用。改定後JSONは使わない=テストファースト)
出力:
  step3.0_テストケース.csv 同形式の「改定後TC叩き台」(99の合格TCと構造レベルで突合可能)

方式 (2026-07-05 設計。06土のう積工で実験裏取り済み):
  1. G条件CSV差分 (列追加 / 選択肢の追加・削除・文字変更 / 注) を抽出
  2. 既存質問の選択肢削除・文字変更を改定前JSONへ適用した「擬似改定後JSON」を合成
  3. 99パイプライン step1(改定前→擬似)→step2→step3 で構造+期待値(改定前ベース)を生成
     - 30の注で新規列をゲートする既存条件は vary昇格「業務ルール(G条件ゲート)」
       → 全選択肢網羅 + 状態戻し回帰TC (合格TCと同型になる)
  4. 新規追加列(改定前JSONに無い質問)は後処理で挿入:
     - 選択肢=30のG条件 / ゲート=30の注 / 非ゲートの差分行を選択肢数に展開
  5. 「期待:変数」列は全廃し、同位置に「代価表行と数量(数率)」列を1本置く(ユーザ指示 2026-07-06):
     - 1行目=「積算基準および設計書通りの代価行と数量が計上されていること
              ※ただし計上区分の切り替えによる代価行変動がある場合は、設計者が追記すること」
     - 2行目以降=「〃」(全行同文で麻痺しないための差別化)
     ※ 内部数値の改定があると改定前ベース値は保証できないため、数値期待は出さない。
       合格TCとの一致度測定でも期待:変数は対象外(構造=軸・行・区分・入力値で測る)。

使い方:
  python3 gen_tc_from_gjoken.py <20_叩き台G条件CSV> <30_人作成G条件CSV> <改定前JSON> [出力dir]
  出力dir省略時: 30のCSVが 運用案件/<工種>/30_人作成G条件/ 配下なら <工種>/60_改定後TC叩き台/
"""
import sys
import os
import re
import csv
import io
import json
import copy
import difflib

BASE = os.path.dirname(os.path.abspath(__file__))
for p in ('engine', 'step1_diff', 'step2_proposals', 'step3_csv'):
    sys.path.insert(0, os.path.join(BASE, p))

from bugakari_json import BugakariJSON            # noqa: E402
from extract_diff import run as run_step1         # noqa: E402
from generate_proposals import run as run_step2   # noqa: E402
from generate_csv import run as run_step3         # noqa: E402
import gen_gjoken                                 # noqa: E402


# ------------------------------------------------------------------
# G条件CSV パース
# ------------------------------------------------------------------

def _read_csv_any(path):
    for enc in ('utf-8-sig', 'cp932'):
        try:
            text = open(path, encoding=enc).read()
            return list(csv.reader(io.StringIO(text)))
        except UnicodeDecodeError:
            continue
    raise RuntimeError(f'エンコーディング判別不可: {path}')


# --- (注) の許容表現 -------------------------------------------------
#   ①(gen_gjoken)が自動生成する注は常に「…を入力する必要はない」だが、
#   改修後G条件表(30)は人が手編集するため語尾が揺れる
#   (2026-08-17 運用FB: 「選択する必要はない」「選択できない」が黙って捨てられ、
#    ゲート未認識→対象列が'-'にならず・源泉列がvary昇格しない不具合)。
#   同義の語尾を広く受け入れ、読めなかった注は note_issues で可視化する。
_NOTE_TAIL = (r'(?:を|は)\s*'
              r'(?:(?:入力|選択|表示|指定)(?:する)?)?\s*'
              r'(?:必要は(?:ない|ありません)|できない|されない|しない|不要)')
_NOTE_PAT = re.compile(r'G(\d+)条件「(.+?)」で(.+?)(?:のいずれか)?を選択した場合は、'
                       r'(.+?)\s*' + _NOTE_TAIL)
# 注らしい行の判定(パースできなかったものを「読めなかった注」として報告するため)
_NOTE_LIKE = re.compile(r'G\d+条件')


def _quoted_spans(s):
    """「…」で囲まれた部分を入れ子を考慮して取り出す。

    選択肢名や条件名それ自体に「」を含む場合(例: ①「「m2」単位の材料単価」)、
    非貪欲な正規表現 「(.+?)」 では「m2 で切れてしまい、選択肢が突合できず
    (注)が丸ごと無効化される(2026-08-27 発覚 / 例 09養生マット: 注1件が0件反映)。
    対応の取れない 」 は無視する(壊れた注でも他の注は生かす)。"""
    out = []
    depth = 0
    start = -1
    for i, ch in enumerate(s):
        if ch == '「':
            if depth == 0:
                start = i + 1
            depth += 1
        elif ch == '」' and depth:
            depth -= 1
            if depth == 0:
                out.append(s[start:i])
    return out


def _norm_name(s):
    """条件名の突合キー(空白/全角空白を無視)。"""
    return re.sub(r'[\s　]+', '', str(s or ''))


def _resolve_ref(gnum, name, cols, issues, where):
    """(注)中の「G<gnum>条件「<name>」」を列indexへ解決する。
    名称優先(人は名称を間違えにくい/列挿入で番号だけがずれるため)。
    番号と名称が食い違う場合は名称を採用しWARNを残す。"""
    idx_by_name = None
    if name:
        key = _norm_name(name)
        hits = [i for i, c in enumerate(cols) if _norm_name(c['name']) == key]
        if len(hits) == 1:
            idx_by_name = hits[0]
    num_ok = gnum is not None and 0 <= gnum < len(cols)
    if idx_by_name is not None:
        if num_ok and gnum != idx_by_name:
            issues.append({
                'level': 'WARN',
                'text': (f'{where}: 番号G{gnum + 1}と名称「{name}」が不一致'
                         f'(G{gnum + 1}＝「{cols[gnum]["name"]}」/ 名称は'
                         f'G{idx_by_name + 1})。名称を優先して解釈しました。'
                         f'条件表の注の番号を直してください。')})
        elif not num_ok and gnum is not None:
            issues.append({
                'level': 'WARN',
                'text': (f'{where}: 番号G{gnum + 1}は条件表に存在しません'
                         f'(全{len(cols)}列)。名称「{name}」＝G{idx_by_name + 1}'
                         f'として解釈しました。')})
        return idx_by_name
    if num_ok:
        issues.append({
            'level': 'WARN',
            'text': (f'{where}: 名称「{name}」が条件表に見つかりません。'
                     f'番号G{gnum + 1}＝「{cols[gnum]["name"]}」として解釈しました。')})
        return gnum
    issues.append({
        'level': 'ERROR',
        'text': (f'{where}: 番号G{gnum + 1 if gnum is not None else "?"}・'
                 f'名称「{name}」のどちらでも列を特定できません。この注は無視されます。')})
    return None


def _strip_circled(s):
    """選択肢セルの ①..⑳ / (21) マーカーのみ除去(Gaiaコード【】は残す=表示用)。"""
    s = s.strip()
    if s and 0x2460 <= ord(s[0]) <= 0x2473:
        return s[1:].strip()
    m = re.match(r'^\((\d+)\)\s*(.+)$', s)
    if m:
        return m.group(2).strip()
    return s


def _strip_marker(s):
    """選択肢セルの ①..⑳ / (21) マーカーを除去し、続けて表示併記された
    Gaia入力条件コード(【A=1】等)も内部識別子から除去する。
    → ①(gen_gjoken)がコードを表示併記しても、③の突合キーは従来どおり(不変)。"""
    return re.sub(r'^【[^】]*】[\s　]+', '', _strip_circled(s)).strip()


def read_gjoken(path):
    """G条件CSV → {'cols': [{name, opts, numeric, kikaku}], 'notes': [...]}"""
    rows = _read_csv_any(path)
    cols = []
    names = []
    kikaku = []
    n = 0
    in_notes = False
    notes_raw = []
    for row in rows:
        head = (row[0] if row else '').strip()
        joined = ''.join(c.strip() for c in row) if row else ''
        if not joined:
            continue
        # (外部設計メモ)以降は人の自由記述欄 → 列/注として解釈せず読み取りを打ち切る
        #   (2026-07-09 運用者フィードバック: メモはTC生成に影響させない)。
        #   旧名(設計メモ)も後方互換で拾えるよう「設計メモ」を含む見出しで判定。
        if '設計メモ' in head or '設計メモ' in joined:
            break
        if head == '(注)' or joined == '(注)':
            in_notes = True
            continue
        if in_notes:
            for c in row:
                c = c.strip()
                if c:
                    notes_raw.append(c)
            continue
        if head == '施工区分/入力条件':
            n = len(row) - 1
            cols = [{'name': '', 'opts': [], 'opts_raw': [], 'numeric': False, 'kikaku': False}
                    for _ in range(n)]
            continue
        if head == '規格名計上':
            for i in range(n):
                cols[i]['kikaku'] = (row[i + 1].strip() == '○') if i + 1 < len(row) else False
            continue
        if head == '各種(条件名)':
            for i in range(n):
                cols[i]['name'] = row[i + 1].strip() if i + 1 < len(row) else ''
            continue
        # 選択肢行
        if cols:
            for i in range(n):
                c = row[i + 1].strip() if i + 1 < len(row) else ''
                if not c:
                    continue
                if c.startswith('(実数入力)') or (cols[i]['numeric'] and re.match(r'^\(.*\)$', c)):
                    cols[i]['numeric'] = True
                    cols[i]['opts'].append(c)
                    cols[i]['opts_raw'].append(c)
                else:
                    cols[i]['opts'].append(_strip_marker(c))      # 突合キー(コード除去)
                    cols[i]['opts_raw'].append(_strip_circled(c))  # 表示用(改修後コード保持)
    # 注のパース: 「Gx条件「名」で◯「選択肢」を選択した場合は、Gy条件「名」・… を入力する必要はない。」
    #   統合形式(2026-07-06 #21要望)にも対応:
    #     ①「A」・②「B」のいずれかを選択した場合は… / ①「A」～③「C」のいずれかを選択した場合は…
    #   → 選択肢ごとに1エントリへ展開して返す(下流ロジックは従来どおり単一選択肢前提)。
    notes = []
    issues = []
    parsed = 0
    for ni, raw in enumerate(notes_raw, 1):
        where = f'注{ni}'
        m = _NOTE_PAT.search(raw)
        if not m:
            if _NOTE_LIKE.search(raw):
                issues.append({
                    'level': 'ERROR',
                    'text': (f'{where}: 文の形が想定外でテストケースに反映できません'
                             f'(この注は無視されます)。「Gx条件「A」で①「B」を選択した'
                             f'場合は、Gy条件「C」を入力する必要はない。」の形に直して'
                             f'ください。 → {raw}')})
            continue
        src_g = _resolve_ref(int(m.group(1)) - 1, m.group(2).strip(), cols, issues,
                             f'{where}(条件側)')
        if src_g is None:
            continue
        src_name = cols[src_g]['name']
        sel_part = m.group(3)
        # 対象列: 「Gy条件「名」」の番号と名称の対で解決(名称優先)。
        pairs = []
        tgt_part = m.group(4)
        for mt in re.finditer(r'G(\d+)条件', tgt_part):
            rest = tgt_part[mt.end():]
            sp = _quoted_spans(rest) if rest[:1] == '「' else []
            pairs.append((mt.group(1), sp[0] if sp else ''))
        targets = []
        for gs, nm in pairs:
            t = _resolve_ref(int(gs) - 1, (nm or '').strip(), cols, issues,
                             f'{where}(対象側)')
            if t is not None and t not in targets:
                targets.append(t)
        if not targets:
            continue
        labels = [x.strip() for x in _quoted_spans(sel_part)]
        if '～' in sel_part and len(labels) == 2:
            opts = cols[src_g]['opts']
            try:
                i0, i1 = opts.index(labels[0]), opts.index(labels[1])
                if 0 <= i0 <= i1:
                    labels = opts[i0:i1 + 1]
            except ValueError:
                pass
        before = len(notes)
        for lbl in labels:
            # 実数入力の条件を起点にした注: ①(gen_gjoken)自身が「任意」と書き出すため
            #   (例「G9条件「1m当りチェアーの使用量」で「任意」を選択した場合は…」)、
            #   実数列の値表現(任意/(実数入力)/(単位)) はその列の値として受け入れる。
            #   TC側も実数列は「任意」と出力するので、突合キーは「任意」へ寄せる。
            #   (2026-08-27: ①が書いた注を③が弾いてERRORにしていた自己不整合の是正。
            #    「任意」以外の不一致は従来どおりERRORなので誤記の検知力は落ちない)
            if cols[src_g]['numeric'] and (lbl == '任意' or lbl in cols[src_g]['opts']):
                notes.append({'src_g': src_g, 'src_name': src_name,
                              'src_choice': '任意', 'targets': targets})
                continue
            if lbl not in cols[src_g]['opts']:
                issues.append({
                    'level': 'ERROR',
                    'text': (f'{where}: 選択肢「{lbl}」が条件「{src_name}」の'
                             f'選択肢に存在しません(候補: '
                             f'{" / ".join(cols[src_g]["opts"])})。'
                             f'この条件分岐はテストケースに反映されません。')})
                continue
            notes.append({'src_g': src_g, 'src_name': src_name,
                          'src_choice': lbl, 'targets': targets})
        if len(notes) > before:
            parsed += 1
    return {'cols': cols, 'notes': notes, 'note_issues': issues,
            'note_raw_count': len(notes_raw), 'note_parsed_count': parsed}


def note_lint(g, label='改修後G条件'):
    """(注)の解釈結果を人が読める行の並びで返す(UI/ログ共用)。
    「注は3件あるが engine は2件しか使っていない」を可視化するのが目的。"""
    issues = g.get('note_issues') or []
    lines = []
    raw = g.get('note_raw_count', 0)
    ok = g.get('note_parsed_count', 0)
    if raw:
        head = f'{label}: (注) {raw}件のうち {ok}件をテストケースに反映しました。'
        if ok < raw:
            head += f' 残り {raw - ok}件は反映されていません。'
        lines.append({'level': 'ERROR' if ok < raw else 'INFO', 'text': head})
    lines.extend(issues)
    return lines


# ------------------------------------------------------------------
# 20 ↔ 30 の突合 (列マッチ・選択肢マッチ)
# ------------------------------------------------------------------

def _strip_tail_paren(s):
    return str(s).rstrip(')）').rstrip()


def _prefix_bonus(o, nw):
    """B-7(#27): 注記追加型rename(旧ラベル+「(床版桁を除く)」等)の検出。
    旧が新の接頭辞(末尾括弧無視。逆=注記削除も同様)なら1。
    SequenceMatcher.ratioは接尾辞追加に長さペナルティがかかり、同名系列
    (T桁⇔床版桁)のたすき掛けの方が高スコアになる誤ペアリングを防ぐ。
    ※被覆率(一致文字率)方式は短ラベル(22 20cm以下⇔0.2m以下)で誤爆するため不採用。"""
    a, b = _strip_tail_paren(o), _strip_tail_paren(nw)
    return 1 if a and b and (b.startswith(a) or a.startswith(b)) else 0


def _best_pairs(olds, news, cutoff=0.4):
    """類似度でペアリング。returns (pairs[(old,new)], removed[old], added[new])
    スコアは(接頭辞一致, ratio)の辞書順(B-7)。"""
    olds = list(olds)
    news = list(news)
    pairs = []
    # 完全一致優先
    for o in list(olds):
        if o in news:
            pairs.append((o, o))
            olds.remove(o)
            news.remove(o)
    cand = []
    for o in olds:
        for nw in news:
            r = difflib.SequenceMatcher(None, o, nw).ratio()
            if r >= cutoff:
                cand.append(((_prefix_bonus(o, nw), r), o, nw))
    cand.sort(reverse=True)
    used_o, used_n = set(), set()
    for r, o, nw in cand:
        if o in used_o or nw in used_n:
            continue
        pairs.append((o, nw))
        used_o.add(o)
        used_n.add(nw)
    removed = [o for o in olds if o not in used_o]
    added = [nw for nw in news if nw not in used_n]
    return pairs, removed, added


def diff_gjoken(g20, g30):
    """列マッチと選択肢差分。
    returns {
      'col_map': {i20: i30}, 'new_cols': [i30...], 'del_cols': [i20...],
      'choice_diffs': {i20: {'renames': {old:new}, 'dels': [old], 'adds': [new]}},
    }
    del_cols = 30で対応列が見つからなかった20の列(=条件そのものの削除)。
    (2026-08-27 不具合#544444: 削除列が戻り値に無く、TCに残り続けていた)"""
    names20 = [c['name'] for c in g20['cols']]
    names30 = [c['name'] for c in g30['cols']]
    # 条件名は **完全一致のみ** 同じ条件とみなす(2026-09-02 ユーザ決定)。
    #   以前は類似度0.5+接頭辞ボーナス(B-7)で改名を検出していたが、文字だけでは
    #   「同じ物の名前直し」と「別物への置き換え」を判別できず、改名扱いのまま選択肢を
    #   全入替すると擬似JSONの選択肢が0行になって落ちた(鉄枠固定ボルト材料費 160-1722:
    #   「蓋区分」→「○区分」)。名前が変わった列は別条件(削除列+新規列)として扱い、
    #   テストは安全側(全選択肢網羅で多く出る)に振る。不要なTCは人が削れる。
    #   影響(回帰35工種の実測): 13・27 の注記追加型改名が別条件扱いになる(TC +5行)。他は不変。
    pairs = [(n, n) for n in names20 if n in names30]
    name_map = dict(pairs)
    col_map = {}
    used30 = set()
    for i20, nm in enumerate(names20):
        if nm in name_map:
            nm30 = name_map[nm]
            for i30, x in enumerate(names30):
                if x == nm30 and i30 not in used30:
                    col_map[i20] = i30
                    used30.add(i30)
                    break
    new_cols = [i for i in range(len(names30)) if i not in used30]
    del_cols = [i for i in range(len(names20)) if i not in col_map]
    choice_diffs = {}
    for i20, i30 in col_map.items():
        c20, c30 = g20['cols'][i20], g30['cols'][i30]
        if c20['numeric'] or c30['numeric']:
            continue
        pairs, dels, adds = _best_pairs(c20['opts'], c30['opts'])
        renames = {o: nw for o, nw in pairs if o != nw}
        if renames or dels or adds:
            choice_diffs[i20] = {'renames': renames, 'dels': dels, 'adds': adds}
    return {'col_map': col_map, 'new_cols': new_cols, 'del_cols': del_cols,
            'choice_diffs': choice_diffs}


def _note_key(g, nt):
    """注(ゲート)を名前ベースの正規化キーにする(20/30間の突合用)。"""
    cols = g.get('cols', [])
    tnames = tuple(sorted(cols[t]['name'] for t in nt.get('targets', [])
                          if 0 <= t < len(cols)))
    return (nt.get('src_name', ''), nt.get('src_choice', ''), tnames)


# ------------------------------------------------------------------
# 擬似改定後JSON 合成 (選択肢の削除・文字変更・追加を改定前JSONへ適用)
# ------------------------------------------------------------------

def _norm(v):
    v = str(v or '').strip()
    v = re.sub(r'^【[^】]*】[\s　]+', '', v)
    return v.replace('\r\n', ' ').strip()


def _rename_preserving_newlines(cell, old_norm, new_norm):
    """セル原文の改行(\r\n)構造を保ちながらラベルを old→new に書き換える。
    G条件ラベルは改行を空白に正規化しているため、そのまま書き戻すとセルが1行に潰れ、
    「(※標準)」等の行構造前提のデフォルト行判定が壊れる(例 24 表層)。
    方法: 正規化ビュー(改行→空白)とセル原文の文字対応表を作り、
    SequenceMatcher(old→new)の編集をセル原文へ射影する。"""
    raw = str(cell)
    # 正規化ビューとインデックス対応 (\r\n(2文字)→' '(1文字))
    view = []
    idx = []  # view位置 -> raw開始位置
    i = 0
    while i < len(raw):
        if raw.startswith('\r\n', i):
            view.append(' ')
            idx.append(i)
            i += 2
        else:
            view.append(raw[i])
            idx.append(i)
            i += 1
    idx.append(len(raw))
    view_s = ''.join(view)
    # 先頭の【..】プレフィックス除去(_normと同じ)分だけオフセット
    import re as _re
    m = _re.match(r'^【[^】]*】[\s　]+', view_s)
    off = m.end() if m else 0
    if view_s[off:].strip() != old_norm:
        return new_norm  # 対応が取れない場合は従来どおり
    base = off + (len(view_s[off:]) - len(view_s[off:].lstrip()))
    out = raw[:idx[base]]
    pos = base
    from difflib import SequenceMatcher
    for tag, a0, a1, b0, b1 in SequenceMatcher(
            None, view_s[base:base + len(old_norm)], new_norm).get_opcodes():
        if tag == 'equal':
            out += raw[idx[base + a0]:idx[base + a1]]
        elif tag in ('replace', 'insert'):
            out += new_norm[b0:b1]
        # delete は何も足さない
    out += raw[idx[base + len(old_norm)]:]
    return out


def _edit_sit(data, sit_no, dels, renames, adds):
    """Sitsumon019(sit_no) に選択肢の削除/文字変更/追加を適用。added labels を返す。"""
    applied_adds = []
    for s in data.get('Sitsumon019', []):
        if s.get('SitsumonNo') != sit_no:
            continue
        cells = s.get('SitTabCells', [])

        def rows_with(label):
            return {c['RowID'] for c in cells
                    if _norm(c.get('Value')) == label or
                    difflib.SequenceMatcher(None, _norm(c.get('Value')), label).ratio() >= 0.99}

        for lbl in dels:
            rids = rows_with(lbl)
            if not rids:
                continue
            for key in ('SitRows', 'SitTabRows'):
                s[key] = [r for r in s.get(key, []) if r.get('RowID') not in rids]
            s['SitTabCells'] = [c for c in cells if c['RowID'] not in rids]
            cells = s['SitTabCells']
        for old, new in renames.items():
            rids = rows_with(old)
            for c in cells:
                if c['RowID'] in rids and _norm(c.get('Value')) == old:
                    c['Value'] = _rename_preserving_newlines(
                        str(c.get('Value') or ''), old, new)
        for new_lbl, src_lbl in adds:
            src_rids = rows_with(src_lbl)
            if not src_rids:
                continue
            src_rid = sorted(src_rids)[0]
            all_rids = [r.get('RowID') for r in s.get('SitTabRows', [])]
            new_rid = max(all_rids, default=0) + 1
            for key in ('SitRows', 'SitTabRows'):
                for r in s.get(key, []):
                    if r.get('RowID') == src_rid:
                        rr = copy.deepcopy(r)
                        rr['RowID'] = new_rid
                        s[key].append(rr)
                        break
            for c in list(cells):
                if c['RowID'] == src_rid:
                    cc = copy.deepcopy(c)
                    cc['RowID'] = new_rid
                    if _norm(cc.get('Value')) == src_lbl:
                        cc['Value'] = new_lbl
                    cells.append(cc)
            applied_adds.append(new_lbl)
    return applied_adds


_STD_MARK = re.compile(r'[（(]※標準[）)]')


def _fix_default_row_for_marker_move(data, sits, renames):
    """B-6(#24): renameペアで「(※標準)」が別選択肢へ移動した場合、
    擬似JSONの既定行(SitTab.DefaultRowID)を移動先の行へ書き換える。
    既定行はタブ/フロー側(SitTab)に保持され、セル文字の書換だけでは追随しないため
    (24 表層: 実改定後はフローボックス全面付け替えで既定行も更新されるが、
    擬似JSON=改定前フローは旧既定(未対策)のまま→基準行が1行余分になる)。
    安全側: 現在の DefaultRowID がマーカー喪失行と一致する場合のみ書き換える。"""
    lost = [(o, n) for o, n in renames.items()
            if _STD_MARK.search(o) and not _STD_MARK.search(n)]
    gained = [(o, n) for o, n in renames.items()
              if not _STD_MARK.search(o) and _STD_MARK.search(n)]
    if not (lost and gained):
        return
    for sit in sits:
        for s in data.get('Sitsumon019', []):
            if s.get('SitsumonNo') != sit:
                continue
            cells = s.get('SitTabCells', [])

            def rid_of(label):  # rename適用後のラベルで行を引く
                for c in cells:
                    if _norm(c.get('Value')) == label:
                        return c.get('RowID')
                return None

            old_rid = rid_of(lost[0][1])     # 旧既定行(マーカー喪失後のラベル)
            new_rid = rid_of(gained[0][1])   # 新既定行(マーカー獲得後のラベル)
            if new_rid is None:
                continue
            for t in data.get('SitTab', []):
                if (t.get('SitsumonNo') == sit and
                        t.get('DefaultRowID') not in (None, 0) and
                        (old_rid is None or t.get('DefaultRowID') == old_rid)):
                    t['DefaultRowID'] = new_rid
                    print(f"  既定行移動(※標準): Sit{sit} "
                          f"DefaultRowID {old_rid}→{new_rid}")


class GjokenApplyError(RuntimeError):
    """改修後G条件の変更を改定前JSONへ適用できなかった(利用者に理由を示して止めるための例外)。
    黙って壊れた擬似JSONで先へ進み IndexError 等になるのを防ぐ(2026-09-02)。"""


def build_pseudo_json(json_path, g20_analysis, diffs, g20, g30, out_path):
    """改定前JSONにG条件差分(既存質問分)を適用した擬似改定後JSONを作る。

    適用順は **追加 → 削除・文字変更**。追加は「最も似た既存選択肢の行を複製」して作るため、
    削除を先にすると複製元が消えて追加できない(2026-09-02 不具合。同名列で選択肢を全入替した
    場合が該当)。追加できなかった選択肢が残れば GjokenApplyError で止める。"""
    bj, gen, g_list, _ = g20_analysis
    data = json.load(open(json_path, encoding='utf-8-sig'))
    # 20のCSV列名 → analyze g_list の対応 (名前で引く。位置フォールバック)
    by_name = {}
    for g in g_list:
        by_name.setdefault(g['name'], []).append(g)
    added_labels = []
    # C-1: 列名(質問Mesho)の変更を反映 (20↔30でマッチした列の名前が違う場合。例 13/27)
    for i20, i30 in diffs['col_map'].items():
        n20, n30 = g20['cols'][i20]['name'], g30['cols'][i30]['name']
        if n20 == n30:
            continue
        for g in by_name.get(n20, []):
            for sit in g['sits']:
                for s in data.get('SitsumonItem', []):
                    if s.get('SitsumonNo') == sit and s.get('Mesho') == n20:
                        s['Mesho'] = n30
                for s in data.get('Sitsumon019', []):
                    if s.get('SitsumonNo') == sit:
                        for c in s.get('SitTabCells', []):
                            if _norm(c.get('Value')) == n20:
                                c['Value'] = n30
    for i20, d in diffs['choice_diffs'].items():
        name = g20['cols'][i20]['name']
        cands = by_name.get(name) or []
        glist = cands if cands else ([g_list[i20]] if i20 < len(g_list) else [])
        adds = []
        for new_lbl in d['adds']:
            # 追加選択肢の複製元 = 最類似の既存選択肢。追加は削除・文字変更より先に
            # 適用するので、複製元は改定前(rename前)のラベルで引く。
            src = None
            best = -1.0
            for o in g20['cols'][i20]['opts']:
                r = difflib.SequenceMatcher(None, o, new_lbl).ratio()
                if r > best:
                    best, src = r, o
            if src:
                adds.append((new_lbl, src))
        # 削除/文字変更は全sitへ適用。追加(複製注入)は基準ルートで到達している
        #   sitに限定する(B-3: G条件は系列マージで「どの系列に追加か」を失うため、
        #   99が差分行を作る基準系列に合わせる。02大型ブレーカ/40小型不整地)。
        # 系列(同名グループ)が複数sitに分かれる場合、注入先は
        #   「先頭グループのcanonical sit」1つに限定する(B-3)。
        #   G条件は系列マージで「どの系列に追加されたか」を持たないため、
        #   99のprobe経路が選ぶ代表系列(canonical)に合わせる近似。
        #   (02: [82(1300),83,84,85]+[81] → 82に限定=合格TCと同じ1300系列)
        reached = set()
        if glist:
            first = glist[0]
            if first.get('sits'):
                reached = {first['sits'][0]}
        # (1) 追加(複製注入) — 複製元が残っているうちに行う
        applied_new = set()
        if adds:
            for g in glist:
                for sit in g['sits']:
                    if reached and sit not in reached:
                        continue
                    got = _edit_sit(data, sit, [], {}, adds)
                    applied_new.update(got)
                    added_labels.extend(got)
            if not applied_new:  # 到達sitに注入できなければ全sitへフォールバック
                for g in glist:
                    for sit in g['sits']:
                        got = _edit_sit(data, sit, [], {}, adds)
                        applied_new.update(got)
                        added_labels.extend(got)
            missing = [nl for nl, _src in adds if nl not in applied_new]
            if missing:
                raise GjokenApplyError(
                    '改修後G条件の条件「%s」に追加された選択肢 %s を、改定前の質問へ反映できませんでした'
                    '（複製元となる既存の選択肢が見つかりません）。条件名・選択肢の書き方を改定前と'
                    '見比べてご確認ください。' % (name, '・'.join('「%s」' % m for m in missing)))
        # (2) 削除・文字変更
        all_sits = []
        for g in glist:
            for sit in g['sits']:
                got = _edit_sit(data, sit, d['dels'], d['renames'], [])
                added_labels.extend(got)
                all_sits.append(sit)
        if d['renames']:
            _fix_default_row_for_marker_move(data, all_sits, d['renames'])
    json.dump(data, open(out_path, 'w', encoding='utf-8'), ensure_ascii=False)
    return sorted(set(added_labels))


# ------------------------------------------------------------------
# step2計画の後編集: 新規列をゲートする既存条件を vary 昇格 (業務ルール)
# ------------------------------------------------------------------

def promote_gate_axes(plan_csv, gate_names):
    rows = _read_csv_any(plan_csv)
    changed = []
    for r in rows[1:]:
        if len(r) < 6:
            continue
        label = re.sub(r'\(固定\)$', '', r[4]).strip()
        if r[1].strip() in gate_names or label in gate_names:
            if r[2] in ('fix', 'auto'):
                r[2] = 'vary'
                r[4] = label
                r[5] = f'業務ルール(G条件ゲート): {label}の切替が新規条件/計上に影響'
                changed.append(label)
            elif (r[2] == 'vary' and '業務ルール' not in str(r[5])
                  and '選択肢テキスト変更' in str(r[5])
                  and '追加' not in str(r[5]) and '削除' not in str(r[5])):
                # B-7(#27): 純粋な「選択肢テキスト変更」varyのゲート軸は、step3の
                #   「既定行+最長行」剪定(新提案A)で非既定選択肢の経路が消えることが
                #   ある(27 桁区分=最長行が既定行と同一に縮退)。ゲート軸は全選択肢
                #   網羅が必要(新規列が非既定選択肢で開く)なため業務ルールを併記。
                #   ※追加/削除混在varyはflow_equiv代表行(分岐網羅)が正なので対象外
                #   (06 作業内容で回帰行が変わるデグレを確認済み)。
                r[5] = (f"{r[5]}; 業務ルール(G条件ゲート): "
                        f"{label}の切替が新規条件/計上に影響")
                changed.append(label)
    BugakariJSON.write_csv(rows, plan_csv)
    return changed


# ------------------------------------------------------------------
# 後処理: 新規追加列の挿入・展開・◯仮置き
# ------------------------------------------------------------------

DAIKA_COL = '代価表行と数量(数率)'
DAIKA_TEXT = ('積算基準および設計書通りの代価行と数量が計上されていること\n'
              '※ただし計上区分の切り替えによる代価行変動がある場合は、設計者が追記すること')
DAIKA_DITTO = '〃'
REGRESSION_KANTEN = '・選択肢が商品(現行版)と変わっていないこと'  # 回帰行の標準観点(step3と同一)


def _axis_span(header):
    end = len(header)
    for i, h in enumerate(header):
        if str(h).startswith('期待:'):
            end = i
            break
    return 2, end


def insert_new_columns(rows, g30, diffs, col_map_20to30, g20, added_labels,
                       existing_names=None):
    existing_names = existing_names or {}
    header = rows[0]
    data = [list(r) for r in rows[1:] if r and str(r[0]).startswith('TC')]
    kansatsu_i = header.index('選択肢の適切さ確認') if '選択肢の適切さ確認' in header else None
    kikaku_i = header.index('規格名計上') if '規格名計上' in header else None
    a0, a1 = _axis_span(header)
    exp_idx = [i for i, h in enumerate(header) if str(h).startswith('期待:')]

    def colname(i):
        return re.sub(r'\(固定\)$', '', str(header[i])).strip()

    has_new = bool(diffs['new_cols'])

    for i30 in diffs['new_cols']:
        col = g30['cols'][i30]
        # 30のみの列でも、質問自体が改定前JSONに存在する場合は「新規質問」でなく
        #   「新規到達(または既存質問の条件化)」→ 展開せず先頭選択肢の1値で挿入
        #   (決定A: 欠落回避で列は出す・余分な行展開はしない。例 04 日当り施工量 / 22 砂の散布)
        # ただし 30の選択肢に改定前JSONに無いものが含まれる場合は「選択肢追加を伴う
        #   新規到達」=真の改定 → 新規質問と同様に展開する(例 00 排ガス機械の選択)。
        is_existing_q = col['name'] in existing_names
        if is_existing_q and not col['numeric']:
            old_opts = existing_names.get(col['name']) or set()
            newset = set(col['opts'])
            # 旧選択肢と1つも重ならない場合はラベル体系が別物(改定前JSONに説明列が
            #   無い等で _g_options が別列を拾うケース。例 05 吹付プラント設備=B/CⅠ…)
            #   → 選択肢追加とは判定できない → 展開しない(安全側)
            if (newset & old_opts) and (newset - old_opts):
                is_existing_q = False
        # 挿入位置: 30で直前にある「既存(=20とマッチ済み)列」の直後
        anchor = None
        rev_map = {v: k for k, v in col_map_20to30.items()}
        for j in range(i30 - 1, -1, -1):
            if j in rev_map:
                anchor = g30['cols'][j]['name']
                break
        # anchorなし(30の先頭が新規列 例03運転日数)は軸列の先頭へ挿入
        ins = a1 if anchor else a0
        if anchor:
            for i in range(a0, a1):
                if colname(i) == anchor:
                    ins = i + 1
                    break
        # 数値直接入力の新規列はTC上は「任意」(合格TC書式)。単位等は観点で補足しない(G条件側にある)
        opts = col['opts'] if not col['numeric'] else ['任意']
        colname_out = col['name'] + ('(固定)' if len(opts) == 1 and not col['numeric'] else '')
        header.insert(ins, colname_out)
        a1 += 1
        exp_idx = [i for i, h in enumerate(header) if str(h).startswith('期待:')]
        kansatsu_i = header.index('選択肢の適切さ確認') if '選択肢の適切さ確認' in header else None
        kikaku_i = header.index('規格名計上') if '規格名計上' in header else None
        # ゲート: 30の注で この列がtargetsに入る (ソース列, 選択肢)
        gates = [(g30['cols'][nt['src_g']]['name'], nt['src_choice'])
                 for nt in g30['notes'] if i30 in nt['targets']]

        # 差分行が1件も無い場合(=新規列以外に改定が無い。条件が丸ごと入れ替わった等)は
        #   回帰行しか無く、展開しないと新規列の選択肢を1つも網羅できない → 展開を許す。
        #   (2026-08-27 不具合#544444「鉄枠固定ボルト材料費」: TCが1件しか出なかった)
        has_sabun = any(str(r[1]).strip() == '差分' for r in data if len(r) > 1)

        new_data = []
        for row in data:
            row = list(row)
            # 挿入前の row に対してゲート判定 (header=挿入後 → ins分を補正して参照)
            gated = False
            for gname, gchoice in gates:
                for i in range(a0, a1):
                    if i == ins:
                        continue
                    ri = i if i < ins else i - 1
                    if ri < len(row) and colname(i) == gname and _norm(row[ri]) == gchoice:
                        gated = True
                        break
                if gated:
                    break
            kind = row[1]
            if gated or not opts:
                row.insert(ins, '-')
                new_data.append(row)
            elif is_existing_q:
                # 既存質問の新規到達列: 展開せず先頭選択肢。観点で人確認を促す
                row.insert(ins, opts[0])
                if kansatsu_i is not None:
                    while len(row) <= kansatsu_i:
                        row.append('')
                    note = (f"{col['name']}(新規到達)\n"
                            f"・改定前から存在する質問が条件として出現。選択肢と計上をGaia条件で確認")
                    if note not in str(row[kansatsu_i]):
                        cur = str(row[kansatsu_i]).strip()
                        row[kansatsu_i] = (cur + '\n' if cur else '') + note
                new_data.append(row)
            elif kind == '回帰' and has_sabun:
                # 状態戻し回帰TCは展開しない(先頭選択肢)
                row.insert(ins, opts[0])
                _mark_new_choice(row, col, opts[0], kansatsu_i, kikaku_i, exp_idx, ins)
                new_data.append(row)
            else:
                for opt in opts:
                    r2 = list(row)
                    r2.insert(ins, opt)
                    _mark_new_choice(r2, col, opt, kansatsu_i, kikaku_i, exp_idx, ins)
                    new_data.append(r2)
        data = new_data
        # 真の新規質問を行使する行はテスト区分=差分(合格TC準拠)。状態戻し回帰行は除く。
        #   単一選択肢の(固定)列は合格TCでも回帰扱いのため対象外(例 04)
        if not is_existing_q and (len(opts) > 1 or col['numeric']):
            for row in data:
                if (row[1] == '回帰' and ins < len(row)
                        and str(row[ins]).strip() not in ('', '-')
                        and (kansatsu_i is None or kansatsu_i >= len(row)
                             or '状態戻し回帰' not in str(row[kansatsu_i]))):
                    row[1] = '差分'

    # 既存列への追加選択肢(擬似JSONに複製行で注入済み) → 観点に明示
    if added_labels:
        a0b, a1b = _axis_span(header)
        for row in data:
            hit = [_norm(row[i]) for i in range(a0b, a1b)
                   if i < len(row) and _norm(row[i]) in added_labels]
            if not hit:
                continue
            ki = header.index('選択肢の適切さ確認') if '選択肢の適切さ確認' in header else None
            note = (f'・「{"、".join(hit)}」は新規追加の選択肢'
                    '(追加された系列(規格)がGaia条件と一致しているか確認)')
            if ki is not None and ki < len(row) and note not in str(row[ki]):
                row[ki] = (str(row[ki]) + '\n' if str(row[ki]).strip() else '') + note

    # TC番号振り直し
    for n, row in enumerate(data, 1):
        row[0] = f'TC-{n:03d}'
    return [header] + data


def reopen_added_choice_rows(rows, g20, g30, diffs):
    """B-1(21側溝): 追加選択肢を選んだ行について、30の注(=改定後の開閉仕様)で
    各列の開閉を再評価し、「注上は開くはずなのに '-'(または列ごと欠落)」の列を
    選択肢数に展開する。閉じる方向の書き換えはしない(安全側)。
    例: 側溝規格=各種(追加) → 注7はG9/G10しか閉じない → 内径(G8)が開く → 3択展開。"""
    header = rows[0]

    # 追加選択肢: 30列名 -> 追加ラベル集合
    added_by_col = {}
    for i20, d in diffs['choice_diffs'].items():
        if d['adds']:
            i30 = diffs['col_map'].get(i20)
            nm = g30['cols'][i30]['name'] if i30 is not None else g20['cols'][i20]['name']
            added_by_col.setdefault(nm, set()).update(d['adds'])
    if not added_by_col:
        return rows

    def colname(i):
        return re.sub(r'\(固定\)$', '', str(header[i])).strip()

    def name2idx():
        a0, a1 = _axis_span(header)
        return {colname(i): i for i in range(a0, a1)}, a0, a1

    n2i, a0, a1 = name2idx()
    kansatsu_i = header.index('選択肢の適切さ確認') if '選択肢の適切さ確認' in header else None

    # 再評価してよい列Y = 追加選択肢のある列Xについて、30の注で
    #   「Xの既存(非追加)選択肢の"すべて"がYを閉じ、追加選択肢だけ閉じ注が無い」もの。
    #   (例 21: 側溝規格①②③全てに「内径不要」注→④各種で内径が開くと確定)
    #   一部の選択肢しか注が無い場合は排他が注に現れきっていない可能性→対象外(例 40)。
    reopen_targets = set()
    name2col30 = {c['name']: c for c in g30['cols']}
    for x_nm, adds in added_by_col.items():
        xcol = name2col30.get(x_nm)
        if not xcol:
            continue
        existing_choices = [o for o in xcol['opts'] if o not in adds]
        if not existing_choices:
            continue
        # Y候補: src=X の注が閉じる列すべて
        y_by_choice = {}
        for nt in g30['notes']:
            src_nm = (g30['cols'][nt['src_g']]['name']
                      if nt['src_g'] < len(g30['cols']) else nt['src_name'])
            if src_nm != x_nm:
                continue
            for tj in nt['targets']:
                if tj < len(g30['cols']):
                    y_by_choice.setdefault(nt['src_choice'], set()).add(
                        g30['cols'][tj]['name'])
        for y_nm in set().union(*y_by_choice.values()) if y_by_choice else set():
            all_existing_close = all(y_nm in y_by_choice.get(c, set())
                                     for c in existing_choices)
            added_no_close = all(y_nm not in y_by_choice.get(a, set()) for a in adds)
            if all_existing_close and added_no_close:
                reopen_targets.add(y_nm)
    if not reopen_targets:
        return rows

    def is_added_row(row):
        return any(nm in n2i and n2i[nm] < len(row) and _norm(row[n2i[nm]]) in adds
                   for nm, adds in added_by_col.items())

    def closed_by_notes(row, target_nm):
        for nt in g30['notes']:
            tnames = [g30['cols'][t]['name'] for t in nt['targets'] if t < len(g30['cols'])]
            if target_nm not in tnames:
                continue
            src_nm = (g30['cols'][nt['src_g']]['name']
                      if nt['src_g'] < len(g30['cols']) else nt['src_name'])
            si = n2i.get(src_nm)
            if si is not None and si < len(row) and _norm(row[si]) == nt['src_choice']:
                return True
        return False

    # 先に「欠落しているが追加選択肢行で開く」列を挿入(全行'-')
    for j30, c30 in enumerate(g30['cols']):
        nm = c30['name']
        if nm in n2i or nm not in reopen_targets:
            continue
        opens = any(is_added_row(r) and not closed_by_notes(r, nm)
                    for r in rows[1:] if r and str(r[0]).startswith('TC'))
        if not opens:
            continue
        # 挿入位置: 30で直前にある既出列の直後
        ins = a1
        for k in range(j30 - 1, -1, -1):
            prev = g30['cols'][k]['name']
            if prev in n2i:
                ins = n2i[prev] + 1
                break
        header.insert(ins, nm)
        for r in rows[1:]:
            if r and str(r[0]).startswith('TC'):
                while len(r) < len(header) - 1:
                    r.append('')
                r.insert(ins, '-')
        n2i, a0, a1 = name2idx()
        kansatsu_i = header.index('選択肢の適切さ確認') if '選択肢の適切さ確認' in header else None

    # 追加選択肢行を30列順に歩き、開くのに'-'の列を展開
    out = [header]
    for row in rows[1:]:
        if not (row and str(row[0]).startswith('TC') and is_added_row(row)):
            out.append(row)
            continue
        partials = [list(row)]
        for c30 in g30['cols']:
            nm = c30['name']
            idx = n2i.get(nm)
            if idx is None or nm not in reopen_targets:
                continue
            nxt = []
            for pr in partials:
                cur = str(pr[idx]).strip() if idx < len(pr) else ''
                if cur not in ('', '-') or closed_by_notes(pr, nm):
                    nxt.append(pr)
                    continue
                opts = c30['opts'] if not c30['numeric'] else ['任意']
                if not opts:
                    nxt.append(pr)
                    continue
                for opt in opts:
                    r2 = list(pr)
                    r2[idx] = opt
                    if len(opts) > 1 and kansatsu_i is not None:
                        while len(r2) <= kansatsu_i:
                            r2.append('')
                        note = (f'{nm}(新規到達)\n・追加選択肢で開く条件。'
                                f'「{opt}」と表示されているが、Gaia条件と一致しているか')
                        if f'{nm}(新規到達)' not in str(r2[kansatsu_i]):
                            cur_k = str(r2[kansatsu_i]).strip()
                            r2[kansatsu_i] = (cur_k + '\n' if cur_k else '') + note
                    nxt.append(r2)
            partials = nxt
        out.extend(partials)

    # TC番号振り直し
    n = 0
    for r in out[1:]:
        if r and str(r[0]).startswith('TC'):
            n += 1
            r[0] = f'TC-{n:03d}'
    return out


def fix_kanten_wording(rows):
    """「選択肢の適切さ確認」内の文言をG条件運用向けに統一:
    「外部設計と正しいか」→「Gaia条件と一致しているか」(step3生成行も含めて置換)。"""
    header = rows[0]
    if '選択肢の適切さ確認' not in header:
        return rows
    ki = header.index('選択肢の適切さ確認')
    for row in rows[1:]:
        if row and str(row[0]).startswith('TC') and ki < len(row):
            row[ki] = str(row[ki]).replace('外部設計と正しいか', 'Gaia条件と一致しているか')
    return rows


def apply_g30_codes(rows, g30):
    """改定後TCの選択肢セルを、改修後G条件(30)の生ラベル(改修後Gaiaコード付き)へ
    貼り替える。③は「改定前JSON＋差分」の合成でTCを作るため、コードだけが変わった
    (例 【H=1】→【G=1】)選択肢は差分に乗らず改定前コードが残ってしまう。ここで
    列見出し一致＋ラベル(コード除去)一致により 30 側の改修後コードへ揃える
    (2026-07-08 運用者フィードバック)。数値/任意や一致先なしのセルは据え置き。
    改修後にコードが無い工種は name2map が空になり no-op(=従来出力を維持)。"""
    if not rows:
        return rows
    header = rows[0]
    name2map = {}
    for c in g30.get('cols', []):
        m = {lbl: raw for lbl, raw in zip(c.get('opts', []), c.get('opts_raw', []))
             if raw and raw != lbl}
        if m:
            name2map[c.get('name', '')] = m
    if not name2map:
        return rows
    for row in rows[1:]:
        if not row or not str(row[0]).startswith('TC'):
            continue
        for i, h in enumerate(header):
            mp = name2map.get(h)
            if not mp or i >= len(row):
                continue
            key = _norm(row[i])
            if key in mp:
                row[i] = mp[key]
    return rows


_LEAD_COLS = ('テストID', 'テスト区分')
_TAIL_COLS = (DAIKA_COL, '選択肢の適切さ確認', '規格名計上')


def add_gate_branch_rows(rows, g20, g30):
    """改定で(注)=ゲートが新設/変更された源泉条件について、ゲートの両側
    (効く側=改定で入力不要になる/効かない側=入力対象のまま)を通す行を足す
    (2026-07-08 運用者フィードバック: 前方選択で後方挙動が変わる→両側テストが妥当)。

    ★爆発回避: vary昇格(全展開・直積)はしない。回帰行(先頭TC)を土台に、当該
    源泉条件“だけ”を効く側/効かない側の代表choiceへ切り替えた行を複製追加する
    (他条件は据え置き)。既に在る側は足さない(＝+1行/ゲート程度に厳選)。
    ※apply_g30_gating の前に呼ぶ(効かない側の複製に対象列の非ゲート値を残すため)。"""
    if not rows:
        return rows
    header = rows[0]
    data = [r for r in rows[1:] if r and str(r[0]).startswith('TC')]
    if not data:
        return rows
    cols = g30.get('cols', [])
    s20 = {_note_key(g20, nt) for nt in g20.get('notes', [])}
    changed = [nt for nt in g30.get('notes', []) if _note_key(g30, nt) not in s20]
    if not changed:
        return rows

    def hidx(name):
        for i, h in enumerate(header):
            if re.sub(r'\(固定\)$', '', str(h)) == name:
                return i
        return None
    from collections import defaultdict
    src2gc = defaultdict(set)
    gate_effects = []   # (si, src_name, choice, [target_names])
    for nt in changed:
        sg = nt.get('src_g')
        if sg is None or not (0 <= sg < len(cols)):
            continue
        src2gc[sg].add(nt.get('src_choice'))
        si0 = hidx(cols[sg].get('name', ''))
        if si0 is not None:
            tnames = [cols[t].get('name', '') for t in nt.get('targets', [])
                      if 0 <= t < len(cols)]
            gate_effects.append((si0, cols[sg].get('name', ''),
                                 nt.get('src_choice'), tnames))
    try:
        ti = header.index('テスト区分')
    except ValueError:
        ti = 1
    ki = header.index('選択肢の適切さ確認') if '選択肢の適切さ確認' in header else None
    di = header.index(DAIKA_COL) if DAIKA_COL in header else None  # 代価表行と数量(数率)

    def gate_kanten(row):
        """行が踏む「変わったゲート」の差分観点(効かない側なら空文字)。"""
        parts = []
        for si0, src, choice, tnames in gate_effects:
            if si0 < len(row) and _norm(row[si0]) == choice and tnames:
                tstr = '・'.join(f'「{t}」' for t in tnames)
                parts.append(f'・改定により「{src}」で「{choice}」選択時は '
                             f'{tstr} が入力対象外(不要)になっていること')
        return '\n'.join(parts)

    template = list(data[0])   # 回帰行を土台
    added = []
    for sg, gchoices in src2gc.items():
        col = cols[sg]
        si = hidx(col.get('name', ''))
        if si is None:
            continue
        opts = col.get('opts', [])
        raws = col.get('opts_raw', opts)
        firing = [o for o in opts if o in gchoices]
        offs = [o for o in opts if o not in gchoices]
        want = ([firing[0]] if firing else []) + ([offs[0]] if offs else [])
        present = {_norm(r[si]) for r in (data + added) if si < len(r)}
        for ch in want:
            if ch in present:
                continue
            raw = raws[opts.index(ch)] if ch in opts else ch
            nr = list(template)
            while len(nr) <= max(si, ti):
                nr.append('')
            nr[si] = raw
            is_firing = ch in gchoices
            nr[ti] = '差分' if is_firing else '回帰'
            if ki is not None and ki < len(nr):
                nr[ki] = gate_kanten(nr) if is_firing else REGRESSION_KANTEN
            if di is not None and di < len(nr):
                nr[di] = DAIKA_DITTO   # 追加行(2行目以降)は「〃」
            added.append(nr)
            present.add(ch)
    if not added:
        return rows
    out = [header] + data + added
    # 既存行の再割当: 変わったゲートを踏む(効く側)行は 差分＋ゲート改定観点。
    #   効かない既存行はタグ・観点を維持(choice差分等を降格しない)。
    for r in data:
        kant = gate_kanten(r)
        if not kant:
            continue
        r[ti] = '差分'
        if ki is not None and ki < len(r):
            cur = str(r[ki]).strip()
            if not cur or cur.startswith('・選択肢が商品'):
                r[ki] = kant
            elif kant not in cur:
                r[ki] = cur + '\n' + kant
    for i, r in enumerate(out[1:], 1):   # テストID再採番
        if r:
            r[0] = f'TC-{i:03d}'
    return out


def drop_deleted_condition_columns(rows, g20, g30, diffs):
    """改修後G条件(30)で削除された条件列をTCから落とす
    (2026-08-27 不具合#544444「鉄枠固定ボルト材料費」)。

    ③は「改定前JSON＋差分」の合成でTCを作るため、改定で条件そのものが消えた列
    (例: 蓋区分 が廃止され ロックボルト(M-16) の形状 に置き換わった)が
    改定前フローのまま残り、改修後には存在しない条件がTCに出てしまう。
    reconcile_columns_with_g30 の対(=30を正として「足す」の逆で「引く」)。

    落とすのは diff_gjoken が del_cols と判定した列(=20にあり30に対応が無い)のみ。
    30に同名列がある場合は安全側で残す。列を落とした結果TCが完全重複したら統合する
    (削除された条件がvary軸だった場合に同じ行が並ぶため)。"""
    if not rows or not diffs.get('del_cols'):
        return rows
    names30 = {c.get('name') for c in g30.get('cols', [])}
    cols20 = g20.get('cols', [])
    targets = {cols20[i]['name'] for i in diffs['del_cols']
               if 0 <= i < len(cols20) and cols20[i].get('name')}
    targets -= names30
    if not targets:
        return rows
    header = rows[0]
    meta = set(_LEAD_COLS) | set(_TAIL_COLS)

    def norm_hdr(h):
        return re.sub(r'\(固定\)$', '', str(h)).strip()
    drop = {i for i, h in enumerate(header)
            if h not in meta and not str(h).startswith('期待:')
            and norm_hdr(h) in targets}
    if not drop:
        return rows
    print('【削除条件の列をTCから除去】%s' % sorted(norm_hdr(header[i]) for i in drop))
    # 削除の検証観点を付与(既存の「削除された選択肢が表示されないこと」と同書式)。
    #   列を落としただけでは「条件が消えたこと」を確かめるTCが1件も無くなるため。
    ki = header.index('選択肢の適切さ確認') if '選択肢の適切さ確認' in header else None
    if ki is not None:
        gone = sorted(norm_hdr(header[i]) for i in drop)
        note = ('条件削除\n・削除された条件(%s)が表示されないこと' % '、'.join(gone))
        for r in rows[1:]:
            if not r or not str(r[0]).startswith('TC'):
                continue
            while len(r) <= ki:
                r.append('')
            if note not in str(r[ki]):
                cur = str(r[ki]).strip()
                r[ki] = (cur + '\n' if cur else '') + note
    keep = [i for i in range(len(header)) if i not in drop]
    out = [[r[i] if i < len(r) else '' for i in keep] for r in rows]
    # 重複TC行の統合(テストIDを除いて全一致なら1行に)
    dedup = [out[0]]
    seen = set()
    for r in out[1:]:
        if not r or not str(r[0]).startswith('TC'):
            dedup.append(r)
            continue
        key = tuple(r[1:])
        if key in seen:
            continue
        seen.add(key)
        dedup.append(r)
    for n, r in enumerate([x for x in dedup[1:] if x and str(x[0]).startswith('TC')], 1):
        r[0] = 'TC-%03d' % n
    return dedup


def reconcile_columns_with_g30(rows, g30):
    """改修後G条件(30)にあってTCに無い条件列を補う(2026-07-08 運用者フィードバック:
    TCの条件列は改修後G条件を正とし、(注)で不要な行だけ「-」)。

    ③は「改定前JSON＋差分」の合成でTCを作るため、フロー改定で“改修後は開くが
    商品フローでは開かなかった列”(例: 発注者仕様ポールでの灯柱列)が欠落する。
    30 を正として欠落列を追加する。値: 30の(注)でその行が不要になるなら「-」、
    それ以外は 数値列=「任意」/選択列=既定choice(先頭・改修後コード付き)。
    列位置は後段 reorder_by_g30 が 30 の順へ整える(ここでは末尾に足す)。"""
    if not rows:
        return rows
    header = rows[0]
    cols = g30.get('cols', [])
    notes = g30.get('notes', [])
    if not cols:
        return rows

    def norm_hdr(h):
        return re.sub(r'\(固定\)$', '', str(h))
    name2i = {}
    for i, h in enumerate(header):
        name2i.setdefault(norm_hdr(h), i)
    present = set(name2i)
    missing = [c for c in cols if c.get('name') and c['name'] not in present]
    if not missing:
        return rows

    def gated_for_row(r, colname):
        for nt in notes:
            tnames = [cols[t]['name'] for t in nt.get('targets', []) if 0 <= t < len(cols)]
            if colname not in tnames:
                continue
            sg = nt.get('src_g')
            if sg is None or not (0 <= sg < len(cols)):
                continue
            si = name2i.get(cols[sg]['name'])
            if si is not None and si < len(r) and _norm(r[si]) == nt.get('src_choice'):
                return True
        return False
    for c in missing:
        nm = c['name']
        raws = c.get('opts_raw') or c.get('opts') or []
        default = '任意' if c.get('numeric') else (raws[0] if raws else '任意')
        header.append(nm)
        for r in rows[1:]:
            while len(r) < len(header) - 1:
                r.append('')
            if r and str(r[0]).startswith('TC'):
                r.append('-' if gated_for_row(r, nm) else default)
            else:
                r.append('')
    return rows


def apply_g30_gating(rows, g30):
    """改定後TCに、改修後G条件(30)の注(分岐ゲート)を適用する
    (2026-07-08 運用者フィードバック)。

    ③は「改定前JSON＋差分」の合成でTCを作るため、改定で新設された分岐
    (例: 区分「25m以下」を選ぶと「補修延べ延長」は入力不要)が反映されず、
    30 の注では不要な条件列にTCが値を出してしまう。ここで 30 の各注
    (src列で src_choice を選んだら targets列は不要) を各TC行に適用し、
    条件成立時の対象列セルを「-」(不要)にする。列は名前で対応付ける。"""
    if not rows:
        return rows
    header = rows[0]
    cols = g30.get('cols', [])
    notes = g30.get('notes', [])
    if not notes or not cols:
        return rows

    def hidx(name):   # G条件列名 → TCヘッダー位置 (「(固定)」を無視)
        for i, h in enumerate(header):
            if re.sub(r'\(固定\)$', '', str(h)) == name:
                return i
        return None
    for row in rows[1:]:
        if not row or not str(row[0]).startswith('TC'):
            continue
        for nt in notes:
            sg = nt.get('src_g')
            if sg is None or not (0 <= sg < len(cols)):
                continue
            si = hidx(cols[sg].get('name', ''))
            if si is None or si >= len(row):
                continue
            if _norm(row[si]) != nt.get('src_choice'):
                continue
            for tg in nt.get('targets', []):
                if not (0 <= tg < len(cols)):
                    continue
                ti = hidx(cols[tg].get('name', ''))
                if ti is not None and ti < len(row):
                    row[ti] = '-'
    return rows


def drop_unused_condition_columns(rows):
    """全TC行で「-」/空になった条件列を丸ごと落とす(2026-07-08 運用者フィードバック)。
    改修後ゲート適用で、どのテスト行でも使われなくなった条件列(特に1行TCで列全体が
    「-」)を除去する。エンジンの「どのルートでも開かない列は出さない」方針と整合。
    一部行のみ「-」(=他行で使う)列は従来どおり残す。先頭/末尾の固定列は対象外。"""
    if not rows:
        return rows
    header = rows[0]
    meta = set(_LEAD_COLS) | set(_TAIL_COLS)
    data = [r for r in rows[1:] if r and str(r[0]).startswith('TC')]
    if not data:
        return rows
    drop = set()
    for i, h in enumerate(header):
        if h in meta or str(h).startswith('期待:'):
            continue
        vals = [(r[i] if i < len(r) else '').strip() for r in data]
        if all(v in ('', '-') for v in vals):
            drop.add(i)
    if not drop:
        return rows
    keep = [i for i in range(len(header)) if i not in drop]
    return [[r[i] if i < len(r) else '' for i in keep] for r in rows]


def drop_placeholder_kanten(rows):
    """回帰観点の埋め草を、実質的な観点が付いた行から取り除く
    (2026-08-27 運用FB「追加条件の行に『商品と変わっていないこと』と出るのはおかしい」)。

    「・選択肢が商品(現行版)と変わっていないこと」は step3(generate_csv.py FB②)が
    「この行には確認事項が無い」ときに置く埋め草。③はその後段で新規列・条件削除・
    ゲート改定の観点を同じセルへ追記するため、「観点ゼロ」の前提が崩れた行に
    埋め草だけが残り矛盾する。最終段で、他に実質的な観点がある行のみ埋め草を落とす。
    埋め草しか無い行(本来の回帰TC)はそのまま残す。"""
    if not rows:
        return rows
    header = rows[0]
    if '選択肢の適切さ確認' not in header:
        return rows
    ki = header.index('選択肢の適切さ確認')
    n = 0
    for r in rows[1:]:
        if not r or not str(r[0]).startswith('TC') or ki >= len(r):
            continue
        lines = str(r[ki]).split('\n')
        if not any(x.strip() == REGRESSION_KANTEN for x in lines):
            continue
        rest = [x for x in lines if x.strip() and x.strip() != REGRESSION_KANTEN]
        if not rest:
            continue          # 埋め草しか無い = 本来の回帰行 → 残す
        r[ki] = '\n'.join(rest)
        n += 1
    if n:
        print('【回帰観点の埋め草を除去】%d行(他に確認観点があるため)' % n)
    return rows


def reorder_by_g30(rows, g30):
    """改定後TCの条件列の並びを、改修後G条件(30)の列順に揃える
    (2026-07-08 運用者フィードバック: G条件とTCの条件順は一致すべき)。

    ③は「改定前JSON＋差分」の合成でTCを作るため、条件列の並びは改定前のフロー順に
    従う。改定で質問順が変わった工種では 30(改修後G条件)とTCで列順が食い違う
    (照合は列名ベースで正しいが、見比べたとき紛らわしい)。ここで最終出力の条件列を
    30 の列順へ並べ替える(内容は不変・並べ替えのみ)。30 に無い条件列(改定前のみ等)は
    30 一致列の後ろへ元の相対順で残す。先頭(テストID/テスト区分)・末尾(代価表行と数量/
    選択肢の適切さ確認/規格名計上)の固定列は位置を保つ。"""
    if not rows:
        return rows
    header = rows[0]
    meta = set(_LEAD_COLS) | set(_TAIL_COLS)
    def _match(h):
        # TC側は auto/fix軸に「(固定)」を付ける。G条件(30)は素の名前なので剥がして照合。
        return re.sub(r'\(固定\)$', '', str(h))
    # G条件(30)の列名→出現位置リスト(同名重複に対応)。
    from collections import defaultdict
    g30occ = defaultdict(list)
    for i, c in enumerate(g30.get('cols', [])):
        g30occ[c.get('name', '')].append(i)
    lead_idx = [i for i, h in enumerate(header) if h in _LEAD_COLS]
    tail_idx = [i for i, h in enumerate(header) if h in _TAIL_COLS]
    exp_idx = [i for i, h in enumerate(header) if str(h).startswith('期待:')]
    cond_idx = [i for i, h in enumerate(header)
                if h not in meta and not str(h).startswith('期待:')]
    # 同名は「TCの元の並び順」に g30 の出現位置を順に割り当てる(貪欲)。
    #   30に無い/出現数超過の列は末尾へ(元の相対順を保持)。
    used = defaultdict(int)
    posof = {}
    for i in cond_idx:
        nm = _match(header[i]); q = g30occ.get(nm)
        if q and used[nm] < len(q):
            posof[i] = q[used[nm]]; used[nm] += 1
        else:
            posof[i] = 10 ** 6
    cond_sorted = sorted(cond_idx, key=lambda i: (posof[i], i))
    new_order = lead_idx + cond_sorted + exp_idx + tail_idx
    seen = set(new_order)
    for i in range(len(header)):   # 取りこぼし防止(全列を1回ずつ)
        if i not in seen:
            new_order.append(i)
    return [[r[i] if i < len(r) else '' for i in new_order] for r in rows]


def replace_expected_columns(rows):
    """「期待:変数」列を全廃し、同位置に「代価表行と数量(数率)」列を1本置く。
    1行目=定型文(積算基準および設計書通り…+※計上区分切替の追記依頼)、2行目以降=「〃」。
    (内部数値の改定があると改定前ベース値は保証できないため、数値期待は出さない)"""
    header = rows[0]
    exp = [i for i, h in enumerate(header) if str(h).startswith('期待:')]
    if not exp:
        return rows
    e0, e1 = exp[0], exp[-1] + 1
    new_header = header[:e0] + [DAIKA_COL] + header[e1:]
    out = [new_header]
    first = True
    for row in rows[1:]:
        if not row or not str(row[0]).startswith('TC'):
            out.append(row)
            continue
        while len(row) < len(header):
            row.append('')
        cell = DAIKA_TEXT if first else DAIKA_DITTO
        first = False
        out.append(row[:e0] + [cell] + row[e1:])
    return out


def _mark_new_choice(row, col, opt, kansatsu_i, kikaku_i, exp_idx, ins):
    """展開行: 観点・規格名計上を追記。rowは挿入後index。"""
    lines = [f"{col['name']}(新規追加)",
             f'・「{opt}」と表示されているが、Gaia条件と一致しているか']
    if kansatsu_i is not None:
        while len(row) <= kansatsu_i:
            row.append('')
        cur = str(row[kansatsu_i]).strip()
        row[kansatsu_i] = (cur + '\n' if cur else '') + '\n'.join(lines)
    if col.get('kikaku') and kikaku_i is not None:
        while len(row) <= kikaku_i:
            row.append('')
        cur = str(row[kikaku_i]).strip()
        add = f"・{col['name']} の規格名計上が意図通りの場所に正しく計上されているか"
        if add not in cur:
            row[kikaku_i] = (cur + '\n' if cur else '') + add


# ------------------------------------------------------------------
# 新規歩掛モード: 改修後G条件(表)1枚だけから TC を生成する
#   (2026-07-09 運用者フィードバック: 新規歩掛は改定前JSON/商品G条件が存在しない)
# ------------------------------------------------------------------

NEW_BASE_KANTEN = '・各条件を既定(先頭)の選択肢で選んだ場合の計上がGaia条件と一致していること'


def build_tc_from_single_gjoken(g30):
    """改修後G条件(表)だけから改定後TC同形式の叩き台を組む。

    JSON/差分は使わない。表の 列・選択肢・(注)ゲート から直接展開する:
      - 既定行(回帰): 各列を先頭選択肢に。(注)ゲートで不要になる列は「-」。
      - 差分行: 列ごとに非既定の選択肢を1つずつ振る(他列は既定=one-factor-at-a-time)。
        ★対象列が既定で(注)ゲートに閉じられている場合は、源泉列を開く選択肢へ
          切り替えてから振る(でないと対象列が常に「-」で網羅できない)。
      - 数値入力列/単一選択肢の(固定)列は差分行を作らない(合格TC書式)。
      - 代価表行と数量・選択肢の適切さ確認・規格名計上 の各列は③と同じ体裁で埋める。
    """
    cols = g30.get('cols', [])
    notes = g30.get('notes', [])
    n = len(cols)

    def raws_of(c):
        return c.get('opts_raw') or c.get('opts') or []

    def default_raw(i):
        c = cols[i]
        if c.get('numeric'):
            return '任意'
        rs = raws_of(c)
        return rs[0] if rs else '-'

    def raw_at(i, j):
        c = cols[i]
        rs = raws_of(c)
        if j < len(rs):
            return rs[j]
        opts = c.get('opts') or []
        return opts[j] if j < len(opts) else '-'

    def closing_choices(src, tgt):
        """源泉列 src が対象列 tgt を閉じる選択肢ラベル集合。"""
        return {nt.get('src_choice') for nt in notes
                if nt.get('src_g') == src and tgt in nt.get('targets', [])}

    base = [default_raw(i) for i in range(n)]

    def apply_gate(cond):
        cond = list(cond)
        for nt in notes:
            sg = nt.get('src_g')
            if sg is None or not (0 <= sg < n):
                continue
            if _norm(cond[sg]) == nt.get('src_choice'):
                for t in nt.get('targets', []):
                    if 0 <= t < n:
                        cond[t] = '-'
        return cond

    def open_base_for(i):
        """列 i を可視化する土台。i を閉じる源泉列が既定で閉じているなら開く選択肢へ切替。"""
        cond = list(base)
        for nt in notes:
            if i not in nt.get('targets', []):
                continue
            sg = nt.get('src_g')
            if sg is None or not (0 <= sg < n):
                continue
            if _norm(cond[sg]) != nt.get('src_choice'):
                continue
            closing = closing_choices(sg, i)
            opts = cols[sg].get('opts') or []
            alt = next((k for k, o in enumerate(opts) if o not in closing), None)
            if alt is not None:
                cond[sg] = raw_at(sg, alt)
        return cond

    # ヘッダー(③と同じ列体裁)
    cond_header = []
    for c in cols:
        nm = c.get('name', '')
        if not c.get('numeric') and len((c.get('opts') or [])) <= 1:
            nm = nm + '(固定)'
        cond_header.append(nm)
    header = list(_LEAD_COLS) + cond_header + [DAIKA_COL, '選択肢の適切さ確認', '規格名計上']

    # 展開計画: (テスト区分, cond, 振った列index or None, 振った選択肢ラベル or None)
    #   新規歩掛には「改定前」が無いので基準行の区分は「回帰」でなく「基準」
    #   (2026-07-09 運用者フィードバック)。差分行=基準から1条件だけ変えた行。
    plan = [('基準', apply_gate(base), None, None)]
    for i, c in enumerate(cols):
        opts = c.get('opts') or []
        if c.get('numeric') or len(opts) <= 1:
            continue
        ob = open_base_for(i)
        for j in range(1, len(opts)):
            cond = list(ob)
            cond[i] = raw_at(i, j)
            plan.append(('差分', apply_gate(cond), i, opts[j]))

    def gate_note_for(vi, cond, vlabel):
        parts = []
        for nt in notes:
            if nt.get('src_g') != vi or _norm(cond[vi]) != nt.get('src_choice'):
                continue
            tnames = [cols[t].get('name', '') for t in nt.get('targets', [])
                      if 0 <= t < n]
            if tnames:
                tstr = '・'.join(f'「{t}」' for t in tnames)
                parts.append(f'・「{vlabel}」選択時は {tstr} が入力対象外(不要)になっていること')
        return '\n'.join(parts)

    out = [header]
    first = True
    for kind, cond, vi, vlabel in plan:
        row = ['', kind] + list(cond)
        daika = DAIKA_TEXT if first else DAIKA_DITTO
        first = False
        if kind == '基準':
            kanten = NEW_BASE_KANTEN
            kikaku = ''
        else:
            gk = gate_note_for(vi, cond, vlabel)
            kanten = (f"{cols[vi].get('name', '')}\n"
                      f"・「{vlabel}」を選択した場合の計上がGaia条件と一致していること"
                      + (('\n' + gk) if gk else ''))
            kikaku = ''
            if cols[vi].get('kikaku'):
                kikaku = (f"・{cols[vi].get('name', '')} の規格名計上が"
                          f"意図通りの場所に正しく計上されているか")
        row += [daika, kanten, kikaku]
        out.append(row)

    # TC採番 → どのルートでも開かない(全行'-'/空)条件列を落とす(③と同方針)
    n_tc = 0
    for r in out[1:]:
        n_tc += 1
        r[0] = f'TC-{n_tc:03d}'
    out = drop_unused_condition_columns(out)
    return out


def _koshu_from_gname(path, fallback='新規歩掛'):
    """G条件CSV/xlsxのファイル名 Gaia入力基準表_<工種>(<単位>)… から工種名を拾う。"""
    m = re.search(r'Gaia入力基準表_(.+?)\(', os.path.basename(path))
    if m:
        return re.sub(r'[\\/:*?"<>|]', '_', m.group(1)).strip()
    return fallback


def _print_note_lint(g30):
    """(注)の解釈結果をログへ。UI(webapp)は service 側で同じ note_lint を使う。"""
    lines = note_lint(g30)
    if not lines:
        return
    print('【(注)の解釈】')
    for ln in lines:
        print(f"  [{ln['level']}] {ln['text']}")


def run_single(csv30, out_dir=None, koshu=None):
    """改修後G条件(表)1枚から改定後TC叩き台CSVを生成する(新規歩掛モード)。"""
    if out_dir is None:
        out_dir = os.path.join(os.getcwd(), 'out_tc')
    os.makedirs(out_dir, exist_ok=True)
    g30 = read_gjoken(csv30)
    _print_note_lint(g30)
    out = build_tc_from_single_gjoken(g30)
    koshu = koshu or _koshu_from_gname(csv30)
    s3 = os.path.join(out_dir, f'step3.0_テストケース_{koshu}.csv')
    BugakariJSON.write_csv(out, s3)
    n = len(out) - 1
    print(f'新規歩掛TC叩き台 生成完了: {s3}  (TC {n}件 / 列 {len(out[0])})')
    return s3


# ------------------------------------------------------------------
# main
# ------------------------------------------------------------------

def run(csv20, csv30, old_json, out_dir=None):
    if out_dir is None:
        p30 = os.path.dirname(os.path.abspath(csv30))
        if os.path.basename(p30).startswith('30_'):
            out_dir = os.path.join(os.path.dirname(p30), '60_改定後TC叩き台')
        else:
            out_dir = os.path.join(os.getcwd(), 'out_tc')
    os.makedirs(out_dir, exist_ok=True)

    g20 = read_gjoken(csv20)
    g30 = read_gjoken(csv30)
    _print_note_lint(g30)
    diffs = diff_gjoken(g20, g30)
    print('【G条件差分】')
    for i20, d in diffs['choice_diffs'].items():
        print(f"  {g20['cols'][i20]['name']}: 変更={d['renames']} 削除={d['dels']} 追加={d['adds']}")
    for i30 in diffs['new_cols']:
        print(f"  新規列: {g30['cols'][i30]['name']} 選択肢={g30['cols'][i30]['opts']}")
    for i20 in diffs.get('del_cols', []):
        print(f"  削除列: {g20['cols'][i20]['name']} 選択肢={g20['cols'][i20]['opts']}")

    print('【改定前JSON解析(列⇔質問No)】')
    analysis = gen_gjoken.analyze(old_json)
    pseudo = os.path.join(out_dir, '_擬似改定後.json')
    added_labels = build_pseudo_json(old_json, analysis, diffs, g20, g30, pseudo)

    koshu = gen_gjoken._header(analysis[0])[0]
    koshu = re.sub(r'[\\/:*?"<>|]', '_', koshu).strip()
    s1 = os.path.join(out_dir, 'step1.0_G条件差分レポート.csv')
    s2 = os.path.join(out_dir, 'step2.0_テスト計画.csv')
    s3 = os.path.join(out_dir, f'step3.0_テストケース_{koshu}.csv')
    run_step1(old_json, pseudo, s1)
    run_step2(s1, pseudo, s2, old_json)

    # 新規列をゲートする既存条件を vary 昇格
    # B-7(#27): ただし「全選択肢が対象列を閉じる」ゲート(例 27 注4/5 歩掛=有/無とも
    #   埋設型枠を閉じる=対象列は当該質問の非到達経路でのみ開く)は、切替では対象列を
    #   開けないため昇格しない(余分なvary軸が行構造を崩す)。
    by_src = {}
    for nt in g30['notes']:
        tgts = [t for t in nt['targets'] if t in diffs['new_cols']]
        if not tgts:
            continue
        e = by_src.setdefault(nt['src_g'], {'names': set(), 'closed': {}})
        nm30 = g30['cols'][nt['src_g']]['name'] if nt['src_g'] < len(g30['cols']) else nt['src_name']
        e['names'].add(nm30)
        e['names'].add(nt['src_name'])
        for t in tgts:
            e['closed'].setdefault(t, set()).add(nt['src_choice'])
    gate_names = set()
    for src_g, e in by_src.items():
        opts = set(g30['cols'][src_g]['opts']) if 0 <= src_g < len(g30['cols']) else set()
        if not opts or any(opts - ch for ch in e['closed'].values()):
            gate_names |= e['names']
        else:
            print(f"【vary昇格スキップ(全選択肢が対象列を閉じる)】{sorted(e['names'])}")
    if gate_names:
        changed = promote_gate_axes(s2, gate_names)
        if changed:
            print(f"【vary昇格(業務ルール/G条件ゲート)】{changed}")

    run_step3(s2, pseudo, s3, old_json)

    rows = _read_csv_any(s3)
    # 改定前JSONの質問名 → 選択肢ラベル集合 (新規到達列の「選択肢追加」判定用)
    existing_names = {}
    for s in analysis[0].data.get('SitsumonItem', []):
        nm = s.get('Mesho')
        if not nm:
            continue
        labels = {t[1] for t in gen_gjoken._g_options(analysis[0], s.get('SitsumonNo'))}
        existing_names.setdefault(nm, set()).update(labels)
    out = insert_new_columns(rows, g30, diffs, diffs['col_map'], g20, added_labels,
                             existing_names)
    out = reopen_added_choice_rows(out, g20, g30, diffs)
    out = replace_expected_columns(out)
    out = fix_kanten_wording(out)
    out = apply_g30_codes(out, g30)
    out = add_gate_branch_rows(out, g20, g30)
    out = apply_g30_gating(out, g30)
    out = drop_deleted_condition_columns(out, g20, g30, diffs)
    out = reconcile_columns_with_g30(out, g30)
    out = drop_unused_condition_columns(out)
    out = reorder_by_g30(out, g30)
    out = drop_placeholder_kanten(out)
    BugakariJSON.write_csv(out, s3)
    n = len(out) - 1
    print(f'③改定後TC叩き台 生成完了: {s3}  (TC {n}件 / 列 {len(out[0])})')
    return s3


if __name__ == '__main__':
    # 新規歩掛モード: python3 gen_tc_from_gjoken.py --single <G条件CSV> [出力dir]
    if len(sys.argv) >= 3 and sys.argv[1] == '--single':
        run_single(sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else None)
        sys.exit(0)
    if len(sys.argv) < 4:
        print('Usage:')
        print('  python3 gen_tc_from_gjoken.py <20_叩き台G条件CSV> <30_人作成G条件CSV> <改定前JSON> [出力dir]')
        print('  python3 gen_tc_from_gjoken.py --single <改修後G条件CSV> [出力dir]   (新規歩掛モード)')
        sys.exit(1)
    run(sys.argv[1], sys.argv[2], sys.argv[3],
        sys.argv[4] if len(sys.argv) > 4 else None)
