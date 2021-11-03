# Copyright (c) 2021, Oracle and/or its affiliates. All rights reserved.

import argparse
import dns.zone
import dns.query
import dns.xfr
import getpass
import oci
import requests
import socket
import sys

from dyn.tm.session import DynectSession
from dyn.tm.zones import Zone
from dyn.tm.zones import SecondaryZone
from requests.adapters import HTTPAdapter
from oci.signer import Signer

parser = argparse.ArgumentParser(description='Migrate a zone from Dyn Managed DNS to OCI DNS')
parser.add_argument('zone_name', type=str, help='Name of the zone to migrate')
parser.add_argument('dynect_customer', type=str, help='Name of the Dynect Customer which owns the zone to be transferred')
parser.add_argument('dynect_username', type=str, help='Username of a Dynect user that has permission to manage the zone in Dynect')
parser.add_argument('--dynect-password', default='', help='Password of the Dynect user')
parser.add_argument('--oci-compartment', type=str, help='OCI compartment to which to migrate the zone', default='')
parser.add_argument('--oci-config-file', type=str, default='~/.oci/config', help='The OCI config file to use for authentication')
parser.add_argument('--oci-config-profile', type=str, default='DEFAULT', help='The OCI config profile to use for authentication')
parser.add_argument('--tsig-key-compartment', type=str, default='', help='The OCI compartment containing any tsig keys that are used by zones to be migrated. By default, the same as --oci-compartment')

args = parser.parse_args()

config = oci.config.from_file(args.oci_config_file, args.oci_config_profile)

OCI_DNS_BASE_URL = f'{oci.regions.endpoint_for("dns", config["region"])}/20180115'

CREATE_OCI_DNS_ZONE_FROM_ZONEFILE_URL = f'{OCI_DNS_BASE_URL}/actions/createZoneFromZoneFile'
CREATE_OCI_DNS_ZONE_URL = f'{OCI_DNS_BASE_URL}/zones'
LIST_TSIG_KEYS_URL = f'{OCI_DNS_BASE_URL}/tsigKeys'

dynect_password = args.dynect_password
if dynect_password == "":
    dynect_password = getpass.getpass(prompt='Dynect password: ')

dynect_session = DynectSession(args.dynect_customer, args.dynect_username, dynect_password)

dynect_zone = Zone(args.zone_name)

session = requests.session()
session.mount('http://', HTTPAdapter(max_retries=0))

auth = Signer(tenancy=config['tenancy'], user=config['user'], fingerprint=config['fingerprint'], private_key_file_location=config['key_file'])
opcprincipal = '{"tenantId": "' + config['tenancy'] + '", "subjectId": "' + config['user'] + '"}'

headers = {'opcprincipal': opcprincipal, 'Accept': 'application/json'}

compartment = args.oci_compartment
if compartment == '':
    compartment = config['tenancy']

tsig_key_compartment = args.tsig_key_compartment
if tsig_key_compartment == '':
    tsig_key_compartment = compartment

if dynect_zone._zone_type == 'Secondary':
    # Create a secondary zone in OCI DNS

    # Fetch the secondary zone
    try:
        dynect_secondary_zone = SecondaryZone(args.zone_name)
    except Exception:
        print(f'\nFailed to load the secondary zone. The Dynect user may need the "SecondaryGet" permission in Dynect.')
        quit(-1)
        
    # Check if the secondary zone is configured to use a tsig key. If it is,
    # verify that a tsig key by that name has been created in OCI.
    if dynect_secondary_zone.tsig_key_name != '':
        # Fetch tsig keys in the tsig key compartment with a name matching the
        # dynect secondary zone's tsig key name
        tsig_keys_response = session.get(LIST_TSIG_KEYS_URL, auth=auth, headers=headers, params={
            'compartmentId': tsig_key_compartment,
            'name': dynect_secondary_zone.tsig_key_name,
        })

        if tsig_keys_response.status_code == requests.codes.ok:
            if len(tsig_keys_response.json()) == 0:
                # tsig key was not found
                print(f'\nFailed to find a tsig key in the "{tsig_key_compartment}" compartment with the name "{dynect_secondary_zone.tsig_key_name}". The dynect zone to be migrated is associated with a dynect tsig key, and a tsig key sharing that name must have been created in OCI prior to the use of this script to migrate this zone.')
                quit(-1)
            tsig_key = tsig_keys_response.json()[0]

            # Verify the tsig key is active
            if tsig_key['lifecycleState'] != 'ACTIVE':
                print(f'\nThe OCI tsig key with the name "{dynect_secondary_zone.tsig_key_name}" was in the "{tsig_key["lifecycleState"]}" state, but must be in the "ACTIVE" state.')
                quit(-1)
            tsig_key_ocid = tsig_key['id']
    else:
        tsig_key_ocid = None

    url = CREATE_OCI_DNS_ZONE_URL

    secondary_zone_data = {
        'name': args.zone_name,
        'compartmentId': compartment,
        'zoneType': 'SECONDARY',
        'externalMasters': [
            {'address': address, 'tsigKeyId': tsig_key_ocid} for address in dynect_secondary_zone._masters
        ]
    }

    headers['Content-Type'] = 'application/json'

    response = session.post(url, auth=auth, headers=headers, json=secondary_zone_data)

else:
    # Acquire the zone's records by executing a zone transfer, and then create
    # a zone in OCI DNS using the zone file created from the records obtained
    # from the zone transfer

    params = {
        'compartmentId': compartment,
    }

    url = CREATE_OCI_DNS_ZONE_FROM_ZONEFILE_URL

    zone_name_with_dot = args.zone_name
    if not zone_name_with_dot.endswith("."):
        zone_name_with_dot = zone_name_with_dot + "."

    try:
        zonefile = dns.zone.from_xfr(dns.query.xfr(socket.gethostbyname('xfrout1.dynect.net'), args.zone_name)).to_text()
        zonefile = f'$ORIGIN {zone_name_with_dot}\n{zonefile}'
    except dns.xfr.TransferError:
        print(f'\nFailed to fetch the zone from Dyn Managed DNS.\n\nThis can happen if your public facing IP address has not been enabled as a transfer server for the zone.\n\nIn order to use this migration script, you must enable zone transfers to your current public facing IP address for any zones you wish to migrate.\n\nTo determine your current public facing IP address, visit "http://checkIP.dyn.com/".\n\nTo enable zone transfers to your IP address, follow the instructions here using your IP address as the External Nameserver IP address: "https://help.dyn.com/using-external-nameservers/". Make sure that "Transfers" is selected for your IP address.\n', file=sys.stderr)
        quit(-1)

    headers['Content-Type'] = 'text/dns'
    
    response = session.post(url, auth=auth, headers=headers, params=params, data=zonefile)

if response.status_code != requests.codes.created:
    print(f'Failed to create zone: {response.json()}')
else:
    print(f'Successfully created {args.zone_name} in OCI DNS. Zone OCID: {response.json()["id"]}')
