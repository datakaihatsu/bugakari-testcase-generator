"""
② テスト計画生成 (絞り込みアプローチ)

【設計思想】
v1 (旧案) の問題: 差分の名称マッチで vary 軸を探すため、同名の別 Sit を誤採用していた。
v2 (新案) の解: 差分の SitsumonNo を直接活用し、baseline で訪問されない vary 候補は
前段 Sit の選択肢を forward 試行して「到達経路」を自動探索する。

【入力】
- step1.0_差分レポート.csv (差分情報)
- 新JSON

【出力】
- step2.0_テスト計画.csv
  HEADER: 軸ID, 軸名, 種別, SitsumonNo, 列ラベル, 変更理由, 備考, 強制行ID
  ※ 「強制行ID」 列は新案で新規追加。絞り込みで決まった probe Sit の固定行を指定する。
"""

import sys
import os
import csv

try:
    import yaml
except ImportError:
    yaml = None

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'engine'))
from bugakari_json import BugakariJSON, KeisanHyo, ExternalReferenceError, ExpressionError
from flow_walker import FlowWalker

_YAML_PATH = os.path.join(os.path.dirname(__file__), '..', 'knowledge', 'global_rules.yaml')

HEADER = [
    '軸ID', '軸名', '種別', 'SitsumonNo', '列ラベル', '変更理由', '備考', '強制行ID',
]


def load_global_rules(path=_YAML_PATH):
    if yaml is None or not os.path.exists(path):
        return {}
    with open(path, encoding='utf-8') as f:
        return yaml.safe_load(f) or {}


class TestPlanGenerator:

    def __init__(self, diff_csv_path, new_json_path, old_json_path=None):
        self.new_json = BugakariJSON(new_json_path)
        self.old_json = BugakariJSON(old_json_path) if old_json_path else None
        self.diff_rows = self._load_diff(diff_csv_path)
        self._counter = 0
        self.global_rules = load_global_rules()
        self._baseline_visited = None
        self._baseline_visit_seq = None

    def _load_diff(self, path):
        with open(path, encoding='cp932', newline='') as f:
            return list(csv.DictReader(f))

    def _next_id(self):
        self._counter += 1
        return f'AX-{self._counter:03d}'

    # ------------------------------------------------------------------
    # 差分から vary 候補の SitsumonNo を抽出
    # ------------------------------------------------------------------

    def _vary_targets_from_diff(self):
        targets = {}
        for row in self.diff_rows:
            cat = row.get('カテゴリ')
            kind = row.get('変更種別')
            rid = row.get('ID', '')
            no = self._parse_sitsumon_no(rid)
            if no is None:
                continue
            if cat == '質問' and kind == '追加':
                targets.setdefault(no, []).append('新規追加質問')
            elif cat == '選択肢' and kind == '追加':
                targets.setdefault(no, []).append('選択肢追加')
            elif cat == '選択肢' and kind == '削除':
                targets.setdefault(no, []).append('選択肢削除')
        return targets

    @staticmethod
    def _parse_sitsumon_no(rid):
        if '質問No:' in rid:
            try:
                return int(rid.split('質問No:')[1].strip())
            except ValueError:
                pass
        return None

    # ------------------------------------------------------------------
    # UI 可視判定
    # ------------------------------------------------------------------

    def _is_ui_visible_axis(self, sit):
        kind = sit.get('SitsumonKind')
        if kind not in (17, 19):
            return False
        if sit.get('ShortCutSitsumonNo'):
            return False
        no = sit.get('SitsumonNo')
        mesho = sit.get('Mesho', '')
        if '=' in mesho:
            left = mesho.split('=', 1)[0]
            has_japanese_in_left = any(ord(c) >= 128 for c in left)
            if not has_japanese_in_left:
                return False
        sit019 = self.new_json.sitsumon019_by_no.get(no)
        if sit019 is not None:
            rows = sit019.get('SitTabRows', [])
            with_joken = sum(
                1 for r in rows
                if r.get('AutoSelectJoken')
                and (r['AutoSelectJoken'].get('VarName') or r['AutoSelectJoken'].get('Shiki'))
            )
            if len(rows) <= 4 and with_joken == 1:
                return False
            has_min_kigou = False
            joken_count = 0
            for r in rows:
                joken = r.get('AutoSelectJoken')
                if not joken:
                    continue
                if not (joken.get('VarName') or joken.get('Shiki')):
                    continue
                joken_count += 1
                mk = joken.get('MinKigou')
                mv = joken.get('MinValue')
                if mk and mv is not None:
                    has_min_kigou = True
                    break
            if joken_count > 0 and has_min_kigou:
                return False
        return True

    # ------------------------------------------------------------------
    # baseline 走査
    # ------------------------------------------------------------------

    def _baseline_walk(self):
        if self._baseline_visited is None:
            walker = FlowWalker(self.new_json)
            result = walker.walk()
            self._baseline_visit_seq = result.get('visited_sitsumons', [])
            self._baseline_visited = set(self._baseline_visit_seq)
            self._baseline_row_sources = result.get('row_sources', {})
        return self._baseline_visited

    # ------------------------------------------------------------------
    # 経路探索 (single-probe)
    # ------------------------------------------------------------------

    def _find_path_to_target(self, target_sit_no):
        self._baseline_walk()
        seen = set()
        for sn in self._baseline_visit_seq:
            if sn in seen:
                continue
            seen.add(sn)
            sit019 = self.new_json.sitsumon019_by_no.get(sn)
            if not sit019:
                continue
            selectable = [
                r['RowID'] for r in sit019.get('SitRows', [])
                if r.get('Visible', True) and not r.get('IsFixed', False)
            ]
            if len(selectable) < 2:
                continue
            for row_id in selectable:
                walker = FlowWalker(self.new_json, vary_selections={sn: row_id})
                result = walker.walk()
                visited = result.get('visited_sitsumons', [])
                if target_sit_no in visited:
                    return (sn, row_id)
        return None

    # ------------------------------------------------------------------
    # 軸列挙 (絞り込み版)
    # ------------------------------------------------------------------

    def _collect_axes(self):
        baseline_visited = self._baseline_walk()
        sit_by_no = {s['SitsumonNo']: s for s in self.new_json.data.get('SitsumonItem', [])}

        vary_diff = self._vary_targets_from_diff()

        path_fix = {}
        vary_axes = []
        excluded_targets = []
        for sit_no, reasons in vary_diff.items():
            s = sit_by_no.get(sit_no)
            if not s:
                continue
            if not self._is_ui_visible_axis(s):
                continue
            if sit_no in baseline_visited:
                vary_axes.append({'sit': s, 'reason': ' / '.join(dict.fromkeys(reasons))})
            else:
                path = self._find_path_to_target(sit_no)
                if path is None:
                    excluded_targets.append((sit_no, s.get('Mesho', '')))
                    continue
                probe_sn, probe_rid = path
                path_fix[probe_sn] = probe_rid
                vary_axes.append({'sit': s, 'reason': ' / '.join(dict.fromkeys(reasons))})

        result_axes = []
        vary_sit_nos = {ax['sit']['SitsumonNo'] for ax in vary_axes}
        for ax in vary_axes:
            result_axes.append({
                'sit': ax['sit'],
                'kind': 'vary',
                'reason': ax['reason'],
                'forced_row_id': '',
            })

        seen = set(vary_sit_nos)
        for sn in self._baseline_visit_seq:
            if sn in seen:
                continue
            seen.add(sn)
            s = sit_by_no.get(sn)
            if not s:
                continue
            if not self._is_ui_visible_axis(s):
                continue
            src = self._baseline_row_sources.get(sn)
            forced = path_fix.get(sn, '')
            if forced:
                kind = 'fix'
                reason = '絞り込みで強制(vary到達経路)'
            elif src == 'auto' and s.get('SitsumonExecuteKind') == 2:
                continue
            elif src == 'auto':
                kind = 'auto'
                reason = '自動選択(AutoSelectJoken)'
            else:
                kind = 'fix'
                reason = ''
            result_axes.append({
                'sit': s,
                'kind': kind,
                'reason': reason,
                'forced_row_id': str(forced) if forced else '',
            })

        for probe_sn, probe_rid in path_fix.items():
            if probe_sn in seen:
                continue
            s = sit_by_no.get(probe_sn)
            if not s or not self._is_ui_visible_axis(s):
                continue
            result_axes.append({
                'sit': s,
                'kind': 'fix',
                'reason': '絞り込みで強制(vary到達経路)',
                'forced_row_id': str(probe_rid),
            })

        self._apply_axis_behaviors(result_axes)
        self._excluded_targets = excluded_targets
        return result_axes

    def _apply_axis_behaviors(self, axes):
        behaviors = self.global_rules.get('axis_behaviors') or []
        if not behaviors:
            return
        has_keisan_change = any(
            r.get('カテゴリ') == '計算表' and r.get('変更種別') in ('変更', '追加', '削除')
            for r in self.diff_rows
        )
        for entry in behaviors:
            axis_name = entry.get('axis_name')
            condition = (entry.get('promote_to_vary_when') or {}).get('condition')
            if not axis_name:
                continue
            for ax in axes:
                if ax['sit'].get('Mesho') != axis_name:
                    continue
                if condition == 'change_affects_suryo_via_axis_switch' and has_keisan_change:
                    if ax['kind'] != 'vary':
                        ax['kind'] = 'vary'
                        ax['reason'] = f'業務ルール: {axis_name}の切替が代価表数量に影響'

    def generate(self):
        axes = self._collect_axes()

        order_map = {}
        for idx, sn in enumerate(self._baseline_visit_seq):
            if sn not in order_map:
                order_map[sn] = idx

        def sort_key(ax):
            sn = ax['sit'].get('SitsumonNo')
            return (order_map.get(sn, 10**9 + sn),)

        axes.sort(key=sort_key)

        plan = []
        for ax in axes:
            sit = ax['sit']
            no = sit.get('SitsumonNo')
            name = sit.get('Mesho', f'SitsumonNo:{no}')
            kind = ax['kind']
            reason = ax['reason']
            forced = ax['forced_row_id']
            label = f'{name}(固定)' if kind == 'auto' else name
            note = f'SitsumonKind={sit.get("SitsumonKind")}'
            plan.append([
                self._next_id(),
                name,
                kind,
                no,
                label,
                reason,
                note,
                forced,
            ])
        return plan


def run(diff_csv_path, new_json_path, output_path, old_json_path=None):
    gen = TestPlanGenerator(diff_csv_path, new_json_path, old_json_path)
    plan = gen.generate()
    BugakariJSON.write_csv([HEADER] + plan, output_path)
    vary_n = sum(1 for r in plan if r[2] == 'vary')
    auto_n = sum(1 for r in plan if r[2] == 'auto')
    fix_n = sum(1 for r in plan if r[2] == 'fix')
    forced_n = sum(1 for r in plan if r[7])
    print(f'テスト計画生成完了: {output_path}')
    print(f'  軸合計: {len(plan)}件 (vary={vary_n} / auto={auto_n} / fix={fix_n} / うち強制={forced_n})')
    for r in plan:
        if r[2] == 'vary':
            print(f'  [vary] {r[1]} ({r[5]})')
        elif r[7]:
            print(f'  [fix:強制 Row{r[7]}] {r[1]} ({r[5]})')
    excluded = getattr(gen, '_excluded_targets', [])
    if excluded:
        print(f'  到達経路なし(除外): {len(excluded)}件 → {", ".join(f"Sit{n}" for n, _ in excluded)}')


if __name__ == '__main__':
    if len(sys.argv) < 4:
        print('Usage: python generate_proposals.py <diff_csv> <new_json> <output_csv> [old_json]')
        sys.exit(1)
    old_json = sys.argv[4] if len(sys.argv) > 4 else None
    run(sys.argv[1], sys.argv[2], sys.argv[3], old_json)
