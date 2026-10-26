"""Persistent, local-only index for image and design document previews.

Each operation owns its SQLite connection so the desktop UI can read statistics
while a worker scans. Original files are never written, moved or deleted.
"""
from __future__ import annotations

from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
import hashlib
import json
import os
import shutil
import sqlite3
import threading
import uuid

from . import features
from .models import LibraryStats, ScanProgress, ScanReport, SearchResult
from .rendering import RenderError, SUPPORTED_EXTENSIONS, render_pages

_INDEX_VERSION = 1
_CHUNK_SIZE = 1024 * 1024


def _path(value: str | Path) -> Path:
    return Path(value).expanduser().resolve()


def _key(value: str | Path) -> str:
    return os.path.normcase(str(_path(value)))


def _inside(child: str | Path, parent: str | Path) -> bool:
    try:
        Path(_key(child)).relative_to(Path(_key(parent)))
        return True
    except ValueError:
        return False


class _Cancelled(InterruptedError):
    def __init__(self) -> None:
        super().__init__("检索或扫描已取消")


def _check_cancel(cancel: threading.Event | None) -> None:
    if cancel is not None and cancel.is_set():
        raise _Cancelled()


def _sha256(path: Path, cancel: threading.Event | None = None) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(_CHUNK_SIZE):
            _check_cancel(cancel)
            digest.update(chunk)
    return digest.hexdigest()


def _same_snapshot(before: os.stat_result, after: os.stat_result) -> bool:
    """Ensure a hash and preview describe the same observed file version."""
    return all(getattr(before, field) == getattr(after, field) for field in (
        "st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns",
    ))


def _check_query_snapshot(path: Path, before: os.stat_result) -> None:
    try:
        unchanged = _same_snapshot(before, path.stat())
    except OSError:
        unchanged = False
    if not unchanged:
        raise RenderError("查询文件在检索期间发生变化或已不可访问，请重新选择或重试")


class Library:
    def __init__(self, database_path: Path, cache_dir: Path, max_pages: int = 20):
        if max_pages < 1:
            raise ValueError("每个文档的最大页数必须大于零")
        self.database_path = _path(database_path)
        self.cache_dir = _path(cache_dir)
        self.max_pages = int(max_pages)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.signature = f"{_INDEX_VERSION}:pages={self.max_pages}"
        with self._connection() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS roots (
                    path TEXT PRIMARY KEY
                );
                CREATE TABLE IF NOT EXISTS files (
                    id INTEGER PRIMARY KEY,
                    path TEXT NOT NULL UNIQUE,
                    size INTEGER NOT NULL,
                    mtime_ns INTEGER NOT NULL,
                    sha256 TEXT,
                    signature TEXT NOT NULL,
                    status TEXT NOT NULL,
                    error TEXT
                );
                CREATE INDEX IF NOT EXISTS files_sha256 ON files(sha256);
                CREATE TABLE IF NOT EXISTS root_files (
                    root TEXT NOT NULL REFERENCES roots(path) ON DELETE CASCADE,
                    file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
                    PRIMARY KEY(root, file_id)
                );
                CREATE TABLE IF NOT EXISTS pages (
                    file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
                    page INTEGER NOT NULL,
                    width INTEGER NOT NULL,
                    height INTEGER NOT NULL,
                    descriptor TEXT NOT NULL,
                    pixel_digest TEXT NOT NULL,
                    thumbnail_path TEXT NOT NULL,
                    PRIMARY KEY(file_id, page)
                );
            """)

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.database_path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            yield db
        finally:
            db.close()

    def roots(self) -> list[str]:
        with self._connection() as db:
            return [row[0] for row in db.execute("SELECT path FROM roots ORDER BY path")]

    def stats(self) -> LibraryStats:
        with self._connection() as db:
            return LibraryStats(
                files=db.execute("SELECT COUNT(*) FROM files WHERE status='ready'").fetchone()[0],
                pages=db.execute("SELECT COUNT(*) FROM pages").fetchone()[0],
                failed=db.execute("SELECT COUNT(*) FROM files WHERE status='failed'").fetchone()[0],
            )

    def failures(self) -> list[tuple[str, str]]:
        with self._connection() as db:
            return [(row[0], row[1]) for row in db.execute(
                "SELECT path, error FROM files WHERE status='failed' ORDER BY path"
            )]

    def _delete_thumbnails(self, paths: Sequence[str]) -> None:
        for value in paths:
            thumbnail = Path(value)
            if not _inside(thumbnail, self.cache_dir):
                continue
            try:
                thumbnail.unlink(missing_ok=True)
                if thumbnail.parent != self.cache_dir:
                    thumbnail.parent.rmdir()
            except OSError:
                # A stale thumbnail is safe to leave behind; source files are untouched.
                pass

    def _drop_orphans(self, db: sqlite3.Connection) -> tuple[int, list[str]]:
        ids = [row[0] for row in db.execute(
            "SELECT id FROM files WHERE NOT EXISTS "
            "(SELECT 1 FROM root_files WHERE root_files.file_id=files.id)"
        )]
        thumbnails: list[str] = []
        for file_id in ids:
            thumbnails.extend(row[0] for row in db.execute(
                "SELECT thumbnail_path FROM pages WHERE file_id=?", (file_id,)
            ))
            db.execute("DELETE FROM files WHERE id=?", (file_id,))
        return len(ids), thumbnails

    def remove_root(self, path: Path) -> None:
        """Forget a folder's index entries. Never remove or change its originals."""
        with self._connection() as db:
            with db:
                db.execute("DELETE FROM roots WHERE path=?", (_key(path),))
                _, thumbnails = self._drop_orphans(db)
        self._delete_thumbnails(thumbnails)

    def _collect(
        self,
        roots: Sequence[str],
        report: ScanReport,
        progress: Callable[[ScanProgress], None] | None,
        cancel: threading.Event | None,
    ) -> tuple[dict[str, Path], dict[str, set[str]], set[str]]:
        candidates: dict[str, Path] = {}
        seen = {root: set() for root in roots}
        complete: set[str] = set()
        # Walk the outermost selected directories once; nested roots share that walk.
        outer = [root for root in roots if not any(
            root != other and _inside(root, other) for other in roots
        )]
        for outer_root in outer:
            _check_cancel(cancel)
            covered = [root for root in roots if _inside(root, outer_root)]
            failures: list[str] = []
            if not Path(outer_root).is_dir():
                report.failed += 1
                report.errors.append((outer_root, "文件夹不存在或当前无法访问；保留已有索引"))
                continue

            def on_error(error: OSError) -> None:
                failed_path = str(error.filename or outer_root)
                failures.append(failed_path)
                report.failed += 1
                report.errors.append((failed_path, f"无法读取文件夹，保留已有索引：{error}"))

            for directory, directories, filenames in os.walk(
                outer_root, followlinks=False, onerror=on_error
            ):
                _check_cancel(cancel)
                directory_path = Path(directory)
                directories[:] = [name for name in directories
                                  if not (directory_path / name).is_symlink()
                                  and not (directory_path / name).is_junction()
                                  and not _inside(directory_path / name, self.cache_dir)]
                if progress:
                    progress(ScanProgress(0, 0, str(directory_path), "正在发现文件"))
                for name in filenames:
                    candidate = directory_path / name
                    if candidate.suffix.lower() not in SUPPORTED_EXTENSIONS:
                        continue
                    if candidate.is_symlink() or _inside(candidate, self.cache_dir):
                        continue
                    candidate_key = _key(candidate)
                    candidates[candidate_key] = candidate
                    for root in covered:
                        if _inside(candidate, root):
                            seen[root].add(candidate_key)
            for root in covered:
                if Path(root).is_dir() and not any(
                    _inside(failure, root) or _inside(root, failure) for failure in failures
                ):
                    complete.add(root)
        return candidates, seen, complete

    def scan(
        self,
        folders: Sequence[Path],
        progress: Callable[[ScanProgress], None] | None = None,
        cancel: threading.Event | None = None,
    ) -> ScanReport:
        report = ScanReport()
        requested = sorted({_key(folder) for folder in folders})
        if not requested:
            return report
        with self._connection() as db:
            with db:
                db.executemany("INSERT OR IGNORE INTO roots(path) VALUES(?)", [(r,) for r in requested])
            known_roots = [row[0] for row in db.execute("SELECT path FROM roots")]
            try:
                candidates, seen, complete = self._collect(requested, report, progress, cancel)
                total = len(candidates)
                for position, (path_key, path) in enumerate(sorted(candidates.items()), start=1):
                    _check_cancel(cancel)
                    if progress:
                        progress(ScanProgress(position - 1, total, str(path), "正在读取预览"))
                    _check_cancel(cancel)
                    old = db.execute("SELECT * FROM files WHERE path=?", (path_key,)).fetchone()
                    stat = None
                    digest = None
                    generated: list[tuple[int, int, int, str, str, str]] = []
                    staging = self.cache_dir / uuid.uuid4().hex
                    try:
                        stat = path.stat()
                        if old and old["status"] == "ready" and old["size"] == stat.st_size \
                                and old["mtime_ns"] == stat.st_mtime_ns \
                                and old["signature"] == self.signature:
                            existing_thumbnails = [row[0] for row in db.execute(
                                "SELECT thumbnail_path FROM pages WHERE file_id=?", (old["id"],)
                            )]
                            if existing_thumbnails and all(Path(p).is_file() for p in existing_thumbnails):
                                with db:
                                    self._associate(db, old["id"], path_key, known_roots)
                                report.unchanged += 1
                                if progress:
                                    progress(ScanProgress(position, total, str(path), "未变化，已跳过"))
                                continue
                        digest = _sha256(path, cancel)
                        staging.mkdir(parents=True)
                        for rendered in render_pages(path, max_pages=self.max_pages):
                            _check_cancel(cancel)
                            image = rendered.image
                            width, height = image.size
                            descriptor = json.dumps(features.describe(image), separators=(",", ":"))
                            pixel_hash = features.pixel_digest(image)
                            thumbnail_path = staging / f"{rendered.page}.jpg"
                            thumbnail = image.convert("RGB")
                            thumbnail.thumbnail((240, 180))
                            thumbnail.save(thumbnail_path, "JPEG", quality=88)
                            generated.append((rendered.page, width, height, descriptor,
                                              pixel_hash, str(thumbnail_path)))
                        if not generated:
                            raise RenderError("文件中没有可读取的预览")
                        after = path.stat()
                        if not _same_snapshot(stat, after):
                            raise RenderError("文件在扫描期间发生变化，请重新扫描")
                        old_thumbnails = self._replace_file(
                            db, path_key, stat.st_size, stat.st_mtime_ns, digest,
                            "ready", None, generated, known_roots,
                        )
                        self._delete_thumbnails(old_thumbnails)
                        report.indexed += 1
                        status = "已建立索引"
                    except _Cancelled:
                        shutil.rmtree(staging, ignore_errors=True)
                        raise
                    except Exception as error:
                        shutil.rmtree(staging, ignore_errors=True)
                        message = str(error) or error.__class__.__name__
                        if digest is not None and stat is not None:
                            # Keep exact-only matches for unsupported previews only
                            # when the completed hash still describes this source.
                            try:
                                if not _same_snapshot(stat, path.stat()):
                                    digest = None
                            except OSError:
                                digest = None
                        old_thumbnails = self._replace_file(
                            db, path_key,
                            stat.st_size if stat else 0,
                            stat.st_mtime_ns if stat else 0,
                            digest, "failed", message, [], known_roots,
                        )
                        self._delete_thumbnails(old_thumbnails)
                        report.failed += 1
                        report.errors.append((str(path), message))
                        status = "无法读取预览"
                    if progress:
                        progress(ScanProgress(position, total, str(path), status))
                _check_cancel(cancel)
                # Only complete, non-cancelled traversals establish that files disappeared.
                with db:
                    for root in complete:
                        memberships = db.execute(
                            "SELECT f.id, f.path FROM files f JOIN root_files r ON r.file_id=f.id "
                            "WHERE r.root=?", (root,)
                        ).fetchall()
                        for membership in memberships:
                            if membership["path"] not in seen[root]:
                                db.execute("DELETE FROM root_files WHERE root=? AND file_id=?",
                                           (root, membership["id"]))
                    report.removed, thumbnails = self._drop_orphans(db)
                self._delete_thumbnails(thumbnails)
            except _Cancelled:
                report.cancelled = True
        return report

    @staticmethod
    def _associate(db: sqlite3.Connection, file_id: int, path: str, roots: Sequence[str]) -> None:
        db.executemany(
            "INSERT OR IGNORE INTO root_files(root, file_id) VALUES(?, ?)",
            [(root, file_id) for root in roots if _inside(path, root)],
        )

    def _replace_file(
        self, db: sqlite3.Connection, path: str, size: int, mtime_ns: int,
        digest: str | None, status: str, error: str | None,
        pages: Sequence[tuple[int, int, int, str, str, str]], roots: Sequence[str],
    ) -> list[str]:
        with db:
            db.execute("""
                INSERT INTO files(path,size,mtime_ns,sha256,signature,status,error)
                VALUES(?,?,?,?,?,?,?)
                ON CONFLICT(path) DO UPDATE SET size=excluded.size,
                    mtime_ns=excluded.mtime_ns, sha256=excluded.sha256,
                    signature=excluded.signature, status=excluded.status, error=excluded.error
            """, (path, size, mtime_ns, digest, self.signature, status, error))
            file_id = db.execute("SELECT id FROM files WHERE path=?", (path,)).fetchone()[0]
            old_thumbnails = [row[0] for row in db.execute(
                "SELECT thumbnail_path FROM pages WHERE file_id=?", (file_id,)
            )]
            db.execute("DELETE FROM pages WHERE file_id=?", (file_id,))
            db.executemany(
                "INSERT INTO pages(file_id,page,width,height,descriptor,pixel_digest,thumbnail_path) "
                "VALUES(?,?,?,?,?,?,?)", [(file_id, *page) for page in pages],
            )
            self._associate(db, file_id, path, roots)
        return old_thumbnails

    def search(
        self, query_path: Path, limit: int = 100, min_score: float = 70,
        cancel: threading.Event | None = None,
    ) -> list[SearchResult]:
        if limit < 1:
            return []
        if not 0 <= min_score <= 100:
            raise ValueError("相似度阈值必须在 0 到 100 之间")
        query = _path(query_path)
        _check_cancel(cancel)
        query_stat = query.stat()
        digest = _sha256(query, cancel)
        query_descriptor = None
        query_pixels = None
        render_error = None
        preview_generator = render_pages(query, max_pages=1)
        try:
            rendered = next(preview_generator)
            query_descriptor = features.describe(rendered.image)
            query_pixels = features.pixel_digest(rendered.image)
        except (RenderError, StopIteration, OSError, ValueError) as error:
            render_error = error
        finally:
            preview_generator.close()
        _check_cancel(cancel)
        _check_query_snapshot(query, query_stat)
        results: dict[int, SearchResult] = {}
        with self._connection() as db:
            rows = db.execute("""
                SELECT f.*, p.page, p.width, p.height, p.descriptor,
                       p.pixel_digest, p.thumbnail_path
                FROM files f LEFT JOIN pages p ON p.file_id=f.id
                ORDER BY f.id, p.page
            """)
            for row in rows:
                _check_cancel(cancel)
                try:
                    current_stat = Path(row["path"]).stat()
                except OSError:
                    continue
                if current_stat.st_size != row["size"] or current_stat.st_mtime_ns != row["mtime_ns"]:
                    # Do not report an obsolete index fingerprint as a current match.
                    continue
                if row["sha256"] == digest:
                    score, match_type = 100.0, "identical"
                elif query_pixels and row["pixel_digest"] == query_pixels:
                    score, match_type = 100.0, "preview"
                elif query_descriptor and row["descriptor"]:
                    score = float(features.similarity(query_descriptor, json.loads(row["descriptor"])))
                    match_type = "similar"
                else:
                    continue
                if score < min_score:
                    continue
                found = SearchResult(
                    file_id=row["id"], path=row["path"], score=round(score, 1),
                    match_type=match_type, page=row["page"] or 1,
                    thumbnail_path=row["thumbnail_path"] or "",
                    width=row["width"] or 0, height=row["height"] or 0,
                    file_size=row["size"], modified_ns=row["mtime_ns"],
                )
                old = results.get(found.file_id)
                priority = {"identical": 2, "preview": 1, "similar": 0}
                if old is None or (found.score, priority[found.match_type]) > \
                        (old.score, priority[old.match_type]):
                    results[found.file_id] = found
        _check_cancel(cancel)
        _check_query_snapshot(query, query_stat)
        if render_error is not None and not results:
            raise RenderError(f"无法读取查询图片，也未找到内容完全相同的文件：{render_error}")
        priority = {"identical": 0, "preview": 1, "similar": 2}
        return sorted(results.values(), key=lambda r: (-r.score, priority[r.match_type], r.path))[:limit]

    def close(self) -> None:
        """Connections are scoped to operations; supplied for UI lifecycle symmetry."""
