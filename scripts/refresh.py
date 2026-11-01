
import json
import os
import re
import sys
import time
import urllib.request

from yt_dlp import YoutubeDL

BASE_URL = os.environ.get("WORKER_URL", "").strip().rstrip("/")
SYNC_TOKEN = os.environ.get("SYNC_TOKEN", "").strip()

if not BASE_URL.startswith("https://") or len(SYNC_TOKEN) < 16:
    sys.exit("WORKER_URL / SYNC_TOKEN fehlt.")


def request_json(url, method="GET", body=None, token=None):
    data = json.dumps(body).encode() if body is not None else None
    headers = {
        "Accept": "application/json",
        "User-Agent": "IPTV-Live-Manager/2"
    }

    if data is not None:
        headers["Content-Type"] = "application/json"

    if token:
        headers["Authorization"] = "Bearer " + token

    req = urllib.request.Request(
        url, method=method, data=data, headers=headers
    )

    with urllib.request.urlopen(req, timeout=45) as response:
        return json.load(response)


def pick_hls(info):
    choices = []

    for fmt in info.get("formats") or []:
        url = fmt.get("url") or ""
        protocol = fmt.get("protocol") or ""

        if url.startswith("https://") and (
            protocol in ("m3u8", "m3u8_native")
            or ".m3u8" in url.split("?")[0]
        ):
            height = fmt.get("height") or 0
            audio = fmt.get("acodec") not in (None, "none")
            video = fmt.get("vcodec") not in (None, "none")

            manifest = fmt.get("manifest_url") or ""
            if manifest.startswith("https://"):
                url = manifest

            choices.append((
                (1 if audio and video else 0, height),
                url
            ))

    if choices:
        return sorted(choices, reverse=True)[0][1]

    manifest = info.get("hls_manifest_url") or ""

    if isinstance(manifest, str) and manifest.startswith("https://"):
        return manifest

    return None


def get_info(url):
    with YoutubeDL({
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "noplaylist": True,
        "socket_timeout": 18,
        "retries": 1,
        "extractor_retries": 1,
        "ignoreerrors": False,
    }) as ydl:
        return ydl.extract_info(url, download=False)


def process(channel, previous):
    slug = channel["slug"]
    source = channel["source"]

    result = {
        "slug": slug,
        "url": None,
        "status": "Nicht verfuegbar",
        "message": "",
        "videoId": None,
        "channelId": None
    }

    previous_channel = (
        (previous.get("channels") or {}).get(slug) or {}
    ).get("channelId")

    targets = []

    if previous_channel and re.fullmatch(
        r"UC[\w-]{22}", previous_channel
    ):
        targets.append(
            "https://www.youtube.com/channel/"
            + previous_channel + "/live"
        )

    targets.append(source)
    problems = []

    for target in dict.fromkeys(targets):
        try:
            info = get_info(target)

            if not isinstance(info, dict):
                problems.append("Keine YouTube-Metadaten")
                continue

            channel_id = (
                info.get("channel_id")
                or info.get("uploader_id")
            )

            if isinstance(channel_id, str) and re.fullmatch(
                r"UC[\w-]{22}", channel_id
            ):
                result["channelId"] = channel_id

            result["videoId"] = str(info.get("id") or "")[:40]

            live_status = info.get("live_status")

            if live_status in (
                "not_live", "was_live",
                "post_live", "is_upcoming"
            ):
                problems.append(
                    "Aktuell nicht live: " + str(live_status)
                )
                continue

            link = pick_hls(info)

            if link:
                result.update(
                    url=link,
                    status="HLS gefunden",
                    message="HLS automatisch erkannt"
                )
                return result

            problems.append("Kein HLS-Stream gefunden")

        except Exception as exc:
            problems.append(
                str(exc).splitlines()[0][:110]
            )

    result["message"] = "; ".join(problems)[:150]
    return result


def main():
    config = request_json(BASE_URL + "/api/config")
    status = request_json(BASE_URL + "/api/status")

    channels = [
        c for c in config
        if c.get("mode") == "youtube"
    ]

    print("YouTube-Kanaele:", len(channels))
    results = []

    for i, channel in enumerate(channels, 1):
        print(
            f"[{i}/{len(channels)}] {channel['name']}",
            flush=True
        )

        record = process(channel, status)
        results.append(record)

        print(
            record["status"],
            record["message"][:85],
            flush=True
        )

        time.sleep(2)

    response = request_json(
        BASE_URL + "/api/sync",
        method="POST",
        body={"results": results},
        token=SYNC_TOKEN
    )

    print("Cloudflare aktualisiert:", response.get("ok"))
    print(
        "HLS-Erfolge:",
        sum(bool(r["url"]) for r in results),
        "/", len(results)
    )


if __name__ == "__main__":
    main()
