#!/usr/bin/env python3
"""Read native bundle paths and PROPOSE bindings; never approve or activate them.

Extract the companion archives into the target repo first. Input discovery is
bounded and read-only; ambiguous candidates remain explicit blockers. Default is
dry-run. --write-proposal writes only a new private proposal directory. No code
from either bundle is executed. QA checker arguments must be supplied/reviewed.
"""
from __future__ import annotations
import argparse
import copy
import json
from pathlib import Path
import re
import sys
import uuid
import engctl as eng
from integration import csv_rows, pointer
from native_bridge import native_tables


def encoded(data: object) -> bytes:
    return (json.dumps(data, indent=2, sort_keys=True) + '\n').encode()


def inventory(root: Path) -> list[Path]:
    if not root.is_dir() or root.is_symlink():
        raise eng.ConfigurationError('Bundle input must be an extracted regular directory')
    result = []
    for path in root.rglob('*'):
        rel = path.relative_to(root)
        if any(p in {'.git', '__pycache__', '.pytest_cache', 'node_modules', '.venv'} for p in rel.parts):
            continue
        if path.is_symlink():
            raise eng.ConfigurationError(f'Symlink in native bundle: {rel}')
        if path.is_file():
            eng.safe_path(root, rel.as_posix())
            result.append(path)
            if len(result) > 5000:
                raise eng.ConfigurationError('Bundle discovery exceeds 5,000 files; select a narrower input')
    return sorted(result)


def propose(repo: Path, pm_root: Path, qa_root: Path, *, pm_version: str | None = None,
            qa_version: str | None = None, selections: dict | None = None,
            lint_args: list[str] | None = None, release_args: list[str] | None = None) -> tuple[dict, dict[str, bytes]]:
    selections = {} if selections is None else selections
    allowed = {'pm_document','pm_catalogue','qa_catalogue','qa_cases','qa_links','qa_runs',
               'qa_checker','team_dod','task_links','workflow_ref','release_record_ref','qa_owner'}
    if (not isinstance(selections, dict) or set(selections) - allowed
            or any(not isinstance(v, str) or not v.strip() for v in selections.values())):
        raise eng.ConfigurationError('Selections must be an object of recognized nonblank path/owner fields')
    for path in (pm_root, qa_root):
        if not path.resolve().is_relative_to(repo.resolve()):
            raise eng.ConfigurationError('Native bundle directories must be inside the repo for safe relative bindings')
    pm_files, qa_files = inventory(pm_root), inventory(qa_root)
    issues, sources, candidates = [], [], {}
    owned = {}
    profile_path = eng.safe_path(repo, '.engineering/repository.json')
    if profile_path.is_file():
        profile = eng.read_json(profile_path)
        if not isinstance(profile, dict) or not isinstance(profile.get('owners', {}), dict):
            raise eng.ConfigurationError('Repository profile/owners must be objects')
        owned = profile.get('owners', {})
    owners = {'engineering': owned.get('engineering'), 'product': owned.get('product'),
              'qa': selections.get('qa_owner')}

    def blocked(role: str, message: str) -> None:
        issues.append({'owner_role': role, 'owner': owners.get(role), 'action': message})

    for role, owner in owners.items():
        if eng.unresolved(owner):
            blocked(role, 'Assign the actual accountable owner before approving the connection')

    def choose(key: str, paths: list[Path], role: str) -> Path | None:
        if key in selections:
            path = eng.safe_path(repo, selections[key])
            if not path.is_file():
                blocked(role, f'{key}: selected file is absent')
                return None
            return path
        candidates[key] = [p.relative_to(repo).as_posix() for p in paths]
        if len(paths) != 1:
            blocked(role, f'{key}: select one actual file; {len(paths)} candidates found')
            return None
        return paths[0]

    def pin(sid: str, domain: str, kind: str, path: Path | None) -> str | None:
        if path is None:
            return None
        label = pm_version if domain == 'product' else qa_version
        sources.append(dict(id=sid, domain=domain, kind=kind, path=path.relative_to(repo).as_posix(),
                            sha256=eng.sha256_file(path), version=label or 'UNCONFIGURED'))
        return sid

    mixed = []
    for f in pm_files:
        if f.suffix == '.json' and f.stat().st_size <= eng.MAX_JSON_BYTES:
            try:
                data = eng.read_json(f)
                if isinstance(data, dict) and isinstance(data.get('elements'), list):
                    mixed.append(f)
            except (ValueError, OSError):
                continue
    pm_doc = choose('pm_document', mixed, 'product')
    pm_cat = choose('pm_catalogue', [p for p in pm_files if p.suffix == '.json' and re.search('catalog(?:ue)?', p.name, re.I)], 'product')
    qa_cat = choose('qa_catalogue', [p for p in qa_files if p.suffix == '.json' and re.search('catalog(?:ue)?', p.name, re.I)], 'qa')
    cases = choose('qa_cases', [p for p in qa_files if p.name == 'case-register.csv'], 'qa')
    links = choose('qa_links', [p for p in qa_files if p.name == 'traceability.csv'], 'qa')
    runs = choose('qa_runs', [p for p in qa_files if p.name in {'run-register.csv', 'execution-register.csv', 'test-runs.csv'}], 'qa')
    checker = choose('qa_checker', [p for p in qa_files if p.name == 'qa_lint.py'], 'qa')
    team = choose('team_dod', [p for p in pm_files if p.name == 'team-checklists.json'], 'product')
    for domain, label in [('product', pm_version), ('qa', qa_version)]:
        if eng.unresolved(label):
            blocked(domain, 'Record the actual companion bundle version; do not replace it with a guessed latest version')
    pin('pm-catalogue', 'product', 'catalogue', pm_cat)
    pin('qa-catalogue', 'qa', 'catalogue', qa_cat)
    pin('pm-records', 'product', 'records', pm_doc)
    pin('qa-cases', 'qa', 'records', cases)
    pin('qa-links', 'qa', 'records', links)
    pin('qa-runs', 'qa', 'records', runs)
    pin('qa-checker', 'qa', 'checker', checker)
    native = {'pm': {}, 'qa': {}}
    proposed_files = {}

    def locate(keys: set, alternatives: list[str], label: str, optional: bool = False) -> str | None:
        matches = [x for x in alternatives if x in keys]
        if len(matches) == 1:
            return matches[0]
        if not optional or matches:
            blocked('engineering', f'{label}: explicit locator required ({matches})')
        return None

    if pm_doc:
        rows = eng.read_json(pm_doc)['elements']
        keys = set.intersection(*(set(r) for r in rows)) if rows and all(isinstance(r, dict) for r in rows) else set()
        ids = locate(keys, ['id', 'elementId', 'element_id'], 'PM id')
        typ = locate(keys, ['type', 'elementType', 'level'], 'PM type')
        parent = locate(keys, ['parentId', 'parent_id'], 'PM parent')
        rev = locate(keys, ['version', 'revision'], 'PM version', True)
        types = {k: [] for k in ['story', 'acceptance_criterion', 'task']}
        synonyms = {'story': {'story', 'userstory'}, 'acceptance_criterion': {'acceptancecriterion'},
                    'task': {'task', 'technicaltask'}}
        if typ:
            for value in sorted({r[typ] for r in rows if isinstance(r[typ], str)}):
                clean = re.sub('[^a-z]', '', value.lower())
                for kind, allowed in synonyms.items():
                    if clean in allowed:
                        types[kind].append(value)
            for kind, values in types.items():
                if not values:
                    blocked('product', f'PM {kind}: choose the native type label; do not bind all elements to this type')
        structured = []
        tasks = [r for r in rows if typ and r.get(typ) in types['task']]
        for loc in ['/acceptance_criterion_ids', '/technical_task/source_criteria_ids', '/technical_task/acceptance_criterion_ids']:
            for row in tasks:
                try:
                    if isinstance(pointer(row, loc), list):
                        structured.append(loc)
                        break
                except (KeyError, ValueError, TypeError):
                    pass
        field = structured[0] if len(structured) == 1 else None
        if len(structured) > 1:
            blocked('product', 'Multiple native structured task-criteria fields found; select the authoritative one')
        extension = eng.safe_path(repo, selections.get('task_links', '.product/engineering-links.json'))
        ext_sid = None
        if extension.exists():
            ext_sid = pin('pm-task-links', 'product', 'records', extension)
        elif field is None and ids:
            extension_data = dict(schema_version=1, pm_source_sha256=eng.sha256_file(pm_doc), state='draft',
                owner=owners['product'], approval_ref=None,
                task_criteria=[dict(task_id=r[ids], acceptance_criterion_ids=[]) for r in tasks])
            proposed_files['task-links.proposed.json'] = encoded(extension_data)
            blocked('product', 'Approve typed task-criterion links and install .product/engineering-links.json, then regenerate. source_criteria prose is never parsed')
        native['pm'] = dict(source_id='pm-records', selector='/elements',
            fields={'id':'/'+ids if ids else None, 'type':'/'+typ if typ else None,
                    'parent':'/'+parent if parent else None, 'version':'/'+rev if rev else None},
            types=types, task_criteria_field=field, task_links_source_id=ext_sid, version_default=None)
    if cases and links and runs:
        def headers(path: Path) -> set:
            # csv_rows validates width/duplicate headers; read headers even for empty run registers.
            csv_rows(path, eng.MAX_JSON_BYTES)
            import csv, io
            return set(csv.DictReader(io.StringIO(path.read_text(encoding='utf-8-sig'))).fieldnames or [])
        ch, lh, rh = headers(cases), headers(links), headers(runs)
        native['qa'] = dict(cases_source_id='qa-cases', links_source_id='qa-links', runs_source_id='qa-runs',
            case_fields={'id':locate(ch,['case_id'],'QA case ID'), 'version':locate(ch,['case_version','version'],'QA case version')},
            link_fields={'case_id':locate(lh,['case_id'],'QA link case ID'), 'case_version':locate(lh,['case_version'],'QA link case version'),
                         'criterion_id':locate(lh,['ac_id','criterion_id','acceptance_criterion_id'],'QA link criterion ID'),
                         'criterion_version':locate(lh,['ac_version','criterion_version','acceptance_criterion_version'],'QA link criterion version',True)},
            run_fields={'id':locate(rh,['run_id'],'QA run ID'), 'case_id':locate(rh,['case_id'],'QA run case ID'),
                        'case_version':locate(rh,['case_version'],'QA run case version'),
                        'source_revision':locate(rh,['build_sha'],'QA run Git revision'), 'outcome':locate(rh,['result','outcome'],'QA run result')},
            outcome_map={})
    draft_path = eng.safe_path(repo, '.aplyd/integration.json')
    draft = copy.deepcopy(eng.read_json(draft_path)) if draft_path.exists() else dict(schema_version=2)
    draft.update(state='draft', approval_ref=None, sources=sources, entities=[], native_bindings=native,
                 pm_catalogue_source_id='pm-catalogue', qa_catalogue_source_id='qa-catalogue',
                 team_dod_ref=team.relative_to(repo).as_posix() if team else None,
                 workflow_ref=selections.get('workflow_ref'), release_record_ref=selections.get('release_record_ref'),
                 exception_register_path=(profile.get('exception_register',{}).get('path') if profile_path.exists() else '.governance/exceptions.json'),
                 root_agent_file='AGENTS.md', root_agent_sha256=None, gates=[])
    agent = eng.safe_path(repo,'AGENTS.md')
    if agent.is_file():
        draft['root_agent_sha256'] = eng.sha256_file(agent)
    for gid, args in [('qa_lint',lint_args), ('qa_release',release_args)]:
        if checker and args is not None:
            if not isinstance(args,list) or any(not isinstance(a,str) or not a for a in args):
                raise eng.ConfigurationError('QA checker arguments must be explicit nonblank string arrays')
            draft['gates'].append(dict(id=gid,checker_source_id='qa-checker',command={'argv':['python3',checker.relative_to(repo).as_posix(),*args], 'cwd':'.','timeout_seconds':300}, approval_ref='UNCONFIGURED'))
        else:
            blocked('qa', f'{gid}: supply the real checker argument array after inspecting its CLI; no flags are guessed')
    blocked('engineering', 'Review paths, source hashes, root AGENTS composition, runtime 3.11+, workflow/release records and selected exception register; activation is manual')
    blocked('product', 'Confirm PM team-checklists.json is the one team Done authority; approve the product relationships')
    preview = None
    try:
        preview = native_tables(repo, native, {s['id']:s for s in sources}, eng)
    except (ValueError, KeyError, OSError, TypeError) as exc:
        blocked('engineering', 'Native preview blocked: '+str(exc))
    proposed_files['integration.proposed.json'] = encoded(draft)
    if preview is not None:
        proposed_files['normalized-preview.json'] = encoded({'state':'PREVIEW_NOT_AUTHORITY','tables':preview})
    report = dict(state='DRAFT_PROPOSAL_NOT_APPROVED', owners=owners, candidates=candidates,
                  decisions=issues, preview_available=preview is not None,
                  note='No installed configuration, companion source, approval, workflow or exception register was changed.')
    proposed_files['binding-review.json'] = encoded(report)
    return report, proposed_files


def write_proposal(repo: Path, files: dict[str,bytes]) -> str:
    rel = f'.aplyd/proposals/binding-{uuid.uuid4().hex}'
    directory = eng.safe_path(repo,rel)
    directory.mkdir(parents=True, mode=0o700, exist_ok=False)
    for name, content in files.items():
        path = eng.safe_path(directory,name)
        with path.open('xb') as stream:
            stream.write(content)
        path.chmod(0o600)
    return rel


def main() -> int:
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--repo',type=Path,required=True)
    p.add_argument('--pm',type=Path)
    p.add_argument('--qa',type=Path)
    p.add_argument('--pm-version')
    p.add_argument('--qa-version')
    p.add_argument('--selections',help='Repo-relative JSON selection/owner/authority references')
    p.add_argument('--qa-lint-args',help='JSON string array of actual native CLI arguments')
    p.add_argument('--qa-release-args',help='JSON string array of actual native release CLI arguments')
    p.add_argument('--write-proposal',action='store_true')
    p.add_argument('--preview-manifest',help='Read and normalize a bound manifest; no code execution or approval')
    args=p.parse_args()
    try:
        repo=args.repo.resolve()
        if args.preview_manifest:
            manifest=eng.read_json(eng.safe_path(repo,args.preview_manifest))
            tables=native_tables(repo,manifest['native_bindings'],{s['id']:s for s in manifest['sources']},eng)
            print(json.dumps({'state':'PREVIEW_NOT_AUTHORITY','tables':tables},indent=2));return 0
        if args.pm is None or args.qa is None:
            raise eng.ConfigurationError('--pm and --qa extracted paths are required')
        sel=eng.read_json(eng.safe_path(repo,args.selections)) if args.selections else {}
        report, files=propose(repo,args.pm.resolve(),args.qa.resolve(),pm_version=args.pm_version,qa_version=args.qa_version,selections=sel,
            lint_args=json.loads(args.qa_lint_args) if args.qa_lint_args is not None else None,
            release_args=json.loads(args.qa_release_args) if args.qa_release_args is not None else None)
        if args.write_proposal:report['proposal_path']=write_proposal(repo,files)
        print(json.dumps(report,indent=2));return 0
    except (ValueError,KeyError,OSError,TypeError) as exc:
        print(json.dumps({'state':'BLOCKED','error':str(exc)},indent=2));return 2

if __name__=='__main__':raise SystemExit(main())
