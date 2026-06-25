"""
絞り込みアプローチ PoC (Proof of Concept)

【目的】
現状の遡りアプローチでは「Sit 81 (K=800用) を vary軸として誤検出」していたが、
本来の修正対象は「Sit 82 (K=1300用) の Row 8 排ガス2014年規制」。
絞り込みアプローチで「Sit 82 に到達する組合せを自動探索」 できるか検証。

【ロジック】
1. step1.0_差分レポート.csv から「選択肢追加 された SitsumonNo」 を抽出
2. baseline flow_walker でその Sit が訪問されるかチェック
3. 訪問されない場合、前段 Sit (baseline 経路の Sit 群) の選択肢を変えながら探索
4. vary 対象 Sit に到達する組合せを発見 → TC 候補として出力

【使い方 (検証用・単独実行)】
  python generate_filtering_poc.py <step1.0_diff.csv> <new_json>
"""

import sys
import os
import csv
import itertools

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'engine'))
from bugakari_json import BugakariJSON
from flow_walker import FlowWalker


def parse_diff(diff_csv_path):
    """step1.0 から 選択肢追加 された SitsumonNo を抽出"""
    targets = []  # [(sitsumon_no, 名称), ...]
    with open(diff_csv_path, encoding='cp932', newline='') as f:
        for row in csv.DictReader(f):
            if row.get('カテゴリ') == '選択肢' and row.get('変更種別') == '追加':
                # ID 列に "質問No:82" のような表記
                rid = row.get('ID', '')
                if '質問No:' in rid:
                    sno = int(rid.split('質問No:')[1].strip())
                    targets.append((sno, row.get('名称', '')))
    return targets


def find_pre_axes(bj, target_sit_no):
    """baseline 経路で訪問される Sit (target より前) を列挙し、
    選択可能行が >=2 の Sit を probe 候補にする。
    """
    walker = FlowWalker(bj)
    result = walker.walk()
    visit_seq = result['visited_sitsumons']

    # baseline で target を訪問しない (今回のケース) → 全 visit を pre 候補
    candidates = []
    seen = set()
    for sn in visit_seq:
        if sn in seen:
            continue
        seen.add(sn)
        sit019 = bj.sitsumon019_by_no.get(sn)
        if not sit019:
            continue
        selectable = [
            r['RowID'] for r in sit019.get('SitRows', [])
            if r.get('Visible', True) and not r.get('IsFixed', False)
        ]
        if len(selectable) < 2:
            continue
        candidates.append((sn, selectable))
    return candidates


def search_paths_to_target_dfs(bj, target_sit_no):
    """Depth-first 探索: 1つずつ probe Sit を変えてみて target に到達するか確認。
    cartesian で組合せ爆発を起こさない。
    """
    pre_axes = find_pre_axes(bj, target_sit_no)
    print(f'  probe 候補総数: {len(pre_axes)}')

    found = []
    # 1個ずつ試す (single-probe)
    for probe_sn, rows in pre_axes:
        for row_id in rows:
            walker = FlowWalker(bj, vary_selections={probe_sn: row_id})
            result = walker.walk()
            visited = result['visited_sitsumons']
            if target_sit_no in visited:
                actual_sel = result['sit_selections'].get(target_sit_no)
                found.append({
                    'probe_sit': probe_sn,
                    'probe_row': row_id,
                    'target_actual_row': actual_sel,
                })
    return found


def search_paths_to_target(bj, target_sit_no, target_row_id=None, max_probe=4):
    """前段 Sit の選択肢 cartesian で target Sit に到達する組合せを探す。
    target_row_id が指定されれば、その Row が auto選択された組合せのみ採用。
    """
    pre_axes = find_pre_axes(bj, target_sit_no, max_probe=max_probe)
    print(f'  probe 候補 (直近 {max_probe} 個): {[(sn, len(rs)) for sn, rs in pre_axes]}')

    found = []
    if not pre_axes:
        return found

    # cartesian
    keys = [sn for sn, _ in pre_axes]
    rows_lists = [rs for _, rs in pre_axes]
    total = 1
    for rs in rows_lists:
        total *= len(rs)
    print(f'  探索組合せ数: {total}')

    for combo in itertools.product(*rows_lists):
        vary_sels = {keys[i]: combo[i] for i in range(len(keys))}
        walker = FlowWalker(bj, vary_selections=vary_sels)
        result = walker.walk()
        visited = result['visited_sitsumons']
        if target_sit_no not in visited:
            continue
        actual_sel = result['sit_selections'].get(target_sit_no)
        # target_row_id 指定があれば、その Row が選ばれた場合のみ採用
        if target_row_id is not None and actual_sel != target_row_id:
            continue
        found.append({
            'pre_selections': vary_sels,
            'target_actual_row': actual_sel,
        })
    return found, keys


def main():
    if len(sys.argv) < 3:
        print('Usage: python generate_filtering_poc.py <step1.0_diff.csv> <new_json>')
        sys.exit(1)
    diff_csv = sys.argv[1]
    new_json = sys.argv[2]

    bj = BugakariJSON(new_json)
    sit_by_no = {s['SitsumonNo']: s for s in bj.data.get('SitsumonItem', [])}

    targets = parse_diff(diff_csv)
    print(f'=== 差分から検出された 追加対象 Sit ===')
    for sno, name in targets:
        s = sit_by_no.get(sno, {})
        print(f'  Sit {sno}: {s.get("Mesho", "")} (差分名称: {name})')

    if not targets:
        print('追加 対象なし。終了。')
        return

    # baseline 走査で訪問される Sit
    bw = FlowWalker(bj)
    bw_result = bw.walk()
    baseline_visited = set(bw_result['visited_sitsumons'])
    print(f'\n=== baseline 走査での訪問 Sit 数: {len(baseline_visited)} ===')

    # 各 target について探索
    for sno, name in targets:
        s = sit_by_no.get(sno, {})
        print(f'\n========== Sit {sno} ({s.get("Mesho", "")}) ==========')
        in_baseline = sno in baseline_visited
        print(f'baseline で訪問? {in_baseline}')

        # 追加された Row を特定 (新JSONの SitRows と差分情報から推定)
        # 簡易: 新JSON の選択可能行のうち、(旧との比較は省略) 全部を試す
        sit019 = bj.sitsumon019_by_no.get(sno)
        selectable_rows = [
            r['RowID'] for r in sit019.get('SitRows', [])
            if r.get('Visible', True) and not r.get('IsFixed', False)
        ]
        print(f'選択可能 Row: {selectable_rows}')

        # 到達経路探索 (Depth-first single-probe)

        found = search_paths_to_target_dfs(bj, sno)
        print(f'到達経路発見: {len(found)} 件')

        if found:
            print(f'\n[探索結果 (single-probe)]')
            for fi in found:
                ps = sit_by_no.get(fi['probe_sit'], {})
                print(f'  Sit{fi["probe_sit"]} (Mesho={ps.get("Mesho", "")[:25]}) を R{fi["probe_row"]} に変えると  ->  Sit{sno} 訪問 (実選択 R{fi["target_actual_row"]})')


if __name__ == '__main__':
    main()
