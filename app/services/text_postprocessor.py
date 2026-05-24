from typing import Optional, Protocol


class TextPostProcessor(Protocol):
    async def postprocess(self, text: str, title: Optional[str] = None) -> str:
        ...
