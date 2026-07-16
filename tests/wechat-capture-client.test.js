const test = require("node:test");
const assert = require("node:assert/strict");

const {
  buildPlaceholderFrontmatter,
  sanitizeJobError,
  shouldCreateCaptureNote,
} = require("../src/wechat-capture-client");

test("placeholder does not contain private media data", () => {
  const text = buildPlaceholderFrontmatter({
    id: "job-1",
    capture_id: "feed-1",
    title: "直播回放",
    author: "作者",
    source_url: "https://weixin.qq.com/sph/example",
  });

  assert.match(text, /status: processing/);
  assert.match(text, /wechat_capture_id: "feed-1"/);
  assert.doesNotMatch(text, /media_url|decrypt_key|request_headers|token=/);
});

test("placeholder YAML escapes quotes and backslashes", () => {
  const text = buildPlaceholderFrontmatter({
    id: "job-1",
    capture_id: "feed-1",
    title: 'A "quoted" \\ title',
    author: "",
    source_url: "",
  });

  assert.match(text, /source_title: "A \\"quoted\\" \\\\ title"/);
});

test("existing capture id is not duplicated", () => {
  assert.equal(shouldCreateCaptureNote(new Set(["feed-1"]), "feed-1"), false);
  assert.equal(shouldCreateCaptureNote(new Set(["feed-1"]), "feed-2"), true);
});

test("job errors remove signed query strings", () => {
  const text = sanitizeJobError(
    "failed https://wxsmw.wxs.qq.com/v.mp4?token=secret",
  );

  assert.doesNotMatch(text, /secret|token=/);
  assert.match(text, /wxsmw\.wxs\.qq\.com\/v\.mp4/);
});
