#!/usr/bin/env python3
"""Import reviewed APLYD additions: dry-run first, no overwrites, no activation.

Default installation vendors a hash-bound enforcement core. Full docs remain in
the original distribution; --payload full vendors reference docs too. Existing
repository-managed records are never proposed for replacement by blank starters.
"""
from __future__ import annotations
import argparse
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import sys
sys.dont_write_bytecode = True
if sys.version_info < (3, 11):
    raise SystemExit('BLOCKED: Python 3.11 or later is required')
from engctl import ConfigurationError, PACKAGE, VERSION, read_json, safe_path, verify_package, sha256_file


@dataclass(frozen=True)
class Item:
    target: Path
    content: bytes
    action: str


def encoded(value: object) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + '\n').encode()


def payload_files(package: Path, mode: str) -> dict[str, bytes]:
    manifest = read_json(package / 'PACKAGE_MANIFEST.json')
    available = {item['path']: item for item in manifest['files']}
    if mode == 'full':
        names = set(available) - {'AGENTS.md'}
    else:
        names = {'CODING_STANDARD.md', 'VERSION.md', 'SOURCES.md', '06-enforcement/README.md'}
        names |= {name for name in available if name.startswith(('06-enforcement/scripts/', '06-enforcement/schemas/'))}
        names.add('06-enforcement/controls.json')
        names |= {item['document'] for item in read_json(package / '06-enforcement/controls.json')['controls']}
        names |= {'01-governance/cross-bundle-contract.md', '04-agent-instructions/engineering-agent-rules.md'}
    files = {name: safe_path(package, name).read_bytes() for name in sorted(names)}
    source_digest = sha256_file(package / 'PACKAGE_MANIFEST.json')
    files['DISTRIBUTION_REFERENCE.json'] = encoded({
        'version': VERSION, 'payload_kind': mode, 'source_manifest_sha256': source_digest,
        'source_archive': f'APLYD-Coding-Engineering-Package-v{VERSION}.zip',
        'unvendored_documents': [available[name] for name in sorted(set(available)-names) if name.endswith('.md')],
        'note': 'Resolve unvendored documentation in the original archive by the recorded file hash; no latest-document substitution.'})
    installed = {'version': VERSION, 'payload_kind': mode, 'source_manifest_sha256': source_digest,
                 'files': [{'path': n, 'sha256': hashlib.sha256(b).hexdigest(), 'bytes': len(b)} for n,b in sorted(files.items())]}
    files['PACKAGE_MANIFEST.json'] = encoded(installed)
    files['SHA256SUMS'] = ''.join(f'{hashlib.sha256(b).hexdigest()}  {n}\n' for n,b in sorted(files.items())).encode()
    return files


def build_plan(package: Path, repo: Path, mode: str = 'core') -> list[Item]:
    integrity = verify_package(package)
    if integrity['errors']:
        raise ConfigurationError('Package integrity failed: ' + '; '.join(integrity['errors'][:5]))
    if repo == package or repo.is_relative_to(package) or package.is_relative_to(repo):
        raise ConfigurationError('Keep the distribution and target repository in separate directory trees')
    proposed: list[tuple[str, bytes, str]] = []
    for name, data in payload_files(package, mode).items():
        proposed.append((f'.aplyd/engineering/{VERSION}/{name}', data, 'immutable'))
    starter = package / '07-starter-kit/repository-template'
    for source in sorted(starter.rglob('*')):
        if source.is_file():
            if source.is_symlink():
                raise ConfigurationError('Symlink in starter')
            name = source.relative_to(starter).as_posix()
            owned = name.endswith('.json') and name.startswith(('.engineering/', '.governance/', '.aplyd/'))
            proposed.append((name, source.read_bytes(), 'record' if owned else 'ordinary'))
    proposed.extend([
        ('AGENTS.md', (package/'AGENTS.md').read_bytes(), 'ordinary'),
        ('.github/PULL_REQUEST_TEMPLATE.md', (package/'PR_CHECKLIST.md').read_bytes(), 'ordinary'),
        (f'.aplyd/proposals/{VERSION}/.github/workflows/engineering.yml',
         (package/'06-enforcement/github-actions/engineering.yml').read_bytes(), 'workflow')])
    configured_repo = safe_path(repo, '.engineering/repository.json').exists()
    plan: list[Item] = []
    destinations: set[Path] = set()
    for relative, data, kind in proposed:
        target = safe_path(repo, relative)
        if target.exists() and not target.is_file():
            raise ConfigurationError(f'Destination is not a regular file: {relative}')
        action = 'ADD'
        if kind == 'record' and (target.exists() or configured_repo):
            # Even on major-version adoption, blank drafts are not config upgrades.
            action = 'KEEP_REPOSITORY_RECORD'
        elif target.exists():
            if target.read_bytes() == data:
                action = 'UNCHANGED'
            elif kind == 'immutable':
                raise ConfigurationError(f'Immutable installed file differs: {relative}')
            else:
                if not relative.startswith(f'.aplyd/proposals/{VERSION}/'):
                    relative = f'.aplyd/proposals/{VERSION}/{relative}'
                target = safe_path(repo, relative)
                if target.exists():
                    if not target.is_file() or target.read_bytes() != data:
                        raise ConfigurationError(f'A different proposal already exists: {relative}')
                    action = 'PROPOSAL_UNCHANGED'
                else:
                    action = 'STAGE_CONFLICT'
        if kind == 'workflow' and action == 'ADD':
            action = 'STAGE_WORKFLOW'
        if target in destinations:
            raise ConfigurationError(f'Duplicate destination: {relative}')
        destinations.add(target)
        parent = target.parent
        while parent != repo:
            if parent.exists() and not parent.is_dir():
                raise ConfigurationError(f'Parent is not a directory: {relative}')
            parent = parent.parent
        plan.append(Item(target, data, action))
    return plan


def apply_plan(plan: list[Item], repo: Path) -> int:
    created: list[Path] = []
    try:
        for item in plan:
            if item.action in {'UNCHANGED', 'PROPOSAL_UNCHANGED', 'KEEP_REPOSITORY_RECORD'}:
                continue
            safe_path(repo, item.target.relative_to(repo).as_posix())
            item.target.parent.mkdir(parents=True, exist_ok=True)
            with item.target.open('xb') as output:
                created.append(item.target)
                output.write(item.content)
    except (OSError, ConfigurationError):
        for path in reversed(created):
            try:
                path.unlink()
            except OSError:
                pass
        raise
    return len(created)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', required=True, type=Path)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--payload', choices=['core', 'full'], default='core')
    args = parser.parse_args()
    try:
        repo = args.repo.expanduser().resolve()
        if not repo.is_dir():
            raise ConfigurationError('Target directory must already exist')
        plan = build_plan(PACKAGE, repo, args.payload)
        count = apply_plan(plan, repo) if args.apply else 0
        print(json.dumps({'mode':'APPLIED_DRAFT_NOT_ACTIVATED' if args.apply else 'DRY_RUN_NO_WRITES',
                          'payload':args.payload, 'created_files':count,
                          'items':[{'action':i.action,'path':i.target.relative_to(repo).as_posix()} for i in plan],
                          'next_state':'Review conflicts and v1-to-v2 migration; configure actual bindings; approve server controls separately.'}, indent=2))
        return 0
    except (ConfigurationError, OSError, ValueError) as exc:
        print(json.dumps({'state':'BLOCKED_NO_OVERWRITE','error':str(exc)}, indent=2))
        return 2

if __name__ == '__main__':
    raise SystemExit(main())
