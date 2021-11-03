# Set Up

pip install oci dnspython requests dyn

# Usage

python migrate_zone.py example.com dynect-customer-name dynect-user-name

Dynect credentials with permission to read the zone from Dynect are required.

# Primary zones

In order to migrate a primary zone, the script executes a zone transfer (AXFR) and will therefore require transfers to be allowed to the public IP address of the machine running the script.

# Secondary zones

If a secondary zone is associated with a tsig key in dynect, the tsig key will need to have already been re-created in OCI with the same name for the script to migrate the secondary zone. If the tsig key was created in a compartment other than the one in to which the zone will be migrated, there is a command line option, --tsig-key-compartment, which can be used to specify which compartment the tsig key is in.
