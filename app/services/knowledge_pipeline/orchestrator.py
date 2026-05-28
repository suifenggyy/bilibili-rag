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

        # Exclude failed/ and done/ subdirs
        paths = [
            p for p in sorted(inbox.glob("*.md"))
            if p.is_file()
        ]
        if limit:
            paths = paths[:limit]

        logger.info(f"[Orchestrator] 开始处理 {len(paths)} 个 inbox 文件")
        return await self.process_files(paths)

    async def process_single_file(self, path: Path) -> FileProcessResult:
        """处理单个文件，执行完整流水线。"""
        start = time.monotonic()
        try:
            return await self._run_pipeline(path, start)
        except Exception as exc:
            elapsed = time.monotonic() - start
            logger.error(f"[Orchestrator] 处理失败: {path.name} - {exc}")
            return FileProcessResult(
                path=path, success=False, error=str(exc), elapsed=elapsed
            )

    # ==================== Internal pipeline ====================

    async def _run_pipeline(self, path: Path, start: float) -> FileProcessResult:
        from app.services.knowledge_pipeline.parser import KnowledgeMarkdownParser
        from app.services.knowledge_pipeline.category_map import CategoryMapRepository
        from app.services.knowledge_pipeline.archiver import KnowledgeArchiver
        from app.services.knowledge_pipeline.topic_updater import TopicUpdater
        from app.services.knowledge_pipeline.processing_logger import ProcessingLogger

        # 1. Parse
        parser = KnowledgeMarkdownParser()
        doc = parser.parse_file(path)

        # 2. Classify
        classifier = self._get_classifier()
        category_repo = CategoryMapRepository(meta_dir=self._meta_dir)
        existing_cats = category_repo.list_categories()
        classification = await classifier.classify(
            title=doc.title,
            summary=doc.summary or doc.body[:200],
            existing_categories=existing_cats,
        )

        # 3. Merge category-map
        category_repo.merge_classification(
            classification.category, classification.topics
        )
        category_repo.save()

        # 4. Archive
        archiver = KnowledgeArchiver(knowledge_dir=self._knowledge_dir)
        archive_path = archiver.archive(path, doc, classification)

        # 5. Update topics
        topic_updater = TopicUpdater(
            topics_dir=Path(self._vault_root) / "knowledge" / "_topics"
        )
        for topic in classification.topics:
            await topic_updater.update_topic(
                topic=topic,
                article_title=doc.title,
                article_date=doc.date_str,
                new_insight=classification.processing_log or doc.summary,
            )

        # 6. Log
        elapsed = time.monotonic() - start
        proc_logger = ProcessingLogger(meta_dir=self._meta_dir)
        proc_logger.log_classification(
            article_title=doc.title,
            category=classification.category,
            topics=classification.topics,
            quality_score=classification.quality_score,
            elapsed_seconds=elapsed,
        )

        logger.info(
            f"[Orchestrator] ✅ {path.name} → {classification.category} | "
            f"topics={classification.topics} | {elapsed:.1f}s"
        )
        return FileProcessResult(
            path=path,
            success=True,
            category=classification.category,
            elapsed=elapsed,
        )

    def _get_classifier(self):
        if self._classifier is not None:
            return self._classifier
        from app.services.knowledge_pipeline.classifier import KnowledgeClassifier
        return KnowledgeClassifier()
