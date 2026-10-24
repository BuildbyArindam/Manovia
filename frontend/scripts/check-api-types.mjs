// Day 26 contract gate: fail when the committed generated types no longer match
// docs/openapi.json (the schema the backend exports). Regenerate with
// `npm run api:types`. Run in CI before `tsc`, so API drift is a red build.
import { execFileSync } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const spec = new URL("../../docs/openapi.json", import.meta.url).pathname;
const committed = new URL("../src/lib/api-schema.d.ts", import.meta.url).pathname;
const dir = mkdtempSync(join(tmpdir(), "api-types-"));
try {
  const fresh = join(dir, "api-schema.d.ts");
  execFileSync("npx", ["--no-install", "openapi-typescript", spec, "-o", fresh], {
    stdio: "ignore",
  });
  if (readFileSync(fresh, "utf8") !== readFileSync(committed, "utf8")) {
    console.error(
      "api-schema.d.ts is out of date with docs/openapi.json. Run `npm run api:types` and commit.",
    );
    process.exit(1);
  }
  console.log("api-schema.d.ts matches docs/openapi.json");
} finally {
  rmSync(dir, { recursive: true, force: true });
}
