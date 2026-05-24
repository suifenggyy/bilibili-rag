"use client";

import { useState, useEffect, useCallback } from "react";
import {
  ProcessingRecord,
  ProcessingContent,
  ProcessingStage,
  RetryStage,
  processingApi,
} from "@/lib/api";

// ==================== 常量 ====================

const PLATFORM_LABELS: Record<string, string> = {
  bilibili: "B站",
  youtube: "YouTube",
  xiaoyuzhou: "小宇宙",
  douyin: "抖音",
};

const STAGE_LABELS: Record<string, { label: string; color: string }> = {
  pending: { label: "待处理", color: "#9ca3af" },
  asr_done: { label: "已转写", color: "#3b82f6" },
  correction_done: { label: "已纠错", color: "#8b5cf6" },
  completed: { label: "已完成", color: "#10b981" },
  failed: { label: "失败", color: "#ef4444" },
};

const RETRY_STAGE_LABELS: Record<RetryStage, string> = {
  asr: "重新转写",
  correction: "重新纠错",
  summary: "重新摘要",
  index: "重建索引",
};

// ==================== 子组件 ====================

function StageBadge({ stage, failedStage }: { stage: ProcessingStage; failedStage?: string | null }) {
  const info = STAGE_LABELS[stage] ?? { label: stage, color: "#9ca3af" };
  return (
    <span
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 4,
        padding: "2px 8px",
        borderRadius: 12,
        fontSize: 12,
        fontWeight: 500,
        background: info.color + "22",
        color: info.color,
        border: `1px solid ${info.color}44`,
      }}
    >
      <span
        style={{
          width: 6,
          height: 6,
          borderRadius: "50%",
          background: info.color,
          flexShrink: 0,
        }}
      />
      {info.label}
      {stage === "failed" && failedStage && (
        <span style={{ opacity: 0.7, fontSize: 11 }}>({failedStage})</span>
      )}
    </span>
  );
}

function StageProgress({ record }: { record: ProcessingRecord }) {
  const stages: Array<{ key: string; label: string }> = [
    { key: "asr", label: "转写" },
    { key: "correction", label: "纠错" },
    { key: "summary", label: "摘要" },
    { key: "index", label: "入库" },
  ];

  const stageReached = (stageKey: string): boolean => {
    const order: Record<string, number> = {
      pending: 0,
      asr_done: 1,
      correction_done: 2,
      completed: 4,
      failed: -1,
    };
    const stageOrder: Record<string, number> = {
      asr: 1,
      correction: 2,
      summary: 3,
      index: 4,
    };
    if (record.stage === "failed") {
      const failedIdx = stageOrder[record.failed_stage ?? ""] ?? 0;
      return stageOrder[stageKey] < failedIdx;
    }
    return order[record.stage] >= stageOrder[stageKey];
  };

  const isCurrent = (stageKey: string): boolean => {
    const map: Record<string, string> = {
      asr: "asr_done",
      correction: "correction_done",
      index: "completed",
    };
    return map[stageKey] === record.stage;
  };

  return (
    <div style={{ display: "flex", alignItems: "center", gap: 2, fontSize: 11 }}>
      {stages.map((s, i) => {
        const done = stageReached(s.key);
        const current = isCurrent(s.key);
        const failed = record.stage === "failed" && record.failed_stage === s.key;
        return (
          <div key={s.key} style={{ display: "flex", alignItems: "center" }}>
            {i > 0 && (
              <div
                style={{
                  width: 12,
                  height: 1,
                  background: done ? "#10b981" : "#e5e7eb",
                  margin: "0 1px",
                }}
              />
            )}
            <span
              title={s.label}
              style={{
                display: "inline-flex",
                alignItems: "center",
                justifyContent: "center",
                width: 22,
                height: 22,
                borderRadius: "50%",
                fontSize: 10,
                fontWeight: 600,
                background: failed
                  ? "#ef444422"
                  : current
                  ? "#3b82f622"
                  : done
                  ? "#10b98122"
                  : "#f3f4f6",
                color: failed ? "#ef4444" : current ? "#3b82f6" : done ? "#10b981" : "#9ca3af",
                border: `1.5px solid ${
                  failed ? "#ef444444" : current ? "#3b82f644" : done ? "#10b98144" : "#e5e7eb"
                }`,
              }}
            >
              {failed ? "✕" : done || current ? "✓" : s.label[0]}
            </span>
          </div>
        );
      })}
    </div>
  );
}

function RetryMenu({
  record,
  onRetry,
  retrying,
}: {
  record: ProcessingRecord;
  onRetry: (stage: RetryStage) => void;
  retrying: boolean;
}) {
  const [open, setOpen] = useState(false);

  const availableStages: RetryStage[] = ["asr"];
  if (record.has_asr_raw) availableStages.push("correction");
  if (record.has_corrected) availableStages.push("summary");
  if (record.has_corrected && record.platform !== "douyin") availableStages.push("index");

  return (
    <div style={{ position: "relative", display: "inline-block" }}>
      <button
        onClick={() => setOpen((v) => !v)}
        disabled={retrying}
        style={{
          padding: "3px 10px",
          borderRadius: 6,
          fontSize: 12,
          fontWeight: 500,
          background: retrying ? "#f3f4f6" : "#eff6ff",
          color: retrying ? "#9ca3af" : "#3b82f6",
          border: "1px solid #bfdbfe",
          cursor: retrying ? "not-allowed" : "pointer",
          whiteSpace: "nowrap",
        }}
      >
        {retrying ? "重试中…" : "重试 ▾"}
      </button>
      {open && !retrying && (
        <div
          style={{
            position: "absolute",
            right: 0,
            top: "calc(100% + 4px)",
            background: "#fff",
            border: "1px solid #e5e7eb",
            borderRadius: 8,
            boxShadow: "0 4px 12px rgba(0,0,0,0.1)",
            zIndex: 100,
            minWidth: 120,
            overflow: "hidden",
          }}
        >
          {availableStages.map((s) => (
            <button
              key={s}
              onClick={() => {
                setOpen(false);
                onRetry(s);
              }}
              style={{
                display: "block",
                width: "100%",
                padding: "7px 14px",
                textAlign: "left",
                fontSize: 12,
                background: "none",
                border: "none",
                cursor: "pointer",
                color: "#374151",
              }}
              onMouseEnter={(e) => (e.currentTarget.style.background = "#f9fafb")}
              onMouseLeave={(e) => (e.currentTarget.style.background = "none")}
            >
              {RETRY_STAGE_LABELS[s]}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

function ContentModal({
  record,
  onClose,
}: {
  record: ProcessingRecord;
  onClose: () => void;
}) {
  const [content, setContent] = useState<ProcessingContent | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    processingApi
      .getContent(record.platform, record.content_id)
      .then(setContent)
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, [record.platform, record.content_id]);

  return (
    <div
      style={{
        position: "fixed",
        inset: 0,
        background: "rgba(0,0,0,0.45)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        zIndex: 1000,
      }}
      onClick={onClose}
    >
      <div
        style={{
          background: "#fff",
          borderRadius: 12,
          width: "min(800px, 92vw)",
          maxHeight: "80vh",
          display: "flex",
          flexDirection: "column",
          overflow: "hidden",
          boxShadow: "0 20px 60px rgba(0,0,0,0.2)",
        }}
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div
          style={{
            padding: "16px 20px",
            borderBottom: "1px solid #f3f4f6",
            display: "flex",
            alignItems: "flex-start",
            gap: 12,
          }}
        >
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ fontWeight: 600, fontSize: 15, marginBottom: 4, lineHeight: 1.4 }}>
              {content?.title ?? record.title ?? record.content_id}
            </div>
            <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
              <span
                style={{
                  fontSize: 11,
                  padding: "1px 7px",
                  borderRadius: 10,
                  background: "#f3f4f6",
                  color: "#6b7280",
                }}
              >
                {PLATFORM_LABELS[record.platform] ?? record.platform}
              </span>
              <StageBadge stage={record.stage} failedStage={record.failed_stage} />
            </div>
          </div>
          <button
            onClick={onClose}
            style={{
              padding: "4px 8px",
              border: "none",
              background: "none",
              cursor: "pointer",
              color: "#9ca3af",
              fontSize: 18,
              lineHeight: 1,
            }}
          >
            ✕
          </button>
        </div>

        {/* Body */}
        <div style={{ flex: 1, overflowY: "auto", padding: "16px 20px" }}>
          {loading && (
            <div style={{ textAlign: "center", color: "#9ca3af", padding: "40px 0" }}>
              加载中…
            </div>
          )}
          {error && (
            <div style={{ color: "#ef4444", padding: "20px 0", fontSize: 13 }}>
              {error}
            </div>
          )}
          {content && !loading && (
            <>
              {content.summary_block && (
                <div
                  style={{
                    background: "#f0fdf4",
                    border: "1px solid #bbf7d0",
                    borderRadius: 8,
                    padding: "12px 16px",
                    marginBottom: 16,
                    fontSize: 13,
                    color: "#166534",
                    lineHeight: 1.6,
                    whiteSpace: "pre-wrap",
                  }}
                >
                  <div style={{ fontWeight: 600, marginBottom: 6, fontSize: 12, color: "#15803d" }}>
                    📋 摘要
                  </div>
                  {content.summary_block}
                </div>
              )}
              {content.content ? (
                <pre
                  style={{
                    fontFamily: "inherit",
                    fontSize: 13,
                    lineHeight: 1.7,
                    whiteSpace: "pre-wrap",
                    color: "#374151",
                    margin: 0,
                  }}
                >
                  {content.content}
                </pre>
              ) : (
                <div style={{ color: "#9ca3af", textAlign: "center", padding: "30px 0", fontSize: 13 }}>
                  暂无文本内容
                </div>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}

// ==================== 主组件 ====================

export default function ProcessingStatusPanel() {
  const [records, setRecords] = useState<ProcessingRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Filters
  const [filterPlatform, setFilterPlatform] = useState<string>("");
  const [filterStage, setFilterStage] = useState<string>("");
  const [filterQ, setFilterQ] = useState<string>("");
  const [inputQ, setInputQ] = useState<string>("");

  // Retry state per content_id
  const [retrying, setRetrying] = useState<Record<string, boolean>>({});
  const [retryMsg, setRetryMsg] = useState<Record<string, string>>({});

  // Content modal
  const [viewRecord, setViewRecord] = useState<ProcessingRecord | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const resp = await processingApi.list({
        platform: filterPlatform || undefined,
        stage: filterStage || undefined,
        q: filterQ || undefined,
        limit: 200,
      });
      setRecords(resp.records);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "加载失败");
    } finally {
      setLoading(false);
    }
  }, [filterPlatform, filterStage, filterQ]);

  useEffect(() => {
    load();
  }, [load]);

  const handleRetry = async (record: ProcessingRecord, stage: RetryStage) => {
    const key = `${record.platform}:${record.content_id}`;
    setRetrying((prev) => ({ ...prev, [key]: true }));
    setRetryMsg((prev) => ({ ...prev, [key]: "" }));
    try {
      const res = await processingApi.retry(record.platform, record.content_id, stage);
      setRetryMsg((prev) => ({ ...prev, [key]: res.message }));
      // Reload after short delay to reflect stage reset
      setTimeout(load, 1500);
    } catch (e: unknown) {
      setRetryMsg((prev) => ({
        ...prev,
        [key]: e instanceof Error ? e.message : "重试失败",
      }));
    } finally {
      setRetrying((prev) => ({ ...prev, [key]: false }));
    }
  };

  const handleSearch = () => setFilterQ(inputQ);

  const formatDate = (iso: string | null) => {
    if (!iso) return "—";
    const d = new Date(iso);
    return d.toLocaleDateString("zh-CN", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" });
  };

  // Summary counts
  const counts = records.reduce(
    (acc, r) => {
      acc[r.stage] = (acc[r.stage] ?? 0) + 1;
      return acc;
    },
    {} as Record<string, number>
  );

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%", overflow: "hidden" }}>
      {/* Toolbar */}
      <div
        style={{
          padding: "12px 16px",
          borderBottom: "1px solid #f3f4f6",
          display: "flex",
          flexWrap: "wrap",
          gap: 8,
          alignItems: "center",
          background: "#fafafa",
        }}
      >
        {/* Search */}
        <div style={{ display: "flex", gap: 4, flex: "1 1 200px", minWidth: 0 }}>
          <input
            type="text"
            placeholder="搜索标题…"
            value={inputQ}
            onChange={(e) => setInputQ(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && handleSearch()}
            style={{
              flex: 1,
              padding: "5px 10px",
              borderRadius: 6,
              border: "1px solid #e5e7eb",
              fontSize: 13,
              outline: "none",
            }}
          />
          <button
            onClick={handleSearch}
            style={{
              padding: "5px 12px",
              borderRadius: 6,
              background: "#3b82f6",
              color: "#fff",
              border: "none",
              fontSize: 13,
              cursor: "pointer",
            }}
          >
            搜索
          </button>
        </div>

        {/* Platform filter */}
        <select
          value={filterPlatform}
          onChange={(e) => setFilterPlatform(e.target.value)}
          style={{
            padding: "5px 10px",
            borderRadius: 6,
            border: "1px solid #e5e7eb",
            fontSize: 13,
            background: "#fff",
          }}
        >
          <option value="">全部来源</option>
          {Object.entries(PLATFORM_LABELS).map(([v, l]) => (
            <option key={v} value={v}>
              {l}
            </option>
          ))}
        </select>

        {/* Stage filter */}
        <select
          value={filterStage}
          onChange={(e) => setFilterStage(e.target.value)}
          style={{
            padding: "5px 10px",
            borderRadius: 6,
            border: "1px solid #e5e7eb",
            fontSize: 13,
            background: "#fff",
          }}
        >
          <option value="">全部状态</option>
          {Object.entries(STAGE_LABELS).map(([v, { label }]) => (
            <option key={v} value={v}>
              {label}
            </option>
          ))}
        </select>

        {/* Refresh */}
        <button
          onClick={load}
          disabled={loading}
          style={{
            padding: "5px 12px",
            borderRadius: 6,
            border: "1px solid #e5e7eb",
            background: "#fff",
            fontSize: 13,
            cursor: loading ? "not-allowed" : "pointer",
            color: "#374151",
          }}
        >
          {loading ? "加载中…" : "刷新"}
        </button>

        {/* Stats */}
        <div style={{ marginLeft: "auto", display: "flex", gap: 8, fontSize: 12, color: "#6b7280" }}>
          {Object.entries(STAGE_LABELS).map(([k, { label, color }]) =>
            counts[k] ? (
              <span key={k} style={{ color }}>
                {label}: {counts[k]}
              </span>
            ) : null
          )}
        </div>
      </div>

      {/* Table area */}
      <div style={{ flex: 1, overflowY: "auto" }}>
        {error && (
          <div style={{ padding: 20, color: "#ef4444", fontSize: 13 }}>{error}</div>
        )}
        {!loading && !error && records.length === 0 && (
          <div
            style={{
              padding: 40,
              textAlign: "center",
              color: "#9ca3af",
              fontSize: 14,
            }}
          >
            暂无处理记录{filterPlatform || filterStage || filterQ ? "（尝试清除筛选条件）" : ""}
          </div>
        )}
        {records.length > 0 && (
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
            <thead>
              <tr style={{ background: "#f9fafb", borderBottom: "1px solid #f3f4f6" }}>
                {["标题", "来源", "阶段进度", "状态", "更新时间", "操作"].map((h) => (
                  <th
                    key={h}
                    style={{
                      padding: "8px 12px",
                      textAlign: "left",
                      fontWeight: 600,
                      color: "#6b7280",
                      fontSize: 12,
                      whiteSpace: "nowrap",
                      position: "sticky",
                      top: 0,
                      background: "#f9fafb",
                      zIndex: 1,
                    }}
                  >
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {records.map((rec) => {
                const key = `${rec.platform}:${rec.content_id}`;
                const isRetrying = retrying[key];
                const msg = retryMsg[key];
                return (
                  <tr
                    key={key}
                    style={{ borderBottom: "1px solid #f9fafb" }}
                    onMouseEnter={(e) =>
                      (e.currentTarget.style.background = "#fafafa")
                    }
                    onMouseLeave={(e) =>
                      (e.currentTarget.style.background = "transparent")
                    }
                  >
                    {/* Title */}
                    <td
                      style={{
                        padding: "10px 12px",
                        maxWidth: 300,
                      }}
                    >
                      <button
                        onClick={() => setViewRecord(rec)}
                        title="查看内容"
                        style={{
                          background: "none",
                          border: "none",
                          padding: 0,
                          cursor: "pointer",
                          textAlign: "left",
                          color: rec.has_corrected ? "#1d4ed8" : "#374151",
                          fontWeight: 500,
                          fontSize: 13,
                          textDecoration: rec.has_corrected ? "underline" : "none",
                          display: "-webkit-box",
                          WebkitLineClamp: 2,
                          WebkitBoxOrient: "vertical",
                          overflow: "hidden",
                          lineHeight: 1.4,
                          maxWidth: 300,
                        }}
                      >
                        {rec.title ?? rec.content_id}
                      </button>
                    </td>

                    {/* Platform */}
                    <td style={{ padding: "10px 12px", whiteSpace: "nowrap" }}>
                      <span
                        style={{
                          fontSize: 12,
                          padding: "2px 8px",
                          borderRadius: 10,
                          background: "#f3f4f6",
                          color: "#4b5563",
                        }}
                      >
                        {PLATFORM_LABELS[rec.platform] ?? rec.platform}
                      </span>
                    </td>

                    {/* Stage progress */}
                    <td style={{ padding: "10px 12px" }}>
                      <StageProgress record={rec} />
                    </td>

                    {/* Stage badge */}
                    <td style={{ padding: "10px 12px", whiteSpace: "nowrap" }}>
                      <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                        <StageBadge stage={rec.stage} failedStage={rec.failed_stage} />
                        {rec.stage === "failed" && rec.error_message && (
                          <span
                            title={rec.error_message}
                            style={{
                              fontSize: 11,
                              color: "#ef4444",
                              maxWidth: 160,
                              overflow: "hidden",
                              textOverflow: "ellipsis",
                              whiteSpace: "nowrap",
                              display: "block",
                            }}
                          >
                            {rec.error_message.slice(0, 60)}
                          </span>
                        )}
                        {msg && (
                          <span style={{ fontSize: 11, color: "#10b981" }}>{msg}</span>
                        )}
                      </div>
                    </td>

                    {/* Updated at */}
                    <td style={{ padding: "10px 12px", whiteSpace: "nowrap", color: "#9ca3af" }}>
                      {formatDate(rec.updated_at)}
                    </td>

                    {/* Actions */}
                    <td style={{ padding: "10px 12px" }}>
                      <RetryMenu
                        record={rec}
                        onRetry={(stage) => handleRetry(rec, stage)}
                        retrying={isRetrying ?? false}
                      />
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </div>

      {/* Content modal */}
      {viewRecord && (
        <ContentModal record={viewRecord} onClose={() => setViewRecord(null)} />
      )}
    </div>
  );
}
