#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Checks of the launcher API description, api/launcher-v1.openrpc.json (see api/README.md).

  launcher-spec.py lint SPEC CASES META...
      SPEC against the OpenRPC meta-schema (the META files, api/vendor); every example of SPEC and
      every case of CASES: its result against the method's result schema, its params against the
      param schemas, its fixture against FIXTURE below; and every errorCode a method can send is
      produced by at least one example or case.
  launcher-spec.py contract SPEC CASES DRIVER WORKDIR [RUNNER...]
      Runs every example and case through the real handlers (DRIVER, test-launcher-contract, started
      as RUNNER... DRIVER, for example under qemu-arm) and compares each reply with the expected
      result and each luna-send run with the expected calls.

Standard library only. The JSON Schema checks are the draft-07 subset below; it refuses a keyword it
does not implement, so a schema cannot silently pass on an unchecked keyword. format is not checked.
"""
import base64
import copy
import json
import os
import re
import shutil
import subprocess
import sys

# ---- a draft-07 subset ----

ANNOTATIONS = {'title', 'description', 'default', 'examples', '$comment', '$schema', '$id',
               'definitions', 'readOnly', 'writeOnly', 'format', 'contentMediaType', 'contentEncoding'}
APPLIED = {'$ref', 'type', 'enum', 'const', 'properties', 'patternProperties', 'additionalProperties',
           'required', 'propertyNames', 'minProperties', 'maxProperties', 'items', 'additionalItems',
           'minItems', 'maxItems', 'uniqueItems', 'contains', 'minLength', 'maxLength', 'pattern',
           'minimum', 'maximum', 'exclusiveMinimum', 'exclusiveMaximum', 'multipleOf', 'allOf', 'anyOf',
           'oneOf', 'not', 'if', 'then', 'else', 'dependencies'}


class SchemaError(Exception):
    pass


def is_number(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def json_equal(a, b):
    if isinstance(a, bool) or isinstance(b, bool):
        return isinstance(a, bool) and isinstance(b, bool) and a == b
    if is_number(a) and is_number(b):
        return a == b
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(json_equal(a[k], b[k]) for k in a)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(json_equal(x, y) for x, y in zip(a, b))
    return type(a) is type(b) and a == b


def has_type(v, t):
    if t not in ('object', 'array', 'string', 'boolean', 'null', 'number', 'integer'):
        raise SchemaError('unknown type %r' % (t,))
    return {
        'object': lambda: isinstance(v, dict),
        'array': lambda: isinstance(v, list),
        'string': lambda: isinstance(v, str),
        'boolean': lambda: isinstance(v, bool),
        'null': lambda: v is None,
        'number': lambda: is_number(v),
        'integer': lambda: (isinstance(v, int) and not isinstance(v, bool)) or (isinstance(v, float) and v.is_integer()),
    }[t]()


class Validator:
    def __init__(self, documents):
        self.registry = {}
        for doc in documents:
            if '$id' in doc:
                self.registry[doc['$id'].rstrip('/')] = doc

    def resolve(self, ref, doc):
        url, _, fragment = ref.partition('#')
        if url:
            if url.rstrip('/') not in self.registry:
                raise SchemaError('unresolved $ref ' + ref)
            doc = self.registry[url.rstrip('/')]
        node = doc
        for part in fragment.split('/')[1:] if fragment else []:
            part = part.replace('~1', '/').replace('~0', '~')
            node = node[int(part)] if isinstance(node, list) else node[part]
        return node, doc

    def errors(self, inst, schema, doc, path=''):
        if schema is True:
            return []
        if schema is False:
            return [path + ': no value is allowed here']
        if not isinstance(schema, dict):
            raise SchemaError('not a schema at ' + path)
        unknown = [k for k in schema if k not in ANNOTATIONS and k not in APPLIED and not k.startswith('x-')]
        if unknown:
            raise SchemaError('unsupported keyword(s) %s at %s' % (unknown, path or '/'))
        if '$ref' in schema:  # draft-07: siblings of $ref are ignored
            target, target_doc = self.resolve(schema['$ref'], doc)
            return self.errors(inst, target, target_doc, path)
        out = []
        sub = lambda i, s, p=path: self.errors(i, s, doc, p)  # noqa: E731
        if 'type' in schema:
            types = schema['type'] if isinstance(schema['type'], list) else [schema['type']]
            if not any(has_type(inst, t) for t in types):
                return [path + ': not of type ' + '/'.join(types)]
        if 'enum' in schema and not any(json_equal(inst, e) for e in schema['enum']):
            out.append('%s: %s is not one of %s' % (path, json.dumps(inst), json.dumps(schema['enum'])))
        if 'const' in schema and not json_equal(inst, schema['const']):
            out.append('%s: %s is not %s' % (path, json.dumps(inst), json.dumps(schema['const'])))
        if isinstance(inst, dict):
            props = schema.get('properties', {})
            patterns = schema.get('patternProperties', {})
            for key, value in inst.items():
                matched = False
                if key in props:
                    matched = True
                    out += sub(value, props[key], path + '/' + key)
                for pattern, s in patterns.items():
                    if re.search(pattern, key):
                        matched = True
                        out += sub(value, s, path + '/' + key)
                if not matched and 'additionalProperties' in schema:
                    out += sub(value, schema['additionalProperties'], path + '/' + key)
                if 'propertyNames' in schema:
                    out += sub(key, schema['propertyNames'], path + '/' + key)
            for key in schema.get('required', []):
                if key not in inst:
                    out.append('%s: %s is required' % (path, key))
            if len(inst) < schema.get('minProperties', 0) or len(inst) > schema.get('maxProperties', len(inst)):
                out.append(path + ': wrong number of members')
            for key, dep in schema.get('dependencies', {}).items():
                if key in inst:
                    if isinstance(dep, list):
                        out += ['%s: %s needs %s' % (path, key, d) for d in dep if d not in inst]
                    else:
                        out += sub(inst, dep)
        if isinstance(inst, list):
            items = schema.get('items', True)
            if isinstance(items, list):
                for i, value in enumerate(inst):
                    s = items[i] if i < len(items) else schema.get('additionalItems', True)
                    out += sub(value, s, '%s/%d' % (path, i))
            else:
                for i, value in enumerate(inst):
                    out += sub(value, items, '%s/%d' % (path, i))
            if len(inst) < schema.get('minItems', 0) or len(inst) > schema.get('maxItems', len(inst)):
                out.append(path + ': wrong number of items')
            if schema.get('uniqueItems'):
                if any(json_equal(a, b) for i, a in enumerate(inst) for b in inst[i + 1:]):
                    out.append(path + ': items are not unique')
            if 'contains' in schema and not any(not sub(v, schema['contains']) for v in inst):
                out.append(path + ': no item matches contains')
        if isinstance(inst, str):
            if len(inst) < schema.get('minLength', 0) or len(inst) > schema.get('maxLength', len(inst)):
                out.append(path + ': wrong length')
            if 'pattern' in schema and not re.search(schema['pattern'], inst):
                out.append('%s: does not match %s' % (path, schema['pattern']))
        if is_number(inst):
            if 'minimum' in schema and inst < schema['minimum']:
                out.append(path + ': below minimum')
            if 'maximum' in schema and inst > schema['maximum']:
                out.append(path + ': above maximum')
            if 'exclusiveMinimum' in schema and inst <= schema['exclusiveMinimum']:
                out.append(path + ': not above exclusiveMinimum')
            if 'exclusiveMaximum' in schema and inst >= schema['exclusiveMaximum']:
                out.append(path + ': not below exclusiveMaximum')
            if 'multipleOf' in schema and (inst / schema['multipleOf']) % 1:
                out.append(path + ': not a multiple')
        for s in schema.get('allOf', []):
            out += sub(inst, s)
        if 'anyOf' in schema and all(sub(inst, s) for s in schema['anyOf']):
            out.append(path + ': matches none of anyOf')
        if 'oneOf' in schema:
            results = [sub(inst, s) for s in schema['oneOf']]
            passing = sum(1 for r in results if not r)
            if passing != 1:
                detail = '; '.join(e for r in results for e in r[:2])
                out.append('%s: matches %d of oneOf, not 1 (%s)' % (path, passing, detail))
        if 'not' in schema and not sub(inst, schema['not']):
            out.append(path + ': matches not')
        if 'if' in schema:
            branch = 'then' if not sub(inst, schema['if']) else 'else'
            if branch in schema:
                out += sub(inst, schema[branch])
        return out


def unsupported(schema, path=''):
    """Keywords anywhere in schema that the subset does not implement."""
    if isinstance(schema, bool):
        return []
    if not isinstance(schema, dict):
        return [path + ': not a schema']
    out = ['%s/%s' % (path, k) for k in schema if k not in ANNOTATIONS and k not in APPLIED and not k.startswith('x-')]
    for key in ('properties', 'patternProperties', 'definitions', 'dependencies'):
        for name, s in schema.get(key, {}).items():
            if not isinstance(s, list):
                out += unsupported(s, '%s/%s/%s' % (path, key, name))
    for key in ('additionalProperties', 'propertyNames', 'additionalItems', 'contains', 'not', 'if', 'then', 'else'):
        if key in schema:
            out += unsupported(schema[key], path + '/' + key)
    items = schema.get('items')
    for i, s in enumerate(items if isinstance(items, list) else [items] if items is not None else []):
        out += unsupported(s, '%s/items/%d' % (path, i))
    for key in ('allOf', 'anyOf', 'oneOf'):
        for i, s in enumerate(schema.get(key, [])):
            out += unsupported(s, '%s/%s/%d' % (path, key, i))
    return out


# ---- the fixture format (api/launcher-v1.openrpc.json, x-fixtureFormat) ----

CALL = {
    'type': 'object', 'additionalProperties': False, 'required': ['uri', 'request'],
    'properties': {
        'uri': {'type': 'string', 'pattern': '^luna://'},
        'request': {'type': 'object'},
        'match': {'type': 'object', 'additionalProperties': {'type': 'string'}},
        'reply': {'type': 'object'},
        'stdout': {'type': 'string'},
        'exit': {'type': 'integer'},
    },
    'not': {'required': ['reply', 'stdout']},
}
FIXTURE = {
    'type': 'object', 'additionalProperties': False, 'required': ['caller', 'root', 'selectedHome', 'calls'],
    'properties': {
        'caller': {'type': ['string', 'null']},
        'senderService': {'type': ['string', 'null']},
        'root': {'type': 'boolean'},
        'selectedHome': {'type': ['string', 'null']},
        'entropy': {'type': 'boolean'},
        'files': {'type': 'object', 'additionalProperties': {
            'type': 'object', 'minProperties': 1, 'maxProperties': 1, 'additionalProperties': False,
            'properties': {'base64': {'type': 'string'}, 'text': {'type': 'string'},
                           'zeroBytes': {'type': 'integer', 'minimum': 0}, 'symlink': {'type': 'string'}}}},
        'calls': {'type': 'array', 'items': CALL},
    },
}
DISAGREEMENT = {
    'type': 'object', 'additionalProperties': False, 'required': ['reason', 'doc', 'code'],
    'properties': {'reason': {'type': 'string'}, 'doc': {'type': 'string'}, 'code': {'type': 'string'}},
}


def load(path):
    with open(path, encoding='utf-8') as f:
        return json.load(f)


def pointer_get(value, pointer):
    for part in pointer.split('/')[1:]:
        part = part.replace('~1', '/').replace('~0', '~')
        value = value[int(part)] if isinstance(value, list) else value[part]
    return value


def collect(spec, cases_doc):
    """Every example and case as one record: method, name, params (dict), result, fixture, match,
    knownDisagreement, origin."""
    methods = {m['name']: m for m in spec['methods']}
    out = []
    for m in spec['methods']:
        for ex in m.get('examples', []):
            out.append({
                'origin': 'spec', 'method': m['name'], 'name': ex['name'],
                'params': {p['name']: p['value'] for p in ex['params']},
                'result': ex['result']['value'], 'fixture': ex.get('x-fixture'),
                'match': ex.get('x-match', {}), 'knownDisagreement': ex.get('x-knownDisagreement'),
                'paramsOutsideSchema': False,
            })
    defaults = cases_doc.get('defaults', {})
    parts = cases_doc.get('parts', {})
    for c in cases_doc['cases']:
        fixture = copy.deepcopy(defaults)
        fixture.update(copy.deepcopy(c.get('fixture', {})))
        calls = []
        for step in fixture.get('calls', []):
            if isinstance(step, str):
                if step not in parts:
                    raise SystemExit('case %s/%s: unknown part %s' % (c['method'], c['name'], step))
                step = copy.deepcopy(parts[step])
            calls.append(step)
        fixture['calls'] = calls
        out.append({
            'origin': 'cases', 'method': c['method'], 'name': c['name'], 'params': c.get('params', {}),
            'result': c['result'], 'fixture': fixture, 'match': c.get('match', {}),
            'knownDisagreement': c.get('knownDisagreement'), 'paramsOutsideSchema': c.get('paramsOutsideSchema', False),
        })
    for r in out:
        if r['method'] not in methods:
            raise SystemExit('%s/%s: no method %s in the spec' % (r['method'], r['name'], r['method']))
    return methods, out


def error_codes(schema, spec_validator, spec, seen=None):
    """The errorCode enum values reachable in a result schema."""
    seen = seen if seen is not None else set()
    codes = set()
    if isinstance(schema, dict):
        if '$ref' in schema:
            if schema['$ref'] in seen:
                return codes
            seen.add(schema['$ref'])
            target, _ = spec_validator.resolve(schema['$ref'], spec)
            return error_codes(target, spec_validator, spec, seen)
        ec = schema.get('properties', {}).get('errorCode', {})
        codes |= set(ec.get('enum', []))
        for key in ('allOf', 'anyOf', 'oneOf'):
            for s in schema.get(key, []):
                codes |= error_codes(s, spec_validator, spec, seen)
    return codes


def lint(spec_path, cases_path, meta_paths):
    spec, cases_doc = load(spec_path), load(cases_path)
    metas = [load(p) for p in meta_paths]
    problems = []
    meta = Validator(metas)
    openrpc_meta = next(m for m in metas if m.get('$id', '').startswith('https://meta.open-rpc.org'))
    problems += ['meta-schema: ' + e for e in meta.errors(spec, openrpc_meta, openrpc_meta)]

    sv = Validator([])
    methods, records = collect(spec, cases_doc)
    want = ['getInfo', 'listApps', 'listRunning', 'getForegroundApp', 'getAppInfo', 'getAppIcon', 'closeApp', 'uninstallApp']
    if sorted(methods) != sorted(want):
        problems.append('methods are %s, v1 has %s' % (sorted(methods), sorted(want)))
    # Every schema in the document, against the subset (an unsupported keyword is a problem too).
    schemas = [('components/schemas/' + n, s) for n, s in spec.get('components', {}).get('schemas', {}).items()]
    for m in spec['methods']:
        schemas += [('%s/params/%s' % (m['name'], p['name']), p['schema']) for p in m['params']]
        schemas.append(('%s/result' % m['name'], m['result']['schema']))
    for where, s in schemas:
        problems += ['%s: unsupported keyword %s' % (where, k) for k in unsupported(s)]

    produced = {}
    for r in records:
        label = '%s %s/%s' % (r['origin'], r['method'], r['name'])
        m = methods[r['method']]
        try:
            problems += ['%s: result%s' % (label, e) for e in sv.errors(r['result'], m['result']['schema'], spec)]
            # Params are checked where they are meant to be valid: a failure case or one marked
            # paramsOutsideSchema may send what the schema does not allow, to show what the service does.
            checked = r['result'].get('returnValue') is True and not r['paramsOutsideSchema']
            declared = {p['name']: p for p in m['params']}
            for key, value in r['params'].items():
                if key not in declared and key not in m.get('x-ignoredParams', []):
                    problems.append('%s: undeclared param %s' % (label, key))
                elif key in declared and checked:
                    problems += ['%s: param %s%s' % (label, key, e) for e in sv.errors(value, declared[key]['schema'], spec)]
            if checked:
                problems += ['%s: missing required param %s' % (label, p['name'])
                             for p in m['params'] if p.get('required') and p['name'] not in r['params']]
        except SchemaError as e:
            problems.append('%s: %s' % (label, e))
        if r['fixture'] is None:
            problems.append(label + ': no x-fixture')
        else:
            problems += ['%s: fixture%s' % (label, e) for e in sv.errors(r['fixture'], FIXTURE, {})]
        for pointer, pattern in r['match'].items():
            try:
                pointer_get(r['result'], pointer)
                re.compile(pattern)
            except (KeyError, IndexError, ValueError, re.error) as e:
                problems.append('%s: match %s: %s' % (label, pointer, e))
        if r['knownDisagreement'] is not None:
            problems += ['%s: knownDisagreement%s' % (label, e) for e in sv.errors(r['knownDisagreement'], DISAGREEMENT, {})]
        elif r['result'].get('returnValue') is False:
            produced.setdefault(r['method'], set()).add(r['result'].get('errorCode'))
    names = [(r['method'], r['name']) for r in records]
    problems += ['duplicate case %s/%s' % n for n in sorted({n for n in names if names.count(n) > 1})]

    all_codes = set()
    for name, m in methods.items():
        codes = error_codes(m['result']['schema'], sv, spec)
        all_codes |= codes
        missing = codes - produced.get(name, set())
        if missing:
            problems.append('%s: no example or case produces %s' % (name, ', '.join(sorted(missing))))
        if not any(r['method'] == name and r['origin'] == 'spec' and r['result'].get('returnValue') is True for r in records):
            problems.append('%s: the spec has no success example' % name)
    if set(spec.get('x-errors', {})) != all_codes:
        problems.append('x-errors %s differs from the methods\' codes %s' % (sorted(spec.get('x-errors', {})), sorted(all_codes)))
    order = spec.get('x-replyModel', {}).get('callerRefusalOrder', [])
    problems += ['callerRefusalOrder: unknown code ' + c for c in order if c not in all_codes]

    for p in problems:
        print('FAIL ' + p)
    spec_count = sum(1 for r in records if r['origin'] == 'spec')
    print('lint: %d examples, %d cases, %d methods, %d error codes: %s' % (
        spec_count, len(records) - spec_count, len(methods), len(all_codes), 'ok' if not problems else '%d problem(s)' % len(problems)))
    return 1 if problems else 0


# ---- contract ----

def substitute(value, root):
    if isinstance(value, str):
        return value.replace('{root}', root)
    if isinstance(value, list):
        return [substitute(v, root) for v in value]
    if isinstance(value, dict):
        return {substitute(k, root): substitute(v, root) for k, v in value.items()}
    return value


def make_files(files):
    for path, spec in files.items():
        os.makedirs(os.path.dirname(path), exist_ok=True)
        if 'symlink' in spec:
            os.symlink(spec['symlink'], path)
            continue
        if 'base64' in spec:
            data = base64.b64decode(spec['base64'])
        elif 'text' in spec:
            data = spec['text'].encode()
        else:
            data = bytes(spec['zeroBytes'])
        with open(path, 'wb') as f:
            f.write(data)


def compare(expected, actual, match, pointer=''):
    """Differences between an expected and an actual JSON value; match: pointer -> regex."""
    if pointer in match:
        if not isinstance(actual, str) or not re.search(match[pointer], actual):
            return ['%s: %s does not match %s' % (pointer or '/', json.dumps(actual), match[pointer])]
        return []
    if isinstance(expected, dict) and isinstance(actual, dict):
        out = []
        for key in sorted(set(expected) | set(actual)):
            p = pointer + '/' + key.replace('~', '~0').replace('/', '~1')
            if key not in actual:
                out.append('%s: missing' % p)
            elif key not in expected:
                out.append('%s: unexpected %s' % (p, json.dumps(actual[key])))
            else:
                out += compare(expected[key], actual[key], match, p)
        return out
    if isinstance(expected, list) and isinstance(actual, list) and len(expected) == len(actual):
        return [d for i, (e, a) in enumerate(zip(expected, actual)) for d in compare(e, a, match, '%s/%d' % (pointer, i))]
    if not json_equal(expected, actual):
        return ['%s: expected %s, got %s' % (pointer or '/', json.dumps(expected), json.dumps(actual))]
    return []


def check_case(r, out, spec, sv, timeouts):
    problems = []
    if out.get('error'):
        return [out['error']]
    replies = out.get('replies', [])
    if len(replies) != 1:
        problems.append('%d replies, not 1: %s' % (len(replies), replies))
    if out.get('refs') != 1:
        problems.append('message references at the end: %s, not 1' % out.get('refs'))
    if replies:
        try:
            reply = json.loads(replies[0])
        except ValueError:
            return problems + ['reply is not JSON: ' + replies[0]]
        problems += ['schema: reply' + e for e in sv.errors(reply, spec['methods_by_name'][r['method']]['result']['schema'], spec)]
        problems += ['reply ' + d for d in compare(r['result'], reply, r['match'])]
    want, made = r['fixture']['calls'], out.get('calls', [])
    for i in range(max(len(want), len(made))):
        if i >= len(made):
            problems.append('call %d: not made: %s %s' % (i, want[i]['uri'], json.dumps(want[i]['request'])))
            continue
        argv = made[i]['argv']
        if i >= len(want):
            problems.append('call %d: unexpected: %s' % (i, ' '.join(argv)))
            continue
        head = ['/usr/bin/luna-send', '-a', 'com.webos.applicationManager', '-n', '1']
        if len(argv) != 7 or argv[:5] != head:
            problems.append('call %d: argv %s' % (i, argv))
            continue
        if argv[5] != want[i]['uri']:
            problems.append('call %d: uri %s, expected %s' % (i, argv[5], want[i]['uri']))
            continue
        try:
            request = json.loads(argv[6])
        except ValueError:
            problems.append('call %d: payload is not JSON: %s' % (i, argv[6]))
            continue
        problems += ['call %d request %s' % (i, d) for d in compare(want[i]['request'], request, want[i].get('match', {}))]
        limit = timeouts.get(argv[5])
        if limit is None:
            problems.append('call %d: %s has no time limit in x-outgoingCalls' % (i, argv[5]))
        elif made[i]['timeout'] != limit:
            problems.append('call %d: %s with a %ss limit, x-outgoingCalls says %ss' % (i, argv[5], made[i]['timeout'], limit))
    return problems


def contract(spec_path, cases_path, driver, workdir, runner):
    spec, cases_doc = load(spec_path), load(cases_path)
    methods, records = collect(spec, cases_doc)
    spec['methods_by_name'] = methods
    sv = Validator([])
    timeouts = spec['x-outgoingCalls']['timeoutSeconds']
    cases_dir = os.path.join(os.path.abspath(workdir), 'cases')
    shutil.rmtree(cases_dir, ignore_errors=True)
    os.makedirs(cases_dir)
    lines = []
    for i, r in enumerate(records):
        root = os.path.join(cases_dir, str(i))
        os.makedirs(root)
        r['fixture'] = f = substitute(r['fixture'], root)
        make_files(f.get('files', {}))
        script = []
        for step in f['calls']:
            text = step.get('stdout')
            if text is None:
                text = json.dumps(step.get('reply', {}), separators=(',', ':')) + '\n'
            script.append({'stdout': text, 'exit': step.get('exit', 0)})
        lines.append(json.dumps({
            'name': '%s/%s' % (r['method'], r['name']), 'method': r['method'],
            'payload': json.dumps(r['params']), 'app': f['caller'], 'service': f.get('senderService'),
            'root': f['root'], 'choice': f['selectedHome'], 'entropy': f.get('entropy', True), 'calls': script,
        }))
    input_path = os.path.join(cases_dir, 'input.jsonl')
    with open(input_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines) + '\n')
    run = subprocess.run(runner + [driver, input_path], capture_output=True, text=True, timeout=600)
    if run.returncode != 0:
        print(run.stderr)
        print('FAIL driver exit %d' % run.returncode)
        return 1
    outputs = [json.loads(line) for line in run.stdout.splitlines() if line.startswith('{')]
    if len(outputs) != len(records):
        print('FAIL driver gave %d results for %d cases' % (len(outputs), len(records)))
        return 1
    passed = xfailed = failed = 0
    for n, (r, out) in enumerate(zip(records, outputs), 1):
        label = '%s %s/%s' % (r['origin'], r['method'], r['name'])
        problems = check_case(r, out, spec, sv, timeouts)
        known = r['knownDisagreement']
        if known and problems:
            xfailed += 1
            print('xfail %d %s: known disagreement: %s (doc %s, code %s)' % (n, label, known['reason'], known['doc'], known['code']))
            for p in problems:
                print('    ' + p)
        elif known:
            failed += 1
            print('FAIL %d %s: passes although marked as a known disagreement; remove the marker' % (n, label))
        elif problems:
            failed += 1
            print('FAIL %d %s' % (n, label))
            for p in problems:
                print('    ' + p)
        else:
            passed += 1
            print('ok %d %s' % (n, label))
    print('contract: %d cases, %d passed, %d expected failures (known disagreements), %d failed' % (
        len(records), passed, xfailed, failed))
    return 1 if failed else 0


def main(argv):
    if len(argv) >= 4 and argv[0] == 'lint':
        return lint(argv[1], argv[2], argv[3:])
    if len(argv) >= 5 and argv[0] == 'contract':
        return contract(argv[1], argv[2], argv[3], argv[4], argv[5:])
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
