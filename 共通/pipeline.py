"""
歩掛JSON テストケース生成パイプライン
①差分抽出 → ②テスト提案 → ③テストケースCSV生成

使い方:
  python pipeline.py <old_json> <new_json> <output_dir>

例:
  python pipeline.py 工種別/03_.../input/old.json 工種別/03_.../input/new.json 工種別/03_.../output
"""

import sys
import os

BASE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(BASE, 'step1_diff'))
sys.path.insert(0, os.path.join(BASE, 'step2_proposals'))
sys.path.insert(0, os.path.join(BASE, 'step3_csv'))

from extract_diff import run as run_step1
from generate_proposals import run as run_step2
from generate_csv import run as run_step3


def run_pipeline(old_json, new_json, output_dir):
    os.makedirs(output_dir, exist_ok=True)

    print('=' * 50)
    print('① 差分抽出')
    print('=' * 50)
    step1_out = os.path.join(output_dir, 'step1_差分レポート.csv')
    run_step1(old_json, new_json, step1_out)

    print()
    print('=' * 50)
    print('② テスト提案リスト生成')
    print('=' * 50)
    step2_out = os.path.join(output_dir, 'step2_提案リスト.csv')
    run_step2(step1_out, new_json, step2_out, old_json)

    print()
    print('=' * 50)
    print('③ テストケースCSV生成')
    print('=' * 50)
    step3_out = os.path.join(output_dir, 'step3_テストケース.csv')
    run_step3(step2_out, step3_out)


if __name__ == '__main__':
    if len(sys.argv) < 4:
        print('Usage: python pipeline.py <old_json> <new_json> <output_dir>')
        sys.exit(1)
    run_pipeline(sys.argv[1], sys.argv[2], sys.argv[3])
