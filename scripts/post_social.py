"""
SNS自動投稿スクリプト
- commitメッセージから [screenshot: ファイル名] [video: ファイル名] を抽出
- README.md を Gemini API に渡して投稿文を生成
- X（日英）・Instagram（日英）・YouTube（日英）に投稿
"""

import os
import re
import sys
import json
import tweepy
import requests
from google import genai
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from google.oauth2.credentials import Credentials


# ─────────────────────────────────────────
# 定数
# ─────────────────────────────────────────

DRY_RUN = os.environ.get("DRY_RUN", "false").lower() == "true"
COMMIT_MESSAGE = os.environ.get("COMMIT_MESSAGE", "")
REPO = os.environ.get("GITHUB_REPOSITORY", "")  # "owner/repo" 形式

RAW_BASE = f"https://raw.githubusercontent.com/{REPO}/main"


# ─────────────────────────────────────────
# 1. commitメッセージからファイル名を抽出
# ─────────────────────────────────────────

def extract_screenshot(commit_message: str) -> str | None:
    """[screenshot: ファイル名] を抽出。なければ None。"""
    match = re.search(r"\[screenshot:\s*(.+?)\]", commit_message)
    return match.group(1).strip() if match else None


def extract_video(commit_message: str) -> str | None:
    """[video: ファイル名] を抽出。なければ None。"""
    match = re.search(r"\[video:\s*(.+?)\]", commit_message)
    return match.group(1).strip() if match else None


# ─────────────────────────────────────────
# 2. README.md を読み込む
# ─────────────────────────────────────────

def load_readme() -> str:
    path = "README.md"
    if not os.path.exists(path):
        print("[ERROR] README.md が見つかりません")
        sys.exit(1)
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


# ─────────────────────────────────────────
# 3. Gemini API で投稿文を生成
# ─────────────────────────────────────────

def generate_posts(readme: str, repo_name: str, has_video: bool) -> dict:
    """
    Gemini 2.0 Flash で投稿文を生成。
    戻り値:
    {
        "x_jp": "...", "x_en": "...",
        "ig_jp": "...", "ig_en": "...",
        "yt_jp": "...", "yt_en": "..."  # has_video=True のときのみ
    }
    """
    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])

    yt_format = ""
    yt_note = ""
    if has_video:
        yt_format = f"""
  "yt_jp": "YouTube日本語説明文\\nキャッチコピー1行\\n機能3点（箇条書き）\\nGitHubリンク: https://github.com/{REPO}\\nハッシュタグ15個（#個人開発 #Webアプリ等）",
  "yt_en": "YouTube English description\\n1 catchphrase\\n3 features (bullet points)\\nGitHub: https://github.com/{REPO}\\n15 hashtags (#buildinpublic #webdev etc)","""
        yt_note = "- YouTube用説明文（yt_jp・yt_en）も含めること。ハッシュタグは各15個。"

    prompt = f"""
あなたはSNS投稿文を生成するアシスタントです。
以下のREADMEを元に、投稿文をJSON形式で生成してください。

リポジトリ名: {repo_name}

README:
{readme}

---

出力フォーマット（JSONのみ・マークダウン不要）:
{{
  "x_jp": "X日本語投稿文（280文字以内）\\nキャッチコピー1行\\n機能3点（箇条書き）\\nGitHubリンク: https://github.com/{REPO}\\nハッシュタグ5個（#個人開発 #Webアプリ #駆け出しエンジニア 等）",
  "x_en": "X English post (within 280 chars)\\n1 catchphrase\\n3 features (bullet points)\\nGitHub: https://github.com/{REPO}\\n5 hashtags (#buildinpublic #opensource #webdev #sideproject etc)",
  "ig_jp": "Instagram日本語投稿文\\nキャッチコピー1行\\n機能3点（箇条書き）\\nGitHubリンク: https://github.com/{REPO}\\nハッシュタグ30個",
  "ig_en": "Instagram English post\\n1 catchphrase\\n3 features (bullet points)\\nGitHub: https://github.com/{REPO}\\n30 hashtags",{yt_format}
}}

注意:
- JSONのみ出力。説明文・マークダウン記号（```等）は不要。
- 文字数制限を厳守（X: 280文字以内）。
- ハッシュタグはX用5個・Instagram用30個。
{yt_note}
"""

    response = client.models.generate_content(
        model="gemini-2.0-flash",
        contents=prompt,
    )

    raw = response.text.strip()
    raw = re.sub(r"^```json\s*|^```\s*|```$", "", raw, flags=re.MULTILINE).strip()

    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"[ERROR] Gemini APIのレスポンスをJSONパースできませんでした: {e}")
        print(f"[DEBUG] raw response:\n{raw}")
        sys.exit(1)


# ─────────────────────────────────────────
# 4. X（Twitter）に投稿
# ─────────────────────────────────────────

def post_to_x(text: str, image_path: str, prefix: str, env_prefix: str):
    if DRY_RUN:
        print(f"[DRY_RUN] {prefix} 投稿文:\n{text}\n画像: {image_path}\n")
        return

    client = tweepy.Client(
        consumer_key=os.environ[f"{env_prefix}_API_KEY"],
        consumer_secret=os.environ[f"{env_prefix}_API_SECRET"],
        access_token=os.environ[f"{env_prefix}_ACCESS_TOKEN"],
        access_token_secret=os.environ[f"{env_prefix}_ACCESS_SECRET"],
    )

    auth = tweepy.OAuth1UserHandler(
        os.environ[f"{env_prefix}_API_KEY"],
        os.environ[f"{env_prefix}_API_SECRET"],
        os.environ[f"{env_prefix}_ACCESS_TOKEN"],
        os.environ[f"{env_prefix}_ACCESS_SECRET"],
    )
    api_v1 = tweepy.API(auth)

    media = api_v1.media_upload(filename=image_path)
    client.create_tweet(text=text, media_ids=[media.media_id])
    print(f"[OK] {prefix} 投稿完了")


# ─────────────────────────────────────────
# 5. Instagram に投稿
# ─────────────────────────────────────────

def post_to_instagram(text: str, image_url: str, prefix: str, env_prefix: str):
    if DRY_RUN:
        print(f"[DRY_RUN] {prefix} 投稿文:\n{text}\n画像URL: {image_url}\n")
        return

    access_token = os.environ[f"{env_prefix}_ACCESS_TOKEN"]
    user_id = os.environ[f"{env_prefix}_USER_ID"]

    create_url = f"https://graph.instagram.com/v19.0/{user_id}/media"
    create_res = requests.post(create_url, data={
        "image_url": image_url,
        "caption": text,
        "access_token": access_token,
    })
    create_data = create_res.json()

    if "id" not in create_data:
        print(f"[ERROR] {prefix} メディアコンテナ作成失敗: {create_data}")
        sys.exit(1)

    container_id = create_data["id"]

    publish_url = f"https://graph.instagram.com/v19.0/{user_id}/media_publish"
    publish_res = requests.post(publish_url, data={
        "creation_id": container_id,
        "access_token": access_token,
    })
    publish_data = publish_res.json()

    if "id" not in publish_data:
        print(f"[ERROR] {prefix} 投稿公開失敗: {publish_data}")
        sys.exit(1)

    print(f"[OK] {prefix} 投稿完了")


# ─────────────────────────────────────────
# 6. YouTube に投稿
# ─────────────────────────────────────────

def get_youtube_client(env_prefix: str):
    creds = Credentials(
        token=os.environ[f"{env_prefix}_ACCESS_TOKEN"],
        refresh_token=os.environ[f"{env_prefix}_REFRESH_TOKEN"],
        client_id=os.environ["YT_CLIENT_ID"],
        client_secret=os.environ["YT_CLIENT_SECRET"],
        token_uri="https://oauth2.googleapis.com/token",
    )
    return build("youtube", "v3", credentials=creds)


def post_video_to_youtube(description: str, video_path: str, repo_name: str, prefix: str, env_prefix: str):
    if DRY_RUN:
        print(f"[DRY_RUN] {prefix} 動画投稿:\nタイトル: {repo_name}\n説明文: {description}\n動画: {video_path}\n")
        return

    youtube = get_youtube_client(env_prefix)

    body = {
        "snippet": {
            "title": repo_name,
            "description": description,
            "categoryId": "28",  # Science & Technology
        },
        "status": {"privacyStatus": "public"},
    }

    media = MediaFileUpload(video_path, chunksize=-1, resumable=True)
    request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)
    response = request.execute()

    if "id" not in response:
        print(f"[ERROR] {prefix} 動画アップロード失敗: {response}")
        sys.exit(1)

    print(f"[OK] {prefix} 動画投稿完了: https://youtu.be/{response['id']}")


def post_community_to_youtube(text: str, image_url: str, prefix: str, env_prefix: str):
    if DRY_RUN:
        print(f"[DRY_RUN] {prefix} コミュニティ投稿:\n{text}\n画像URL: {image_url}\n")
        return

    youtube = get_youtube_client(env_prefix)

    body = {"snippet": {"textOriginal": text}}
    response = youtube.communityPosts().insert(part="snippet", body=body).execute()

    if "id" not in response:
        print(f"[ERROR] {prefix} コミュニティ投稿失敗: {response}")
        sys.exit(1)

    print(f"[OK] {prefix} コミュニティ投稿完了")


# ─────────────────────────────────────────
# メイン処理
# ─────────────────────────────────────────

def main():
    print(f"[INFO] DRY_RUN={DRY_RUN}")
    print(f"[INFO] COMMIT_MESSAGE={COMMIT_MESSAGE}")

    screenshot_filename = extract_screenshot(COMMIT_MESSAGE)
    video_filename = extract_video(COMMIT_MESSAGE)

    if not screenshot_filename and not video_filename:
        print("[INFO] commitメッセージに [screenshot: ファイル名] も [video: ファイル名] も見つかりませんでした。投稿をスキップします。")
        print("[INFO] 投稿したい場合は commitメッセージに [screenshot: your_image.png] や [video: your_video.mp4] を追加してください。")
        sys.exit(0)

    screenshot_local = None
    screenshot_url = None
    if screenshot_filename:
        screenshot_local = f"screenshots/{screenshot_filename}"
        screenshot_url = f"{RAW_BASE}/screenshots/{screenshot_filename}"
        if not os.path.exists(screenshot_local):
            print(f"[ERROR] 画像ファイルが見つかりません: {screenshot_local}")
            sys.exit(1)
        print(f"[INFO] 使用する画像: {screenshot_local}")

    video_local = None
    if video_filename:
        video_local = f"videos/{video_filename}"
        if not os.path.exists(video_local):
            print(f"[ERROR] 動画ファイルが見つかりません: {video_local}")
            sys.exit(1)
        print(f"[INFO] 使用する動画: {video_local}")

    readme = load_readme()
    repo_name = REPO.split("/")[-1] if "/" in REPO else REPO

    print("[INFO] Gemini APIで投稿文を生成中...")
    posts = generate_posts(readme, repo_name, has_video=bool(video_filename))
    print("[INFO] 生成完了")

    if screenshot_filename:
        post_to_x(text=posts["x_jp"], image_path=screenshot_local, prefix="X_JP", env_prefix="X_JP")
        post_to_x(text=posts["x_en"], image_path=screenshot_local, prefix="X_EN", env_prefix="X_EN")
        post_to_instagram(text=posts["ig_jp"], image_url=screenshot_url, prefix="IG_JP", env_prefix="IG_JP")
        post_to_instagram(text=posts["ig_en"], image_url=screenshot_url, prefix="IG_EN", env_prefix="IG_EN")
        post_community_to_youtube(text=posts.get("yt_jp", posts["x_jp"]), image_url=screenshot_url, prefix="YT_JP", env_prefix="YT_JP")
        post_community_to_youtube(text=posts.get("yt_en", posts["x_en"]), image_url=screenshot_url, prefix="YT_EN", env_prefix="YT_EN")

    if video_filename:
        post_video_to_youtube(description=posts["yt_jp"], video_path=video_local, repo_name=repo_name, prefix="YT_JP", env_prefix="YT_JP")
        post_video_to_youtube(description=posts["yt_en"], video_path=video_local, repo_name=repo_name, prefix="YT_EN", env_prefix="YT_EN")

    print("[INFO] 全アカウントへの投稿が完了しました")


if __name__ == "__main__":
    main()
