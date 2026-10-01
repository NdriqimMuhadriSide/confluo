// Fails when a language file is missing a key that English has (or has extras),
// or when a placeholder like {email} differs between languages. Run by `pnpm lint`.
import { readFileSync } from "node:fs";

const LOCALES = ["en", "nl", "fr", "de", "sq"];
const load = (l) => JSON.parse(readFileSync(new URL(`../messages/${l}.json`, import.meta.url)));

function flatten(obj, prefix = "") {
  return Object.entries(obj).flatMap(([k, v]) =>
    typeof v === "object" ? flatten(v, `${prefix}${k}.`) : [[`${prefix}${k}`, v]],
  );
}

const placeholders = (s) => [...s.matchAll(/\{(\w+)\}/g)].map((m) => m[1]).sort().join(",");
const en = new Map(flatten(load("en")));
let problems = 0;
for (const locale of LOCALES.slice(1)) {
  const other = new Map(flatten(load(locale)));
  for (const [key, text] of en) {
    if (!other.has(key)) {
      console.error(`${locale}: missing ${key}`);
      problems++;
    } else if (placeholders(text) !== placeholders(other.get(key))) {
      console.error(`${locale}: placeholders differ in ${key}`);
      problems++;
    }
  }
  for (const key of other.keys()) {
    if (!en.has(key)) {
      console.error(`${locale}: extra key ${key}`);
      problems++;
    }
  }
}
if (problems) process.exit(1);
console.log(`messages: ${en.size} keys in ${LOCALES.length} languages, all consistent`);
