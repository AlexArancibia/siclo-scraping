import os
import time
import requests
from dotenv import load_dotenv
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request

GOOGLE_TOKEN_URI = "https://oauth2.googleapis.com/token"
DRIVE_UPLOAD_URL = "https://www.googleapis.com/upload/drive/v3/files?uploadType=multipart"


def get_credentials():
    """Load OAuth creds using refresh token and auto-refresh."""
    creds = Credentials(
        None,
        refresh_token=os.environ["GOOGLE_REFRESH_TOKEN"],
        client_id=os.environ["GOOGLE_CLIENT_ID"],
        client_secret=os.environ["GOOGLE_CLIENT_SECRET"],
        token_uri=GOOGLE_TOKEN_URI,
        scopes=["https://www.googleapis.com/auth/drive.file"],
    )
    creds.refresh(Request())
    return creds


def upload_file(file_bytes, filename, mimetype, folder_id):
    """Upload a file to Google Drive using OAuth automatically refreshed creds."""
    creds = get_credentials()

    metadata = {
        "name": filename,
        "parents": [folder_id],
        "mimeType": mimetype
    }

    # Multipart upload
    boundary = "====BOUNDARY===="
    multipart_body = (
        f"--{boundary}\r\n"
        "Content-Type: application/json; charset=UTF-8\r\n\r\n"
        f"{metadata}\r\n"
        f"--{boundary}\r\n"
        f"Content-Type: {mimetype}\r\n\r\n"
    ).encode("utf-8") + file_bytes + f"\r\n--{boundary}--".encode("utf-8")

    headers = {
        "Authorization": f"Bearer {creds.token}",
        "Content-Type": f"multipart/related; boundary={boundary}",
    }

    response = requests.post(DRIVE_UPLOAD_URL, headers=headers, data=multipart_body)

    if not response.ok:
        raise Exception(f"Upload failed: {response.text}")

    return response.json()


if __name__ == "__main__":
    # Example usage:
    load_dotenv("../.env")
    folder_id = "1M10yylyExJjh8hbtJt7iKGxVo1CkdB2H"  # The folder you shared with your Google account
    filename = "../example.xlsx"
    mimetype = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

    # Example: load file into memory
    with open(filename, "rb") as f:
        contents = f.read()

    res = upload_file(contents, "example.xlsx", mimetype, folder_id)
    print("Uploaded:", res)
