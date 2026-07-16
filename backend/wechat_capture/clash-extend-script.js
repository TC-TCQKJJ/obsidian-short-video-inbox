function main(config) {
  const proxyName = "ChannelsCapture";
  const unsafePolicies = new Set([
    "DIRECT",
    "REJECT",
    "REJECT-DROP",
    "PASS",
    proxyName.toUpperCase(),
  ]);
  const managedRulePrefixes = [
    "PROCESS-NAME,python.exe,",
    "PROCESS-NAME,pythonw.exe,",
    "DOMAIN,weixin.qq.com,",
    "DOMAIN,channels.weixin.qq.com,",
    "DOMAIN,finder.video.qq.com,",
    "DOMAIN-SUFFIX,wxs.qq.com,",
    "DOMAIN-SUFFIX,wxqcloud.qq.com,",
    "DOMAIN-SUFFIX,wxlivecdn.com,",
  ];

  config.proxies = Array.isArray(config.proxies) ? config.proxies : [];
  config.rules = Array.isArray(config.rules) ? config.rules : [];
  config.tun =
    config.tun && typeof config.tun === "object" ? config.tun : {};

  let upstreamPolicy = "GLOBAL";
  for (let index = config.rules.length - 1; index >= 0; index -= 1) {
    const parts = String(config.rules[index]).split(",");
    if (parts[0].trim().toUpperCase() !== "MATCH") {
      continue;
    }
    const policy = (parts[1] || "").trim();
    if (policy && !unsafePolicies.has(policy.toUpperCase())) {
      upstreamPolicy = policy;
    }
    break;
  }

  config.tun.enable = true;
  config.proxies = config.proxies.filter((proxy) => proxy.name !== proxyName);
  config.proxies.push({
    name: proxyName,
    type: "http",
    server: "127.0.0.1",
    port: 2023,
  });

  config.rules = config.rules.filter(
    (rule) =>
      !managedRulePrefixes.some((prefix) => String(rule).startsWith(prefix)),
  );
  config.rules.unshift(
    `PROCESS-NAME,python.exe,${upstreamPolicy}`,
    `PROCESS-NAME,pythonw.exe,${upstreamPolicy}`,
    `DOMAIN,weixin.qq.com,${proxyName}`,
    `DOMAIN,channels.weixin.qq.com,${proxyName}`,
    `DOMAIN,finder.video.qq.com,${proxyName}`,
    `DOMAIN-SUFFIX,wxs.qq.com,${proxyName}`,
    `DOMAIN-SUFFIX,wxqcloud.qq.com,${proxyName}`,
    `DOMAIN-SUFFIX,wxlivecdn.com,${proxyName}`,
  );
  return config;
}
