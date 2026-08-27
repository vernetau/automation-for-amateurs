# This python script will run in a temp k8s pod to seed Netbox with some basic data
import os
import sys
import yaml
import pynetbox
import time

# Set vars
NETBOX_URL = os.environ["NETBOX_URL"]
NETBOX_API_TOKEN = os.environ["NETBOX_API_TOKEN"]
GIT_ORGANIZATION = os.environ["GIT_ORGANIZATION"]
GIT_REPOSITORY = os.environ["GIT_REPOSITORY"]
GIT_USERNAME = os.environ["GIT_USERNAME"]
GIT_TOKEN = os.environ["GIT_TOKEN"]

# Create NetBox API client
nb = pynetbox.api(
    NETBOX_URL,
    token=NETBOX_API_TOKEN
)

def get_or_create(endpoint, search, data):
    """
    Get an existing NetBox object or create it if it does not exist.

    This function will take an endpoint, a search param and some data and 
    determine if the object exists. If it doesn't, then the object will be 
    created. Search must be included, otherwise lookups can not be performed.

    Args:
        endpoint: A pynetbox endpoint object (e.g. nb.dcim.devices,
            nb.ipam.prefixes, nb.dcim.interfaces).
        search (dict): Data used to search for the object. These values
            are passed directly to the endpoint's ``get()`` method.
        data (dict): Data used to create the object if no existing
            object is found. These values are passed directly to the
            endpoint's ``create()`` method.

    Returns:
        object: The existing or newly created pynetbox object.

    Side Effects:
        Prints a message indicating whether the object already existed or
        was created.

    Example:
        site = get_or_create(
            nb.dcim.sites,
            {
                "slug": "melbourne",
            },
            {
                "name": "Melbourne",
                "slug": "melbourne",
            }
        )
    """

    # Verify we were passed the search parameter
    if not search:
        raise ValueError("Search must be included as a lookup key")

    # Test if object exists
    obj = endpoint.get(**search)

    # Existing
    if obj:
        print(f"    Existing: {data.get('name', data.get('slug', 'object'))}")
        return obj

    # Create
    obj = endpoint.create(data)
    print(f"    Created: {obj.name if hasattr(obj, 'name') else obj}")
    return obj

def wait_for_sync(nb, data_source, timeout=300, poll_interval=3):
    """
    Poll a NetBox data source until its sync finishes or times out.

    Args:
        nb (object): A NetBox API client object.
        data_source (object): A NetBox data source object.
        timeout (int): The number of seconds to wait for the sync to finish.
        poll_interval (int): The number of seconds to wait between polling.

    Returns:
        bool: True if the sync finished successfully, False if it timed out.
    """

    elapsed = 0
    while elapsed < timeout:
        current = nb.core.data_sources.get(data_source.id)
        status = current.status.value  # e.g. "completed", "syncing", "failed", "new"
        if status == "completed":
            print(f"    {current.name}: sync completed")
            return True
        if status == "failed":
            print(f"    {current.name}: sync FAILED")
            return False
        time.sleep(poll_interval)
        elapsed += poll_interval
    print(f"  {data_source.name}: sync timed out after {timeout}s")
    return False

def main():
    # Get the seed data from file
    with open("/netbox-seed/data.yaml", "r") as f:
        data = yaml.safe_load(f)

    # Add Datasources
    data_sources = {}
    print("\nCreating Data Sources...")
    for data_source_data in data.get("data_sources", []):
        # Set URL
        url = f"https://github.com/{GIT_ORGANIZATION}/{GIT_REPOSITORY}.git"
        data_source = get_or_create(
            nb.core.data_sources,
            {
                "name": data_source_data["name"],
            },
            {
                "name": data_source_data["name"],
                "type": "git",
                "source_url": url,
                "description": data_source_data["description"],
                "parameters": {
                    "branch": data_source_data["branch"],
                    "username": GIT_USERNAME,
                    "password": GIT_TOKEN
                }
            }
        )

        # Run a sync for the datasource
        data_source.sync.create()

        data_sources[data_source_data["name"]] = data_source

    # Wait for all data sources to finish syncing
    print("\nWaiting for Data Source syncs to complete...")
    for name, data_source in data_sources.items():
        wait_for_sync(nb, data_source)

    # Sites
    sites = {}
    print("\nCreating sites...")
    for site_data in data.get("sites", []):
        site = get_or_create(
            nb.dcim.sites,
            {
                "slug": site_data["slug"],
            },
            {
                "name": site_data["name"],
                "slug": site_data["slug"],
            }
        )

        sites[site_data["slug"]] = site

    # RIRs
    rirs = {}
    print("\nCreating RIRs...")
    for rir_data in data.get("rirs", []):
        rir = get_or_create(
            nb.ipam.rirs,
            {
                "slug": rir_data["slug"],
            },
            {
                "name": rir_data["name"],
                "slug": rir_data["slug"],
                "description": rir_data["description"],
            }
        )

        rirs[rir_data["slug"]] = rir

    # ASNs
    asns = {}
    print("\nCreating ASNs...")
    for asn_data in data.get("asns", []):
        asn = get_or_create(
            nb.ipam.asns,
            {
                "asn": asn_data["asn"]
            },
            {
                "asn": asn_data["asn"],
                "rir": rirs[asn_data["rir"]].id,
                "description": asn_data["description"]
            }
        )

        asns[asn_data["asn"]] = asn

    # Route targets
    route_targets = {}
    print("\nCreating route targets...")
    for route_target_data in data.get("route_targets", []):
        route_target = get_or_create(
            nb.ipam.route_targets,
            {
                "name": route_target_data["name"]
            },
            {
                "name": route_target_data["name"]
            }
        )

        route_targets[route_target_data["name"]] = route_target

    # Prefixes
    prefixes = {}
    print("\nCreating prefixes...")
    for prefix_data in data.get("prefixes", []):
        prefix = get_or_create(
            nb.ipam.prefixes,
            {
                "prefix": prefix_data["prefix"],
            },
            {
                "prefix": prefix_data["prefix"],
                "description": prefix_data["description"]
            }
        )

        prefixes[prefix_data["prefix"]] = prefix

    # Get our data file ids
    custom_script_files = {}
    template_files = {}
    for data_file_data in nb.core.data_files.all():
        # Check for custom scripts
        if "custom_script" in data_file_data.path:
            custom_script_files[data_file_data.path] = data_file_data.id

        # Check for templates
        if "jinja-templates" in data_file_data.path:
            template_files[data_file_data.path] = data_file_data.id

    # Templates
    templates = {}
    print("\nCreating templates...")
    for template_data in data.get("templates", []):
        # First lets confirm we have the datafile
        new_file_name = "jinja-templates/" + template_data["file"]
        if new_file_name not in template_files:
            raise Exception(f"Data file {new_file_name} not found!")

        template = get_or_create(
            nb.extras.config_templates,
            {
                "name": template_data["name"],
            },
            {
                "name": template_data["name"],
                "data_source": data_sources["Automation For Amateurs Github Repo"].id,
                "template_code": "placeholder - will be overwritten on sync",
                "data_file": template_files[new_file_name],
                "auto_sync_enabled": True,
                "environment_params": {
                    "trim_blocks": True,
                    "lstrip_blocks": True
                }
            }
        )

        templates[template_data["name"]] = template


    print("\nRe-syncing data sources after template creation...")
    for name, data_source in data_sources.items():
        data_source.sync.create()

    for name, data_source in data_sources.items():
        wait_for_sync(nb, data_source)

    # Manufacturers
    manufacturers = {}
    print("\nCreating manufacturers...")
    for manufacturer_data in data.get("manufacturers", []):
        manufacturer = get_or_create(
            nb.dcim.manufacturers,
            {
                "slug": manufacturer_data["slug"],
            },
            {
                "name": manufacturer_data["name"],
                "slug": manufacturer_data["slug"],
            }
        )

        manufacturers[manufacturer_data["slug"]] = manufacturer

    # Device types
    device_types = {}
    print("\nCreating device types...")
    for device_type_data in data.get("device_types", []):
        # Need manufacturer for device types
        manufacturer = manufacturers[
            device_type_data["manufacturer"]
        ]

        device_type = get_or_create(
            nb.dcim.device_types,
            {
                "slug": device_type_data["slug"],
            },
            {
                "model": device_type_data["name"],
                "slug": device_type_data["slug"],
                "manufacturer": manufacturer.id,
                "u_height": device_type_data["u_height"],
            }
        )

        device_types[device_type_data["slug"]] = device_type

    # Device roles
    roles = {}
    print("\nCreating device roles...")
    for role_data in data.get("roles", []):
        # Check if config template is set
        template = None
        if role_data.get("config_template"):
            if templates.get(role_data["config_template"]):
                template = templates[role_data["config_template"]].id

        role = get_or_create(
            nb.dcim.device_roles,
            {
                "slug": role_data["slug"],
            },
            {
                "name": role_data["name"],
                "slug": role_data["slug"],
                "config_template": template
            }
        )

        roles[role_data["slug"]] = role

    # Devices
    devices = {}
    print("\nCreating devices...")
    for device_data in data.get("devices", []):
        # Get our required object first
        site = sites[
            device_data["site"]
        ]
        device_type = device_types[
            device_data["device_type"]
        ]
        role = roles[
            device_data["role"]
        ]

        device = get_or_create(
            nb.dcim.devices,
            {
                "name": device_data["name"],
            },
            {
                "name": device_data["name"],
                "device_type": device_type.id,
                "role": role.id,
                "site": site.id,
            }
        )

        devices[device_data["name"]] = device

    # Interfaces
    interfaces = {}
    print("\nCreating interfaces...")
    for interface_data in data.get("interfaces", []):
        # Get our required object first
        device = devices[
            interface_data["device"]
        ]

        # We need to check for parent int first
        parent_id = None
        if interface_data.get("parent"):
            parent = nb.dcim.interfaces.get(
                device_id=device.id,
                name=interface_data["parent"],
            )
            if not parent:
                raise ValueError(
                    f"Parent interface '{interface_data['parent']}' not found on device '{device.name}'"
                )
            parent_id = parent.id

        # Check if mtu is set
        mtu = None
        if interface_data.get("mtu"):
            mtu = interface_data["mtu"]

        interface = get_or_create(
            nb.dcim.interfaces,
            {
                "name": interface_data["name"],
                "device": device.name,
            },
            {
                "device": device.id,
                "name": interface_data["name"],
                "type": interface_data["type"],
                "description": interface_data.get("description", ""),
                "parent": parent_id,
                "mtu": mtu
            }
        )

        interfaces[f"{device.name}:{interface_data['name']}"] = interface

    # IP addresses
    ip_addresses = {}
    print("\nCreating IP addresses...")
    for ip_data in data.get("ip_addresses", []):
        # Build interface key so we can get the interface id from the array
        interface_key = (f"{ip_data['device']}:{ip_data['interface']}")

        # Get our required object first
        interface = interfaces[
            interface_key
        ]

        ip = get_or_create(
            nb.ipam.ip_addresses,
            {
                "address": ip_data["address"],
            },
            {
                "address": ip_data["address"],
                "assigned_object_type": "dcim.interface",
                "assigned_object_id": interface.id
            }
        )

        # Check if the IP is a primary for the device
        if ip_data.get("is_primary"):
            if ip_data["is_primary"] == True:
                device = nb.dcim.devices.get(name=ip_data['device'])
                device.primary_ip4 = ip.id
                device.save()

        ip_addresses[ip_data["address"]] = ip

    # Add L2VPN
    l2vpns = {}
    print("\nCreating L2VPNs...")
    for l2vpn_data in data.get("l2vpns", []):
        l2vpn = get_or_create(
            nb.vpn.l2vpns,
            {
                "slug": l2vpn_data["slug"],
            },
            {
                "name": l2vpn_data["name"],
                "slug": l2vpn_data["slug"],
                "description": l2vpn_data["description"],
                "type": l2vpn_data["type"],
                "import_targets": [
                    route_targets[x].id
                    for x in l2vpn_data.get("import_targets", [])
                ],
                "export_targets": [
                    route_targets[x].id
                    for x in l2vpn_data.get("export_targets", [])
                ],
            }
        )

        l2vpns[l2vpn_data["slug"]] = l2vpn
    
    # Add L2VPN Terminations
    l2vpn_terminations = {}
    print("\nCreating L2VPN Terminations...")
    for l2vpn_termination_data in data.get("l2vpn_terminations", []):
        l2vpn_termination = get_or_create(
            nb.vpn.l2vpn_terminations,
            {
                "l2vpn": l2vpn_termination_data["l2vpn"],
                "assigned_object_type": "dcim.interface",
                "assigned_object_id": interfaces[f"{l2vpn_termination_data['device']}:{l2vpn_termination_data['interface']}"].id
            },
            {
                "l2vpn": l2vpns[l2vpn_termination_data["l2vpn"]].id,
                "assigned_object_type": "dcim.interface",
                "assigned_object_id": interfaces[f"{l2vpn_termination_data['device']}:{l2vpn_termination_data['interface']}"].id
            }
        )

        l2vpn_terminations[f"{l2vpn_termination_data['device']}:{l2vpn_termination_data['interface']}"] = l2vpn_termination

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