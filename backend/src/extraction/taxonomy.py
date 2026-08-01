"""分類體系載入與查詢模組。

提供七大高階事件類別定義與驗證。
資產檔案：
- `high_level_types.json`：高階類別定義、預設類別與 taxonomy_version
"""

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any
import json
import logging

TAXONOMY_ASSETS_DIR = Path(__file__).resolve().parent / "assets" / "taxonomy"

logger = logging.getLogger(__name__)

HIGH_LEVEL_TYPES_FILE = "high_level_types.json"


class TaxonomyError(ValueError):
    """分類體系資產不一致錯誤。"""


@dataclass(frozen=True)
class HighLevelType:
    """對外公開的高階事件類別定義。"""

    id: str
    display_name: str
    description: str


@dataclass(frozen=True)
class Taxonomy:
    """已校驗且封裝完畢的不可變分類體系實例。"""

    taxonomy_version: str
    default_type: str
    types: tuple[HighLevelType, ...]

    @property
    def type_ids(self) -> tuple[str, ...]:
        return tuple(t.id for t in self.types)

    def validate_type(self, type_id: str) -> bool:
        """驗證 type_id 是否為合法的高階事件類別。"""
        return type_id in self.type_ids

    def resolve_type(self, label: str) -> tuple[str, bool]:
        """將模型輸出標籤映射為合法 type_id。回傳 (type_id, is_valid)。"""
        if label in self.type_ids:
            return label, True
        return self.default_type, False


def _read_json(path: Path) -> Any:
    if not path.is_file():
        raise TaxonomyError(f"分類體系資產缺失：{path}")
    with path.open(encoding="utf-8") as fp:
        return json.load(fp)


def load_taxonomy(assets_dir: Path | str | None = None) -> Taxonomy:
    """載入分類體系實例。"""
    resolved = Path(assets_dir) if assets_dir is not None else TAXONOMY_ASSETS_DIR
    return _load_taxonomy_cached(str(resolved.resolve()))


@lru_cache(maxsize=8)
def _load_taxonomy_cached(assets_dir: str) -> Taxonomy:
    base = Path(assets_dir)
    types_raw = _read_json(base / HIGH_LEVEL_TYPES_FILE)

    types, default_type, taxonomy_version = _build_types(types_raw)

    return Taxonomy(
        taxonomy_version=taxonomy_version,
        default_type=default_type,
        types=types,
    )


def _build_types(raw: Any) -> tuple[tuple[HighLevelType, ...], str, str]:
    types_raw = raw.get("types") if isinstance(raw, dict) else None
    if not isinstance(types_raw, list) or not types_raw:
        raise TaxonomyError("高階類別資產不含 types 陣列")

    types: list[HighLevelType] = []
    seen: set[str] = set()
    for entry in types_raw:
        type_id = entry.get("id")
        if not type_id:
            raise TaxonomyError("高階類別缺少 id")
        if type_id in seen:
            raise TaxonomyError(f"高階類別 id 重複：{type_id}")
        seen.add(type_id)
        types.append(
            HighLevelType(
                id=type_id,
                display_name=entry.get("display_name") or type_id,
                description=entry.get("description") or "",
            )
        )

    default_type = raw.get("default_type")
    if default_type not in seen:
        raise TaxonomyError(f"default_type 不在高階類別清單內：{default_type}")

    taxonomy_version = raw.get("taxonomy_version")
    if not taxonomy_version:
        raise TaxonomyError("高階類別資產缺少 taxonomy_version")

    return tuple(types), default_type, taxonomy_version
