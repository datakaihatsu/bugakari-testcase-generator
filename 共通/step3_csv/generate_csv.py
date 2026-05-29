"""
③ 列形式テストケースCSV生成 (新アーキテクチャ)
step2.0_テスト計画.csv + 新JSON → step3.0_テストケース.csv

【出力列】
  テストID, テスト区分,
  [軸1の列ラベル], [軸2の列ラベル], ...,   (軸はSitsumonNo昇順)
  期待:S1, 期待:S2, ..., 期待:S5,         (新JSONに存在するもののみ)
  選択肢の適切さ確認

【ロジック】
1. step2の各軸について、Sitsumon019から行を抽出
   - vary: 表示可能な全行 (見出し・固定除外)
   - fix/auto: SitTab.DefaultRowID または最初の行
2. vary軸の cartesian product でTC列挙
3. 各TCで KeisanHyo に各行の変数設定を投入し、S1〜S5 を評価
4. vary軸が「新規追加質問」なら「選択肢の適切さ確認」テンプレを生成
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

    def __init__(self, plan_csv_path, new_json_path):
        self.new_json = BugakariJSON(new_json_path)
        self.plan = self._load_plan(plan_csv_path)

    def _load_plan(self, path):
        with open(path, encoding='cp932', newline='') as f:
            return list(csv.DictReader(f))

    # ------------------------------------------------------------------
    # Sitsumon019 から「選択可能な行」を抽出
    # ------------------------------------------------------------------

    def _get_axis_rows(self, sitsumon_no):
        """戻り値: [{'row_id', 'display', 'var_settings'(dict)}]"""
        sit019 = self.new_json.sitsumon019_by_no.get(sitsumon_no)
        if sit019 is None:
            # Sitsumon017 (数値入力) など
            return self._handle_non_019(sitsumon_no)

        cells = {
            (c['RowID'], c['ColID']): c.get('Value', '')
            for c in sit019.get('SitTabCells', [])
        }
        cols = sit019.get('SitCols', [])
        # 変数設定列: VarName を持つ列 (ColKind=2)
        var_cols = [c for c in cols if c.get('VarName')]
        # 表示列の動的判定: VarName 持たず Visible なカラムから、
        # 選択可能行 (Visible=True, IsFixed=False) で値が入っているセル数が最大のものを採用
        sit_rows = sit019.get('SitRows', [])
        selectable_row_ids = [
            r['RowID'] for r in sit_rows
            if r.get('Visible', True) and not r.get('IsFixed', False)
        ]
        disp_col_candidates = [
            c for c in cols
            if c.get('Visible', True) and not c.get('VarName')
        ]
        disp_col = None
        max_text_count = -1
        for c in disp_col_candidates:
            col_id = c.get('ColID')
            text_count = sum(
                1 for rid in selectable_row_ids
                if cells.get((rid, col_id)) and str(cells.get((rid, col_id))).strip()
            )
            if text_count > max_text_count:
                max_text_count = text_count
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
        """Sitsumon017 (数値入力) 等の場合: 単一値 (デフォルト) を返す"""
        for s017 in self.new_json.data.get('Sitsumon017', []):
            if s017.get('SitsumonNo') == sitsumon_no:
                default = s017.get('DefaultValue', 0)
                vname = s017.get('VarName', '')
                tani = s017.get('TaniMesho', '')
                return [{
                    'row_id': 0,
                    'display': f'{default}{tani}',
                    'var_settings': {vname: default} if vname else {},
                }]
        return [{'row_id': 0, 'display': '(値なし)', 'var_settings': {}}]

    def _get_default_row(self, sitsumon_no, rows):
        """SitTab.DefaultRowID に従ったデフォルト行。無ければ最初の行"""
        for tab in self.new_json.data.get('SitTab', []):
            if tab.get('SitsumonNo') == sitsumon_no:
                default_id = tab.get('DefaultRowID')
                if default_id:
                    for r in rows:
                        if r['row_id'] == default_id:
                            return r
                break
        return rows[0] if rows else None

    # ------------------------------------------------------------------
    # 列ヘッダー構築
    # ------------------------------------------------------------------

    def _build_headers(self, axes_sorted):
        cols = ['テストID', 'テスト区分']
        cols += [ax['列ラベル'] for ax in axes_sorted]
        # S変数 (新JSON に存在するもののみ)
        s_present = [v for v in S_VARS if v in self.new_json.keisan_by_varname]
        cols += [f'期待:{v}' for v in s_present]
        cols += ['選択肢の適切さ確認']
        return cols, s_present

    # ------------------------------------------------------------------
    # メイン
    # ------------------------------------------------------------------

    def generate(self):
        axes_sorted = sorted(self.plan, key=lambda p: int(p['SitsumonNo']))

        vary_axes = [ax for ax in self.plan if ax['種別'] == 'vary']
        fix_or_auto_axes = [ax for ax in self.plan if ax['種別'] in ('fix', 'auto')]

        # vary 軸の選択肢全列挙
        vary_row_lists = []
        for ax in vary_axes:
            rows = self._get_axis_rows(int(ax['SitsumonNo']))
            vary_row_lists.append((ax, rows))

        # fix/auto 軸のデフォルト値
        fix_chosen = {}  # 軸ID → row
        for ax in fix_or_auto_axes:
            rows = self._get_axis_rows(int(ax['SitsumonNo']))
            fix_chosen[ax['軸ID']] = self._get_default_row(int(ax['SitsumonNo']), rows)

        # cartesian product (vary が無ければ 1TC)
        if vary_row_lists:
            combos = list(itertools.product(*[rs for _, rs in vary_row_lists]))
        else:
            combos = [tuple()]

        headers, s_present = self._build_headers(axes_sorted)
        out_rows = []

        # === Baseline scope: vary無しで全パスを通り、全変数のデフォルト値を確定 ===
        baseline_walker = FlowWalker(self.new_json)
        baseline_walker.walk()
        baseline_scope = dict(baseline_walker.hyo._user_inputs)

        # S 変数 → 代価表行番号 のマップ (S1→1, S2→2, ...)
        # 慣習: 代価表行N の数量変数 = S{N}。汎用に書くなら DaikaItemLine.BikoShikiLinkKeisanItemCD →
        #       KeisanItem.VarName を辿ってもよいが、現状は番号一致で十分。
        s_to_row = {f'S{i}': i for i in range(1, 10)}

        for tc_idx, combo in enumerate(combos, 1):
            tc_id = f'TC-{tc_idx:03d}'

            chosen_rows_by_ax = {}  # 軸ID → row (表示用)
            for ax_id, row in fix_chosen.items():
                if row is None:
                    continue
                chosen_rows_by_ax[ax_id] = row
            for (ax, _), chosen_row in zip(vary_row_lists, combo):
                chosen_rows_by_ax[ax['軸ID']] = chosen_row

            # === Baseline scope を起点に KeisanHyo を構築 ===
            hyo = KeisanHyo(self.new_json.data.get('KeisanItem', []))
            for vname, val in baseline_scope.items():
                try:
                    hyo.set_input(vname, val)
                except Exception:
                    pass

            # === vary軸の変数だけを上書き ===
            for (ax, _), chosen_row in zip(vary_row_lists, combo):
                for vname, val in chosen_row['var_settings'].items():
                    try:
                        hyo.set_input(vname, val)
                    except Exception:
                        pass

            # === TC専用 walker: vary軸選択で代価表行フラグを取得 ===
            #   行フラグ=0 の S* は代価表に表示されない → 空欄にする
            tc_vary_sels = {}
            for (ax, _), chosen_row in zip(vary_row_lists, combo):
                tc_vary_sels[int(ax['SitsumonNo'])] = chosen_row['row_id']
            tc_walker = FlowWalker(self.new_json, vary_selections=tc_vary_sels)
            tc_walk = tc_walker.walk()
            daika_flags = tc_walk.get('daika_row_flags', {})

            # 行データ
            row_data = [tc_id, '差分']
            for ax in axes_sorted:
                row = chosen_rows_by_ax.get(ax['軸ID'])
                row_data.append(row['display'] if row else '')

            # 期待値 S1..S5
            for v in s_present:
                # 代価表行フラグ=0 なら 該当 S* は代価表に出ない → 空欄
                row_no = s_to_row.get(v)
                if row_no is not None:
                    flag = daika_flags.get((1, row_no))  # sheet=1 を見る
                    if flag is not None and flag == 0:
                        row_data.append('')
                        continue
                try:
                    val = hyo.value(v)
                    row_data.append(self._fmt_decimal(val))
                except ExternalReferenceError:
                    row_data.append('（外部単価依存）')
                except ExpressionError:
                    row_data.append('（評価不能）')
                except Exception as e:
                    row_data.append(f'（エラー: {e.__class__.__name__}）')

            # 選択肢の適切さ確認
            checks = []
            for (ax, _), chosen_row in zip(vary_row_lists, combo):
                reason = ax.get('変更理由', '')
                if '新規追加質問' in reason:
                    checks.append(f'{ax["軸名"]}（2026年新規追加）')
                    checks.append(f'・「{chosen_row["display"]}」と表示されているが、外部設計と正しいか')
                elif '選択肢追加' in reason:
                    checks.append(f'{ax["軸名"]}（選択肢追加）')
                    checks.append(f'・「{chosen_row["display"]}」と表示されているが、外部設計と正しいか')
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


def run(plan_csv_path, new_json_path, output_path):
    gen = ColumnTCGenerator(plan_csv_path, new_json_path)
    rows = gen.generate()
    BugakariJSON.write_csv(rows, output_path)
    n = len(rows) - 1
    cols = len(rows[0]) if rows else 0
    print(f'テストケースCSV生成完了: {output_path}')
    print(f'  TC件数: {n} / 列数: {cols}')


if __name__ == '__main__':
    if len(sys.argv) < 4:
        print('Usage: python generate_csv.py <plan_csv> <new_json> <output_csv>')
        sys.exit(1)
    run(sys.argv[1], sys.argv[2], sys.argv[3])
