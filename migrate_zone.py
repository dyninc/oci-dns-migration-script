# Copyright (c) 2021, Oracle and/or its affiliates. All rights reserved.

'''
Migrate zones from dynect to OCI
'''

import argparse
import getpass
import socket
import time
import traceback
import urllib.parse
import urllib.request

import requests
import oci
import dns.zone
import dns.query
import dns.xfr

from dyn.tm.session import DynectSession
from dyn.tm.zones import Zone
from dyn.tm.zones import SecondaryZone
from dyn.tm.zones import TSIG
from requests.adapters import HTTPAdapter
from oci.signer import Signer


MAX_POLL_ATTEMPTS = 100


parser = argparse.ArgumentParser(
    description='Migrate zones from Dyn Managed DNS to OCI DNS'
)

zone_target_group = parser.add_mutually_exclusive_group(required=True)
zone_target_group.add_argument(
    '--zone_name',
    type=str,
    default=None,
    help='Name of the zone to migrate. Required if --zone_names_file is not used.'
)
zone_target_group.add_argument(
    '--zone_names_file',
    type=str,
    default=None,
    help='A file containing names of zones to migrate. Required if --zone_name is not used.'
)

parser.add_argument(
    'dynect_customer',
    type=str,
    help='Name of the Dynect Customer which owns the zone to be transferred'
)
parser.add_argument(
    'dynect_username',
    type=str,
    help='Username of a Dynect user that has permission to manage the zone in Dynect'
)
parser.add_argument(
    '--dynect-password',
    default='',
    help='Password of the Dynect user'
)

parser.add_argument(
    '--oci-compartment',
    type=str,
    help='OCI compartment to which to migrate the zone',
    default=''
)
parser.add_argument(
    '--oci-config-file',
    type=str,
    default='~/.oci/config',
    help='The OCI config file to use for authentication'
)
parser.add_argument(
    '--oci-config-profile',
    type=str,
    default='DEFAULT',
    help='The OCI config profile to use for authentication'
)
parser.add_argument(
    '--tsig-key-compartment',
    type=str,
    default='',
    help='The OCI compartment containing any tsig keys that are used by zones to be migrated. ' \
         'By default, the same as --oci-compartment'
)

parser.add_argument(
    '--ignore-failures',
    action='store_true',
    help='If an error occurs while migrating a zone, skip that zone and continue trying to ' \
         'migrate the rest.'
)
parser.add_argument(
    '--no-ignore-failures',
    action='store_false',
    dest="ignore_failures",
    help='If an error occurs while migrating a zone, exit the script without migrating any more ' \
         'zones.'
)
parser.set_defaults(ignore_failures=True)


args = parser.parse_args()


dynect_password = args.dynect_password
if dynect_password == "":
    dynect_password = getpass.getpass(prompt='Dynect password: ')

# determine if the dynect session should be configured to use a proxy
PROXY_HOST = None
PROXY_PORT = None
PROXY_USER = None
PROXY_PASS = None
proxies = urllib.request.getproxies()
https_proxy = proxies.get('https', proxies.get('all', None))
if https_proxy is not None:
    parsed = urllib.parse.urlparse(https_proxy)
    PROXY_HOST = parsed.hostname
    PROXY_PORT = parsed.port
    PROXY_USER = parsed.username
    PROXY_PASS = parsed.password

dynect_session = DynectSession(
    args.dynect_customer,
    args.dynect_username,
    dynect_password,
    proxy_host=PROXY_HOST,
    proxy_port=PROXY_PORT,
    proxy_user=PROXY_USER,
    proxy_pass=PROXY_PASS,
)


config = oci.config.from_file(args.oci_config_file, args.oci_config_profile)


OCI_DNS_BASE_URL = f'{oci.regions.endpoint_for("dns", config["region"])}/20180115'
CREATE_OCI_DNS_ZONE_FROM_ZONEFILE_URL = f'{OCI_DNS_BASE_URL}/actions/createZoneFromZoneFile'
OCI_DNS_ZONES_BASE_URL = f'{OCI_DNS_BASE_URL}/zones'
OCI_DNS_TSIG_KEYS_BASE_URL = f'{OCI_DNS_BASE_URL}/tsigKeys'


session = requests.session()
session.mount('http://', HTTPAdapter(max_retries=0))

auth = Signer(
    tenancy=config['tenancy'],
    user=config['user'],
    fingerprint=config['fingerprint'],
    private_key_file_location=config['key_file'],
)
opcprincipal = f'{{"tenantId": "{config["tenancy"]}", "subjectId": "{config["user"]}"}}'

headers = {
    'opc-principal': opcprincipal,
    'Accept': 'application/json',
}


compartment = args.oci_compartment
if compartment == '':
    compartment = config['tenancy']

tsig_key_compartment = args.tsig_key_compartment
if tsig_key_compartment == '':
    tsig_key_compartment = compartment


def poll_tsig_key_create(tsig_key_ocid):
    poll_attempts = 0

    while poll_attempts < MAX_POLL_ATTEMPTS:
        poll_attempts += 1

        response = session.get(
            f'{OCI_DNS_TSIG_KEYS_BASE_URL}/{tsig_key_ocid}',
            auth=auth,
            headers=headers,
        )

        if response.status_code != requests.codes.ok:
            raise Exception(
                f'Failed to get lifecycle state for tsig key "{tsig_key_ocid}" (opc-request-id: '
                f'"{response.headers.get("opc-request-id")}"): {response.json()}'
            )

        if response.json()['lifecycleState'] == 'CREATING':
            time.sleep(5)
        elif response.json()['lifecycleState'] == 'ACTIVE':
            return
        else:
            raise Exception(
                f'Unexpected status for tsig key "{tsig_key_ocid}" (opc-request-id: '
                f'"{response.headers.get("opc-request-id")}"): {response.json()}'
            )

    raise Exception(f'Timed out waiting for tsig key "{tsig_key_ocid}" to finish being created')


def poll_zone_create(zone_ocid):
    poll_attempts = 0

    while poll_attempts < MAX_POLL_ATTEMPTS:
        poll_attempts += 1

        response = session.get(
            f'{OCI_DNS_ZONES_BASE_URL}/{zone_ocid}',
            auth=auth,
            headers=headers,
        )

        if response.status_code != requests.codes.ok:
            raise Exception(
                f'Failed to get lifecycle state for zone "{zone_ocid}" (opc-request-id: ' \
                f'"{response.headers.get("opc-request-id")}"): {response.json()}')

        if response.json()['lifecycleState'] == 'CREATING':
            time.sleep(5)
        elif response.json()['lifecycleState'] == 'ACTIVE':
            return
        else:
            raise Exception(
                f'Unexpected status for zone "{zone_ocid}" (opc-request-id: '
                f'"{response.headers.get("opc-request-id")}"): {response.json()}'
            )

    raise Exception(f'Timed out waiting for zone "{zone_ocid}" to finish being created')


def get_or_create_tsig_key(tsig_key_name):
    # Fetch tsig keys in the tsig key compartment with a name matching the
    # dynect secondary zone's tsig key name
    response = session.get(
        OCI_DNS_TSIG_KEYS_BASE_URL,
        auth=auth,
        headers=headers,
        params={
            'compartmentId': tsig_key_compartment,
            'name': tsig_key_name,
        }
    )

    if response.status_code == requests.codes.ok:
        if len(response.json()) > 0:
            tsig_key = response.json()[0]

            # Verify the tsig key is active
            if tsig_key['lifecycleState'] != 'ACTIVE':
                raise Exception(
                    f'The OCI tsig key with the name "{tsig_key_name}" was in the '
                    f'"{tsig_key["lifecycleState"]}" state, but must be in the "ACTIVE" state'
                )

            return tsig_key['id']
    else:
        raise Exception(
            f'Failed to look up tsig keys in OCI (opc-request-id: '
            f'"{response.headers.get("opc-request-id")}")'
        )

    # Attempt to fetch the tsig key's details from dynect and create the tsig key in OCI.
    try:
        dynect_tsig_key = TSIG(tsig_key_name)
    except Exception as ex:
        raise Exception(
            f'Failed to look up details for the tsig key "{tsig_key_name}" in Dynect'
        ) from ex

    tsig_key_data = {
        'name': tsig_key_name,
        'algorithm': dynect_tsig_key.algorithm,
        'secret': dynect_tsig_key.secret,
        'compartmentId': tsig_key_compartment,
    }

    response = session.post(
        OCI_DNS_TSIG_KEYS_BASE_URL,
        auth=auth,
        headers={
            'Content-Type': 'application/json',
            **headers
        },
        json=tsig_key_data,
    )

    if response.status_code != requests.codes.created:
        raise Exception(
            f'Failed to create tsig key with name "{tsig_key_name}" (opc-request-id: '
            f'"{response.headers.get("opc-request-id")}"): {response.json()}'
        )

    tsig_key_ocid = response.json()['id']

    print(
        f'Creating tsig key "{tsig_key_name}" in OCI DNS. Tsig key OCID: "{tsig_key_ocid}". '
        f'Waiting for tsig key creation to complete.'
    )
    try:
        poll_tsig_key_create(tsig_key_ocid)
    except Exception as ex:
        raise Exception(
            f'Encountered a problem while waiting for creation of tsig key "{tsig_key_name}" '
            f'to complete'
        ) from ex

    print(f'Creation of tsig key "{tsig_key_name}" in OCI DNS complete.')

    return tsig_key_ocid


def create_zone(zone_name):
    # Check if there is already an OCI zone in the compartment with the provided name
    response = session.get(
        OCI_DNS_ZONES_BASE_URL,
        auth=auth,
        headers=headers,
        params={
            'compartmentId': compartment,
            'name': zone_name,
        },
    )

    if response.status_code == requests.codes.ok:
        if len(response.json()) > 0:
            print(
                f'Found existing OCI zone with name "{zone_name}". Zone OCID: '
                f'"{response.json()[0]["id"]}. Skipping."'
            )
            return

    try:
        dynect_zone = Zone(zone_name)
    except Exception as ex:
        raise Exception(
            f'Failed to look up the zone {zone_name} from Dynect. Verify the zone exists in '
            f'Dynect and that the user has permission to look up the zone.'
        ) from ex

    if dynect_zone._zone_type == 'Secondary':
        # Create a secondary zone in OCI DNS
        # Fetch the secondary zone
        try:
            dynect_secondary_zone = SecondaryZone(zone_name)
        except Exception as ex:
            raise Exception(
                f'Failed to load the secondary zone {zone_name} from Dynect. The Dynect user may '
                f'need the "SecondaryGet" permission in Dynect.'
            ) from ex

        tsig_key_ocid = None

        # Check if the secondary zone is configured to use a tsig key. If it is,
        # check if a tsig key by that name has already been created in OCI. If it
        # has not already been created, attempt to create it before creating the
        # secondary zone.
        if dynect_secondary_zone.tsig_key_name != '':
            try:
                tsig_key_ocid = get_or_create_tsig_key(dynect_secondary_zone.tsig_key_name)
            except Exception as ex:
                raise Exception(
                    f'Could not get or create tsig key for secondary zone {zone_name}'
                ) from ex

        secondary_zone_data = {
            'name': zone_name,
            'compartmentId': compartment,
            'zoneType': 'SECONDARY',
            'externalMasters': [
                {
                    'address': address,
                    'tsigKeyId': tsig_key_ocid,
                } for address in dynect_secondary_zone._masters
            ]
        }

        response = session.post(
            OCI_DNS_ZONES_BASE_URL,
            auth=auth,
            headers={
                'Content-Type': 'application/json',
                **headers
            },
            json=secondary_zone_data,
        )

    else:
        # Acquire the zone's records by executing a zone transfer, and then create
        # a zone in OCI DNS using the zone file created from the records obtained
        # from the zone transfer

        params = {
            'compartmentId': compartment,
        }

        zone_name_with_dot = zone_name
        if not zone_name_with_dot.endswith("."):
            zone_name_with_dot = zone_name_with_dot + "."

        try:
            zonefile = dns.zone.from_xfr(
                dns.query.xfr(
                    socket.gethostbyname('xfrout1.dynect.net'),
                    zone_name,
                )
            ).to_text()
            zonefile = f'$ORIGIN {zone_name_with_dot}\n{zonefile}'
        except dns.xfr.TransferError:
            raise Exception(
                f'''

Failed to fetch the zone "{zone_name}" from Dyn Managed DNS.

This can happen if your public facing IP address has not been enabled as a
transfer server for the zone.

In order to use this migration script for PRIMARY zones, you must enable zone
transfers to your current public facing IP address for any PRIMARY zones you
wish to migrate.

To determine your current public facing IP address, visit:
"http://checkIP.dyn.com/".

To enable zone transfers to your IP address, follow the instructions here using
your IP address as the External Nameserver IP address:
"https://help.dyn.com/using-external-nameservers/".

Make sure that "Transfers" is selected for your IP address.
'''
                )

        response = session.post(
            CREATE_OCI_DNS_ZONE_FROM_ZONEFILE_URL,
            auth=auth,
            headers={
                'Content-Type': 'text/dns',
                **headers
            },
            params=params,
            data=zonefile,
        )

    if response.status_code != requests.codes.created:
        raise Exception(
            f'Failed to create zone with name "{zone_name}" (opc-request-id: '
            f'"{response.headers.get("opc-request-id")}"): {response.json()}'
        )

    print(
        f'Creating "{zone_name}" in OCI DNS. Zone OCID: {response.json()["id"]}. Waiting for '
        f'zone creation to complete.'
    )

    poll_zone_create(response.json()["id"])
    print(f'Creation of zone "{zone_name}" in OCI DNS complete.')


def migrate_zones():
    if args.zone_name is not None:
        zone_names = [args.zone_name]
    if args.zone_names_file is not None:
        with open(args.zone_names_file, 'r', encoding='UTF-8') as zone_names_file:
            zone_names = zone_names_file.read().splitlines()

    for zone_name in zone_names:
        try:
            create_zone(zone_name)
        except Exception as ex:
            if not args.ignore_failures:
                raise ex
            traceback.print_exc()
            print(f'\nFailed to create the zone {zone_name}. Moving on to the next.\n')


migrate_zones()
