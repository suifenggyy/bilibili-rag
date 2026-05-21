"""
Bilibili RAG 知识库系统

视频内容获取服务 - 二级降级策略
"""
from typing import Optional
from urllib.parse import urlparse
import asyncio
import math
import os
import shutil
import subprocess
import time
import httpx
from loguru import logger
from app.models import VideoContent, ContentSource
from app.services.bilibili import BilibiliService
from app.services.asr import ASRService
from app.services.content_storage import ContentStorageManager
from app.services.asr_temp_file_manager import ASRTempFileManager
from app.services.content_summary import ContentSummaryService
from app.services.text_postprocessor import TextPostProcessor
from app.services.text_postprocessor_factory import create_text_postprocessor


class AudioDownloadError(RuntimeError):
    """音频下载失败，且不应继续产出兜底内容。"""


class ASRProcessingError(RuntimeError):
    """ASR 处理失败，且不应继续产出兜底内容。"""


class ContentFetcher:
    """
    视频内容获取器
    
    采用二级降级策略：
    1. 音频转写（ASR）
    2. 视频基本信息 (兜底)
    """
    
    def __init__(
        self,
        bilibili_service: BilibiliService,
        asr_service: ASRService,
        text_postprocessor: Optional[TextPostProcessor] = None,
        summary_service: Optional[ContentSummaryService] = None,
        storage_manager: Optional[ContentStorageManager] = None,
    ):
        self.bili = bilibili_service
        self.asr = asr_service
        self.text_postprocessor = text_postprocessor or create_text_postprocessor()
        self.summary_service = summary_service or ContentSummaryService()
        self.storage_manager = storage_manager or ContentStorageManager()
        self.temp_file_manager = ASRTempFileManager(
            source="bilibili",
            storage_manager=self.storage_manager,
        )
        if hasattr(self.asr, "temp_file_manager"):
            self.asr.temp_file_manager.source = "bilibili"
            self.asr.temp_file_manager.storage_manager = self.storage_manager
    
    async def fetch_content(
        self,
        bvid: str,
        cid: int = None,
        title: str = None,
        fail_on_audio_download_error: bool = False,
        fail_on_asr_error: bool = False,
    ) -> VideoContent:
        """
        获取视频内容，自动降级
        
        Args:
            bvid: 视频 BV 号
            cid: 视频 cid (如果没有会自动获取)
            title: 视频标题 (如果没有会自动获取)
            
        Returns:
            VideoContent 对象
        """
        # 获取视频基本信息
        video_info = None
        if not cid or not title:
            try:
                video_info = await self.bili.get_video_info(bvid)
                if not cid:
                    cid = video_info.get("cid")
                if not title:
                    title = video_info.get("title", "未知标题")
            except Exception as e:
                logger.error(f"获取视频信息失败 [{bvid}]: {e}")
                return VideoContent(
                    bvid=bvid,
                    title=title or "未知标题",
                    content="无法获取视频信息",
                    source=ContentSource.BASIC_INFO
                )
        
        description = video_info.get("desc", "") if video_info else ""
        
        # Level 1: 跳过 AI 摘要，优先使用 ASR
        logger.info(f"[{bvid}] 已跳过 AI 摘要，优先使用 ASR")

        asr_text = await self._try_asr(
            bvid,
            cid,
            title=title,
            fail_on_audio_download_error=fail_on_audio_download_error,
            fail_on_asr_error=fail_on_asr_error,
        )
        if asr_text:
            self._persist_text_artifact(title, "asr_raw.txt", asr_text)
            asr_text = await self._postprocess_asr_text(bvid, asr_text)
            self._persist_text_artifact(title, "asr_corrected.txt", asr_text)
            summary_block = await self._summarize_content(bvid, asr_text)
            logger.info(f"[{bvid}] 使用 ASR 文本")
            return VideoContent(
                bvid=bvid,
                title=title,
                content=asr_text,
                source=ContentSource.ASR,
                summary_block=summary_block,
            )
        
        # ASR 失败时，补齐基础信息（避免遗漏简介）
        if not video_info:
            try:
                video_info = await self.bili.get_video_info(bvid)
            except Exception as e:
                logger.debug(f"[{bvid}] 获取视频信息失败(兜底): {e}")

        if video_info and not description:
            description = video_info.get("desc", "") or description

        # Level 3: 使用基本信息兜底
        logger.info(f"[{bvid}] 使用基本信息")
        basic_content = f"视频标题：{title}"
        if description:
            basic_content += f"\n\n视频简介：{description}"
        
        return VideoContent(
            bvid=bvid,
            title=title,
            content=basic_content,
            source=ContentSource.BASIC_INFO
        )

    async def _try_asr(
        self,
        bvid: str,
        cid: int,
        title: Optional[str] = None,
        fail_on_audio_download_error: bool = False,
        fail_on_asr_error: bool = False,
    ) -> Optional[str]:
        """尝试进行音频转写"""
        try:
            audio_url = await self.bili.get_audio_url(bvid, cid)
            if not audio_url:
                logger.info(f"[{bvid}] 未获取到音频 URL")
                return None
            status = await self._probe_audio_url(bvid, audio_url)
            if status is not None and status < 400:
                logger.info(f"[{bvid}] 音频 URL 可达，使用 Transcription")
                await self._persist_audio_copy(bvid, audio_url, title=title or bvid)
                text = await self.asr.transcribe_url(audio_url, title=title or bvid)
            else:
                logger.info(f"[{bvid}] 音频 URL 不可达，使用 Recognition 兜底")
                text = await self._try_asr_with_local_audio(
                    bvid,
                    cid,
                    audio_url,
                    title=title,
                    fail_on_audio_download_error=fail_on_audio_download_error,
                )

            if not text or len(text) < 50:
                if fail_on_asr_error:
                    raise ASRProcessingError(f"[{bvid}] ASR 失败或内容过少")
                logger.info(f"[{bvid}] ASR 内容过少")
                return None
            preview = text[:120].replace("\n", " ").strip()
            logger.info(f"[{bvid}] ASR 成功，长度={len(text)}，预览：{preview}")
            return text
        except AudioDownloadError:
            raise
        except ASRProcessingError:
            raise
        except Exception as e:
            if fail_on_asr_error:
                raise ASRProcessingError(f"[{bvid}] ASR 失败: {e}") from e
            logger.warning(f"[{bvid}] ASR 失败: {e}")
            return None

    async def _postprocess_asr_text(self, bvid: str, text: str) -> str:
        processor = getattr(self, "text_postprocessor", None)
        raw_text = (text or "").strip()
        if not processor or not raw_text:
            return raw_text

        try:
            processed_text = await processor.postprocess(raw_text)
        except Exception as e:
            logger.warning(f"[{bvid}] ASR 文本后处理失败，回退原始文本: {e}")
            return raw_text

        normalized = (processed_text or "").strip()
        if not normalized:
            logger.warning(f"[{bvid}] ASR 文本后处理为空，回退原始文本")
            return raw_text
        return normalized

    async def _summarize_content(self, bvid: str, text: str) -> str:
        summary_service = getattr(self, "summary_service", None)
        cleaned = (text or "").strip()
        if not summary_service or not cleaned:
            return ""
        try:
            return await summary_service.summarize(cleaned)
        except Exception as e:
            logger.warning(f"[{bvid}] 内容总结失败，跳过总结块: {e}")
            return ""

    async def _probe_audio_url(self, bvid: str, audio_url: str) -> Optional[int]:
        """探测音频 URL 可达性（不带 Cookie，模拟 ASR 服务拉取）"""
        try:
            parsed = urlparse(audio_url)
            safe_url = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
        except Exception:
            safe_url = "unknown"

        timeout = httpx.Timeout(10.0, connect=5.0)
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            status = None
            try:
                head = await client.head(audio_url)
                status = head.status_code
            except Exception as e:
                logger.info(f"[{bvid}] 音频 URL HEAD 失败: {e}")

            if status is None or status >= 400:
                try:
                    headers = {"Range": "bytes=0-0"}
                    get = await client.get(audio_url, headers=headers)
                    status = get.status_code
                except Exception as e:
                    logger.info(f"[{bvid}] 音频 URL GET 失败: {e}")

        if status is None:
            logger.info(f"[{bvid}] 音频 URL 不可达: {safe_url}")
        else:
            logger.info(f"[{bvid}] 音频 URL 可达性: {status} - {safe_url}")
        return status

    async def _try_asr_with_local_audio(
        self,
        bvid: str,
        cid: int,
        audio_url: str,
        title: Optional[str] = None,
        fail_on_audio_download_error: bool = False,
    ) -> Optional[str]:
        """本地下载后使用 Recognition 直传"""
        readable_title = title or bvid
        yt_dlp_path = self.temp_file_manager.build_path(readable_title, ".mp3", prefix="audio")

        try:
            parsed = urlparse(audio_url)
            ext = os.path.splitext(parsed.path)[1] or ".m4s"
        except Exception:
            ext = ".m4s"

        fallback_path = self.temp_file_manager.build_path(readable_title, ext, prefix="audio")
        file_path = yt_dlp_path

        yt_dlp_error = None
        try:
            await self.bili.download_bvid_audio_to_file(
                bvid,
                cid,
                yt_dlp_path,
                raise_on_error=True,
            )
        except Exception as e:
            yt_dlp_error = str(e)
            logger.info(f"[{bvid}] yt-dlp 页面下载失败，回退音频直链下载")
            try:
                await self.bili.download_audio_to_file(
                    audio_url,
                    fallback_path,
                    raise_on_error=True,
                )
                file_path = fallback_path
            except Exception as direct_error:
                message = (
                    f"[{bvid}] 音频下载失败；"
                    f" yt-dlp={yt_dlp_error};"
                    f" direct={direct_error}"
                )
                if fail_on_audio_download_error:
                    raise AudioDownloadError(message) from direct_error
                logger.warning(message)
                return None

        if os.path.exists(file_path) and os.path.getsize(file_path) < 1024:
            message = f"[{bvid}] 本地音频文件过小，跳过上传: {file_path}"
            logger.info(message)
            if fail_on_audio_download_error:
                raise AudioDownloadError(message)
            return None

        self.temp_file_manager.cleanup_if_needed()

        text = await self.asr.transcribe_local_file(file_path, title=readable_title)
        if text:
            preview = text[:120].replace("\n", " ").strip()
            logger.info(f"[{bvid}] Recognition ASR 成功，长度={len(text)}，预览：{preview}")
        return text

    async def _persist_audio_copy(self, bvid: str, audio_url: str, title: str) -> None:
        try:
            parsed = urlparse(audio_url)
            ext = os.path.splitext(parsed.path)[1] or ".m4s"
        except Exception:
            ext = ".m4s"

        file_path = self.storage_manager.build_work_file_path("bilibili", title, f"audio{ext}")
        if file_path.exists():
            return

        try:
            await self.bili.download_audio_to_file(audio_url, str(file_path), raise_on_error=True)
            self.storage_manager.cleanup_workspace_if_needed()
        except Exception as exc:
            logger.info(f"[{bvid}] 额外保存音频副本失败，继续 ASR: {exc}")

    def _persist_text_artifact(self, title: str, filename: str, text: str) -> None:
        if not (text or "").strip():
            return
        self.storage_manager.write_work_text("bilibili", title, filename, text.strip())

    def _transcode_audio_to_wav(self, bvid: str, file_path: str) -> Optional[str]:
        """使用 ffmpeg 转码为 16k 单声道 wav，提高 ASR 兼容性"""
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            logger.info(f"[{bvid}] 未检测到 ffmpeg，跳过转码")
            return None

        base, _ext = os.path.splitext(file_path)
        wav_path = base + ".wav"

        cmd = [
            ffmpeg,
            "-y",
            "-i", file_path,
            "-ac", "1",
            "-ar", "16000",
            "-vn",
            wav_path,
        ]
        try:
            result = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            if result.returncode != 0:
                err = (result.stderr or "").strip()
                logger.info(f"[{bvid}] ffmpeg 转码失败: {err[:300]}")
                return None
        except Exception as e:
            logger.info(f"[{bvid}] ffmpeg 转码异常: {e}")
            return None

        if not os.path.exists(wav_path) or os.path.getsize(wav_path) < 1024:
            logger.info(f"[{bvid}] 转码输出过小，跳过使用 wav")
            return None

        logger.info(f"[{bvid}] 转码完成，使用 wav 上传: {wav_path}")
        return wav_path

    def _get_audio_duration_sec(self, file_path: str) -> Optional[float]:
        ffprobe = shutil.which("ffprobe")
        if not ffprobe:
            return None
        cmd = [
            ffprobe,
            "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            file_path,
        ]
        try:
            result = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            if result.returncode != 0:
                return None
            value = (result.stdout or "").strip()
            return float(value) if value else None
        except Exception:
            return None

    def _split_audio_wav(self, bvid: str, wav_path: str, segment_seconds: int = 1200) -> list[str]:
        """将较长 wav 切分为多段，提升 ASR 成功率"""
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            logger.info(f"[{bvid}] 未检测到 ffmpeg，跳过切分")
            return [wav_path]

        duration = self._get_audio_duration_sec(wav_path)
        if not duration or duration <= segment_seconds:
            return [wav_path]

        total_segments = int(math.ceil(duration / segment_seconds))
        logger.info(f"[{bvid}] 音频较长({duration:.1f}s)，切分为 {total_segments} 段")

        base, _ext = os.path.splitext(wav_path)
        segment_paths: list[str] = []
        for idx in range(total_segments):
            start = idx * segment_seconds
            out_path = f"{base}_part{idx+1:03d}.wav"
            cmd = [
                ffmpeg,
                "-y",
                "-i", wav_path,
                "-ss", str(start),
                "-t", str(segment_seconds),
                "-ac", "1",
                "-ar", "16000",
                "-vn",
                out_path,
            ]
            try:
                result = subprocess.run(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
                if result.returncode != 0:
                    err = (result.stderr or "").strip()
                    logger.info(f"[{bvid}] 切分失败: {err[:300]}")
                    continue
            except Exception as e:
                logger.info(f"[{bvid}] 切分异常: {e}")
                continue

            if os.path.exists(out_path) and os.path.getsize(out_path) >= 1024:
                segment_paths.append(out_path)

        if not segment_paths:
            return [wav_path]

        try:
            os.remove(wav_path)
        except Exception:
            logger.debug(f"[{bvid}] 清理原始 wav 失败: {wav_path}")

        return segment_paths

    async def _try_ai_summary(
        self, 
        bvid: str, 
        cid: int, 
        up_mid: int = None
    ) -> Optional[dict]:
        """尝试获取 AI 摘要"""
        try:
            result = await self.bili.get_video_summary(bvid, cid, up_mid)
            
            if not result:
                return None
            
            # 检查是否有有效摘要
            inner_code = result.get("code", -1)
            if inner_code != 0:
                logger.debug(f"[{bvid}] AI 摘要不可用: code={inner_code}")
                return None
            
            model_result = result.get("model_result", {})
            summary = model_result.get("summary", "")
            
            if not summary:
                logger.debug(f"[{bvid}] AI 摘要为空")
                return None
            
            # 解析分段提纲
            outline = []
            for item in model_result.get("outline", []):
                outline_item = {
                    "title": item.get("title", ""),
                    "timestamp": item.get("timestamp", 0),
                    "points": []
                }
                for point in item.get("part_outline", []):
                    outline_item["points"].append({
                        "content": point.get("content", ""),
                        "timestamp": point.get("timestamp", 0)
                    })
                outline.append(outline_item)
            
            return {
                "summary": summary,
                "outline": outline
            }
            
        except Exception as e:
            logger.warning(f"[{bvid}] 获取 AI 摘要失败: {e}")
            return None
    
    async def _try_subtitle(self, bvid: str, cid: int, video_info: Optional[dict] = None) -> Optional[str]:
        """尝试获取字幕"""
        try:
            def pick_subtitle(subtitles: list) -> Optional[dict]:
                """优先选中文且人工字幕，没有就回退到中文自动字幕"""
                if not subtitles:
                    return None
                def is_zh(sub):
                    lan = sub.get("lan", "") or ""
                    return "zh" in lan.lower() or "cn" in lan.lower()
                for sub in subtitles:
                    if is_zh(sub) and str(sub.get("ai_status", "0")) == "0":
                        return sub
                for sub in subtitles:
                    if is_zh(sub):
                        return sub
                return subtitles[0]

            def extract_subtitles(data: dict) -> list:
                subtitle_block = (data or {}).get("subtitle", {}) or {}
                return subtitle_block.get("subtitles") or subtitle_block.get("list") or []

            def extract_url(sub: dict) -> str:
                return sub.get("subtitle_url") or sub.get("url") or ""

            cookies = self.bili._get_cookies()
            has_login = bool(cookies.get("SESSDATA"))

            # 第一次尝试：播放器接口
            aid = video_info.get("aid") if video_info else None
            player_info = await self.bili.get_player_info(bvid, cid, aid=aid)

            subtitles = extract_subtitles(player_info or {})
            if subtitles:
                selected_subtitle = pick_subtitle(subtitles)
                subtitle_url = extract_url(selected_subtitle or {})
                if subtitle_url:
                    subtitle_text = await self.bili.download_subtitle(subtitle_url)
                    if subtitle_text and len(subtitle_text) >= 50:
                        preview = subtitle_text[:120].replace("\n", " ").strip()
                        logger.info(f"[{bvid}] 字幕获取成功，长度={len(subtitle_text)}，预览：{preview}")
                        return subtitle_text
                    logger.info(f"[{bvid}] 字幕内容过少，已忽略")
                else:
                    logger.info(f"[{bvid}] 字幕地址为空，无法下载")
            else:
                logger.info(f"[{bvid}] 播放器字幕为空（登录态={'已设置' if has_login else '未设置'}）")

            # 如果没有 video_info，尝试获取以补齐 aid 与字幕列表
            if not video_info:
                try:
                    video_info = await self.bili.get_video_info(bvid)
                except Exception as e:
                    logger.debug(f"[{bvid}] 获取视频信息失败(字幕兜底): {e}")
                    video_info = None

            # 第二次尝试：带 aid 再取一次播放器字幕
            if video_info and not aid:
                aid = video_info.get("aid")
                if aid:
                    player_info = await self.bili.get_player_info(bvid, cid, aid=aid)
                    subtitles = extract_subtitles(player_info or {})
                    if subtitles:
                        selected_subtitle = pick_subtitle(subtitles)
                        subtitle_url = extract_url(selected_subtitle or {})
                        if subtitle_url:
                            subtitle_text = await self.bili.download_subtitle(subtitle_url)
                            if subtitle_text and len(subtitle_text) >= 50:
                                preview = subtitle_text[:120].replace("\n", " ").strip()
                                logger.info(f"[{bvid}] 字幕获取成功(补aid)，长度={len(subtitle_text)}，预览：{preview}")
                                return subtitle_text
                            logger.info(f"[{bvid}] 字幕内容过少，已忽略")
                        else:
                            logger.info(f"[{bvid}] 字幕地址为空，无法下载")
                    else:
                        logger.info(f"[{bvid}] 播放器字幕仍为空（aid 已补齐）")

            # 最后兜底：从 view 接口的字幕列表里取
            view_subtitles = (video_info or {}).get("subtitle", {}).get("list") or []
            if view_subtitles:
                selected_subtitle = pick_subtitle(view_subtitles)
                subtitle_url = extract_url(selected_subtitle or {})
                if subtitle_url:
                    subtitle_text = await self.bili.download_subtitle(subtitle_url)
                    if subtitle_text and len(subtitle_text) >= 50:
                        preview = subtitle_text[:120].replace("\n", " ").strip()
                        logger.info(f"[{bvid}] 字幕获取成功(view兜底)，长度={len(subtitle_text)}，预览：{preview}")
                        return subtitle_text
                    logger.info(f"[{bvid}] 字幕内容过少，已忽略")
                else:
                    logger.info(f"[{bvid}] 字幕地址为空，无法下载")
            else:
                logger.info(f"[{bvid}] view 字幕列表为空，无法兜底")

            logger.info(f"[{bvid}] 没有可用字幕，回退到简介兜底")
            return None
            
        except Exception as e:
            logger.warning(f"[{bvid}] 获取字幕失败: {e}")
            return None
    
    async def fetch_all_videos_content(
        self, 
        videos: list, 
        progress_callback=None
    ) -> list[VideoContent]:
        """
        批量获取视频内容
        
        Args:
            videos: 视频列表，每个元素需包含 bvid, title (可选 cid)
            progress_callback: 进度回调函数 callback(current, total, video_title)
            
        Returns:
            VideoContent 列表
        """
        import asyncio
        
        results = []
        total = len(videos)
        
        for i, video in enumerate(videos):
            bvid = video.get("bvid") or video.get("bv_id")
            title = video.get("title", "")
            cid = video.get("cid") or video.get("id")
            
            if not bvid:
                logger.warning(f"跳过无效视频: {video}")
                continue
            
            try:
                content = await self.fetch_content(bvid, cid, title)
                results.append(content)
                
                if progress_callback:
                    progress_callback(i + 1, total, title)
                    
            except Exception as e:
                logger.error(f"处理视频失败 [{bvid}]: {e}")
                results.append(VideoContent(
                    bvid=bvid,
                    title=title or bvid,
                    content=f"处理失败: {str(e)}",
                    source=ContentSource.BASIC_INFO
                ))
            
            # 控制请求速率
            await asyncio.sleep(0.5)
        
        return results
