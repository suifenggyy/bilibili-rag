"""
ChatCompletionPostProcessor — adapts LLMService to the TextPostProcessor Protocol.

This is the backward-compatibility bridge: existing code that uses
create_text_postprocessor() or depends on the TextPostProcessor Protocol
continues to work unchanged, while internally delegating to the new
unified LLM factory.
"""

from __future__ import annotations

import re
from typing import Optional

from loguru import logger

from app.config import settings
from app.services.llm.factory import get_llm_service
from app.services.llm.types import LLMMessage


class ChatCompletionPostProcessor:
    """Implements TextPostProcessor Protocol using the unified LLM factory.

    Replaces both OllamaTextPostProcessor and ProxyTextPostProcessor
    with a single implementation that uses OpenAI-compatible
    /v1/chat/completions for all providers (including Ollama).

    For long ASR texts, the postprocessor splits the text into chunks
    (controlled by TEXT_MODEL_CORRECTION_CHUNK_SIZE) and processes each
    chunk independently to avoid LLM output truncation.

    Chunking strategy:
      - Title context is injected via the *system* prompt, never in the user
        content, so the LLM will not echo "标题：" / "正文：" prefixes.
      - Only the first chunk receives the full title context.
      - Subsequent chunks receive a short trailing-context hint from the
        previous *corrected* chunk (not raw) to improve coherence.
      - Chunks overlap by _OVERLAP_SIZE characters to prevent data loss at
        boundaries; the overlap is removed during merge via exact matching.
      - Meta labels (标题：/正文：) added by the LLM are stripped from output.
      - Summarization is performed by the caller on the fully corrected text
        after all chunks are merged, never per-chunk.
    """

    _OVERLAP_SIZE = 200  # overlap chars between chunks to prevent boundary data loss

    def __init__(self, role: str, system_prompt: str, timeout: int | None = None):
        self._role = role
        self._system_prompt = system_prompt
        self._timeout = timeout
        # Lazily resolve LLM service on first call
        self._llm = None

    def _get_llm(self):
        if self._llm is None:
            self._llm = get_llm_service(self._role)
        return self._llm

    async def postprocess(self, text: str, title: Optional[str] = None) -> str:
        """Process text through the LLM.

        If the text exceeds TEXT_MODEL_CORRECTION_CHUNK_SIZE, it is split
        into smaller chunks with overlap and each chunk is processed
        independently.  Results are merged with overlap deduplication.

        Args:
            text: The user content to process.
            title: Optional title used as system-prompt context only.

        Returns:
            The LLM response content as a string.
        """
        raw_text = (text or "").strip()
        if not raw_text:
            return raw_text

        chunk_size = settings.text_model_correction_chunk_size
        if chunk_size <= 0 or len(raw_text) <= chunk_size:
            result = await self._postprocess_single(raw_text, title=title)
            return self._strip_meta_labels(result)

        raw_chunks = self._split_text(raw_text, chunk_size)
        logger.info(
            f"[ChatCompletionPostProcessor] Text too long ({len(raw_text)} chars), "
            f"splitting into {len(raw_chunks)} chunks (chunk_size={chunk_size})"
        )

        # Add overlap: prepend tail of previous raw chunk to each subsequent
        # chunk so no content is lost at boundaries.
        overlapped: list[str] = [raw_chunks[0]]
        for i in range(1, len(raw_chunks)):
            prev = raw_chunks[i - 1]
            overlap = prev[-self._OVERLAP_SIZE:] if len(prev) > self._OVERLAP_SIZE else prev
            overlapped.append(overlap + "\n" + raw_chunks[i])

        results: list[str] = []
        prev_corrected_tail = ""
        for i, chunk in enumerate(overlapped, 1):
            logger.debug(
                f"[ChatCompletionPostProcessor] Processing chunk {i}/{len(overlapped)} "
                f"({len(chunk)} chars)"
            )
            # First chunk gets the title; later chunks get trailing context
            # from the previous *corrected* chunk to improve coherence.
            if i == 1:
                result = await self._postprocess_single(
                    chunk, title=title, context_hint=""
                )
            else:
                result = await self._postprocess_single(
                    chunk, title=None, context_hint=prev_corrected_tail
                )
            result = self._strip_meta_labels(result)
            results.append(result)
            # Use corrected tail (not raw) for context continuity.
            prev_corrected_tail = result[-200:] if len(result) > 200 else result

        return self._merge_corrected_chunks(results)

    async def _postprocess_single(
        self,
        text: str,
        title: Optional[str] = None,
        context_hint: str = "",
    ) -> str:
        """Send a single chunk to the LLM and return the result.

        The title and any trailing context are injected into the *system*
        prompt so the LLM never echoes "标题：" / "正文：" in its output.
        """
        llm = self._get_llm()

        # Build system prompt: base + title context + continuity hint
        system_parts: list[str] = [self._system_prompt]
        if title:
            system_parts.append(f"\n【上下文】这是一段关于「{title}」的播客/视频转写文本，请结合主题方向进行纠错。")
        if context_hint:
            # Truncate context hint to avoid bloating the prompt
            hint = context_hint[-200:] if len(context_hint) > 200 else context_hint
            system_parts.append(f"\n【衔接提示】这是同一音频转写的后续内容，前一段的结尾是：「{hint}」。请保持语气和用词一致，继续纠错。")
        system_content = "\n".join(system_parts)

        messages = [
            LLMMessage(role="system", content=system_content),
            LLMMessage(role="user", content=text),
        ]

        response = await llm.complete(
            messages,
            timeout=self._timeout,
        )
        result = (response.content or "").strip()
        if not result:
            logger.warning(
                f"[ChatCompletionPostProcessor] LLM returned empty result, "
                f"falling back to original text (input_len={len(text)}, "
                f"model={response.model}, usage={response.usage})"
            )
            return text
        return result

    @staticmethod
    def _strip_meta_labels(text: str) -> str:
        """Remove LLM-generated meta labels like '标题：...' / '正文：' from corrected output."""
        cleaned = re.sub(r'^标题[：:]\s*.*$', '', text, flags=re.MULTILINE)
        cleaned = re.sub(r'^正文[：:]\s*$', '', cleaned, flags=re.MULTILINE)
        cleaned = re.sub(r'\n{3,}', '\n\n', cleaned)
        return cleaned

    def _merge_corrected_chunks(self, chunks: list[str]) -> str:
        """Merge corrected chunks, deduplicating overlap at boundaries."""
        if not chunks:
            return ""
        if len(chunks) == 1:
            return chunks[0]

        result = chunks[0].rstrip("\n")
        for i in range(1, len(chunks)):
            deduped = self._dedup_boundary(result, chunks[i])
            if deduped.strip():
                result = result + "\n" + deduped
        return result

    def _dedup_boundary(self, prev: str, current: str) -> str:
        """Remove duplicate overlap at boundary between consecutive corrected chunks.

        Finds the longest prefix of *current* that matches a suffix of *prev*.
        The search is limited to _OVERLAP_SIZE + a small margin, because the
        overlap region between consecutive chunks is exactly _OVERLAP_SIZE
        characters.  Searching beyond that risks false-positive matches that
        delete legitimate content.

        If no significant match is found (≥10 chars), returns current as-is
        (accepting minor duplication over potential data loss).
        """
        max_search = self._OVERLAP_SIZE + 50  # overlap + margin for LLM edits
        tail = prev.rstrip()[-max_search:]
        if not tail or not current:
            return current

        # Find the longest prefix of current that matches a suffix of tail
        upper = min(len(tail), len(current), max_search)
        for length in range(upper, 9, -1):  # ≥10 chars for a confident match
            if tail[-length:] == current[:length]:
                remaining = current[length:].lstrip("\n")
                return remaining if remaining else current

        # No match found – return as-is (minor duplication better than data loss)
        return current

    @staticmethod
    def _split_text(text: str, chunk_size: int) -> list[str]:
        """Split text into chunks that do not exceed *chunk_size* characters.

        The algorithm:
        1. Split text into lines.
        2. Accumulate lines into a chunk until adding the next line would
           exceed *chunk_size*.
        3. If a single line exceeds *chunk_size*, split it by sentences.
        4. Never split inside a sentence unless the sentence itself is longer
           than *chunk_size*.
        """
        lines = text.splitlines()
        chunks: list[str] = []
        current: list[str] = []
        current_len = 0  # len("\n".join(current))

        for line in lines:
            line = line.rstrip("\n")
            line_len = len(line)

            if line_len > chunk_size:
                # Flush current chunk first
                if current:
                    chunks.append("\n".join(current))
                    current = []
                    current_len = 0

                # Split oversized line by sentences
                sentences = ChatCompletionPostProcessor._split_sentences(line)
                temp: list[str] = []
                temp_len = 0
                for sent in sentences:
                    sent_len = len(sent)
                    if temp_len + sent_len > chunk_size and temp:
                        chunks.append("".join(temp))
                        temp = [sent]
                        temp_len = sent_len
                    else:
                        temp.append(sent)
                        temp_len += sent_len
                if temp:
                    chunks.append("".join(temp))
                continue

            # Normal line: accumulate into current chunk
            add_len = line_len + (1 if current else 0)  # +1 for the joining '\n'
            if current_len + add_len > chunk_size and current:
                chunks.append("\n".join(current))
                current = [line]
                current_len = line_len
            else:
                current.append(line)
                current_len += add_len

        if current:
            chunks.append("\n".join(current))

        return chunks

    @staticmethod
    def _split_sentences(text: str) -> list[str]:
        """Split text into sentences, preserving trailing punctuation."""
        # Match CJK and Latin sentence-ending punctuation
        pattern = re.compile(r"([。！？．.?!])")
        parts = pattern.split(text)
        sentences: list[str] = []
        i = 0
        while i < len(parts):
            sent = parts[i]
            if i + 1 < len(parts) and parts[i + 1] in "。！？．.?!":
                sent += parts[i + 1]
                i += 2
            else:
                i += 1
            if sent.strip():
                sentences.append(sent)
        return sentences
