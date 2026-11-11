from pathlib import Path
import os
import shutil
import sqlite3
import threading

import pytest
from PIL import Image, ImageDraw

from image_tools.library import Library
from image_tools.rendering import RenderError


@pytest.fixture
def library(tmp_path):
    return Library(tmp_path / "index" / "library.sqlite3", tmp_path / "index" / "thumbnails")


@pytest.fixture
def picture():
    image = Image.new("RGB", (240, 180), "#e3e8dd")
    draw = ImageDraw.Draw(image)
    draw.rectangle((13, 20, 82, 151), fill="#164aac")
    draw.ellipse((97, 34, 221, 160), fill="#da833d")
    draw.polygon([(87, 10), (139, 31), (75, 81)], fill="#308b67")
    draw.line((5, 157, 210, 11), fill="#ffffff", width=6)
    return image


def test_scan_incremental_exact_and_preview_matches(library, picture, tmp_path):
    root = tmp_path / "images"
    root.mkdir()
    original = root / "source.png"
    picture.save(original)
    shutil.copyfile(original, root / "copy.png")
    picture.save(root / "same_pixels.bmp")
    picture.resize((120, 90)).save(root / "resized.jpg", quality=88)
    (root / "ignored.txt").write_text("not an image")
    first = library.scan([root])
    assert (first.indexed, first.failed, first.unchanged) == (4, 0, 0)
    assert library.stats().files == 4
    assert library.stats().pages == 4
    assert all(Path(result.thumbnail_path).is_file() for result in library.search(original))
    results = {Path(r.path).name: r for r in library.search(original, min_score=70)}
    assert results["source.png"].match_type == "identical"
    assert results["copy.png"].match_type == "identical"
    assert results["same_pixels.bmp"].match_type == "preview"
    assert results["resized.jpg"].match_type == "similar"
    assert results["resized.jpg"].score > 85
    second = library.scan([root])
    assert (second.indexed, second.unchanged) == (0, 4)


def test_changed_and_deleted_files_are_updated(library, picture, tmp_path):
    root = tmp_path / "images"
    root.mkdir()
    source, deleted = root / "source.png", root / "deleted.png"
    picture.save(source)
    picture.save(deleted)
    library.scan([root])
    deleted.unlink()
    Image.new("RGB", (90, 90), "black").save(source)
    # Stale entries must not claim exact matches before rescan.
    query = tmp_path / "query.png"
    picture.save(query)
    assert library.search(query, min_score=100) == []
    result = library.scan([root])
    assert (result.indexed, result.removed, result.failed) == (1, 1, 0)
    assert library.stats().files == 1
    assert library.search(source)[0].match_type == "identical"


def test_overlapping_roots_do_not_duplicate_and_remove_only_index(library, picture, tmp_path):
    root = tmp_path / "images"
    nested = root / "nested"
    nested.mkdir(parents=True)
    outside = root / "outside.png"
    inside = nested / "inside.png"
    picture.save(outside)
    picture.save(inside)
    result = library.scan([root, nested, root])
    assert result.indexed == 2
    assert len(library.roots()) == 2
    assert len(library.search(inside)) == 2
    library.remove_root(root)
    assert library.stats().files == 1
    assert [Path(path) for path in library.roots()] == [nested.resolve()]
    assert inside.is_file() and outside.is_file()
    library.remove_root(nested)
    assert library.stats().files == 0
    assert inside.is_file() and outside.is_file()


def test_failed_render_removes_stale_descriptors_and_can_retry(library, picture, tmp_path):
    root = tmp_path / "images"
    root.mkdir()
    source = root / "source.png"
    picture.save(source)
    library.scan([root])
    source.write_bytes(b"broken image")
    failed = library.scan([root])
    assert failed.failed == 1
    assert library.stats().files == 0
    assert library.stats().pages == 0
    assert library.stats().failed == 1
    assert Path(library.failures()[0][0]) == source.resolve()
    assert library.scan([root]).failed == 1  # Failed files are retried even if unchanged.
    picture.save(source)
    assert library.scan([root]).indexed == 1
    assert library.failures() == []


def test_cancelled_scan_does_not_purge_unseen_entries(library, picture, tmp_path):
    root = tmp_path / "images"
    root.mkdir()
    source = root / "source.png"
    picture.save(source)
    library.scan([root])
    source.unlink()
    cancel = threading.Event()

    def progress(_):
        cancel.set()

    report = library.scan([root], progress=progress, cancel=cancel)
    assert report.cancelled
    assert report.removed == 0
    assert library.stats().files == 1


def test_disconnected_and_unscanned_roots_keep_index(library, picture, tmp_path):
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    picture.save(first / "one.png")
    picture.save(second / "two.png")
    library.scan([first, second])
    first.rename(tmp_path / "disconnected")
    report = library.scan([first])
    assert report.failed == 1 and report.removed == 0
    assert library.stats().files == 2
    report = library.scan([second])
    assert report.unchanged == 1 and library.stats().files == 2


def test_incomplete_traversal_does_not_purge(library, picture, tmp_path, monkeypatch):
    root = tmp_path / "images"
    root.mkdir()
    picture.save(root / "source.png")
    library.scan([root])

    def incomplete_walk(path, followlinks=False, onerror=None):
        onerror(PermissionError(13, "Access denied", str(root)))
        yield str(root), [], []

    monkeypatch.setattr("image_tools.library.os.walk", incomplete_walk)
    report = library.scan([root])
    assert report.failed == 1
    assert report.removed == 0
    assert library.stats().files == 1


def test_cache_inside_library_is_not_recursively_indexed(picture, tmp_path):
    root = tmp_path / "images"
    root.mkdir()
    picture.save(root / "source.png")
    library = Library(root / "index.sqlite3", root / "cache")
    assert library.scan([root]).indexed == 1
    assert library.scan([root]).unchanged == 1
    assert library.stats().files == 1


def test_multi_page_document_returns_best_page_once(library, picture, tmp_path):
    root = tmp_path / "documents"
    root.mkdir()
    pdf = root / "design.pdf"
    first = Image.new("RGB", picture.size, "white")
    first.save(pdf, save_all=True, append_images=[picture], resolution=72)
    query = tmp_path / "query.png"
    picture.save(query)
    report = library.scan([root])
    assert report.indexed == 1 and report.failed == 0
    assert library.stats().pages == 2
    results = library.search(query, min_score=75)
    assert len(results) == 1
    assert results[0].page == 2
    assert results[0].score > 85


def test_page_limit_change_reindexes_documents(picture, tmp_path):
    root = tmp_path / "documents"
    root.mkdir()
    first = Image.new("RGB", picture.size, "white")
    first.save(root / "design.pdf", save_all=True, append_images=[picture])
    database = tmp_path / "database.sqlite3"
    cache = tmp_path / "cache"
    one = Library(database, cache, max_pages=1)
    assert one.scan([root]).indexed == 1
    assert one.stats().pages == 1
    two = Library(database, cache, max_pages=2)
    assert two.scan([root]).indexed == 1
    assert two.stats().pages == 2


def test_corrupt_query_has_clear_error_or_exact_only_result(library, tmp_path):
    query = tmp_path / "query.png"
    query.write_bytes(b"broken image")
    with pytest.raises(RenderError, match="无法读取查询图片"):
        library.search(query)
    root = tmp_path / "images"
    root.mkdir()
    shutil.copyfile(query, root / "copy.png")
    library.scan([root])
    results = library.search(query)
    assert len(results) == 1
    assert results[0].match_type == "identical"
    assert results[0].thumbnail_path == ""


def test_missing_thumbnail_is_regenerated(library, picture, tmp_path):
    root = tmp_path / "images"
    root.mkdir()
    query = root / "source.png"
    picture.save(query)
    library.scan([root])
    thumbnail = Path(library.search(query)[0].thumbnail_path)
    thumbnail.unlink()
    assert library.scan([root]).indexed == 1
    assert Path(library.search(query)[0].thumbnail_path).is_file()


def test_changed_query_cannot_return_stale_exact_match(library, picture, tmp_path, monkeypatch):
    from image_tools import library as library_module

    root = tmp_path / "images"
    root.mkdir()
    source = root / "original.png"
    picture.save(source)
    library.scan([root])
    query = tmp_path / "query.png"
    shutil.copyfile(source, query)
    original_renderer = library_module.render_pages

    def edit_before_preview(path, max_pages=20):
        Image.new("RGB", (50, 50), "black").save(path)
        yield from original_renderer(path, max_pages)

    monkeypatch.setattr(library_module, "render_pages", edit_before_preview)
    with pytest.raises(RenderError, match="查询文件在检索期间发生变化"):
        library.search(query)


def test_query_changed_during_comparison_must_be_retried(library, picture, tmp_path, monkeypatch):
    from image_tools import features

    root = tmp_path / "images"
    root.mkdir()
    picture.save(root / "source.png")
    library.scan([root])
    query = tmp_path / "query.jpg"
    picture.resize((120, 90)).save(query)
    compare = features.similarity

    def compare_then_edit(a, b):
        score = compare(a, b)
        Image.new("RGB", (50, 50), "black").save(query)
        return score

    monkeypatch.setattr(features, "similarity", compare_then_edit)
    with pytest.raises(RenderError, match="查询文件在检索期间发生变化"):
        library.search(query)


def test_search_cancellation_during_query_render_with_empty_index(library, picture, tmp_path, monkeypatch):
    from image_tools import library as library_module

    query = tmp_path / "query.png"
    picture.save(query)
    cancel = threading.Event()
    original_renderer = library_module.render_pages

    def render_then_cancel(path, max_pages=20):
        for page in original_renderer(path, max_pages):
            cancel.set()
            yield page

    monkeypatch.setattr(library_module, "render_pages", render_then_cancel)
    with pytest.raises(InterruptedError, match="已取消"):
        library.search(query, cancel=cancel)


@pytest.mark.parametrize("render_fails", [False, True])
def test_source_replaced_during_scan_does_not_keep_stale_hash(
    library, picture, tmp_path, monkeypatch, render_fails,
):
    from image_tools import library as library_module

    root = tmp_path / "images"
    root.mkdir()
    source = root / "source.png"
    picture.save(source)
    query = tmp_path / "query.png"
    shutil.copyfile(source, query)
    before = source.stat()
    # Editors often replace files atomically. Reuse size and modification time
    # to exercise identity/ctime checking, not just the existing metadata guard.
    replacement = tmp_path / "replacement.png"
    replacement.write_bytes(b"x" * before.st_size)
    os.utime(replacement, ns=(before.st_atime_ns, before.st_mtime_ns))
    original_renderer = library_module.render_pages

    def replace_source(path, max_pages=20):
        replacement.replace(path)
        if render_fails:
            raise RenderError("预览失败")
        # Simulate an already-decoded old preview while the file is replaced.
        yield from original_renderer(query, max_pages)

    monkeypatch.setattr(library_module, "render_pages", replace_source)
    report = library.scan([root])
    assert report.indexed == 0 and report.failed == 1
    assert library.stats().pages == 0
    monkeypatch.setattr(library_module, "render_pages", original_renderer)
    assert library.search(query) == []


def test_scan_skips_windows_junction_directories(library, picture, tmp_path, monkeypatch):
    root = tmp_path / "images"
    junction = root / "junction"
    junction.mkdir(parents=True)
    picture.save(root / "source.png")
    picture.save(junction / "outside-or-cycle.png")
    # POSIX cannot create NTFS junctions. Simulate their platform detection;
    # os.walk(followlinks=False) by itself does not exclude junctions on Windows.
    monkeypatch.setattr(Path, "is_junction", lambda path: path == junction)
    report = library.scan([root])
    assert report.indexed == 1 and report.failed == 0
    assert library.stats().files == 1


@pytest.mark.parametrize("version", [1, 2])
def test_query_matches_individual_artboard_and_retains_label_after_reopen(tmp_path, artboard_factory, version):
    source, exports = artboard_factory(version=version)
    root = tmp_path / "sources"
    root.mkdir()
    source = source.rename(root / source.name)
    query = tmp_path / "board-export.png"
    exports[1].save(query)
    library = Library(tmp_path / "index.sqlite3", tmp_path / "thumbs")
    report = library.scan([root])
    assert report.failed == 0 and report.indexed == 1
    assert library.stats().pages == 4
    results = library.search(query, min_score=95)
    assert len(results) == 1  # Best board per source file, not one result per board.
    assert Path(results[0].path) == source
    assert results[0].page_label == "画板 2：设计画板 2"
    assert results[0].width == exports[1].width
    assert results[0].height == exports[1].height
    reopened = Library(library.database_path, library.cache_dir)
    assert reopened.search(query, min_score=95)[0].page_label == results[0].page_label
    assert reopened.scan([root]).unchanged == 1


def test_old_database_migrates_and_only_photoshop_files_need_reindex(tmp_path, artboard_factory, monkeypatch):
    from image_tools import library as library_module

    source, exports = artboard_factory()
    root = tmp_path / "sources"
    root.mkdir()
    source = source.rename(root / source.name)
    photo = root / "photo.png"
    exports[0].save(photo)
    database, cache = tmp_path / "index.sqlite3", tmp_path / "thumbs"
    library = Library(database, cache)
    library.scan([root])
    source_bytes = source.read_bytes()
    source_key = os.path.normcase(str(source.resolve()))
    with sqlite3.connect(database) as db:
        # Reproduce v0.1.0's schema and single-preview Photoshop record.
        db.execute("DELETE FROM pages WHERE file_id=(SELECT id FROM files WHERE path=?) AND page>1", (source_key,))
        db.execute("UPDATE files SET signature=? WHERE path=?", (library.signature, source_key))
        db.execute("ALTER TABLE pages DROP COLUMN label")
    reopened = Library(database, cache)
    assert reopened.stats().files == 2
    assert [Path(path) for path in reopened.roots()] == [root]
    assert reopened.search(photo)[0].match_type == "identical"
    renderer = library_module.render_pages
    rendered_paths = []

    def track_render(path, max_pages=20):
        rendered_paths.append(path)
        yield from renderer(path, max_pages=max_pages)

    monkeypatch.setattr(library_module, "render_pages", track_render)
    report = reopened.scan([root])
    assert (report.indexed, report.unchanged, report.failed) == (1, 1, 0)
    assert rendered_paths == [source]
    assert reopened.stats().pages == 5
    assert reopened.scan([root]).unchanged == 2
    assert source.read_bytes() == source_bytes


def test_multi_artboard_search_checks_source_path_only_once(tmp_path, artboard_factory, monkeypatch):
    source, exports = artboard_factory()
    root = tmp_path / "sources"
    root.mkdir()
    source = source.rename(root / source.name)
    query = tmp_path / "query.png"
    exports[1].save(query)
    library = Library(tmp_path / "index.sqlite3", tmp_path / "thumbs")
    library.scan([root])
    original_stat = Path.stat
    calls = []

    def track_stat(path, *args, **kwargs):
        if path == source:
            calls.append(path)
        return original_stat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", track_stat)
    assert library.search(query, min_score=95)[0].page_label == "画板 2：设计画板 2"
    assert calls == [source]  # Important for network shares with many boards/pages.
