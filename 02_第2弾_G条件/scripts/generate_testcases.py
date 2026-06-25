"""
後方互換ラッパー
BugakariJSON の実体は 02_第2弾_G条件/engine/bugakari_json.py に移行済み。
工種別スクリプトからの既存 import を維持するために残す。
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'engine'))
from bugakari_json import BugakariJSON, fmt  # noqa: F401
