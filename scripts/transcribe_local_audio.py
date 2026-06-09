"""
本地音频转写工具

对本地音频文件执行「ASR 转写 → 文本纠错 → 内容总结」完整流程，
输出 Markdown 文件（默认保存在 data/collection/ 目录）。

支持的 ASR 后端（与 export_xiaoyuzhou_to_md.py 相同）：
  whisper     本地 openai-whisper（默认，读取 .env WHISPER_MODEL）
  dashscope   阿里云 DashScope paraformer-v2（需要 DASHSCOPE_API_KEY）
  ollama      本地 Ollama Whisper HTTP API

用法：
    # 使用默认 ASR 后端（.env 中的 ASR_BACKEND）
    python scripts/transcribe_local_audio.py audio.mp3

    # 指定标题（默认取文件名）
    python scripts/transcribe_local_audio.py audio.mp3 --title "我的播客"

    # 指定 ASR 后端
    python scripts/transcribe_local_audio.py audio.mp3 --asr-backend dashscope

    # 指定输出目录
    python scripts/transcribe_local_audio.py audio.mp3 --output-dir /tmp/out

    # 跳过纠错或总结
    python scripts/transcribe_local_audio.py audio.mp3 --no-correction
    python scripts/transcribe_local_audio.py audio.mp3 --no-summary

    # 直接输出到终端，不写文件
    python scripts/transcribe_local_audio.py audio.mp3 --stdout
"""

import argparse
import asyncio
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

from dotenv import load_dotenv
from loguru import logger

load_dotenv(ROOT_DIR / ".env")


def _get_env(key: str, default: str = "") -> str:
    return os.environ.get(key, default).strip()


DEFAULT_OUTPUT_DIR = _get_env(
    "COLLECTION_OUTPUT_DIR",
    str(ROOT_DIR / "data" / "collection"),
)


def _safe_filename(name: str, max_len: int = 80) -> str:
    name = re.sub(r'[\\/:*?"<>|]', "_", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name[:max_len] if len(name) > max_len else name


def _to_wav(src: str, dst: str) -> bool:
    """使用 ffmpeg 将音频转为 16kHz 单声道 WAV。"""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        logger.info("未检测到 ffmpeg，跳过音频转换，直接使用原始文件")
        return False

    cmd = [
        ffmpeg, "-y",
        "-i", src,
        "-vn",
        "-ac", "1",
        "-ar", "16000",
        "-acodec", "pcm_s16le",
        dst,
    ]
    try:
        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=600,
        )
        if result.returncode != 0:
            err = (result.stderr or "").strip()[-300:]
            logger.warning(f"ffmpeg 转码失败: {err}")
            return False
        size = os.path.getsize(dst) if os.path.exists(dst) else 0
        if size < 1024:
            logger.warning("ffmpeg 输出文件过小，可能转码失败")
            if os.path.exists(dst):
                os.remove(dst)
            return False
        return True
    except subprocess.TimeoutExpired:
        logger.warning("ffmpeg 超时")
        if os.path.exists(dst):
            os.remove(dst)
        return False
    except Exception as e:
        logger.warning(f"ffmpeg 异常: {e}")
        return False


async def _build_asr_service(backend: str, api_key: str, ollama_url: str, ollama_model: str, ollama_language: str):
    """根据参数构建 ASR 服务。"""
    resolved = backend.strip().lower()
    if resolved == "auto":
        resolved = _get_env("ASR_BACKEND", "whisper").strip().lower() or "whisper"
        if resolved == "auto":
            resolved = "whisper"
        print(f"ℹ️  自动模式，使用 .env ASR_BACKEND={resolved}")

    if resolved == "dashscope":
        if not api_key:
            print("❌ 未配置 DASHSCOPE_API_KEY，无法使用 DashScope ASR")
            sys.exit(1)
        from app.services.asr import ASRService
        print(f"🔊 ASR 后端：DashScope（{_get_env('ASR_MODEL', 'paraformer-v2')}）")
        return ASRService(api_key=api_key)

    if resolved == "whisper":
        from app.services.asr_whisper import OpenAIWhisperASRService
        asr = OpenAIWhisperASRService(
            model=_get_env("WHISPER_MODEL", "turbo"),
            language=_get_env("WHISPER_LANGUAGE", "zh"),
            timeout=int(_get_env("ASR_TIMEOUT", "600")),
        )
        print(f"🔊 ASR 后端：openai-whisper 本地（模型：{asr.model_name}）")
        return asr

    # ollama
    from app.services.asr_local import OllamaASRService
    asr = OllamaASRService(
        base_url=ollama_url,
        model=ollama_model,
        language=ollama_language,
        timeout=int(_get_env("ASR_TIMEOUT", "600")),
    )
    print(f"🔊 ASR 后端：Ollama（{asr.base_url}，模型：{asr.model}）")
    if not asr.check_ollama_available():
        print(f"❌ 无法连接到 Ollama 服务（{asr.base_url}），请确认 Ollama 已启动")
        sys.exit(1)
    if not asr.check_model_available():
        print(f"⚠️  Ollama 中未找到模型 '{asr.model}'，请先运行：ollama pull {asr.model}")
        sys.exit(1)
    print(f"   ✅ Ollama 服务正常，模型 '{asr.model}' 已就绪")
    return asr


def _build_markdown(
    title: str,
    audio_path: str,
    asr_raw: str,
    corrected: str,
    summary_block: str,
    content_source: str,
) -> str:
    """构建最终 Markdown 内容。"""
    source_label = {
        "asr": "ASR 语音转写（已纠错）",
        "asr_raw": "ASR 语音转写（未纠错）",
    }.get(content_source, content_source)

    lines = [
        f"# {title}",
        "",
        "## 文件信息",
        "",
        "| 字段 | 内容 |",
        "|------|------|",
        f"| 文件 | `{audio_path}` |",
        f"| 内容来源 | {source_label} |",
        f"| 导出时间 | {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} |",
    ]

    if summary_block:
        from app.services.content_summary import append_summary_section
        append_summary_section(lines, summary_block)

    display_content = corrected or asr_raw
    lines += ["", "---", "", "## 转写内容", ""]
    if display_content and display_content.strip():
        lines.append(display_content.strip())
    else:
        lines.append("_（未获取到有效内容）_")

    lines += ["", "---", ""]
    return "\n".join(lines)


async def main() -> None:
    parser = argparse.ArgumentParser(
        description="本地音频 ASR 转写 → 纠错 → 总结 → Markdown",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("audio", help="本地音频文件路径（支持 mp3/wav/m4a/flac 等 ffmpeg 可处理格式）")
    parser.add_argument("--title", default="", help="输出标题（默认取音频文件名）")
    parser.add_argument(
        "--output-dir",
        default=DEFAULT_OUTPUT_DIR,
        help=f"Markdown 输出目录（默认: {DEFAULT_OUTPUT_DIR}）",
    )
    parser.add_argument("--stdout", action="store_true", help="将结果输出到终端，不写文件")
    parser.add_argument(
        "--asr-backend",
        default=_get_env("ASR_BACKEND", "auto"),
        choices=["auto", "dashscope", "ollama", "whisper"],
        help="ASR 转写后端（auto 时读取 .env ASR_BACKEND）",
    )
    parser.add_argument("--api-key", default=_get_env("DASHSCOPE_API_KEY"), help="DashScope API Key")
    parser.add_argument(
        "--ollama-url",
        default=_get_env("OLLAMA_BASE_URL", "http://localhost:11434"),
        help="Ollama 服务地址",
    )
    parser.add_argument(
        "--ollama-model",
        default=_get_env("OLLAMA_ASR_MODEL", "whisper"),
        help="Ollama ASR 模型名",
    )
    parser.add_argument(
        "--ollama-language",
        default=_get_env("OLLAMA_ASR_LANGUAGE", "zh"),
        help="转写语言（ollama 后端）",
    )
    parser.add_argument("--no-correction", action="store_true", help="跳过 ASR 文本纠错步骤")
    parser.add_argument("--no-summary", action="store_true", help="跳过 AI 内容总结步骤")
    args = parser.parse_args()

    # ── 检查输入文件 ────────────────────────────────────────────────────────
    audio_path = Path(args.audio).resolve()
    if not audio_path.exists():
        print(f"❌ 文件不存在：{audio_path}")
        sys.exit(1)
    if not audio_path.is_file():
        print(f"❌ 不是文件：{audio_path}")
        sys.exit(1)

    title = args.title or audio_path.stem
    print(f"\n🎵 音频文件：{audio_path}")
    print(f"📝 标题：{title}")

    # ── 构建 ASR 服务 ───────────────────────────────────────────────────────
    asr = await _build_asr_service(
        backend=args.asr_backend,
        api_key=args.api_key,
        ollama_url=args.ollama_url,
        ollama_model=args.ollama_model,
        ollama_language=args.ollama_language,
    )

    # ── 准备临时目录 ────────────────────────────────────────────────────────
    tmp_dir = ROOT_DIR / "data" / "local_audio_tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    safe_stem = _safe_filename(title)
    wav_path = str(tmp_dir / f"{safe_stem}.wav")

    # ── 音频转换 ────────────────────────────────────────────────────────────
    src = str(audio_path)
    if audio_path.suffix.lower() != ".wav":
        print("🔄 转换音频格式（ffmpeg → 16kHz WAV）...", end="", flush=True)
        converted = _to_wav(src, wav_path)
        if converted:
            print(" ✅")
            transcribe_path = wav_path
        else:
            print(" ⚠️  转换失败，使用原始文件")
            transcribe_path = src
    else:
        transcribe_path = src

    # ── ASR 转写 ────────────────────────────────────────────────────────────
    print("🔊 ASR 转写中...", end="", flush=True)
    try:
        asr_raw = await asr.transcribe_local_file(transcribe_path, title=title)
        asr_raw = (asr_raw or "").strip()
        if asr_raw:
            print(f" ✅（{len(asr_raw)} 字）")
        else:
            print(" ⚠️  转写结果为空")
    except Exception as e:
        print(f" ❌ 转写失败：{e}")
        logger.exception("ASR 转写异常")
        sys.exit(1)
    finally:
        # 清理临时 WAV
        if transcribe_path == wav_path and os.path.exists(wav_path):
            try:
                os.remove(wav_path)
            except Exception:
                pass

    if not asr_raw:
        print("❌ ASR 未产生任何文本，退出")
        sys.exit(1)

    # ── 文本纠错 ────────────────────────────────────────────────────────────
    corrected = ""
    if not args.no_correction:
        print("✏️  文本纠错中...", end="", flush=True)
        try:
            from app.services.text_postprocessor_factory import create_text_postprocessor
            processor = create_text_postprocessor()
            corrected = await processor.postprocess(asr_raw, title=title)
            corrected = (corrected or "").strip() or asr_raw
            print(f" ✅（{len(corrected)} 字）")
        except Exception as e:
            print(f" ⚠️  纠错失败，使用原始文本：{e}")
            logger.warning(f"文本纠错异常: {e}")
            corrected = asr_raw
    else:
        print("⏭️  跳过文本纠错")
        corrected = asr_raw

    # ── 内容总结 ────────────────────────────────────────────────────────────
    summary_block = ""
    if not args.no_summary:
        print("📋 AI 总结中...", end="", flush=True)
        try:
            from app.services.content_summary import ContentSummaryService
            summary_svc = ContentSummaryService()
            summary_block = await summary_svc.summarize(corrected)
            if summary_block:
                print(" ✅")
            else:
                print(" ⚠️  总结结果为空")
        except Exception as e:
            print(f" ⚠️  总结失败，跳过：{e}")
            logger.warning(f"内容总结异常: {e}")
    else:
        print("⏭️  跳过内容总结")

    # ── 构建 Markdown ───────────────────────────────────────────────────────
    content_source = "asr" if not args.no_correction else "asr_raw"
    md_content = _build_markdown(
        title=title,
        audio_path=str(audio_path),
        asr_raw=asr_raw,
        corrected=corrected,
        summary_block=summary_block,
        content_source=content_source,
    )

    # ── 输出结果 ────────────────────────────────────────────────────────────
    if args.stdout:
        print("\n" + "=" * 60)
        print(md_content)
        return

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    md_filename = f"{_safe_filename(title)}.md"
    md_path = output_dir / md_filename
    md_path.write_text(md_content, encoding="utf-8")
    print(f"\n✅ 完成！已保存到：{md_path}")


if __name__ == "__main__":
    asyncio.run(main())
