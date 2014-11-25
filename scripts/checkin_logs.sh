#!/bin/bash

REF_NAME=$1

tar mzxvf $REF_NAME.tgz -C ~/ci/
rm -fr $REF_NAME.tgz

pushd .
cd ~/ci
git add -u :/ >> ../git.log
git commit -a -m "Adding results from $REF_NAME"
git push >> ../git.log

popd

