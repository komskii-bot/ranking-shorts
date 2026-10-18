# Ranking Shorts — automated "Top 5" videos

Every day at **4:00 PM Manila** this makes one finished vertical ranking Short. **You upload it yourself** to Bojji (@bojjiii).

```
GitHub Actions (free)
  1. Takes the next script from state/queue.json
     (queue low? Gemini writes more, searching the web for current data)
  2. Voiceover        edge-tts (free neural voice)
  3. Visuals          Pexels / Pixabay video, or Openverse public-domain images
  4. Editing          FFmpeg: rank badges, captions, hidden #1 ladder, progress bar, mascot, music
  5. Posts the MP4 + a copy-paste upload sheet on the repo's Releases page
```

## Your daily 2 minutes
1. Open **github.com/komskii-bot/ranking-shorts/releases** → newest release → download the `.mp4`.
   (Get an email for each new video: repo page → **Watch → Custom → Releases** → Apply.)
2. YouTube Studio → check the channel is **Bojji** → Create → Upload videos.
3. Copy the title, description and tags from the release notes; set "Not made for kids".
4. Visibility → **Schedule** for your posting slot.

---

## One-time setup (about 30 minutes)

### 1. Put this code on GitHub
1. Create a free account at github.com.
2. Click **New repository** → name it `ranking-shorts` → Public or Private both work → Create.
   (Private repos get 2,000 free Actions minutes a month; one run takes about 5–8 minutes, so daily posting fits.)
3. On the empty repo page, click **uploading an existing file** and drag in everything from this folder.
   **The `.github` folder is hidden on Mac and Windows** — make sure it's uploaded too (on Mac press Cmd+Shift+. in Finder to show it). Without it nothing runs.
   Easier option: connect GitHub to Claude and ask Claude to push it for you.

### 2. Get two free API keys
| Key | Where | Used for |
|---|---|---|
| `GEMINI_API_KEY` | aistudio.google.com/apikey → Create API key | Writing new scripts with live web data |
| `PEXELS_API_KEY` | pexels.com/api → sign up → copy key | Stock footage |

### 3. Add them as repository secrets
Repo → **Settings → Secrets and variables → Actions → New repository secret**. Add both, named exactly as above.

### 4. Allow the workflow to save its progress
Repo → **Settings → Actions → General → Workflow permissions** → choose **Read and write permissions** → Save.

### 5. Test it
Repo → **Actions** tab → **Daily ranking Short** → **Run workflow**. After ~5 minutes, the video appears in YouTube Studio, and a backup copy appears under the repo's **Releases**. Do this after step 6.

### 6. Connect your YouTube channel (about 15 minutes)
**a. Google Cloud project**
1. Go to console.cloud.google.com, signed in with the Google account that owns the channel → create a project called `ranking-shorts`.
2. **APIs & Services → Library** → search **YouTube Data API v3** → **Enable**.
3. **Google Auth Platform → Branding**: app name `Ranking Shorts`, your email as support and developer contact → Save.
4. **Audience**: User type **External**. Then click **Publish app** so the status is **In production**. (In "Testing" mode your login expires every 7 days and uploads stop.)
5. **Clients → Create client** → type **Web application** → under *Authorized redirect URIs* add `https://developers.google.com/oauthplayground` → Create. Copy the **Client ID** and **Client secret**.

**b. Get a refresh token (lets GitHub upload as you)**
1. Open developers.google.com/oauthplayground → gear icon (top right) → tick **Use your own OAuth credentials** → paste the Client ID and secret.
2. In the box under the scope list, type `https://www.googleapis.com/auth/youtube.upload` → **Authorize APIs**.
3. Sign in and **choose your ranking channel** when asked which account/channel. If you see "Google hasn't verified this app", click **Advanced → Go to Ranking Shorts** (it's your own app).
4. Click **Exchange authorization code for tokens** → copy the **Refresh token**.

**c. Add 3 more secrets** (same place as step 3): `YT_CLIENT_ID`, `YT_CLIENT_SECRET`, `YT_REFRESH_TOKEN`.

**d. Request YouTube's API audit (free)**
YouTube keeps API uploads **private** until your project passes its compliance audit. Search "YouTube API Services Audit and Quota Extension Form", fill it in (purpose: uploading videos to your own channel), and submit. Approval can take a few weeks. Until then, each daily video lands in YouTube Studio as Private — tap **Visibility → Public** (about 10 seconds). After approval, they go public on their own.

---

## Day to day (all optional)

**Add topic ideas** — edit `topics.txt` on GitHub, one per line. The writer uses them top to bottom, then invents its own, alternating current tech/AI topics with evergreen ones.

**Review upcoming scripts** — `state/queue.json` always holds ~5 ready scripts, including their sources. Edit or delete any you don't like. AI-written facts can be wrong, so skimming the queue once a week is the best quality check.

**Mascot** — your character (`assets/mascot.png`) is added to every video automatically: big and pop-up during the intro and outro, small in the corner during the countdown, bobbing while the narrator talks. To change him, replace that file with another PNG that has a transparent background. To turn him off, set `"mascot": ""` in `config.json`.

**Talking host clips (optional, Google Flow, once a month, ~15 min)** — generate short vertical clips of your character and upload the MP4s to:
- `host_clips/intro/` — e.g. "Let's rank them!", "You won't believe number one"
- `host_clips/outro/` — e.g. "What's your number one? Comment below!"

Each video picks one at random. When a host clip is used, the video is automatically marked as AI-generated content on YouTube (required for realistic AI people). Keep lines generic so they fit any topic. With 200 AI Plus credits at 10 credits per Veo Lite clip, that's about 20 clips a month, which is plenty.

**Background music** — download free tracks from YouTube Studio → Audio Library and upload MP3s to `assets/music/`. One is picked at random, played quietly under the voice.

**Change the look or voice** — edit `config.json`: `accent_color`, `voice` (e.g. `en-US-GuyNeural`, `en-US-AriaNeural`), `voice_rate`, `channel_name`.

**Change the time** — edit the `cron` line in `.github/workflows/daily.yml` (it's in UTC; Manila is UTC+8).

---

## Limits worth knowing
- YouTube's free API quota is 10,000 units a day and one upload uses about 1,600, so daily posting is well within it.
- If an upload fails (expired login, quota), the run shows a red ❌ in the Actions tab and GitHub emails you. The script stays in the queue, so nothing is skipped.

## Testing locally
`python -m pipeline.main --offline` renders the next script with placeholder audio and backgrounds, no keys needed (requires Python 3.11+ and FFmpeg).

## Staying monetizable
YouTube demonetizes "mass-produced, repetitive" content. This setup helps by varying topics, footage, host clips and music, but the strongest protection is adding your own touch: your host character, real topic choices, and replying to comments.
