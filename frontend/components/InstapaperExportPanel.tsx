"use client";

import { useState, useEffect, useRef } from "react";
import {
  InstapaperFolder,
  InstapaperExportRequest,
  InstapaperExportJobStatus,
  instapaperExportApi,
  settingsApi,
} from "@/lib/api";

const INSTAPAPER_SESSION_KEY = "instapaper_session_id";

// 内置文件夹（与后端保持一致）
const BUILTIN_FOLDERS: InstapaperFolder[] = [
  { folder_id: "unread",  title: "稍后阅读" },
  { folder_id: "starred", title: "星标收藏" },
  { folder_id: "archive", title: "已归档" },
];

export default function InstapaperExportPanel() {
  // 认证
  const [consumerKey, setConsumerKey] = useState("");
  const [consumerSecret, setConsumerSecret] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [savedSessionId, setSavedSessionId] = useState<string | null>(null);
  const [credsSaved, setCredsSaved] = useState(false);

  // 文件夹
  const [folders, setFolders] = useState<InstapaperFolder[]>(BUILTIN_FOLDERS);
  const [selectedFolders, setSelectedFolders] = useState<Set<string>>(new Set(["starred"]));
  const [loadingFolders, setLoadingFolders] = useState(false);

  // 导出配置
  const [limit, setLimit] = useState(0);

  // 任务状态
  const [jobId, setJobId] = useState<string | null>(null);
  const [jobStatus, setJobStatus] = useState<InstapaperExportJobStatus | null>(null);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const logRef = useRef<HTMLDivElement | null>(null);

  // 页面挂载时：尝试从 localStorage 恢复凭据
  useEffect(() => {
    const sid = localStorage.getItem(INSTAPAPER_SESSION_KEY);
    if (!sid) return;
    instapaperExportApi.getSession(sid).then((creds) => {
      setConsumerKey(creds.consumer_key);
      setConsumerSecret(creds.consumer_secret);
      setEmail(creds.email);
      setPassword(creds.password);
      setSavedSessionId(sid);
      setCredsSaved(true);
    }).catch(() => {
      localStorage.removeItem(INSTAPAPER_SESSION_KEY);
    });
  }, []);

  // 从 .env 预填凭据（若字段为空）
  useEffect(() => {
    settingsApi.getPrefill().then((cfg) => {
      if (cfg.instapaper_consumer_key && !consumerKey) setConsumerKey(cfg.instapaper_consumer_key);
      if (cfg.instapaper_consumer_secret && !consumerSecret) setConsumerSecret(cfg.instapaper_consumer_secret);
      if (cfg.instapaper_email && !email) setEmail(cfg.instapaper_email);
      if (cfg.instapaper_password && !password) setPassword(cfg.instapaper_password);
    }).catch(() => {});
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // 轮询任务状态
  useEffect(() => {
    if (!jobId) return;
    const poll = async () => {
      try {
        const status = await instapaperExportApi.getStatus(jobId);
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
    return () => { if (pollRef.current) clearInterval(pollRef.current); };
  }, [jobId]);

  // 日志自动滚动到底部
  useEffect(() => {
    if (logRef.current) {
      logRef.current.scrollTop = logRef.current.scrollHeight;
    }
  }, [jobStatus?.logs?.length]);

  const toggleFolder = (folderId: string) => {
    setSelectedFolders(prev => {
      const next = new Set(prev);
      if (next.has(folderId)) { next.delete(folderId); } else { next.add(folderId); }
      return next;
    });
  };

  const saveCredentials = async (sid: string) => {
    try {
      await instapaperExportApi.saveSession(sid, {
        consumer_key: consumerKey,
        consumer_secret: consumerSecret,
        email,
        password,
      });
      localStorage.setItem(INSTAPAPER_SESSION_KEY, sid);
      setSavedSessionId(sid);
      setCredsSaved(true);
    } catch { /* non-critical, ignore */ }
  };

  const handleLogout = async () => {
    if (savedSessionId) {
      try { await instapaperExportApi.deleteSession(savedSessionId); } catch { /* ignore */ }
      localStorage.removeItem(INSTAPAPER_SESSION_KEY);
    }
    setSavedSessionId(null);
    setCredsSaved(false);
    setConsumerKey("");
    setConsumerSecret("");
    setEmail("");
    setPassword("");
    setFolders(BUILTIN_FOLDERS);
  };

  const handleLoadFolders = async () => {
    if (!consumerKey || !consumerSecret || !email || !password) {
      setError("请先填写完整的凭据信息再加载文件夹");
      return;
    }
    setError(null);
    setLoadingFolders(true);
    try {
      const res = await instapaperExportApi.getFolders(consumerKey, consumerSecret, email, password);
      setFolders(res.folders);
      // Save credentials on first successful verification
      const sid = savedSessionId || crypto.randomUUID();
      await saveCredentials(sid);
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : "加载文件夹失败，请检查凭据";
      setError(msg);
      setFolders(BUILTIN_FOLDERS);
    } finally {
      setLoadingFolders(false);
    }
  };

  const handleStart = async () => {
    if (!consumerKey || !consumerSecret) {
      setError("请填写 API Consumer Key 和 Consumer Secret");
      return;
    }
    if (!email || !password) {
      setError("请填写 Instapaper 登录邮箱和密码");
      return;
    }
    if (selectedFolders.size === 0) {
      setError("请至少选择一个文件夹");
      return;
    }
    setError(null);
    setStarting(true);
    setJobStatus(null);
    setJobId(null);

    const req: InstapaperExportRequest = {
      consumer_key: consumerKey,
      consumer_secret: consumerSecret,
      email,
      password,
      folders: Array.from(selectedFolders),
      limit,
    };

    try {
      const res = await instapaperExportApi.start(req);
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
    a.href = instapaperExportApi.getDownloadUrl(jobId);
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
              d="M12 6.253v13m0-13C10.832 5.477 9.246 5 7.5 5S4.168 5.477 3 6.253v13C4.168 18.477 5.754 18 7.5 18s3.332.477 4.5 1.253m0-13C13.168 5.477 14.754 5 16.5 5c1.747 0 3.332.477 4.5 1.253v13C19.832 18.477 18.247 18 16.5 18c-1.746 0-3.332.477-4.5 1.253" />
          </svg>
          Instapaper 书签导出
        </h2>
        <p className="export-desc">
          提取 Instapaper 收藏文章正文，导出为 Markdown 文件。免费账户可用，无需 Premium 订阅。
        </p>
      </div>

      <div className="export-body">

        {/* Step 1: 认证配置 */}
        <section className="export-step">
          <div className="export-step-title">
            <span className="step-badge">1</span> API 凭据
            {credsSaved && (
              <span style={{ marginLeft: "auto", fontSize: 12, color: "var(--accent)" }}>
                ✅ 已保存
                <button
                  className="btn btn-ghost"
                  style={{ fontSize: 11, marginLeft: 8, padding: "2px 8px", color: "#ef4444" }}
                  onClick={handleLogout}
                >
                  退出
                </button>
              </span>
            )}
          </div>

          <div className="instapaper-hint">
            <a href="https://www.instapaper.com/main/request_oauth_consumer_token" target="_blank" rel="noopener noreferrer">
              申请 Consumer Key/Secret →
            </a>
          </div>

          <div className="douyin-fields">
            <div className="ollama-field">
              <label className="ollama-label">Consumer Key</label>
              <input
                className="ollama-input"
                type="text"
                value={consumerKey}
                onChange={(e) => setConsumerKey(e.target.value)}
                placeholder="Instapaper Consumer Key"
                autoComplete="off"
              />
            </div>
            <div className="ollama-field">
              <label className="ollama-label">Consumer Secret</label>
              <input
                className="ollama-input"
                type="password"
                value={consumerSecret}
                onChange={(e) => setConsumerSecret(e.target.value)}
                placeholder="Instapaper Consumer Secret"
                autoComplete="off"
              />
            </div>
            <div className="ollama-field">
              <label className="ollama-label">登录邮箱</label>
              <input
                className="ollama-input"
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="your@email.com"
              />
            </div>
            <div className="ollama-field">
              <label className="ollama-label">登录密码</label>
              <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
                <input
                  className="ollama-input"
                  type={showPassword ? "text" : "password"}
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  placeholder="Instapaper 密码"
                  style={{ flex: 1 }}
                />
                <button
                  className="btn btn-outline"
                  style={{ padding: "4px 10px", fontSize: 12 }}
                  onClick={() => setShowPassword(!showPassword)}
                  type="button"
                >
                  {showPassword ? "隐藏" : "显示"}
                </button>
              </div>
            </div>
          </div>
        </section>

        {/* Step 2: 文件夹选择 */}
        <section className="export-step">
          <div className="export-step-title">
            <span className="step-badge">2</span> 选择文件夹
            <button
              className="btn btn-outline"
              style={{ marginLeft: "auto", padding: "3px 10px", fontSize: 12 }}
              onClick={handleLoadFolders}
              disabled={loadingFolders}
            >
              {loadingFolders ? "加载中..." : "加载自定义文件夹"}
            </button>
          </div>

          <div className="folder-list">
            <label className="folder-item folder-item-all"
              onClick={() => {
                if (selectedFolders.size === folders.length) {
                  setSelectedFolders(new Set());
                } else {
                  setSelectedFolders(new Set(folders.map(f => f.folder_id)));
                }
              }}>
              <input type="checkbox" readOnly className="folder-checkbox"
                checked={selectedFolders.size === folders.length && folders.length > 0} />
              <span className="folder-name">全选（{folders.length} 个文件夹）</span>
            </label>
            {folders.map(f => (
              <label key={f.folder_id} className="folder-item" onClick={() => toggleFolder(f.folder_id)}>
                <input type="checkbox" readOnly className="folder-checkbox"
                  checked={selectedFolders.has(f.folder_id)} />
                <span className="folder-name">{f.title}</span>
                <span className="folder-count" style={{ fontSize: 11, color: "var(--text-muted)" }}>
                  {f.folder_id}
                </span>
              </label>
            ))}
          </div>

          <div className="ollama-field" style={{ marginTop: 12 }}>
            <label className="ollama-label">每个文件夹导出数量限制</label>
            <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
              <input
                className="ollama-input ollama-input-sm"
                type="number" min={0}
                value={limit}
                onChange={(e) => setLimit(Math.max(0, parseInt(e.target.value) || 0))}
                style={{ width: 100 }}
              />
              <span className="ollama-hint">{limit === 0 ? "0 = 全部导出" : `最多 ${limit} 篇`}</span>
            </div>
          </div>
        </section>

        {/* Step 3: 执行 */}
        <section className="export-step">
          <div className="export-step-title">
            <span className="step-badge">3</span> 开始导出
          </div>

          <p className="ollama-hint" style={{ marginBottom: 12 }}>
            正文会先用 <strong>requests + trafilatura</strong> 提取，失败后回退到 <strong>Playwright</strong> 渲染抓取；仍失败时只保存标题和链接。
          </p>

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
              {starting ? "登录验证中..." : `导出 ${selectedFolders.size} 个文件夹`}
            </button>
          )}

          {jobStatus && (
            <div className="export-progress-wrap">
              <div className="export-status-row">
                <span className={`status-badge status-${jobStatus.status}`}>
                  {jobStatus.status === "pending" && "⏳ 等待中"}
                  {jobStatus.status === "running" && "🔄 提取中"}
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
                  <span>{jobStatus.processed_articles} / {jobStatus.total_articles} 篇文章</span>
                  {jobStatus.file_count > 0 && (
                    <span>已生成 {jobStatus.file_count} 个文件</span>
                  )}
                </div>
              )}

              {isRunning && jobStatus.current_article && (
                <div className="progress-current">
                  <span className="current-label">当前：</span>
                  <span className="current-name">{jobStatus.current_article}</span>
                </div>
              )}

              {jobStatus.logs && jobStatus.logs.length > 0 && (
                <div
                  ref={logRef}
                  style={{
                    background: "#0f172a",
                    borderRadius: 8,
                    padding: "10px 14px",
                    maxHeight: 200,
                    overflowY: "auto",
                    fontFamily: "monospace",
                    fontSize: 12,
                    color: "#94a3b8",
                    marginTop: 12,
                  }}
                >
                  {jobStatus.logs.slice(-50).map((line, i) => (
                    <div key={i} style={{ lineHeight: 1.6 }}>{line}</div>
                  ))}
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
