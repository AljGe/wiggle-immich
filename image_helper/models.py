from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class AssetRecord:
    asset_id: str
    local_datetime: datetime
    phash: str
    checksum: str | None = None


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
