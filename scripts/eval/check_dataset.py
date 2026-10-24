"""Проверка датасета: uv run python scripts/eval/check_dataset.py"""
import sys

from dataset import problems, summary

if __name__ == "__main__":
    found = problems()
    for line in found:
        print("ПРОБЛЕМА:", line)
    if not found:
        print("OK:", summary())
    sys.exit(1 if found else 0)
