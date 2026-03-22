from __future__ import annotations

from ..html_utils import DOMSnapshot


class MediaMetadataWorker:
    def analyze(self, html: str) -> dict:
        snapshot = DOMSnapshot.from_html(html)
        videos = snapshot.find("video")
        audios = snapshot.find("audio")
        tracks = snapshot.find("track")
        caption_tracks = [
            node for node in tracks if node.attrs.get("kind", "").lower() in {"captions", "subtitles"}
        ]
        description_tracks = [node for node in tracks if node.attrs.get("kind", "").lower() in {"descriptions"}]
        autoplay_media = [node for node in (videos + audios) if "autoplay" in node.attrs]

        return {
            "video_count": len(videos),
            "audio_count": len(audios),
            "caption_track_count": len(caption_tracks),
            "description_track_count": len(description_tracks),
            "autoplay_media_count": len(autoplay_media),
            "has_live_hint": any("live" in (node.attrs.get("class", "") + node.attrs.get("id", "")).lower() for node in videos),
        }
