"use client";

import { useState, useEffect, useRef } from "react";
import {
  FavoriteFolder,
  ExportRequest,
  ExportJobStatus,
  favoritesApi,
  exportApi,
  API_BASE_URL,
} from "@/lib/api";

interface Props {
  sessionId: string;
}

type ASRBackend = "auto" | "dashscope" | "ollama";

export default function ExportPanel({ sessionId }: Props) {
  const [folders, setFolders] = useState<FavoriteFolder[]>([]);
  const [loadingFolders, setLoadingFolders] = useState(true);
  const [selectedFolders, setSelectedFolders] = useState<Set<number>>(new Set());

  const [asrBackend, setAsrBackend] = useState<ASRBackend>("auto");
  const [ollamaUrl, setOllamaUrl] = useState("http://localhost:11434");
  const [ollamaModel, setOllamaModel] = useState("whisper");
  const [ollamaLanguage, setOllamaLanguage] = useState("zh");
  const [showOllamaSettings, setShowOllamaSettings] = useState(false);

  const [jobId, setJobId] = useState<string | null>(null);
  const [jobStatus, setJobStatus] = useState<ExportJobStatus | null>(null);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // 加载收藏夹列表
  useEffect(() => {
    setLoadingFolders(true);
    favoritesApi
      .getList(sessionId)
      .then((data) => {
        setFolders(data);
        // 默认全选
        setSelectedFolders(new Set(data.map((f) => f.media_id)));
      })
      .catch(() => setError("获取收藏夹失败，请刷新重试"))
      .finally(() => setLoadingFolders(false));
  }, [sessionId]);

  // 轮询任务状态
  useEffect(() => {
    if (!jobId) return;

    const poll = async () => {
      try {
        const status = await exportApi.getStatus(jobId);
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

  // 显示/隐藏 Ollama 设置
  useEffect(() => {
    setShowOllamaSettings(asrBackend === "ollama");
  }, [asrBackend]);

  const toggleFolder = (mediaId: number) => {
    setSelectedFolders((prev) => {
      const next = new Set(prev);
      next.has(mediaId) ? next.delete(mediaId) : next.add(mediaId);
      return next;
    });
  };

  const toggleAll = () => {
    if (selectedFolders.size === folders.length) {
      setSelectedFolders(new Set());
    } else {
      setSelectedFolders(new Set(folders.map((f) => f.media_id)));
    }
  };

  const handleStart = async () => {
    if (selectedFolders.size === 0) {
      setError("请至少选择一个收藏夹");
      return;
    }
    setError(null);
    setStarting(true);
    setJobStatus(null);
    setJobId(null);

    const req: ExportRequest = {
      folder_ids: Array.from(selectedFolders),
      asr_backend: asrBackend,
      ollama_url: ollamaUrl,
      ollama_model: ollamaModel,
      ollama_language: ollamaLanguage,
    };

    try {
      const res = await exportApi.start(req, sessionId);
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
    const url = exportApi.getDownloadUrl(jobId);
    const a = document.createElement("a");
    a.href = url;
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

  const isRunning =
    jobStatus?.status === "pending" || jobStatus?.status === "running";
  const isDone = jobStatus?.status === "completed";
  const isFailed = jobStatus?.status === "failed";

  return (
    <div className="export-panel">
      <div className="export-header">
        <h2 className="export-title">
          <svg width="18" height="18" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="1.8" style={{ display: "inline", marginRight: 6, verticalAlign: "middle" }}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M4 16v2a2 2 0 002 2h12a2 2 0 002-2v-2M7 10l5 5m0 0l5-5m-5 5V4" />
          </svg>
          导出为 Markdown
        </h2>
        <p className="export-desc">
          将收藏夹视频转写内容批量导出为本地 Markdown 文件，不影响知识库。
        </p>
      </div>

      {/* 步骤区域 */}
      <div className="export-body">

        {/* Step 1: 选择收藏夹 */}
        <section className="export-step">
          <div className="export-step-title">
            <span className="step-badge">1</span> 选择收藏夹
          </div>

          {loadingFolders ? (
            <p className="export-hint">加载中...</p>
          ) : folders.length === 0 ? (
            <p className="export-hint">暂无收藏夹</p>
          ) : (
            <div className="folder-list">
              <label className="folder-item folder-item-all" onClick={toggleAll}>
                <input
                  type="checkbox"
                  readOnly
                  checked={selectedFolders.size === folders.length}
                  className="folder-checkbox"
                />
                <span className="folder-name">全选（{folders.length} 个收藏夹）</span>
              </label>
              {folders.map((f) => (
                <label key={f.media_id} className="folder-item" onClick={() => toggleFolder(f.media_id)}>
                  <input
                    type="checkbox"
                    readOnly
                    checked={selectedFolders.has(f.media_id)}
                    className="folder-checkbox"
                  />
                  <span className="folder-name">{f.title}</span>
                  <span className="folder-count">{f.media_count} 个视频</span>
                </label>
              ))}
            </div>
          )}
        </section>

        {/* Step 2: 选择 ASR 后端 */}
        <section className="export-step">
          <div className="export-step-title">
            <span className="step-badge">2</span> 转写方式
          </div>

          <div className="asr-options">
            {(["auto", "dashscope", "ollama"] as ASRBackend[]).map((b) => (
              <label key={b} className={`asr-option ${asrBackend === b ? "asr-option-active" : ""}`}>
                <input
                  type="radio"
                  name="asr_backend"
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

          {/* Ollama 详细设置 */}
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
              disabled={starting || selectedFolders.size === 0}
            >
              {starting ? "启动中..." : `导出 ${selectedFolders.size} 个收藏夹`}
            </button>
          )}

          {/* 进度区域 */}
          {jobStatus && (
            <div className="export-progress-wrap">
              {/* 状态徽章 */}
              <div className="export-status-row">
                <span className={`status-badge status-${jobStatus.status}`}>
                  {jobStatus.status === "pending" && "⏳ 等待中"}
                  {jobStatus.status === "running" && "🔄 转写中"}
                  {jobStatus.status === "completed" && "✅ 完成"}
                  {jobStatus.status === "failed" && "❌ 失败"}
                </span>
                <span className="export-msg">{jobStatus.message}</span>
              </div>

              {/* 进度条 */}
              {(isRunning || isDone) && (
                <div className="progress-bar-wrap">
                  <div
                    className="progress-bar-fill"
                    style={{ width: `${jobStatus.progress}%` }}
                  />
                </div>
              )}

              {/* 详情 */}
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

              {/* 完成后操作 */}
              {isDone && (
                <div className="export-actions">
                  <button className="btn btn-primary" onClick={handleDownload}>
                    <svg width="16" height="16" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2" style={{ marginRight: 6 }}>
                      <path strokeLinecap="round" strokeLinejoin="round" d="M4 16v2a2 2 0 002 2h12a2 2 0 002-2v-2M7 10l5 5m0 0l5-5m-5 5V4" />
                    </svg>
                    下载 ZIP（{jobStatus.file_count} 个文件）
                  </button>
                  <button className="btn btn-outline" onClick={handleReset}>
                    重新导出
                  </button>
                </div>
              )}

              {isFailed && (
                <button className="btn btn-outline" onClick={handleReset}>
                  重试
                </button>
              )}
            </div>
          )}
        </section>
      </div>
    </div>
  );
}
