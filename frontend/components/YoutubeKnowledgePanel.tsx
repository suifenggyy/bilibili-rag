"use client";

import { useState, useEffect, useRef } from "react";
import { YoutubeSource, YoutubeBuildStatus, youtubeApi } from "@/lib/api";

interface Props {
    sessionId: string;
}

const SOURCE_TYPE_LABELS: Record<string, string> = {
    video: "单个视频",
    playlist: "播放列表",
    channel: "频道",
    liked: "点赞视频",
    watch_later: "稍后观看",
};

export default function YoutubeKnowledgePanel({ sessionId }: Props) {
    const [sources, setSources] = useState<YoutubeSource[]>([]);
    const [selected, setSelected] = useState<Set<number>>(new Set());
    const [loading, setLoading] = useState(true);
    const [building, setBuilding] = useState(false);
    const [progress, setProgress] = useState<YoutubeBuildStatus | null>(null);
    const [message, setMessage] = useState<string | null>(null);
    const [sourceNameFilter, setSourceNameFilter] = useState("");
    const [filterError, setFilterError] = useState<string | null>(null);

    // 添加来源表单
    const [addUrl, setAddUrl] = useState("");
    const [addAfterDate, setAddAfterDate] = useState("");
    const [adding, setAdding] = useState(false);

    // Cookie 表单
    const [cookieText, setCookieText] = useState("");
    const [showCookieForm, setShowCookieForm] = useState(false);
    const [hasCookie, setHasCookie] = useState(false);
    const [cookieMsg, setCookieMsg] = useState<string | null>(null);

    const logRef = useRef<HTMLDivElement | null>(null);

    const loadData = async () => {
        setLoading(true);
        try {
            const [srcs, cookieStatus] = await Promise.all([
                youtubeApi.listSources(sessionId),
                youtubeApi.getCookieStatus(sessionId),
            ]);
            setSources(srcs);
            setHasCookie(cookieStatus.has_cookie);
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

    const handleAddSource = async () => {
        const url = addUrl.trim();
        if (!url) return;
        setAdding(true);
        setMessage(null);
        try {
            await youtubeApi.addSource(sessionId, url, "auto", addAfterDate || undefined);
            setAddUrl("");
            setAddAfterDate("");
            await loadData();
            setMessage("来源添加成功");
        } catch (err: unknown) {
            setMessage(`添加失败: ${(err as Error).message}`);
        }
        setAdding(false);
    };

    const handleDeleteSource = async (id: number) => {
        try {
            await youtubeApi.deleteSource(id, sessionId);
            setSources((prev) => prev.filter((s) => s.id !== id));
            const s = new Set(selected);
            s.delete(id);
            setSelected(s);
        } catch (err: unknown) {
            setMessage(`删除失败: ${(err as Error).message}`);
        }
    };

    const handleSaveCookie = async () => {
        if (!cookieText.trim()) return;
        try {
            await youtubeApi.saveCookie(sessionId, cookieText.trim());
            setHasCookie(true);
            setShowCookieForm(false);
            setCookieText("");
            setCookieMsg("Cookie 已保存，现可访问点赞/稍后观看列表");
        } catch (err: unknown) {
            setCookieMsg(`保存失败: ${(err as Error).message}`);
        }
    };

    const handleDeleteCookie = async () => {
        try {
            await youtubeApi.deleteCookie(sessionId);
            setHasCookie(false);
            setCookieMsg(null);
        } catch { /* ignore */ }
    };

    const applySourceNameFilter = (filterText: string, currentSources: YoutubeSource[]) => {
        const names = filterText.split(",").map((n) => n.trim()).filter(Boolean);
        if (names.length === 0) {
            setFilterError(null);
            return;
        }
        const missing = names.filter(
            (name) => !currentSources.some(
                (s) => (s.title || s.source_url).toLowerCase() === name.toLowerCase()
            )
        );
        if (missing.length > 0) {
            setFilterError(`未找到来源：${missing.join("、")}`);
            return;
        }
        setFilterError(null);
        const matched = new Set(
            currentSources
                .filter((s) => names.some(
                    (name) => (s.title || s.source_url).toLowerCase() === name.toLowerCase()
                ))
                .map((s) => s.id)
        );
        setSelected(matched);
    };

    const handleSourceNameFilterChange = (value: string) => {
        setSourceNameFilter(value);
        applySourceNameFilter(value, sources);
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

    const handleBuild = async () => {
        if (selected.size === 0) return;
        // 若有名称过滤，先验证
        if (sourceNameFilter.trim()) {
            const names = sourceNameFilter.split(",").map((n) => n.trim()).filter(Boolean);
            const missing = names.filter(
                (name) => !sources.some(
                    (s) => (s.title || s.source_url).toLowerCase() === name.toLowerCase()
                )
            );
            if (missing.length > 0) {
                setFilterError(`未找到来源：${missing.join("、")}`);
                return;
            }
        }
        setBuilding(true);
        setProgress(null);
        setMessage(null);
        try {
            const res = await youtubeApi.build(sessionId, Array.from(selected));
            const poll = async () => {
                const status = await youtubeApi.getBuildStatus(res.task_id);
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

    const addPrivateList = async (type: "liked" | "watch_later") => {
        if (!hasCookie) {
            setMessage("请先配置 YouTube Cookie 才能访问私人列表");
            return;
        }
        setAdding(true);
        try {
            await youtubeApi.addSource(sessionId, type, type);
            await loadData();
            setMessage(`${SOURCE_TYPE_LABELS[type]} 已添加`);
        } catch (err: unknown) {
            setMessage(`添加失败: ${(err as Error).message}`);
        }
        setAdding(false);
    };

    return (
        <div className="panel-inner">
            <div className="panel-header">
                <div>
                    <div className="panel-title">YouTube</div>
                    <div className="panel-subtitle">{sources.length} 个来源</div>
                </div>
                <div className="panel-actions">
                    <button onClick={loadData} className="btn btn-ghost" disabled={loading}>
                        {loading ? "加载中..." : "刷新"}
                    </button>
                </div>
            </div>

            <div className="panel-body" style={{ overflowY: "auto" }}>
                {/* Cookie 配置区 */}
                <div className="mb-4 p-3 rounded-lg border border-[var(--border)]">
                    <div className="flex items-center justify-between mb-2">
                        <span className="text-sm font-medium">
                            Cookie {hasCookie ? (
                                <span className="text-green-500 ml-1">✓ 已配置</span>
                            ) : (
                                <span className="text-[var(--muted)] ml-1">未配置（公开视频无需配置）</span>
                            )}
                        </span>
                        <div className="flex gap-2">
                            {hasCookie && (
                                <button
                                    onClick={handleDeleteCookie}
                                    className="btn btn-ghost text-xs"
                                >
                                    删除
                                </button>
                            )}
                            <button
                                onClick={() => setShowCookieForm(!showCookieForm)}
                                className="btn btn-ghost text-xs"
                            >
                                {hasCookie ? "更新" : "配置"}
                            </button>
                        </div>
                    </div>
                    {showCookieForm && (
                        <div className="mt-2">
                            <textarea
                                className="w-full text-xs font-mono border border-[var(--border)] rounded p-2 bg-[var(--surface)] resize-y"
                                rows={4}
                                placeholder="粘贴 Netscape 格式的 YouTube Cookie（从浏览器扩展导出，如 Get cookies.txt）"
                                value={cookieText}
                                onChange={(e) => setCookieText(e.target.value)}
                            />
                            <button
                                onClick={handleSaveCookie}
                                className="btn btn-primary text-xs mt-2 w-full"
                                disabled={!cookieText.trim()}
                            >
                                保存 Cookie
                            </button>
                        </div>
                    )}
                    {cookieMsg && <div className="text-xs text-[var(--muted)] mt-1">{cookieMsg}</div>}

                    {/* 私人列表快捷入口 */}
                    <div className="flex gap-2 mt-2">
                        <button
                            onClick={() => addPrivateList("liked")}
                            disabled={adding || !hasCookie}
                            className="btn btn-ghost text-xs flex-1"
                            title={hasCookie ? "添加点赞视频列表" : "需要先配置 Cookie"}
                        >
                            + 点赞视频
                        </button>
                        <button
                            onClick={() => addPrivateList("watch_later")}
                            disabled={adding || !hasCookie}
                            className="btn btn-ghost text-xs flex-1"
                            title={hasCookie ? "添加稍后观看列表" : "需要先配置 Cookie"}
                        >
                            + 稍后观看
                        </button>
                    </div>
                </div>

                {/* 添加来源 */}
                <div className="mb-4">
                    <div className="flex gap-2 mb-1">
                        <input
                            type="text"
                            className="flex-1 text-sm border border-[var(--border)] rounded px-3 py-1.5 bg-[var(--surface)]"
                            placeholder="YouTube 视频 / 播放列表 / 频道 URL"
                            value={addUrl}
                            onChange={(e) => setAddUrl(e.target.value)}
                            onKeyDown={(e) => e.key === "Enter" && handleAddSource()}
                        />
                        <button
                            onClick={handleAddSource}
                            disabled={adding || !addUrl.trim()}
                            className="btn btn-primary text-sm px-3"
                        >
                            {adding ? "..." : "添加"}
                        </button>
                    </div>
                    <input
                        type="date"
                        className="w-full text-xs border border-[var(--border)] rounded px-3 py-1 bg-[var(--surface)]"
                        title="仅获取此日期之后上传的视频（留空=全部）"
                        value={addAfterDate}
                        onChange={(e) => setAddAfterDate(e.target.value)}
                    />
                    {addAfterDate && (
                        <div className="text-xs text-[var(--muted)] mt-0.5">仅获取 {addAfterDate} 之后的内容</div>
                    )}
                </div>

                {/* 来源列表 */}
                {/* 来源名称过滤 */}
                {sources.length > 0 && (
                    <div className="mb-3">
                        <input
                            type="text"
                            className="w-full text-sm border border-[var(--border)] rounded px-3 py-1.5 bg-[var(--surface)]"
                            placeholder="指定来源名称（逗号分隔，留空=显示全部）"
                            value={sourceNameFilter}
                            onChange={(e) => handleSourceNameFilterChange(e.target.value)}
                        />
                        {filterError && (
                            <div className="text-xs text-red-400 mt-1">{filterError}</div>
                        )}
                    </div>
                )}
                {loading ? (
                    <div className="text-center text-sm text-[var(--muted)] py-4">加载中...</div>
                ) : sources.length === 0 ? (
                    <div className="text-center text-sm text-[var(--muted)] py-4">
                        暂无来源，请添加 YouTube 视频或播放列表链接
                    </div>
                ) : (
                    <div className="space-y-2">
                        {(() => {
                            const filterNames = sourceNameFilter.split(",").map((n) => n.trim()).filter(Boolean);
                            const visibleSources = filterNames.length > 0
                                ? sources.filter((s) => filterNames.some(
                                    (name) => (s.title || s.source_url).toLowerCase() === name.toLowerCase()
                                ))
                                : sources;
                            return visibleSources.map((s) => (
                            <div
                                key={s.id}
                                className={`folder-card ${selected.has(s.id) ? "selected" : ""}`}
                            >
                                <div className="folder-head">
                                    <input
                                        type="checkbox"
                                        checked={selected.has(s.id)}
                                        onChange={() => toggleSelect(s.id)}
                                        className="w-4 h-4 accent-[var(--accent)]"
                                    />
                                    <div className="folder-meta flex-1 min-w-0">
                                        <div className="folder-title truncate" title={s.title || s.source_url}>
                                            {s.title || s.source_url}
                                        </div>
                                        <div className="folder-count">
                                            {SOURCE_TYPE_LABELS[s.source_type] || s.source_type}
                                            {s.after_date && ` · ${s.after_date}起`}
                                            {s.last_sync_at && ` · 同步: ${new Date(s.last_sync_at).toLocaleDateString()}`}
                                        </div>
                                    </div>
                                    <button
                                        onClick={() => handleDeleteSource(s.id)}
                                        className="text-[var(--muted)] hover:text-red-400 text-xs px-1"
                                    >
                                        ✕
                                    </button>
                                </div>
                            </div>
                        ));
                        })()}
                    </div>
                )}
            </div>

            <div className="panel-footer">
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
                            {progress.processed_videos} / {progress.total_videos} 个视频
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
                    disabled={selected.size === 0 || building || !!filterError}
                    className="btn btn-primary w-full"
                >
                    {building
                        ? progress?.current_step || "处理中..."
                        : selected.size > 0
                        ? `入库 (${selected.size} 个来源)`
                        : "选择来源"}
                </button>
                <p className="text-xs text-[var(--muted)] text-center mt-2">
                    公开视频无需 Cookie · 私人列表需配置 Cookie
                </p>
            </div>
        </div>
    );
}
