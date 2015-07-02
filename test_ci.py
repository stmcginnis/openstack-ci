#!/usr/bin/python

# Copyright 2015 Sean McGinnis
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#  http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import json
import logging
import requests

from executor import Executor

if __name__ == '__main__':
    global config

    logging.basicConfig(
        format='%(asctime)s %(name)s %(levelname)s: %(message)s',
        level=logging.DEBUG)
    logging.getLogger('paramiko').setLevel(logging.WARNING)
    logging.getLogger('requests').setLevel(logging.WARNING)
    requests.packages.urllib3.disable_warnings()

    json_data = open('test.config')
    config = json.load(json_data)
    json_data.close()

    event = json.loads(
        '{"patchSet": { "ref": '
        '"refs/changes/95/176095/1", "number": "1" '
        '}, "change": { "number": "176095" } }')

    tests = []
    for test in config['tests']:
        logging.debug(test['test-name'])
        testexec = Executor(test, event)
        tests.append(testexec)
        testexec.start()

    # Wait for all executions to finish
    for test in tests:
        test.join()

    # Report the results
    for test in tests:
        logging.info(test.get_results())