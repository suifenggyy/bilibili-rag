"use client";

import { useState, useEffect, useRef } from "react";
import {
  DouyinExportRequest,
  DouyinExportJobStatus,
  DouyinQRCodeResponse,
  DouyinCreator,
  douyinExportApi,
  douyinCreatorApi,
  settingsApi,
} from "@/lib/api";

const DOUYIN_SESSION_KEY = "douyin_session_id";
type ASRBackend = "auto" | "dashscope" | "ollama" | "whisper";
type CookieMode = "qrcode" | "manual";
type QRStatus = "idle" | "loading" | "waiting" | "scanned" | "confirmed" | "expired" | "error";

interface Props {
  sessionId: string;
}

export default function DouyinExportPanel({ sessionId }: Props) {
  // Cookie 获取方式
  const [cookieMode, setCookieMode] = useState<CookieMode>("qrcode");
  const [cookie, setCookie] = useState("");
  const [douyinSessionId, setDouyinSessionId] = useState<string | null>(null);
  const [evil0ctalUrl, setEvil0ctalUrl] = useState("http://localhost:2333");
  const [limit, setLimit] = useState<number>(0);

  // QR 登录状态
  const [qrData, setQrData] = useState<DouyinQRCodeResponse | null>(null);
  const [qrStatus, setQrStatus] = useState<QRStatus>("idle");
  const [qrMessage, setQrMessage] = useState("");
  const qrPollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // ASR 配置
  const [asrBackend, setAsrBackend] = useState<ASRBackend>("auto");
  const [ollamaUrl, setOllamaUrl] = useState("http://localhost:11434");
  const [ollamaModel, setOllamaModel] = useState("whisper");
  const [ollamaLanguage, setOllamaLanguage] = useState("zh");
  const showOllamaSettings = asrBackend === "ollama";

  // 任务状态
  const [jobId, setJobId] = useState<string | null>(null);
  const [jobStatus, setJobStatus] = useState<DouyinExportJobStatus | null>(null);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const logRef = useRef<HTMLDivElement | null>(null);

  // 创作者管理
  const [creators, setCreators] = useState<DouyinCreator[]>([]);
  const [creatorSecUid, setCreatorSecUid] = useState("");
  const [creatorNickname, setCreatorNickname] = useState("");
  const [creatorAfterDate, setCreatorAfterDate] = useState("");
  const [creatorAdding, setCreatorAdding] = useState(false);
  const [creatorSyncing, setCreatorSyncing] = useState(false);
  const [creatorMessage, setCreatorMessage] = useState<string | null>(null);
  const [showCreators, setShowCreators] = useState(false);

  // 页面挂载时：尝试从 localStorage 恢复已保存的 Cookie
  useEffect(() => {
    const savedId = localStorage.getItem(DOUYIN_SESSION_KEY);
    if (!savedId) return;
    douyinExportApi.getSession(savedId).then((res) => {
      setCookie(res.cookie_str);
      setDouyinSessionId(savedId);
      setQrStatus("confirmed");
      setQrMessage("✅ 已从上次登录恢复");
    }).catch(() => {
      // 会话已失效，清除
      localStorage.removeItem(DOUYIN_SESSION_KEY);
    });
  }, []);

  // 从 .env 预填 Cookie（若 Cookie 为空）
  useEffect(() => {
    settingsApi.getPrefill().then((cfg) => {
      if (cfg.douyin_cookie && !cookie) {
        setCookie(cfg.douyin_cookie);
        setCookieMode("manual");
      }
    }).catch(() => {});
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // 轮询导出任务状态
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

  // 日志自动滚动到底部
  useEffect(() => {
    if (logRef.current) {
      logRef.current.scrollTop = logRef.current.scrollHeight;
    }
  }, [jobStatus?.logs?.length]);

  // 切换 tab 时停止 QR 轮询
  useEffect(() => {
    if (cookieMode !== "qrcode") {
      if (qrPollRef.current) clearInterval(qrPollRef.current);
    }
  }, [cookieMode]);

  // 加载创作者列表
  const loadCreators = async () => {
    try {
      const list = await douyinCreatorApi.list(sessionId);
      setCreators(list);
    } catch { /* ignore */ }
  };

  useEffect(() => {
    loadCreators();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionId]);

  // ==================== 创作者管理 ====================

  const handleAddCreator = async () => {
    const secUid = creatorSecUid.trim();
    if (!secUid) return;
    setCreatorAdding(true);
    setCreatorMessage(null);
    try {
      await douyinCreatorApi.add(sessionId, secUid, creatorNickname.trim() || undefined, creatorAfterDate || undefined);
      setCreatorSecUid("");
      setCreatorNickname("");
      setCreatorAfterDate("");
      await loadCreators();
      setCreatorMessage("创作者已添加");
    } catch (e: unknown) {
      setCreatorMessage(`添加失败: ${(e as Error).message}`);
    }
    setCreatorAdding(false);
  };

  const handleDeleteCreator = async (id: number) => {
    try {
      await douyinCreatorApi.delete(sessionId, id);
      await loadCreators();
    } catch (e: unknown) {
      setCreatorMessage(`删除失败: ${(e as Error).message}`);
    }
  };

  const handleSyncCreators = async () => {
    setCreatorSyncing(true);
    setCreatorMessage(null);
    try {
      const res = await douyinCreatorApi.sync(sessionId);
      setCreatorMessage(`同步任务已启动 (${res.task_id.slice(0, 8)}...)`);
    } catch (e: unknown) {
      setCreatorMessage(`同步失败: ${(e as Error).message}`);
    }
    setCreatorSyncing(false);
  };

  // ==================== QR 登录 ====================

  const startQRLogin = async () => {
    if (qrPollRef.current) clearInterval(qrPollRef.current);
    setQrStatus("loading");
    setQrMessage("");
    setQrData(null);
    try {
      const data = await douyinExportApi.generateQrcode();
      setQrData(data);
      setQrStatus("waiting");
      setQrMessage("请用抖音 APP 扫描二维码");
      startPollingQR(data.token);
    } catch (err: unknown) {
      setQrStatus("error");
      setQrMessage((err as Error).message || "生成二维码失败");
    }
  };

  const startPollingQR = (token: string) => {
    if (qrPollRef.current) clearInterval(qrPollRef.current);
    qrPollRef.current = setInterval(async () => {
      try {
        const result = await douyinExportApi.pollQrcode(token);
        setQrMessage(result.message);
        if (result.status === "confirmed" && result.cookie_str) {
          if (qrPollRef.current) clearInterval(qrPollRef.current);
          setQrStatus("confirmed");
          setCookie(result.cookie_str);
          // Persist session so it survives page refresh
          if (result.session_id) {
            localStorage.setItem(DOUYIN_SESSION_KEY, result.session_id);
            setDouyinSessionId(result.session_id);
          }
        } else if (result.status === "expired") {
          if (qrPollRef.current) clearInterval(qrPollRef.current);
          setQrStatus("expired");
        } else if (result.status === "scanned") {
          setQrStatus("scanned");
        }
      } catch {
        if (qrPollRef.current) clearInterval(qrPollRef.current);
        setQrStatus("error");
        setQrMessage("轮询失败，请重试");
      }
    }, 2000);
  };

  // ==================== 导出 ====================

  const handleStart = async () => {
    if (!cookie.trim()) {
      setError(cookieMode === "qrcode" ? "请先完成扫码登录" : "请填入抖音 Cookie");
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

  const handleLogout = async () => {
    if (douyinSessionId) {
      try { await douyinExportApi.deleteSession(douyinSessionId); } catch { /* ignore */ }
      localStorage.removeItem(DOUYIN_SESSION_KEY);
    }
    setDouyinSessionId(null);
    setCookie("");
    setQrStatus("idle");
    setQrMessage("");
    setQrData(null);
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
  const hasCookie = !!cookie.trim();

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

        {/* Step 1: 登录方式 */}
        <section className="export-step">
          <div className="export-step-title">
            <span className="step-badge">1</span> 抖音登录
          </div>

          {/* Tab 切换 */}
          <div style={{ display: "flex", gap: 8, marginBottom: 16 }}>
            <button
              className={`workspace-tab ${cookieMode === "qrcode" ? "workspace-tab-active" : ""}`}
              style={{ fontSize: 13 }}
              onClick={() => setCookieMode("qrcode")}
            >
              📱 扫码登录
            </button>
            <button
              className={`workspace-tab ${cookieMode === "manual" ? "workspace-tab-active" : ""}`}
              style={{ fontSize: 13 }}
              onClick={() => setCookieMode("manual")}
            >
              🔑 手动 Cookie
            </button>
          </div>

          {/* 扫码登录 */}
          {cookieMode === "qrcode" && (
            <div>
              {qrStatus === "idle" && (
                <div style={{ textAlign: "center", padding: "20px 0" }}>
                  <p className="ollama-hint" style={{ marginBottom: 12 }}>
                    点击按钮生成二维码，然后用抖音 APP 扫码登录
                  </p>
                  <button className="btn btn-primary" onClick={startQRLogin}>
                    生成登录二维码
                  </button>
                </div>
              )}

              {qrStatus === "loading" && (
                <div style={{ textAlign: "center", padding: "20px 0" }}>
                  <p className="ollama-hint">正在生成二维码...</p>
                </div>
              )}

              {(qrStatus === "waiting" || qrStatus === "scanned") && qrData && (
                <div style={{ textAlign: "center" }}>
                  {/* eslint-disable-next-line @next/next/no-img-element */}
                  <img
                    src={qrData.qrcode_image_base64}
                    alt="抖音登录二维码"
                    style={{ width: 180, height: 180, margin: "0 auto 12px", display: "block", borderRadius: 8 }}
                  />
                  <p style={{
                    fontSize: 13,
                    color: qrStatus === "scanned" ? "var(--accent)" : "var(--muted)",
                    marginBottom: 8,
                  }}>
                    {qrStatus === "scanned" ? "✅ 已扫码，请在 APP 上点击确认" : "📱 请用抖音 APP 扫码"}
                  </p>
                  <button className="btn btn-ghost" style={{ fontSize: 12 }} onClick={startQRLogin}>
                    刷新二维码
                  </button>
                </div>
              )}

              {qrStatus === "confirmed" && (
                <div style={{ textAlign: "center", padding: "16px 0" }}>
                  <div style={{ fontSize: 32, marginBottom: 8 }}>✅</div>
                  <p style={{ color: "var(--accent)", fontWeight: 600, marginBottom: 4 }}>登录成功</p>
                  <p className="ollama-hint">Cookie 已自动填入，可继续配置并导出</p>
                  <div style={{ display: "flex", gap: 8, justifyContent: "center", marginTop: 8 }}>
                    <button className="btn btn-ghost" style={{ fontSize: 12 }} onClick={startQRLogin}>
                      重新登录
                    </button>
                    <button className="btn btn-ghost" style={{ fontSize: 12, color: "#ef4444" }} onClick={handleLogout}>
                      退出登录
                    </button>
                  </div>
                </div>
              )}

              {qrStatus === "expired" && (
                <div style={{ textAlign: "center", padding: "16px 0" }}>
                  <p style={{ color: "#f59e0b", marginBottom: 8 }}>⚠️ 二维码已过期</p>
                  <button className="btn btn-primary" onClick={startQRLogin}>
                    重新生成
                  </button>
                </div>
              )}

              {qrStatus === "error" && (
                <div style={{ textAlign: "center", padding: "16px 0" }}>
                  <p style={{ color: "#ef4444", marginBottom: 4, fontSize: 13 }}>❌ {qrMessage}</p>
                  <p className="ollama-hint" style={{ marginBottom: 8 }}>
                    提示：此功能依赖抖音 SSO 接口，网络不稳定时可能失败，可改用手动 Cookie
                  </p>
                  <button className="btn btn-primary" onClick={startQRLogin}>重试</button>
                </div>
              )}
            </div>
          )}

          {/* 手动 Cookie */}
          {cookieMode === "manual" && (
            <div className="douyin-cookie-guide">
              <p className="ollama-hint" style={{ marginBottom: 8 }}>
                Chrome 打开 douyin.com 并登录 → F12 → Application → Cookies → douyin.com → 复制所有字段
              </p>
              <textarea
                className="douyin-cookie-input"
                placeholder={"ttwid=xxx; sessionid=xxx; odin_tt=xxx; msToken=xxx; ..."}
                value={cookie}
                onChange={(e) => setCookie(e.target.value)}
                rows={3}
              />
            </div>
          )}

          {/* 已有 Cookie 提示 */}
          {hasCookie && cookieMode === "qrcode" && qrStatus !== "confirmed" && (
            <p className="ollama-hint" style={{ marginTop: 8, textAlign: "center" }}>
              已有 Cookie（{cookie.length} 字符）
            </p>
          )}

          <div className="douyin-fields" style={{ marginTop: 16 }}>
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
            {(["auto", "dashscope", "ollama", "whisper"] as ASRBackend[]).map((b) => (
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
                    {b === "auto" ? "自动选择" : b === "dashscope" ? "DashScope 云端"
                      : b === "ollama" ? "Ollama 本地" : "openai-whisper 本地"}
                  </span>
                  <span className="asr-option-desc">
                    {b === "auto" ? "按 .env 中 ASR_BACKEND 选择"
                      : b === "dashscope" ? "paraformer-v2，中文效果最佳"
                      : b === "ollama" ? "Whisper，需本地 Ollama 服务"
                      : "Whisper，直接在本机运行，无需额外服务"}
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
              disabled={starting || !hasCookie}
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

        {/* 创作者作品获取 */}
        <section className="export-step">
          <div className="export-step-title">
            <button
              className="w-full flex items-center justify-between"
              onClick={() => setShowCreators((v) => !v)}
              style={{ background: "none", border: "none", cursor: "pointer", padding: 0 }}
            >
              <span>
                <span className="step-badge">+</span> 创作者作品 {creators.length > 0 ? `(${creators.length} 位)` : "（可选）"}
              </span>
              <span className="text-xs text-gray-400">{showCreators ? "▲" : "▼"}</span>
            </button>
          </div>
          {showCreators && (
            <div style={{ marginTop: 12 }}>
              <div className="douyin-fields">
                <div className="ollama-field">
                  <label className="ollama-label">创作者主页 URL 或 sec_uid</label>
                  <input
                    className="ollama-input"
                    placeholder="https://www.douyin.com/user/MS4w... 或 sec_uid"
                    value={creatorSecUid}
                    onChange={(e) => setCreatorSecUid(e.target.value)}
                  />
                </div>
                <div className="ollama-field">
                  <label className="ollama-label">昵称（可选）</label>
                  <input
                    className="ollama-input"
                    placeholder="创作者昵称，用于展示"
                    value={creatorNickname}
                    onChange={(e) => setCreatorNickname(e.target.value)}
                  />
                </div>
                <div className="ollama-field">
                  <label className="ollama-label">仅获取此日期之后的作品（留空=全部）</label>
                  <input
                    type="date"
                    className="ollama-input"
                    value={creatorAfterDate}
                    onChange={(e) => setCreatorAfterDate(e.target.value)}
                  />
                </div>
                <button
                  className="btn btn-primary"
                  onClick={handleAddCreator}
                  disabled={creatorAdding || !creatorSecUid.trim()}
                >
                  {creatorAdding ? "添加中..." : "+ 添加创作者"}
                </button>
              </div>

              {creators.length > 0 && (
                <div style={{ marginTop: 12 }}>
                  <div style={{ fontSize: 13, fontWeight: 500, marginBottom: 6 }}>已配置创作者</div>
                  {creators.map((c) => (
                    <div key={c.id} style={{ display: "flex", alignItems: "center", justifyContent: "space-between", padding: "4px 0", borderBottom: "1px solid var(--border)" }}>
                      <div>
                        <span style={{ fontSize: 13 }}>{c.nickname || c.sec_uid.slice(0, 20) + "..."}</span>
                        {c.after_date && <span style={{ fontSize: 11, color: "var(--muted)", marginLeft: 6 }}>{c.after_date}起</span>}
                      </div>
                      <button onClick={() => handleDeleteCreator(c.id)} style={{ fontSize: 12, color: "var(--muted)", cursor: "pointer", background: "none", border: "none" }}>✕</button>
                    </div>
                  ))}
                  <button
                    className="btn btn-outline"
                    style={{ marginTop: 8, width: "100%" }}
                    onClick={handleSyncCreators}
                    disabled={creatorSyncing}
                  >
                    {creatorSyncing ? "同步中..." : "同步创作者作品"}
                  </button>
                </div>
              )}

              {creatorMessage && <div style={{ fontSize: 12, color: "var(--muted)", marginTop: 8 }}>{creatorMessage}</div>}
            </div>
          )}
        </section>
      </div>
    </div>
  );
}
