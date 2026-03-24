"""Tests for hkstock.core.io module."""
import json
from pathlib import Path
from hkstock.core.io import read_json, write_json


def test_read_json_missing_file_returns_default(tmp_data_dir):
    result = read_json("nonexistent.json", default={"fallback": True})
    assert result == {"fallback": True}


def test_read_json_missing_file_returns_none(tmp_data_dir):
    result = read_json("nonexistent.json")
    assert result is None


def test_write_and_read_json(tmp_data_dir):
    data = {"key": "value", "number": 42, "list": [1, 2, 3]}
    write_json("test.json", data)
    result = read_json("test.json")
    assert result == data


def test_write_json_chinese(tmp_data_dir):
    data = {"name": "腾讯控股", "sector": "科技互联网"}
    write_json("chinese.json", data)
    result = read_json("chinese.json")
    assert result["name"] == "腾讯控股"
    # Verify file content is not ASCII-escaped
    path = tmp_data_dir / "chinese.json"
    content = path.read_text(encoding="utf-8")
    assert "腾讯控股" in content


def test_write_json_creates_subdirs(tmp_data_dir):
    write_json("sub/dir/test.json", {"nested": True})
    assert (tmp_data_dir / "sub" / "dir" / "test.json").exists()
    result = read_json("sub/dir/test.json")
    assert result == {"nested": True}


def test_write_json_atomic(tmp_data_dir):
    """Atomic write should not leave partial files on error."""
    write_json("atomic.json", {"first": True})
    # Overwrite
    write_json("atomic.json", {"second": True})
    result = read_json("atomic.json")
    assert result == {"second": True}


def test_write_json_non_atomic(tmp_data_dir):
    write_json("non_atomic.json", {"data": 1}, atomic=False)
    result = read_json("non_atomic.json")
    assert result == {"data": 1}


def test_write_json_returns_path(tmp_data_dir):
    path = write_json("return_path.json", {})
    assert isinstance(path, Path)
    assert path.exists()
