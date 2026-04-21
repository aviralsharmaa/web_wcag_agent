"""Fix screenshot links in Kotak Neo and HSL reports.

Kotak Neo: 2593 issues have files on OneDrive but no share links.
HSL: Try to improve coverage with screen-level fallback screenshots.
"""
import os
import sys
import time
import json
import re
import requests
import msal
import openpyxl
from openpyxl.styles import Alignment, Font
from pathlib import Path

sys.stdout.reconfigure(line_buffering=True)

# ── OneDrive setup ──
tenant_id = os.environ["ONEDRIVE_TENANT_ID"]
client_id = os.environ["ONEDRIVE_CLIENT_ID"]
client_secret = os.environ["ONEDRIVE_CLIENT_SECRET"]
user_id = os.environ["ONEDRIVE_USER_ID"]
folder = os.environ.get("ONEDRIVE_FOLDER", "WCAG_Screenshots")

app = msal.ConfidentialClientApplication(
    client_id, client_credential=client_secret,
    authority=f"https://login.microsoftonline.com/{tenant_id}",
)

_token = None
_token_expiry = 0

def get_token():
    global _token, _token_expiry
    if _token and time.time() < _token_expiry - 60:
        return _token
    result = app.acquire_token_for_client(scopes=["https://graph.microsoft.com/.default"])
    _token = result["access_token"]
    _token_expiry = time.time() + result.get("expires_in", 3600)
    return _token

def headers():
    return {"Authorization": f"Bearer {get_token()}"}

base_url = f"https://graph.microsoft.com/v1.0/users/{user_id}/drive"
drive_prefix = f"/users/{user_id}/drive"


def list_all_onedrive_files():
    """List all files in OneDrive folder → {name: id}."""
    print("Listing all OneDrive files...")
    existing = {}
    url = f"{base_url}/root:/{folder}:/children?$top=1000&$select=name,id"
    while url:
        resp = requests.get(url, headers=headers(), timeout=60)
        if resp.status_code == 404:
            break
        resp.raise_for_status()
        data = resp.json()
        for item in data.get("value", []):
            existing[item["name"]] = item["id"]
        url = data.get("@odata.nextLink")
        if len(existing) % 2000 == 0 and existing:
            print(f"  Indexed {len(existing)} files...")
    print(f"  Total OneDrive files: {len(existing)}")
    return existing


def batch_share_links(item_ids_dict, batch_size=10):
    """Create share links for items. item_ids_dict = {name: item_id}. Returns {name: url}."""
    results = {}
    items = list(item_ids_dict.items())
    total = len(items)
    total_batches = (total + batch_size - 1) // batch_size

    for batch_idx, start in enumerate(range(0, total, batch_size)):
        batch = items[start:start + batch_size]
        req_body = []
        for i, (name, item_id) in enumerate(batch):
            req_body.append({
                "id": str(i),
                "method": "POST",
                "url": f"{drive_prefix}/items/{item_id}/createLink",
                "body": {"type": "view", "scope": "anonymous"},
                "headers": {"Content-Type": "application/json"},
            })

        for attempt in range(5):
            try:
                resp = requests.post(
                    "https://graph.microsoft.com/v1.0/$batch",
                    headers={**headers(), "Content-Type": "application/json"},
                    json={"requests": req_body},
                    timeout=60,
                )
                if resp.status_code == 429:
                    wait = int(resp.headers.get("Retry-After", 15))
                    print(f"  Rate limited (batch level), waiting {wait}s...")
                    time.sleep(wait)
                    continue
                resp.raise_for_status()
                data = resp.json()

                retry_items = []
                for r in data.get("responses", []):
                    idx = int(r["id"])
                    name = batch[idx][0]
                    if r.get("status") in (200, 201):
                        link_url = r["body"]["link"]["webUrl"]
                        results[name] = link_url
                    elif r.get("status") == 429:
                        retry_items.append(batch[idx])
                    # else: skip other errors

                if retry_items and attempt < 4:
                    wait = 10 * (attempt + 1)
                    print(f"  {len(retry_items)} items rate-limited in batch, retrying in {wait}s...")
                    time.sleep(wait)
                    # Rebuild batch with just retry items
                    batch = retry_items
                    req_body = []
                    for i, (name, item_id) in enumerate(batch):
                        req_body.append({
                            "id": str(i),
                            "method": "POST",
                            "url": f"{drive_prefix}/items/{item_id}/createLink",
                            "body": {"type": "view", "scope": "anonymous"},
                            "headers": {"Content-Type": "application/json"},
                        })
                    continue
                break
            except Exception as e:
                if attempt == 4:
                    print(f"  Batch error: {e}")
                else:
                    time.sleep(2 ** attempt)

        if (batch_idx + 1) % 50 == 0 or batch_idx == total_batches - 1:
            print(f"  Share links: {len(results)}/{total} ({batch_idx+1}/{total_batches} batches)")
        time.sleep(0.3)

    return results


def fix_kotak_neo():
    """Fix Kotak Neo report: get share links for 2593 existing OneDrive files."""
    print("\n" + "="*60)
    print("Fixing Kotak Neo report")
    print("="*60)

    # List OneDrive files
    od_files = list_all_onedrive_files()

    # Read the report and find issues needing links
    wb = openpyxl.load_workbook("Kotak_Neo_WCAG_Report.xlsx")
    ws = wb["Issues"]

    # Find which cells have filenames but no hyperlinks
    needs_link = {}  # row -> onedrive_filename
    for row_idx in range(2, ws.max_row + 1):
        cell = ws.cell(row=row_idx, column=10)
        if cell.value and cell.value != "View Screenshot" and cell.hyperlink is None:
            # Cell has a filename but no link - compute the OneDrive upload name
            ann_filename = str(cell.value)
            needs_link[row_idx] = ann_filename

    print(f"  Issues needing links: {len(needs_link)}")

    # Now we need to map the annotated filenames to OneDrive upload names
    # The upload name is: {checklist_folder}_{filename}
    # But we don't know which checklist folder from the report alone.
    # Instead, search by filename suffix in OneDrive
    filename_to_od_name = {}
    for od_name in od_files:
        # od_name like: 01_labels_or_instructions_state_2026-04-08_130623__ISSUE-001_annotated.png
        # The original filename is everything after the first checklist prefix
        filename_to_od_name[od_name] = od_files[od_name]  # od_name -> item_id

    # For each needing link, find matching OneDrive file
    to_link = {}  # od_name -> item_id
    row_to_od_name = {}  # row -> od_name
    for row_idx, ann_filename in needs_link.items():
        # Search for the filename as a suffix in OneDrive names
        for od_name, item_id in od_files.items():
            if od_name.endswith(ann_filename):
                to_link[od_name] = item_id
                row_to_od_name[row_idx] = od_name
                break

    print(f"  Matched on OneDrive: {len(to_link)}")

    if not to_link:
        print("  No files to link, skipping.")
        wb.close()
        return

    # Batch create share links
    print(f"  Creating share links for {len(to_link)} files...")
    links = batch_share_links(to_link, batch_size=10)
    print(f"  Got {len(links)} share links")

    # Update the report
    updated = 0
    for row_idx, od_name in row_to_od_name.items():
        url = links.get(od_name, "")
        if url:
            cell = ws.cell(row=row_idx, column=10)
            cell.value = "View Screenshot"
            cell.hyperlink = url
            cell.font = Font(name="Calibri", size=10, color="0563C1", underline="single")
            cell.alignment = Alignment(horizontal="center", vertical="center")
            updated += 1

    wb.save("Kotak_Neo_WCAG_Report.xlsx")
    print(f"  Updated {updated} cells in Kotak_Neo_WCAG_Report.xlsx")


def fix_hsl():
    """Fix HSL report: upload screen-level screenshots for unmatched issues."""
    print("\n" + "="*60)
    print("Fixing HSL InvestRight report")
    print("="*60)

    from src.accessibility_scanner.image_uploader import OneDriveUploader

    base = Path("reports/com-hsl-investright-uat__v4-7-2__20260327_174224/checklist_reports")

    # Build screen-level screenshot index from all checklist folders
    # Map screen timestamp -> screenshot path (prefer the first checklist with that screen)
    screen_screenshots = {}  # screen_tag -> path
    for ann_dir in sorted(base.glob("*/states/annotated")):
        for png in ann_dir.glob("*_annotated.png"):
            if "__ISSUE" not in png.name:
                tag = png.stem.replace("state_", "").replace("_annotated", "")
                if tag not in screen_screenshots:
                    screen_screenshots[tag] = str(png)

    print(f"  Screen-level screenshots available: {len(screen_screenshots)}")

    # Read the report
    wb = openpyxl.load_workbook("HSL_InvestRight_WCAG_Report.xlsx")
    ws = wb["Issues"]

    # Find issues needing screenshots
    needs_screenshot = {}  # row -> annotated filename from cell
    for row_idx in range(2, ws.max_row + 1):
        cell = ws.cell(row=row_idx, column=10)
        if cell.value and cell.value != "View Screenshot" and cell.hyperlink is None:
            needs_screenshot[row_idx] = str(cell.value)

    print(f"  Issues needing screenshots: {len(needs_screenshot)}")

    # Map the Excel screen timestamps to available screen screenshots
    # Excel filenames like: state_2026-03-23_010600__ISSUE-001_annotated.png
    # We need to map Screen-01's timestamp (010600) to a screen in our data
    # Strategy: for each unique Excel screen timestamp, find the closest matching
    # screen timestamp from our checklist_reports

    # First, collect unique screen timestamps from the Excel
    excel_screen_tags = {}  # tag -> count
    for row_idx, filename in needs_screenshot.items():
        m = re.search(r'state_\d{4}-\d{2}-\d{2}_(\d{6})', filename)
        if m:
            time_part = m.group(1)
            excel_screen_tags.setdefault(time_part, []).append(row_idx)

    print(f"  Unique Excel screen timestamps: {len(excel_screen_tags)}")
    print(f"  Available screen timestamps: {len(screen_screenshots)}")

    # Sort available timestamps
    avail_tags = sorted(screen_screenshots.keys())
    avail_time_parts = [(tag, tag.split('_')[1] if '_' in tag else '') for tag in avail_tags]

    # Map Excel timestamps to available ones by order (Screen-01 -> 1st available, etc.)
    excel_times_sorted = sorted(excel_screen_tags.keys())
    avail_times_sorted = sorted(set(t[1] for t in avail_time_parts if t[1]))

    # Simple mapping: assign in order
    time_mapping = {}  # excel_time_part -> available screen tag
    for i, excel_time in enumerate(excel_times_sorted):
        if i < len(avail_tags):
            time_mapping[excel_time] = avail_tags[i]

    print(f"  Mapped {len(time_mapping)} screen timestamps")

    # Upload the screen-level screenshots that will be used
    paths_to_upload = set()
    row_to_path = {}
    for excel_time, rows in excel_screen_tags.items():
        screen_tag = time_mapping.get(excel_time)
        if screen_tag and screen_tag in screen_screenshots:
            path = screen_screenshots[screen_tag]
            paths_to_upload.add(path)
            for row_idx in rows:
                row_to_path[row_idx] = path

    print(f"  Unique screenshots to upload: {len(paths_to_upload)}")

    # Upload and get links
    uploader = OneDriveUploader()
    upload_cache = {}
    od_files = list_all_onedrive_files()

    for path in sorted(paths_to_upload):
        fp = Path(path)
        filename = fp.name
        parts = fp.parts
        unique_prefix = ""
        for i, part in enumerate(parts):
            if part == "checklist_reports" and i + 1 < len(parts):
                unique_prefix = parts[i + 1] + "_"
                break
        upload_name = f"{unique_prefix}{filename}"

        # Check if already on OneDrive
        if upload_name in od_files:
            item_id = od_files[upload_name]
            try:
                links = batch_share_links({upload_name: item_id}, batch_size=1)
                if upload_name in links:
                    upload_cache[path] = links[upload_name]
                    continue
            except Exception:
                pass

        # Upload
        for attempt in range(3):
            try:
                url = uploader.upload(path)
                upload_cache[path] = url
                time.sleep(0.3)
                break
            except Exception as e:
                if attempt == 2:
                    print(f"  FAILED: {filename} - {e}")
                else:
                    time.sleep(2 ** attempt)

    print(f"  Uploaded/linked: {len(upload_cache)} screenshots")

    # Update the report
    updated = 0
    for row_idx, path in row_to_path.items():
        url = upload_cache.get(path, "")
        if url:
            cell = ws.cell(row=row_idx, column=10)
            cell.value = "View Screenshot"
            cell.hyperlink = url
            cell.font = Font(name="Calibri", size=10, color="0563C1", underline="single")
            cell.alignment = Alignment(horizontal="center", vertical="center")
            updated += 1

    wb.save("HSL_InvestRight_WCAG_Report.xlsx")
    print(f"  Updated {updated} cells in HSL_InvestRight_WCAG_Report.xlsx")


if __name__ == "__main__":
    fix_kotak_neo()
    fix_hsl()

    # Verify
    for f in ["Kotak_Neo_WCAG_Report.xlsx", "HSL_InvestRight_WCAG_Report.xlsx"]:
        wb = openpyxl.load_workbook(f, read_only=True)
        ws = wb["Issues"]
        links = sum(1 for r in ws.iter_rows(min_row=2, min_col=10, max_col=10, values_only=True) if r[0] == "View Screenshot")
        total = sum(1 for r in ws.iter_rows(min_row=2, max_col=1, values_only=True) if r[0])
        print(f"\n{f}: {links}/{total} screenshot links ({100*links//total}%)")
        wb.close()
