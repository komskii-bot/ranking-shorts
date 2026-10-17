"""Optional direct upload via the YouTube Data API (upload_mode = "youtube").

Note: YouTube locks API uploads to PRIVATE until your Google Cloud project
passes YouTube's free API audit. Until then, keep upload_mode = "metricool".
"""
import os


def upload(video_path, script, config, synthetic):
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload

    creds = Credentials(
        None,
        refresh_token=os.environ["YT_REFRESH_TOKEN"],
        client_id=os.environ["YT_CLIENT_ID"],
        client_secret=os.environ["YT_CLIENT_SECRET"],
        token_uri="https://oauth2.googleapis.com/token",
        scopes=["https://www.googleapis.com/auth/youtube.upload"],
    )
    yt = build("youtube", "v3", credentials=creds, cache_discovery=False)
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
