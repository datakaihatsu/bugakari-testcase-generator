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
    pat = re.compile(r'G(\d+)条件「(.+?)」で(.+?)(?:のいずれか)?を選択した場合は、(.+?)\s*を入力する必要はない')
    for raw in notes_raw:
        m = pat.search(raw)
        if not m:
            continue
        src_g = int(m.group(1)) - 1
        src_name = m.group(2).strip()
        sel_part = m.group(3)
        targets = [int(x) - 1 for x in re.findall(r'G(\d+)条件', m.group(4))]
        labels = [x.strip() for x in re.findall(r'「(.+?)」', sel_part)]
        if '～' in sel_part and len(labels) == 2 and 0 <= src_g < len(cols):
            opts = cols[src_g]['opts']
            try:
                i0, i1 = opts.index(labels[0]), opts.index(labels[1])
                if 0 <= i0 <= i1:
                    labels = opts[i0:i1 + 1]
            except ValueError:
                pass
        for lbl in labels:
            notes.append({'src_g': src_g, 'src_name': src_name,
                          'src_choice': lbl, 'targets': targets})
    return {'cols': cols, 'notes': notes}


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
      'col_map': {i20: i30}, 'new_cols': [i30...],
      'choice_diffs': {i20: {'renames': {old:new}, 'dels': [old], 'adds': [new]}},
    }"""
    names20 = [c['name'] for c in g20['cols']]
    names30 = [c['name'] for c in g30['cols']]
    pairs, removed, added = _best_pairs(names20, names30, cutoff=0.5)
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
    choice_diffs = {}
    for i20, i30 in col_map.items():
        c20, c30 = g20['cols'][i20], g30['cols'][i30]
        if c20['numeric'] or c30['numeric']:
            continue
        pairs, dels, adds = _best_pairs(c20['opts'], c30['opts'])
        renames = {o: nw for o, nw in pairs if o != nw}
        if renames or dels or adds:
            choice_diffs[i20] = {'renames': renames, 'dels': dels, 'adds': adds}
    return {'col_map': col_map, 'new_cols': new_cols, 'choice_diffs': choice_diffs}


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


def build_pseudo_json(json_path, g20_analysis, diffs, g20, g30, out_path):
    """改定前JSONにG条件差分(既存質問分)を適用した擬似改定後JSONを作る。"""
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
            # 追加選択肢の複製元 = 最類似の既存選択肢 (rename適用後のラベルで引く)
            src = None
            best = -1.0
            for o in g20['cols'][i20]['opts']:
                r = difflib.SequenceMatcher(None, o, new_lbl).ratio()
                if r > best:
                    best, src = r, o
            if src:
                adds.append((new_lbl, d['renames'].get(src, src)))
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
        all_sits = []
        for g in glist:
            for sit in g['sits']:
                got = _edit_sit(data, sit, d['dels'], d['renames'], [])
                added_labels.extend(got)
                all_sits.append(sit)
        if d['renames']:
            _fix_default_row_for_marker_move(data, all_sits, d['renames'])
        add_ok = False
        if adds:
            for g in glist:
                for sit in g['sits']:
                    if reached and sit not in reached:
                        continue
                    got = _edit_sit(data, sit, [], {}, adds)
                    if got:
                        add_ok = True
                    added_labels.extend(got)
            if not add_ok:  # 到達sitに注入できなければ全sitへフォールバック
                for g in glist:
                    for sit in g['sits']:
                        got = _edit_sit(data, sit, [], {}, adds)
                        added_labels.extend(got)
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
            elif kind == '回帰':
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
    diffs = diff_gjoken(g20, g30)
    print('【G条件差分】')
    for i20, d in diffs['choice_diffs'].items():
        print(f"  {g20['cols'][i20]['name']}: 変更={d['renames']} 削除={d['dels']} 追加={d['adds']}")
    for i30 in diffs['new_cols']:
        print(f"  新規列: {g30['cols'][i30]['name']} 選択肢={g30['cols'][i30]['opts']}")

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
    out = reconcile_columns_with_g30(out, g30)
    out = drop_unused_condition_columns(out)
    out = reorder_by_g30(out, g30)
    BugakariJSON.write_csv(out, s3)
    n = len(out) - 1
    print(f'③改定後TC叩き台 生成完了: {s3}  (TC {n}件 / 列 {len(out[0])})')
    return s3


if __name__ == '__main__':
    if len(sys.argv) < 4:
        print('Usage: python3 gen_tc_from_gjoken.py <20_叩き台G条件CSV> <30_人作成G条件CSV> <改定前JSON> [出力dir]')
        sys.exit(1)
    run(sys.argv[1], sys.argv[2], sys.argv[3],
        sys.argv[4] if len(sys.argv) > 4 else None)
