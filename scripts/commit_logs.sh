#!/bin/bash

REF_NAME=$1
COMMIT_ID=$2
TEST_STATUS=$3

#tar zxvf $REF_NAME.tgz
#scp -r $REF_NAME 192.168.100.2:/var/http/oslogs/
#rm -fr $REF_NAME*

ssh -p 29418 -i gerrit_key dell-storagecenter-ci@review.openstack.org gerrit review -m '"
Build succeeded.
$TEST_STATUS
"' --verified=+1 $COMMIT_ID

