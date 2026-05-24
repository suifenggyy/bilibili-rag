"use client";

import { useState, useEffect, useRef } from "react";
import { XiaoyuzhouSubscription, XiaoyuzhouBuildStatus, xiaoyuzhouApi } from "@/lib/api";

interface Props {
    sessionId: string;
}

type LoginStep = "idle" | "phone" | "code" | "done";

export default function XiaoyuzhouKnowledgePanel({ sessionId }: Props) {
    const [subscriptions, setSubscriptions] = useState<XiaoyuzhouSubscription[]>([]);
    const [selected, setSelected] = useState<Set<number>>(new Set());
    const [loading, setLoading] = useState(true);
    const [building, setBuilding] = useState(false);
    const [progress, setProgress] = useState<XiaoyuzhouBuildStatus | null>(null);
    const [message, setMessage] = useState<string | null>(null);

    // 登录状态
    const [loginStep, setLoginStep] = useState<LoginStep>("idle");
    const [phone, setPhone] = useState("");
    const [smsCode, setSmsCode] = useState("");
    const [xyzUser, setXyzUser] = useState<{ nickname?: string; phone?: string } | null>(null);
    const [loginMsg, setLoginMsg] = useState<string | null>(null);
    const [sendingCode, setSendingCode] = useState(false);
    const [loggingIn, setLoggingIn] = useState(false);

    // 手动添加 RSS
    const [rssUrl, setRssUrl] = useState("");
    const [addingRss, setAddingRss] = useState(false);

    // 集数预览
    const [previewSubId, setPreviewSubId] = useState<number | null>(null);
    const [previewEpisodes, setPreviewEpisodes] = useState<{ episode_id: string; title: string; duration?: number }[]>([]);
    const [previewLoading, setPreviewLoading] = useState(false);

    // 构建参数
    const [episodeLimit, setEpisodeLimit] = useState(10);

    const logRef = useRef<HTMLDivElement | null>(null);

    const loadData = async () => {
        setLoading(true);
        try {
            const [subs, authStatus] = await Promise.all([
                xiaoyuzhouApi.listSubscriptions(sessionId),
                xiaoyuzhouApi.getAuthStatus(sessionId),
            ]);
            setSubscriptions(subs);
            if (authStatus.logged_in) {
                setXyzUser({ nickname: authStatus.nickname, phone: authStatus.phone });
                setLoginStep("done");
            }
        } catch (err) {
            console.error(err);
        }
        setLoading(false);
    };

    useEffect(() => {
        loadData();
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [sessionId]);

    // 日志自动滚动到底部
    useEffect(() => {
        if (logRef.current) {
            logRef.current.scrollTop = logRef.current.scrollHeight;
        }
    }, [progress?.logs?.length]);

    // ==================== 登录流程 ====================

    const handleSendCode = async () => {
        if (!phone.trim()) return;
        setSendingCode(true);
        setLoginMsg(null);
        try {
            await xiaoyuzhouApi.sendSms(sessionId, phone.trim());
            setLoginStep("code");
            setLoginMsg("验证码已发送，请查收短信");
        } catch (err: unknown) {
            setLoginMsg(`发送失败: ${(err as Error).message}`);
        }
        setSendingCode(false);
    };

    const handleLogin = async () => {
        if (!smsCode.trim()) return;
        setLoggingIn(true);
        setLoginMsg(null);
        try {
            const result = await xiaoyuzhouApi.login(sessionId, phone.trim(), smsCode.trim());
            setXyzUser({ nickname: result.nickname, phone: phone.trim() });
            setLoginStep("done");
            setLoginMsg("登录成功");
            setSmsCode("");
        } catch (err: unknown) {
            setLoginMsg(`登录失败: ${(err as Error).message}`);
        }
        setLoggingIn(false);
    };

    const handleLogout = async () => {
        try {
            await xiaoyuzhouApi.logout(sessionId);
            setXyzUser(null);
            setLoginStep("idle");
            setLoginMsg(null);
        } catch { /* ignore */ }
    };

    const handleSyncSubscriptions = async () => {
        if (loginStep !== "done") return;
        setLoading(true);
        setMessage(null);
        try {
            const result = await xiaoyuzhouApi.syncSubscriptions(sessionId);
            setMessage(result.message);
            await loadData();
        } catch (err: unknown) {
            setMessage(`同步失败: ${(err as Error).message}`);
        }
        setLoading(false);
    };

    // ==================== 订阅管理 ====================

    const handleAddRss = async () => {
        const url = rssUrl.trim();
        if (!url) return;
        setAddingRss(true);
        setMessage(null);
        try {
            await xiaoyuzhouApi.addSubscription(sessionId, url);
            setRssUrl("");
            await loadData();
            setMessage("播客添加成功");
        } catch (err: unknown) {
            setMessage(`添加失败: ${(err as Error).message}`);
        }
        setAddingRss(false);
    };

    const handleDeleteSub = async (id: number) => {
        try {
            await xiaoyuzhouApi.deleteSubscription(id, sessionId);
            setSubscriptions((prev) => prev.filter((s) => s.id !== id));
            const s = new Set(selected);
            s.delete(id);
            setSelected(s);
        } catch (err: unknown) {
            setMessage(`删除失败: ${(err as Error).message}`);
        }
    };

    const handlePreviewEpisodes = async (subId: number) => {
        if (previewSubId === subId) {
            setPreviewSubId(null);
            return;
        }
        setPreviewSubId(subId);
        setPreviewLoading(true);
        try {
            const eps = await xiaoyuzhouApi.getEpisodes(subId, sessionId, 5);
            setPreviewEpisodes(eps);
        } catch {
            setPreviewEpisodes([]);
        }
        setPreviewLoading(false);
    };

    const toggleSelect = (id: number) => {
        const s = new Set(selected);
        if (s.has(id)) {
            s.delete(id);
        } else {
            s.add(id);
        }
        setSelected(s);
    };

    // ==================== 构建 ====================

    const handleBuild = async () => {
        if (selected.size === 0) return;
        setBuilding(true);
        setProgress(null);
        setMessage(null);
        try {
            const res = await xiaoyuzhouApi.build(sessionId, Array.from(selected), "auto", episodeLimit);
            const poll = async () => {
                const status = await xiaoyuzhouApi.getBuildStatus(res.task_id);
                setProgress(status);
                if (status.status === "running" || status.status === "pending") {
                    setTimeout(poll, 1500);
                } else {
                    setBuilding(false);
                    if (status.status === "completed") {
                        setMessage(status.message || "构建完成");
                        await loadData();
                    } else {
                        setMessage(`构建失败: ${status.message}`);
                    }
                }
            };
            poll();
        } catch (err: unknown) {
            setBuilding(false);
            setMessage(`启动失败: ${(err as Error).message}`);
        }
    };

    const formatDuration = (sec?: number) => {
        if (!sec) return "";
        const h = Math.floor(sec / 3600);
        const m = Math.floor((sec % 3600) / 60);
        return h > 0 ? `${h}h${m}m` : `${m}min`;
    };

    return (
        <div className="panel-inner">
            <div className="panel-header">
                <div>
                    <div className="panel-title">小宇宙播客</div>
                    <div className="panel-subtitle">{subscriptions.length} 个订阅</div>
                </div>
                <div className="panel-actions">
                    <button onClick={loadData} className="btn btn-ghost" disabled={loading}>
                        {loading ? "加载中..." : "刷新"}
                    </button>
                </div>
            </div>

            <div className="panel-body" style={{ overflowY: "auto" }}>
                {/* 登录区域 */}
                <div className="mb-4 p-3 rounded-lg border border-[var(--border)]">
                    {loginStep === "done" && xyzUser ? (
                        <div className="flex items-center justify-between">
                            <div>
                                <span className="text-sm font-medium text-green-500">✓ 已登录</span>
                                {xyzUser.nickname && (
                                    <span className="text-sm text-[var(--muted)] ml-2">{xyzUser.nickname}</span>
                                )}
                            </div>
                            <div className="flex gap-2">
                                <button onClick={handleSyncSubscriptions} className="btn btn-ghost text-xs" disabled={loading}>
                                    同步订阅
                                </button>
                                <button onClick={handleLogout} className="btn btn-ghost text-xs">
                                    退出
                                </button>
                            </div>
                        </div>
                    ) : (
                        <div>
                            <div className="text-sm text-[var(--muted)] mb-2">
                                登录小宇宙（手机号 + 短信验证码）可同步订阅列表
                            </div>
                            {loginStep === "idle" && (
                                <div className="flex gap-2">
                                    <input
                                        type="tel"
                                        className="flex-1 text-sm border border-[var(--border)] rounded px-3 py-1.5 bg-[var(--surface)]"
                                        placeholder="手机号"
                                        value={phone}
                                        onChange={(e) => setPhone(e.target.value)}
                                        onKeyDown={(e) => e.key === "Enter" && handleSendCode()}
                                    />
                                    <button
                                        onClick={handleSendCode}
                                        disabled={sendingCode || !phone.trim()}
                                        className="btn btn-primary text-xs px-3"
                                    >
                                        {sendingCode ? "发送中..." : "发送验证码"}
                                    </button>
                                </div>
                            )}
                            {loginStep === "code" && (
                                <div className="flex gap-2">
                                    <input
                                        type="text"
                                        className="flex-1 text-sm border border-[var(--border)] rounded px-3 py-1.5 bg-[var(--surface)]"
                                        placeholder="验证码"
                                        maxLength={6}
                                        value={smsCode}
                                        onChange={(e) => setSmsCode(e.target.value)}
                                        onKeyDown={(e) => e.key === "Enter" && handleLogin()}
                                    />
                                    <button
                                        onClick={handleLogin}
                                        disabled={loggingIn || !smsCode.trim()}
                                        className="btn btn-primary text-xs px-3"
                                    >
                                        {loggingIn ? "登录中..." : "登录"}
                                    </button>
                                    <button
                                        onClick={() => { setLoginStep("idle"); setLoginMsg(null); }}
                                        className="btn btn-ghost text-xs"
                                    >
                                        重新输入手机号
                                    </button>
                                </div>
                            )}
                            {loginMsg && <div className="text-xs text-[var(--muted)] mt-1">{loginMsg}</div>}
                        </div>
                    )}
                </div>

                {/* 手动添加 RSS */}
                <div className="mb-4">
                    <div className="flex gap-2">
                        <input
                            type="text"
                            className="flex-1 text-sm border border-[var(--border)] rounded px-3 py-1.5 bg-[var(--surface)]"
                            placeholder="小宇宙 RSS 地址（feeds.xiaoyuzhoufm.com/podcast/...）"
                            value={rssUrl}
                            onChange={(e) => setRssUrl(e.target.value)}
                            onKeyDown={(e) => e.key === "Enter" && handleAddRss()}
                        />
                        <button
                            onClick={handleAddRss}
                            disabled={addingRss || !rssUrl.trim()}
                            className="btn btn-primary text-sm px-3"
                        >
                            {addingRss ? "..." : "添加"}
                        </button>
                    </div>
                </div>

                {/* 订阅列表 */}
                {loading ? (
                    <div className="text-center text-sm text-[var(--muted)] py-4">加载中...</div>
                ) : subscriptions.length === 0 ? (
                    <div className="text-center text-sm text-[var(--muted)] py-4">
                        暂无订阅，请登录后同步或手动添加 RSS 地址
                    </div>
                ) : (
                    <div className="space-y-2">
                        {subscriptions.map((s) => (
                            <div
                                key={s.id}
                                className={`folder-card ${selected.has(s.id) ? "selected" : ""}`}
                            >
                                <div className="folder-head" onClick={() => handlePreviewEpisodes(s.id)}>
                                    <input
                                        type="checkbox"
                                        checked={selected.has(s.id)}
                                        onChange={() => toggleSelect(s.id)}
                                        onClick={(e) => e.stopPropagation()}
                                        className="w-4 h-4 accent-[var(--accent)]"
                                    />
                                    {s.cover_url && (
                                        <img
                                            src={s.cover_url}
                                            alt=""
                                            className="w-8 h-8 rounded object-cover flex-shrink-0"
                                            onError={(e) => { (e.target as HTMLImageElement).style.display = "none"; }}
                                        />
                                    )}
                                    <div className="folder-meta flex-1 min-w-0">
                                        <div className="folder-title truncate" title={s.title}>
                                            {s.title}
                                        </div>
                                        <div className="folder-count">
                                            {s.author || "未知作者"}
                                            {s.last_sync_at && ` · 上次同步: ${new Date(s.last_sync_at).toLocaleDateString()}`}
                                        </div>
                                    </div>
                                    <div className="flex items-center gap-1">
                                        <button
                                            onClick={(e) => { e.stopPropagation(); handleDeleteSub(s.id); }}
                                            className="text-[var(--muted)] hover:text-red-400 text-xs px-1"
                                        >
                                            ✕
                                        </button>
                                        <svg
                                            className={`w-4 h-4 transition-transform ${previewSubId === s.id ? "rotate-90" : ""}`}
                                            fill="none"
                                            viewBox="0 0 24 24"
                                            stroke="currentColor"
                                        >
                                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
                                        </svg>
                                    </div>
                                </div>

                                {/* 集数预览 */}
                                {previewSubId === s.id && (
                                    <div className="folder-list">
                                        {previewLoading ? (
                                            <div className="text-xs text-[var(--muted)]">加载中...</div>
                                        ) : previewEpisodes.length === 0 ? (
                                            <div className="text-xs text-[var(--muted)]">暂无集数</div>
                                        ) : (
                                            previewEpisodes.map((ep) => (
                                                <div key={ep.episode_id} className="video-item">
                                                    <span className="text-[var(--accent)]">🎵</span>
                                                    <span className="truncate" title={ep.title}>{ep.title}</span>
                                                    {ep.duration && (
                                                        <span className="text-[var(--muted)] text-xs ml-auto flex-shrink-0">
                                                            {formatDuration(ep.duration)}
                                                        </span>
                                                    )}
                                                </div>
                                            ))
                                        )}
                                    </div>
                                )}
                            </div>
                        ))}
                    </div>
                )}
            </div>

            <div className="panel-footer">
                {/* 集数限制 */}
                <div className="flex items-center gap-2 mb-3">
                    <span className="text-xs text-[var(--muted)]">每播客最多</span>
                    <input
                        type="number"
                        min={1}
                        max={100}
                        value={episodeLimit}
                        onChange={(e) => setEpisodeLimit(Number(e.target.value) || 10)}
                        className="w-16 text-xs border border-[var(--border)] rounded px-2 py-1 bg-[var(--surface)] text-center"
                    />
                    <span className="text-xs text-[var(--muted)]">集（0=全部）</span>
                </div>

                {/* 进度条 */}
                {progress && building && (
                    <div className="mb-4">
                        <div className="flex justify-between text-xs mb-2">
                            <span className="text-[var(--muted)] truncate">{progress.current_step}</span>
                            <span className="text-[var(--accent)]">{progress.progress}%</span>
                        </div>
                        <div className="progress">
                            <div className="progress-bar" style={{ width: `${progress.progress}%` }} />
                        </div>
                        <div className="text-xs text-[var(--muted)] mt-1">
                            {progress.processed_episodes} / {progress.total_episodes} 集
                        </div>
                    </div>
                )}

                {message && <div className="text-xs text-[var(--muted)] mb-3">{message}</div>}

                {progress?.logs && progress.logs.length > 0 && (
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
                            marginBottom: 12,
                        }}
                    >
                        {progress.logs.slice(-50).map((line, i) => (
                            <div key={i} style={{ lineHeight: 1.6 }}>{line}</div>
                        ))}
                    </div>
                )}

                <button
                    onClick={handleBuild}
                    disabled={selected.size === 0 || building}
                    className="btn btn-primary w-full"
                >
                    {building
                        ? progress?.current_step || "处理中..."
                        : selected.size > 0
                        ? `入库 (${selected.size} 个播客)`
                        : "选择播客"}
                </button>
                <p className="text-xs text-[var(--muted)] text-center mt-2">
                    登录后可同步订阅 · 不登录也可手动添加 RSS 地址
                </p>
            </div>
        </div>
    );
}
