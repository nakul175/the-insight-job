#!/usr/bin/env python3
"""Read-only, hash-bound adapters for actual PM/QA files; no native schema guesses.

Only JSON and CSV bindings are supported. A maintainer must inspect the native
catalogue and checker help before approving mappings/arguments. Hashes bind bytes,
not authority or semantic correctness. This is not a replacement QA checker.
"""
from __future__ import annotations
import csv
import io
from pathlib import Path
import re
from typing import Any

FIELDS = {
    'story': {'id'},
    'acceptance_criterion': {'id', 'story_id'},
    'task': {'id', 'story_ids', 'acceptance_criterion_ids'},
    'qa_case': {'id', 'acceptance_criterion_ids'},
    'qa_run': {'id', 'case_ids', 'source_revision', 'outcome'},
}
LISTS = {'story_ids', 'acceptance_criterion_ids', 'case_ids'}


def pointer(value: Any, path: str) -> Any:
    if path == '':
        return value
    if not isinstance(path, str) or not path.startswith('/'):
        raise ValueError('JSON selectors must be JSON pointers, starting with /')
    for token in path[1:].split('/'):
        if re.search(r'~(?![01])', token):
            raise ValueError('Invalid JSON pointer escape')
        key = token.replace('~1', '/').replace('~0', '~')
        if isinstance(value, dict):
            value = value[key]
        elif isinstance(value, list) and re.fullmatch(r'0|[1-9][0-9]*', key):
            value = value[int(key)]
        else:
            raise ValueError('JSON pointer does not resolve')
    return value


def csv_rows(path: Path, limit: int) -> list[dict[str, str]]:
    if path.stat().st_size > limit:
        raise ValueError('CSV exceeds the input size bound')
    reader = csv.DictReader(io.StringIO(path.read_text(encoding='utf-8-sig'), newline=''))
    headers = reader.fieldnames or []
    if not headers or len(headers) != len(set(headers)) or any(not h.strip() for h in headers):
        raise ValueError('CSV requires unique nonblank headers')
    rows = list(reader)
    if any(None in row or any(value is None for value in row.values()) for row in rows):
        raise ValueError('CSV row width differs from its headers')
    return rows


def identifiers(value: Any, separator: str) -> list[str]:
    if isinstance(value, str):
        value = [v.strip() for v in value.split(separator) if v.strip()]
    if not isinstance(value, list) or any(not isinstance(v, str) or not v.strip() for v in value):
        raise ValueError('Relationship fields must be nonblank ID arrays or explicitly delimited strings')
    if len(value) != len(set(value)):
        raise ValueError('Duplicate relationship IDs')
    return value


def check_integration(root: Path, profile: dict, records: list[dict], api: Any) -> tuple[list[str], dict]:
    errors: list[str] = []
    try:
        path = api.safe_path(root, profile['integration_manifest'])
        manifest = api.read_json(path)
        schema = api.read_json(api.PACKAGE / '06-enforcement/schemas/integration.schema.json')
        errors.extend(api.schema_errors(manifest, schema, 'integration'))
        if errors:
            return errors, {}
        if manifest['state'] != 'approved':
            errors.append('integration: native PM/QA bindings are not approved; combined gates remain BLOCKED')
        errors.extend(api.placeholder_errors(manifest, 'integration'))
        for key in ('approval_ref', 'team_dod_ref', 'workflow_ref', 'release_record_ref'):
            errors.extend(api.reference_errors(root, manifest[key], 'integration.' + key))
        if manifest['team_dod_ref'] != profile['team_dod_ref']:
            errors.append('integration: one authoritative team Definition of Done must be selected')
        if manifest['exception_register_path'] != profile['exception_register']['path']:
            errors.append('integration: exception-register paths disagree; do not maintain competing registers')
        if manifest['root_agent_file'] != 'AGENTS.md':
            errors.append('integration: the combined instruction router must be the root AGENTS.md')
        agent_path = api.safe_path(root, manifest['root_agent_file'])
        if not agent_path.is_file() or api.sha256_file(agent_path) != manifest['root_agent_sha256']:
            errors.append('integration: the composed root agent router is missing or differs from its reviewed hash')
        sources: dict[str, dict] = {}
        for source in manifest['sources']:
            sid = source['id']
            if sid in sources:
                errors.append(f'integration: duplicate source {sid}')
                continue
            source_path = api.safe_path(root, source['path'])
            if not source_path.is_file() or api.sha256_file(source_path) != source['sha256']:
                errors.append(f'integration: missing or changed pinned source {sid}')
                continue
            sources[sid] = source
        for domain, key in (('product', 'pm_catalogue_source_id'), ('qa', 'qa_catalogue_source_id')):
            source = sources.get(manifest[key])
            if not source or source['domain'] != domain or source['kind'] != 'catalogue':
                errors.append(f'integration.{key}: actual hash-pinned {domain} catalogue is required')
        if 'native_bindings' in manifest:
            from native_bridge import native_tables, validate_native_records
            tables = {}
            if manifest['entities']:
                errors.append('integration: use native_bindings OR flat entity mappings, never two authorities')
            try:
                tables = native_tables(root, manifest['native_bindings'], sources, api)
                errors.extend(validate_native_records(tables, records))
            except (ValueError, KeyError, IndexError, TypeError, csv.Error) as exc:
                errors.append(f'integration.native_bindings: {exc}')
        else:
            tables: dict[str, dict[str, dict]] = {kind: {} for kind in FIELDS}
            for binding in manifest['entities']:
                kind = binding['kind']
                source = sources.get(binding['source_id'])
                expected_domain = 'qa' if kind.startswith('qa_') else 'product'
                if not source or source['kind'] != 'records' or source['domain'] != expected_domain:
                    errors.append(f'integration.{kind}: binding requires an actual {expected_domain} records source')
                    continue
                if set(binding['fields']) != FIELDS[kind] or not all(isinstance(v, str) and v for v in binding['fields'].values()):
                    errors.append(f'integration.{kind}: explicit field map must contain exactly {sorted(FIELDS[kind])}')
                    continue
                try:
                    source_path = api.safe_path(root, source['path'])
                    if binding['format'] == 'json':
                        rows = pointer(api.read_json(source_path), binding['selector'])
                    else:
                        if binding['selector']:
                            raise ValueError('CSV has no collection selector')
                        rows = csv_rows(source_path, api.MAX_JSON_BYTES)
                    if not isinstance(rows, list):
                        raise ValueError('Binding must select an array of records')
                    for row in rows:
                        normalized = {}
                        for target, locator in binding['fields'].items():
                            value = pointer(row, locator) if binding['format'] == 'json' else row[locator]
                            if target in LISTS:
                                value = identifiers(value, binding['list_separator'])
                            elif not isinstance(value, str) or not value.strip():
                                raise ValueError('IDs and status fields must be nonblank strings')
                            normalized[target] = value
                        identity = normalized['id']
                        if identity in tables[kind]:
                            raise ValueError(f'Duplicate {kind} identifier {identity}')
                        maps = binding.get('value_maps', {})
                        if maps:
                            if kind != 'qa_run' or set(maps) != {'outcome'} or not isinstance(maps['outcome'], dict):
                                raise ValueError('Only explicit qa_run outcome value maps are supported')
                            normalized['outcome'] = maps['outcome'][normalized['outcome']]
                            if normalized['outcome'] not in {'PASS', 'FAIL', 'BLOCKED', 'NOT_RUN', 'SKIPPED'}:
                                raise ValueError('Outcome map target must be an explicit supported state')
                        tables[kind][identity] = normalized
                except (ValueError, KeyError, IndexError, TypeError, csv.Error) as exc:
                    errors.append(f'integration.{kind}: invalid native mapping: {exc}')
            # Validate native relationships before accepting the engineering extension.
            for ac in tables['acceptance_criterion'].values():
                if ac['story_id'] not in tables['story']:
                    errors.append(f'integration: AC {ac["id"]} refers to an unknown story')
            for task in tables['task'].values():
                if any(s not in tables['story'] for s in task['story_ids']):
                    errors.append(f'integration: task {task["id"]} refers to an unknown story')
                for ac_id in task['acceptance_criterion_ids']:
                    ac = tables['acceptance_criterion'].get(ac_id)
                    if not ac or ac['story_id'] not in task['story_ids']:
                        errors.append(f'integration: task {task["id"]} has an invalid story/criterion relationship')
            for case in tables['qa_case'].values():
                if any(ac not in tables['acceptance_criterion'] for ac in case['acceptance_criterion_ids']):
                    errors.append(f'integration: QA case {case["id"]} refers to an unknown criterion')
            for run in tables['qa_run'].values():
                if re.fullmatch(r'(?:[a-fA-F0-9]{40}|[a-fA-F0-9]{64})', run['source_revision']) is None:
                    errors.append(f'integration: QA run {run["id"]} needs a full 40/64-hex Git revision')
                if any(case not in tables['qa_case'] for case in run['case_ids']):
                    errors.append(f'integration: QA run {run["id"]} refers to an unknown case')
            for record in records:
                tid = record['task_id']
                task = tables['task'].get(tid)
                if not task:
                    errors.append(f'{tid}: canonical task is absent from the pinned product source')
                    continue
                for field in ('story_ids', 'acceptance_criterion_ids'):
                    if set(record[field]) != set(task[field]):
                        errors.append(f'{tid}: {field} must match the canonical task, not a second backlog')
                covered: set[str] = set()
                for cid in record['qa_case_ids']:
                    case = tables['qa_case'].get(cid)
                    if not case:
                        errors.append(f'{tid}: unknown QA case {cid}')
                        continue
                    linked = set(case['acceptance_criterion_ids']) & set(record['acceptance_criterion_ids'])
                    if not linked:
                        errors.append(f'{tid}: QA case {cid} has no relationship to this task criteria')
                    covered |= linked
                if record['kind'] == 'implementation' and not set(record['acceptance_criterion_ids']) <= covered:
                    errors.append(f'{tid}: task criteria lack QA case links; links alone do not prove sufficient tests')
                successful_cases: set[str] = set()
                for rid in record['qa_run_ids']:
                    run = tables['qa_run'].get(rid)
                    if not run:
                        errors.append(f'{tid}: unknown QA run {rid}')
                        continue
                    if not set(run['case_ids']) & set(record['qa_case_ids']):
                        errors.append(f'{tid}: QA run {rid} does not cover linked cases')
                    # Outcome remains native text. Only explicit PASS is considered a pass;
                    # other native vocabularies require an explicit reviewed value map.
                    if run['outcome'] == 'PASS' and run['source_revision'] == record['commit']:
                        successful_cases |= set(run['case_ids'])
                if record['kind'] == 'implementation' and record['status'] in {'reviewed', 'merged', 'deployed'}:
                    if not record['qa_case_ids'] or not set(record['qa_case_ids']) <= successful_cases:
                        errors.append(f'{tid}: reviewed/merged/deployed needs passing linked QA runs for the exact recorded revision')
        gates: dict[str, dict] = {}
        for gate in manifest['gates']:
            gid = gate['id']
            if gid in gates:
                errors.append(f'integration: duplicate gate {gid}')
            source = sources.get(gate['checker_source_id'])
            command = gate['command']
            argv = command['argv']
            # Do not accept a no-op in place of the checker. Its flags still need native review.
            if (not source or source['domain'] != 'qa' or source['kind'] != 'checker'
                    or command['cwd'] != '.' or len(argv) < 2
                    or re.fullmatch(r'python(?:3(?:\.\d+)?)?', Path(argv[0]).name) is None
                    or argv[1] != source['path']):
                errors.append(f'integration.{gid}: invoke the actual pinned QA Python checker directly from repository root')
            errors.extend(api.reference_errors(root, gate['approval_ref'], 'integration.' + gid + '.approval_ref'))
            gates[gid] = gate
        if set(gates) != {'qa_lint', 'qa_release'}:
            errors.append('integration: both native qa_lint and native qa_release invocations must be bound')
        return errors, {'manifest': manifest, 'sources': sources, 'tables': tables, 'gates': gates,
                        'manifest_sha256': api.sha256_file(path)}
    except (OSError, ValueError, KeyError, TypeError, RecursionError) as exc:
        errors.append(f'integration: {exc}')
        return errors, {}
