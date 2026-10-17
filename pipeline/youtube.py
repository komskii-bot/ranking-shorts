"""Direct upload via the YouTube Data API, with a channel safety lock.

Before any upload, the signed-in channel is checked against
config["youtube_channel"] (e.g. "@bojjiii"). On a mismatch nothing is uploaded.
"""
import os

SCOPES = ["https://www.googleapis.com/auth/youtube.upload",
          "https://www.googleapis.com/auth/youtube.readonly"]


def _client():
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    creds = Credentials(
        None,
        refresh_token=os.environ["YT_REFRESH_TOKEN"],
        client_id=os.environ["YT_CLIENT_ID"],
        client_secret=os.environ["YT_CLIENT_SECRET"],
        token_uri="https://oauth2.googleapis.com/token",
        scopes=SCOPES,
    )
    return build("youtube", "v3", credentials=creds, cache_discovery=False)


def check_channel(config):
    """Return the signed-in channel's handle, or raise if it isn't the expected one."""
    want = (config.get("youtube_channel") or "").lower().lstrip("@")
    if not want:
        raise RuntimeError('Set "youtube_channel" in config.json (e.g. "@bojjiii") before uploading.')
    try:
        items = _client().channels().list(part="snippet", mine=True).execute().get("items", [])
    except Exception as e:
        raise RuntimeError(
            "Couldn't read which channel this login belongs to. Redo the sign-in with BOTH scopes "
            f"(youtube.upload and youtube.readonly) and update YT_REFRESH_TOKEN. Details: {e}")
    if not items:
        raise RuntimeError("This login has no YouTube channel.")
    snip = items[0]["snippet"]
    got = (snip.get("customUrl") or "").lower().lstrip("@")
    label = f'{snip.get("title")} (@{got}, id {items[0]["id"]})'
    if got != want:
        raise RuntimeError(f"SAFETY LOCK: signed in as {label}, expected @{want}. Nothing was uploaded.")
    print(f"Channel check OK: {label}")
    return label


def upload(video_path, script, config, synthetic):
    from googleapiclient.http import MediaFileUpload

    yt = _client()
    body = {
        "snippet": {
            "title": script["yt_title"],
            "description": script["yt_description"],
            "tags": script["tags"][:15],
            "categoryId": config["youtube_category_id"],
        },
        "status": {
            "privacyStatus": config["youtube_privacy"],
            "selfDeclaredMadeForKids": config["made_for_kids"],
            "containsSyntheticMedia": synthetic,
        },
    }
    req = yt.videos().insert(part="snippet,status", body=body,
                             media_body=MediaFileUpload(video_path, chunksize=-1, resumable=True))
    resp = None
    while resp is None:
        _, resp = req.next_chunk()
    return resp["id"]
