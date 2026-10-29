#!/usr/bin/env python3
"""Check RDF Node MQTT payloads against the per-topic JSON Schemas in contract/schema/.

Standard library only, so it runs on the Pi, the Ground host, or a laptop. It implements the subset
of JSON Schema 2020-12 the schemas use: type, const, enum, properties, required,
additionalProperties, items, minItems, maxItems, minimum, maximum, minLength, maxLength, pattern,
and anyOf.

    python3 contract/validate.py telemetry/doa payload.json
    mosquitto_sub -t 'sdr/v2/uav-01/telemetry/doa' | python3 contract/validate.py telemetry/doa -
"""
from __future__ import annotations
import json
import re
import sys
from pathlib import Path

SCHEMA_DIR = Path(__file__).resolve().parent / 'schema'


def schema_for(topic: str) -> dict:
    """Schema for a topic suffix (`telemetry/doa`) or a full topic (`sdr/v2/uav-01/telemetry/doa`)."""
    suffix = re.sub(r'^.*?/v2/[^/]+/', '', topic)
    name = 'cmd' if suffix.startswith('cmd/') else suffix.replace('/', '.')
    return json.loads((SCHEMA_DIR / f'{name}.json').read_text(encoding='utf-8'))


def _type_ok(value, kind: str) -> bool:
    if kind == 'null': return value is None
    if kind == 'boolean': return type(value) is bool
    if kind == 'integer':
        return (type(value) is int) or (type(value) is float and value.is_integer())
    if kind == 'number': return type(value) in (int, float)
    if kind == 'string': return isinstance(value, str)
    if kind == 'array': return isinstance(value, list)
    if kind == 'object': return isinstance(value, dict)
    raise ValueError(f'unsupported type {kind}')


def errors(value, schema: dict, path: str = '$') -> list[str]:
    """Every way `value` breaks `schema`, as readable strings; empty when it conforms."""
    found = []
    kinds = schema.get('type')
    if kinds is not None:
        kinds = [kinds] if isinstance(kinds, str) else kinds
        if not any(_type_ok(value, kind) for kind in kinds):
            return [f'{path}: expected {"/".join(kinds)}, got {type(value).__name__}']
    if 'const' in schema and not (value == schema['const'] and type(value) is type(schema['const'])):
        found.append(f'{path}: expected {schema["const"]!r}, got {value!r}')
    if 'enum' in schema and not any(value == option and type(value) is type(option) for option in schema['enum']):
        found.append(f'{path}: {value!r} not in {schema["enum"]!r}')
    if type(value) in (int, float):
        if 'minimum' in schema and value < schema['minimum']: found.append(f'{path}: {value} < {schema["minimum"]}')
        if 'maximum' in schema and value > schema['maximum']: found.append(f'{path}: {value} > {schema["maximum"]}')
    if isinstance(value, str):
        if 'minLength' in schema and len(value) < schema['minLength']: found.append(f'{path}: shorter than {schema["minLength"]}')
        if 'maxLength' in schema and len(value) > schema['maxLength']: found.append(f'{path}: longer than {schema["maxLength"]}')
        if 'pattern' in schema and not re.search(schema['pattern'], value):
            found.append(f'{path}: {value!r} does not match {schema["pattern"]}')
    if isinstance(value, list):
        if 'minItems' in schema and len(value) < schema['minItems']: found.append(f'{path}: fewer than {schema["minItems"]} items')
        if 'maxItems' in schema and len(value) > schema['maxItems']: found.append(f'{path}: more than {schema["maxItems"]} items')
        if 'items' in schema:
            for index, item in enumerate(value):
                found.extend(errors(item, schema['items'], f'{path}[{index}]'))
    if isinstance(value, dict):
        for key in schema.get('required', ()):
            if key not in value: found.append(f'{path}: missing {key}')
        properties = schema.get('properties', {})
        for key, item in value.items():
            if key in properties: found.extend(errors(item, properties[key], f'{path}.{key}'))
            elif schema.get('additionalProperties') is False: found.append(f'{path}: unexpected {key}')
    if 'anyOf' in schema and not any(not errors(value, option, path) for option in schema['anyOf']):
        found.append(f'{path}: matches none of anyOf')
    return found


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(__doc__.strip(), file=sys.stderr)
        return 2
    schema = schema_for(argv[1])
    lines = sys.stdin.read().splitlines() if argv[2] == '-' else [Path(argv[2]).read_text(encoding='utf-8')]
    status = 0
    for line in (line for line in lines if line.strip()):
        problems = errors(json.loads(line), schema)
        print('OK' if not problems else 'INVALID: ' + '; '.join(problems))
        status = status or (1 if problems else 0)
    return status


if __name__ == '__main__':
    sys.exit(main(sys.argv))
