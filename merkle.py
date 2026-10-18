#!/usr/bin/env python3
"""
merkle.py — построение Меркл-дерева из списка хешей.

Использование как модуля:
    from merkle import merkle_root
    root = merkle_root(["hash1", "hash2", ...])

Использование как скрипта:
    python merkle.py
"""

import hashlib


def _hash_pair(left: str, right: str) -> str:
    """
    Объединяет два хеша в один.

    Берём конкатенацию строк (hex), считаем SHA-256 от неё.
    Возвращаем hex-строку.
    """
    combined = (left + right).encode("utf-8")
    return hashlib.sha256(combined).hexdigest()


def merkle_root(hashes: list[str]) -> str:
    """
    Строит Меркл-дерево и возвращает корень.

    Аргументы:
        hashes — список hex-строк (SHA-256 каждого файла)

    Возвращает:
        hex-строку — корень дерева.
        Если список пуст — возвращает хеш пустой строки.
    """
    if not hashes:
        return hashlib.sha256(b"").hexdigest()

    # Копируем, чтобы не менять исходный список
    level = list(hashes)

    # Пока не останется один элемент — объединяем попарно
    while len(level) > 1:
        next_level = []

        # Идём парами
        for i in range(0, len(level), 2):
            left = level[i]
            # Если пары нет (нечётное число) — берём сам с собой
            right = level[i + 1] if i + 1 < len(level) else left
            next_level.append(_hash_pair(left, right))

        level = next_level

    return level[0]


def merkle_levels(hashes: list[str]) -> list[list[str]]:
    """
    Возвращает все уровни дерева — для отладки и визуализации.

    Первый уровень — исходные хеши.
    Последний — [корень].
    """
    if not hashes:
        return [[hashlib.sha256(b"").hexdigest()]]

    levels = [list(hashes)]
    level = list(hashes)

    while len(level) > 1:
        next_level = []
        for i in range(0, len(level), 2):
            left = level[i]
            right = level[i + 1] if i + 1 < len(level) else left
            next_level.append(_hash_pair(left, right))
        levels.append(next_level)
        level = next_level

    return levels


# --- Тест при запуске как скрипта ---
if __name__ == "__main__":
    # Простой тест на трёх элементах
    test_hashes = [
        "a" * 64,
        "b" * 64,
        "c" * 64,
    ]

    print("Тест Меркл-дерева:\n")
    levels = merkle_levels(test_hashes)

    for i, level in enumerate(levels):
        print(f"Уровень {i} ({len(level)} элементов):")
        for h in level:
            print(f"  {h[:32]}...")
        print()

    root = merkle_root(test_hashes)
    print(f"Корень: {root}")