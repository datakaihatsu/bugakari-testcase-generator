"""
② テスト計画生成 (列形式CSV対応・新アーキテクチャ)
step1.0_差分レポート.csv + 新JSON → step2.0_テスト計画.csv

【役割】
新JSONを解析し、テストケースに必要な「軸」を特定して分類する。
軸 = 積算画面でユーザに見える質問(Sitsumon) または 自動選択される質問。

【分類ロジック】
- vary : 差分で「質問追加」「選択肢追加/削除」が検出された質問 → 値を変えてTCを生成
- auto : AutoSelectJokenが真になる行が確定する質問 → 値固定で列表示("(固定)")
- fix  : 上記以外でユーザに見える質問 → デフォルト値で固定

【出力列】
  軸ID       : AX-001, AX-002, ...
  軸名       : UI表示名 (SitsumonItem.Mesho)
  種別       : vary / auto / fix
  SitsumonNo : 該当する SitsumonItem.SitsumonNo
  列ラベル    : 出力CSV列名 (auto時は末尾に "(固定)")
  変更理由    : varyの根拠 (新規追加 / 選択肢追加 / 選択肢削除 / 計算式影響)
  備考       : 補足情報

使い方:
  python generate_proposals.py <diff_csv> <new_json> <output_csv> [old_json]
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

# 業務知識ルール (global_rules.yaml)
_YAML_PATH = os.path.join(os.path.dirname(__file__), '..', 'knowledge', 'global_rules.yaml')


def load_global_rules(path=_YAML_PATH):
    """global_rules.yaml を読み込む。yaml が無ければ空の dict を返す"""
    if yaml is None or not os.path.exists(path):
        return {}
    with open(path, encoding='utf-8') as f:
        return yaml.safe_load(f) or {}

HEADER = [
    '軸ID', '軸名', '種別', 'SitsumonNo', '列ラベル', '変更理由', '備考',
]


class TestPlanGenerator:

    def __init__(self, diff_csv_path, new_json_path, old_json_path=None):
        self.new_json = BugakariJSON(new_json_path)
        self.old_json = BugakariJSON(old_json_path) if old_json_path else None
        self.diff_rows = self._load_diff(diff_csv_path)
        self._counter = 0
        # 計算表評価用 (strict: 未確定変数は ExpressionError として扱う → auto誤判定を防ぐ)
        self.hyo = KeisanHyo(self.new_json.data.get('KeisanItem', []), strict_undefined=True)
        # 業務知識ルール
        self.global_rules = load_global_rules()

    # ------------------------------------------------------------------
    # 入力
    # ------------------------------------------------------------------

    def _load_diff(self, path):
        with open(path, encoding='cp932', newline='') as f:
            return list(csv.DictReader(f))

    def _next_id(self):
        self._counter += 1
        return f'AX-{self._counter:03d}'

    # ------------------------------------------------------------------
    # 差分解析: 変化のあった SitsumonNo を抽出
    # ------------------------------------------------------------------

    def _vary_sitsumon_nos(self):
        """vary対象の SitsumonNo 集合と、軸ごとの変更理由を返す"""
        reasons = {}  # SitsumonNo -> [reason1, reason2, ...]

        def add_reason(no, reason):
            if no is not None:
                reasons.setdefault(no, []).append(reason)

        for row in self.diff_rows:
            cat = row['カテゴリ']
            kind = row['変更種別']
            rid = row['ID']
            name = row['名称']

            if cat == '質問':
                no = self._parse_sitsumon_no(rid)
                if no is not None:
                    if kind == '追加':
                        add_reason(no, '新規追加質問')
                    elif kind == '変更':
                        # 値変更だけなら軸にしないが、後で再考の余地あり
                        pass
                    elif kind == '削除':
                        # 削除質問は出力に出さないので無視
                        pass

            elif cat == '選択肢':
                # 選択肢追加・削除があった質問は軸候補
                no = self._sitsumon_no_by_mesho(name)
                if no is not None:
                    if kind == '追加':
                        add_reason(no, '選択肢追加')
                    elif kind == '削除':
                        add_reason(no, '選択肢削除')

        return reasons

    def _parse_sitsumon_no(self, rid):
        if rid.startswith('質問No:'):
            try:
                return int(rid.replace('質問No:', ''))
            except ValueError:
                pass
        return None

    def _sitsumon_no_by_mesho(self, mesho):
        for s in self.new_json.data.get('SitsumonItem', []):
            if s.get('Mesho') == mesho:
                return s.get('SitsumonNo')
        return None

    # ------------------------------------------------------------------
    # UI見える質問の抽出
    # ------------------------------------------------------------------

    def _is_ui_visible_axis(self, sit):
        """この Sitsumon が「ユーザに見えるテスト軸」として扱えるかを判定。

        除外ロジック:
        1. SitsumonKind が 17(数値入力) / 19(選択式) 以外 (8=子質問, 14=サブルーチン, 105/113等=内部コマンド)
        2. Mesho が "Var=値" 形式の内部ラベル (`A=10`, `NFG1=2`, `0=代価表1枚目4行目` 等)
        3. SitTabRows が小さく(<=4) AutoSelectJoken付き行が1つだけ
           → 内部の自動分岐確定パターン
        """
        kind = sit.get('SitsumonKind')
        if kind not in (17, 19):
            return False
        no = sit.get('SitsumonNo')
        mesho = sit.get('Mesho', '')

        # 内部質問パターン: Mesho が `Var=値` 形式
        # 判定: '=' を含み、'=' より左側に日本語(Unicode 128+) がない → 技術的ラベル
        # 例: "A=10", "NFG1=2", "0=代価表1枚目4行目" → 内部
        # 例: "代価表当り単位の選択(標準=10m3)" → UI見える (左側に日本語あり)
        if '=' in mesho:
            left = mesho.split('=', 1)[0]
            has_japanese_in_left = any(ord(c) >= 128 for c in left)
            if not has_japanese_in_left:
                return False

        # 内部自動分岐パターン: SitTabRows<=4 で AutoSelectJoken付き行が1個だけ
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

        return True

    def _collect_ui_axes(self):
        """フロー走査で実際に到達される Sitsumon の中から、UI-visible 軸を列挙。

        フロー走査結果の row_sources も保持し、軸分類時に参照する:
        - 'auto': AutoSelectJoken が真で選ばれた → 完全自動
        - 'default', 'first': user 入力相当 (AutoSelect 評価不能 or joken無し)
        - 'vary': vary軸として指定 (走査時には None)
        """
        walker = FlowWalker(self.new_json)
        walk_result = walker.walk()
        visited_set = set(walk_result['visited_sitsumons'])
        self._flow_row_sources = walk_result.get('row_sources', {})

        sit_by_no = {s['SitsumonNo']: s for s in self.new_json.data.get('SitsumonItem', [])}
        result = []
        for sit_no in visited_set:
            s = sit_by_no.get(sit_no)
            if s is None:
                continue
            if not self._is_ui_visible_axis(s):
                continue
            # フロー走査で「auto」と判定 かつ SitsumonExecuteKind=2 (Execute)
            # → 完全裏方で固定される軸として除外
            src = self._flow_row_sources.get(sit_no)
            if src == 'auto' and s.get('SitsumonExecuteKind') == 2:
                continue
            result.append(s)
        result.sort(key=lambda x: x.get('SitsumonNo', 0))
        return result

    # ------------------------------------------------------------------
    # AutoSelectJoken の評価: 値が確定する行があるか
    # ------------------------------------------------------------------

    def _evaluate_auto_select(self, sitsumon_no):
        """
        Sitsumon019 の SitTabRows[*].AutoSelectJoken を評価し、
        真になる行があれば auto固定として(row_id, display, vars) を返す。
        なければ None。
        """
        sit019 = self.new_json.sitsumon019_by_no.get(sitsumon_no)
        if sit019 is None:
            return None

        rows_with_joken = []
        for row in sit019.get('SitTabRows', []):
            joken = row.get('AutoSelectJoken')
            if joken and (joken.get('VarName') or joken.get('Shiki')):
                rows_with_joken.append(row)

        if not rows_with_joken:
            return None

        # Shiki または MinKigou/MaxKigou から条件式を組み立てて評価
        for row in rows_with_joken:
            joken = row['AutoSelectJoken']
            shiki = joken.get('Shiki') or self._build_shiki_from_kigou(joken)
            if not shiki:
                continue
            try:
                result = self.hyo.evaluate(shiki)
                if result != 0:
                    return {'row_id': row['RowID']}
            except (ExternalReferenceError, Exception):
                continue
        return None

    def _build_shiki_from_kigou(self, joken):
        """
        AutoSelectJoken の Min/Max + Kigou から Shiki 文字列を構築。
        Sirius の SitTabRowRecord.Shiki の挙動を模倣。
        """
        var = joken.get('VarName')
        if not var:
            return None
        kigou_map = {1: '==', 2: '<', 3: '<='}  # 推測: Equal=1, Greater=2(変数<値), GreatThanEqual=3(変数<=値)
        min_kigou = joken.get('MinKigou', 0)
        max_kigou = joken.get('MaxKigou', 0)
        min_val = joken.get('MinValue')
        max_val = joken.get('MaxValue')
        parts = []
        if min_kigou and min_val is not None:
            op = kigou_map.get(min_kigou, '==')
            if min_kigou == 1:
                parts.append(f'{min_val}{op}{var}')
            else:
                parts.append(f'{min_val}{op}{var}')
        if max_kigou and max_val is not None:
            op = kigou_map.get(max_kigou, '==')
            if max_kigou == 1:
                parts.append(f'{var}{op}{max_val}')
            else:
                parts.append(f'{var}{op}{max_val}')
        return ' && '.join(parts) if parts else None

    # ------------------------------------------------------------------
    # メイン
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # 業務ルール: axis_behaviors の適用
    # ------------------------------------------------------------------

    def _apply_axis_behaviors(self, vary_reasons, ui_axes):
        """global_rules.yaml の axis_behaviors を評価し、該当軸を vary に昇格させる。

        現状サポートする condition:
          - change_affects_suryo_via_axis_switch:
              軸が UI 可視 かつ diff に 計算表変更がある → vary
        """
        behaviors = self.global_rules.get('axis_behaviors') or []
        if not behaviors:
            return

        has_keisan_change = any(
            r.get('カテゴリ') == '計算表' and r.get('変更種別') in ('変更', '追加', '削除')
            for r in self.diff_rows
        )

        for entry in behaviors:
            axis_name = entry.get('axis_name')
            if not axis_name:
                continue
            condition = (entry.get('promote_to_vary_when') or {}).get('condition')

            target_sit = None
            for s in ui_axes:
                if s.get('Mesho') == axis_name:
                    target_sit = s
                    break
            if target_sit is None:
                continue

            sit_no = target_sit['SitsumonNo']
            promote = False
            reason = ''
            if condition == 'change_affects_suryo_via_axis_switch':
                if has_keisan_change:
                    promote = True
                    reason = f'業務ルール: {axis_name}の切替が代価表数量に影響'

            if promote and sit_no not in vary_reasons:
                vary_reasons[sit_no] = [reason]

    # ------------------------------------------------------------------
    # メイン
    # ------------------------------------------------------------------

    def generate(self):
        vary_reasons = self._vary_sitsumon_nos()
        ui_axes = self._collect_ui_axes()
        # axis_behaviors を適用 (vary 昇格)
        self._apply_axis_behaviors(vary_reasons, ui_axes)

        plan = []
        for sit in ui_axes:
            no = sit.get('SitsumonNo')
            name = sit.get('Mesho', f'SitsumonNo:{no}')

            if no in vary_reasons:
                kind = 'vary'
                reason = ' / '.join(dict.fromkeys(vary_reasons[no]))
            else:
                auto = self._evaluate_auto_select(no)
                if auto:
                    kind = 'auto'
                    reason = '自動選択(AutoSelectJoken)'
                else:
                    kind = 'fix'
                    reason = ''

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
            ])

        return plan


def run(diff_csv_path, new_json_path, output_path, old_json_path=None):
    gen = TestPlanGenerator(diff_csv_path, new_json_path, old_json_path)
    plan = gen.generate()
    BugakariJSON.write_csv([HEADER] + plan, output_path)

    vary_n = sum(1 for r in plan if r[2] == 'vary')
    auto_n = sum(1 for r in plan if r[2] == 'auto')
    fix_n = sum(1 for r in plan if r[2] == 'fix')
    print(f'テスト計画生成完了: {output_path}')
    print(f'  軸合計: {len(plan)}件 (vary={vary_n} / auto={auto_n} / fix={fix_n})')
    for r in plan:
        if r[2] == 'vary':
            print(f'  [vary] {r[1]} ({r[5]})')


if __name__ == '__main__':
    if len(sys.argv) < 4:
        print('Usage: python generate_proposals.py <diff_csv> <new_json> <output_csv> [old_json]')
        sys.exit(1)
    old_json = sys.argv[4] if len(sys.argv) > 4 else None
    run(sys.argv[1], sys.argv[2], sys.argv[3], old_json)
