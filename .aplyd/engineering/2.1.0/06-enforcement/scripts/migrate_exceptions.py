#!/usr/bin/env python3
"""Propose a lossless v1 engineering-register merge; never overwrite or approve.

Use the selected canonical JSON target. For a QA CSV target first approve its
mapping/migration; this utility will not guess missing QA semantics or rewrite CSV.
"""
from __future__ import annotations
import argparse
import copy
import json
from pathlib import Path
import engctl as eng
from connect_bundles import encoded, write_proposal


def propose_migration(repo: Path) -> tuple[dict, dict[str, bytes]]:
    profile = eng.read_json(eng.safe_path(repo, '.engineering/repository.json'))
    config = profile['exception_register']
    if config['format'] != 'json' or config['path'] == '.engineering/exceptions.json':
        raise eng.ConfigurationError('Select the reviewed canonical JSON target first; native QA CSV is never rewritten automatically')
    target = eng.safe_path(repo, config['path'])
    current = eng.read_json(target) if target.exists() else {'schema_version':2,'exceptions':[]}
    source = eng.safe_path(repo, '.engineering/exceptions.json')
    old = eng.read_json(source)
    if not isinstance(old, dict) or old.get('schema_version') != 1 or not isinstance(old.get('exceptions'), list):
        raise eng.ConfigurationError('Expected the original schema-1 engineering exception register')
    schema = eng.read_json(eng.PACKAGE/'06-enforcement/schemas/exceptions.schema.json')
    issues = eng.schema_errors(current,schema,'target')
    if issues:
        raise eng.ConfigurationError('; '.join(issues))
    merged = copy.deepcopy(current)
    index = {row['id']:row for row in merged['exceptions']}
    if len(index) != len(merged['exceptions']):
        raise eng.ConfigurationError('Duplicate IDs in selected register')
    mapping = {}
    for row in old['exceptions']:
        item = copy.deepcopy(row)
        if 'domains' in item:
            raise eng.ConfigurationError('Unexpected domains in legacy schema; do not silently rewrite it')
        item['domains'] = ['engineering']
        sid = item['id']
        if sid in mapping:
            raise eng.ConfigurationError('Duplicate legacy IDs')
        if sid in index and index[sid] != item:
            raise eng.ConfigurationError(f'Conflicting exception {sid}; resolve explicitly, not by renumbering')
        if sid not in index:
            merged['exceptions'].append(item)
            index[sid] = item
        mapping[sid] = sid
    issues = eng.schema_errors(merged,schema,'merged')
    if issues:
        raise eng.ConfigurationError('; '.join(issues))
    receipt = dict(schema_version=1,state='draft',approval_ref=None,source_path='.engineering/exceptions.json',
                   source_sha256=eng.sha256_file(source),target_path=config['path'],id_map=mapping)
    report = dict(state='MIGRATION_PROPOSED_NOT_APPLIED',legacy_count=len(mapping),target_count=len(merged['exceptions']),
                  owner=profile.get('owners',{}).get('engineering'),
                  instruction='Review and install merged register and approved receipt together. Retain the legacy file frozen at its recorded hash. No approval, expiry or ID was altered.')
    return report, {'exceptions.merged.proposed.json':encoded(merged),
                    'exception-migration.proposed.json':encoded(receipt),'migration-review.json':encoded(report)}


def main() -> int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo',type=Path,required=True)
    parser.add_argument('--write-proposal',action='store_true')
    args=parser.parse_args()
    try:
        root=args.repo.resolve()
        report, files=propose_migration(root)
        if args.write_proposal:report['proposal_path']=write_proposal(root,files)
        print(json.dumps(report,indent=2));return 0
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print(json.dumps({'state':'BLOCKED','error':str(exc)},indent=2));return 2

if __name__=='__main__':raise SystemExit(main())
