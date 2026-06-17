"""
知识库流水线 Orchestrator。

单文件/批量文件处理总入口：
  parse → classify → merge category-map → archive → update topics → log
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from loguru import logger


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
    2. classify（KnowledgeClassifier）
    3. merge category-map（CategoryMapRepository）
    4. archive（KnowledgeArchiver）
    5. update topics（TopicUpdater）
    6. write daily log（ProcessingLogger）
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
        """扫描 inbox 目录并处理全部 pending Markdown 文件。"""
        inbox = Path(self._inbox_dir)
        if not inbox.exists():
            logger.info(f"[Orchestrator] inbox 目录不存在，跳过: {inbox}")
            return PipelineResult()

        paths = self._iter_inbox_markdown_files(inbox)
        if limit:
            paths = paths[:limit]

        logger.info(f"[Orchestrator] 开始处理 {len(paths)} 个 inbox 文件")
        return await self.process_files(paths)

    async def process_single_file(self, path: Path) -> FileProcessResult:
        """处理单个文件，执行完整流水线。处理完成后自动归档到 done/ 或 failed/。"""
        start = time.monotonic()
        try:
            result = await self._run_pipeline(path, start)
        except Exception as exc:
            elapsed = time.monotonic() - start
            logger.error(f"[Orchestrator] 处理失败: {path.name} - {exc}")
            result = FileProcessResult(
                path=path, success=False, error=str(exc), elapsed=elapsed
            )

        # Archive: 成功 → done/, 失败 → failed/
        dest_dir = "done" if result.success else "failed"
        self._archive_file(path, dest_dir)
        return result

    def _archive_file(self, path: Path, dest_dir: str) -> None:
        """将处理后的文件移动到 inbox 下的 done/ 或 failed/ 子目录。"""
        if not path.exists():
            return
        target_dir = Path(self._inbox_dir) / dest_dir
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / path.name
        # 同名文件已存在时添加序号后缀
        if target.exists():
            stem, suffix = path.stem, path.suffix
            n = 1
            while target.exists():
                target = target_dir / f"{stem}_{n}{suffix}"
                n += 1
        try:
            path.replace(target)
            logger.debug(f"[Orchestrator] 归档: {path.name} → {dest_dir}/")
        except OSError as e:
            logger.warning(f"[Orchestrator] 归档失败: {path.name} → {dest_dir}/: {e}")

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
