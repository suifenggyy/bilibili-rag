"""
知识库日报生成器。

产出：daily/YYYY-MM-DD.md
结构：
  # 知识库日报 YYYY-MM-DD
  ## 重点关注
  ## 今日新增
  ## 近期趋势
  ## 待关注信号
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

import yaml
from loguru import logger


# ==================== Data models ====================


@dataclass
class TopicSignal:
    topic: str
    score: float
    today_count: int = 0
    recent3_count: int = 0
    recent14_count: int = 0
    avg_quality: float = 0.0
    sample_titles: list[str] = field(default_factory=list)


@dataclass
class ExternalTopicSignal:
    topic: str
    headline: str
    url: str
    source: str = "tavily"


# ==================== Reporter ====================


class DailyReporter:
    """
    扫描 knowledge 目录，按日期聚合 topic 信号，生成结构化日报 Markdown。

    当 tavily_api_key 为空或为 None 时，跳过外部信号查询（适合离线/单元测试）。
    """

    def __init__(
        self,
        knowledge_dir: Path,
        daily_dir: Path,
        tavily_api_key: str = "",
        meta_dir: Optional[Path] = None,
    ):
        self._knowledge_dir = Path(knowledge_dir)
        self._daily_dir = Path(daily_dir)
        self._tavily_key = tavily_api_key

    # ==================== Public API ====================

    async def generate(self, day: Optional[date] = None) -> str:
        """生成日报字符串（不写文件）。"""
        day = day or date.today()
        internal = await self.collect_internal_topic_signals(day)
        external = await self.collect_external_topic_signals(
            [s.topic for s in internal[:5]]
        )
        todays = self._get_todays_articles(day)
        return self._render(day, internal, external, todays)

    async def generate_and_save(self, day: Optional[date] = None) -> Path:
        """生成日报并写入 daily/YYYY-MM-DD.md。"""
        day = day or date.today()
        content = await self.generate(day)
        self._daily_dir.mkdir(parents=True, exist_ok=True)
        out = self._daily_dir / f"{day.isoformat()}.md"
        out.write_text(content, encoding="utf-8")
        logger.info(f"[DailyReporter] 日报已写入: {out}")
        return out

    # ==================== Signal collection ====================

    async def collect_internal_topic_signals(self, day: date) -> list[TopicSignal]:
        """扫描 knowledge 目录，统计 topic 热度。"""
        topic_map: dict[str, TopicSignal] = {}
        today_d = day
        d3 = day - timedelta(days=3)
        d14 = day - timedelta(days=14)

        for md_file in self._knowledge_dir.rglob("*.md"):
            frontmatter = _extract_frontmatter(md_file)
            if not frontmatter:
                continue
            raw_date = frontmatter.get("date") or frontmatter.get("processed_at", "")
            if not raw_date:
                continue
            art_date = _parse_date_safe(str(raw_date))
            if art_date is None or art_date < d14:
                continue

            topics_raw = frontmatter.get("topics") or []
            if isinstance(topics_raw, str):
                topics_raw = [t.strip() for t in topics_raw.strip("[]").split(",") if t.strip()]
            quality = float(frontmatter.get("quality_score") or 0.0)
            title = str(frontmatter.get("title") or md_file.stem)

            for topic in topics_raw:
                topic = topic.strip()
                if not topic:
                    continue
                if topic not in topic_map:
                    topic_map[topic] = TopicSignal(topic=topic, score=0.0)
                sig = topic_map[topic]
                if art_date == today_d:
                    sig.today_count += 1
                if art_date >= d3:
                    sig.recent3_count += 1
                sig.recent14_count += 1
                # running average quality
                n = sig.recent14_count
                sig.avg_quality = sig.avg_quality * (n - 1) / n + quality / n
                if len(sig.sample_titles) < 3:
                    sig.sample_titles.append(title)

        # Compute composite score
        for sig in topic_map.values():
            sig.score = (
                sig.today_count * 1.0
                + sig.recent3_count * 0.7
                + sig.recent14_count * 0.3
                + sig.avg_quality * 2.0
            )

        return sorted(topic_map.values(), key=lambda s: s.score, reverse=True)

    async def collect_external_topic_signals(
        self, topics: list[str]
    ) -> list[ExternalTopicSignal]:
        """用 Tavily API 搜索外部最新资讯；无 key 时返回空列表。"""
        if not self._tavily_key or not topics:
            return []
        results: list[ExternalTopicSignal] = []
        try:
            from tavily import TavilyClient  # type: ignore

            client = TavilyClient(api_key=self._tavily_key)
            for topic in topics[:3]:  # limit API calls
                resp = client.search(
                    query=topic, max_results=2, search_depth="basic"
                )
                for item in resp.get("results", []):
                    results.append(
                        ExternalTopicSignal(
                            topic=topic,
                            headline=item.get("title", ""),
                            url=item.get("url", ""),
                        )
                    )
        except Exception as exc:
            logger.warning(f"[DailyReporter] Tavily 查询失败: {exc}")
        return results

    # ==================== Internal helpers ====================

    def _get_todays_articles(self, day: date) -> list[dict]:
        articles = []
        for md_file in self._knowledge_dir.rglob("*.md"):
            fm = _extract_frontmatter(md_file)
            if not fm:
                continue
            raw_date = fm.get("date") or fm.get("processed_at", "")
            if _parse_date_safe(str(raw_date)) == day:
                articles.append(
                    {
                        "title": str(fm.get("title") or md_file.stem),
                        "category": str(fm.get("category") or "未分类"),
                        "source": str(fm.get("source") or ""),
                        "quality_score": float(fm.get("quality_score") or 0.0),
                    }
                )
        return sorted(articles, key=lambda a: a["quality_score"], reverse=True)

    def _render(
        self,
        day: date,
        internal: list[TopicSignal],
        external: list[ExternalTopicSignal],
        todays: list[dict],
    ) -> str:
        lines = [
            f"# 知识库日报 {day.isoformat()}",
            "",
            f"> 生成时间：{day.isoformat()}  |  今日新增：{len(todays)} 篇",
            "",
        ]

        # ── 重点关注 ──
        lines += ["## 重点关注", ""]
        top = internal[:5]
        if top:
            for sig in top:
                lines.append(
                    f"- **{sig.topic}**  score={sig.score:.1f}  "
                    f"今日+{sig.today_count}  近3天+{sig.recent3_count}  "
                    f"近14天+{sig.recent14_count}  avg_quality={sig.avg_quality:.2f}"
                )
        else:
            lines.append("_暂无活跃 topic_")
        lines.append("")

        # ── 今日新增 ──
        lines += ["## 今日新增", ""]
        if todays:
            for art in todays:
                src = f" [↗]({art['source']})" if art["source"] else ""
                lines.append(
                    f"- [{art['title']}]{src}  —  {art['category']}  ★{art['quality_score']:.1f}"
                )
        else:
            lines.append("_今日无新增文章_")
        lines.append("")

        # ── 近期趋势 ──
        lines += ["## 近期趋势", ""]
        recent = [s for s in internal if s.recent3_count > 0][:8]
        if recent:
            for sig in recent:
                lines.append(f"- {sig.topic}（近3天 +{sig.recent3_count}）")
        else:
            lines.append("_近3天无新增_")
        lines.append("")

        # ── 待关注信号 ──
        lines += ["## 待关注信号", ""]
        if external:
            ext_by_topic: dict[str, list[ExternalTopicSignal]] = {}
            for e in external:
                ext_by_topic.setdefault(e.topic, []).append(e)
            for topic, items in ext_by_topic.items():
                lines.append(f"### {topic}")
                for item in items:
                    lines.append(f"- [{item.headline}]({item.url})")
                lines.append("")
        else:
            lines.append("_（未配置 Tavily API Key，跳过外部信号）_")
            lines.append("")

        return "\n".join(lines)


# ==================== Helpers ====================


def _extract_frontmatter(path: Path) -> Optional[dict]:
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        return None
    if not text.startswith("---"):
        return None
    end = text.find("\n---", 3)
    if end == -1:
        return None
    try:
        return yaml.safe_load(text[3:end]) or {}
    except Exception:
        return None


def _parse_date_safe(value: str) -> Optional[date]:
    if not value:
        return None
    # Try YYYY-MM-DD prefix
    m = re.match(r"(\d{4}-\d{2}-\d{2})", value)
    if m:
        try:
            return date.fromisoformat(m.group(1))
        except ValueError:
            pass
    return None
