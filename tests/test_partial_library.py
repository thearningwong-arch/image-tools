from pathlib import Path
import sqlite3
import threading

import numpy as np
from PIL import Image
from psd_tools import PSDImage
from psd_tools.constants import Compression
import pytest

from image_tools import library as library_module, partial
from image_tools.library import Library
from image_tools.rendering import RenderError, find_ghostscript


def open_library(tmp_path):
    return Library(tmp_path / 'index.sqlite3', tmp_path / 'thumbs')


def records(database):
    with sqlite3.connect(database) as db:
        return {table: db.execute(f'SELECT * FROM {table} ORDER BY ' + ('1' if table == 'roots' else '1,2')).fetchall()
                for table in ('roots', 'files', 'pages', 'root_files')}


def make_query(tmp_path, image):
    query = tmp_path / '局部截图.jpg'
    image.crop((453, 299, 697, 503)).resize((383, 320)).save(query, quality=75)
    return query


def test_legacy_index_backfills_once_preserving_records_and_originals(tmp_path, detailed_picture, monkeypatch):
    root = tmp_path / '素材库'
    root.mkdir()
    source = root / '完整图片.png'
    detailed_picture.save(source)
    query = make_query(tmp_path, detailed_picture)
    library = open_library(tmp_path)
    assert library.scan([root]).indexed == 1
    original = source.read_bytes()
    with sqlite3.connect(library.database_path) as db:
        db.execute('DROP TABLE partial_pages')  # Real v0.1.1 schema, not a new empty index.
    old_records = records(library.database_path)
    thumbnails = {p: p.read_bytes() for p in library.cache_dir.rglob('*.jpg')}
    reopened = open_library(tmp_path)
    assert records(reopened.database_path) == old_records
    assert reopened.search(source)[0].match_type == 'identical'
    renderer = library_module.render_pages
    calls = []

    def track(path, max_pages=20):
        calls.append(Path(path))
        yield from renderer(path, max_pages=max_pages)

    monkeypatch.setattr(library_module, 'render_pages', track)
    result = reopened.search(query, mode='partial')[0]
    assert result.path == str(source) and result.match_type == 'partial'
    assert result.score > 90
    assert np.max(np.abs(np.array(result.match_region) - np.array((453/800, 299/600, 697/800, 503/600)))) < 0.015
    assert calls == [query, source]
    calls.clear()
    assert open_library(tmp_path).search(query, mode='partial')[0].match_region == result.match_region
    assert calls == [query]  # Reuse cache after reopening, without re-reading the source preview.
    assert reopened.scan([root]).unchanged == 1
    assert records(library.database_path) == old_records
    assert {p: p.read_bytes() for p in library.cache_dir.rglob('*.jpg')} == thumbnails
    assert source.read_bytes() == original
    preview = reopened.match_preview(result)
    assert preview.size == detailed_picture.size


@pytest.mark.parametrize('version', [1, 2])
@pytest.mark.parametrize('merged', [True, False])
def test_partial_screenshot_finds_matching_psd_psb_artboard(tmp_path, artboard_factory, version, merged):
    source, exports = artboard_factory(version=version, preview=merged)
    root = tmp_path / 'sources'
    root.mkdir()
    source = source.rename(root / source.name)
    crop = (32, 17, 147, 108)
    query = tmp_path / '截图.png'
    exports[1].crop(crop).resize((230, 182), Image.Resampling.LANCZOS).save(query)
    library = open_library(tmp_path)
    assert library.scan([root]).failed == 0
    results = library.search(query, mode='partial', min_score=85)
    assert len(results) == 1 and results[0].path == str(source)
    result = results[0]
    assert result.page_label == '画板 2：设计画板 2'
    assert np.max(np.abs(np.array(result.match_region) - np.array(crop) / (160, 120, 160, 120))) < 0.025
    assert library.match_preview(result).size == exports[1].size


def test_screenshot_finds_real_raster_photoshop_pdf_ai_and_postscript_sources(tmp_path, detailed_picture):
    root = tmp_path / 'documents'
    root.mkdir()
    detailed_picture.save(root / '完整图片.png')
    for extension, version in [('psd', 1), ('psb', 2)]:
        document = PSDImage.frompil(detailed_picture, compression=Compression.RAW)
        document._record.header.version = version
        document.save(root / f'源稿.{extension}')
    first = Image.new('RGB', detailed_picture.size, 'white')
    first.save(root / '画册.pdf', save_all=True, append_images=[detailed_picture], resolution=72)
    (root / 'PDF兼容.ai').write_bytes((root / '画册.pdf').read_bytes())
    expected = {'完整图片.png', '源稿.psd', '源稿.psb', '画册.pdf', 'PDF兼容.ai'}
    if find_ghostscript():
        detailed_picture.save(root / '旧稿.eps', 'EPS')
        detailed_picture.save(root / '旧版.ai', 'EPS')
        expected.update({'旧稿.eps', '旧版.ai'})
    query = make_query(tmp_path, detailed_picture)
    originals = {p: p.read_bytes() for p in root.iterdir()}
    library = open_library(tmp_path)
    assert library.scan([root]).failed == 0
    results = {Path(result.path).name: result for result in library.search(query, mode='partial', min_score=85)}
    assert set(results) == expected
    assert results['画册.pdf'].page == results['PDF兼容.ai'].page == 2
    for result in results.values():
        assert np.max(np.abs(np.array(result.match_region) - (np.array((453, 299, 697, 503)) / (800, 600, 800, 600)))) < 0.02
    assert {p: p.read_bytes() for p in root.iterdir()} == originals


def test_cancelled_legacy_backfill_rolls_back_without_losing_existing_index(tmp_path, detailed_picture, monkeypatch):
    root = tmp_path / 'sources'
    root.mkdir()
    source = root / 'source.tiff'
    detailed_picture.save(source, save_all=True, append_images=[Image.new('RGB', detailed_picture.size, 'white')])
    query = make_query(tmp_path, detailed_picture)
    library = open_library(tmp_path)
    library.scan([root])
    with sqlite3.connect(library.database_path) as db:
        db.execute('DELETE FROM partial_pages')
    old = records(library.database_path)
    prepare = partial.prepare
    cancel = threading.Event()
    source_pages = 0

    def cancel_on_source(image):
        nonlocal source_pages
        preview = prepare(image)
        if image.size == detailed_picture.size:
            source_pages += 1
            if source_pages == 2:
                cancel.set()  # Page one has already been inserted into the transaction.
        return preview

    monkeypatch.setattr(partial, 'prepare', cancel_on_source)
    with pytest.raises(InterruptedError, match='已取消'):
        library.search(query, mode='partial', cancel=cancel)
    assert records(library.database_path) == old
    assert source_pages == 2
    with sqlite3.connect(library.database_path) as db:
        assert db.execute('SELECT COUNT(*) FROM partial_pages').fetchone()[0] == 0
    assert library.search(source)[0].match_type == 'identical'


@pytest.mark.parametrize('change', ['query', 'source'])
def test_changed_file_during_partial_matching_does_not_return_stale_region(tmp_path, detailed_picture, monkeypatch, change):
    root = tmp_path / 'sources'
    root.mkdir()
    source = root / 'source.png'
    detailed_picture.save(source)
    query = make_query(tmp_path, detailed_picture)
    library = open_library(tmp_path)
    library.scan([root])
    locate = partial.locate

    def locate_then_change(*args, **kwargs):
        result = locate(*args, **kwargs)
        Image.new('RGB', (60, 60), 'black').save(query if change == 'query' else source)
        return result

    monkeypatch.setattr(partial, 'locate', locate_then_change)
    if change == 'query':
        with pytest.raises(RenderError, match='查询文件在检索期间发生变化'):
            library.search(query, mode='partial')
    else:
        assert library.search(query, mode='partial') == []


def test_cache_invalidates_on_rescan_and_cascades_on_removal(tmp_path, detailed_picture):
    root = tmp_path / 'sources'
    root.mkdir()
    source = root / 'source.png'
    detailed_picture.save(source)
    query = make_query(tmp_path, detailed_picture)
    library = open_library(tmp_path)
    library.scan([root])
    result = library.search(query, mode='partial')[0]
    Image.new('RGB', detailed_picture.size, 'white').save(source)
    assert library.search(query, mode='partial') == []
    assert library.scan([root]).indexed == 1
    assert library.search(query, mode='partial') == []
    with pytest.raises(RenderError, match='索引已更新'):
        library.match_preview(result)
    library.remove_root(root)
    with sqlite3.connect(library.database_path) as db:
        assert db.execute('SELECT COUNT(*) FROM partial_pages').fetchone()[0] == 0
        assert db.execute('PRAGMA foreign_key_check').fetchall() == []
    assert source.is_file()


def test_corrupt_cache_is_rebuilt_and_missing_source_keeps_index(tmp_path, detailed_picture):
    root = tmp_path / 'sources'
    root.mkdir()
    source = root / 'source.png'
    detailed_picture.save(source)
    query = make_query(tmp_path, detailed_picture)
    library = open_library(tmp_path)
    library.scan([root])
    with sqlite3.connect(library.database_path) as db:
        db.execute('UPDATE partial_pages SET data=?', (b'broken cache',))
    assert library.search(query, mode='partial')[0].match_region is not None
    old = records(library.database_path)
    source.rename(root / 'offline.png')
    assert library.search(query, mode='partial') == []
    assert records(library.database_path) == old


def test_partial_query_validation_and_limit(tmp_path, detailed_picture):
    root = tmp_path / 'sources'
    root.mkdir()
    for number in range(2):
        detailed_picture.save(root / f'{number}.png')
    query = make_query(tmp_path, detailed_picture)
    library = open_library(tmp_path)
    library.scan([root])
    assert len(library.search(query, mode='partial', limit=1)) == 1
    assert library.search(query, mode='partial', limit=0) == []
    with pytest.raises(ValueError, match='阈值'):
        library.search(query, mode='partial', min_score=101)
    with pytest.raises(ValueError, match='未知检索方式'):
        library.search(query, mode='unknown')
    query.write_bytes(b'not an image')
    with pytest.raises(RenderError, match='无法读取局部截图'):
        library.search(query, mode='partial')
    Image.new('RGB', (100, 100), 'white').save(query)
    with pytest.raises(RenderError, match='缺少可辨细节'):
        library.search(query, mode='partial')


def test_thin_catalog_image_does_not_break_other_partial_matches(tmp_path, detailed_picture):
    root = tmp_path / 'sources'
    root.mkdir()
    Image.new('RGB', (1, 2000), 'red').save(root / 'one-pixel-border.png')
    source = root / 'poster.png'
    detailed_picture.save(source)
    query = make_query(tmp_path, detailed_picture)
    library = open_library(tmp_path)
    report = library.scan([root])
    assert report.indexed == 2 and report.failed == 0
    assert [result.path for result in library.search(query, mode='partial')] == [str(source)]
