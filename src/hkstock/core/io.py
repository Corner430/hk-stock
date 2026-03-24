"""
统一文件 I/O 工具

提供 JSON 文件读写的标准化接口，默认操作 DATA_DIR 目录。
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from hkstock.core.config import DATA_DIR


def read_json(filename: str, default: Any = None) -> Any:
    """
    从 data/ 目录读取 JSON 文件。

    Args:
        filename: 相对于 DATA_DIR 的文件名（如 "portfolio.json"）
        default: 文件不存在时返回的默认值

    Returns:
        解析后的 JSON 数据，或 default
    """
    path = DATA_DIR / filename
    if not path.exists():
        return default
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def write_json(filename: str, data: Any, *, indent: int = 2, atomic: bool = True) -> Path:
    """
    将数据写入 data/ 目录的 JSON 文件。

    Args:
        filename: 相对于 DATA_DIR 的文件名
        data: 可 JSON 序列化的数据
        indent: 缩进空格数
        atomic: 是否使用原子写入（先写临时文件再 rename）

    Returns:
        写入的文件路径
    """
    path = DATA_DIR / filename
    path.parent.mkdir(parents=True, exist_ok=True)

    if atomic:
        fd, tmp_path = tempfile.mkstemp(
            dir=str(path.parent), suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=indent)
            os.replace(tmp_path, str(path))
        except BaseException:
            # 清理临时文件
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
    else:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=indent)

    return path
