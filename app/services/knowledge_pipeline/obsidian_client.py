"""
Obsidian 写入客户端。

优先通过 Obsidian Local REST API 写入，失败时回退到直接文件系统写入。
两种方式都失败时抛出包含两个异常上下文的 RuntimeError。
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from loguru import logger


class ObsidianWriter:
    """
    写入 Obsidian Vault 的双路客户端。

    - 首先尝试 Obsidian Local REST API（需 obsidian-local-rest-api 插件）
    - API 不可用或失败时自动 fallback 到直接写文件系统
    """

    def __init__(
        self,
        vault_root: Optional[Path] = None,
        rest_url: Optional[str] = None,
        api_key: Optional[str] = None,
    ):
        from app.config import settings
        from app.services.content_storage import ContentStorageManager

        self._vault_root = vault_root or ContentStorageManager().get_vault_root()
        self._rest_url = (rest_url or settings.obsidian_local_rest_url or "").rstrip("/")
        self._api_key = api_key or settings.obsidian_local_rest_api_key

    async def write_text(self, vault_relative_path: str, text: str) -> None:
        """
        将文本写入 vault-relative 路径。

        先尝试 Local REST API；若失败则直接写文件系统。
        """
        rest_error: Optional[Exception] = None
        if self._rest_url and self._api_key:
            try:
                await self._write_via_local_rest(vault_relative_path, text)
                return
            except Exception as exc:
                rest_error = exc
                logger.debug(
                    f"[ObsidianWriter] REST API 写入失败，回退到文件系统: {exc}"
                )

        try:
            self._write_via_filesystem(vault_relative_path, text)
        except Exception as fs_error:
            if rest_error:
                raise RuntimeError(
                    f"Obsidian 写入失败（REST: {rest_error}，FS: {fs_error}）"
                ) from fs_error
            raise

    async def _write_via_local_rest(
        self, vault_relative_path: str, text: str
    ) -> None:
        """通过 Obsidian Local REST API PUT /vault/<path> 写入文件。"""
        import httpx

        url = f"{self._rest_url}/vault/{vault_relative_path.lstrip('/')}"
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "text/markdown",
        }
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.put(url, content=text.encode("utf-8"), headers=headers)
            resp.raise_for_status()
        logger.debug(f"[ObsidianWriter] REST 写入成功: {vault_relative_path}")

    def _write_via_filesystem(self, vault_relative_path: str, text: str) -> None:
        """直接写入本地文件系统。"""
        target = Path(self._vault_root) / vault_relative_path.lstrip("/")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        logger.debug(f"[ObsidianWriter] 文件系统写入成功: {target}")
