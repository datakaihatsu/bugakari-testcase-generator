"""
③ 段付きG条件の読み取り（(注)の段表記・2026-09-29 FB → 2026-10-20 本体実装）。

③は入力に段があるかを**自動で判別**する（①のモードには依存しない）。段が無ければ何もしない
（prepare が入力パスをそのまま返す＝今の③と完全に同じ経路）。

段があるとき（設計書 §8・§9.1・§10・§12）:
  1. 段付きG条件（xlsx / csv）を読み、今の1枚の表（全段の選択肢の和）に戻す。
     「－」は「分岐点=r なら入力不要」の(注)に変える（①と同じ書き方・条件側のG順に並べる）。
     段ごとの「使える選択肢」を制約として持つ。
  2. 今の③をそのまま実行する。
  3. 段の約束に反する行（例: 損料×機-6）を段を移して直す/外し、段ごとに新しい選択肢を
     1行ずつ確認する補完と、段の確認観点を足す。
     各段に最低1行（B案）は、分岐点そのものが改定で新しく入ったときだけ。

段の読み方:
  - 段の区切り: A列の「段n」、または見出し行（条件名の行）の繰り返し（依頼者の書き方）。
  - 分岐点: 太字のセル（xlsx）。無ければ「どの段にも値があり、段どうしで値が重ならない」最も左の列。
  - 空欄=段1と共通 / 「－」=その段では聞かれない。
  - 段ごとに①から振り直していたら通し番号に直す。番号だけで指す(注)がその列にあればエラー。
  - 中身が同じ段しか無ければ 1段として扱う（お知らせ）。
"""
import os
import re

import gen_tc_from_gjoken as T
from bugakari_json import BugakariJSON

DASH = ('－', '-', '―', 'ー')
_HEAD_A = ('施工区分/入力条件', '規格名計上', '各種(条件名)')
_DAN_LABEL = re.compile(r'段\s*\d+')


def _key(s):
    return T._opt_key(s)


# ---------------------------------------------------------------- 読み込み
def _read_matrix(path):
    """[[(text, bold)]] を返す。xlsx は1枚目のシート（太字も読む）、csv は太字なし。"""
    if path.lower().endswith('.xlsx'):
        import openpyxl
        ws = openpyxl.load_workbook(path).worksheets[0]
        out = []
        for row in ws.iter_rows():
            out.append([('' if c.value is None else str(c.value).strip(),
                         bool(c.font and c.font.bold)) for c in row])
        return out
    return [[(str(c).strip(), False) for c in r] for r in T._read_csv_any(path)]


def _split_sections(m):
    """表の部分（見出し・本体）と(注)を切り出す。(外部設計メモ)以降は読まない。"""
    head_g, kikaku, names = None, None, None
    body, notes = [], []
    state = 'head'
    for r in m:
        a = r[0][0] if r else ''
        joined = ''.join(t for t, _ in r)
        if not joined:
            continue
        if '設計メモ' in a or '設計メモ' in joined:
            break
        if a == '(注)' or joined == '(注)':
            state = 'notes'
            continue
        if state == 'notes':
            notes += [t for t, _ in r if t]
            continue
        if a == '施工区分/入力条件':
            head_g = [t for t, _ in r[1:]]
        elif a == '規格名計上':
            kikaku = [t for t, _ in r[1:]]
        elif a == '各種(条件名)':
            names = [t for t, _ in r[1:]]
        elif names is not None:
            body.append(r)
    return head_g, kikaku, names, body, notes


def _cut_dans(body, names):
    """本体を段に切る。段が2つ以上なければ None。"""
    n = len(names)
    dans, cur, found_mark = [], [], False
    for r in body:
        a = r[0][0]
        cells = [r[i + 1][0] for i in range(n)]
        hit = [i for i, c in enumerate(cells) if c and names[i] and c == names[i]]
        others = [i for i, c in enumerate(cells) if c and names[i] and i not in hit]
        if len(hit) >= 2 or (hit and not others):
            # 見出し（条件名）の繰り返し＝段の区切り（依頼者の書き方）。見出しのセルは消す
            found_mark = True
            dans.append(cur)
            r = list(r)
            for i in hit:
                r[i + 1] = ('', False)
            cur = [r] if any(r[i + 1][0] for i in range(n)) else []
            continue
        if _DAN_LABEL.fullmatch(a):
            found_mark = True
            if cur:
                dans.append(cur)
            cur = []
        cur.append(r)
    dans.append(cur)
    dans = [d for d in dans if d]
    if not found_mark or len(dans) < 2:
        return None
    return dans


def has_dan(path):
    """入力のG条件に段があるか（③の自動判別）。"""
    try:
        m = _read_matrix(path)
    except Exception:  # noqa: BLE001  読めないものは段なしとして今の③に任せる
        return False
    width = max((len(r) for r in m), default=0)
    m = [r + [('', False)] * (width - len(r)) for r in m]
    _g, _k, names, body, _n = _split_sections(m)
    return bool(names) and _cut_dans(body, names) is not None


def parse_dan(path):
    """段付きG条件 → dict。段が無ければ None。決められないときは GjokenApplyError。"""
    m = _read_matrix(path)
    width = max((len(r) for r in m), default=0)
    m = [r + [('', False)] * (width - len(r)) for r in m]
    head_g, kikaku, names, body, notes = _split_sections(m)
    if not names:
        return None
    n = len(names)
    dans = _cut_dans(body, names)
    if dans is None:
        return None
    msgs = []
    keep = [i for i in range(n) if names[i]]
    for i in range(n):
        if not names[i] and any(r[i + 1][0] for r in body):
            msgs.append('INFO: 条件名の無い列（%s列）はメモとみなして読み飛ばしました'
                        % _col_letter(i + 1))

    # 段ごと・列ごとのセル
    D = []
    for d in dans:
        D.append([[(r[i + 1][0], r[i + 1][1]) for r in d if r[i + 1][0]] for i in range(n)])

    # --- 分岐点 ---
    bcol, bsets = None, None
    bold_cols = [i for i in keep if any(b for per in D for (_t, b) in per[i])]
    if len(bold_cols) == 1:
        bcol = bold_cols[0]
        bsets = [[t for t, b in per[bcol] if b] for per in D]
        if any(not s for s in bsets):
            bcol = None
    if bcol is None:
        for i in keep:          # 太字が無い（CSV等）: 全段に値があり、段どうしで重ならない最も左の列
            vals = [[_key(t) for t, _b in per[i] if t not in DASH] for per in D]
            if not all(vals):
                continue
            flat = [v for vs in vals for v in vs]
            if len(flat) == len(set(flat)):
                bcol = i
                bsets = [[t for t, _b in per[i] if t not in DASH] for per in D]
                break
    if bcol is None:
        raise T.GjokenApplyError(
            '段付きG条件の分岐点（どの選択で入る段か）が決められません。'
            '各段の分岐点の選択肢を太字にしてください。')

    # --- 列ごとの選択肢（全段の和）と段ごとの使える選択肢 ---
    cols = []
    for i in keep:
        flat, allowed, marks = [], [], {}
        for k, per in enumerate(D):
            vals = [t for t, _b in per[i]]
            if not vals:
                allowed.append('common' if k > 0 else [])
                continue
            if all(v in DASH for v in vals):
                allowed.append(None)
                continue
            ks = []
            for v in vals:
                if v in DASH:
                    continue
                kk = _key(v)
                if kk not in [_key(x) for x in flat]:
                    flat.append(v if v.startswith('(') else T._strip_circled(v))
                    marks[kk] = T._marker_index(v)
                ks.append(kk)
            allowed.append(ks)
        mk = [marks.get(_key(x)) for x in flat]
        renum = False
        if mk and all(x is not None for x in mk):
            if len(set(mk)) == len(mk):
                flat = [x for _m, x in sorted(zip(mk, flat))]   # 通し番号の順
            else:
                renum = True                                      # 段ごとに振り直し
        allowed = [allowed[0] if a == 'common' else a for a in allowed]
        cols.append({'i': i, 'name': names[i], 'opts': flat, 'allowed': allowed,
                     'renum': renum, 'kikaku': (kikaku or [''] * n)[i] == '○',
                     'g': (head_g or [''] * n)[i]})
        if renum:
            msgs.append('INFO: 「%s」は段ごとに番号を振り直していたため、通し番号に直しました'
                        % names[i])
    bi = [c['i'] for c in cols].index(bcol)
    bsets = [[_key(x) for x in s] for s in bsets]

    rest = [tuple(tuple(c['allowed'][k]) if c['allowed'][k] is not None else None
                  for j, c in enumerate(cols) if j != bi) for k in range(len(D))]
    if len(set(rest)) == 1:
        msgs.append('INFO: 段の中身がすべて同じため、1段として扱いました')
        dans_info = None
    else:
        dans_info = [{'branch': bsets[k],
                      'allowed': {c['name']: c['allowed'][k]
                                  for j, c in enumerate(cols) if j != bi}}
                     for k in range(len(D))]
    return {'cols': cols, 'bi': bi, 'dans': dans_info, 'notes': notes, 'msgs': msgs}


def _col_letter(i):
    s = ''
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s


# ---------------------------------------------------------------- 1枚の表へ
def write_flat(P, out_csv):
    """段を戻した1枚の表（今の③が読む形）を書く。ERROR があれば GjokenApplyError。"""
    cols, bi = P['cols'], P['bi']
    errs = []
    gs = [c['g'] for c in cols]
    ok = all(re.fullmatch(r'G\d+', g) for g in gs) and len(set(gs)) == len(gs)
    if not ok:
        if P['notes']:
            errs.append('G番号に欠け・重複があり、(注)の参照先が決まりません。'
                        'G番号を振り直してください。')
        else:
            P['msgs'].append('INFO: G番号に欠け・重複があったため、並び順で振り直しました')
        gs = ['G%d' % (j + 1) for j in range(len(cols))]
    for c, g in zip(cols, gs):
        if not c['renum']:
            continue
        for nt in P['notes']:
            sp = T._split_note(re.sub(r'^\d+\.\s*', '', nt))
            if sp and 'G%d' % sp[0] == g and '「' not in sp[2]:
                errs.append('(注)「%s」は番号だけで選択肢を指していますが、「%s」は段ごとに'
                            '番号が振り直されているため、どの選択肢か決まりません。'
                            '選択肢名を「」で書いてください。' % (nt, c['name']))
    if errs:
        raise T.GjokenApplyError('段付きG条件を読めません: ' + ' / '.join(errs))

    # 「－」→(注)。①と同じ書き方で作り、全(注)を条件側のG順に並べ直す
    #   （今の③は(注)の並び順でTCが変わるため、①の出力と同じ並びにそろえる）。
    gen = []
    if P['dans']:
        okeys = [_key(o) for o in cols[bi]['opts']]
        tg_by_choice = {}
        for d in P['dans']:
            for j, c in enumerate(cols):
                if j != bi and d['allowed'][c['name']] is None:
                    for b in d['branch']:
                        if b in okeys:
                            tg_by_choice.setdefault(okeys.index(b), []).append(j)
        groups = {}
        for oi, tg in sorted(tg_by_choice.items()):
            groups.setdefault(tuple(sorted(tg)), []).append(oi)
        for tg, ois in groups.items():
            if len(ois) == 1:
                sel = T._mk_disp(ois[0])
            elif ois == list(range(ois[0], ois[-1] + 1)) and len(ois) > 2:
                sel = '%s～%sのいずれか' % (T._mk_disp(ois[0]), T._mk_disp(ois[-1]))
            else:
                sel = '・'.join(T._mk_disp(o) for o in ois) + 'のいずれか'
            gen.append((bi, ois[0], '%s条件で%sを選択した場合は、%sを入力する必要はない。'
                        % (gs[bi], sel, '、'.join('%s条件' % gs[t] for t in tg))))
    keyed = []
    for t in P['notes']:
        t2 = re.sub(r'^\d+\.\s*', '', t)
        sp = T._split_note(t2)
        src = gs.index('G%d' % sp[0]) if sp and ('G%d' % sp[0]) in gs else 999
        keyed.append((src, 0, t2))
    keyed += gen
    notes = [t for _a, _b, t in sorted(keyed, key=lambda x: (x[0], x[1]))]
    h = max((len(c['opts']) for c in cols), default=0)
    rows = [['施工区分/入力条件'] + gs,
            ['規格名計上'] + ['○' if c['kikaku'] else '' for c in cols],
            ['各種(条件名)'] + [c['name'] for c in cols]]
    for r in range(h):
        row = ['']
        for c in cols:
            if r < len(c['opts']):
                o = c['opts'][r]
                row.append(o if o.startswith('(') else T._mk_disp(r) + o)
            else:
                row.append('')
        rows.append(row)
    rows += [[], ['(注)']] + [['', '%d. %s' % (k + 1, t)] for k, t in enumerate(notes)]
    rows += [[], ['(外部設計メモ)']]
    BugakariJSON.write_csv(rows, out_csv)
    return out_csv


def prepare(csv_path, out_dir, tag, src=None):
    """③の入口で呼ぶ。段が無ければ (csv_path, None)＝何もしない。
    段があれば段を戻した1枚の表を out_dir に書き (そのパス, 段の情報) を返す。
    src: 元のファイル（xlsx。太字を読むため）。無ければ csv_path を読む。"""
    path = src if (src and os.path.exists(src)) else csv_path
    if not has_dan(path):
        return csv_path, None
    P = parse_dan(path)
    if P is None:
        return csv_path, None
    os.makedirs(out_dir, exist_ok=True)
    flat = write_flat(P, os.path.join(out_dir, '_段を戻したG条件_%s.csv' % tag))
    for msg in P['msgs']:
        print('【段】' + msg)
    if P['dans']:
        print('【段】%s: 段付きG条件として読みました（分岐点=「%s」 段数=%d）'
              % (tag, P['cols'][P['bi']]['name'], len(P['dans'])))
    return flat, P


def summary(P):
    """画面表示用の1行。段が無ければ ''。"""
    if not P or not P['dans']:
        return ''
    return '段あり形式として読みました（分岐点=「%s」／%d段）' % (
        P['cols'][P['bi']]['name'], len(P['dans']))


# ---------------------------------------------------------------- TCの後処理
def fix_tc(P, tc_csv, csv20=None):
    """段の約束に反する行を直し、段ごとの補完と段の確認観点を足す。戻り: (直した, 外した)"""
    if not P or not P['dans']:
        return 0, 0
    rows = T._read_csv_any(tc_csv)
    hdr = rows[0]
    bname = P['cols'][P['bi']]['name']
    col_by_name = {c['name']: c for c in P['cols']}

    def hidx(nm):
        for i, h in enumerate(hdr):
            if re.sub(r'\(固定\)$', '', h) == nm:
                return i
        return None
    bx = hidx(bname)
    kan = next((i for i, h in enumerate(hdr) if h == '選択肢の適切さ確認'), None)
    if bx is None:
        P['msgs'].append('WARN: TCに分岐点の列「%s」が無いため、段の絞り込みを掛けられません' % bname)
        print('【段】' + P['msgs'][-1])
        return 0, 0
    cmap = {c['name']: hidx(c['name']) for c in P['cols']}
    blabel = {}
    for r in rows[1:]:
        blabel.setdefault(_key(r[bx]), r[bx])

    def branch_label(b):
        return blabel.get(b) or next(o for o in P['cols'][P['bi']]['opts'] if _key(o) == b)

    def dan_of(v):
        k = _key(v)
        for di, d in enumerate(P['dans']):
            if k in d['branch']:
                return di
        return None

    def violations(r, di):
        bad = []
        for nm, al in P['dans'][di]['allowed'].items():
            ci = cmap.get(nm)
            if ci is None or al is None:
                continue
            v = r[ci].strip()
            if not v or v == '-' or v == '任意':
                continue
            kv = _key(v)
            full = [_key(o) for o in col_by_name[nm]['opts']]
            hit = [a for a in full if a and (a == kv or a in kv)]
            if not hit:
                continue          # G条件の選択肢に無い値（固定列等）は判定しない
            if not any(a in al for a in hit):
                bad.append(nm)
        return bad

    out, fixed, dropped = [hdr], 0, 0
    for r in rows[1:]:
        di = dan_of(r[bx])
        if di is None or not violations(r, di):
            out.append(r)
            continue
        alt = None
        for dj, d in enumerate(P['dans']):
            if dj == di:
                continue
            r2 = list(r)
            r2[bx] = branch_label(d['branch'][0])
            for nm, al in d['allowed'].items():      # その段で聞かれない列は「-」
                ci = cmap.get(nm)
                if ci is not None and al is None:
                    r2[ci] = '-'
            if not violations(r2, dj):
                alt = r2
                break
        if alt:
            if kan is not None:   # 観点の分岐点の値も移した先に合わせる
                alt[kan] = alt[kan].replace('「%s」' % r[bx], '「%s」' % alt[bx])
            out.append(alt)
            fixed += 1
        else:
            dropped += 1

    # --- 段ごとの補完: 新しい選択肢を段の中で1行ずつ確認する／各段に最低1行 ---
    old_opts = {}
    if csv20:
        g20 = T.read_gjoken(csv20)
        old_opts = {c['name']: {_key(o) for o in c['opts']} for c in g20['cols']}
    # 各段に最低1行（B案）: 分岐点そのものが改定で新しく入ったときだけ（設計書 §12）
    branch_is_new = (bname not in old_opts) if csv20 else True

    def has(di, nm, k):
        ci = cmap.get(nm)
        return any(dan_of(r[bx]) == di and (_key(r[ci]) == k or k in _key(r[ci]))
                   for r in out[1:])

    def template(di):
        for r in out[1:]:
            if dan_of(r[bx]) == di:
                return list(r)
        if len(out) < 2:
            return None
        r2 = list(out[1])
        d = P['dans'][di]
        r2[bx] = branch_label(d['branch'][0])
        sdi = dan_of(out[1][bx])
        for nm, al in d['allowed'].items():
            ci = cmap.get(nm)
            if ci is None:
                continue
            if al is None:
                r2[ci] = '-'
                continue
            full = col_by_name[nm]
            numeric = bool(full['opts']) and full['opts'][0].startswith('(')
            if (r2[ci] in ('-', '') and sdi is not None
                    and P['dans'][sdi]['allowed'].get(nm, 1) is None):
                # ひな形の段では聞かれなかった列 → この段の既定値
                r2[ci] = '任意' if numeric else next(o for o in full['opts'] if _key(o) == al[0])
            elif not numeric and al and _key(r2[ci]) not in al and r2[ci] != '任意':
                r2[ci] = next(o for o in full['opts'] if _key(o) == al[0])
        return r2

    dc = next((i for i, h in enumerate(hdr) if h == '代価表行と数量(数率)'), None)
    added = 0
    for di, d in enumerate(P['dans']):
        need = []
        if branch_is_new and not any(dan_of(r[bx]) == di for r in out[1:]):
            need.append((None, None))
        for nm, al in d['allowed'].items():
            if not al or cmap.get(nm) is None:
                continue
            full = col_by_name[nm]
            if full['opts'] and full['opts'][0].startswith('('):
                continue          # 実数入力
            for o in full['opts']:
                k = _key(o)
                if k in al and k not in old_opts.get(nm, set()) and not has(di, nm, k):
                    need.append((nm, o))
        for nm, o in need:
            r2 = template(di)
            if r2 is None:
                continue
            if nm:
                r2[cmap[nm]] = o
            r2[1] = '差分'
            if dc is not None:
                r2[dc] = '〃'
            if kan is not None:
                r2[kan] = ('段%d（%s=「%s」）\n' % (di + 1, bname, r2[bx]) +
                           ('%s(選択肢追加)\n・「%s」を選択した場合の計上がGaia条件と一致していること'
                            % (nm, o) if nm else
                            '・この段の既定の選択で計上がGaia条件と一致していること'))
            out.append(r2)
            added += 1
    # 重複行を除く（テストID・区分・観点以外が同じ）
    cond = [i for i, h in enumerate(hdr) if i not in (0, 1) and h not in
            ('代価表行と数量(数率)', '選択肢の適切さ確認', '規格名計上')]
    seen, dedup = set(), [hdr]
    for r in out[1:]:
        k = tuple(r[i] for i in cond)
        if k in seen:
            dropped += 1
            continue
        seen.add(k)
        dedup.append(r)
    # 段の確認観点（各段の最初の行に1回）
    if kan is not None:
        done = set()
        for r in dedup[1:]:
            di = dan_of(r[bx])
            if di is None or di in done:
                continue
            done.add(di)
            lines = []
            for nm, al in P['dans'][di]['allowed'].items():
                full = col_by_name[nm]
                if al is None:
                    lines.append('・「%s」は表示されないこと' % nm)
                elif full['opts'] and len(al) < len(full['opts']):
                    lab = [o for o in full['opts'] if _key(o) in al]
                    lines.append('・「%s」の選択肢は「%s」だけが表示されること'
                                 % (nm, '」「'.join(lab)))
            if lines:
                hl = '段%d（%s=「%s」）' % (di + 1, bname, r[bx])
                if r[kan].startswith(hl):
                    r[kan] = r[kan] + '\n' + '\n'.join(lines)
                else:
                    r[kan] = (r[kan] + '\n' if r[kan] else '') + hl + '\n' + '\n'.join(lines)
    for k, r in enumerate(dedup[1:], 1):
        r[0] = 'TC-%03d' % k
    BugakariJSON.write_csv(dedup, tc_csv)
    print('【段】TC後処理: 段の約束に反する行 → 段を移して直した %d件 / 外した %d件 / '
          '段ごとの補完 %d行' % (fixed, dropped, added))
    return fixed, dropped
