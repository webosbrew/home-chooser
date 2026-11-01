#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""The JSON Schema checker of the launcher API description (api/README.md), shared by
service/test/launcher-spec.py (the CTest lint and contract tests), api/tools/gen-client.py and the
--api-check reply validation of e2e/homechooser_e2e.py.

A draft-07 subset, standard library only. It refuses a keyword it does not implement, so a schema
cannot silently pass on an unchecked keyword. format is not checked.
"""
import json
import os
import re

SPEC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'launcher-v1.openrpc.json')

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


def load_spec(path=SPEC):
    with open(path, encoding='utf-8') as f:
        return json.load(f)


def reply_problems(spec, method, reply, validator=None):
    """Problems of one reply of `method` against its result schema in `spec`: [] when it conforms,
    None when the reply is not Home Chooser's (not an object, or no apiVersion: a reply of the luna
    bus itself, which x-replyModel.platformErrors leaves outside the schemas)."""
    if not isinstance(reply, dict) or 'apiVersion' not in reply:
        return None
    methods = {m['name']: m for m in spec['methods']}
    if method not in methods:
        return ['no method %s in the spec' % method]
    validator = validator or Validator([])
    return ['reply' + e for e in validator.errors(reply, methods[method]['result']['schema'], spec)]
