#!/bin/bash

REF_NAME=$1
cd ~/

mkdir -p $REF_NAME/logs/etc

PROJECTS="openstack-dev/devstack $PROJECTS"
PROJECTS="openstack/cinder $PROJECTS"
PROJECTS="openstack/glance $PROJECTS"
PROJECTS="openstack/glance_store $PROJECTS"
PROJECTS="openstack/horizon $PROJECTS"
PROJECTS="openstack/keystone $PROJECTS"
PROJECTS="openstack/keystonemiddleware $PROJECTS"
PROJECTS="openstack/nova $PROJECTS"
PROJECTS="openstack/oslo.config $PROJECTS"
PROJECTS="openstack/oslo.db $PROJECTS"
PROJECTS="openstack/oslo.i18n $PROJECTS"
PROJECTS="openstack/oslo.messaging $PROJECTS"
PROJECTS="openstack/oslo.middleware $PROJECTS"
PROJECTS="openstack/oslo.rootwrap $PROJECTS"
PROJECTS="openstack/oslo.serialization $PROJECTS"
PROJECTS="openstack/oslo.vmware $PROJECTS"
PROJECTS="openstack/python-cinderclient $PROJECTS"
PROJECTS="openstack/python-glanceclient $PROJECTS"
PROJECTS="openstack/python-keystoneclient $PROJECTS"
PROJECTS="openstack/python-novaclient $PROJECTS"
PROJECTS="openstack/python-openstackclient $PROJECTS"
PROJECTS="openstack/requirements $PROJECTS"
PROJECTS="openstack/stevedore $PROJECTS"
PROJECTS="openstack/taskflow $PROJECTS"
PROJECTS="openstack/tempest $PROJECTS"

# devstack logs
cd ~/devstack
cp local.conf ~/$REF_NAME/logs/local.conf.txt
cp /tmp/stack.sh.log ~/$REF_NAME/logs/stack.sh.log.txt

# Archive config files
for PROJECT in $PROJECTS; do
  proj=`basename $PROJECT`
  if [ -d /etc/$proj ]; then
    sudo cp -r /etc/$proj ~/$REF_NAME/logs/etc/
  fi
done

# OS Service Logs
cd /opt/stack/screen-logs
for log in `ls -1 /opt/stack/screen-logs | grep "[a-zA-Z].log"`; do
  cp $log ~/$REF_NAME/logs/$log.txt
done

# Add the commit id
cd /opt/stack/cinder
COMMIT=`git log --abbrev-commit --pretty=oneline -n1`
COMMIT_ID=$(echo $COMMIT | cut -f1 -d' ')

# Tempest logs
cd /opt/stack/tempest
echo "commit_id: $COMMIT_ID" >> console.log.out
cp console.log.out ~/$REF_NAME/

files=(
"tempest.log"
"etc/tempest.conf"
)

for file in "${files[@]}"
do
  if [ -f "$file" ]; then
    cp "$file" ~/$REF_NAME/logs/;
  fi
done

#cp console.log.out ~/$REF_NAME/console.log.out
#cp console.log.err ~/$REF_NAME/console.log.err
#cp tempest.log ~/$REF_NAME/tempest.log
#cp etc/tempest.conf ~/$REF_NAME/logs/tempest.conf

# Tar it all up
#cd $REF_NAME
cd ~/
me=`whoami`
sudo chown -R $me:$me $REF_NAME
gzip -r $REF_NAME/*
tar -cvf $REF_NAME.tar $REF_NAME > /dev/null
gzip $REF_NAME.tar

