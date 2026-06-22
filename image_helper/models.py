from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class AssetRecord:
    asset_id: str
    local_datetime: datetime
    phash: str
    checksum: str | None = None
    width: int | None = None
    height: int | None = None
    original_file_name: str | None = None
    stack_id: str | None = None
    is_primary_in_stack: bool | None = None


@dataclass(frozen=True)
class WiggleGroup:
    assets: tuple[AssetRecord, ...]
    distances: tuple[int, ...]

    @property
    def group_key(self) -> str:
        return "|".join(asset.asset_id for asset in self.assets)

    @property
    def average_distance(self) -> float:
        if not self.distances:
            return 0.0
        return sum(self.distances) / len(self.distances)

    @property
    def min_distance(self) -> int | None:
        if not self.distances:
            return None
        return min(self.distances)

    @property
    def max_distance(self) -> int | None:
        if not self.distances:
            return None
        return max(self.distances)

    @property
    def dimensions_summary(self) -> str:
        sizes = {
            (asset.width, asset.height)
            for asset in self.assets
            if asset.width is not None and asset.height is not None
        }
        if not sizes:
            return "unknown"
        if len(sizes) == 1:
            width, height = next(iter(sizes))
            return f"{width}x{height}"
        return "mixed"

    @property
    def stack_summary(self) -> str:
        stack_ids = {asset.stack_id for asset in self.assets if asset.stack_id}
        if not stack_ids:
            return "none"
        if len(stack_ids) == 1:
            return "shared"
        return "mixed"
