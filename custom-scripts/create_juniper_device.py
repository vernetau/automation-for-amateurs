from extras.scripts import *
from dcim.models import Device, Interface, Site, DeviceType, DeviceRole
from dcim.choices import DeviceStatusChoices
from ipam.models import IPAddress, Prefix
from django.core.exceptions import ValidationError
from django.db import transaction

class CreateJuniperDevice(Script):
    class Meta:
        name = "Create a new Juniper device"
        description = "This script will create a new Juniper device. The script will add the device to the site, and create any management/loopback interfaces as required."
        commit_default = False
        scheduling_enabled = False

    device_name = StringVar(
        label="Device Name",
        description="Name for the new device. Must not already exist in Netbox.",
        required=True
    )

    site = ObjectVar(
        model=Site,
        label="Site",
        description="Site the device will be added to. The site must be active.",
        query_params={
            'status': 'active'
        },
        required=True
    )

    status = ChoiceVar(
        choices=DeviceStatusChoices,
        label="Device Status",
        required=True,
        default=DeviceStatusChoices.STATUS_ACTIVE,
        description="Status of the device."
    )

    type = ObjectVar(
        model=DeviceType,
        label="Device Type",
        description="Select the type of device to create",
        required=True,
        query_params={
            'manufacturer': 'juniper'
        }
    )

    ip_address = StringVar(
        label="Desired Management IP (Optional)",
        description="Enter an available IP within the selected prefix for the management interface. This interface must not be already assigned and must be within the selected prefix. If no option is specified, the first free IP will be assigned.",
        required=False
    )

    def run(self, data, commit):
        device_name = data['device_name']

        # Check if the device already exists
        try:
            Device.objects.get(name=device_name)
            self.log_failure("Device already exists in Netbox.")
            return  # stop the script
        except Device.DoesNotExist:
            pass
        
        # Build the available IP choices once when the script class is loaded
        parent_prefix = Prefix.objects.get(prefix="192.168.0.0/24")

        # Check if we have free IPs in our prefix
        available_ips = list(parent_prefix.get_available_ips())
        available_ips_str = [str(ip) for ip in available_ips]
        if not available_ips:
            self.log_failure("No free IP addresses available in the selected prefix.")
            return

        # Check if we were passed an IP, if so check its in the prefix
        if data['ip_address']:
            if data['ip_address'] not in available_ips_str:
                self.log_failure(f"IP address {data['ip_address']} is not available in the selected prefix. Please select another IP address, or leave blank to use the first available IP.")
                return
        else:
            data['ip_address'] = str(available_ips[0])

        self.log_info(f"Using IP address {data['ip_address']} for the management interface.")
            
        # Get role
        role = DeviceRole.objects.get(slug="router")

        # Create the device
        device = Device(
            name=device_name,
            site=data['site'],
            status=data['status'],
            device_type=data['type'],
            role=role
        )
    
        # Create the management interface
        # Set interface name
        int_name = "fxp0"
        unit_name = "fxp0.0"
        
        # Physcal interface
        management_interface = Interface(
            name=int_name,
            type="1000base-t",
            speed=1000000,
            duplex="full",
            mgmt_only=True
        )

        # Unit
        management_interface_unit = Interface(
            name=unit_name,
            type="virtual",
            speed=1000000,
            duplex="full",
            mgmt_only=True,
            parent=management_interface,
            description="Management interface"
        )

        # Management ip
        # Need to get subnet from the child prefix
        mask = parent_prefix.mask_length

        # Build management IP
        management_ip = IPAddress(
            address=str(data['ip_address']) + "/" + str(mask),
            status="active",
            description=device.name + " " + management_interface_unit.name
        )

        # Need to add the loopback interfaces
        loopback_interface = Interface(
            name="lo0",
            type="virtual",
            duplex="full"
        )

        loopback_interface_unit = Interface(
            name="lo0.0",
            type="virtual",
            duplex="full",
            parent=loopback_interface,
            description="Loopback interface"
        )

        # Lets now build the loopback IP
        loopback_prefix_cidr = "10.240.0.0/24"
        loopback_prefix = Prefix.objects.get(prefix=loopback_prefix_cidr)
        available_ips = list(loopback_prefix.get_available_ips())
        assigned_ip = str(available_ips[0])
        loopback_ip = IPAddress(
            address=str(assigned_ip) + "/32",
            status="active",
            description=device.name + " loopback IP"
        )

        # COMMIT STARTS HERE
        if commit:
            try:
                with transaction.atomic():
                    # Create the device
                    device.full_clean()
                    device.save()

                    device.save()
                    self.log_success(f"Device {device_name} created")

                    # Create the management interfaces
                    management_interface.device = device
                    management_interface.full_clean()
                    management_interface.save()
                    self.log_success(f"Interface {management_interface.name} created")

                    management_interface_unit.device = device
                    management_interface_unit.full_clean()
                    management_interface_unit.save()
                    self.log_success(f"Interface {management_interface_unit.name} created")

                    # Create the management IP
                    management_ip.assigned_object = management_interface_unit
                    management_ip.full_clean()
                    management_ip.save()
                    device.primary_ip4 = management_ip
                    device.save()
                    self.log_success(f"Management IP {management_ip.address} created and assigned to {management_interface_unit.name}")

                    # Create the loopback interfaces
                    loopback_interface.device = device
                    loopback_interface.full_clean()
                    loopback_interface.save()
                    self.log_success(f"Interface {loopback_interface.name} created")

                    loopback_interface_unit.device = device
                    loopback_interface_unit.full_clean()
                    loopback_interface_unit.save()
                    self.log_success(f"Interface {loopback_interface_unit.name} created")

                    loopback_ip.assigned_object = loopback_interface_unit
                    loopback_ip.full_clean()
                    loopback_ip.save()
                    self.log_success(f"Loopback IP {loopback_ip.address} created and assigned to {loopback_interface_unit.name}")

                    self.log_success(f"Device creation complete.")
            except ValidationError as e:
                self.log_failure(f"Failed to create device: {e}")
        else:
            self.log_success(f"Device {device_name} will be created")
            self.log_success(f"Interface {management_interface.name} will be created")
            self.log_success(f"Interface {management_interface_unit.name} will be created")
            self.log_success(f"Management IP {management_ip.address} will be created and assigned to {management_interface_unit.name}")
            self.log_success(f"Interface {loopback_interface.name} will be created")
            self.log_success(f"Interface {loopback_interface_unit.name} will be created")
            self.log_success(f"Loopback IP {loopback_ip.address} will be created and assigned to {loopback_interface_unit.name}")