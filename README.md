# Dyn Managed DNS to OCI DNS Migration Script

This script will migrate Dyn Managed DNS zones to OCI's Public DNS.

## IMPORTANT INFORMATION

This script will not migrate Dyn Advanced Services at this time.

If your zone includes Advanced Services and you choose to use this script at this time, please see the
section at the bottom of this guide for important tips.

## USING THE SCRIPT

Requires Python 3.6 or higher

Step 1: Install Python SDK

Follow the [installation guide](https://oracle-cloud-infrastructure-python-sdk.readthedocs.io/en/latest/configuration.html) to install and configure the OCI Python SDK.

Step 2: Create OCI Config File (CLI File)

Create a configuration file to use the OCI CLI following instructions provided [here](https://docs.oracle.com/en-us/iaas/Content/API/Concepts/sdkconfig.htm#SDK_and_CLI_Configuration_File)

Step 3: Install the Migration Script

Run the following command:

`$ pip install oci dnspython requests dyn`

Step 4: Usage

`python migrate_zone.py example.com dynect-customer-name dynect-user-name`

Dynect credentials with permission to read the zone from Dynect are required. The following permissions should be enabled on the Dynect-user-name:

SecondaryGet
ZoneGet
TSIGGet

## Primary zones

In order to migrate a primary zone, the script executes a zone transfer (AXFR) and will therefore require transfers to be allowed to the public IP address of the machine running the script. This is enabled in the "external nameservers" section of your Dyn Managed DNS account . Details and steps can be found on https://help.dyn.com/using-external-nameservers/

For help determining your public IP you can go to http://checkip.dyndns.com/


## Secondary zones

If a secondary zone is associated with a tsig key in Dynect, the tsig key will need to have already been re-created in OCI with the same name for the script to migrate the secondary zone. If the tsig key was created in a compartment other than the one in to which the zone will be migrated, there is a command line option, --tsig-key-compartment, which can be used to specify which compartment the tsig key is in.

## Help

**the default compartment the zone will be migrated to is the root of your OCI tenancy. users can use call pass a -h or --help (or see below) to add additional arguments such as compartment ocid**

```
_Migrate a zone from Dyn Managed DNS to OCI DNS

positional arguments:
  zone_name             Name of the zone to migrate
  dynect_customer       Name of the Dynect Customer which owns the zone to be
                        transferred
  dynect_username       Username of a Dynect user that has permission to
                        manage the zone in Dynect

optional arguments:
  -h, --help            show this help message and exit
  --dynect-password DYNECT_PASSWORD
                        Password of the Dynect user
  --oci-compartment OCI_COMPARTMENT
                        OCI compartment to which to migrate the zone
  --oci-config-file OCI_CONFIG_FILE
                        The OCI config file to use for authentication
  --oci-config-profile OCI_CONFIG_PROFILE
                        The OCI config profile to use for authentication
  --tsig-key-compartment TSIG_KEY_COMPARTMENT
                        The OCI compartment containing any tsig keys that are
                        used by zones to be migrated. By default, the same as
                        --oci-compartment_
```

## IMPORTANT TIPS (IF YOU HAVE ADVANCED SERVICES AND USE THIS SCRIPT)

Using this script for zones with Advanced Services will require careful zone adjustments in order to replicate your service within OCI.

If you are migrating a zone with Traffic Director (TD): On the node attached to your TD Service
you should expect to see several service-related sub-nodes which will need to be removed
prior to configuring the appropriate OCI Traffic Management service.

If you are migrating a zone with other non-TD Advanced Services (such as Traffic Manager,
Active Failover or HTTP Redirect):  Depending on the configuration of your service, the service
node may appear with a record or not appear at all.  In all cases, you should be prepared to
adjust the node as appropriate for OCI replacement service.

After you have cleaned up your advanced service nodes and are ready to configure the replacement
OCI service, you can use the following guides for replicating your Dyn Advanced Service using the
appropriate OCI option: https://www.oracle.com/corporate/acquisitions/dyn/technologies/migrate-your-services/
