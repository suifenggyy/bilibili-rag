"""
知识库流水线 Orchestrator。

单文件/批量文件处理总入口：
  parse → classify → merge category-map → archive → update topics → log
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from loguru import logger

# Matches a YYYY-MM-DD date segment inside an inbox path (e.g. .../douyin/2026-06-12/xxx.md).
_DATE_IN_PATH_RE = re.compile(r"(?:^|/)(\d{4}-\d{2}-\d{2})(?:/|$)")

# Body markers written by the export scripts when content fetch / ASR failed.
# A file whose body contains any of these is treated as having no usable
# content and is skipped before the LLM pipeline runs (no distill / topic work).
# Sources:
#   - article_fetcher (instapaper): "_（正文提取失败，请访问原始链接查看）_"
#   - export_favorites/douyin/youtube/xiaoyuzhou_to_md (ASR fallback): "_（未获取到有效内容）_"
_BODY_FETCH_FAILED_MARKERS: tuple[str, ...] = (
    "正文提取失败",
    "正文获取失败",
    "未获取到有效内容",
)


def _has_failed_body(doc) -> bool:
    """Return True if the parsed doc's body indicates a fetch/extraction failure.

    Export scripts write a fixed placeholder line into the body when the
    content could not be fetched (ASR failed, article extraction failed, …).
    Detecting it lets us skip these files instead of feeding placeholder text
    through the LLM pipeline.
    """
    body = doc.body or ""
    return any(marker in body for marker in _BODY_FETCH_FAILED_MARKERS)


@dataclass
class FileProcessResult:
    path: Path
    success: bool
    category: str = ""
    error: str = ""
    elapsed: float = 0.0


@dataclass
class PipelineResult:
    completed: int = 0
    failed: int = 0
    file_results: list[FileProcessResult] = field(default_factory=list)


class KnowledgePipelineOrchestrator:
    """
    知识库处理流水线 Orchestrator。

    职责（每个文件依次执行）：
    1. parse（KnowledgeMarkdownParser）
    2. distill（KnowledgeDistiller）
    3. resolve topic path（TopicPathResolver）
    4. render & write knowledge note（KnowledgeNoteStore）
    5. rebuild topic pages（TopicRebuilder）
    6. write daily log（ProcessingLogger）

    inbox 文件处理完成后保留在原位，不再移动到 done/failed 目录；是否已处理以
    source mapping 记录为准（见 process_inbox 的分界日期逻辑）。
    """

    def __init__(
        self,
        vault_root: Optional[Path] = None,
        knowledge_dir: Optional[Path] = None,
        inbox_dir: Optional[Path] = None,
        classifier=None,
        meta_dir: Optional[Path] = None,
        obsidian_writer=None,
    ):
        from app.services.content_storage import ContentStorageManager

        if vault_root is not None:
            _storage = ContentStorageManager(vault_root=str(vault_root))
        else:
            _storage = ContentStorageManager()

        self._vault_root = vault_root or _storage.get_vault_root()
        self._inbox_dir = inbox_dir or _storage.get_inbox_dir()
        self._knowledge_dir = knowledge_dir or _storage.get_knowledge_dir()
        self._meta_dir = meta_dir or _storage.get_meta_dir()

        self._classifier = classifier
        if obsidian_writer is not None:
            self._obsidian_writer = obsidian_writer
        else:
            from app.config import settings
            from app.services.knowledge_pipeline.obsidian_client import ObsidianWriter

            self._obsidian_writer = ObsidianWriter(
                vault_root=Path(self._vault_root),
                write_backend=settings.obsidian_write_backend,
            )

    # ==================== Public API ====================

    async def process_files(self, paths: list[Path]) -> PipelineResult:
        """处理指定文件列表。"""
        result = PipelineResult()
        for path in paths:
            file_result = await self.process_single_file(path)
            result.file_results.append(file_result)
            if file_result.success:
                result.completed += 1
            else:
                result.failed += 1
        return result

    async def process_inbox(self, limit: Optional[int] = None) -> PipelineResult:
        """扫描 inbox 目录并处理 pending Markdown 文件。

        处理范围由 source mapping 记录决定：
          1. 已存在于 source mapping 记录中的文件视为已处理，跳过。
          2. 按日期（路径中的 YYYY-MM-DD 段）倒序判断：找到最近一个已在
             source mapping 中出现过的日期作为分界点 cutoff_date，更早的
             日期视为已整体处理完成，跳过。
          3. 从 cutoff_date 开始按日期正序获取待处理文件。
        """
        inbox = Path(self._inbox_dir)
        if not inbox.exists():
            logger.info(f"[Orchestrator] inbox 目录不存在，跳过: {inbox}")
            return PipelineResult()

        all_paths = self._iter_inbox_markdown_files(inbox)

        processed_paths, cutoff_date = await self._load_processed_state()

        # 过滤：已处理过的文件跳过；早于分界日期的文件视为已处理完成跳过。
        pending: list[tuple[str, Path]] = []
        skipped_done = 0
        skipped_old = 0
        for path in all_paths:
            if str(path) in processed_paths:
                skipped_done += 1
                continue
            path_date = self._extract_date_from_path(str(path))
            if path_date and cutoff_date and path_date < cutoff_date:
                skipped_old += 1
                continue
            pending.append((path_date or "", path))

        # 按日期正序处理（无日期的文件排到最后）。
        pending.sort(key=lambda t: t[0])
        paths = [p for _, p in pending]

        logger.info(
            f"[Orchestrator] inbox 待处理 {len(paths)} 个"
            f"（跳过已处理 {skipped_done}、早于分界日期 {skipped_old}）"
            f"{f'，分界日期={cutoff_date}' if cutoff_date else ''}"
        )
        if limit:
            paths = paths[:limit]
        return await self.process_files(paths)

    async def process_single_file(self, path: Path) -> FileProcessResult:
        """处理单个文件，执行完整流水线。文件处理完成后保留在 inbox 原位，
        是否已处理以 source mapping 记录为准（不再移动到 done/failed 目录）。"""
        start = time.monotonic()
        try:
            result = await self._run_pipeline(path, start)
            if result.success:
                # best-effort：在 source 目录下记录逐文件进度，防止异常 kill 丢进度。
                await self._record_source_progress(path)
        except Exception as exc:
            elapsed = time.monotonic() - start
            logger.error(f"[Orchestrator] 处理失败: {path.name} - {exc}")
            result = FileProcessResult(
                path=path, success=False, error=str(exc), elapsed=elapsed
            )
        return result

    async def _record_source_progress(self, path: Path) -> None:
        """在所属 source 目录的 _pipeline_progress.log 中标记该文件已处理。

        纯可见性/冗余记录，失败仅告警、不影响流水线；不参与跳过逻辑。
        """
        try:
            from app.services.knowledge_pipeline.source_progress_logger import (
                SourceProgressLogger,
            )

            await SourceProgressLogger(inbox_root=self._inbox_dir).record_processed(
                path
            )
        except Exception as exc:
            logger.warning(
                f"[Orchestrator] 更新 source 进度日志失败: {path.name} - {exc}"
            )

    # ==================== Inbox selection ====================

    async def _load_processed_state(self) -> tuple[set[str], Optional[str]]:
        """读取 source mapping 记录，返回 (已处理的 source_inbox_path 集合, 分界日期)。

        分界日期 = 记录中出现的最近日期（路径中的 YYYY-MM-DD 段），按日期倒序
        判断时的第一个命中日期；更早的日期视为已整体处理完成。
        """
        from app.services.knowledge_pipeline.metadata_state import MetadataState

        meta_state = MetadataState(meta_dir=self._meta_dir)
        await meta_state.bootstrap()
        try:
            container = await meta_state.load_source_mapping()
        except ValueError:
            logger.warning("[Orchestrator] source mapping 不可读，按全量处理")
            return set(), None

        processed_paths: set[str] = set()
        dates: list[str] = []
        for record in container.get("items", []):
            sp = record.get("source_inbox_path")
            if sp:
                processed_paths.add(sp)
                d = self._extract_date_from_path(sp)
                if d:
                    dates.append(d)

        cutoff_date = max(dates) if dates else None
        return processed_paths, cutoff_date

    @staticmethod
    def _extract_date_from_path(path_str: str) -> Optional[str]:
        """从路径中提取 YYYY-MM-DD 日期段，找不到时返回 None。"""
        m = _DATE_IN_PATH_RE.search(path_str)
        return m.group(1) if m else None

    # ==================== Internal pipeline ====================

    async def _run_pipeline(self, path: Path, start: float) -> FileProcessResult:
        from app.services.knowledge_pipeline.parser import KnowledgeMarkdownParser
        from app.services.knowledge_pipeline.metadata_state import MetadataState
        from app.services.knowledge_pipeline.topic_graph import TopicGraph
        from app.services.knowledge_pipeline.knowledge_distiller import KnowledgeDistiller
        from app.services.knowledge_pipeline.topic_path_resolver import TopicPathResolver
        from app.services.knowledge_pipeline.knowledge_note_identity import build_knowledge_note_id
        from app.services.knowledge_pipeline.knowledge_note_renderer import KnowledgeNoteRenderer
        from app.services.knowledge_pipeline.knowledge_note_store import KnowledgeNoteStore, KnowledgeNoteFileMetadata
        from app.services.knowledge_pipeline.topic_page_renderer import TopicPageRenderer
        from app.services.knowledge_pipeline.topic_rebuilder import TopicRebuilder
        from app.services.knowledge_pipeline.processing_logger import ProcessingLogger
        from app.config import settings

        # 1. Parse
        parser = KnowledgeMarkdownParser()
        doc = parser.parse_file(path)

        # 1b. Skip files whose body indicates content fetch/extraction failed —
        # the export scripts write a fixed placeholder in this case, so there is
        # no real content to distill. Avoids wasting an LLM call on placeholder
        # text and prevents garbage notes from entering the knowledge base.
        if _has_failed_body(doc):
            elapsed = time.monotonic() - start
            logger.info(f"[Orchestrator] 跳过（正文获取失败）: {path.name}")
            return FileProcessResult(
                path=path,
                success=False,
                error="body_fetch_failed",
                elapsed=elapsed,
            )

        # 2. State & Graph Initialization
        meta_state = MetadataState(meta_dir=self._meta_dir)
        await meta_state.bootstrap()
        
        # In a real system, you'd wait on the write lock here.
        # For prototype simplicity we will just do operations inline
        # async with meta_state.write_lock():
        
        graph_snapshot = await meta_state.load_topic_graph()
        graph = TopicGraph.from_snapshot(graph_snapshot)
        
        mapping_records_container = await meta_state.load_source_mapping()
        mapping_records = mapping_records_container.get("items", [])
        
        # 3. Distillation (replaces flat Classifier)
        from app.services.knowledge_pipeline.llm_processor import DistillerProcessor, TopicPathProcessor, TopicDedupProcessor
        from app.services.knowledge_pipeline.topic_similarity import LLMTopicSimilarityChecker
        distill_processor = getattr(self, '_distill_processor', None) or DistillerProcessor()
        path_processor = getattr(self, '_path_processor', None) or TopicPathProcessor()
        dedup_processor = getattr(self, '_dedup_processor', None) or TopicDedupProcessor()
        similarity_checker = LLMTopicSimilarityChecker(processor=dedup_processor)

        distiller = KnowledgeDistiller(distill_processor)
        # Pass context
        source_identity = {
            "source_inbox_path": str(path),
            "source_url": doc.source_url or "",
            "title": doc.title,
            "published_date": doc.date_str or "1970-01-01"
        }
        units_result = await distiller.distill(doc, source_identity=source_identity)
        if units_result.status != "processed":
            return FileProcessResult(
                path=path,
                success=False,
                error=units_result.failure_reason or "skipped",
                elapsed=0.0
            )
        units = units_result.knowledge
        
        # 4. Resolve Path
        resolver = TopicPathResolver(path_processor, similarity_checker=similarity_checker)
        resolution = await resolver.resolve(units, graph)
        placement = await graph.finalize_resolution(resolution, similarity_checker=similarity_checker)
        
        # 5. Render & Write Note
        note_id = build_knowledge_note_id({
            "source_url": units.source_identity["source_url"],
            "published_date": units.source_identity["published_date"],
            "persisted_first_seen_inbox_path": str(path),
            "title": units.source_identity["title"],
        })
        
        renderer = KnowledgeNoteRenderer()
        rendered_note = renderer.render(units, note_id, placement=placement)
        
        store = KnowledgeNoteStore(
            knowledge_root=Path(self._knowledge_dir),
            graph=graph
        )
        
        # Build mapping seed
        # Assuming fingerprinter is just simple hash for now
        import hashlib
        fp = hashlib.sha256(doc.body.encode()).hexdigest()
        mapping_seed = {
            "source_inbox_path": str(path),
            "source_content_fingerprint": fp,
            "persisted_first_seen_inbox_path": str(path)
        }
        
        # See if we have an existing mapping
        # Just use flat list check for demo
        existing_mapping = next((r for r in mapping_records if r.get("source_inbox_path") == str(path)), None)
        
        write_result, processed_mapping = await store.write_note(
            knowledge_note_id=note_id,
            mapping_record=existing_mapping,
            source_mapping_seed=mapping_seed,
            placement=placement,
            note_metadata=KnowledgeNoteFileMetadata(title=doc.title, published_date=doc.date_str or "1970-01-01"),
            rendered_markdown=rendered_note
        )
        
        # Update mapping
        if existing_mapping:
            mapping_records.remove(existing_mapping)
        mapping_records.append(processed_mapping)
        mapping_records_container["items"] = mapping_records
        
        # 6. Topic Rebuild
        page_renderer = TopicPageRenderer()
        rebuilder = TopicRebuilder(
            graph=graph,
            metadata_state=meta_state, # Need to make sure it has latest mapping, but using direct mock in code
            distiller=distiller,
            store=store,
            renderer=page_renderer
        )
        
        # Hack to pass state safely
        meta_state.get_source_mapping_records = lambda: mapping_records
        
        impacted_nodes = [write_result.placement_path[-1]] if write_result.placement_path else []
        node_ids = []
        for n_path in [write_result.placement_path]:
            n = graph.get_node_by_path(n_path)
            if n:
                node_ids.append(n.id)
                
        rebuild_results = await rebuilder.rebuild_nodes(node_ids)
        for nid, rr in rebuild_results.items():
            if rr["markdown"]:
                node = graph.get_node(nid)
                topic_path = Path(self._knowledge_dir) / "_topics" / f"{node.path[-1]}.md"
                topic_path.parent.mkdir(parents=True, exist_ok=True)
                with open(topic_path, "w", encoding="utf-8") as f:
                    f.write(rr["markdown"])
        
        # Save state
        async with meta_state.write_lock():
            await meta_state.save_source_mapping(mapping_records_container)
            await meta_state.save_topic_graph(graph.to_snapshot())
        
        # 7. Log
        elapsed = time.monotonic() - start
        proc_logger = ProcessingLogger(meta_dir=self._meta_dir)
        await proc_logger.log_classification(
            article_title=doc.title,
            category=write_result.placement_path[0] if write_result.placement_path else "未知",
            topics=write_result.placement_path,
            quality_score=0.9,
            elapsed_seconds=elapsed,
            writer=self._obsidian_writer,
        )

        logger.info(
            f"[Orchestrator] ✅ {path.name} → {write_result.placement_path} | {elapsed:.1f}s"
        )
        return FileProcessResult(
            path=path,
            success=True,
            category=write_result.placement_path[0] if write_result.placement_path else "未知",
            elapsed=elapsed,
        )

    def _get_classifier(self):
        if self._classifier is not None:
            return self._classifier
        from app.services.knowledge_pipeline.classifier import KnowledgeClassifier
        return KnowledgeClassifier()

    @staticmethod
    def _iter_inbox_markdown_files(inbox: Path) -> list[Path]:
        excluded_dirs = {"done", "failed"}
        paths = []
        for path in sorted(inbox.rglob("*.md")):
            if not path.is_file():
                continue
            if any(part in excluded_dirs for part in path.parts[len(inbox.parts):]):
                continue
            paths.append(path)
        return paths
