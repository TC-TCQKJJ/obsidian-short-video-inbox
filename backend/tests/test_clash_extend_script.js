const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const scriptPath = path.join(
  __dirname,
  "..",
  "wechat_capture",
  "clash-extend-script.js",
);
const source = fs.readFileSync(scriptPath, "utf8");
const context = {};
vm.runInNewContext(`${source}\nglobalThis.applyClashExtension = main;`, context);

function apply(config) {
  const input = JSON.parse(JSON.stringify(config));
  return JSON.parse(
    JSON.stringify(context.applyClashExtension(input, "test")),
  );
}

test("enables TUN and routes Python through the final safe MATCH policy", () => {
  const result = apply({
    tun: { enable: false, stack: "system", mtu: 1500 },
    proxies: [
      { name: "ChannelsCapture", type: "http", server: "old", port: 1 },
    ],
    rules: [
      "PROCESS-NAME,python.exe,DIRECT",
      "DOMAIN,channels.weixin.qq.com,ChannelsCapture",
      "MATCH,OrdinaryProxy",
    ],
  });

  assert.deepEqual(result.tun, {
    enable: true,
    stack: "system",
    mtu: 1500,
  });
  assert.equal(
    result.proxies.filter((proxy) => proxy.name === "ChannelsCapture").length,
    1,
  );
  assert.deepEqual(
    result.proxies.find((proxy) => proxy.name === "ChannelsCapture"),
    {
      name: "ChannelsCapture",
      type: "http",
      server: "127.0.0.1",
      port: 2023,
    },
  );
  assert.ok(result.rules.includes("PROCESS-NAME,python.exe,OrdinaryProxy"));
  assert.ok(result.rules.includes("PROCESS-NAME,pythonw.exe,OrdinaryProxy"));
  assert.ok(!result.rules.includes("PROCESS-NAME,python.exe,DIRECT"));
});

test("falls back to GLOBAL when MATCH is missing or unsafe", () => {
  for (const rules of [
    [],
    ["MATCH,DIRECT"],
    ["MATCH,ChannelsCapture"],
    ["MATCH,REJECT"],
  ]) {
    const result = apply({ rules, proxies: [] });
    assert.ok(result.rules.includes("PROCESS-NAME,python.exe,GLOBAL"));
    assert.ok(result.rules.includes("PROCESS-NAME,pythonw.exe,GLOBAL"));
  }
});

test("is idempotent for managed proxies and rules", () => {
  const initial = {
    tun: { stack: "system" },
    proxies: [],
    rules: ["DOMAIN,example.com,DIRECT", "MATCH,OrdinaryProxy"],
  };
  const result = apply(apply(initial));

  assert.equal(
    result.proxies.filter((proxy) => proxy.name === "ChannelsCapture").length,
    1,
  );
  const managed = result.rules.filter(
    (rule) =>
      rule.startsWith("PROCESS-NAME,python") ||
      rule.includes(",ChannelsCapture"),
  );
  assert.equal(new Set(managed).size, managed.length);
  assert.equal(
    result.rules.filter((rule) => rule === "DOMAIN,example.com,DIRECT").length,
    1,
  );
});
