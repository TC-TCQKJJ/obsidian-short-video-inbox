const {
  Notice,
  Plugin,
  PluginSettingTab,
  Setting,
  SuggestModal,
  TFile,
  normalizePath,
  requestUrl,
  setIcon,
} = require("obsidian");
const fs = require("fs/promises");
const path = require("path");
const os = require("os");
const net = require("net");
const { spawn } = require("child_process");
const {
  buildPlaceholderFrontmatter,
  sanitizeJobError,
  shouldCreateCaptureNote,
} = require("./wechat-capture-client");
const {
  loadSecureSettings,
  serializeSecureSettings,
} = require("./secure-settings");

const DEFAULT_SETTINGS = {
  inboxFolder: "00 收件箱",
  attachmentFolder: "00 收件箱/attachments",
  backendUrl: "http://127.0.0.1:5050",
  whisperModel: "base",
  transcriptionEngine: "doubao",
  doubaoApiKeyConfigured: false,
  doubaoResourceId: "volc.seedasr.auc",
  whisperFallback: true,
  audioCaptureDevice: "",
  allowMicrophoneCapture: false,
  scanIntervalMs: 3000,
  wechatCaptureJobs: {},
};

const DOUYIN_URL_RE =
  /https?:\/\/(?:v\.douyin\.com|www\.douyin\.com|www\.iesdouyin\.com|m\.douyin\.com)[^\s\]]*/i;
const WECHAT_CHANNELS_URL_RE =
  /https?:\/\/(?:channels\.weixin\.qq\.com|finder\.video\.qq\.com|weixin110\.qq\.com|mp\.weixin\.qq\.com|weixin\.qq\.com\/sph)[^\s\]]*/i;
const PROCESSING_PREFIX = "captureProcessing_";
const LEGACY_PROCESSING_PREFIX = "douyinProcessing_";
const PENDING_PREFIXES = [
  "toBeSaved_",
  LEGACY_PROCESSING_PREFIX,
  PROCESSING_PREFIX,
];
const FAILED_PREFIX = "captureFailed_";
const LEGACY_FAILED_PREFIX = "douyinFailed_";
const PLATFORMS = {
  douyin: {
    id: "douyin",
    label: "抖音",
    sourceSite: "抖音",
    tag: "douyin",
    itemIdKey: "douyin_id",
    attachmentPrefix: "douyin",
    urlRe: DOUYIN_URL_RE,
  },
  wechat_channels: {
    id: "wechat_channels",
    label: "微信视频号",
    sourceSite: "微信视频号",
    tag: "wechat-channels",
    itemIdKey: "wechat_channels_id",
    attachmentPrefix: "wechat-channels",
    urlRe: WECHAT_CHANNELS_URL_RE,
  },
};

class DouyinInboxBridgePlugin extends Plugin {
  async onload() {
    await this.loadSettings();
    this.processing = new Set();
    this.backendProcess = null;
    this.scanning = false;
    this.wechatJobSyncing = false;
    this.localCaptureActive = false;
    this.missingDoubaoKeyNotified = false;
    this.manualButtonTimers = new Map();

    this.addSettingTab(new DouyinInboxBridgeSettingTab(this.app, this));
    this.addCommand({
      id: "process-pending-douyin-links",
      name: "处理待采集和失败的抖音链接",
      callback: () => void this.scanQueues(true),
    });
    this.addCommand({
      id: "check-doubao-environment",
      name: "检查豆包转写运行环境",
      callback: () => void this.checkDoubaoEnvironment(),
    });
    this.addCommand({
      id: "transcribe-current-note-with-doubao",
      name: "用豆包转写当前笔记的音轨",
      checkCallback: (checking) => {
        const file = this.app.workspace.getActiveFile();
        const canRun = this.isDouyinVideoNote(file);
        if (!checking && canRun) {
          void this.manualTranscribe(file);
        }
        return canRun;
      },
    });
    this.addCommand({
      id: "start-local-playback-capture",
      name: "开始录制本地播放音频",
      callback: () => void this.startLocalPlaybackCapture(),
    });
    this.addCommand({
      id: "stop-local-playback-capture",
      name: "停止录制并写入当前笔记",
      callback: () => void this.stopLocalPlaybackCapture(),
    });
    this.addCommand({
      id: "extract-current-wechat-radium-audio",
      name: "从 PC 微信当前视频号提取完整音频",
      callback: () => void this.extractCurrentWechatRadiumAudio(),
    });
    this.addCommand({
      id: "check-safe-wechat-capture-status",
      name: "微信视频号安全采集：检查状态",
      callback: () => void this.checkWechatCaptureStatus(),
    });
    this.addCommand({
      id: "retry-failed-safe-wechat-captures",
      name: "微信视频号安全采集：重试失败任务",
      callback: () => void this.retryFailedWechatCaptureJobs(),
    });
    this.registerMarkdownPostProcessor((_element, context) => {
      this.scheduleManualTranscribeButton(context.sourcePath);
    });

    this.registerEvent(
      this.app.vault.on("create", (file) => {
        if (this.isPendingQueue(file)) {
          window.setTimeout(() => void this.processQueue(file, false), 200);
        }
      }),
    );

    this.app.workspace.onLayoutReady(() => {
      void this.initializeBackendState();
      const activeFile = this.app.workspace.getActiveFile();
      if (activeFile) {
        this.scheduleManualTranscribeButton(activeFile.path);
      }
      this.registerInterval(
        window.setInterval(
          () => {
            void this.scanQueues(false);
            void this.syncWechatCaptureJobs(false);
          },
          this.settings.scanIntervalMs,
        ),
      );
      this.registerInterval(
        window.setInterval(() => {
          const file = this.app.workspace.getActiveFile();
          if (this.isDouyinVideoNote(file)) {
            this.scheduleManualTranscribeButton(file.path);
          }
        }, 1500),
      );
    });
  }

  onunload() {
    for (const timer of this.manualButtonTimers.values()) {
      window.clearTimeout(timer);
    }
    this.manualButtonTimers.clear();
    if (this.backendProcess && !this.backendProcess.killed) {
      this.backendProcess.kill();
    }
  }

  async loadSettings() {
    const { settings, legacyDoubaoApiKey } = loadSecureSettings(
      DEFAULT_SETTINGS,
      await this.loadData(),
    );
    this.settings = settings;
    this.legacyDoubaoApiKey = legacyDoubaoApiKey;
    if (
      !this.settings.wechatCaptureJobs ||
      typeof this.settings.wechatCaptureJobs !== "object" ||
      Array.isArray(this.settings.wechatCaptureJobs)
    ) {
      this.settings.wechatCaptureJobs = {};
    }
  }

  async saveSettings() {
    await this.saveData(
      serializeSecureSettings(this.settings, this.legacyDoubaoApiKey),
    );
  }

  async initializeBackendState() {
    try {
      await this.ensureBackend();
      await this.migrateLegacyDoubaoApiKey();
      await this.refreshDoubaoApiKeyStatus();
    } catch (error) {
      if (this.legacyDoubaoApiKey) {
        new Notice(
          `豆包 API Key 安全迁移尚未完成：${this.errorMessage(error)}`,
          12000,
        );
      }
    }
    void this.scanQueues(false);
    void this.syncWechatCaptureJobs(false);
  }

  hasDoubaoApiKey() {
    return Boolean(
      this.settings.doubaoApiKeyConfigured || this.legacyDoubaoApiKey,
    );
  }

  legacyDoubaoApiKeyPayload() {
    return this.legacyDoubaoApiKey || "";
  }

  async migrateLegacyDoubaoApiKey() {
    if (!this.legacyDoubaoApiKey) {
      return;
    }
    await this.wechatCaptureRequest("/api/secrets/doubao", "PUT", {
      api_key: this.legacyDoubaoApiKey,
    });
    this.legacyDoubaoApiKey = "";
    this.settings.doubaoApiKeyConfigured = true;
    await this.saveSettings();
    new Notice("豆包 API Key 已迁移到本机 DPAPI 加密存储。", 7000);
  }

  async refreshDoubaoApiKeyStatus() {
    const data = await this.wechatCaptureRequest("/api/secrets/doubao");
    const configured = data.configured === true;
    if (this.settings.doubaoApiKeyConfigured !== configured) {
      this.settings.doubaoApiKeyConfigured = configured;
      await this.saveSettings();
    }
    return configured;
  }

  async setDoubaoApiKey(apiKey) {
    const value = String(apiKey || "").trim();
    if (!value) {
      throw new Error("豆包 API Key 不能为空");
    }
    await this.ensureBackend();
    await this.wechatCaptureRequest("/api/secrets/doubao", "PUT", {
      api_key: value,
    });
    this.legacyDoubaoApiKey = "";
    this.settings.doubaoApiKeyConfigured = true;
    this.missingDoubaoKeyNotified = false;
    await this.saveSettings();
  }

  async clearDoubaoApiKey() {
    await this.ensureBackend();
    await this.wechatCaptureRequest("/api/secrets/doubao", "DELETE");
    this.legacyDoubaoApiKey = "";
    this.settings.doubaoApiKeyConfigured = false;
    await this.saveSettings();
  }

  wechatCaptureTokenPath() {
    const localAppData =
      process.env.LOCALAPPDATA ||
      path.join(os.homedir(), "AppData", "Local");
    return path.join(
      localAppData,
      "Xiaolou",
      "WechatCapture",
      "auth-token",
    );
  }

  async loadWechatCaptureToken() {
    const token = (await fs.readFile(this.wechatCaptureTokenPath(), "utf8")).trim();
    if (!token) {
      throw new Error("安全采集 token 不可用");
    }
    return token;
  }

  async authenticatedBackendHeaders() {
    return {
      "Content-Type": "application/json",
      "X-Xiaolou-Capture-Token": await this.loadWechatCaptureToken(),
    };
  }

  async wechatCaptureRequest(endpoint, method = "GET", body = null) {
    const response = await requestUrl({
      url: `${this.settings.backendUrl}${endpoint}`,
      method,
      throw: false,
      headers: await this.authenticatedBackendHeaders(),
      ...(body === null ? {} : { body: JSON.stringify(body) }),
    });
    const data = response.json;
    if (response.status >= 400 || !data?.success) {
      throw new Error(
        sanitizeJobError(
          data?.error || `安全采集后端返回 HTTP ${response.status}`,
        ),
      );
    }
    return data;
  }

  wechatCaptureStartSettings() {
    return {
      model: this.settings.whisperModel,
      transcription_engine: this.settings.transcriptionEngine,
      doubao_api_key:
        this.settings.transcriptionEngine === "doubao"
          ? this.legacyDoubaoApiKeyPayload()
          : "",
      doubao_resource_id: this.settings.doubaoResourceId,
      whisper_fallback: this.settings.whisperFallback,
    };
  }

  async syncWechatCaptureJobs(showNotice) {
    if (this.wechatJobSyncing) {
      return;
    }
    this.wechatJobSyncing = true;
    try {
      const { jobs = [] } = await this.wechatCaptureRequest(
        "/api/wechat-capture/jobs",
      );
      for (const job of jobs) {
        if (job.status === "waiting_for_obsidian") {
          await this.startWechatCaptureJob(job);
        } else if (job.status === "completed") {
          await this.completeWechatCaptureJob(job);
        } else if (job.status === "failed") {
          await this.failWechatCaptureJob(job);
        }
      }
      if (showNotice) {
        new Notice(`安全采集任务已同步：${jobs.length} 个`);
      }
    } catch (error) {
      if (showNotice) {
        new Notice(`安全采集同步失败：${this.errorMessage(error)}`, 10000);
      }
    } finally {
      this.wechatJobSyncing = false;
    }
  }

  async startWechatCaptureJob(job) {
    const existingIds = this.wechatCaptureIds();
    let noteFile =
      this.wechatCaptureMappedNote(job.id) ||
      this.findWechatCaptureNote(job.capture_id);
    if (noteFile && !this.settings.wechatCaptureJobs[job.id]) {
      this.settings.wechatCaptureJobs[job.id] = noteFile.path;
      await this.saveSettings();
    }
    if (!noteFile && !shouldCreateCaptureNote(existingIds, job.capture_id)) {
      await this.wechatCaptureRequest(
        `/api/wechat-capture/jobs/${encodeURIComponent(job.id)}/ack`,
        "POST",
        {},
      );
      return;
    }

    if (!noteFile) {
      await this.ensureVaultFolder(this.settings.inboxFolder);
      const baseName = `${this.localTimestamp(new Date())} ${this.safeName(
        job.title || "微信视频号采集",
        40,
      )}`;
      const notePath = this.uniqueNotePath(baseName);
      noteFile = await this.app.vault.create(
        notePath,
        buildPlaceholderFrontmatter(job),
      );
      this.settings.wechatCaptureJobs[job.id] = noteFile.path;
      await this.saveSettings();
    }

    await this.wechatCaptureRequest(
      `/api/wechat-capture/jobs/${encodeURIComponent(job.id)}/start`,
      "POST",
      this.wechatCaptureStartSettings(),
    );
  }

  async completeWechatCaptureJob(job) {
    let noteFile =
      this.wechatCaptureMappedNote(job.id) ||
      this.findWechatCaptureNote(job.capture_id);
    if (!noteFile) {
      await this.ensureVaultFolder(this.settings.inboxFolder);
      noteFile = await this.app.vault.create(
        this.uniqueNotePath(
          `${this.localTimestamp(new Date())} ${this.safeName(
            job.title || "微信视频号采集",
            40,
          )}`,
        ),
        buildPlaceholderFrontmatter(job),
      );
    }
    await this.writeRemoteAudioResult(noteFile, job.result || {});
    await this.app.fileManager.processFrontMatter(noteFile, (frontmatter) => {
      frontmatter.status = "raw";
      frontmatter.wechat_capture_id = job.capture_id || "";
      frontmatter.wechat_capture_job_id = job.id;
    });
    await this.wechatCaptureRequest(
      `/api/wechat-capture/jobs/${encodeURIComponent(job.id)}/ack`,
      "POST",
      {},
    );
    delete this.settings.wechatCaptureJobs[job.id];
    await this.saveSettings();
    new Notice(`微信视频号音频已写入：${job.title || noteFile.basename}`, 7000);
  }

  async failWechatCaptureJob(job) {
    const noteFile = this.wechatCaptureMappedNote(job.id);
    if (!(noteFile instanceof TFile)) {
      return;
    }
    await this.app.fileManager.processFrontMatter(noteFile, (frontmatter) => {
      frontmatter.status = "capture_failed";
      frontmatter.wechat_capture_id = job.capture_id || "";
      frontmatter.wechat_capture_job_id = job.id;
    });
    const marker = "## 采集错误";
    const error = sanitizeJobError(job.error || "未知错误");
    const section = `${marker}\n\n${error}\n`;
    await this.app.vault.process(noteFile, (content) =>
      content.includes(marker)
        ? content.replace(/## 采集错误\r?\n[\s\S]*$/, section)
        : `${content.trimEnd()}\n\n${section}`,
    );
  }

  wechatCaptureIds() {
    const ids = new Set();
    for (const file of this.app.vault.getMarkdownFiles()) {
      const frontmatter =
        this.app.metadataCache.getFileCache(file)?.frontmatter || {};
      const value =
        frontmatter.wechat_capture_id ||
        (frontmatter.platform === "wechat_channels"
          ? frontmatter.wechat_channels_id || frontmatter.platform_item_id
          : "");
      if (value) {
        ids.add(String(value));
      }
    }
    return ids;
  }

  wechatCaptureMappedNote(jobId) {
    const notePath = this.settings.wechatCaptureJobs?.[jobId];
    const file = notePath
      ? this.app.vault.getAbstractFileByPath(notePath)
      : null;
    return file instanceof TFile ? file : null;
  }

  findWechatCaptureNote(captureId) {
    if (!captureId) {
      return null;
    }
    for (const file of this.app.vault.getMarkdownFiles()) {
      const frontmatter =
        this.app.metadataCache.getFileCache(file)?.frontmatter || {};
      if (String(frontmatter.wechat_capture_id || "") === String(captureId)) {
        return file;
      }
    }
    return null;
  }

  async retryFailedWechatCaptureJobs() {
    try {
      const { jobs = [] } = await this.wechatCaptureRequest(
        "/api/wechat-capture/jobs",
      );
      const failed = jobs.filter((job) => job.status === "failed");
      for (const job of failed) {
        const noteFile = this.wechatCaptureMappedNote(job.id);
        if (noteFile) {
          await this.app.fileManager.processFrontMatter(noteFile, (frontmatter) => {
            frontmatter.status = "processing";
          });
        }
        await this.wechatCaptureRequest(
          `/api/wechat-capture/jobs/${encodeURIComponent(job.id)}/start`,
          "POST",
          this.wechatCaptureStartSettings(),
        );
      }
      new Notice(`已重试 ${failed.length} 个安全采集任务`);
    } catch (error) {
      new Notice(`重试失败：${this.errorMessage(error)}`, 10000);
    }
  }

  async checkWechatCaptureStatus() {
    let tokenAvailable = false;
    let backendAvailable = false;
    let jobs = [];
    try {
      await this.loadWechatCaptureToken();
      tokenAvailable = true;
      const result = await this.wechatCaptureRequest(
        "/api/wechat-capture/jobs",
      );
      jobs = result.jobs || [];
      backendAvailable = true;
    } catch {
      // Status only; never surface token or request details.
    }
    const hostAvailable = await this.isLocalPortOpen(2023, 500);
    const counts = jobs.reduce((result, job) => {
      result[job.status] = (result[job.status] || 0) + 1;
      return result;
    }, {});
    new Notice(
      [
        `后端：${backendAvailable ? "正常" : "不可用"}`,
        `采集宿主：${hostAvailable ? "运行中" : "未运行"}`,
        `token：${tokenAvailable ? "可用" : "不可用"}`,
        `等待 ${counts.waiting_for_obsidian || 0} / 处理中 ${
          counts.processing || 0
        } / 完成 ${counts.completed || 0} / 失败 ${counts.failed || 0}`,
      ].join("\n"),
      10000,
    );
  }

  isLocalPortOpen(port, timeoutMs) {
    return new Promise((resolve) => {
      const socket = net.createConnection({ host: "127.0.0.1", port });
      const finish = (value) => {
        socket.destroy();
        resolve(value);
      };
      socket.setTimeout(timeoutMs);
      socket.once("connect", () => finish(true));
      socket.once("timeout", () => finish(false));
      socket.once("error", () => finish(false));
    });
  }

  isPendingQueue(file) {
    return (
      file instanceof TFile &&
      file.extension === "json" &&
      PENDING_PREFIXES.some((prefix) => file.basename.startsWith(prefix))
    );
  }

  async scanQueues(includeFailed) {
    if (this.scanning) {
      return;
    }
    this.scanning = true;
    try {
      const files = this.app.vault.getFiles().filter((file) => {
        if (this.isPendingQueue(file)) {
          return true;
        }
        return (
          includeFailed &&
          file.extension === "json" &&
          (file.basename.startsWith(FAILED_PREFIX) ||
            file.basename.startsWith(LEGACY_FAILED_PREFIX))
        );
      });
      for (const file of files) {
        await this.processQueue(file, includeFailed);
      }
    } finally {
      this.scanning = false;
    }
  }

  async processQueue(file, retryFailed) {
    if (!(file instanceof TFile) || this.processing.has(file.path)) {
      return;
    }

    const originalPath = file.path;
    this.processing.add(originalPath);
    let claimedFile = file;
    let entry;
    try {
      entry = JSON.parse(await this.app.vault.read(file));
    } catch {
      this.processing.delete(originalPath);
      return;
    }

    const source = this.extractSupportedUrl(entry.url || "");
    if (!source) {
      this.processing.delete(originalPath);
      return;
    }
    const { url: sourceUrl, platform } = source;

    if (
      (file.basename.startsWith(FAILED_PREFIX) ||
        file.basename.startsWith(LEGACY_FAILED_PREFIX)) &&
      !retryFailed
    ) {
      this.processing.delete(originalPath);
      return;
    }

    try {
      claimedFile = await this.claimQueue(file);

      await this.ensureBackend();
      const data = await this.extractContent(sourceUrl, platform);
      if (await this.hasCaptured(data.video_id, platform)) {
        await this.app.fileManager.trashFile(claimedFile);
        await this.cleanupOutput(data.out_dir);
        new Notice(`这个${platform.label}视频已经在知识库中。`);
        return;
      }

      const note = await this.writeInboxNote(data, sourceUrl, platform);
      await this.app.fileManager.trashFile(claimedFile);
      await this.cleanupOutput(data.out_dir);
      new Notice(`${platform.label}内容已进入收件箱：${note.basename}`, 6000);
    } catch (error) {
      await this.markFailed(claimedFile, entry, error);
      new Notice(`${platform.label}采集失败：${this.errorMessage(error)}`, 10000);
    } finally {
      this.processing.delete(originalPath);
      if (claimedFile) {
        this.processing.delete(claimedFile.path);
      }
    }
  }

  extractDouyinUrl(text) {
    const match = String(text).match(DOUYIN_URL_RE);
    return match ? match[0].replace(/[.,;)\]]+$/, "") : null;
  }

  extractSupportedUrl(text) {
    const value = String(text);
    for (const platform of Object.values(PLATFORMS)) {
      const match = value.match(platform.urlRe);
      if (match) {
        return {
          platform,
          url: match[0].replace(/[.,;)\]]+$/, ""),
        };
      }
    }
    return null;
  }

  async claimQueue(file) {
    if (
      file.basename.startsWith(LEGACY_PROCESSING_PREFIX) ||
      file.basename.startsWith(PROCESSING_PREFIX)
    ) {
      this.processing.add(file.path);
      return file;
    }

    const suffix = file.name
      .replace(/^toBeSaved_/, "")
      .replace(/^captureFailed_/, "")
      .replace(/^douyinFailed_/, "");
    const parent = file.parent?.path;
    const target = normalizePath(
      `${parent && parent !== "/" ? `${parent}/` : ""}${PROCESSING_PREFIX}${suffix}`,
    );
    await this.app.fileManager.renameFile(file, target);
    const claimed = this.app.vault.getAbstractFileByPath(target);
    if (!(claimed instanceof TFile)) {
      throw new Error("无法认领 Share to Save 队列文件");
    }
    this.processing.add(claimed.path);
    return claimed;
  }

  async ensureBackend() {
    const health = await this.backendHealth();
    if (health.success) {
      return;
    }

    if (!this.backendProcess || this.backendProcess.killed) {
      const root = path.join(
        process.env.LOCALAPPDATA || path.join(os.homedir(), "AppData", "Local"),
        "Xiaolou",
        "DouyinCapture",
      );
      const python = path.join(root, ".venv", "Scripts", "python.exe");
      const backend = path.join(root, "backend");
      await fs.access(python);
      await fs.access(path.join(backend, "web", "app.py"));

      this.backendProcess = spawn(
        python,
        [
          "-m",
          "flask",
          "--app",
          "web.app",
          "run",
          "--host",
          "127.0.0.1",
          "--port",
          "5050",
        ],
        {
          cwd: backend,
          windowsHide: true,
          stdio: "ignore",
        },
      );
      this.backendProcess.once("exit", () => {
        this.backendProcess = null;
      });
    }

    const deadline = Date.now() + 30000;
    while (Date.now() < deadline) {
      await this.sleep(500);
      if (await this.backendHealthy()) {
        return;
      }
    }
    throw new Error("本地抖音转写服务未能启动");
  }

  async backendHealthy() {
    return (await this.backendHealth()).success;
  }

  async backendHealth() {
    try {
      const response = await requestUrl({
        url: `${this.settings.backendUrl}/api/health`,
        method: "GET",
      });
      if (response.status !== 200 || response.json?.success !== true) {
        return { success: false };
      }
      return response.json;
    } catch {
      return { success: false };
    }
  }

  async extractContent(sourceUrl, platform) {
    let engine = this.settings.transcriptionEngine;
    if (engine === "doubao" && !this.hasDoubaoApiKey()) {
      if (!this.missingDoubaoKeyNotified) {
        new Notice("豆包 API Key 尚未填写，本次使用本地 Whisper。", 6000);
        this.missingDoubaoKeyNotified = true;
      }
      if (!this.settings.whisperFallback) {
        throw new Error("请先在“抖音收件箱桥接”设置中填写豆包 API Key");
      }
      engine = "whisper";
    }

    const response = await requestUrl({
      url: `${this.settings.backendUrl}/api/video/extract`,
      method: "POST",
      headers: await this.authenticatedBackendHeaders(),
      body: JSON.stringify({
        url: sourceUrl,
        platform: platform.id,
        model: this.settings.whisperModel,
        transcription_engine: engine,
        doubao_api_key:
          engine === "doubao" ? this.legacyDoubaoApiKeyPayload() : "",
        doubao_resource_id:
          engine === "doubao" ? this.settings.doubaoResourceId : "",
        whisper_fallback: this.settings.whisperFallback,
      }),
    });
    const data = response.json;
      if (response.status >= 400 || !data?.success) {
        throw new Error(data?.error || `后端返回 HTTP ${response.status}`);
      }
      if (engine === "doubao" && data.transcription_engine === "whisper") {
        const reason = data.transcription_error || "未知错误";
        new Notice(`豆包转写失败，已回退 Whisper：${reason}`, 12000);
      }
      return data;
  }

  async checkDoubaoEnvironment() {
    try {
      await this.ensureBackend();
      await this.refreshDoubaoApiKeyStatus();
      const keyStatus = this.hasDoubaoApiKey()
        ? "API Key 已填写"
        : "API Key 尚未填写";
      new Notice(
        `豆包转写环境正常；${keyStatus}；资源：${this.settings.doubaoResourceId}。`,
        6000,
      );
    } catch (error) {
      new Notice(`豆包转写环境异常：${this.errorMessage(error)}`, 10000);
    }
  }

  async showLocalAudioDevices() {
    try {
      await this.ensureBackend();
      const response = await requestUrl({
        url: `${this.settings.backendUrl}/api/local-audio/devices`,
        method: "GET",
        throw: false,
      });
      const data = response.json;
      if (response.status >= 400 || !data?.success) {
        throw new Error(data?.error || `后端返回 HTTP ${response.status}`);
      }
      const devices = data.devices?.length ? data.devices.join("；") : "无";
      const recommended = data.recommended_device || "未找到系统回环设备";
      new Notice(`可见音频设备：${devices}\n推荐：${recommended}`, 15000);
    } catch (error) {
      new Notice(`查看录音设备失败：${this.errorMessage(error)}`, 10000);
    }
  }

  isDouyinVideoNote(file) {
    if (!(file instanceof TFile)) {
      return false;
    }
    const frontmatter =
      this.app.metadataCache.getFileCache(file)?.frontmatter || {};
    return (
      ["抖音", "微信视频号"].includes(frontmatter.source_site) &&
      frontmatter.content_type === "video"
    );
  }

  scheduleManualTranscribeButton(sourcePath) {
    const previous = this.manualButtonTimers.get(sourcePath);
    if (previous) {
      window.clearTimeout(previous);
    }
    const timer = window.setTimeout(() => {
      this.manualButtonTimers.delete(sourcePath);
      void this.injectManualTranscribeButton(sourcePath);
    }, 1000);
    this.manualButtonTimers.set(sourcePath, timer);
  }

  async injectManualTranscribeButton(sourcePath) {
    const file = this.app.vault.getAbstractFileByPath(sourcePath);
    if (!this.isDouyinVideoNote(file)) {
      return;
    }

    for (const leaf of this.app.workspace.getLeavesOfType("markdown")) {
      if (leaf.view?.file?.path !== sourcePath) {
        continue;
      }
      const root = leaf.view.containerEl;
      const actions = Array.from(
        root.querySelectorAll(".douyin-doubao-action"),
      );
      const mediaPlayer = root.querySelector(
        '[data-media-player][data-view-type="audio"]',
      );
      const audioHeading = Array.from(root.querySelectorAll("h2")).find(
        (heading) => heading.textContent?.trim() === "音轨",
      );
      const anchor =
        mediaPlayer?.closest(".mx-player") || mediaPlayer || audioHeading;
      if (!anchor) {
        continue;
      }
      const frontmatter =
        this.app.metadataCache.getFileCache(file)?.frontmatter || {};
      const primary =
        actions.find((action) => action.previousElementSibling === anchor) ||
        actions[0] ||
        this.createManualTranscribeButton(file, frontmatter);
      for (const action of actions) {
        if (action !== primary) {
          action.remove();
        }
      }
      if (primary.previousElementSibling !== anchor) {
        anchor.insertAdjacentElement("afterend", primary);
      }
      return;
    }
  }

  createManualTranscribeButton(file, frontmatter) {
    const container = document.createElement("div");
    container.className = "douyin-doubao-action";
    const button = container.createEl("button");
    button.type = "button";
    const icon = button.createSpan({ cls: "douyin-doubao-action-icon" });
    setIcon(icon, "audio-lines");
    button.createSpan({
      text:
        frontmatter.transcription_engine === "doubao"
          ? "用豆包重新转写"
          : "用豆包转写",
    });
    const status = container.createSpan({
      cls: "douyin-doubao-action-status",
    });
    button.addEventListener("click", () => {
      void this.manualTranscribe(file, button, status);
    });
    return container;
  }

  async manualTranscribe(noteFile, button = null, status = null) {
    if (!this.hasDoubaoApiKey()) {
      new Notice("请先在插件设置中填写豆包 API Key。", 6000);
      return;
    }

    if (button) {
      button.disabled = true;
    }
    status?.setText("正在调用豆包…");
    new Notice("正在调用豆包转写现有音轨…", 5000);
    try {
      const noteContent = await this.app.vault.read(noteFile);
      const audioFile = this.findAudioFile(noteContent, noteFile.path);
      if (!(audioFile instanceof TFile)) {
        throw new Error("笔记中未找到可用的 MP3 音轨");
      }

      await this.ensureBackend();
      const frontmatter =
        this.app.metadataCache.getFileCache(noteFile)?.frontmatter || {};
      const response = await requestUrl({
        url: `${this.settings.backendUrl}/api/audio/transcribe`,
        method: "POST",
        throw: false,
        headers: await this.authenticatedBackendHeaders(),
        body: JSON.stringify({
          audio_path: this.localFilePath(audioFile),
          doubao_api_key: this.legacyDoubaoApiKeyPayload(),
          doubao_resource_id: this.settings.doubaoResourceId,
          title: frontmatter.source_title || noteFile.basename,
          author: frontmatter.author || "",
        }),
      });
      const data = response.json;
      if (response.status >= 400 || !data?.success) {
        throw new Error(data?.error || `后端返回 HTTP ${response.status}`);
      }

      await this.applyManualTranscript(noteFile, data.text || "");
      status?.setText("豆包转写已写入笔记");
      new Notice("豆包转写完成。", 5000);
    } catch (error) {
      const message = this.errorMessage(error);
      status?.setText("转写失败");
      new Notice(`豆包转写失败：${message}`, 12000);
    } finally {
      if (button) {
        button.disabled = false;
      }
    }
  }

  async startLocalPlaybackCapture() {
    try {
      await this.ensureBackend();
      const response = await requestUrl({
        url: `${this.settings.backendUrl}/api/local-audio/start`,
        method: "POST",
        throw: false,
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          device: this.settings.audioCaptureDevice.trim(),
          allow_microphone: this.settings.allowMicrophoneCapture,
        }),
      });
      const data = response.json;
      if (response.status >= 400 || !data?.success) {
        throw new Error(data?.error || `后端返回 HTTP ${response.status}`);
      }
      this.localCaptureActive = true;
      new Notice(`已开始录制本地播放音频：${data.device}`, 7000);
    } catch (error) {
      new Notice(`开始录音失败：${this.errorMessage(error)}`, 12000);
    }
  }

  async stopLocalPlaybackCapture() {
    const noteFile = this.app.workspace.getActiveFile();
    if (!(noteFile instanceof TFile)) {
      new Notice("请先打开要写入的收件箱笔记。", 6000);
      return;
    }
    try {
      await this.ensureBackend();
      const frontmatter =
        this.app.metadataCache.getFileCache(noteFile)?.frontmatter || {};
      const response = await requestUrl({
        url: `${this.settings.backendUrl}/api/local-audio/stop`,
        method: "POST",
        throw: false,
        headers: await this.authenticatedBackendHeaders(),
        body: JSON.stringify({
          model: this.settings.whisperModel,
          doubao_api_key: this.legacyDoubaoApiKeyPayload(),
          doubao_resource_id: this.settings.doubaoResourceId,
          title: frontmatter.source_title || noteFile.basename,
          author: frontmatter.author || "",
          source: frontmatter.source || "",
          platform: frontmatter.platform || "wechat_channels",
        }),
      });
      const data = response.json;
      if (response.status >= 400 || !data?.success) {
        throw new Error(data?.error || `后端返回 HTTP ${response.status}`);
      }
      this.localCaptureActive = false;
      await this.writeLocalCaptureResult(noteFile, data);
      new Notice("本地播放录音已写入当前笔记。", 7000);
    } catch (error) {
      new Notice(`停止录音失败：${this.errorMessage(error)}`, 12000);
    }
  }

  async extractCurrentWechatRadiumAudio() {
    const noteFile = this.app.workspace.getActiveFile();
    if (!(noteFile instanceof TFile)) {
      new Notice("请先打开要写入的微信视频号收件箱笔记。", 6000);
      return;
    }

    try {
      await this.ensureBackend();
      const frontmatter =
        this.app.metadataCache.getFileCache(noteFile)?.frontmatter || {};
      const sourceUrl = frontmatter.source || "";
      new Notice("正在读取 PC 微信缓存里的候选媒体流…", 6000);
      const candidates = await this.fetchWechatRadiumCandidates(sourceUrl);
      let extractUrl = sourceUrl;
      if (candidates.length) {
        const candidate =
          candidates.length === 1
            ? candidates[0]
            : await this.chooseWechatMediaCandidate(candidates);
        if (!candidate) {
          new Notice("已取消视频号音频提取。", 5000);
          return;
        }
        extractUrl = candidate.url;
      } else {
        new Notice("没有找到候选媒体流，尝试直接解析当前链接…", 6000);
      }
      const data = await this.extractWechatRadiumAudio(
        extractUrl,
        frontmatter,
        noteFile.basename,
      );
      await this.writeRemoteAudioResult(noteFile, data);
      new Notice("微信视频号完整音频和转写已写入当前笔记。", 7000);
    } catch (error) {
      new Notice(`视频号音频提取失败：${this.errorMessage(error)}`, 15000);
    }
  }

  async fetchWechatRadiumCandidates(sourceUrl) {
    const response = await requestUrl({
      url: `${this.settings.backendUrl}/api/wechat-radium/candidates`,
      method: "POST",
      throw: false,
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        url: sourceUrl || "",
        minutes: 240,
        limit: 6,
      }),
    });
    const data = response.json;
    if (response.status >= 400 || !data?.success) {
      throw new Error(data?.error || `候选媒体流接口返回 HTTP ${response.status}`);
    }
    return Array.isArray(data.candidates) ? data.candidates : [];
  }

  chooseWechatMediaCandidate(candidates) {
    return new Promise((resolve) => {
      new WechatMediaCandidateModal(this.app, candidates, resolve).open();
    });
  }

  async extractWechatRadiumAudio(extractUrl, frontmatter, fallbackTitle) {
    const response = await requestUrl({
      url: `${this.settings.backendUrl}/api/wechat-radium/extract`,
      method: "POST",
      throw: false,
      headers: await this.authenticatedBackendHeaders(),
      body: JSON.stringify({
        url: extractUrl || "",
        minutes: 240,
        model: this.settings.whisperModel,
        transcription_engine: this.settings.transcriptionEngine,
        doubao_api_key:
          this.settings.transcriptionEngine === "doubao"
            ? this.legacyDoubaoApiKeyPayload()
            : "",
        doubao_resource_id: this.settings.doubaoResourceId,
        whisper_fallback: this.settings.whisperFallback,
        title: frontmatter.source_title || fallbackTitle,
        author: frontmatter.author || "",
      }),
    });
    const data = response.json;
    if (response.status >= 400 || !data?.success) {
      throw new Error(data?.error || `后端返回 HTTP ${response.status}`);
    }
    return data;
  }

  async writeRemoteAudioResult(noteFile, data) {
    await this.ensureVaultFolder(this.settings.attachmentFolder);
    const platform = PLATFORMS.wechat_channels;
    const meta = await this.readMeta(data.out_dir);
    const itemId = this.safeName(
      data.video_id || meta.aweme_id || `wechat-${Date.now()}`,
      80,
    );
    const mediaLines = await this.importMedia(
      { ...data, video_id: itemId, content_type: "video" },
      meta,
      platform,
    );

    await this.app.fileManager.processFrontMatter(noteFile, (frontmatter) => {
      frontmatter.type = frontmatter.type || "inbox";
      frontmatter.status = "raw";
      frontmatter.source_site = "微信视频号";
      frontmatter.platform = "wechat_channels";
      frontmatter.content_type = "video";
      frontmatter.platform_item_id = data.video_id || meta.aweme_id || "";
      frontmatter.wechat_channels_id = data.video_id || meta.aweme_id || "";
      frontmatter.transcription_engine = data.transcription_engine || "unknown";
      if (data.radium_url) {
        frontmatter.wechat_radium_url = data.radium_url;
      }
    });

    const transcript = String(data.text || "").trim() || "> 未识别到语音内容。";
    const section = [
      ...mediaLines,
      mediaLines.length ? "" : null,
      "## 语音转写",
      "",
      transcript,
      "",
    ]
      .filter((line) => line !== null)
      .join("\n");
    await this.app.vault.process(noteFile, (content) =>
      content.includes("\n## 音轨")
        ? content.replace(
            /\n## 音轨\r?\n[\s\S]*?(?=\r?\n## 来源(?:\r?\n|$)|$)/,
            `\n${section}`,
          )
        : `${content.trimEnd()}\n\n${section}`,
    );
  }

  async writeLocalCaptureResult(noteFile, data) {
    await this.ensureVaultFolder(this.settings.attachmentFolder);
    const sessionId = this.safeName(data.session_id || data.video_id || "local-audio", 80);
    const rel = normalizePath(
      `${this.settings.attachmentFolder}/wechat-channels-${sessionId}-audio.mp3`,
    );
    await this.writeBinary(rel, await fs.readFile(data.audio_path));

    await this.app.fileManager.processFrontMatter(noteFile, (frontmatter) => {
      frontmatter.type = frontmatter.type || "inbox";
      frontmatter.status = frontmatter.status || "raw";
      frontmatter.source_site = frontmatter.source_site || "微信视频号";
      frontmatter.platform = frontmatter.platform || "wechat_channels";
      frontmatter.content_type = frontmatter.content_type || "video";
      frontmatter.transcription_engine = data.transcription_engine || "unknown";
      frontmatter.local_audio_capture = true;
    });

    const transcript = String(data.text || "").trim() || "> 未识别到语音内容。";
    const section = [
      "## 音轨",
      "",
      `![[${rel}]]`,
      "",
      "## 语音转写",
      "",
      transcript,
      "",
    ].join("\n");
    await this.app.vault.process(noteFile, (content) =>
      content.includes("\n## 音轨")
        ? content.replace(
            /\n## 音轨\r?\n[\s\S]*?(?=\r?\n## 来源(?:\r?\n|$)|$)/,
            `\n${section}`,
          )
        : `${content.trimEnd()}\n\n${section}`,
    );
  }

  findAudioFile(noteContent, sourcePath) {
    const match = noteContent.match(
      /!\[\[([^\]|]+\.mp3)(?:\|[^\]]*)?\]\]/i,
    );
    if (!match) {
      return null;
    }
    return this.app.metadataCache.getFirstLinkpathDest(match[1], sourcePath);
  }

  localFilePath(file) {
    const adapter = this.app.vault.adapter;
    if (typeof adapter.getBasePath !== "function") {
      throw new Error("当前设备无法访问本地音频路径");
    }
    return path.join(adapter.getBasePath(), ...file.path.split("/"));
  }

  async applyManualTranscript(noteFile, transcript) {
    const cleanTranscript = String(transcript).trim();
    if (!cleanTranscript) {
      throw new Error("豆包没有返回转写正文");
    }

    await this.app.fileManager.processFrontMatter(noteFile, (frontmatter) => {
      frontmatter.transcription_engine = "doubao";
      delete frontmatter.transcription_fallback;
      delete frontmatter.transcription_error;
    });

    const section = `## 语音转写\n\n${cleanTranscript}`;
    const roughTranscript =
      /%%\r?\n### 机器粗转写（仅供 AI 检索）\r?\n[\s\S]*?\r?\n%%/;
    const visibleTranscript =
      /## 语音转写\r?\n[\s\S]*?(?=\r?\n## 来源(?:\r?\n|$))/;
    await this.app.vault.process(noteFile, (content) => {
      if (roughTranscript.test(content)) {
        return content.replace(roughTranscript, section);
      }
      if (visibleTranscript.test(content)) {
        return content.replace(visibleTranscript, section);
      }
      if (content.includes("\n## 来源")) {
        return content.replace("\n## 来源", `\n${section}\n\n## 来源`);
      }
      return `${content.trimEnd()}\n\n${section}\n`;
    });
  }

  async hasCaptured(itemId, platform) {
    if (!itemId) {
      return false;
    }
    for (const file of this.app.vault.getMarkdownFiles()) {
      const frontmatter =
        this.app.metadataCache.getFileCache(file)?.frontmatter || {};
      const existingPlatform =
        frontmatter.platform ||
        (frontmatter.source_site === "微信视频号" ? "wechat_channels" : "douyin");
      const existingId =
        frontmatter.platform_item_id ||
        frontmatter[platform.itemIdKey] ||
        frontmatter.douyin_id ||
        frontmatter.wechat_channels_id;
      if (
        existingPlatform === platform.id &&
        String(existingId || "") === String(itemId)
      ) {
        return true;
      }
    }
    return false;
  }

  async writeInboxNote(data, sourceUrl, platform) {
    await this.ensureVaultFolder(this.settings.inboxFolder);
    await this.ensureVaultFolder(this.settings.attachmentFolder);

    const meta = await this.readMeta(data.out_dir);
    const rawTitle = String(data.title || meta.title || `${platform.label}视频`).trim();
    const author = String(data.author || meta.author || "未知").trim();
    const { displayTitle, hashtags } = this.splitTitle(rawTitle, `${platform.label}视频`);
    const mediaLines = await this.importMedia(data, meta, platform);
    const capturedAt = new Date();
    const noteName = this.uniqueNotePath(
      `${this.localTimestamp(capturedAt)} ${this.safeName(displayTitle, 40)}`,
    );

    const itemId = String(data.video_id || meta.aweme_id || "");
    const tags = [platform.tag, ...hashtags];
    const frontmatter = [
      "---",
      "type: inbox",
      "status: raw",
      `source: "${this.yamlEscape(sourceUrl)}"`,
      `source_title: "${this.yamlEscape(displayTitle)}"`,
      `source_site: "${this.yamlEscape(platform.sourceSite)}"`,
      `captured_at: "${this.localDateTime(capturedAt)}"`,
      `content_type: ${data.content_type || "video"}`,
      `platform: "${this.yamlEscape(platform.id)}"`,
      `platform_item_id: "${this.yamlEscape(itemId)}"`,
      `${platform.itemIdKey}: "${this.yamlEscape(itemId)}"`,
      `author: "${this.yamlEscape(author)}"`,
      `transcription_engine: "${this.yamlEscape(data.transcription_engine || "unknown")}"`,
      ...(data.transcription_error
        ? [
            "transcription_fallback: true",
            `transcription_error: "${this.yamlEscape(data.transcription_error)}"`,
          ]
        : []),
      "tags:",
      ...tags.map((tag) => `  - "${this.yamlEscape(tag)}"`),
      "---",
    ].join("\n");

    const transcript = String(data.text || "").trim();
    const doubaoTranscript =
      data.transcription_engine === "doubao" ||
      data.transcription_engine === "doubao-seedasr-2.0";
    const transcriptLines = doubaoTranscript
      ? ["## 语音转写", "", transcript || "> 豆包未返回转写正文。"]
      : [
          "%%",
          "### 机器粗转写（仅供 AI 检索）",
          "",
          transcript || "> 未提取到可识别的语音。",
          "%%",
        ];
    const body = [
      frontmatter,
      "",
      `# ${displayTitle}`,
      "",
      ...mediaLines,
      mediaLines.length ? "" : null,
      "## 原始记录",
      "",
      "### 视频简介",
      "",
      rawTitle,
      "",
      ...transcriptLines,
      "",
      "## 来源",
      "",
      `- 作者：${author}`,
      `- [在${platform.label}查看原视频](${sourceUrl})`,
      "",
    ]
      .filter((line) => line !== null)
      .join("\n");

    return this.app.vault.create(noteName, body);
  }

  async readMeta(outDir) {
    try {
      return JSON.parse(
        await fs.readFile(path.join(outDir, "meta.json"), "utf8"),
      );
    } catch {
      return {};
    }
  }

  async importMedia(data, meta, platform) {
    if (data.content_type === "image") {
      const lines = [];
      const imageDir = path.join(data.out_dir, "images");
      let names = [];
      try {
        names = (await fs.readdir(imageDir)).filter((name) =>
          /\.(png|jpe?g|webp|gif)$/i.test(name),
        );
      } catch {
        return lines;
      }
      for (const [index, name] of names.sort().entries()) {
        const ext = path.extname(name) || ".jpg";
        const rel = normalizePath(
          `${this.settings.attachmentFolder}/${platform.attachmentPrefix}-${data.video_id}-${index + 1}${ext}`,
        );
        await this.writeBinary(rel, await fs.readFile(path.join(imageDir, name)));
        lines.push(`![[${rel}]]`);
      }
      return lines;
    }

    if (!meta.cover_url) {
      return this.importAudio(data, platform);
    }
    const lines = await this.importAudio(data, platform);
    try {
      const response = await requestUrl({
        url: meta.cover_url,
        method: "GET",
      });
      const rel = normalizePath(
        `${this.settings.attachmentFolder}/${platform.attachmentPrefix}-${data.video_id}-cover.webp`,
      );
      await this.writeBinary(rel, Buffer.from(response.arrayBuffer));
      return [`![[${rel}]]`, "", ...lines];
    } catch {
      return lines;
    }
  }

  async importAudio(data, platform) {
    const source = path.join(data.out_dir, "audio.mp3");
    try {
      const rel = normalizePath(
        `${this.settings.attachmentFolder}/${platform.attachmentPrefix}-${data.video_id}-audio.mp3`,
      );
      await this.writeBinary(rel, await fs.readFile(source));
      return ["## 音轨", "", `![[${rel}]]`];
    } catch {
      return [];
    }
  }

  async writeBinary(relPath, buffer) {
    const existing = this.app.vault.getAbstractFileByPath(relPath);
    const arrayBuffer = Uint8Array.from(buffer).buffer;
    if (existing instanceof TFile) {
      await this.app.vault.modifyBinary(existing, arrayBuffer);
    } else {
      await this.app.vault.createBinary(relPath, arrayBuffer);
    }
  }

  async cleanupOutput(outDir) {
    if (!outDir) {
      return;
    }
    const root = path.resolve(
      process.env.LOCALAPPDATA || path.join(os.homedir(), "AppData", "Local"),
      "Xiaolou",
      "DouyinCapture",
      "backend",
      "output",
    );
    const target = path.resolve(outDir);
    if (target.startsWith(`${root}${path.sep}`)) {
      await fs.rm(target, { recursive: true, force: true });
    }
  }

  async markFailed(file, entry, error) {
    if (!(file instanceof TFile)) {
      return;
    }
    const suffix = file.name
      .replace(/^captureProcessing_/, "")
      .replace(/^douyinProcessing_/, "");
    const parent = file.parent?.path;
    const failedPath = normalizePath(
      `${parent && parent !== "/" ? `${parent}/` : ""}${FAILED_PREFIX}${suffix}`,
    );
    if (file.path !== failedPath) {
      await this.app.fileManager.renameFile(file, failedPath);
    }

    await this.ensureVaultFolder(this.settings.inboxFolder);
    const source = this.extractSupportedUrl(entry.url || "");
    const platform = source?.platform || PLATFORMS.douyin;
    const sourceUrl = source?.url || entry.url || "";
    const name = this.uniqueNotePath(
      `${this.localTimestamp(new Date())} ${platform.label}采集失败`,
    );
    const content = [
      "---",
      "type: inbox",
      "status: capture_failed",
      "ai_processed: true",
      `source: "${this.yamlEscape(sourceUrl)}"`,
      `source_site: "${this.yamlEscape(platform.sourceSite)}"`,
      `platform: "${this.yamlEscape(platform.id)}"`,
      `captured_at: "${this.localDateTime(new Date())}"`,
      "---",
      "",
      `# ${platform.label}采集失败`,
      "",
      `- 原链接：${sourceUrl}`,
      `- 错误：${this.errorMessage(error)}`,
      "- 重试：命令面板运行“抖音收件箱桥接：处理待采集和失败的抖音链接”",
      "",
    ].join("\n");
    await this.app.vault.create(name, content);
  }

  async ensureVaultFolder(folderPath) {
    const parts = normalizePath(folderPath).split("/");
    let current = "";
    for (const part of parts) {
      current = current ? `${current}/${part}` : part;
      if (!this.app.vault.getAbstractFileByPath(current)) {
        await this.app.vault.createFolder(current);
      }
    }
  }

  splitTitle(rawTitle, fallbackTitle = "抖音视频") {
    const hashtags = [];
    const seen = new Set();
    for (const match of rawTitle.matchAll(/#([^\s#]+)/gu)) {
      const tag = match[1].trim();
      if (tag && !seen.has(tag)) {
        seen.add(tag);
        hashtags.push(tag);
      }
    }
    const displayTitle =
      rawTitle.replace(/#[^\s#]+/gu, "").replace(/\s+/g, " ").trim() ||
      fallbackTitle;
    return { displayTitle, hashtags };
  }

  uniqueNotePath(baseName) {
    let pathName = normalizePath(
      `${this.settings.inboxFolder}/${baseName}.md`,
    );
    let index = 2;
    while (this.app.vault.getAbstractFileByPath(pathName)) {
      pathName = normalizePath(
        `${this.settings.inboxFolder}/${baseName} ${index}.md`,
      );
      index += 1;
    }
    return pathName;
  }

  safeName(value, maxLength) {
    const cleaned = String(value)
      .replace(/[\\/:*?"<>|#^[\]]/g, "")
      .replace(/\s+/g, " ")
      .trim();
    return (cleaned || "抖音视频").slice(0, maxLength).trim();
  }

  yamlEscape(value) {
    return String(value).replace(/\\/g, "\\\\").replace(/"/g, '\\"');
  }

  localTimestamp(date) {
    const pad = (value) => String(value).padStart(2, "0");
    return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}${pad(date.getMinutes())}`;
  }

  localDateTime(date) {
    const pad = (value) => String(value).padStart(2, "0");
    return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`;
  }

  errorMessage(error) {
    return error instanceof Error ? error.message : String(error);
  }

  sleep(ms) {
    return new Promise((resolve) => window.setTimeout(resolve, ms));
  }
}

class WechatMediaCandidateModal extends SuggestModal {
  constructor(app, candidates, onChoose) {
    super(app);
    this.candidates = candidates;
    this.onChoose = onChoose;
    this.setPlaceholder("选择要提取音频的视频号媒体流");
  }

  getSuggestions(query) {
    const text = String(query || "").trim().toLowerCase();
    if (!text) {
      return this.candidates;
    }
    return this.candidates.filter((candidate) =>
      this.candidateLabel(candidate).toLowerCase().includes(text),
    );
  }

  renderSuggestion(candidate, el) {
    const title = el.createEl("div", {
      text: this.candidateLabel(candidate),
    });
    title.style.fontWeight = "600";
    el.createEl("small", {
      text: `缓存文件：${candidate.file || "unknown"} · offset ${candidate.offset || 0}`,
    });
  }

  onChooseSuggestion(candidate) {
    const onChoose = this.onChoose;
    this.onChoose = null;
    if (onChoose) {
      onChoose(candidate);
    }
  }

  onClose() {
    const onChoose = this.onChoose;
    this.onChoose = null;
    if (onChoose) {
      onChoose(null);
    }
  }

  candidateLabel(candidate) {
    const time = candidate.modified_at
      ? new Date(candidate.modified_at * 1000).toLocaleTimeString()
      : "未知时间";
    return `${candidate.index || "?"}. ${candidate.host || "未知来源"} · ${time}`;
  }
}

class DouyinInboxBridgeSettingTab extends PluginSettingTab {
  constructor(app, plugin) {
    super(app, plugin);
    this.plugin = plugin;
  }

  display() {
    const { containerEl } = this;
    containerEl.empty();

    new Setting(containerEl)
      .setName("收件箱目录")
      .setDesc("监听该目录直属的 Markdown 笔记。")
      .addText((text) =>
        text
          .setValue(this.plugin.settings.inboxFolder)
          .onChange(async (value) => {
            this.plugin.settings.inboxFolder = normalizePath(value.trim());
            await this.plugin.saveSettings();
          }),
      );

    new Setting(containerEl)
      .setName("附件目录")
      .setDesc("保存提取的音频和图文附件。")
      .addText((text) =>
        text
          .setValue(this.plugin.settings.attachmentFolder)
          .onChange(async (value) => {
            this.plugin.settings.attachmentFolder = normalizePath(value.trim());
            await this.plugin.saveSettings();
          }),
      );

    new Setting(containerEl)
      .setName("本地后端 URL")
      .setDesc("默认后端仅监听本机 127.0.0.1。")
      .addText((text) =>
        text
          .setValue(this.plugin.settings.backendUrl)
          .onChange(async (value) => {
            this.plugin.settings.backendUrl = value.trim().replace(/\/$/, "");
            await this.plugin.saveSettings();
          }),
      );

    new Setting(containerEl)
      .setName("语音转写引擎")
      .setDesc("豆包 2.0 直接识别音频；本地 Whisper 仅作为备用。")
      .addDropdown((dropdown) =>
        dropdown
          .addOption("doubao", "豆包录音文件识别 2.0")
          .addOption("whisper", "本地 Whisper")
          .setValue(this.plugin.settings.transcriptionEngine)
          .onChange(async (value) => {
            this.plugin.settings.transcriptionEngine = value;
            await this.plugin.saveSettings();
          }),
      );

    let pendingDoubaoApiKey = "";
    let saveDoubaoApiKeyButton = null;
    const doubaoKeyConfigured = this.plugin.hasDoubaoApiKey();
    new Setting(containerEl)
      .setName("豆包 API Key")
      .setDesc(
        `${doubaoKeyConfigured ? "已安全保存在本机" : "尚未配置"}；` +
          "Key 使用 Windows DPAPI 加密，不写入 Vault。",
      )
      .addText((text) => {
        text.inputEl.type = "password";
        text
          .setPlaceholder(
            doubaoKeyConfigured ? "输入新 Key 可替换" : "在此粘贴 X-Api-Key",
          )
          .onChange((value) => {
            pendingDoubaoApiKey = value.trim();
            saveDoubaoApiKeyButton?.setDisabled(!pendingDoubaoApiKey);
          });
      })
      .addButton((button) => {
        saveDoubaoApiKeyButton = button;
        button
          .setButtonText("保存")
          .setCta()
          .setDisabled(true)
          .onClick(async () => {
            try {
              await this.plugin.setDoubaoApiKey(pendingDoubaoApiKey);
              new Notice("豆包 API Key 已安全保存。", 5000);
              this.display();
            } catch (error) {
              new Notice(
                `保存豆包 API Key 失败：${this.plugin.errorMessage(error)}`,
                10000,
              );
            }
          });
      })
      .addExtraButton((button) => {
        button
          .setIcon("trash-2")
          .setTooltip("移除豆包 API Key")
          .setDisabled(!doubaoKeyConfigured)
          .onClick(async () => {
            if (!window.confirm("确定移除本机保存的豆包 API Key？")) {
              return;
            }
            try {
              await this.plugin.clearDoubaoApiKey();
              new Notice("豆包 API Key 已移除。", 5000);
              this.display();
            } catch (error) {
              new Notice(
                `移除豆包 API Key 失败：${this.plugin.errorMessage(error)}`,
                10000,
              );
            }
          });
      });

    new Setting(containerEl)
      .setName("豆包语音资源")
      .setDesc("要和火山控制台里当前应用实际开通的录音文件识别资源一致。")
      .addDropdown((dropdown) =>
        dropdown
          .addOption("volc.seedasr.auc", "SeedASR: volc.seedasr.auc")
          .addOption("volc.bigasr.auc", "BigASR: volc.bigasr.auc")
          .setValue(this.plugin.settings.doubaoResourceId)
          .onChange(async (value) => {
            this.plugin.settings.doubaoResourceId = value;
            await this.plugin.saveSettings();
          }),
      );

    new Setting(containerEl)
      .setName("失败时使用本地 Whisper")
      .setDesc("豆包接口、网络或临时音频上传失败时继续完成采集。")
      .addToggle((toggle) =>
        toggle
          .setValue(this.plugin.settings.whisperFallback)
          .onChange(async (value) => {
            this.plugin.settings.whisperFallback = value;
            await this.plugin.saveSettings();
          }),
      );

    new Setting(containerEl)
      .setName("本地播放录音设备")
      .setDesc("留空时自动寻找“立体声混音 / VB-CABLE”等系统回环设备。")
      .addText((text) =>
        text
          .setPlaceholder("例如：立体声混音 (Realtek(R) Audio)")
          .setValue(this.plugin.settings.audioCaptureDevice)
          .onChange(async (value) => {
            this.plugin.settings.audioCaptureDevice = value.trim();
            await this.plugin.saveSettings();
          }),
      );

    new Setting(containerEl)
      .setName("允许用麦克风临时录音")
      .setDesc("只有找不到系统回环设备时才会使用，音质取决于扬声器外放。")
      .addToggle((toggle) =>
        toggle
          .setValue(this.plugin.settings.allowMicrophoneCapture)
          .onChange(async (value) => {
            this.plugin.settings.allowMicrophoneCapture = value;
            await this.plugin.saveSettings();
          }),
      );

    new Setting(containerEl)
      .setName("查看本机录音设备")
      .setDesc("列出 FFmpeg 能看到的音频输入设备。")
      .addButton((button) =>
        button.setButtonText("查看").onClick(async () => {
          await this.plugin.showLocalAudioDevices();
        }),
      );

    new Setting(containerEl)
      .setName("检查运行环境")
      .setDesc("检查本地转写后端，不会调用豆包计费接口。")
      .addButton((button) =>
        button.setButtonText("检查").onClick(async () => {
          await this.plugin.checkDoubaoEnvironment();
        }),
      );
  }
}

module.exports = DouyinInboxBridgePlugin;
