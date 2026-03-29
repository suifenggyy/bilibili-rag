"use client";

import { useState, useEffect, useRef } from "react";
import {
  DouyinExportRequest,
  DouyinExportJobStatus,
  douyinExportApi,
} from "@/lib/api";

type ASRBackend = "auto" | "dashscope" | "ollama";

export default function DouyinExportPanel() {
  // Cookie & 连接配置
  const [cookie, setCookie] = useState("");
  const [evil0ctalUrl, setEvil0ctalUrl] = useState("http://localhost:2333");
  const [limit, setLimit] = useState<number>(0);

  // ASR 配置
  const [asrBackend, setAsrBackend] = useState<ASRBackend>("auto");
  const [ollamaUrl, setOllamaUrl] = useState("http://localhost:11434");
  const [ollamaModel, setOllamaModel] = useState("whisper");
  const [ollamaLanguage, setOllamaLanguage] = useState("zh");
  const [showOllamaSettings, setShowOllamaSettings] = useState(false);

  // 任务状态
  const [jobId, setJobId] = useState<string | null>(null);
  const [jobStatus, setJobStatus] = useState<DouyinExportJobStatus | null>(null);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // 轮询任务状态
  useEffect(() => {
    if (!jobId) return;
    const poll = async () => {
      try {
        const status = await douyinExportApi.getStatus(jobId);
        setJobStatus(status);
        if (status.status === "completed" || status.status === "failed") {
          if (pollRef.current) clearInterval(pollRef.current);
        }
      } catch {
        if (pollRef.current) clearInterval(pollRef.current);
      }
    };
    poll();
    pollRef.current = setInterval(poll, 2000);
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, [jobId]);

  useEffect(() => {
    setShowOllamaSettings(asrBackend === "ollama");
  }, [asrBackend]);

  const handleStart = async () => {
    if (!cookie.trim()) {
      setError("请填入抖音 Cookie");
      return;
    }
    setError(null);
    setStarting(true);
    setJobStatus(null);
    setJobId(null);

    const req: DouyinExportRequest = {
      cookie: cookie.trim(),
      evil0ctal_url: evil0ctalUrl,
      limit,
      asr_backend: asrBackend,
      ollama_url: ollamaUrl,
      ollama_model: ollamaModel,
      ollama_language: ollamaLanguage,
    };

    try {
      const res = await douyinExportApi.start(req);
      setJobId(res.job_id);
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : "启动失败";
      setError(msg);
    } finally {
      setStarting(false);
    }
  };

  const handleDownload = () => {
    if (!jobId) return;
    const a = document.createElement("a");
    a.href = douyinExportApi.getDownloadUrl(jobId);
    a.download = "";
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
  };

  const handleReset = () => {
    setJobId(null);
    setJobStatus(null);
    setError(null);
    if (pollRef.current) clearInterval(pollRef.current);
  };

  const isRunning = jobStatus?.status === "pending" || jobStatus?.status === "running";
  const isDone = jobStatus?.status === "completed";
  const isFailed = jobStatus?.status === "failed";

  return (
    <div className="export-panel">
      <div className="export-header">
        <h2 className="export-title">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8"
            style={{ display: "inline", marginRight: 6, verticalAlign: "middle" }}>
            <path strokeLinecap="round" strokeLinejoin="round"
              d="M15 10l4.553-2.07A1 1 0 0121 8.82V17a1 1 0 01-1.447.894L15 16M3 8a2 2 0 012-2h8a2 2 0 012 2v8a2 2 0 01-2 2H5a2 2 0 01-2-2V8z" />
          </svg>
          抖音收藏夹导出
        </h2>
        <p className="export-desc">
          将抖音收藏夹视频音频转写为 Markdown 文件。需要本地运行 Evil0ctal API 服务。
        </p>
      </div>

      <div className="export-body">

        {/* Step 1: Cookie 配置 */}
        <section className="export-step">
          <div className="export-step-title">
            <span className="step-badge">1</span> 配置抖音 Cookie
          </div>

          <div className="douyin-cookie-guide">
            <p className="ollama-hint" style={{ marginBottom: 8 }}>
              获取方式：Chrome 打开 douyin.com 并登录 → F12 → Application → Cookies → douyin.com → 复制所有字段
            </p>
            <textarea
              className="douyin-cookie-input"
              placeholder={"ttwid=xxx; sessionid=xxx; odin_tt=xxx; msToken=xxx; ..."}
              value={cookie}
              onChange={(e) => setCookie(e.target.value)}
              rows={3}
            />
          </div>

          <div className="douyin-fields">
            <div className="ollama-field">
              <label className="ollama-label">Evil0ctal API 地址</label>
              <input
                className="ollama-input"
                value={evil0ctalUrl}
                onChange={(e) => setEvil0ctalUrl(e.target.value)}
                placeholder="http://localhost:2333"
              />
              <span className="ollama-hint">
                需先部署：<code>git clone https://github.com/Evil0ctal/Douyin_TikTok_Download_API && python main.py</code>
              </span>
            </div>

            <div className="ollama-field">
              <label className="ollama-label">导出数量限制</label>
              <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                <input
                  className="ollama-input ollama-input-sm"
                  type="number"
                  min={0}
                  value={limit}
                  onChange={(e) => setLimit(Math.max(0, parseInt(e.target.value) || 0))}
                  style={{ width: 100 }}
                />
                <span className="ollama-hint">{limit === 0 ? "0 = 导出全部" : `最多导出最新 ${limit} 个`}</span>
              </div>
            </div>
          </div>
        </section>

        {/* Step 2: ASR 后端 */}
        <section className="export-step">
          <div className="export-step-title">
            <span className="step-badge">2</span> 转写方式
          </div>

          <div className="asr-options">
            {(["auto", "dashscope", "ollama"] as ASRBackend[]).map((b) => (
              <label key={b} className={`asr-option ${asrBackend === b ? "asr-option-active" : ""}`}>
                <input
                  type="radio"
                  name="douyin_asr_backend"
                  value={b}
                  checked={asrBackend === b}
                  onChange={() => setAsrBackend(b)}
                  className="sr-only"
                />
                <div className="asr-option-content">
                  <span className="asr-option-name">
                    {b === "auto" ? "自动选择" : b === "dashscope" ? "DashScope 云端" : "Ollama 本地"}
                  </span>
                  <span className="asr-option-desc">
                    {b === "auto"
                      ? "有 API Key 则云端，否则本地"
                      : b === "dashscope"
                      ? "paraformer-v2，中文效果最佳"
                      : "Whisper，完全免费，数据不出本机"}
                  </span>
                </div>
              </label>
            ))}
          </div>

          {showOllamaSettings && (
            <div className="ollama-settings">
              <div className="ollama-field">
                <label className="ollama-label">服务地址</label>
                <input
                  className="ollama-input"
                  value={ollamaUrl}
                  onChange={(e) => setOllamaUrl(e.target.value)}
                  placeholder="http://localhost:11434"
                />
              </div>
              <div className="ollama-field">
                <label className="ollama-label">模型</label>
                <input
                  className="ollama-input"
                  value={ollamaModel}
                  onChange={(e) => setOllamaModel(e.target.value)}
                  placeholder="whisper"
                />
                <span className="ollama-hint">可选：whisper · whisper:large</span>
              </div>
              <div className="ollama-field">
                <label className="ollama-label">语言</label>
                <input
                  className="ollama-input ollama-input-sm"
                  value={ollamaLanguage}
                  onChange={(e) => setOllamaLanguage(e.target.value)}
                  placeholder="zh"
                />
                <span className="ollama-hint">留空自动检测</span>
              </div>
            </div>
          )}
        </section>

        {/* Step 3: 执行 */}
        <section className="export-step">
          <div className="export-step-title">
            <span className="step-badge">3</span> 开始导出
          </div>

          {error && (
            <div className="export-error">
              <svg width="16" height="16" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
                <circle cx="12" cy="12" r="10" /><line x1="12" y1="8" x2="12" y2="12" /><line x1="12" y1="16" x2="12.01" y2="16" />
              </svg>
              {error}
            </div>
          )}

          {!jobStatus && !isRunning && (
            <button
              className="btn btn-primary btn-export"
              onClick={handleStart}
              disabled={starting || !cookie.trim()}
            >
              {starting ? "启动中..." : (limit > 0 ? `导出最新 ${limit} 个视频` : "导出全部收藏视频")}
            </button>
          )}

          {jobStatus && (
            <div className="export-progress-wrap">
              <div className="export-status-row">
                <span className={`status-badge status-${jobStatus.status}`}>
                  {jobStatus.status === "pending" && "⏳ 等待中"}
                  {jobStatus.status === "running" && "🔄 转写中"}
                  {jobStatus.status === "completed" && "✅ 完成"}
                  {jobStatus.status === "failed" && "❌ 失败"}
                </span>
                <span className="export-msg">{jobStatus.message}</span>
              </div>

              {(isRunning || isDone) && (
                <div className="progress-bar-wrap">
                  <div className="progress-bar-fill" style={{ width: `${jobStatus.progress}%` }} />
                </div>
              )}

              {(isRunning || isDone) && (
                <div className="progress-detail">
                  <span>{jobStatus.processed_videos} / {jobStatus.total_videos} 个视频</span>
                  {jobStatus.file_count > 0 && (
                    <span>已生成 {jobStatus.file_count} 个文件</span>
                  )}
                </div>
              )}

              {isRunning && jobStatus.current_video && (
                <div className="progress-current">
                  <span className="current-label">当前：</span>
                  <span className="current-name">{jobStatus.current_video}</span>
                </div>
              )}

              {isDone && (
                <div className="export-actions">
                  <button className="btn btn-primary" onClick={handleDownload}>
                    <svg width="16" height="16" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2"
                      style={{ marginRight: 6 }}>
                      <path strokeLinecap="round" strokeLinejoin="round"
                        d="M4 16v2a2 2 0 002 2h12a2 2 0 002-2v-2M7 10l5 5m0 0l5-5m-5 5V4" />
                    </svg>
                    下载 ZIP（{jobStatus.file_count} 个文件）
                  </button>
                  <button className="btn btn-outline" onClick={handleReset}>重新导出</button>
                </div>
              )}

              {isFailed && (
                <button className="btn btn-outline" onClick={handleReset}>重试</button>
              )}
            </div>
          )}
        </section>
      </div>
    </div>
  );
}
