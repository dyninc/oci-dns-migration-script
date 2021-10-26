# Copyright (c) 2021, Oracle and/or its affiliates. All rights reserved.

import argparse
import dns.zone
import dns.query
import dns.xfr
import oci
import requests
import socket
import sys
from requests.adapters import HTTPAdapter
from oci.signer import Signer

parser = argparse.ArgumentParser(description='Migrate a zone from Dyn Managed DNS to OCI DNS')
parser.add_argument('zone_name', type=str, help='Name of the zone to migrate')
parser.add_argument('--oci-compartment', type=str, help='OCI compartment to which to migrate the zone', default="")
parser.add_argument('--oci-config-file', type=str, default='~/.oci/config', help='The OCI config file to use for authentication')
parser.add_argument('--oci-config-profile', type=str, default='DEFAULT', help='The OCI config profile to use for authentication')

args = parser.parse_args()

config = oci.config.from_file(args.oci_config_file, args.oci_config_profile)

CREATE_OCI_DNS_ZONE_FROM_ZONEFILE_URL = f'{oci.regions.endpoint_for("dns", config["region"])}/20180115/actions/createZoneFromZoneFile'

session = requests.session()
session.mount('http://', HTTPAdapter(max_retries=0))

auth = Signer(tenancy=config['tenancy'], user=config['user'], fingerprint=config['fingerprint'], private_key_file_location=config['key_file'])
opcprincipal = '{"tenantId": "' + config['tenancy'] + '", "subjectId": "' + config['user'] + '"}'

headers = {'opcprincipal': opcprincipal, 'Accept': 'application/json', 'Content-Type': 'text/dns'}

compartment = args.oci_compartment
if compartment == "":
    compartment = config['tenancy']

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
    print(f'\nFailed to fetch the zone from Dyn Managed DNS.\n\nThis can happen if the zone does not exist in Dyn Managed DNS or if your public facing IP address has not been enabled as a transfer server for the zone.\n\nIn order to use this migration script, you must enable zone transfers to your current public facing IP address for any zones you wish to migrate.\n\nTo determine your current public facing IP address, visit "http://checkIP.dyn.com/".\n\nTo enable zone transfers to your IP address, follow the instructions here using your IP address as the External Nameserver IP address: "https://help.dyn.com/using-external-nameservers/". Make sure that "Transfers" is selected for your IP address.\n', file=sys.stderr)
    quit(-1)

response = session.post(url, auth=auth, headers=headers, params=params, data=zonefile, verify=False)

print(f'Successfully created {args.zone_name} in OCI DNS. Zone OCID: {response.json()["id"]}')
