#!/usr/bin/env python3
"""Native mixed-PM / relational-QA adapter. Read-only, hash-bound by integration.py.

This implements the shapes described in the review, not a claim to have inspected
unavailable PM v4.1 / QA v1.2 archives. Field locators remain explicit and reviewed.
No source_criteria prose is interpreted as an approved scope decision.
"""
from __future__ import annotations
import json
import re
from pathlib import Path
from typing import Any
from integration import pointer, csv_rows

OUTCOMES = {'PASS', 'FAIL', 'BLOCKED', 'NOT_RUN', 'SKIPPED'}


def text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'{label}: a nonblank string is required')
    return value.strip()


def version(value: Any, label: str, optional: bool = False) -> str | None:
    if optional and value is None:
        return None
    if type(value) is int and value >= 0:
        return str(value)
    return text(value, label)


def case_key(identity: str, revision: str) -> str:
    # Delimiter-safe and reversible. Never concatenate IDs with an ambiguous @.
    return json.dumps([identity, revision], separators=(',', ':'))


def id_list(value: Any, label: str) -> list[str]:
    if not isinstance(value, list):
        raise ValueError(f'{label}: a structured ID array is required; prose is not a mapping')
    result = [text(x, label) for x in value]
    if len(result) != len(set(result)):
        raise ValueError(f'{label}: duplicate IDs')
    return result


def native_tables(root: Path, config: dict, sources: dict, api: Any) -> dict:
    """Resolve native tables without writing a competing task/case ledger."""
    schema = api.read_json(api.PACKAGE / '06-enforcement/schemas/native-bindings.schema.json')
    problems = api.schema_errors(config, schema, 'native_bindings')
    if problems:
        raise ValueError('; '.join(problems))

    def source(sid: str, domain: str) -> Path:
        item = sources.get(sid)
        if not item or item['domain'] != domain or item['kind'] != 'records':
            raise ValueError(f'{sid}: a pinned {domain} records source is required')
        path = api.safe_path(root, item['path'])
        # Recheck at the point of use; preview callers cannot bypass input pinning.
        if not path.is_file() or api.sha256_file(path) != item['sha256']:
            raise ValueError(f'{sid}: native source bytes differ from the approved hash')
        return path

    pm, qa = config['pm'], config['qa']
    pm_path = source(pm['source_id'], 'product')
    raw = pointer(api.read_json(pm_path), pm['selector'])
    if not isinstance(raw, list):
        raise ValueError('PM selector must resolve to the native elements array')
    fields, types = pm['fields'], pm['types']
    kind_for = {}
    for kind, values in types.items():
        for v in values:
            if v in kind_for:
                raise ValueError('PM type filters must be disjoint')
            kind_for[v] = kind
    nodes, tables = {}, {k: {} for k in ('story', 'acceptance_criterion', 'task', 'qa_case', 'qa_run')}
    for row in raw:
        identity = text(pointer(row, fields['id']), 'PM id')
        if identity in nodes:
            raise ValueError(f'Duplicate native PM ID {identity}')
        native_type = text(pointer(row, fields['type']), identity + '.type')
        parent = pointer(row, fields['parent'])
        if parent is not None:
            parent = text(parent, identity + '.parent')
        rev = None
        if fields['version'] is not None:
            rev = version(pointer(row, fields['version']), identity + '.version', optional=True)
        if rev is None:
            rev = pm.get('version_default')
        nodes[identity] = dict(id=identity, kind=kind_for.get(native_type), parent=parent,
                               version=rev, raw=row)
    # Check the full native hierarchy, including ignored vision/strategy rows.
    # Null parents on roots are legal; cycles/dangling links never are.
    for node in nodes.values():
        current, seen = node, set()
        while current['parent'] is not None:
            if current['id'] in seen:
                raise ValueError(f'PM parent cycle involving {current["id"]}')
            seen.add(current['id'])
            if len(seen) > 256:
                raise ValueError('PM hierarchy exceeds 256 ancestor hops')
            if current['parent'] not in nodes:
                raise ValueError(f'PM dangling parent {current["parent"]}')
            current = nodes[current['parent']]

    def story_for(node: dict) -> str:
        current = node
        while current['parent'] is not None:
            current = nodes[current['parent']]
            if current['kind'] == 'story':
                return current['id']
        raise ValueError(f'{node["id"]}: no story ancestor; an explicit product decision is needed')

    links = {}
    if pm['task_links_source_id'] is not None:
        path = source(pm['task_links_source_id'], 'product')
        ext = api.read_json(path)
        eschema = api.read_json(api.PACKAGE / '06-enforcement/schemas/task-links.schema.json')
        problems = api.schema_errors(ext, eschema, 'task_links')
        if problems:
            raise ValueError('; '.join(problems))
        if ext['pm_source_sha256'] != api.sha256_file(pm_path):
            raise ValueError('Product-owned task links refer to a different PM source revision')
        if ext['state'] != 'approved' or api.unresolved(ext['owner']):
            raise ValueError('Product-owned task links require the product owner and approval')
        problems = api.reference_errors(root, ext['approval_ref'], 'task_links.approval_ref')
        if problems:
            raise ValueError('; '.join(problems))
        for row in ext['task_criteria']:
            tid = row['task_id']
            if tid in links or tid not in nodes or nodes[tid]['kind'] != 'task':
                raise ValueError(f'Task link {tid}: duplicate or unknown native technical task')
            links[tid] = id_list(row['acceptance_criterion_ids'], tid)

    for node in nodes.values():
        kind, identity = node['kind'], node['id']
        if kind is None:
            continue  # Filter BEFORE trying to read story-specific fields.
        item = dict(id=identity, version=node['version'])
        if kind == 'acceptance_criterion':
            item['story_id'] = story_for(node)
        elif kind == 'task':
            item['story_ids'] = [story_for(node)]
            native_ids = None
            if pm['task_criteria_field'] is not None:
                try:
                    native_value = pointer(node['raw'], pm['task_criteria_field'])
                except KeyError:
                    native_value = None
                if native_value is not None:
                    native_ids = id_list(native_value, identity + '.structured_criteria')
            if native_ids is not None and identity in links and set(native_ids) != set(links[identity]):
                raise ValueError(f'{identity}: native structured criteria and product-owned extension disagree')
            chosen = native_ids if native_ids is not None else links.get(identity)
            item['acceptance_criterion_ids'] = chosen if chosen is not None else []
            item['criteria_binding_state'] = 'bound' if chosen is not None else 'unbound'
        tables[kind][identity] = item
    for task in tables['task'].values():
        for aid in task['acceptance_criterion_ids']:
            ac = tables['acceptance_criterion'].get(aid)
            if not ac or ac['story_id'] not in task['story_ids']:
                raise ValueError(f'{task["id"]}: criterion {aid} is not in the task story')

    for row in csv_rows(source(qa['cases_source_id'], 'qa'), api.MAX_JSON_BYTES):
        cf = qa['case_fields']
        identity, rev = text(row[cf['id']], 'QA case id'), version(row[cf['version']], 'QA case version')
        key = case_key(identity, rev)
        if key in tables['qa_case']:
            raise ValueError(f'Duplicate QA case/version {identity}/{rev}')
        tables['qa_case'][key] = dict(id=identity, version=rev, acceptance_criterion_ids=[], criterion_refs=[])
    pairs = set()
    for row in csv_rows(source(qa['links_source_id'], 'qa'), api.MAX_JSON_BYTES):
        lf = qa['link_fields']
        identity, rev = text(row[lf['case_id']], 'QA link case'), version(row[lf['case_version']], 'QA link version')
        key = case_key(identity, rev)
        aid = text(row[lf['criterion_id']], 'QA link criterion')
        av = version(row[lf['criterion_version']], 'QA criterion version') if lf['criterion_version'] else None
        if key not in tables['qa_case']:
            raise ValueError(f'QA link refers to unknown case/version {identity}/{rev}')
        ac = tables['acceptance_criterion'].get(aid)
        if not ac:
            raise ValueError(f'QA link refers to unknown criterion {aid}')
        if av is not None and ac['version'] != av:
            raise ValueError(f'QA link criterion {aid}: version differs from the pinned PM baseline')
        pair = (key, aid, av)
        if pair in pairs:
            raise ValueError(f'Duplicate QA relation {identity}/{rev} -> {aid}/{av}')
        pairs.add(pair)
        case = tables['qa_case'][key]
        case['acceptance_criterion_ids'].append(aid)
        case['criterion_refs'].append(dict(id=aid, version=av))
    for row in csv_rows(source(qa['runs_source_id'], 'qa'), api.MAX_JSON_BYTES):
        rf = qa['run_fields']
        rid = text(row[rf['id']], 'QA run id')
        cid, cv = text(row[rf['case_id']], rid + '.case'), version(row[rf['case_version']], rid + '.case_version')
        key = case_key(cid, cv)
        if key not in tables['qa_case']:
            raise ValueError(f'{rid}: unknown QA case/version {cid}/{cv}')
        sha = text(row[rf['source_revision']], rid + '.build_sha').lower()
        if re.fullmatch(r'(?:[a-f0-9]{40}|[a-f0-9]{64})', sha) is None:
            raise ValueError(f'{rid}: build_sha must be a full 40/64-hex Git revision, not a label')
        outcome = text(row[rf['outcome']], rid + '.result')
        outcome = qa['outcome_map'].get(outcome, outcome)
        if outcome not in OUTCOMES:
            raise ValueError(f'{rid}: unrecognised outcome; configure an explicit value map')
        ref = dict(id=cid, version=cv)
        if rid in tables['qa_run']:
            run = tables['qa_run'][rid]
            if run['source_revision'] != sha or run['outcome'] != outcome or ref in run['case_refs']:
                raise ValueError(f'{rid}: duplicate/conflicting run rows')
            run['case_refs'].append(ref)
        else:
            tables['qa_run'][rid] = dict(id=rid, case_refs=[ref], source_revision=sha, outcome=outcome)
    return tables


def validate_native_records(tables: dict, records: list[dict]) -> list[str]:
    errors = []
    for record in records:
        tid = record['task_id']
        task = tables['task'].get(tid)
        if not task:
            errors.append(f'{tid}: canonical task is absent from the native PM source')
            continue
        if task['criteria_binding_state'] != 'bound':
            errors.append(f'{tid}: typed criteria are UNBOUND; product owner must supply structured links, not prose')
        for field in ('story_ids', 'acceptance_criterion_ids'):
            if set(record[field]) != set(task[field]):
                errors.append(f'{tid}: {field} differ from native task relationships')
        refs = record.get('qa_case_refs', [])
        if set(r['id'] for r in refs) != set(record['qa_case_ids']):
            errors.append(f'{tid}: native QA requires explicit qa_case_refs with version for every qa_case_id')
        selected, covered = set(), set()
        for ref in refs:
            key = case_key(ref['id'], ref['version'])
            if key in selected:
                errors.append(f'{tid}: duplicate versioned QA case reference')
            selected.add(key)
            case = tables['qa_case'].get(key)
            if not case:
                errors.append(f'{tid}: unknown selected QA case/version {ref["id"]}/{ref["version"]}')
                continue
            intersection = set(case['acceptance_criterion_ids']) & set(task['acceptance_criterion_ids'])
            if not intersection:
                errors.append(f'{tid}: selected QA case/version has no relationship to task criteria')
            covered |= intersection
        if record['kind'] == 'implementation' and not set(task['acceptance_criterion_ids']) <= covered:
            errors.append(f'{tid}: task criteria lack links to selected QA case versions')
        successful = set()
        needs_pass = record['kind'] == 'implementation' and record['status'] in {'reviewed', 'merged', 'deployed'}
        for rid in record['qa_run_ids']:
            run = tables['qa_run'].get(rid)
            if not run:
                errors.append(f'{tid}: unknown QA run {rid}')
                continue
            keys = {case_key(r['id'], r['version']) for r in run['case_refs']}
            if not keys & selected:
                errors.append(f'{tid}: QA run {rid} covers no selected QA case version')
            matching = run['source_revision'] == (record['commit'] or '').lower()
            if run['outcome'] == 'PASS' and matching:
                successful |= keys
            elif needs_pass:
                errors.append(f'{tid}: selected run {rid} is not PASS for the exact source revision')
        if needs_pass and (not selected or not selected <= successful):
            errors.append(f'{tid}: reviewed/merged/deployed requires passing runs for exact case versions and commit')
    return errors
