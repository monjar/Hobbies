"""Export publisher: a dated folder with the video and its caption, for manual upload."""

import shutil
import time
from pathlib import Path

from .base import Post, PublishResult


class ExportPublisher:
    def __init__(self, platform: str, out_dir: Path, ai_disclosure: bool):
        self.platform = platform
        self.out_dir = out_dir
        self.ai_disclosure = ai_disclosure

    def _paths(self, post: Post) -> tuple[Path, Path]:
        folder = self.out_dir / "export" / time.strftime("%Y-%m-%d") / self.platform
        return folder / f"{post.job_id}.mp4", folder / f"{post.job_id}.txt"

    def _caption_file(self, post: Post) -> str:
        text = f"TITLE\n{post.title}\n\nCAPTION\n{post.caption}\n"
        if self.ai_disclosure:
            text += "\nBEFORE POSTING\nSwitch on the platform's AI-generated content label: the narration is a synthetic voice.\n"
        return text

    def describe(self, post: Post) -> list[dict]:
        video, caption = self._paths(post)
        return [
            {"action": "copy", "from": str(post.video), "to": str(video)},
            {"action": "write", "to": str(caption), "content": self._caption_file(post)},
        ]

    def publish(self, post: Post) -> PublishResult:
        video, caption = self._paths(post)
        video.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(post.video, video)
        caption.write_text(self._caption_file(post))
        return PublishResult("exported", None, None, {"video": str(video), "caption": str(caption)})
