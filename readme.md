# VERNet - Automation for Amateurs - AusNOG 2026

## Overview

This project provides a self-contained lab environment that demonstates a simplifed version of VERNet's production network automation system. Further details about this project can be seen in the presentation notes shown at AusNOG 2026.

Automation for Amateurs shows a portable solution for network automation, demoing **network automation with NetBox as its source of truth and Ansible (AWX) running as the main orchestrator and automation engine**. A single install script will spin up instances of NetBox and AWX on Kubernetes, populates them with a sample network topology, workflows and credentials that will enable device configuration renders to be produced directly from NetBox. Any changes detected in these renders from the provisioned "golden" configurations will raise a PR for review. In a producton system, this PR can run validations, manditory checks, and configuration applications via GitHub actions or similar.

This is a **lab or demonstation project only**, and is not designed nor intended for production use. Secrets are auto generated, security is ignored and all data is fictionalised. Any use of this system is purely for educational or demonstrative purposes only.

> ⚠️ **Not for production use.** This project is not hardened and is not recommended for production environments. Review all code, scripts, and templates before running any part of it. No warranty or guarantee of any kind is provided.

## Components

- **NetBox** as a source of truth, along with a UI allowing custom provisioning scripts, configuation viewing, inventory among many others.
- **AWX** as the workflow orchestrator and UI for Ansible
- **Ansible** for configuration and exection of workflows between NetBox/Git/Network
- **Python** for custom scripts within NetBox, and for custom automation logic inside Ansible
- **Jinja** for configuration templates and custom logic inside Ansible playbooks
- **Bash Script** for the automated install of this system
- **Kubernetes/Helm** for the deployment platform and automated installation

## What it does

1. **Deploys NetBox and AWX** into a Kubernetes cluster using their official Helm charts.
2. **Seeds NetBox** with sample data, including: sites, a Juniper MX960 router device type, prefixes, an ASN, and four routers, plus a Jinja2 config-rendering template and a custom data source pointed at this repo.
3. **Seeds AWX** with an organization, a project (this repo), a NetBox-backed dynamic inventory, and a job template.
4. **Creates and links credentials** for NetBox, AWX and Git.
5. **Provides two NetBox custom scripts** for creating a Juniper device (with management/loopback interfaces and IPs auto-assigned) and provisioning a VPLS service on a router interface.
6. **Provides an AWX playbook + Python workflow** that renders each active Juniper device's config from NetBox, compares it against what's committed in the repo, and if there's a difference - pushes a branch and opens a GitHub pull request for review.

## Repository structure

| Path | Purpose |
|---|---|
| `install.sh` | Main entry point: deploys NetBox + AWX via Helm, creates Kubernetes secrets/configmaps, and runs the seed jobs. |
| `generate-secrets.sh` | Generates a `secrets.env` file of random passwords/keys (or prompts for your own values interactively). Is auto run via install.sh, but can also be run prior to install. |
| `versions.env` | Pinned chart/image versions for NetBox, AWX, Postgres, and Redis. |
| `netbox-values.yaml` | Helm value overrides for the `netbox/netbox` chart. See the offical NetBox chart if you wish to change other parameters |
| `awx.yaml` | AWX custom resource applied after the AWX operator is installed. |
| `ansible.cfg` | Enables the plugins for Ansible, including the dynamic NetBox inventory. |
| `collections/requirements.yml` | Ansible Galaxy collections required (`netbox.netbox`, `community.general`, `juniper.device`). |
| `netbox-seed/` | Kubernetes Job, seed data, and Python script used to populate NetBox on first install. |
| `awx-seed/` | Kubernetes Job, seed data, and Python script used to populate AWX on first install. |
| `awx-files/inventory/` | Dynamic Ansible inventory sourced from NetBox, filtered to active Juniper devices. Other inventories can be added as required. |
| `awx-files/playbooks/` | The `render-netbox-configuration.yaml` playbook run by the AWX job template. Other playbooks can be added as required. |
| `awx-files/python-scripts/` | `netbox-workflow.py` renders configs, diffs them against Git, and opens a PR on drift. Other scripts for config deployment, live config tracking and any other as required can be added. |
| `custom-scripts/` | NetBox custom scripts: `create_juniper_device.py` and `create_vpls_service.py`. These provide validation and UI inside NetBox for manipulating data. |
| `jinja-templates/` | Jinja2 templates NetBox uses to render configuration. In this example, a top template inherits from all the others to provide modular sections of the configuration. |
| `device-renders/` | Git-tracked rendered configs per device, our "golden" configs. These are generated by NetBox, and are intended to be the configurations that get pushed to the network. |

## Prerequisites

- A Kubernetes cluster and `kubectl` configured against it
- `helm`
- `python3`
- A GitHub repository (this one, forked/cloned, works) with a personal access token that has permission to push branches and open pull requests

## Getting started

1. **Clone this repository.**

2. **Upload to your own git organisation.** This will allow you to interact with the repo as intended. Simply cloning this repo will not work as this is meant as a demonstration only.

3. **Create a `git.env` file** in the repo root (this file is intentionally gitignored) with the credentials used by AWX and NetBox to connect to this repository. It must match the following layout:

   ```bash
   GIT_ORGANIZATION=your-org
   GIT_REPOSITORY=your-repo
   GIT_USERNAME=your-username
   GIT_EMAIL=you@example.com
   GIT_TOKEN=your-github-token
   ```

4. **(Optional) Provide your own secrets.** By default `install.sh` will call `generate-secrets.sh` to create a `secrets.env` file of random passwords/keys on first run. If you'd rather set your own, create `secrets.env` yourself with the same variable names before running the installer. If the file doesn't exist prior to running the installer for the first time, prompts will be given to provide variables. These can still be auto generated by hitting enter.

5. **Run the installer:**

   ```bash
   ./install.sh
   ```

   By default this deploys into the `automation-for-amateurs` namespace. Override with:

   ```bash
   NAMESPACE=my-namespace ./install.sh
   ```

   The script will:
   - Add/update the NetBox and AWX-operator Helm repos
   - Install NetBox and AWX
   - Wait for pods to become ready
   - Create the NetBox and AWX API tokens, Git credentials, and device credentials as Kubernetes secrets
   - Run the NetBox and AWX seed jobs and print their logs

   NOTE: This script can take some time to spin up the pods, and populate all the data. Please allow 10-15 minutes for the job to complete. Job logs are printed to the terminal as the script executes.

6. **Setup Kubernetes ingress** via whatever method you see fit. For the purposes of this lab, port-forwarding was most commonly used.

7. **Log in to NetBox and AWX** using the admin passwords from `secrets.env`.

8. **Launch the "Render Netbox Configuration" job template in AWX** to render configs from NetBox and open a pull request against this repo if anything has drifted from `device-renders/`. On the first run of this job, configurations will be added. Device names matching those in NetBox must be added as 'extra_vars' when running the AWX template.

## Cleaning up

Tear down the namespace to remove everything the installer created:

```bash
kubectl delete namespace automation-for-amateurs
```

(or your custom `$NAMESPACE`).