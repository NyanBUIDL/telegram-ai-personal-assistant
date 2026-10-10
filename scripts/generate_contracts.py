"""Generate the JavaScript contract mirror; run via scripts/check.py.

python scripts/check.py generate_contracts [--check]
No Node package or runtime schema library is required by the generated module.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from tg_assistant.contracts import PUBLIC_CONTRACTS, public_contract_schema

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "dashboard-prototype/src/contracts/generated.js"

VALIDATOR = r'''
const own = (value, key) => Object.prototype.hasOwnProperty.call(value, key)

function nonsecretPayload(value, depth = 0) {
  if (depth > contractSchema.native_payload_max_depth) return false
  if (Array.isArray(value)) return value.every((child) => nonsecretPayload(child, depth + 1))
  if (value !== null && typeof value === 'object') {
    if (Object.getPrototypeOf(value) !== Object.prototype && Object.getPrototypeOf(value) !== null) return false
    return Object.entries(value).every(([key, child]) => {
      const normalized = key.toLowerCase().replace(/[^a-z0-9]/g, '')
      return !contractSchema.sensitive_key_parts.some((part) => normalized.includes(part))
        && !contractSchema.native_target_keys.some((part) => normalized.includes(part))
        && nonsecretPayload(child, depth + 1)
    })
  }
  if (typeof value === 'string') return !/[A-Za-z][A-Za-z0-9+.-]*:\/\//.test(value)
  return value === null || typeof value === 'boolean' || (typeof value === 'number' && Number.isFinite(value))
}

function timestamp(value) {
  const match = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.\d+)?(?:Z|[+-](\d{2}):(\d{2}))$/.exec(value)
  if (!match || !Number.isFinite(Date.parse(value))) return false
  const [, year, month, day, hour, minute, second, offsetHour = '0', offsetMinute = '0'] = match
  const days = new Date(Date.UTC(Number(year), Number(month), 0)).getUTCDate()
  return Number(month) >= 1 && Number(month) <= 12 && Number(day) >= 1 && Number(day) <= days
    && Number(hour) <= 23 && Number(minute) <= 59 && Number(second) <= 59
    && Number(offsetHour) <= 23 && Number(offsetMinute) <= 59
}

function matches(schema, value, root, depth = 0) {
  if (depth > 64) return false
  if (schema.$ref) {
    const definition = root.$defs?.[schema.$ref.split('/').at(-1)]
    return definition !== undefined && matches(definition, value, root, depth + 1)
  }
  if (schema.anyOf && !schema.anyOf.some((option) => matches(option, value, root, depth + 1))) return false
  if (schema.enum && !schema.enum.includes(value)) return false
  if (schema.const !== undefined && schema.const !== value) return false
  switch (schema.type) {
    case 'null': if (value !== null) return false; break
    case 'boolean': if (typeof value !== 'boolean') return false; break
    case 'integer': if (!Number.isSafeInteger(value)) return false; break
    case 'number': if (typeof value !== 'number' || !Number.isFinite(value)) return false; break
    case 'string':
      if (typeof value !== 'string') return false
      if (schema.minLength !== undefined && [...value].length < schema.minLength) return false
      if (schema.maxLength !== undefined && [...value].length > schema.maxLength) return false
      if (schema.pattern && !new RegExp(schema.pattern).test(value)) return false
      if (schema.format === 'date-time' && !timestamp(value)) return false
      break
    case 'array':
      if (!Array.isArray(value) || !value.every((item) => matches(schema.items, item, root, depth + 1))) return false
      break
    case 'object': {
      if (value === null || typeof value !== 'object' || Array.isArray(value)) return false
      if (Object.getPrototypeOf(value) !== Object.prototype && Object.getPrototypeOf(value) !== null) return false
      if ((schema.required ?? []).some((key) => !own(value, key))) return false
      for (const [key, child] of Object.entries(value)) {
        if (schema.propertyNames && !matches(schema.propertyNames, key, root, depth + 1)) return false
        if (own(schema.properties ?? {}, key)) {
          if (!matches(schema.properties[key], child, root, depth + 1)) return false
        } else if (schema.additionalProperties === false) return false
        else if (schema.additionalProperties && !matches(schema.additionalProperties, child, root, depth + 1)) return false
      }
      if (schema.title === 'PublicProfile' && !contractSchema.unverified_stages.includes(value.setup_stage) && value.owner_id === null) return false
      break
    }
    default: break
  }
  if (typeof value === 'number') {
    if (schema.minimum !== undefined && value < schema.minimum) return false
    if (schema.maximum !== undefined && value > schema.maximum) return false
  }
  if (schema['x-native-payload']) {
    if (!nonsecretPayload(value)) return false
    if (new TextEncoder().encode(JSON.stringify(value)).length > contractSchema.native_payload_max_bytes) return false
  }
  return true
}

/** Validate JSON data from the public boundary. Unknown/internal contracts fail closed.
 * @param {string} name
 * @param {unknown} value
 * @returns {boolean}
 */
export function validateContract(name, value) {
  if (!own(contractSchema.contracts, name)) return false
  try {
    return matches(contractSchema.contracts[name], value, contractSchema.contracts[name])
  } catch {
    return false
  }
}

/** Reject a malformed public DTO before a consumer uses it.
 * @param {string} name
 * @param {unknown} value
 * @returns {unknown}
 */
export function assertContract(name, value) {
  if (!validateContract(name, value)) throw new TypeError(`Invalid ${name} contract`)
  return value
}
'''


def js_type(schema: dict) -> str:
    if "$ref" in schema:
        return schema["$ref"].split("/")[-1]
    if "enum" in schema:
        return "|".join(json.dumps(value) for value in schema["enum"])
    if "anyOf" in schema:
        return "(" + "|".join(js_type(value) for value in schema["anyOf"]) + ")"
    match schema.get("type"):
        case "string":
            return "string"
        case "integer" | "number":
            return "number"
        case "boolean":
            return "boolean"
        case "null":
            return "null"
        case "array":
            return f"Array<{js_type(schema['items'])}>"
        case "object":
            additional = schema.get("additionalProperties")
            if isinstance(additional, dict):
                return f"Object<string, {js_type(additional)}>"
            return "Object"
        case _:
            return "unknown"


def render() -> str:
    schema = public_contract_schema()
    definitions: dict[str, dict] = {}
    for model in PUBLIC_CONTRACTS:
        model_schema = schema["contracts"][model.__name__]
        definitions.update(model_schema.get("$defs", {}))
        definitions[model.__name__] = model_schema
    comments = []
    for name, definition in sorted(definitions.items()):
        if "properties" in definition:
            lines = [f"/** @typedef {{Object}} {name}"]
            lines.extend(f" * @property {{{js_type(prop)}}} {key}" for key, prop in definition["properties"].items())
            lines.append(" */")
            comments.append("\n".join(lines))
        else:
            comments.append(f"/** @typedef {{{js_type(definition)}}} {name} */")
    return (
        "// Generated by scripts/generate_contracts.py. Edit Python contracts, then regenerate.\n"
        "// Internal tickets, sessions and leases are intentionally excluded.\n\n"
        + "\n\n".join(comments)
        + "\n\nexport const contractSchema = "
        + json.dumps(schema, ensure_ascii=False, indent=2, sort_keys=True)
        + "\n"
        + VALIDATOR
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Exit nonzero if the checked-in mirror drifted")
    args = parser.parse_args()
    generated = render()
    if args.check:
        if not OUTPUT.is_file() or OUTPUT.read_text(encoding="utf-8") != generated:
            raise SystemExit("Generated contracts are stale; run python scripts/check.py generate_contracts")
        print("Generated contracts match Python source")
    else:
        OUTPUT.parent.mkdir(parents=True, exist_ok=True)
        OUTPUT.write_text(generated, encoding="utf-8", newline="\n")
        print(f"Generated {OUTPUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
