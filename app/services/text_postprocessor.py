from typing import Protocol


class TextPostProcessor(Protocol):
    async def postprocess(self, text: str) -> str:
        ...
