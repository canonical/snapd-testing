import argparse
import os
import sys

from jira import JIRA


JIRA_BASE_URL = "https://warthogs.atlassian.net"

# Prepare parser
parser = argparse.ArgumentParser(description="Update Jira Epics Rank")
parser.add_argument("--file", required=True, help="Path to file with ordered epic keys")
args = parser.parse_args()

access_token_secret = os.environ.get("JIRA_ACCESS_TOKEN_SECRET")
access_token = os.environ.get("JIRA_ACCESS_TOKEN")
key_cert = os.environ.get("JIRA_KEY_CERT")

if not access_token_secret or not access_token or not key_cert:
    print(
        "Jira Secret token, Jira access token and Jira key certificated are required through env vars"
    )
    sys.exit(1)

# Read the certificate
with open(key_cert, "r", encoding='ascii') as file:
    cert_data = file.read()

# Read the epic list file
# The expected format is to have an id on each line (no empty lines allowed)
# MYID-112
# MYID-113
# ...
with open(args.file, "r", encoding='ascii') as f:
    epics = [line.strip() for line in f if line.strip()]

# Define OAuth 1.0 parameters
oauth_dict = dict(
    access_token=access_token,
    access_token_secret=access_token_secret,
    consumer_key="OauthKey",
    key_cert=cert_data,
)

# Define how to connect to Jira
jira = JIRA(options={"server": JIRA_BASE_URL}, oauth=oauth_dict)

# Rank all the epics following the order defined in the input file
for i in range(1, len(epics)):
    epic_to_rank = epics[i]
    prev_epic = epics[i - 1]

    jira.rank(epic_to_rank, prev_issue=prev_epic)

    print(f"Epic {epic_to_rank} moved after {prev_epic}")
