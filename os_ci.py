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

from collections import deque
from email.mime import text
import json
import logging
from optparse import OptionParser
import os
import paramiko
import smtplib
import subprocess
import sys
from threading import Thread
import time

from executor import Executor


# CI related variables
CI_KEYFILE = os.environ.get('GERRIT_SSH_KEY', './gerrit_key')
OS_REVIEW_HOST = os.environ.get('GERRIT_HOST', 'review.openstack.org')
OS_REVIEW_HOST_PORT = os.environ.get('GERRIT_PORT', 29418)
PROJECT = os.environ.get('CI_PROJECT', 'openstack/cinder')
KEY_NAME = os.environ.get('DEFAULT_OS_KEYFILE', './jenkins_key')


def _send_email(host, to, subject, body):
    msg = text.MIMEText(body)
    msg['Subject'] = subject
    msg['From'] = to
    msg['To'] = msg['From']
    mail = smtplib.SMTP(host)
    try:
        mail.sendmail(msg['To'], msg['From'], msg.as_string())
    except:
        logging.exception('Failed to send email notice.')
    mail.quit()


def _filter_events(event):
    try:
        if (event.get('type', 'nill') == 'comment-added' and
                event['change']['project'] in PROJECT):
            if ((event['author'].get('username', '') == 'jenkins' and
                 'Verified+1' in event['comment']) or
                    'check dell' in event['comment']):
                logging.info('Adding review id %s to job queue...',
                             event['change']['number'])
                return event
    except KeyError:
        logging.exception('Something wrong in event data. %s', event)
    return None


class JobThread(Thread):
    """ Thread to process the gerrit events. """

    def _publish_results_to_gerrit(self, event, result):
        pass

    def run(self):
        global event_queue
        global config
        while True:
            if not event_queue:
                time.sleep(20)
            else:
                event = event_queue.popleft()
                # Launch test runs
                tests = []
                for test in config['tests']:
                    testexec = Executor(test, event)
                    tests.append(testexec)
                    testexec.start()
                    logging.info('Started %s test execution...',
                                 test['test-name'])

                # Wait for all executions to finish
                for test in tests:
                    test.join()
                logging.info('All tests completed')

                # Report the results
                status_lines = ''
                success = True
                commit_id = ''
                commands = ''
                for test in tests:
                    if test.get_results() is None:
                        logging.info('No result for %s', test.get_name())
                        continue
                    status_lines += '\n%s' % test.get_results()
                    success = success and test.test_passed()
                    commit_id = test.get_commit_id()
                    commands += 'tar zxvf %s.tgz\n' % test.get_name()
                    commands += (
                        'scp -r %s 192.168.100.22:/var/http/oslogs/\n' %
                        test.get_name())
                    commands += 'rm -fr %s*\n' % test.get_name()

                if commands == '':
                    continue
                commands += "ssh -p 29418 -i gerrit_key my-ci@"
                commands += "review.openstack.org gerrit review -m '\"\n"

                if success:
                    commands += 'Build succeeded.\n%s' % status_lines
                else:
                    commands += 'Build failed.\n%s' % status_lines

                commands += "\"' %s\n" % commit_id
                logging.info('Commands:\n%s\n', commands)
                if not success and config.get('smtp-host'):
                    _send_email(config['smtp-host'],
                                config.get('smtp-to',
                                           'me@example.com'),
                                'CI Test Failure',
                                commands)

                # if success:
                returncode = subprocess.call(
                    commands,
                    shell=True,
                    stdout=open('/dev/null', 'w'),
                    stderr=subprocess.STDOUT)
                logging.info('Command execution returned %d', returncode)

                # Throttle things a little but
                time.sleep(5)


class GerritEventStream(object):
    def __init__(self, *args, **kwargs):

        self.username = args[0]
        self.key_file = CI_KEYFILE
        self.host = OS_REVIEW_HOST
        self.port = OS_REVIEW_HOST_PORT
        logging.info('Connecting to gerrit stream with %s@%s:%d '
                     'using keyfile %s',
                     self.username,
                     self.host,
                     self.port,
                     self.key_file)

        self.ssh = paramiko.SSHClient()
        self.ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        try:
            self.ssh.connect(self.host,
                             self.port,
                             self.username,
                             key_filename=self.key_file)
        except paramiko.SSHException as e:
            logging.critical('Failed to connect to gerrit stream: %s', e)
            sys.exit(1)

        self.stdin, self.stdout, self.stderr = self.ssh.exec_command(
            "gerrit stream-events")

    def __iter__(self):
        return self

    def next(self):
        return self.stdout.readline()


def process_options():
    usage = "usage: %prog [options]\nos_ci.py."
    parser = OptionParser(usage, version='%prog 0.1')

    parser.add_option('-n', '--num-threads', action='store',
                      type='int',
                      default=2,
                      dest='number_of_worker_threads',
                      help='Number of job threads to run (default = 2).')
    parser.add_option('-m', action='store_true',
                      dest='event_monitor_only',
                      help='Just monitor Gerrit stream, dont process events.')
    (options, args) = parser.parse_args()
    return options

if __name__ == '__main__':
    global config
    json_data = open('ci.config')
    config = json.load(json_data)
    json_data.close()

    global event_queue
    event_queue = deque()
    options = process_options()

    logging.basicConfig(
        format='%(asctime)s %(thread)d %(levelname)s: %(message)s',
        level=logging.DEBUG,
        filename='/home/smcginnis/ci.log')
    logging.getLogger('paramiko').setLevel(logging.WARNING)
    logging.getLogger('requests').setLevel(logging.WARNING)

    for i in xrange(config['worker-threads']):
        jobThread = JobThread()
        jobThread.daemon = True
        jobThread.start()

    retries = 0
    while True:
        events = GerritEventStream(config['gerrit-id'])

        try:
            # event = json.loads(
            #     '{"patchSet": { "ref": '
            #     '"refs/changes/95/176095/1", "number": "1" '
            #     '}, "change": { "number": "176095" } }')
            # event_queue.append(event)
            for event in events:
                event = json.loads(event)
                valid_event = _filter_events(event)
                if valid_event:
                    retries = 0
                    try:
                        match = [src for src in event_queue if src['patchSet']['ref'] == event['patchSet']['ref']]
                        if match:
                            event_queue.remove(match[0])
                            logging.debug('Removed event')
                    except Exception, ValueError:
                        pass
                    if not options.event_monitor_only:
                        event_queue.append(valid_event)
        except KeyboardInterrupt:
            print('Got keyboard interrupt.')
            sys.exit()
        except Exception as e:
            if retries < 30:
                logging.exception(
                    'Error in event processing, attempting to reinitialize...')
                retries = retries + 1
                time.sleep(min(300, 30 * retries))
            else:
                logging.exception(
                    'Hit max retry attempts. Have a nice day.')
                if config.get('smtp-host'):
                    _send_email(config['smtp-host'],
                                config.get('smtp-to',
                                           'openstack-cinder-sc-ci@dell.com'),
                                'CI Connection Failure',
                                'The CI system has exceeded the max number of '
                                'connection retries and is exiting.')
                sys.exit()