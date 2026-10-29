#!/usr/bin/env node
/**
 * Offline dependency-manifest checker for the ledger-sync update.
 *
 * Usage: node scripts/verify-deps.js /tmp/updated-package.json
 *
 * This script reads only the candidate manifest and uses Node built-ins. It
 * does not install packages, contact a registry, or touch the network.
 */

import { readFileSync } from "node:fs";
import { resolve } from "node:path";

const EXPECTED_METADATA = {
  name: "ledger-sync",
  version: "2.4.0",
  private: true,
  type: "module",
  description: "Offline reconciliation service for the internal ledger workspace",
  license: "UNLICENSED",
  engines: { node: ">=20.11" },
};

const EXPECTED_SCRIPTS = {
  start: "node src/index.js",
  test: "node --test test/",
  "check:deps": "node scripts/verify-deps.js",
};

const EXPECTED_DEPENDENCIES = {
  express: "^4.21.2",
  pg: "^8.13.1",
  pino: "^9.5.0",
  zod: "^3.24.1",
};

const EXPECTED_DEV_DEPENDENCIES = {
  eslint: "^8.57.1",
  prettier: "^3.4.2",
  vitest: "^1.6.1",
};

function stable(value) {
  if (value === null || typeof value !== "object") {
    return JSON.stringify(value);
  }
  if (Array.isArray(value)) {
    return `[${value.map(stable).join(",")}]`;
  }
  return `{${Object.keys(value)
    .sort()
    .map((key) => `${JSON.stringify(key)}:${stable(value[key])}`)
    .join(",")}}`;
}

function compareSection(failures, sectionName, actual, expected) {
  if (actual === null || typeof actual !== "object" || Array.isArray(actual)) {
    failures.push(`${sectionName} must be an object`);
    return;
  }

  const actualKeys = Object.keys(actual).sort();
  const expectedKeys = Object.keys(expected).sort();
  for (const key of expectedKeys) {
    if (!(key in actual)) {
      failures.push(`${sectionName}.${key} is missing`);
    } else if (actual[key] !== expected[key]) {
      failures.push(
        `${sectionName}.${key} is ${JSON.stringify(actual[key])}, expected ${JSON.stringify(expected[key])}`,
      );
    }
  }
  for (const key of actualKeys) {
    if (!(key in expected)) {
      failures.push(`${sectionName}.${key} should not be present`);
    }
  }
}

function main() {
  const candidatePath = process.argv[2];
  if (!candidatePath) {
    console.error("usage: node scripts/verify-deps.js <updated-package.json>");
    process.exitCode = 2;
    return;
  }

  let manifest;
  try {
    manifest = JSON.parse(readFileSync(resolve(candidatePath), "utf8"));
  } catch (error) {
    console.error(`FAIL: cannot read JSON from ${candidatePath}: ${error.message}`);
    process.exitCode = 1;
    return;
  }

  const failures = [];

  for (const [field, expected] of Object.entries(EXPECTED_METADATA)) {
    if (!(field in manifest)) {
      failures.push(`metadata field ${field} is missing`);
    } else if (stable(manifest[field]) !== stable(expected)) {
      failures.push(
        `metadata field ${field} is ${stable(manifest[field])}, expected ${stable(expected)}`,
      );
    }
  }

  compareSection(failures, "scripts", manifest.scripts, EXPECTED_SCRIPTS);
  compareSection(
    failures,
    "dependencies",
    manifest.dependencies,
    EXPECTED_DEPENDENCIES,
  );
  compareSection(
    failures,
    "devDependencies",
    manifest.devDependencies,
    EXPECTED_DEV_DEPENDENCIES,
  );

  if (failures.length > 0) {
    console.error(`FAIL: ${failures.length} problem(s) in ${candidatePath}`);
    for (const failure of failures) {
      console.error(`  - ${failure}`);
    }
    process.exitCode = 1;
    return;
  }

  console.log(`PASS: ${candidatePath} matches the dependency update guide`);
  console.log("  dependencies: express, pg, pino, zod");
  console.log("  devDependencies: eslint, prettier, vitest");
}

main();
