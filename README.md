openstack-ci - Yet Another Simple OpenStack CI
==============================================

Simple scripting solution for implementing an 
OpenStack CI system for third party testing.
This has been implemented specifically for Cinder
testing, but should be easily adaptable for use
with other projects.

Based on the work started by:
Duncan Thomas - https://github.com/Funcan/kiss-ci
John Griffith - https://github.com/j-griffith/sos-ci

Requirements
------------
To use these scripts you will need the following:

- You have requested a service account and have read
  all background here: http://ci.openstack.org/third_party.html
- Master node to run the scripts on
- Node running an OpenStack cloud to spin up test VMs
  (can be same node)
- Staged VM image in glance to create test VMs from

Configuration
-------------
- Spin up hosting OpenStack cloud for test VM creation
  * If not accessing from the same host add the following line
    to local.conf:

    auto_assign_floating_ip = true
- Stage cloud VM with devstack pulled down to speed testing 
- Edit ci.conf with your local environment information
- Place gerrit_key and jenkin_key private keys in local directory
- Run CI via './os_ci.py &'


Work in progress. Will update with more details as needed.

