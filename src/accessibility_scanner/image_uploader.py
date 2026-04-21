"""Upload screenshots to free image hosts and return public URLs."""
from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Callable


def upload_catbox(file_path: str) -> str:
    """Upload to catbox.moe — no API key needed. Returns public URL."""
    import requests
    with open(file_path, "rb") as f:
        resp = requests.post(
            "https://catbox.moe/user/api.php",
            data={"reqtype": "fileupload"},
            files={"fileToUpload": f},
            timeout=30,
        )
    resp.raise_for_status()
    url = resp.text.strip()
    if not url.startswith("http"):
        raise RuntimeError(f"catbox.moe upload failed: {resp.text}")
    return url


def upload_imgbb(file_path: str, api_key: str) -> str:
    """Upload to imgbb.com — requires free API key from https://api.imgbb.com/"""
    import base64
    import requests
    with open(file_path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()
    resp = requests.post(
        "https://api.imgbb.com/1/upload",
        data={"key": api_key, "image": b64},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    if not data.get("success"):
        raise RuntimeError(f"imgbb upload failed: {data}")
    return data["data"]["url"]


# ── OneDrive uploader via Microsoft Graph API ────────────────────────────────

class OneDriveUploader:
    """Upload files to OneDrive and return anonymous sharing links.

    Env vars required:
      ONEDRIVE_TENANT_ID     — Azure AD tenant ID
      ONEDRIVE_CLIENT_ID     — Azure AD app (client) ID
      ONEDRIVE_CLIENT_SECRET — Azure AD app client secret
      ONEDRIVE_USER_ID       — User ID or UPN (email) for the target OneDrive
      ONEDRIVE_FOLDER        — (optional) Folder path in OneDrive, e.g. "wcag-screenshots"
    """

    def __init__(self, report_id: str = "") -> None:
        import msal
        self._tenant_id = os.environ["ONEDRIVE_TENANT_ID"]
        self._client_id = os.environ["ONEDRIVE_CLIENT_ID"]
        self._client_secret = os.environ["ONEDRIVE_CLIENT_SECRET"]
        self._user_id = os.environ.get("ONEDRIVE_USER_ID", "me")
        base_folder = os.environ.get("ONEDRIVE_FOLDER", "wcag-screenshots")
        # Include report_id as a subfolder to prevent collisions across reports
        self._folder = f"{base_folder}/{report_id}" if report_id else base_folder
        self._token: str | None = None
        self._token_expiry: float = 0
        self._app = msal.ConfidentialClientApplication(
            client_id=self._client_id,
            client_credential=self._client_secret,
            authority=f"https://login.microsoftonline.com/{self._tenant_id}",
        )

    def _get_token(self) -> str:
        if self._token and time.time() < self._token_expiry - 60:
            return self._token
        result = self._app.acquire_token_for_client(
            scopes=["https://graph.microsoft.com/.default"]
        )
        if "access_token" not in result:
            raise RuntimeError(f"OneDrive auth failed: {result.get('error_description', result)}")
        self._token = result["access_token"]
        self._token_expiry = time.time() + result.get("expires_in", 3600)
        return self._token

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._get_token()}"}

    def upload(self, file_path: str) -> str:
        """Upload a file to OneDrive and return an anonymous view link."""
        import requests

        # Build a unique upload name by including parent directory context
        # to avoid collisions when different checklist folders have files with
        # the same basename (e.g. state_03_blogs__ISSUE-002_annotated.png)
        fp = Path(file_path)
        filename = fp.name
        # Walk up to find a distinguishing directory (checklist name or run ID)
        parts = fp.parts
        # Look for "checklist_reports" in path to extract checklist subfolder
        unique_prefix = ""
        for i, part in enumerate(parts):
            if part == "checklist_reports" and i + 1 < len(parts):
                unique_prefix = parts[i + 1] + "_"
                break
        upload_name = f"{unique_prefix}{filename}"

        file_size = os.path.getsize(file_path)

        # Build the upload URL
        if self._user_id == "me":
            base = "https://graph.microsoft.com/v1.0/me/drive"
        else:
            base = f"https://graph.microsoft.com/v1.0/users/{self._user_id}/drive"

        folder_path = self._folder.strip("/")
        upload_path = f"{folder_path}/{upload_name}" if folder_path else upload_name

        if file_size < 4 * 1024 * 1024:
            # Simple upload for files < 4MB
            url = f"{base}/root:/{upload_path}:/content"
            with open(file_path, "rb") as f:
                resp = requests.put(
                    url,
                    headers={**self._headers(), "Content-Type": "application/octet-stream"},
                    data=f,
                    timeout=60,
                )
            resp.raise_for_status()
            item_id = resp.json()["id"]
        else:
            # Resumable upload for larger files
            session_url = f"{base}/root:/{upload_path}:/createUploadSession"
            session_resp = requests.post(
                session_url,
                headers={**self._headers(), "Content-Type": "application/json"},
                json={"item": {"@microsoft.graph.conflictBehavior": "rename"}},
                timeout=30,
            )
            session_resp.raise_for_status()
            upload_url = session_resp.json()["uploadUrl"]

            with open(file_path, "rb") as f:
                data = f.read()
            resp = requests.put(
                upload_url,
                data=data,
                headers={"Content-Range": f"bytes 0-{len(data)-1}/{len(data)}"},
                timeout=120,
            )
            resp.raise_for_status()
            item_id = resp.json()["id"]

        # Create anonymous sharing link
        share_url = f"{base}/items/{item_id}/createLink"
        share_resp = requests.post(
            share_url,
            headers={**self._headers(), "Content-Type": "application/json"},
            json={"type": "view", "scope": "anonymous"},
            timeout=30,
        )
        share_resp.raise_for_status()
        return share_resp.json()["link"]["webUrl"]


def make_uploader(
    provider: str = "catbox",
    imgbb_api_key: str = "",
    report_id: str = "",
) -> Callable[[str], str]:
    """
    Returns an upload function.

    provider: "catbox" (no key needed) | "imgbb" (requires imgbb_api_key) | "onedrive" (requires env vars)
    report_id: unique identifier for the report (e.g. app_id) to prevent filename collisions in OneDrive
    """
    cache: dict[str, str] = {}
    onedrive: OneDriveUploader | None = None

    if provider == "onedrive":
        onedrive = OneDriveUploader(report_id=report_id)

    def _upload(file_path: str) -> str:
        if file_path in cache:
            return cache[file_path]
        for attempt in range(3):
            try:
                if provider == "onedrive":
                    url = onedrive.upload(file_path)
                elif provider == "imgbb":
                    if not imgbb_api_key:
                        raise ValueError("imgbb_api_key is required for imgbb provider")
                    url = upload_imgbb(file_path, imgbb_api_key)
                else:
                    url = upload_catbox(file_path)
                cache[file_path] = url
                print(f"  Uploaded: {Path(file_path).name} → {url}")
                time.sleep(0.3)
                return url
            except Exception as e:
                if attempt == 2:
                    print(f"  Upload failed after 3 attempts: {file_path} — {e}")
                    return ""
                time.sleep(2 ** attempt)
        return ""

    return _upload
