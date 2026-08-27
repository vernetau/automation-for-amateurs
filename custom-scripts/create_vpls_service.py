from extras.scripts import *
from dcim.models import Device, Interface
from vpn.models import L2VPN, L2VPNTermination
from django.core.exceptions import ValidationError
from django.db import transaction
from django.contrib.contenttypes.models import ContentType

class CreateRouterVPLSService(Script):
    class Meta:
        name = "Create a VPLS service"
        description = "Creates a VPLS service on a router. This script will do the following: Create a VPLS termination in the VPLS, edit the physical interface, create the unit"
        commit_default = False
        scheduling_enabled = False

    vpls = ObjectVar(
        model=L2VPN,
        label="VPLS",
        description="Select the VPLS to connect to",
        required=True,
        query_params={
            'type': 'vpls'
        }
    )

    device_name = ObjectVar(
        model=Device,
        description="Device the service will terminate on.",
        required=True,
        query_params={
            'manufacturer': 'juniper',
        }
    )

    physical_interface = ObjectVar(
        model=Interface,
        label="Physical interface termination",
        description="Select the interface you wish to terminate the service on",
        query_params={
            'device_id': '$device_name',
            'type__n': ['virtual'],
            'mgmt_only': False
        },
        required=True
    )

    physical_interface_description = StringVar(
        label="Physical interface description",
        description="Enter a description for the physical interface if desired",
        required=False
    )

    interface_unit = IntegerVar(
        label="Interface Unit",
        description="Select the interface unit you wish to terminate the service on. Must be between 0 and 16385, and not already provisioned on the interface.",
        required=True,
        min_value=0,
        max_value=16385,
        default=0
    )

    unit_description = StringVar(
        label="Interface unit description",
        description="Enter a description for the interface unit if desired",
        required=False
    )

    mtu = IntegerVar(
        label="MTU",
        description="MTU for the service",
        required=True,
        min_value=1500,
        max_value=9216,
        default=9192
    )

    def run(self, data, commit):
        # Set vars
        device = data['device_name'] # Device object
        parent = data['physical_interface'] # Physical interface object
        unit = data['interface_unit'] # Integer from IntVar
        unit_name = f"{parent.name}.{unit}" # String for interface name
        vpls_name = data['vpls'].name # VPLS name

        # COMMIT STARTS HERE
        if commit:
            try:
                with transaction.atomic():
                    # Edit physical interface
                    physical_interface = data['physical_interface']
                    physical_interface.mtu = data['mtu']
                    physical_interface.enabled = True
                    if data['physical_interface_description']:
                        physical_interface.description = data['physical_interface_description']
                    physical_interface.full_clean()
                    physical_interface.save()
                    self.log_success(f"Interface {physical_interface.name} edited")

                    # Create unit
                    unit = Interface(
                        name=unit_name,
                        device=device,
                        parent=parent,
                        type="virtual",
                        mtu=data['mtu'],
                        enabled=True,
                        duplex='auto',
                    )
                    if data['unit_description']:
                        unit.description = data['unit_description']
                    
                    unit.full_clean()
                    unit.save()
                    self.log_success(f"Interface {unit.name} created")

                    # VPLS termination
                    termination = L2VPNTermination(
                        l2vpn=data['vpls'],
                        assigned_object_type=ContentType.objects.get_for_model(Interface),
                        assigned_object=unit
                    )

                    # Add termination
                    termination.full_clean()
                    termination.save()
                    self.log_success(f"Termination created")
            except ValidationError as e:
                self.log_failure(f"Failed to create vpls: {e}")
        else:
            self.log_success(f"Simulating creation")
            self.log_success(f"Interface {data['physical_interface'].name} will be edited")
            self.log_success(f"Interface {unit_name} will be created and assigned to {data['physical_interface'].name}")
            self.log_info(f"{vpls_name} Termination will be created")