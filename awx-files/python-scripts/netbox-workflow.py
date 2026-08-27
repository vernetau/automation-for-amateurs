#!/usr/bin/env python3
"""
netbox-workflow.py

Renders device configs from Netbox, commits any changes to a git branch,
pushes the branch, and opens (or reuses) a GitHub pull request.

Exit codes (preserved from the original script for AWX changed_when/failed_when):
  0 - success, no changes needed
  1 - failure
  2 - success, changes were made and a PR was created/exists
"""

import argparse
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

# -------
# Helpers
# -------
def run(cmd, cwd=None, check=True, capture=True):
    """Run a subprocess command and return the completed process."""
    result = subprocess.run(
        cmd,
        cwd=cwd,
        text=True,
        capture_output=capture,
    )
    if check and result.returncode != 0:
        print(f"Command failed: {' '.join(cmd)}", file=sys.stderr)
        if capture:
            print(result.stdout, file=sys.stderr)
            print(result.stderr, file=sys.stderr)
        sys.exit(1)
    return result


def load_text_file(file_path):
    """Return the contents of file_path, or None if it doesn't exist."""
    path = Path(file_path)
    if path.is_file():
        return path.read_text()
    return None

def http_request(url, method="GET", headers=None, json_body=None):
    """
    Minimal wrapper around urllib to mimic the bits of the `requests` module,
    without requiring the requests package to be installed in the EE. In a prod
    environment, the requests package is easier to install when creating your own
    Execution Environment.
 
    Returns (status_code, parsed_json_or_None, raw_text).
    """

    data = None
    headers = dict(headers or {})
    if json_body is not None:
        data = json.dumps(json_body).encode("utf-8")
        headers.setdefault("Content-Type", "application/json")
 
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
 
    try:
        with urllib.request.urlopen(req) as resp:
            raw = resp.read().decode("utf-8")
            status = resp.status
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8")
        status = exc.code
    except urllib.error.URLError as exc:
        print(f"Request to {url} failed: {exc}", file=sys.stderr)
        return None, None, ""
 
    try:
        parsed = json.loads(raw) if raw else None
    except json.JSONDecodeError:
        parsed = None
 
    return status, parsed, raw
 

# -------
# Main
# -------
def main():
    # Pull in device args
    parser = argparse.ArgumentParser()
    parser.add_argument("-d", dest="device_json", required=True, help="JSON array of devices, e.g. [{\"id\": 1, \"name\": \"router-01\"}]")
    args = parser.parse_args()

    # Validate we received a JSON array
    try:
        device_ids = json.loads(args.device_json)
    except json.JSONDecodeError as exc:
        print(f"Error: -d must be valid JSON ({exc})", file=sys.stderr)
        sys.exit(1)

    if not isinstance(device_ids, list):
        print("Error: -d must be a JSON array", file=sys.stderr)
        sys.exit(1)

    # Determine branch name
    if len(device_ids) > 1:
        branch_name = "netbox-updates"
    else:
        branch_name = device_ids[0]["name"]

    # Set vars
    git_username = os.environ.get("GIT_USERNAME")
    git_email = os.environ.get("GIT_EMAIL")
    git_token = os.environ.get("GIT_TOKEN")
    git_organization = os.environ.get("GIT_ORGANIZATION")
    git_repository = os.environ.get("GIT_REPOSITORY")
    netbox_url = os.environ.get("NETBOX_API").rstrip("/") + "/api"
    netbox_api_token = os.environ.get("NETBOX_TOKEN")

    git_pull_api_url = f"https://api.github.com/repos/{git_organization}/{git_repository}/pulls"
    repo_dir = Path(git_repository) / "device-renders"

    # Check if the repo has been cloned or not by looking for the directory
    if not repo_dir.is_dir():
        clone_url = f"https://{git_username}:{git_token}@github.com/{git_organization}/{git_repository}.git"
        result = run(["git", "clone", clone_url], check=False)
        if result.returncode != 0:
            print("Failed to clone the repository.", file=sys.stderr)
            print(result.stderr, file=sys.stderr)
            sys.exit(1)
        print("Successfully cloned the repository.")

    # Pull latest and reset local branches
    run(["git", "pull", "--prune"], cwd=repo_dir)
    run(["git", "checkout", "master"], cwd=repo_dir)

    local_branches_raw = run(["git", "branch"], cwd=repo_dir).stdout
    local_branches = [b.strip().lstrip("* ").strip() for b in local_branches_raw.splitlines() if b.strip()]

    for branch in local_branches:
        if branch != "master":
            run(["git", "branch", "-D", branch], cwd=repo_dir)
            print(f"Force deleted local branch: {branch}")

    # Check if the target branch exists on the remote
    remote_check = run(["git", "ls-remote", "--exit-code", "origin", branch_name], cwd=repo_dir, check=False)
    branch_existed = remote_check.returncode == 0

    if branch_existed:
        print(f"Branch '{branch_name}' exists on remote. Pulling latest changes...")
        run(["git", "checkout", branch_name], cwd=repo_dir)
        run(["git", "pull", "origin", branch_name], cwd=repo_dir)
    else:
        print(f"Branch '{branch_name}' does not exist on remote. Creating and switching to it...")
        run(["git", "checkout", "-b", branch_name], cwd=repo_dir)

    # -------
    # Render config for each device via Netbox, write into the repo
    # -------
    for device in device_ids:
        device_id = device.get("id")
        name = device.get("name")
        print(f"Processing device id={device_id} name={name}")

        # Render config via API call
        response = http_request(
            f"{netbox_url}/dcim/devices/{device_id}/render-config/",
            method="POST",
            headers={
                "Authorization": f"Token {netbox_api_token}",
                "Content-Type": "application/json",
                "Accept": "application/json; indent=4",
            },
        )

        # Check result
        if response[0] != 200:
            print(f"Request to {netbox_url}/dcim/devices/{device_id}/render-config/ failed: {response[2]}", file=sys.stderr)
            sys.exit(1)

        resp = response[1]

        try:
            rendered_config = resp.get("content", "")
        except AttributeError:
            print(f"Netbox returned empty for {name}'s rendered config. Skipping commit.", file=sys.stderr)
            sys.exit(1)

        render_path = repo_dir / f"{name}_render.conf"
        render_path.write_text(rendered_config)

        if not rendered_config:
            print(f"Netbox returned empty for {name}'s rendered config. Skipping commit.", file=sys.stderr)
            sys.exit(1)

        netbox_conf = load_text_file(render_path)
        github_file = repo_dir / f"{name}.conf"

        if netbox_conf is not None:
            # Overwrite the git file
            github_file.write_text(netbox_conf)
        else:
            print(f"Netbox configuration render not found for {name}. Skipping commit.", file=sys.stderr)
            sys.exit(1)

        # Remove temp render file
        render_path.unlink(missing_ok=True)

        # Remove empty lines from the final config file
        lines = github_file.read_text().splitlines()
        github_file.write_text("\n".join(line for line in lines if line.strip() != "") + "\n")

        print(f"Netbox configuration for {name} updated.")

    # -------
    # Commit, push, PR
    # -------
    run(["git", "config", "user.email", git_email], cwd=repo_dir, check=True)
    run(["git", "config", "user.name", git_username], cwd=repo_dir, check=True)

    run(["git", "add", "."], cwd=repo_dir)

    commit_msg = f"Netbox update at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    commit_result = run(["git", "commit", "-m", commit_msg], cwd=repo_dir, check=False)
    print(commit_result)

    if "nothing to commit" in commit_result.stdout:
        if not branch_existed:
            run(["git", "branch", "-D", branch_name], cwd=repo_dir)
        print("No changes to configuration found, git up to date with Netbox.")
        run(["git", "switch", "master"], cwd=repo_dir)
        sys.exit(0)

    print("Changes detected on Netbox")

    run(["git", "push", "origin", branch_name], cwd=repo_dir)

    # Look for an existing open PR for this branch
    print("Checking for existing PR")
    pr_resp = http_request(
        git_pull_api_url,
        headers={"Authorization": f"token {git_token}"},
        method="GET",
    )

    if pr_resp[0] != 200:
        print(f"Request to {git_pull_api_url} failed: {pr_resp[2]}", file=sys.stderr)
        sys.exit(1)

    pr_list = pr_resp[1]

    pull_request_number = None
    pull_request_url = None
    if isinstance(pr_list, list):
        for pr in pr_list:
            if branch_name in pr.get("title", ""):
                pull_request_number = pr.get("number")
                pull_request_url = pr.get("html_url")
                break

    if not pull_request_number:
        print(f"No pull request with the branch: '{branch_name}' found.")
        print("Creating PR")

        create_pr = http_request(
            git_pull_api_url,
            method="POST",
            headers={"Authorization": f"token {git_token}"},
            json_body={"title": branch_name, "head": branch_name, "base": "master"},
        )

        if create_pr[0] != 201:
            print(f"Request to {git_pull_api_url} failed: {create_pr[2]}", file=sys.stderr)
            sys.exit(1)

        create_resp = create_pr[1]
        pull_request_number = create_resp.get("number")
        pull_request_url = create_resp.get("html_url")
    else:
        print("PR already exists")

    if not pull_request_number:
        print("Error: Pull request creation failed. Response from server:", file=sys.stderr)
        run(["git", "switch", "master"], cwd=repo_dir)
        sys.exit(1)

    print(f"Pull request #{pull_request_number}: {pull_request_url}")

    run(["git", "switch", "master"], cwd=repo_dir)
    sys.exit(2)


if __name__ == "__main__":
    main()