/**
 * Emit a normalized JSON description of the Manifest schema defined in
 * src/parser/types.ts, for the Python↔TypeScript schema-sync test
 * (tests/brook/script_video/test_manifest_schema_sync.py).
 *
 * Uses the TypeScript compiler API (semantic checker, not regex/syntax
 * walking) so formatting changes, intersection flattening (`SceneBase & {…}`)
 * and alias resolution never produce false drift.
 *
 * Output shape (stdout):
 *   {
 *     "types": {
 *       "<ExportedAliasName>":
 *         { "kind": "object", "fields": { "<field>": { "type": <desc>, "optional": bool } } }
 *       | { "kind": "union", "members": [<desc>, …] }
 *     }
 *   }
 *
 * <desc> is one of:
 *   { "kind": "string" | "number" | "boolean" | "object" }
 *   { "kind": "literal", "value": <string|number|boolean> }
 *   { "kind": "ref", "name": "<TypeName>" }        // named object types only
 *   { "kind": "array", "items": <desc> }
 *   { "kind": "union", "members": [<desc>, …] }
 *   any of the above may carry "nullable": true when `| null` appeared.
 *
 * Normalization rules (mirrored by the Python side of the test):
 *   - `| undefined` is stripped silently (absence marker, not a JSON value).
 *   - `| null` is stripped and recorded as "nullable": true.
 *   - Named OBJECT aliases (BBox, Citation, …) become {"kind":"ref"}.
 *   - Named UNION aliases (Scene) are always expanded inline to a union of
 *     refs — never emitted as a ref — so both sides agree regardless of
 *     whether the checker preserves the alias symbol at a use site.
 *   - Record<…> / index-signature objects collapse to {"kind":"object"}.
 *   - Anything unrecognized becomes {"kind":"unhandled"} which the Python
 *     comparator rejects loudly (fail-closed, never silently passes).
 */

import { fileURLToPath } from "url";
import { dirname, join } from "path";
import ts from "typescript";

const scriptDir = dirname(fileURLToPath(import.meta.url));
const typesPath = join(scriptDir, "..", "src", "parser", "types.ts");

const program = ts.createProgram([typesPath], {
  strict: true,
  target: ts.ScriptTarget.ES2022,
  module: ts.ModuleKind.NodeNext,
  moduleResolution: ts.ModuleResolutionKind.NodeNext,
  skipLibCheck: true,
  noEmit: true,
});
const checker = program.getTypeChecker();
const sourceFile = program.getSourceFile(typesPath);
if (sourceFile === undefined) {
  process.stderr.write(`Cannot load ${typesPath}\n`);
  process.exit(1);
}

// ---------------------------------------------------------------------------
// Collect exported type aliases
// ---------------------------------------------------------------------------

/** @type {Map<string, ts.Type>} */
const namedTypes = new Map();
for (const stmt of sourceFile.statements) {
  if (!ts.isTypeAliasDeclaration(stmt)) continue;
  const isExported = stmt.modifiers?.some(
    (m) => m.kind === ts.SyntaxKind.ExportKeyword,
  );
  if (!isExported) continue;
  const symbol = checker.getSymbolAtLocation(stmt.name);
  if (symbol === undefined) continue;
  namedTypes.set(stmt.name.text, checker.getDeclaredTypeOfSymbol(symbol));
}

// ---------------------------------------------------------------------------
// Type → normalized descriptor
// ---------------------------------------------------------------------------

/**
 * @param {ts.Type} type
 * @param {string | null} selfName  Alias name currently being expanded
 *                                  (suppresses the self-reference shortcut).
 * @returns {Record<string, unknown>}
 */
function describeType(type, selfName) {
  const aliasName = type.aliasSymbol?.name;
  if (aliasName !== undefined && aliasName !== selfName && namedTypes.has(aliasName)) {
    const aliased = namedTypes.get(aliasName);
    if (aliased !== undefined && aliased.isUnion()) {
      // Union aliases (Scene) expand inline — see header.
      return describeType(aliased, aliasName);
    }
    return { kind: "ref", name: aliasName };
  }

  if (type.isUnion()) {
    let nullable = false;
    const members = [];
    for (const member of type.types) {
      if (member.flags & ts.TypeFlags.Undefined) continue; // absence marker
      if (member.flags & ts.TypeFlags.Null) {
        nullable = true;
        continue;
      }
      members.push(describeType(member, null));
    }
    const desc =
      members.length === 1 ? { ...members[0] } : { kind: "union", members };
    if (nullable) desc.nullable = true;
    return desc;
  }

  if (type.flags & ts.TypeFlags.StringLiteral) {
    return { kind: "literal", value: /** @type {ts.StringLiteralType} */ (type).value };
  }
  if (type.flags & ts.TypeFlags.NumberLiteral) {
    return { kind: "literal", value: /** @type {ts.NumberLiteralType} */ (type).value };
  }
  if (type.flags & ts.TypeFlags.BooleanLiteral) {
    return { kind: "literal", value: checker.typeToString(type) === "true" };
  }
  if (type.flags & ts.TypeFlags.String) return { kind: "string" };
  if (type.flags & ts.TypeFlags.Number) return { kind: "number" };
  if (type.flags & ts.TypeFlags.Boolean) return { kind: "boolean" };

  if (checker.isArrayType(type)) {
    const [elem] = checker.getTypeArguments(/** @type {ts.TypeReference} */ (type));
    return {
      kind: "array",
      items: elem === undefined ? { kind: "unhandled" } : describeType(elem, null),
    };
  }

  // Record<string, unknown> and friends: opaque object with index signature.
  if (
    type.aliasSymbol?.name === "Record" ||
    checker.getIndexInfosOfType(type).length > 0
  ) {
    return { kind: "object" };
  }

  return { kind: "unhandled", text: checker.typeToString(type) };
}

/**
 * @param {ts.Type} type
 * @param {string} name
 * @returns {Record<string, unknown>}
 */
function describeNamedType(type, name) {
  if (type.isUnion()) return describeType(type, name);

  const fields = {};
  for (const prop of checker.getPropertiesOfType(type)) {
    const decl = prop.valueDeclaration ?? sourceFile;
    const propType = checker.getTypeOfSymbolAtLocation(prop, decl);
    fields[prop.getName()] = {
      type: describeType(propType, null),
      optional: (prop.flags & ts.SymbolFlags.Optional) !== 0,
    };
  }
  return { kind: "object", fields };
}

// ---------------------------------------------------------------------------
// Emit
// ---------------------------------------------------------------------------

const out = { types: {} };
for (const [name, type] of namedTypes) {
  out.types[name] = describeNamedType(type, name);
}
process.stdout.write(JSON.stringify(out, null, 2) + "\n");
