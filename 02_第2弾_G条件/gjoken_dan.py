"""
① G条件の段表記（(注)の段表記・2026-09-29 FB → 2026-10-20 本体実装）。

段ありモードのときだけ gen_gjoken.build_g_ex から呼ばれる。段なしモードの出力には一切関与しない。

判定（段にする条件・設計書 §6）:
  - 列 C に「同じ名前の別の質問」が複数あり（同名列の統合で1列になったもの）、
  - その入れ替わりを、上流の人が選ぶ列 X の選択肢が決めている
    （X の選択 → C で最初に聞かれる質問No が関数になり、2種以上）、
  - かつ、段に分けたとき C の選択肢（聞かれる/聞かれない を含む）が段ごとに実際に違う
  とき、X を分岐点とし、X の選択肢ごとに段を作る。分岐点が複数あれば最も上流の1つだけ（入れ子は対象外）。

段の中身:
  - 列（G番号・見出し）は全段共通。選択肢番号は通し番号（表の並び＝段なしと同じ番号）。
  - 段 r の各列 = 分岐点=r の歩行で到達した質問の選択肢。
    到達しない列は、①の(注)と同じ根拠（分岐点=r で閉じる）があるときだけ「－」、
    根拠が無いとき（走査で届かなかっただけ）は全選択肢を残す。
  - 段2以降で段1と同じ列は空欄（＝段1と共通）。
  - 中身が同じ段は1つにまとめる（分岐点の選択肢を並べる）。
  - 分岐点を発生源とする(注)は「－」で表したので消す。残りの(注)は段なしと同じ書き方。
"""
from gjoken_reach import selectable_rows


def _g_options(bj, sn):
    import gen_gjoken
    return gen_gjoken._g_options(bj, sn)


def _first_hit(vis, sits):
    for sn in vis.order:
        if sn in sits:
            return sn
    return None


def _find_branch(bj, cols, walks):
    """[(xi, ci)] 分岐点 xi が同名別質問の列 ci の入れ替わりを決めている組。"""
    found = []
    for ci, c in enumerate(cols):
        if len(c['sits']) < 2:
            continue
        csits = set(c['sits'])
        for xi in range(ci):
            x = cols[xi]
            if x['kind'] != 19:
                continue
            xsits = set(x['sits'])
            mp, ok = {}, True
            for _sel, vis in walks:
                xs = _first_hit(vis, xsits)
                cs = _first_hit(vis, csits)
                if xs is None or cs is None:
                    continue
                rid = vis.ss.get(xs)
                if rid not in selectable_rows(bj, xs):
                    continue
                if mp.setdefault((xs, rid), cs) != cs:
                    ok = False
                    break
            if ok and len(set(mp.values())) >= 2:
                found.append((xi, ci))
                break
    return found


def build_dan(bj, g_list, reach):
    """段の構成を返す。段の対象外なら (None, 理由)。

    戻り: ({'xi': 分岐点の列index, 'dans': [[列ごとの選択肢ラベル list | None(－)], ...],
            'raw_notes': 分岐点以外を発生源とする注 [(x, rid, y)]}, '')
    """
    cols = reach['cols']
    walks = reach.get('walk_list') or []
    found = _find_branch(bj, cols, walks)
    if not found:
        return None, '同じ名前の別の質問が、分岐点の選択で入れ替わる列がありません'
    xi = min(f[0] for f in found)
    split_cols = [ci for x, ci in found if x == xi]
    xsits = set(cols[xi]['sits'])
    xs0 = None
    for _sel, vis in walks:              # 分岐点の「人が選ぶ」質問 = 最初に到達するもの
        xs0 = _first_hit(vis, xsits)
        if xs0:
            break
    xlabel = {rid: lbl for rid, lbl, _raw in _g_options(bj, xs0)}
    closed = {(rid, yi) for (a, rid, yi) in reach['notes'] if a == xi}

    dans = []
    for rid in selectable_rows(bj, xs0):
        per = []
        for ci, c in enumerate(cols):
            if ci == xi:
                per.append([xlabel.get(rid, str(rid))])
                continue
            g = g_list[ci]
            reached = []
            for _sel, vis in walks:
                if vis.ss.get(xs0) != rid:
                    continue
                for sn in vis.order:
                    if sn in c['sits'] and sn not in reached:
                        reached.append(sn)
            if not reached:
                # 「－」は①の(注)と同じ根拠があるときだけ。根拠なし → 絞り込まない
                per.append(None if (rid, ci) in closed else list(g['opt_labels']))
                continue
            if g['numeric']:
                per.append(list(g['opt_labels']))
                continue
            labels = set()
            for sn in reached:
                labels.update(lbl for _r, lbl, _raw in _g_options(bj, sn))
            per.append([o for o in g['opt_labels'] if o in labels])   # 通し番号の順
        dans.append(per)

    # 中身（分岐点以外の列）が同じ段は1つにまとめる
    merged = []
    for per in dans:
        rest = [o for ci, o in enumerate(per) if ci != xi]
        for m in merged:
            if [o for ci, o in enumerate(m) if ci != xi] == rest:
                m[xi] = m[xi] + per[xi]
                break
        else:
            merged.append(per)
    if len(merged) < 2:
        return None, '分岐点の選択肢ごとの中身がすべて同じです'
    if not any(len({tuple(d[ci]) if d[ci] is not None else None for d in merged}) >= 2
               for ci in split_cols):
        return None, '同じ名前の別の質問はありますが、選択肢が段ごとに変わりません'
    order = g_list[xi]['opt_labels']
    for d in merged:
        d[xi] = sorted(d[xi], key=lambda o: order.index(o) if o in order else 10 ** 6)
    raw_notes = [n for n in reach['notes'] if n[0] != xi]
    return {'xi': xi, 'dans': merged, 'raw_notes': raw_notes,
            'split_cols': split_cols}, ''


def render_rows(g_list, dan, mk):
    """段の行（CSVの行）と書式用の情報を返す。
    戻り: (rows, segments)  segments = [(段内の先頭行offset, 行数), ...]（rows 内の位置）"""
    xi, dans = dan['xi'], dan['dans']

    def cells(ci, labels):
        g = g_list[ci]
        if g['numeric']:
            return list(g['opt_raws'])
        out = []
        for lbl in labels:
            if lbl not in g['opt_labels']:
                continue
            k = g['opt_labels'].index(lbl)
            out.append(mk(k) + g['opt_raws'][k])
        return out

    rows, segments = [], []
    for d, per in enumerate(dans):
        block = []
        for ci, o in enumerate(per):
            if d > 0 and ci != xi and o == dans[0][ci]:
                block.append([])            # 段1と共通
            elif o is None:
                block.append(['－'])         # この段では聞かれない
            else:
                block.append(cells(ci, o))
        h = max(1, max(len(b) for b in block))
        segments.append((len(rows), h))
        for r in range(h):
            rows.append(['段%d' % (d + 1) if r == 0 else ''] +
                        [b[r] if r < len(b) else '' for b in block])
    return rows, segments
