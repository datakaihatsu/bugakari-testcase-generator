"""
歩掛JSON テストケース生成パイプライン (絞り込みアプローチ)

【使い方】
  python pipeline.py <old_json> <new_json> <output_dir> [--external-scenarios]
"""

import sys
import os

BASE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(BASE, 'step1_diff'))
sys.path.insert(0, os.path.join(BASE, 'step1_5_check'))
sys.path.insert(0, os.path.join(BASE, 'step2_proposals'))
sys.path.insert(0, os.path.join(BASE, 'step3_csv'))

from extract_diff import run as run_step1
from check_alignment import run as run_step1_5
from generate_proposals import run as run_step2
from generate_csv import run as run_step3


def run_pipeline(old_json, new_json, output_dir, external_scenarios=False):
    os.makedirs(output_dir, exist_ok=True)

    print('=' * 50)
    print('① 差分抽出')
    print('=' * 50)
    step1_out = os.path.join(output_dir, 'step1.0_差分レポート.csv')
    run_step1(old_json, new_json, step1_out)

    print()
    print('=' * 50)
    print('①.5 修正方針との乖離チェック')
    print('=' * 50)
    intent_path = os.path.join(os.path.dirname(new_json), '修正方針.txt')
    step1_5_out = os.path.join(output_dir, 'step1.5_乖離チェック.csv')
    run_step1_5(intent_path, step1_out, step1_5_out, new_json, old_json)

    print()
    print('=' * 50)
    print('② テスト計画生成 (絞り込み)')
    print('=' * 50)
    step2_out = os.path.join(output_dir, 'step2.0_テスト計画.csv')
    run_step2(step1_out, new_json, step2_out, old_json)

    print()
    print('=' * 50)
    print('③ テストケースCSV生成 (強制行ID対応)')
    print('=' * 50)
    step3_out = os.path.join(output_dir, 'step3.0_テストケース.csv')
    run_step3(step2_out, new_json, step3_out, old_json)

    # --- 任意: 外部/計設定変数シナリオの追加生成 (既定OFF・本体不変) ---
    #   ONのときだけ、差分が外部変数の非既定分岐の奥にある工種で
    #   step3.0_テストケース_<シナリオ>.csv を別ファイルで追加出力する(#35 大阪市等)。
    if external_scenarios:
        try:
            from external_scenarios import generate as gen_ext
            print()
            print('=' * 50)
            print('④ 外部/計設定変数シナリオ (任意・追加生成)')
            print('=' * 50)
            made = gen_ext(old_json, new_json, output_dir, run_step2, run_step3)
            if not made:
                print('  (該当シナリオなし)')
        except Exception as e:
            print(f'  [外部シナリオ生成スキップ] {e}')


if __name__ == '__main__':
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    ext = '--external-scenarios' in sys.argv
    if len(args) < 3:
        print('Usage: python pipeline.py <old_json> <new_json> <output_dir> [--external-scenarios]')
        sys.exit(1)
    run_pipeline(args[0], args[1], args[2], external_scenarios=ext)
