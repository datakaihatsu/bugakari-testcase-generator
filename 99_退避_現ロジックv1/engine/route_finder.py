# -*- coding: utf-8 -*-
"""
route_finder: 歩掛JSONのフローグラフ上で「ある質問(Sitsumon)へ到達する最小限の選択」を逆算する探査ツール。

用途:
  ある運転費/子代価などが既定経路では計上されない(別ルートでのみ到達)とき、
  そのSitsumonを計上させるために必要な分岐選択(SitsumonNo→RowID)を機械的に特定する。
  例) #44 ﾌﾞﾛｯｸ転置【潜水士船】: 潜水士船(運転費No32)は既定(陸上=ｸﾛｰﾗｸﾚｰﾝ)では計上されず、
      No30「労務編成」=「玉掛・玉外片方水中」を選ぶと到達する。

アルゴリズム:
  1. FlowItems から box->子box の有向グラフを構築。
  2. 目標Sitsumonを持つbox(複数可)へ「到達可能なbox集合 reach」を逆グラフBFSで求める。
  3. 既定walkを実行 → 経路上の分岐(FlowKind=2)で、現在選んでいる行が reach 側でない
     ものがあれば、reach側の選択可能行へ強制(vary_selections)して再walk。
  4. 目標Sitsumonが visited に入るまで反復。各反復で1分岐ずつ寄せる(最小化志向)。

戻り値: dict[SitsumonNo -> RowID]  (これを FlowWalker(vary_selections=...) に渡すと到達する)
        到達不能なら None。
"""
import collections


def _build_reach(fis, target_boxes):
    """target_boxes のいずれかに到達できる box の集合を返す。"""
    children = {}
    for f in fis:
        children[f.get('BoxNo')] = [
            b for b in (f.get('CallBox') or []) if isinstance(b, int) and b >= 0
        ]
    rev = collections.defaultdict(list)
    for b, cs in children.items():
        for c in cs:
            rev[c].append(b)
    reach = set(target_boxes)
    st = list(target_boxes)
    while st:
        x = st.pop()
        for p in rev[x]:
            if p not in reach:
                reach.add(p)
                st.append(p)
    return reach


def _sel_rows(data, sno):
    """選択可能行 [(RowID, 代表ラベル), ...] を CallBoxインデックス順(=Visible & not IsFixed)で返す。"""
    for ss in data.get('Sitsumon019', []):
        if ss['SitsumonNo'] == sno:
            cells = {}
            for c in ss.get('SitTabCells', []):
                cells.setdefault(c['RowID'], []).append(c.get('Value'))
            out = []
            for rr in ss['SitRows']:
                if rr.get('Visible', True) and not rr.get('IsFixed', False):
                    t = [str(x) for x in cells.get(rr['RowID'], [])
                         if x and any(ord(ch) >= 128 for ch in str(x))]
                    out.append((rr['RowID'], (t[:1] or [''])[0]))
            return out
    return []


def boxes_of_sitsumon(fis, sno):
    return [f.get('BoxNo') for f in fis if f.get('SitsumonNo') == sno]


def find_route_to_sitsumon(bj, target_sitsumon, max_iter=30):
    """
    target_sitsumon へ到達する最小限の強制選択 dict[SitsumonNo->RowID] を返す。
    既定で既に到達するなら {} を返す。到達不能なら None。

    bj: BugakariJSON
    """
    from flow_walker import FlowWalker
    data = bj.data
    fis = data.get('FlowItems', [])
    tboxes = boxes_of_sitsumon(fis, target_sitsumon)
    if not tboxes:
        return None
    reach = _build_reach(fis, tboxes)

    forced = {}
    for _ in range(max_iter):
        r = FlowWalker(bj, vary_selections=dict(forced)).walk()
        if target_sitsumon in r['visited_sitsumons']:
            return forced
        visited = set(r['visited_sitsumons'])
        sel = r.get('sit_selections', {})
        changed = False
        for f in fis:
            if f.get('FlowKind') != 2:
                continue
            sno = f.get('SitsumonNo')
            if sno not in visited or sno in forced:
                continue
            cb = f.get('CallBox') or []
            good = [i for i, b in enumerate(cb) if isinstance(b, int) and b in reach]
            if not good:
                continue
            rows = _sel_rows(data, sno)
            cur = sel.get(sno)
            cur_idx = [i for i, (rid, _) in enumerate(rows) if rid == cur]
            if cur_idx and cur_idx[0] in good:
                continue  # 既に到達側を選んでいる
            ridtxt = rows[good[0]] if good[0] < len(rows) else (None, '?')
            if ridtxt[0] is not None:
                forced[sno] = ridtxt[0]
                changed = True
                break
        if not changed:
            return None
    return None


def describe_route(bj, forced):
    """forced(dict) を人間可読な [(SitsumonNo, Mesho, RowID, ラベル), ...] にする。"""
    data = bj.data
    nm = {s['SitsumonNo']: s.get('Mesho') for s in data['SitsumonItem']}
    out = []
    for sno, rid in forced.items():
        rows = dict(_sel_rows(data, sno))
        out.append((sno, nm.get(sno), rid, rows.get(rid, '?')))
    return out


if __name__ == '__main__':
    import sys, glob, os
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from bugakari_json import BugakariJSON
    # 使い方: python route_finder.py <工種ディレクトリのglob> <目標SitsumonNo>
    pat = sys.argv[1] if len(sys.argv) > 1 else '工種別/44_*'
    target = int(sys.argv[2]) if len(sys.argv) > 2 else 32
    D = glob.glob(pat + '/')[0]
    new = sorted(glob.glob(D + 'input/*.json'))[-1]
    bj = BugakariJSON(new)
    forced = find_route_to_sitsumon(bj, target)
    if forced is None:
        print('到達不能: SitNo%d' % target)
    elif not forced:
        print('既定経路で到達: SitNo%d (強制不要)' % target)
    else:
        print('SitNo%d への到達に必要な選択:' % target)
        for sno, mesho, rid, label in describe_route(bj, forced):
            print('  No%s「%s」= 行%s「%s」' % (sno, mesho, rid, label))
