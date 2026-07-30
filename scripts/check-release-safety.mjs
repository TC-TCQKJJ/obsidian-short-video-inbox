import { execFileSync } from "node:child_process";
import { readFileSync, statSync } from "node:fs";

const trackedFiles = execFileSync(
  "git",
  ["ls-files", "-z"],
  { encoding: "utf8" },
)
  .split("\0")
  .filter(Boolean);

const forbiddenPaths = [
  /(^|\/)data\.json$/i,
  /(^|\/)(?:output|queue|jobs|secrets)\//i,
  /(^|\/)auth-token$/i,
  /\.(?:log|pem|key|p12|pfx)$/i,
  /(^|\/)docs\/images\/.*\.(?:png|jpe?g|webp|gif)$/i,
];
const pathViolations = trackedFiles.filter((file) =>
  forbiddenPaths.some((pattern) => pattern.test(file)),
);

const windowsHome = new RegExp(
  "[A-Za-z]:[\\\\/]Users[\\\\/](?!example(?:[\\\\/]|$))",
  "i",
);
const unixHome = new RegExp("/" + "Users/(?!example(?:/|$))", "i");
const contentViolations = [];
for (const file of trackedFiles) {
  const stats = statSync(file);
  if (stats.size > 2 * 1024 * 1024) {
    continue;
  }
  const content = readFileSync(file);
  if (content.includes(0)) {
    continue;
  }
  const text = content.toString("utf8");
  if (windowsHome.test(text) || unixHome.test(text)) {
    contentViolations.push(file);
  }
}

const violations = [...new Set([...pathViolations, ...contentViolations])];
if (violations.length) {
  process.stderr.write(
    `Release safety check rejected tracked files:\n${violations
      .map((file) => `- ${file}`)
      .join("\n")}\n`,
  );
  process.exit(1);
}

process.stdout.write(
  `Release safety check passed for ${trackedFiles.length} tracked files.\n`,
);
