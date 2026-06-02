"""
③ 列形式テストケースCSV生成 (絞り込みアプローチ対応)

【機能】
- 列順: フロー登場順 (baseline_walker の初回出現順)
- 強制行ID: step2.0 の「強制行ID」 列を読み、fix軸の指定行で固定 (vary到達経路)
- TC walker: 強制行+vary を flow_walker に渡し、訪問された Sit のみ列に残す
- display_col 改善 (I): unique 値が多い列を優先
- 任意入力表記 (B): SitsumonKind=17 は「任意」
- 期待値の自然言語化 (F): 任意入力軸を含む TC で値=0 のとき「計算結果が正しいか」
- テスト区分判定 (G):
   - 追加 Row より上の既存 Row は除外 (オーバーテスト防止)
   - 追加 Row を選ぶ TC = 差分、それ以外 = 回帰
   - 業務ルール vary 軸の「状態戻し回帰TC」 を1件追加

【入力】
- step2.0_テスト計画.csv
- 新JSON (および旧JSON: G 判定用)

【出力】
- step3.0_テストケース.csv
"""

import sys
import os
import csv
import itertools

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'engine'))
from bugakari_json import BugakariJSON, KeisanHyo, ExternalReferenceError, ExpressionError
from flow_walker import FlowWalker


S_VARS = ['S1', 'S2', 'S3', 'S4', 'S5']


class ColumnTCGenerator:

    def __init__(self, plan_csv_path, new_json_path, old_json_path=None):
        self.new_json = BugakariJSON(new_json_path)
        self.old_json = BugakariJSON(old_json_path) if old_json_path else None
        self.plan = self._load_plan(plan_csv_path)
        self._added_row_index_by_sit = self._detect_added_rows() if self.old_json else {}

    def _detect_added_rows(self):
        result = {}
        for s_new in self.new_json.data.get('Sitsumon019', []):
            sno = s_new.get('SitsumonNo')
            new_selectable = [
                r['RowID'] for r in s_new.get('SitRows', [])
                if r.get('Visible', True) and not r.get('IsFixed', False)
            ]
            old_s = self.old_json.sitsumon019_by_no.get(sno)
            if not old_s:
                if new_selectable:
                    result[sno] = 0
                continue
            old_selectable = set(
                r['RowID'] for r in old_s.get('SitRows', [])
                if r.get('Visible', True) and not r.get('IsFixed', False)
            )
            for idx, rid in enumerate(new_selectable):
                if rid not in old_selectable:
                    result[sno] = idx
                    break
        return result

    def _load_plan(self, path):
        with open(path, encoding='cp932', newline='') as f:
            return list(csv.DictReader(f))

    def _get_axis_rows(self, sitsumon_no):
        sit019 = self.new_json.sitsumon019_by_no.get(sitsumon_no)
        if sit019 is None:
            return self._handle_non_019(sitsumon_no)
        cells = {
            (c['RowID'], c['ColID']): c.get('Value', '')
            for c in sit019.get('SitTabCells', [])
        }
        cols = sit019.get('SitCols', [])
        var_cols = [c for c in cols if c.get('VarName')]
        sit_rows = sit019.get('SitRows', [])
        selectable_row_ids = [
            r['RowID'] for r in sit_rows
            if r.get('Visible', True) and not r.get('IsFixed', False)
        ]
        # D 改善: VarName 持ち列も表示候補に入れる (規格コード等が業務的に重要)
        #   I (unique_count 優先) で適切な列が選ばれる
        disp_col_candidates = [
            c for c in cols
            if c.get('Visible', True)
        ]
        disp_col = None
        best_score = (-1, -1, -1)
        for c in disp_col_candidates:
            col_id = c.get('ColID')
            values = []
            for rid in selectable_row_ids:
                v = cells.get((rid, col_id))
                if v and str(v).strip():
                    values.append(str(v).strip())
            text_count = len(values)
            unique_count = len(set(values))
            # D 改善: 同点なら VarName 持ち列 (規格コード等) を優先
            has_varname = 1 if c.get('VarName') else 0
            score = (unique_count, has_varname, text_count)
            if score > best_score:
                best_score = score
                disp_col = col_id
        result = []
        for sr in sit_rows:
            row_id = sr.get('RowID')
            if not sr.get('Visible', True) or sr.get('IsFixed', False):
                continue
            display = cells.get((row_id, disp_col), '').replace('\r\n', ' ').strip() if disp_col else f'Row{row_id}'
            var_settings = {}
            for vc in var_cols:
                val = cells.get((row_id, vc['ColID']))
                if val is not None and val != '':
                    var_settings[vc['VarName']] = val
            result.append({
                'row_id': row_id,
                'display': display or f'Row{row_id}',
                'var_settings': var_settings,
            })
        return result

    def _handle_non_019(self, sitsumon_no):
        for s017 in self.new_json.data.get('Sitsumon017', []):
            if s017.get('SitsumonNo') == sitsumon_no:
                default = s017.get('DefaultValue', 0)
                vname = s017.get('VarName', '')
                return [{
                    'row_id': 0,
                    'display': '任意',
                    'var_settings': {vname: default} if vname else {},
                }]
        return [{'row_id': 0, 'display': '(値なし)', 'var_settings': {}}]

    def _get_default_row(self, sitsumon_no, rows):
        for tab in self.new_json.data.get('SitTab', []):
            if tab.get('SitsumonNo') == sitsumon_no:
                default_id = tab.get('DefaultRowID')
                if default_id:
                    for r in rows:
                        if r['row_id'] == default_id:
                            return r
                break
        return rows[0] if rows else None

    def _get_row_by_id(self, rows, row_id):
        for r in rows:
            if r['row_id'] == row_id:
                return r
        return None

    def _build_headers(self, axes_sorted):
        cols = ['テストID', 'テスト区分']
        cols += [ax['列ラベル'] for ax in axes_sorted]
        s_present = [v for v in S_VARS if v in self.new_json.keisan_by_varname]
        cols += [f'期待:{v}' for v in s_present]
        cols += ['選択肢の適切さ確認']
        return cols, s_present

    def generate(self):
        _baseline_for_order = FlowWalker(self.new_json)
        _baseline_result_for_order = _baseline_for_order.walk()
        _visit_seq = _baseline_result_for_order.get('visited_sitsumons', [])
        _visit_order = {}
        for idx, sn in enumerate(_visit_seq):
            if sn not in _visit_order:
                _visit_order[sn] = idx

        def _axis_order_key(p):
            return _visit_order.get(int(p['SitsumonNo']), 10**9 + int(p['SitsumonNo']))
        axes_sorted = sorted(self.plan, key=_axis_order_key)

        vary_axes = [ax for ax in self.plan if ax['種別'] == 'vary']
        fix_or_auto_axes = [ax for ax in self.plan if ax['種別'] in ('fix', 'auto')]

        # 強制行ID
        forced_rows = {}
        for ax in self.plan:
            forced = ax.get('強制行ID', '')
            if forced:
                try:
                    forced_rows[int(ax['SitsumonNo'])] = int(forced)
                except ValueError:
                    pass
        if forced_rows:
            print(f'  [強制行] {len(forced_rows)} 件: '
                  + ', '.join(f'Sit{k}=R{v}' for k, v in forced_rows.items()))

        # vary 軸列挙 + G フィルタ (追加 Row より上の既存除外)
        vary_row_lists = []
        vary_added_rows = {}
        for ax in vary_axes:
            sit_no = int(ax['SitsumonNo'])
            rows = self._get_axis_rows(sit_no)
            reason = ax.get('変更理由', '')
            is_business_rule = '業務ルール' in reason
            added_idx = self._added_row_index_by_sit.get(sit_no)
            if is_business_rule or added_idx is None:
                filtered = rows
                added_set = set()
            else:
                filtered = rows[added_idx:]
                old_s = self.old_json.sitsumon019_by_no.get(sit_no) if self.old_json else None
                old_set = set(
                    r['RowID'] for r in (old_s.get('SitRows', []) if old_s else [])
                    if r.get('Visible', True) and not r.get('IsFixed', False)
                )
                added_set = set(r['row_id'] for r in filtered if r['row_id'] not in old_set)
            vary_row_lists.append((ax, filtered))
            vary_added_rows[ax['軸ID']] = added_set

        # fix/auto
        fix_chosen = {}
        for ax in fix_or_auto_axes:
            sit_no = int(ax['SitsumonNo'])
            rows = self._get_axis_rows(sit_no)
            if sit_no in forced_rows:
                forced_row = self._get_row_by_id(rows, forced_rows[sit_no])
                fix_chosen[ax['軸ID']] = forced_row or self._get_default_row(sit_no, rows)
            else:
                fix_chosen[ax['軸ID']] = self._get_default_row(sit_no, rows)

        # cartesian
        if vary_row_lists:
            combos = list(itertools.product(*[rs for _, rs in vary_row_lists]))
        else:
            combos = [tuple()]

        # G: 業務ルール vary 軸の「状態戻し回帰 TC」 1件追加
        num_diff_tcs = len(combos)
        biz_rule_axis_idx = None
        for i, (ax, _) in enumerate(vary_row_lists):
            if '業務ルール' in ax.get('変更理由', ''):
                biz_rule_axis_idx = i
                break
        if biz_rule_axis_idx is not None and combos and len(combos) > 1:
            regression_combo = list(combos[-1])
            biz_ax, biz_rows = vary_row_lists[biz_rule_axis_idx]
            if biz_rows:
                regression_combo[biz_rule_axis_idx] = biz_rows[0]
            combos.append(tuple(regression_combo))

        # baseline scope
        baseline_walker = FlowWalker(self.new_json)
        baseline_result = baseline_walker.walk()
        baseline_scope = dict(baseline_walker.hyo._user_inputs)

        # 全 TC walker
        tc_walks = []
        all_visited = set()
        for combo in combos:
            tc_vary_sels = dict(forced_rows)
            for (ax, _), chosen_row in zip(vary_row_lists, combo):
                tc_vary_sels[int(ax['SitsumonNo'])] = chosen_row['row_id']
            tcw = FlowWalker(self.new_json, vary_selections=tc_vary_sels)
            tc_res = tcw.walk()
            visited = set(tc_res.get('visited_sitsumons', []))
            tc_walks.append({
                'visited': visited,
                'daika_flags': tc_res.get('daika_row_flags', {}),
                'tc_scope': dict(tcw.hyo._user_inputs),
            })
            all_visited.update(visited)

        # A: 列除外
        vary_sit_nos = {int(ax['SitsumonNo']) for ax in vary_axes}
        axes_displayed = [
            ax for ax in axes_sorted
            if int(ax['SitsumonNo']) in all_visited or int(ax['SitsumonNo']) in vary_sit_nos
        ]
        axes_excluded = [ax for ax in axes_sorted if ax not in axes_displayed]
        if axes_excluded:
            print(f'  [列除外] 到達せず: {len(axes_excluded)}件 -> '
                  + ', '.join(ax['軸名'] for ax in axes_excluded))

        headers, s_present = self._build_headers(axes_displayed)
        out_rows = []
        s_to_row = {f'S{i}': i for i in range(1, 10)}

        has_any_input_axis = any(
            'SitsumonKind=17' in ax.get('備考', '')
            for ax in axes_displayed
        )

        for tc_idx, (combo, tcw_data) in enumerate(zip(combos, tc_walks), 1):
            tc_id = f'TC-{tc_idx:03d}'

            chosen_rows_by_ax = {}
            for ax_id, row in fix_chosen.items():
                if row is None:
                    continue
                chosen_rows_by_ax[ax_id] = row
            for (ax, _), chosen_row in zip(vary_row_lists, combo):
                chosen_rows_by_ax[ax['軸ID']] = chosen_row

            hyo = KeisanHyo(self.new_json.data.get('KeisanItem', []))
            for vname, val in tcw_data['tc_scope'].items():
                try:
                    hyo.set_input(vname, val)
                except Exception:
                    pass

            daika_flags = tcw_data['daika_flags']

            # G: 差分/回帰判定
            if tc_idx > num_diff_tcs:
                # 業務ルール vary 軸 状態戻し回帰TC
                test_kind = '回帰'
            else:
                test_kind = '回帰'
                for (ax, _), chosen_row in zip(vary_row_lists, combo):
                    ax_id = ax['軸ID']
                    reason = ax.get('変更理由', '')
                    if chosen_row['row_id'] in vary_added_rows.get(ax_id, set()):
                        test_kind = '差分'
                        break
                    if '業務ルール' in reason or '新規追加質問' in reason:
                        test_kind = '差分'
                        break

            row_data = [tc_id, test_kind]
            for ax in axes_displayed:
                row = chosen_rows_by_ax.get(ax['軸ID'])
                row_data.append(row['display'] if row else '')

            for v in s_present:
                row_no = s_to_row.get(v)
                if row_no is not None:
                    flag = daika_flags.get((1, row_no))
                    if flag is not None and flag == 0:
                        row_data.append('')
                        continue
                try:
                    val = hyo.value(v)
                    fmt = self._fmt_decimal(val)
                    if has_any_input_axis and fmt == '0':
                        row_data.append('計算結果が正しいか')
                    else:
                        row_data.append(fmt)
                except ExternalReferenceError:
                    row_data.append('(外部単価依存)')
                except ExpressionError:
                    row_data.append('(評価不能)')
                except Exception as e:
                    row_data.append(f'(エラー: {e.__class__.__name__})')

            checks = []
            for (ax, _), chosen_row in zip(vary_row_lists, combo):
                reason = ax.get('変更理由', '')
                if '新規追加質問' in reason:
                    checks.append(f'{ax["軸名"]}(2026年新規追加)')
                    checks.append(f'・「{chosen_row["display"]}」と表示されているが、外部設計と正しいか')
                elif '選択肢追加' in reason:
                    checks.append(f'{ax["軸名"]}(選択肢追加)')
                    checks.append(f'・「{chosen_row["display"]}」と表示されているが、外部設計と正しいか')
                # H: 規格名計上の確認観点 (人間に判断を促す)
                checks.append(f'・{ax["軸名"]} に規格名計上選択がありますが、意図通りの場所に正しく計上されているか')
            if tc_idx > num_diff_tcs:
                if biz_rule_axis_idx is not None:
                    biz_ax = vary_row_lists[biz_rule_axis_idx][0]
                    checks.append(f'・{biz_ax["軸名"]} を切り替えた後、最初の選択肢に戻したとき、計算結果が初期状態と一致すること(状態戻し回帰)')
            row_data.append('\n'.join(checks))
            out_rows.append(row_data)

        return [headers] + out_rows

    @staticmethod
    def _fmt_decimal(v):
        s = str(v)
        if '.' in s:
            s = s.rstrip('0').rstrip('.')
            if not s:
                s = '0'
        return s


def run(plan_csv_path, new_json_path, output_path, old_json_path=None):
    gen = ColumnTCGenerator(plan_csv_path, new_json_path, old_json_path)
    rows = gen.generate()
    BugakariJSON.write_csv(rows, output_path)
    n = len(rows) - 1
    cols = len(rows[0]) if rows else 0
    print(f'テストケースCSV生成完了: {output_path}')
    print(f'  TC件数: {n} / 列数: {cols}')


if __name__ == '__main__':
    if len(sys.argv) < 4:
        print('Usage: python generate_csv.py <plan_csv> <new_json> <output_csv> [old_json]')
        sys.exit(1)
    old_json = sys.argv[4] if len(sys.argv) > 4 else None
    run(sys.argv[1], sys.argv[2], sys.argv[3], old_json)
