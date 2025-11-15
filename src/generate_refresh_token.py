# generates a refresh token to pass as an env variable for the scraping script
# RUN ONLY ONCE IN LOCAL

from google_auth_oauthlib.flow import InstalledAppFlow

def generate_refresh_token():
    flow = InstalledAppFlow.from_client_secrets_file(
        "../client_secret.json",  # downloaded earlier
        scopes=["https://www.googleapis.com/auth/drive.file"],
    )
    creds = flow.run_local_server(port=0)
    print("ACCESS TOKEN:", creds.token)
    print("REFRESH TOKEN:", creds.refresh_token)
    print("CLIENT ID:", creds.client_id)
    print("CLIENT SECRET:", creds.client_secret)

generate_refresh_token()
