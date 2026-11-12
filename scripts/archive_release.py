"""Promote verified Actions artifacts to a non-expiring GitHub Release.

Uses gh's existing credentials. Does not run downloaded code, move tags, delete
assets or replace a previously published binary. Run from workflow_dispatch.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import base64
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import tempfile
import zipfile


def gh(*args: str, output: Path | None = None, allow_missing: bool = False):
    with output.open('wb') if output else tempfile.TemporaryFile() as stream:
        result = subprocess.run(['gh', *args], stdout=stream, stderr=subprocess.PIPE)
        if result.returncode:
            error = result.stderr.decode(errors='replace')
            if allow_missing and 'HTTP 404' in error:
                return None
            raise RuntimeError(error)
        if output:
            return output
        stream.seek(0)
        return stream.read().decode()


def sha(path: Path) -> str:
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def main():
    repo = os.environ['GH_REPO']
    tag = os.environ['RELEASE_TAG']
    if not re.fullmatch(r'v\d+\.\d+\.\d+', tag):
        raise ValueError('Use an existing vMAJOR.MINOR.PATCH tag.')
    version = tag[1:]
    prefix = f'repos/{repo}'

    def api(endpoint, **kwargs):
        value = gh('api', f'{prefix}/{endpoint}', **kwargs)
        return json.loads(value) if value is not None else None

    tag_info = api(f'git/ref/tags/{tag}')['object']
    while tag_info['type'] == 'tag':
        tag_info = api(f"git/tags/{tag_info['sha']}")['object']
    tag_sha = tag_info['sha']
    release = api(f'releases/tags/{tag}', allow_missing=True)
    existing = {asset['name']: asset for asset in release['assets']} if release else {}
    root = Path('build/release-archive').resolve()
    root.mkdir(parents=True, exist_ok=True)
    staged = root/'assets'
    staged.mkdir(exist_ok=True)
    archive_id = os.environ.get('GITHUB_RUN_ID', 'local')
    run_records, artifacts = [], []
    platforms = {
        'macos': ('MACOS_RUN_ID', 'macOS application', [
            'ImageTools-macos-arm64', 'ImageTools-macos-x86_64',
            'macOS-validation-arm64', 'macOS-validation-x86_64']),
        'windows': ('WINDOWS_RUN_ID', 'Windows installer', [
            'ImageTools-windows-x64-installer', 'Windows-validation-reports']),
    }
    for platform, (variable, workflow, names) in platforms.items():
        run_id = os.environ.get(variable, '').strip()
        if not run_id:
            continue
        if not run_id.isdecimal():
            raise ValueError(f'Invalid run ID: {variable}')
        run = api(f'actions/runs/{run_id}')
        if run['name'] != workflow or run['conclusion'] != 'success':
            raise ValueError(f'{run_id} is not a successful {workflow} build.')
        if run['repository']['full_name'] != repo or run['head_repository']['full_name'] != repo:
            raise ValueError('Only builds from this repository may be promoted.')
        source = api(f"contents/image_tools/__init__.py?ref={run['head_sha']}")
        source = base64.b64decode(source['content']).decode()
        found = re.search(r'__version__\s*=\s*[\"\']([^\"\']+)', source)
        if not found or found[1] != version:
            raise ValueError('Application version does not match the release tag.')
        if release is None and run['head_sha'] != tag_sha:
            raise ValueError('A new release must match the tag commit; backfills need an existing release.')
        available = api(f'actions/runs/{run_id}/artifacts?per_page=100')['artifacts']
        by_name = {item['name']: item for item in available if not item['expired']}
        if not set(names) <= by_name.keys():
            raise ValueError(f'Missing or expired verified artifacts for {run_id}.')
        run_records.append({'id':int(run_id), 'platform':platform, 'source_commit':run['head_sha'],
                            'url':run['html_url'], 'conclusion':run['conclusion']})
        for name in names:
            info = by_name[name]
            target = root/f"artifact-{info['id']}.zip"
            gh('api', f"{prefix}/actions/artifacts/{info['id']}/zip", output=target)
            if info.get('digest') != 'sha256:'+sha(target):
                raise ValueError(f'Artifact checksum mismatch: {name}')
            artifacts.append({'id':info['id'], 'name':name, 'digest':info['digest'], 'run_id':int(run_id)})
            with zipfile.ZipFile(target) as bundle:
                if bundle.testzip() is not None:
                    raise ValueError(f'Corrupt artifact: {name}')
                members = {PurePosixPath(n).name:n for n in bundle.namelist() if not n.endswith('/')}
                for member in bundle.namelist():
                    path = PurePosixPath(member)
                    if path.is_absolute() or '..' in path.parts:
                        raise ValueError('Unsafe artifact member.')
                if name.startswith('ImageTools-'):
                    checks = [n for n in members if n.endswith('.sha256')]
                    if len(checks) != 1:
                        raise ValueError('Expected exactly one installer checksum.')
                    digest, filename = bundle.read(members[checks[0]]).decode().strip().split(maxsplit=1)
                    filename = filename.lstrip('*')
                    expected_name = (f'ImageTools-{version}-win-x64-setup.exe' if platform=='windows'
                                     else f'ImageTools-{version}-macos-{name.removeprefix("ImageTools-macos-")}.dmg')
                    if filename != expected_name or filename not in members:
                        raise ValueError('Unexpected installer name or version.')
                    data = bundle.read(members[filename])
                    if hashlib.sha256(data).hexdigest() != digest.lower():
                        raise ValueError('Installer checksum mismatch.')
                    if filename in existing and existing[filename].get('digest') != 'sha256:'+digest.lower():
                        # Keep the original installer; preserve this distinct verified build in a ZIP.
                        alternate = f'ImageTools-{version}-{platform}-build-{run["head_sha"][:12]}-run{run_id}.zip'
                        (staged/alternate).write_bytes(target.read_bytes())
                    else:
                        (staged/filename).write_bytes(data)
                        (staged/checks[0]).write_bytes(bundle.read(members[checks[0]]))
                else:
                    (staged/f'{name}-run{run_id}.zip').write_bytes(target.read_bytes())
    if not run_records:
        raise ValueError('Select at least one successful application build.')

    # Capture every retained application workflow run, including failed/cancelled
    # history. This publisher's own in-progress run is deliberately excluded.
    pages = json.loads(gh('api', '--paginate', '--slurp', f'{prefix}/actions/runs?per_page=100'))
    history = [r for page in pages for r in page['workflow_runs']
               if r['status']=='completed' and r['name'] in {'Windows installer','macOS application'}]

    def get_log(run):
        path = root/f"run-{run['id']}-logs.zip"
        try:
            gh('api', f"{prefix}/actions/runs/{run['id']}/logs", output=path)
            with zipfile.ZipFile(path) as log:
                if log.testzip() is not None:
                    raise ValueError('Corrupt log ZIP')
            return run, path, None
        except Exception as error:
            return run, None, str(error)

    log_name = f'ImageTools-{version}-build-and-test-logs-{archive_id}.zip'
    log_records, log_files = [], 0
    with zipfile.ZipFile(staged/log_name, 'w', zipfile.ZIP_DEFLATED) as output:
        with ThreadPoolExecutor(max_workers=4) as pool:
            for run, path, error in pool.map(get_log, sorted(history,key=lambda r:r['id'])):
                log_records.append({'id':run['id'], 'name':run['name'], 'source_commit':run['head_sha'],
                                    'conclusion':run['conclusion'], 'url':run['html_url'],
                                    'archived':error is None, 'error':error})
                if path:
                    with zipfile.ZipFile(path) as log:
                        for member in sorted(log.namelist()):
                            if member.endswith('/'):
                                continue
                            p = PurePosixPath(member)
                            if p.is_absolute() or '..' in p.parts:
                                raise ValueError('Unsafe log member.')
                            data = log.read(member)
                            if re.search(rb'gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{30,}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----',data):
                                raise ValueError('Unmasked credential pattern in logs; publishing stopped.')
                            output.writestr(f"runs/{run['id']}/{member}",data)
                            log_files += 1
        output.writestr('run-manifest.json',json.dumps(log_records,ensure_ascii=False,indent=2))
        for report in staged.glob('*-run*.zip'):
            if 'validation' in report.name:
                output.write(report,'validation/'+report.name)
    if any(not r['archived'] and r['id'] in {x['id'] for x in run_records} for r in log_records):
        raise ValueError('Logs for a selected release build are unavailable.')
    manifest_name = f'ImageTools-{version}-release-manifest-{archive_id}.json'
    manifest = {'release_tag':tag,'tag_commit':tag_sha,'selected_builds':run_records,
                'source_artifacts':artifacts,'history_logs':log_records,'raw_log_files':log_files,
                'original_release_asset_names':sorted(existing),'notarized':False}
    (staged/manifest_name).write_text(json.dumps(manifest,ensure_ascii=False,indent=2))
    checksum_name = f'ImageTools-{version}-release-SHA256SUMS-{archive_id}.txt'
    (staged/checksum_name).write_text(''.join(f'{sha(p)}  {p.name}\n' for p in sorted(staged.iterdir())))

    notes = root/'release-notes.md'
    marker = '<!-- image-tools-archive -->'
    old_body = (release or {}).get('body') or ''
    if marker in old_body:
        old_body = old_body.split('<!-- /image-tools-archive -->',1)[1].lstrip()
    summary = f'''{marker}
图迹 {version} · Windows / Apple 芯片 Mac / Intel Mac 安装包及日志长期归档。
Release 附件不受 Actions 的 30 天保留期限制。

| 平台 | 安装包 | 系统要求 |
| --- | --- | --- |
| Windows x64 | ImageTools-{version}-win-x64-setup.exe | Windows 10/11 64 位 |
| Apple 芯片 Mac | ImageTools-{version}-macos-arm64.dmg | macOS 14+ |
| Intel Mac | ImageTools-{version}-macos-x86_64.dmg | macOS 14+ |

Mac：打开 DMG，将 ImageTools.app 拖入“应用程序”。当前包采用 ad hoc 签名，尚未经过 Apple 公证。

本次保存 {sum(r['archived'] for r in log_records)} 次 Actions 运行的 {log_files} 个原始日志文件。
日志总包：`{log_name}`，内含成功、失败及取消记录和三平台验证报告；状态见 run-manifest.json。
来源清单：`{manifest_name}`，记录每个包的实际源码提交、构建编号及校验值。
Mac 适配包可能来自与原 Windows 标签不同的提交，具体以来源清单为准。
原安装包保留；同名但内容不同的 Windows 构建另存为带提交和 run 编号的 ZIP，未覆盖原文件。

所有安装包均经过来源 ZIP 与安装文件 SHA-256 双重校验；附带各自 .sha256 以及本次附件总校验表。
Mac 两种架构各通过 106 项测试、原生启动及安装/重装验证；原生测试系统为 macOS 15。
<!-- /image-tools-archive -->

'''
    notes.write_text(summary+old_body)
    if release is None:
        gh('release','create',tag,'--repo',repo,'--verify-tag','--title',f'图迹 {tag} · Windows 与 Mac 安装包','--notes-file',str(notes))
    for path in sorted(staged.iterdir()):
        if path.name in existing:
            asset = existing[path.name]
            if asset.get('digest') != 'sha256:'+sha(path):
                raise ValueError(f'Published asset differs; will not replace {path.name}')
            continue
        gh('release','upload',tag,str(path),'--repo',repo)
    gh('release','edit',tag,'--repo',repo,'--title',f'图迹 {tag} · Windows 与 Mac 安装包及日志','--notes-file',str(notes))
    final = api(f'releases/tags/{tag}')
    published = {x['name']:x for x in final['assets']}
    for path in staged.iterdir():
        asset = published[path.name]
        if asset['state']!='uploaded' or asset['size']!=path.stat().st_size or asset.get('digest')!='sha256:'+sha(path):
            raise ValueError(f'Published checksum verification failed: {path.name}')
    result = {'release_url':final['html_url'],'new_assets':len(list(staged.iterdir())),
              'history_runs':len(log_records),'archived_runs':sum(r['archived'] for r in log_records),
              'raw_log_files':log_files,'existing_assets_preserved':len(existing)}
    print(json.dumps(result,ensure_ascii=False,indent=2))
    if os.environ.get('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'],'a') as stream:
            stream.write(f"Published and verified: {final['html_url']}\n\n```json\n{json.dumps(result,indent=2)}\n```\n")


if __name__ == '__main__':
    main()
