/**
 * API 客户端
 */

export const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

// 通用请求函数
async function request<T>(
    endpoint: string,
    options: RequestInit = {}
): Promise<T> {
    const url = `${API_BASE_URL}${endpoint}`;

    const response = await fetch(url, {
        ...options,
        headers: {
            "Content-Type": "application/json",
            ...options.headers,
        },
    });

    // 会话失效时自动清除登录状态并刷新页面
    if (response.status === 401) {
        if (typeof window !== "undefined") {
            localStorage.removeItem("bili_session");
            localStorage.removeItem("bili_user");
            window.location.href = "/";
        }
        throw new Error("会话已过期，请重新登录");
    }

    if (!response.ok) {
        const error = await response.text();
        throw new Error(error || `请求失败: ${response.status}`);
    }

    return response.json();
}

// ==================== 类型定义 ====================

export interface QRCodeResponse {
    qrcode_key: string;
    qrcode_url: string;
    qrcode_image_base64: string;
}

export interface LoginStatusResponse {
    status: "waiting" | "scanned" | "confirmed" | "expired";
    message: string;
    user_info?: UserInfo;
    session_id?: string;
}

export interface UserInfo {
    mid: number;
    uname: string;
    face: string;
    level?: number;
}

export interface FavoriteFolder {
    media_id: number;
    title: string;
    media_count: number;
    is_selected: boolean;
    is_default?: boolean;
}

export interface Video {
    bvid: string;
    title: string;
    cover?: string;
    duration?: number;
    owner?: string;
    play_count?: number;
    intro?: string;
    is_selected: boolean;
}

export interface FavoriteVideosResponse {
    folder_info: Record<string, unknown>;
    videos: Video[];
    has_more: boolean;
    page: number;
    page_size: number;
}

export interface OrganizePreviewItem {
    bvid: string;
    title: string;
    resource_id: number;
    resource_type: number;
    target_folder_id: number | null;
    target_folder_title: string;
    reason?: string;
}

export interface OrganizePreviewResponse {
    default_folder_id: number;
    default_folder_title: string;
    folders: FavoriteFolder[];
    items: OrganizePreviewItem[];
    stats: {
        total: number;
        matched: number;
        unmatched: number;
    };
}

export interface BuildRequest {
    folder_ids: number[];
    exclude_bvids?: string[];
}

export interface BuildStatus {
    task_id: string;
    status: "pending" | "running" | "completed" | "failed";
    progress: number;
    current_step: string;
    total_videos: number;
    processed_videos: number;
    message: string;
}

export interface FolderStatus {
    media_id: number;
    indexed_count: number;
    media_count?: number;
    last_sync_at?: string;
}

export interface SyncRequest {
    folder_ids?: number[];
}

export interface SyncResult {
    folder_id: number;
    total: number;
    added: number;
    removed: number;
    indexed: number;
    message: string;
    last_sync_at: string;
}

export interface KnowledgeStats {
    total_chunks: number;
    total_videos: number;
    collection_name: string;
}

export interface ChatResponse {
    answer: string;
    sources: Array<{
        bvid: string;
        title: string;
        url: string;
    }>;
}

// ==================== 导出相关类型 ====================

export interface ExportRequest {
    folder_ids: number[];
    asr_backend: "auto" | "dashscope" | "ollama" | "whisper";
    ollama_url?: string;
    ollama_model?: string;
    ollama_language?: string;
}

export interface ExportJobStatus {
    job_id: string;
    status: "pending" | "running" | "completed" | "failed";
    progress: number;
    total_videos: number;
    processed_videos: number;
    current_video: string;
    message: string;
    created_at: string;
    completed_at?: string;
    file_count: number;
}

// ==================== API 函数 ====================

// 认证相关
export const authApi = {
    // 获取登录二维码
    getQRCode: () => request<QRCodeResponse>("/auth/qrcode"),

    // 轮询登录状态
    pollQRCode: (qrcodeKey: string) =>
        request<LoginStatusResponse>(`/auth/qrcode/poll/${qrcodeKey}`),

    // 获取会话信息
    getSession: (sessionId: string) =>
        request<{ valid: boolean; user_info: UserInfo }>(`/auth/session/${sessionId}`),

    // 退出登录
    logout: (sessionId: string) =>
        request(`/auth/session/${sessionId}`, { method: "DELETE" }),
};

// 收藏夹相关
export const favoritesApi = {
    // 获取收藏夹列表
    getList: (sessionId: string) =>
        request<FavoriteFolder[]>(`/favorites/list?session_id=${sessionId}`),

    // 获取收藏夹视频（分页）
    getVideos: (mediaId: number, sessionId: string, page = 1) =>
        request<FavoriteVideosResponse>(
            `/favorites/${mediaId}/videos?session_id=${sessionId}&page=${page}`
        ),

    // 获取收藏夹全部视频
    getAllVideos: (mediaId: number, sessionId: string) =>
        request<{ total: number; videos: Video[] }>(
            `/favorites/${mediaId}/all-videos?session_id=${sessionId}`
        ),

    // 预览整理
    organizePreview: (folderId: number, sessionId: string) =>
        request<OrganizePreviewResponse>(
            `/favorites/organize/preview?session_id=${sessionId}`,
            {
                method: "POST",
                body: JSON.stringify({ folder_id: folderId }),
            }
        ),

    // 执行整理
    organizeExecute: (
        data: {
            default_folder_id: number;
            moves: Array<{ resource_id: number; resource_type: number; target_folder_id: number }>;
        },
        sessionId: string
    ) =>
        request<{ message: string; moved: number; groups: number }>(
            `/favorites/organize/execute?session_id=${sessionId}`,
            {
                method: "POST",
                body: JSON.stringify(data),
            }
        ),

    // 清理失效内容
    cleanInvalid: (folderId: number, sessionId: string) =>
        request<{ message: string; data: Record<string, unknown> }>(
            `/favorites/organize/clean-invalid?session_id=${sessionId}`,
            {
                method: "POST",
                body: JSON.stringify({ folder_id: folderId }),
            }
        ),
};

// 知识库相关
export const knowledgeApi = {
    // 获取统计信息
    getStats: () => request<KnowledgeStats>("/knowledge/stats"),

    // 构建知识库
    build: (data: BuildRequest, sessionId: string) =>
        request<{ task_id: string; message: string }>(
            `/knowledge/build?session_id=${sessionId}`,
            {
                method: "POST",
                body: JSON.stringify(data),
            }
        ),

    // 获取构建状态
    getBuildStatus: (taskId: string) =>
        request<BuildStatus>(`/knowledge/build/status/${taskId}`),

    // 获取收藏夹入库状态
    getFolderStatus: (sessionId: string) =>
        request<FolderStatus[]>(`/knowledge/folders/status?session_id=${sessionId}`),

    // 同步收藏夹到向量库
    syncFolders: (data: SyncRequest, sessionId: string) =>
        request<SyncResult[]>(
            `/knowledge/folders/sync?session_id=${sessionId}`,
            {
                method: "POST",
                body: JSON.stringify(data),
            }
        ),

    // 清空知识库
    clear: () =>
        request<{ message: string }>("/knowledge/clear", { method: "DELETE" }),

    // 删除视频
    deleteVideo: (bvid: string) =>
        request<{ message: string }>(`/knowledge/video/${bvid}`, { method: "DELETE" }),
};

// 对话相关
export const chatApi = {
    // 提问
    ask: (question: string, sessionId?: string, folderIds?: number[]) =>
        request<ChatResponse>("/chat/ask", {
            method: "POST",
            body: JSON.stringify({ question, session_id: sessionId, folder_ids: folderIds }),
        }),

    // 搜索
    search: (query: string, k = 5) =>
        request<{ results: Array<{ bvid: string; title: string; url: string; content_preview: string }> }>(
            `/chat/search?query=${encodeURIComponent(query)}&k=${k}`,
            { method: "POST" }
        ),
};

// 导出相关
export const exportApi = {
    // 启动导出任务
    start: (data: ExportRequest, sessionId: string) =>
        request<{ job_id: string; message: string }>(
            `/export/start?session_id=${sessionId}`,
            {
                method: "POST",
                body: JSON.stringify(data),
            }
        ),

    // 查询任务状态
    getStatus: (jobId: string) =>
        request<ExportJobStatus>(`/export/status/${jobId}`),

    // 下载导出 ZIP（返回 blob URL）
    getDownloadUrl: (jobId: string) =>
        `${API_BASE_URL}/export/download/${jobId}`,

    // 列出历史任务
    listJobs: (sessionId: string) =>
        request<{ jobs: ExportJobStatus[] }>(`/export/jobs?session_id=${sessionId}`),
};

// ==================== 抖音导出 ====================

export interface DouyinQRCodeResponse {
    token: string;
    qrcode_url: string;
    qrcode_image_base64: string;
}

export interface DouyinQRPollResponse {
    status: "waiting" | "scanned" | "confirmed" | "expired";
    message: string;
    cookie_str?: string;
    session_id?: string;   // returned when confirmed; use to restore session
}

export interface DouyinSessionResponse {
    session_id: string;
    cookie_str: string;
}

export interface DouyinExportRequest {
    cookie: string;
    evil0ctal_url?: string;
    limit?: number;
    asr_backend: "auto" | "dashscope" | "ollama" | "whisper";
    ollama_url?: string;
    ollama_model?: string;
    ollama_language?: string;
}

export interface DouyinExportJobStatus {
    job_id: string;
    status: "pending" | "running" | "completed" | "failed";
    progress: number;
    total_videos: number;
    processed_videos: number;
    current_video: string;
    message: string;
    file_count: number;
    created_at: string;
    completed_at?: string;
    logs?: string[];
}

export const douyinExportApi = {
    // QR 码登录：生成二维码
    generateQrcode: () =>
        request<DouyinQRCodeResponse>("/douyin-export/qrcode"),

    // QR 码登录：轮询状态
    pollQrcode: (token: string) =>
        request<DouyinQRPollResponse>(`/douyin-export/qrcode/poll/${encodeURIComponent(token)}`),

    // 启动导出任务
    start: (data: DouyinExportRequest) =>
        request<{ job_id: string; message: string }>(
            "/douyin-export/start",
            {
                method: "POST",
                body: JSON.stringify(data),
            }
        ),

    // 查询任务状态
    getStatus: (jobId: string) =>
        request<DouyinExportJobStatus>(`/douyin-export/status/${jobId}`),

    // 下载导出 ZIP
    getDownloadUrl: (jobId: string) =>
        `${API_BASE_URL}/douyin-export/download/${jobId}`,

    // 根据 session_id 恢复已保存的 Cookie
    getSession: (sessionId: string) =>
        request<DouyinSessionResponse>(`/douyin-export/session/${sessionId}`),

    // 删除保存的 Cookie（登出）
    deleteSession: (sessionId: string) =>
        request<void>(`/douyin-export/session/${sessionId}`, { method: "DELETE" }),
};

// ==================== Instapaper 导出 ====================

export interface InstapaperFolder {
    folder_id: string;
    title: string;
}

export interface InstapaperCredentials {
    consumer_key: string;
    consumer_secret: string;
    email: string;
    password: string;
}

export interface InstapaperExportRequest {
    consumer_key: string;
    consumer_secret: string;
    email: string;
    password: string;
    folders: string[];
    limit?: number;
}

export interface InstapaperExportJobStatus {
    job_id: string;
    status: "pending" | "running" | "completed" | "failed";
    progress: number;
    total_articles: number;
    processed_articles: number;
    current_article: string;
    message: string;
    file_count: number;
    created_at: string;
    completed_at?: string;
    logs?: string[];
}

export const instapaperExportApi = {
    // 获取文件夹列表
    getFolders: (consumerKey: string, consumerSecret: string, email: string, password: string) =>
        request<{ folders: InstapaperFolder[] }>(
            `/instapaper-export/folders?consumer_key=${encodeURIComponent(consumerKey)}&consumer_secret=${encodeURIComponent(consumerSecret)}&email=${encodeURIComponent(email)}&password=${encodeURIComponent(password)}`
        ),

    // 启动导出任务
    start: (data: InstapaperExportRequest) =>
        request<{ job_id: string; message: string }>(
            "/instapaper-export/start",
            { method: "POST", body: JSON.stringify(data) }
        ),

    // 查询任务状态
    getStatus: (jobId: string) =>
        request<InstapaperExportJobStatus>(`/instapaper-export/status/${jobId}`),

    // 下载导出 ZIP
    getDownloadUrl: (jobId: string) =>
        `${API_BASE_URL}/instapaper-export/download/${jobId}`,

    // 保存凭据（首次验证成功后调用）
    saveSession: (sessionId: string, creds: InstapaperCredentials) =>
        request<InstapaperCredentials>(`/instapaper-export/session/${sessionId}`, {
            method: "POST",
            body: JSON.stringify(creds),
        }),

    // 恢复凭据（页面加载时调用）
    getSession: (sessionId: string) =>
        request<InstapaperCredentials>(`/instapaper-export/session/${sessionId}`),

    // 删除凭据（登出）
    deleteSession: (sessionId: string) =>
        request<void>(`/instapaper-export/session/${sessionId}`, { method: "DELETE" }),
};

// ==================== YouTube 知识库 ====================

export interface YoutubeSource {
    id: number;
    session_id: string;
    source_type: "video" | "playlist" | "channel" | "liked" | "watch_later";
    source_url: string;
    title?: string;
    after_date?: string;
    is_selected: boolean;
    last_sync_at?: string;
    created_at: string;
}

export interface YoutubeVideoInfo {
    video_id: string;
    title: string;
    url: string;
    description?: string;
    duration?: number;
    channel?: string;
    thumbnail?: string;
    upload_date?: string;
}

export interface YoutubeBuildStatus {
    task_id: string;
    status: "pending" | "running" | "completed" | "failed";
    progress: number;
    current_step: string;
    total_videos: number;
    processed_videos: number;
    message: string;
    logs?: string[];
}

export const youtubeApi = {
    // Cookie 管理
    saveCookie: (sessionId: string, cookieContent: string) =>
        request<{ message: string }>("/youtube/cookie", {
            method: "POST",
            body: JSON.stringify({ session_id: sessionId, cookie_content: cookieContent }),
        }),

    getCookieStatus: (sessionId: string) =>
        request<{ has_cookie: boolean }>(`/youtube/cookie/status?session_id=${sessionId}`),

    deleteCookie: (sessionId: string) =>
        request<{ message: string }>(`/youtube/cookie?session_id=${sessionId}`, {
            method: "DELETE",
        }),

    // 来源管理
    addSource: (sessionId: string, sourceUrl: string, sourceType = "auto", afterDate?: string) =>
        request<{ source_id: number; source_type: string; title?: string; message: string }>(
            "/youtube/sources",
            {
                method: "POST",
                body: JSON.stringify({ session_id: sessionId, source_url: sourceUrl, source_type: sourceType, after_date: afterDate }),
            }
        ),

    listSources: (sessionId: string) =>
        request<YoutubeSource[]>(`/youtube/sources?session_id=${sessionId}`),

    deleteSource: (sourceId: number, sessionId: string) =>
        request<{ message: string }>(`/youtube/sources/${sourceId}?session_id=${sessionId}`, {
            method: "DELETE",
        }),

    previewSourceVideos: (sourceId: number, sessionId: string) =>
        request<{ total: number; videos: YoutubeVideoInfo[] }>(
            `/youtube/sources/${sourceId}/videos?session_id=${sessionId}`
        ),

    // 知识库构建
    build: (sessionId: string, sourceIds: number[], asrBackend = "auto", limitPerSource = 0) =>
        request<YoutubeBuildStatus>("/youtube/build", {
            method: "POST",
            body: JSON.stringify({
                session_id: sessionId,
                source_ids: sourceIds,
                asr_backend: asrBackend,
                limit_per_source: limitPerSource,
            }),
        }),

    getBuildStatus: (taskId: string) =>
        request<YoutubeBuildStatus>(`/youtube/build/${taskId}`),
};

// ==================== 小宇宙知识库 ====================

export interface XiaoyuzhouSubscription {
    id: number;
    podcast_id: string;
    title: string;
    author?: string;
    rss_url?: string;
    cover_url?: string;
    is_selected: boolean;
    last_sync_at?: string;
    created_at: string;
}

export interface XiaoyuzhouEpisode {
    episode_id: string;
    title: string;
    description?: string;
    duration?: number;
    audio_url?: string;
    pub_date?: string;
}

export interface XiaoyuzhouBuildStatus {
    task_id: string;
    status: "pending" | "running" | "completed" | "failed";
    progress: number;
    current_step: string;
    total_episodes: number;
    processed_episodes: number;
    message: string;
    logs?: string[];
}

export const xiaoyuzhouApi = {
    // 认证
    sendSms: (sessionId: string, phone: string) =>
        request<{ message: string }>("/xiaoyuzhou/auth/send-sms", {
            method: "POST",
            body: JSON.stringify({ session_id: sessionId, phone }),
        }),

    login: (sessionId: string, phone: string, code: string) =>
        request<{ uid?: string; nickname?: string; message: string }>("/xiaoyuzhou/auth/login", {
            method: "POST",
            body: JSON.stringify({ session_id: sessionId, phone, code }),
        }),

    getAuthStatus: (sessionId: string) =>
        request<{ logged_in: boolean; phone?: string; nickname?: string; uid?: string }>(
            `/xiaoyuzhou/auth/status?session_id=${sessionId}`
        ),

    logout: (sessionId: string) =>
        request<{ message: string }>(`/xiaoyuzhou/auth/logout?session_id=${sessionId}`, {
            method: "DELETE",
        }),

    // 订阅管理
    syncSubscriptions: (sessionId: string) =>
        request<{ added: number; total: number; message: string }>(
            `/xiaoyuzhou/subscriptions/sync?session_id=${sessionId}`,
            { method: "POST" }
        ),

    addSubscription: (sessionId: string, rssUrl: string) =>
        request<{ subscription_id: number; podcast_id: string; title: string; message: string }>(
            "/xiaoyuzhou/subscriptions",
            {
                method: "POST",
                body: JSON.stringify({ session_id: sessionId, rss_url: rssUrl }),
            }
        ),

    listSubscriptions: (sessionId: string) =>
        request<XiaoyuzhouSubscription[]>(`/xiaoyuzhou/subscriptions?session_id=${sessionId}`),

    deleteSubscription: (subId: number, sessionId: string) =>
        request<{ message: string }>(`/xiaoyuzhou/subscriptions/${subId}?session_id=${sessionId}`, {
            method: "DELETE",
        }),

    getEpisodes: (subId: number, sessionId: string, limit = 20) =>
        request<XiaoyuzhouEpisode[]>(
            `/xiaoyuzhou/subscriptions/${subId}/episodes?session_id=${sessionId}&limit=${limit}`
        ),

    // 知识库构建
    build: (sessionId: string, subscriptionIds: number[], asrBackend = "auto", episodeLimit = 10) =>
        request<XiaoyuzhouBuildStatus>("/xiaoyuzhou/build", {
            method: "POST",
            body: JSON.stringify({
                session_id: sessionId,
                subscription_ids: subscriptionIds,
                asr_backend: asrBackend,
                episode_limit: episodeLimit,
            }),
        }),

    getBuildStatus: (taskId: string) =>
        request<XiaoyuzhouBuildStatus>(`/xiaoyuzhou/build/${taskId}`),
};

// ==================== B站 UP主创作者 ====================

export interface BiliCreator {
    id: number;
    uid: string;
    nickname?: string;
    after_date?: string;
}

export const biliCreatorApi = {
    list: (sessionId: string) =>
        request<BiliCreator[]>(`/knowledge/creators?session_id=${sessionId}`),

    add: (sessionId: string, uid: string, nickname?: string, afterDate?: string) =>
        request<BiliCreator>("/knowledge/creators?" + new URLSearchParams({ session_id: sessionId }), {
            method: "POST",
            body: JSON.stringify({ uid, nickname, after_date: afterDate }),
        }),

    delete: (sessionId: string, creatorId: number) =>
        request<{ message: string }>(`/knowledge/creators/${creatorId}?session_id=${sessionId}`, {
            method: "DELETE",
        }),

    sync: (sessionId: string) =>
        request<{ task_id: string; message: string }>(
            `/knowledge/creators/sync?session_id=${sessionId}`,
            { method: "POST" }
        ),
};

// ==================== 抖音创作者 ====================

export interface DouyinCreator {
    id: number;
    sec_uid: string;
    nickname?: string;
    after_date?: string;
}

export const douyinCreatorApi = {
    list: (sessionId: string) =>
        request<DouyinCreator[]>(`/douyin-export/creators?session_id=${sessionId}`),

    add: (sessionId: string, secUid: string, nickname?: string, afterDate?: string) =>
        request<DouyinCreator>("/douyin-export/creators?" + new URLSearchParams({ session_id: sessionId }), {
            method: "POST",
            body: JSON.stringify({ sec_uid: secUid, nickname, after_date: afterDate }),
        }),

    delete: (sessionId: string, creatorId: number) =>
        request<void>(`/douyin-export/creators/${creatorId}?session_id=${sessionId}`, {
            method: "DELETE",
        }),

    sync: (sessionId: string) =>
        request<{ task_id: string; message: string }>(
            `/douyin-export/creators/sync?session_id=${sessionId}`,
            { method: "POST" }
        ),
};

// ==================== 内容处理状态 ====================

export type ProcessingStage = "pending" | "asr_done" | "correction_done" | "completed" | "failed";
export type RetryStage = "asr" | "correction" | "summary" | "index";
export type Platform = "bilibili" | "youtube" | "xiaoyuzhou" | "douyin";

export interface ProcessingRecord {
    platform: Platform;
    content_id: string;
    title: string | null;
    stage: ProcessingStage;
    failed_stage: string | null;
    error_message: string | null;
    has_asr_raw: boolean;
    has_corrected: boolean;
    has_summary: boolean;
    created_at: string | null;
    updated_at: string | null;
}

export interface ProcessingListResponse {
    records: ProcessingRecord[];
    total: number;
}

export interface ProcessingContent {
    platform: string;
    content_id: string;
    title: string;
    stage: string;
    content: string;
    summary_block: string;
}

export const processingApi = {
    list: (params: {
        platform?: string;
        stage?: string;
        q?: string;
        limit?: number;
        offset?: number;
    } = {}) => {
        const qs = new URLSearchParams();
        if (params.platform) qs.set("platform", params.platform);
        if (params.stage) qs.set("stage", params.stage);
        if (params.q) qs.set("q", params.q);
        if (params.limit != null) qs.set("limit", String(params.limit));
        if (params.offset != null) qs.set("offset", String(params.offset));
        return request<ProcessingListResponse>(`/api/processing/list?${qs}`);
    },

    getContent: (platform: string, contentId: string) =>
        request<ProcessingContent>(`/api/processing/${encodeURIComponent(platform)}/${encodeURIComponent(contentId)}/content`),

    retry: (platform: string, contentId: string, stage: RetryStage, asrBackend?: string) =>
        request<{ status: string; message: string }>(
            `/api/processing/${encodeURIComponent(platform)}/${encodeURIComponent(contentId)}/retry`,
            {
                method: "POST",
                body: JSON.stringify({ stage, asr_backend: asrBackend ?? null }),
            }
        ),
};

export interface PrefillConfig {
    douyin_cookie: string;
    instapaper_consumer_key: string;
    instapaper_consumer_secret: string;
    instapaper_email: string;
    instapaper_password: string;
}

export const settingsApi = {
    getPrefill: () => request<PrefillConfig>("/api/settings/prefill"),
};

