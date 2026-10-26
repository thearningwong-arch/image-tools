"""Shared contracts between the desktop interface and local search engine."""
from dataclasses import dataclass, field
from PIL import Image


@dataclass
class RenderedPage:
    image: Image.Image
    page: int = 1


@dataclass
class ScanProgress:
    current: int
    total: int
    path: str
    status: str


@dataclass
class ScanReport:
    indexed: int = 0
    unchanged: int = 0
    failed: int = 0
    removed: int = 0
    cancelled: bool = False
    errors: list[tuple[str, str]] = field(default_factory=list)


@dataclass
class LibraryStats:
    files: int = 0
    pages: int = 0
    failed: int = 0


@dataclass
class SearchResult:
    file_id: int
    path: str
    score: float
    match_type: str  # identical (bytes), preview (rendered pixels), similar
    page: int
    thumbnail_path: str
    width: int
    height: int
    file_size: int
    modified_ns: int
