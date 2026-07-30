const test = require("node:test");
const assert = require("node:assert/strict");

const {
  loadSecureSettings,
  serializeSecureSettings,
} = require("../src/secure-settings");

const DEFAULTS = {
  transcriptionEngine: "doubao",
  doubaoApiKeyConfigured: false,
};

test("loads a legacy plaintext key only for one-time migration", () => {
  const { settings, legacyDoubaoApiKey } = loadSecureSettings(DEFAULTS, {
    doubaoApiKey: "  legacy-key  ",
  });

  assert.equal(legacyDoubaoApiKey, "legacy-key");
  assert.equal(settings.doubaoApiKeyConfigured, true);
  assert.equal("doubaoApiKey" in settings, false);
});

test("keeps a legacy key until migration succeeds", () => {
  const persisted = serializeSecureSettings(
    { ...DEFAULTS, doubaoApiKeyConfigured: true },
    "legacy-key",
  );

  assert.equal(persisted.doubaoApiKey, "legacy-key");
});

test("never serializes a migrated key into the vault", () => {
  const persisted = serializeSecureSettings({
    ...DEFAULTS,
    doubaoApiKeyConfigured: true,
    doubaoApiKey: "must-not-be-written",
  });

  assert.equal("doubaoApiKey" in persisted, false);
  assert.equal(persisted.doubaoApiKeyConfigured, true);
});
