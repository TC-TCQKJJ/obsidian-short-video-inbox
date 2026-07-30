function loadSecureSettings(defaultSettings, persistedSettings) {
  const persisted =
    persistedSettings &&
    typeof persistedSettings === "object" &&
    !Array.isArray(persistedSettings)
      ? { ...persistedSettings }
      : {};
  const legacyDoubaoApiKey =
    typeof persisted.doubaoApiKey === "string"
      ? persisted.doubaoApiKey.trim()
      : "";
  delete persisted.doubaoApiKey;

  const settings = {
    ...defaultSettings,
    ...persisted,
    doubaoApiKeyConfigured: Boolean(
      persisted.doubaoApiKeyConfigured || legacyDoubaoApiKey,
    ),
  };
  return { settings, legacyDoubaoApiKey };
}

function serializeSecureSettings(settings, legacyDoubaoApiKey = "") {
  const persisted = { ...settings };
  delete persisted.doubaoApiKey;
  const legacyKey = String(legacyDoubaoApiKey || "").trim();
  if (legacyKey) {
    persisted.doubaoApiKey = legacyKey;
  }
  return persisted;
}

module.exports = {
  loadSecureSettings,
  serializeSecureSettings,
};
