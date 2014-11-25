#!/usr/bin/python

from collections import deque
import json
import logging
from optparse import OptionParser
import os
import paramiko
import sys
from threading import Thread
import time

from executor import Executor
import instance

if __name__ == '__main__':
    global config

    logging.basicConfig(format='%(asctime)s %(name)s %(levelname)s: %(message)s',
                        level=logging.DEBUG)
                        # filename='/home/smcginnis/ci.log')
    logging.getLogger('paramiko').setLevel(logging.WARNING)
    logging.getLogger('requests').setLevel(logging.WARNING)

    json_data = open('ci.config')
    config = json.load(json_data)
    json_data.close()

    event = json.loads('{"patchSet": { "ref": "refs/changes/12/128012/1", "number": "1" }, "change": { "number": "128012" } }')
    #event['patchSet']['ref'] = 'refs/changes/91/123391/8'
    #event['change']['number'] = '123391'
    #event['patchSet']['number'] = '8'

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

