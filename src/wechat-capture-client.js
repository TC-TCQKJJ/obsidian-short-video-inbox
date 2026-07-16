function yamlEscape(value) {
  return String(value ?? "")
    .replace(/\\/g, "\\\\")
    .replace(/"/g, '\\"');
}

function buildPlaceholderFrontmatter(job, capturedAt = new Date()) {
  const title = String(job.title || "微信视频号采集").trim();
  const author = String(job.author || "").trim();
  const source = String(job.source_url || "").trim();
  const timestamp = localDateTime(capturedAt);
  return [
    "---",
    "type: inbox",
    "status: processing",
    `source: "${yamlEscape(source)}"`,
    `source_title: "${yamlEscape(title)}"`,
    'source_site: "微信视频号"',
    'platform: "wechat_channels"',
    `wechat_capture_id: "${yamlEscape(job.capture_id || "")}"`,
    `wechat_capture_job_id: "${yamlEscape(job.id || "")}"`,
    `author: "${yamlEscape(author)}"`,
    `captured_at: "${timestamp}"`,
    "---",
    "",
    `# ${title}`,
    "",
    "> 正在提取完整音频并转写。",
    "",
  ].join("\n");
}

function shouldCreateCaptureNote(existingCaptureIds, captureId) {
  const normalized = String(captureId || "");
  return Boolean(normalized) && !existingCaptureIds.has(normalized);
}

function sanitizeJobError(value) {
  return String(value || "")
    .replace(/https?:\/\/[^\s]+/gi, (raw) => {
      try {
        const parsed = new URL(raw);
        return `${parsed.protocol}//${parsed.host}${parsed.pathname}`;
      } catch {
        return "[redacted URL]";
      }
    })
    .slice(0, 2000);
}

function localDateTime(date) {
  const pad = (value) => String(value).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(
    date.getDate(),
  )} ${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

module.exports = {
  buildPlaceholderFrontmatter,
  sanitizeJobError,
  shouldCreateCaptureNote,
  yamlEscape,
};
