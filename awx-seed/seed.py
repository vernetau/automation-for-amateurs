# This python script will run in a temp k8s pod to seed Netbox with some basic data
import os
import sys
import yaml
import requests
import json
import time

# Set vars
AWX_URL = os.environ["AWX_URL"]
AWX_TOKEN = os.environ["AWX_TOKEN"]
NETBOX_URL = os.environ["NETBOX_URL"]
NETBOX_API_TOKEN = os.environ["NETBOX_API_TOKEN"]
GIT_ORGANIZATION = os.environ["GIT_ORGANIZATION"]
GIT_REPOSITORY = os.environ["GIT_REPOSITORY"]
GIT_USERNAME = os.environ["GIT_USERNAME"]
GIT_TOKEN = os.environ["GIT_TOKEN"]
DEVICE_USERNAME = os.environ["DEVICE_USERNAME"]
DEVICE_PASSWORD = os.environ["DEVICE_PASSWORD"]

headers = {
    "Authorization": f"Bearer {AWX_TOKEN}",
    "Content-Type": "application/json",
}

def get_or_create(endpoint, name, data):
    """
    Get an existing AWX object or create it if it does not exist.
    
    This function will take an endpoint, a search param and some data and
    determine if the object exists. If it doesn't, then the object will be
    created.

    Args:
        endpoint: An AWX endpoint object (e.g. users,
            projects).
        name (str): The name of the object to search for.
        data (dict): Data used to create the object if no existing
            object is found. These values are passed directly to the
            POST method.

    Returns:
        object: The existing or newly created object.

    Side Effects:
        Prints a message indicating whether the object already existed or
        was created.

    Example:
        projects = get_or_create(
            projects,
            "New Project",
            {
                "name": "New Project",
                "description": "This is a new project",
                "scm_type": "git",
                "scm_url": "https://github.com/username/repo.git",
                "scm_branch": "master",
                "scm_clean": True
            },
        )
    """

    # Make the GET request first
    response = requests.get(
        f"{AWX_URL}/api/v2/{endpoint}/",
        headers=headers,
        params={"name":name}
    )

    # Print the error body before raising, so we can see what AWX rejected
    if not response.ok:
        print(f"    ERROR creating {name}: {response.status_code}")
        print(f"    Response: {response.text}")

    # Get the results
    response.raise_for_status()
    results = response.json()["results"]

    # Check if it exists
    if results:
        print(f"    Existing: {name}")
        return results[0]

    # If not, create it
    response = requests.post(
        f"{AWX_URL}/api/v2/{endpoint}/",
        headers=headers,
        json=data
    )

    # Print the error body before raising, so we can see what AWX rejected
    if not response.ok:
        print(f"    ERROR creating {name}: {response.status_code}")
        print(f"    Response: {response.text}")
    
    # Get the results
    response.raise_for_status()
    obj = response.json()

    print(f"    Created: {name}")
    return obj

def wait_for_inventory_sync(inventory_update_id, headers, timeout=300, poll_interval=3):
    """
    Poll an AWX inventory update job until it finishes or times out.
    
    Args:
        inventory_update_id (int): The ID of the inventory update job.
        headers (dict): A dictionary of headers to use for the request.
        timeout (int): The number of seconds to wait for the sync to finish.
        poll_interval (int): The number of seconds to wait between polling.

    Returns:
        bool: True if the sync finished successfully, False if it timed out.
    """
    elapsed = 0
    url = f"{AWX_URL}/api/v2/inventory_updates/{inventory_update_id}/"
    while elapsed < timeout:
        r = requests.get(url, headers=headers)
        r.raise_for_status()
        result = r.json()
        status = result["status"]

        if status == "successful":
            print(f"        Inventory update: sync completed")
            return True
        if status in ("failed", "error", "canceled"):
            print(f"        Inventory update: sync {status}")
            return False

        time.sleep(poll_interval)
        elapsed += poll_interval

    print(f"    inventory update {inventory_update_id}: sync timed out after {timeout}s")
    return False

def main():
    # Get the seed data from file
    with open("/awx-seed/data.yaml", "r") as f:
        data = yaml.safe_load(f)

    # Organizations
    print("\nCreating Organizations...")
    organizations = {}
    for organization_data in data.get("organizations", []):
        organization = get_or_create(
            "organizations",
            organization_data["name"],
            organization_data
        )

        organizations[organization_data["name"]] = organization

        # Look up the default "Ansible Galaxy" credential ID, this is required for connecting to Ansible Galaxy
        r = requests.get(
            f"{AWX_URL}/api/v2/credentials/",
            headers=headers,
            params={"name": "Ansible Galaxy"}
        )
        galaxy_cred_id = r.json()["results"][0]["id"]

        # Associate it with our org
        r = requests.post(
            f"{AWX_URL}/api/v2/organizations/{organization['id']}/galaxy_credentials/",
            headers=headers,
            json={"id": galaxy_cred_id}
        )

    # Create Netbox credential type. Required for adding our url/token for Netbox connection
    print("\nCreating Netbox Credential Type...")
    credential_type_data = {
        "name": "Netbox",
        "kind": "cloud",
        "inputs": {
            "fields": [
                {"id": "url", "type": "string", "label": "Netbox URL"},
                {"id": "token", "type": "string", "label": "Netbox Token", "secret": True}
            ],
            "required": ["url", "token"]
        },
        "injectors": {
            "env": {
                "NETBOX_API": "{{ url }}",
                "NETBOX_TOKEN": "{{ token }}"
            }
        }
    }

    credential_type = get_or_create(
        "credential_types",
        credential_type_data["name"],
        credential_type_data
    )
    netbox_cred_type_id = credential_type["id"]

    # Create Github credential type. Required for adding our git vars to our python scripts. This is separate to the Git source credential type
    print("\nCreating Github Credential Type...")
    git_credential_type_data = {
        "name": "GitHub",
        "kind": "cloud",
        "inputs": {
            "fields": [
                {"id": "organization", "type": "string", "label": "Git Organization"},
                {"id": "repository", "type": "string", "label": "Git Repository"},
                {"id": "username", "type": "string", "label": "Git Username",},
                {"id": "password", "type": "string", "label": "Git Password", "secret": True},
                {"id": "token", "type": "string", "label": "Git Token", "secret": True}
            ],
            "required": ["organization", "repository", "username", "password", "token"]
        },
        "injectors": {
            "env": {
                "GIT_ORGANIZATION": "{{ organization }}",
                "GIT_REPOSITORY": "{{ repository }}",
                "GIT_USERNAME": "{{ username }}",
                "GIT_TOKEN": "{{ token }}"
            }
        }
    }

    git_credential_type = get_or_create(
        "credential_types",
        git_credential_type_data["name"],
        git_credential_type_data
    )
    git_cred_type_id = git_credential_type["id"]

    # Create the credentials
    print("\nCreating GitHub source control credentials...")
    credentials = {}
    github_credential_data = {
        "name": "GitHub (Source Control)",
        "organization": organizations["Automation-For-Amateurs"]["id"],
        "credential_type": 2,
        "inputs": {
            "username": GIT_USERNAME,
            "password": GIT_TOKEN
        }
    }

    github_credential = get_or_create(
        "credentials",
        github_credential_data["name"],
        github_credential_data
    )

    credentials[github_credential_data["name"]] = github_credential

    # Create the GitHub credential for bash scripts
    print("\nCreating GitHub (playbooks) credential...")
    git_credential_data = {
        "name": "GitHub (playbooks)",
        "organization": organizations["Automation-For-Amateurs"]["id"],
        "credential_type": git_cred_type_id,
        "inputs": {
            "organization": GIT_ORGANIZATION,
            "repository": GIT_REPOSITORY,
            "username": GIT_USERNAME,
            "password": GIT_TOKEN,
            "token": GIT_TOKEN
        }
    }

    git_credential = get_or_create(
        "credentials",
        git_credential_data["name"],
        git_credential_data
    )
    credentials[git_credential_data["name"]] = git_credential

    # Create the Netbox credential
    print("\nCreating Netbox Credential...")
    netbox_credential_data = {
        "name": "Netbox",
        "organization": organizations["Automation-For-Amateurs"]["id"],
        "credential_type": netbox_cred_type_id,
        "inputs": {
            "url": NETBOX_URL,
            "token": NETBOX_API_TOKEN
        }
    }

    netbox_credential = get_or_create(
        "credentials",
        netbox_credential_data["name"],
        netbox_credential_data
    )
    credentials[netbox_credential_data["name"]] = netbox_credential

    # Create the Device credential
    print("\nCreating Device Credential...")
    device_credential_data = {
        "name": "Device",
        "organization": organizations["Automation-For-Amateurs"]["id"],
        "credential_type": 1,
        "inputs": {
            "username": DEVICE_USERNAME,
            "password": DEVICE_PASSWORD
        }
    }

    device_credential = get_or_create(
        "credentials",
        device_credential_data["name"],
        device_credential_data
    )
    credentials[device_credential_data["name"]] = device_credential

    # Projects
    print("\nCreating Projects...")
    projects = {}
    for project_data in data.get("projects", []):
        # Map org from name to id
        project_data["organization"] = organizations[project_data["organization"]]["id"]
        project_data["scm_url"] = f"https://github.com/{GIT_ORGANIZATION}/{GIT_REPOSITORY}.git"
        project_data["credential"] = credentials["GitHub (Source Control)"]["id"]

        project = get_or_create(
            "projects",
            project_data["name"],
            project_data
        )

        projects[project_data["name"]] = project

    # Inventories
    print("\nCreating Inventories...")
    inventories = {}
    for inventory_data in data.get("inventories", []):
        # Map org from name to id
        inventory_data["organization"] = organizations[inventory_data["organization"]]["id"]

        # Get file location and unset from data
        inventory_file = inventory_data["inventory_file"]
        del inventory_data["inventory_file"]

        inventory = get_or_create(
            "inventories",
            inventory_data["name"],
            inventory_data
        )

        # Now we can create the inventory source
        inventory_source_name = f"{inventory_data['name']} Source"
        inventory_source = get_or_create(
            "inventory_sources",
            inventory_source_name,
            {
                "name": inventory_source_name,
                "source": "scm",
                "inventory": inventory["id"],
                "source_project": projects[inventory_data["project"]]["id"],
                "source_path": inventory_file,
                "overwrite": True,
                "credential": credentials["Netbox"]["id"]
            }
        )

        # Sync our new inventory
        r = requests.post(
            f"{AWX_URL}/api/v2/inventory_sources/{inventory_source["id"]}/update/",
            headers=headers
        )

        r.raise_for_status()
        inventory_update_id = r.json()["inventory_update"]

        print(f"    Waiting for inventory sync: {inventory_source_name}...")
        wait_for_inventory_sync(inventory_update_id, headers)

        inventories[inventory_data["name"]] = inventory

    # Templates
    print("\nCreating Templates...")
    templates = {}
    for template_data in data.get("templates", []):
        # Map vars
        template_data["project"] = projects[template_data["project"]]["id"]
        template_data["inventory"] = inventories[template_data["inventory"]]["id"]
        template_data["extra_vars"] = json.dumps({"nodes": None})
        template_data["playbook"] = "awx-files/playbooks/" + template_data["playbook"]

        template_credentials = template_data.pop("credentials")
        
        template = get_or_create(
            "job_templates",
            template_data["name"],
            template_data
        )

        # Now need to add the credentials to the template
        for cred in template_credentials:
            r = requests.post(
                f"{AWX_URL}/api/v2/job_templates/{template['id']}/credentials/",
                headers=headers,
                data=json.dumps({
                    "id": credentials[cred]["id"]
                })
            )

        templates[template_data["name"]] = template


# Main
if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(
            f"\nERROR: {e}",
            file=sys.stderr
        )

        sys.exit(1)