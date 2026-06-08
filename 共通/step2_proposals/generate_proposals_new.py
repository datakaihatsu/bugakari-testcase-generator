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
        """全分岐を辿って到達質問の集合と初出順を返す。"""
        order = []
        seen = set()

        def add_seq(seq):
            for sn in seq:
                if sn not in seen:
                    seen.add(sn)
                    order.append(sn)

        base = FlowWalker(self.bj).walk()
        add_seq(base.get('visited_sitsumons', []))

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
                    res = FlowWalker(self.bj, vary_selections={sn: rid}).walk()
                    before = len(seen)
                    add_seq(res.get('visited_sitsumons', []))
                    if len(seen) > before:
                        changed = True
        return order

    def generate(self):
        order = self._discover_reachable()
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

            if self._is_default_exec(sit):
                plan.append([self._next_id(), name, 'auto', sn, f'{name}(固定)',
                             'デフォルト実行 (SitsumonExecuteKind=1, SekisanEnv連動なし)', note, ''])
                continue
            if kind == 17:
                plan.append([self._next_id(), name, 'fix', sn, name, '', note, ''])
                continue
            # kind == 19
            rows = self._selectable_rows(sn)
            if len(rows) >= 2:
                # 自動確定検知: AutoSelectJoken の駆動変数が上流で確定し、最終値が
                #   行を自動選択する質問はユーザが選ばない (例: 17 クレーン規格、駆動変数 L~CK)。
                #   vary にすると選択肢分の直積爆発を起こすため auto に降格する。
                if self._auto._is_autodetermined(sit):
                    plan.append([self._next_id(), name, 'auto', sn, f'{name}(固定)',
                                 '自動確定 (AutoSelectJokenの駆動変数が確定)', note, ''])
                else:
                    plan.append([self._next_id(), name, 'vary', sn, name, '新規工種:全選択肢網羅', note, ''])
            else:
                plan.append([self._next_id(), name, 'fix', sn, name, '', note, ''])
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
