"""
② テスト計画生成 — 新規工種モード (差分なし)

【背景】
既存工種は「旧→新JSONの差分」を起点に vary 軸を絞り込んでいた。
新規作成された工種は旧JSONが存在せず差分が取れないため、差分起点の
絞り込みができない。代わりに「カバレッジ基準を満たす有効抜粋」でテストを構築する。

【軸の分類】
- UI 可視の選択質問 (SitsumonKind=19) で選択可能行>=2 かつ デフォルト実行でない
  かつ 自動確定でない                                                     -> vary
- デフォルト実行 (J3 条件: ExecKind=1 + Flags subset {105,108} + Kind!=17)  -> auto
- 自動確定 (AutoSelectJoken の駆動変数が確定し最終値が行を自動選択)         -> auto
- 数値入力 (SitsumonKind=17)                                              -> fix (任意 表示)
- 単一選択 (選択可能行<2)                                                  -> fix
- マスタ選択 (SitsumonKind=8) 等                                          -> 列に出さない

【到達質問の探索】
各選択肢を選んで走査し、訪問質問を union することで全分岐の到達質問を集める。

【入力】 新JSON のみ
【出力】 step2.0_テスト計画.csv (既存と同一フォーマット)
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'engine'))
from bugakari_json import BugakariJSON
from flow_walker import FlowWalker
from generate_proposals import TestPlanGenerator

HEADER = ['軸ID', '軸名', '種別', 'SitsumonNo', '列ラベル', '変更理由', '備考', '強制行ID']


class NewKotsuPlanGenerator:

    def __init__(self, new_json_path):
        self.bj = BugakariJSON(new_json_path)
        self._counter = 0
        # 自動確定検知 (AutoSelectJoken の駆動変数->自動選択) を差分型と共用する。
        #   差分なしで使うため diff_csv_path=None で生成 (差分起点ロジックは未使用)。
        self._auto = TestPlanGenerator(None, new_json_path)

    def _next_id(self):
        self._counter += 1
        return f'AX-{self._counter:03d}'

    @staticmethod
    def _is_default_exec(sit):
        """J3 条件: ExecKind=1 + Flags subset {105,108} + Kind!=17 = デフォルト実行。"""
        if sit.get('SitsumonExecuteKind') != 1:
            return False
        if sit.get('SitsumonKind') == 17:
            return False
        extra = [f for f in (sit.get('SitsumonFlags') or []) if f not in (105, 108)]
        return not extra

    def _selectable_rows(self, sitsumon_no):
        s019 = self.bj.sitsumon019_by_no.get(sitsumon_no)
        if not s019:
            return []
        return [
            r['RowID'] for r in s019.get('SitRows', [])
            if r.get('Visible', True) and not r.get('IsFixed', False)
        ]

    def _discover_reachable(self):
        """全分岐を辿って到達質問の集合と初出順を返す。
        併せて各分岐走査の変数スコープ(hyo._user_inputs)を self._branch_scopes に収集する
        (generate() の『上流選択で開く条件』補完に使う)。"""
        order = []
        seen = set()
        self._branch_scopes = []
        _scope_keys = set()

        def add_seq(seq):
            for sn in seq:
                if sn not in seen:
                    seen.add(sn)
                    order.append(sn)

        def capture(walker):
            try:
                sc = dict(walker.hyo._user_inputs)
            except Exception:
                return
            key = frozenset(sc.items())
            if key not in _scope_keys:
                _scope_keys.add(key)
                self._branch_scopes.append(sc)

        base_w = FlowWalker(self.bj)
        add_seq(base_w.walk().get('visited_sitsumons', []))
        capture(base_w)

        changed = True
        guard = 0
        while changed and guard < 50:
            guard += 1
            changed = False
            for sn in list(seen):
                sit = self.bj.sitsumon_by_no.get(sn)
                if not sit or sit.get('SitsumonKind') not in (19,):
                    continue
                rows = self._selectable_rows(sn)
                if len(rows) < 2:
                    continue
                for rid in rows:
                    w = FlowWalker(self.bj, vary_selections={sn: rid})
                    res = w.walk()
                    before = len(seen)
                    add_seq(res.get('visited_sitsumons', []))
                    capture(w)
                    if len(seen) > before:
                        changed = True
        return order

    def generate(self):
        order = self._discover_reachable()
        auto = self._auto  # 検証済み TestPlanGenerator (可視ゲート/自動確定判定を共用)
        # 『分岐で開く条件』補完の重複ガード用: 通常の可視ゲートを通る質問名の集合。
        #   同名が既に正規の軸として出るなら、隠れ質問の補完で重複列を作らない
        #   (例: 21 側溝規格 は Sit33 が既に可視軸 → Sit34 の補完はしない)。
        _visible_names = set()
        for _sn in order:
            _s = self.bj.sitsumon_by_no.get(_sn)
            if _s and _s.get('SitsumonKind') in (17, 19) and auto._is_ui_visible_axis(_s):
                _visible_names.add(_s.get('Mesho', ''))
        plan = []
        for sn in order:
            sit = self.bj.sitsumon_by_no.get(sn)
            if not sit:
                continue
            kind = sit.get('SitsumonKind')
            name = sit.get('Mesho', f'SitsumonNo:{sn}')
            note = f'SitsumonKind={kind}'

            if kind == 205:
                continue
            if kind == 8:
                continue
            if kind not in (17, 19):
                continue

            # ── (1) UI可視ゲート (TC作成スクリプトと共用) ──────────────────
            #   開かない質問 = 条件自動確定で制御されるもの (AutoSelectJoken の駆動変数が
            #   JSON内で確定/内部分岐/"="mesho/ShortCut先が非可視 等) は列に出さない。
            #   _is_ui_visible_axis 内の _var_is_determined が「JSON内で確定する変数値で
            #   確定するロジック」に相当 (例: 21 省庁分岐/材料区分)。
            if not auto._is_ui_visible_axis(sit):
                # 差分モードと同等の補完(generate_proposals L837-868): baseline では駆動変数が
                #   行を自動選択して隠れるが、上流 vary 選択で駆動変数が範囲の隙間に落ちて
                #   『開く』質問は、ユーザ入力軸として残す (#40 条件選択: 運搬物種別=セメント
                #   →FG1=8 が Row の範囲外 → 開く)。_opens_on_forced_route は全駆動が
                #   スコープに直接確定&外部でない&確定Kigouでどの行も選ばない時のみ真(安全側)。
                if (len(self._selectable_rows(sn)) >= 2
                        and name not in _visible_names
                        and any(auto._opens_on_forced_route(sn, sc)
                                for sc in getattr(self, '_branch_scopes', []))):
                    plan.append([self._next_id(), name, 'vary', sn, name,
                                 '分岐で開く条件 (上流選択で駆動変数が範囲外→開く)', note, ''])
                    continue
                plan.append([self._next_id(), name, 'auto', sn, f'{name}(固定)',
                             '自動確定 (UI非可視: 変数確定/内部分岐)', note, ''])
                continue

            if kind == 17:
                plan.append([self._next_id(), name, 'fix', sn, name, '', note, ''])
                continue

            # ── (2) kind==19 の種別判定 (TC作成スクリプト L716-738 に準拠) ──
            #   ※ baseline行出所 src=='auto' による除外は行わない。差分モードでは
            #     差分軸(旧→新で変化)は vary に昇格するため baseline-auto でも列に残る
            #     (例: 17 クレーン賃料補正率)。差分を持たない新規モードで src=='auto' を
            #     一律除外すると、こうした「本来出す」軸まで落とすため不採用。
            #     決定A(代替経路も出す=多めに出す)に整合。
            rows = self._selectable_rows(sn)
            if sit.get('LevelVarName'):
                # レベル変数を持つ質問は「実効実行種別」= レベル変数の最終値で開閉が決まる
                #   (仕様§1.5)。ExecKind ではなく値で判定する:
                #     値2 = デフォルト選択(初回不要だが再選択可能) → 開く(列に残す)
                #     値3 = 必ず実行(ユーザ入力)                   → 開く(vary)
                #     値0 = 機能OFF/計設定確定 / 値1 = 閉(非表示)   → 出さない(auto)
                #   (値1は上流の _is_ui_visible_axis で既に除外済み)
                try:
                    lvval = float(auto._resolve_value(sit.get('LevelVarName')))
                except Exception:
                    lvval = None
                if lvval in (2.0, 3.0):
                    if len(rows) >= 2:
                        plan.append([self._next_id(), name, 'vary', sn, name,
                                     f'レベル変数=開(値{lvval:g}: 再選択可能/必ず実行)', note, ''])
                    else:
                        plan.append([self._next_id(), name, 'fix', sn, name,
                                     f'レベル変数=デフォルト選択・再選択可能(値{lvval:g})', note, ''])
                else:
                    plan.append([self._next_id(), name, 'auto', sn, f'{name}(固定)',
                                 f'自動確定 (レベル変数値={lvval}: 閉/機能OFF)', note, ''])
            elif auto._all_rows_autoselect(sit):
                # 全選択可能行が AutoSelectJoken 条件付き = ユーザが直接選ばない範囲表。
                plan.append([self._next_id(), name, 'auto', sn, f'{name}(固定)',
                             '自動確定 (全選択可能行がAutoSelectJoken付き)', note, ''])
            elif self._is_default_exec(sit):
                # 純デフォルト実行 (ExecKind=1 + Flags⊆{105,108}) = 問われず固定実行。
                plan.append([self._next_id(), name, 'auto', sn, f'{name}(固定)',
                             'デフォルト実行 (SitsumonExecuteKind=1, SekisanEnv連動なし)', note, ''])
            elif len(rows) >= 2:
                plan.append([self._next_id(), name, 'vary', sn, name,
                             '新規工種:全選択肢網羅', note, ''])
            else:
                # 選択可能行が1件 = デフォルト選択で初回表示され再選択可能な質問
                #   (例: 22 スタビライザ/タイヤローラ排ガス機械の選択)。列に残す (fix)。
                plan.append([self._next_id(), name, 'fix', sn, name,
                             'デフォルト選択・再選択可能 (選択肢1件)', note, ''])
        return plan


def run(new_json_path, output_path):
    gen = NewKotsuPlanGenerator(new_json_path)
    plan = gen.generate()
    BugakariJSON.write_csv([HEADER] + plan, output_path)
    vary_n = sum(1 for r in plan if r[2] == 'vary')
    auto_n = sum(1 for r in plan if r[2] == 'auto')
    fix_n = sum(1 for r in plan if r[2] == 'fix')
    print(f'テスト計画生成完了 (新規工種モード): {output_path}')
    print(f'  軸合計: {len(plan)}件 (vary={vary_n} / auto={auto_n} / fix={fix_n})')
    for r in plan:
        if r[2] == 'vary':
            print(f'  [vary] {r[1]} ({r[5]})')


if __name__ == '__main__':
    if len(sys.argv) < 3:
        print('Usage: python generate_proposals_new.py <new_json> <output_csv>')
        sys.exit(1)
    run(sys.argv[1], sys.argv[2])
