"""
後方互換ラッパー
BugakariJSON の実体は 99_退避_現ロジックv1/engine/bugakari_json.py に移行済み。
工種別スクリプトからの既存 import を維持するために残す。
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'engine'))
from bugakari_json import BugakariJSON, fmt  # noqa: F401
