#!/usr/bin/python

# Copyright 2016 Sean McGinnis
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

import atexit
from collections import deque
from email.mime import text
import json
import logging
from optparse import OptionParser
import os
import paramiko
import pickle
import random
import re
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
SAVED_QUEUE_NAME = 'queue.log'

DEP_REGEX = re.compile("depends-on:\s*(?P<id>\w*)", re.I)


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


def _filter_events(event, recheck_trigger):
    try:
        if (event.get('type', 'nill') == 'comment-added' and
                event['change']['project'] in PROJECT and
                event['change']['branch'] == 'master'):
            if ((event['author'].get('username', '') == 'jenkins' and
                 'Verified+1' in event['comment']) or
                    recheck_trigger in event['comment']):
                logging.info('Adding review id %s to job queue...',
                             event['change']['number'])
                return event
    except KeyError:
        logging.exception('Something wrong in event data. %s', event)
    return None


def shutdown_handler():
    # Use 'pkill -SIGINT os_ci' to complete tests and cleanly
    # shut down.
    global process_events
    global worker_threads
    logging.info('CI processing stopping.')
    process_events = False

    if worker_threads:
        timeout = 40 * 60 # wait 40 minutes
        for worker in worker_threads:
            worker.join(timeout)

    pickle.dump(event_queue, open(SAVED_QUEUE_NAME, 'w'))

    time.sleep(3)
    logging.info('CI processing complete, exiting...')


class JobThread(Thread):
    """ Thread to process the gerrit events. """

    def _publish_results_to_gerrit(self, config, tests, job_id):
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
            commands += ('scp -r %s 192.168.100.22:/var/http/oslogs/\n' %
                test.get_name())
            commands += 'rm -fr %s*\n' % test.get_name()

        if commands == '':
            return
        commands += "ssh -p 29418 -i gerrit_key dell-storage-ci@"
        commands += "review.openstack.org gerrit review -m '\"\n"

        if success:
            commands += 'Build succeeded.\n%s' % status_lines
        else:
            commands += 'Build failed.\n%s' % status_lines

        commands += "\"' %s\n" % commit_id
        logging.info('Commands:\n%s\n', commands)
        if not success and config.get('smtp-host'):
            txt = commands[commands.find('Build'):]
            txt = '%s\nJob ID: %s\n%d events in queue.' % (
                txt, job_id, len(event_queue))
            _send_email(config['smtp-host'],
                        config.get('smtp-to',
                                   'openstack-ci@example.com'),
                        'CI Test Failure',
                        txt)

        # if success:
        returncode = subprocess.call(
            commands,
            shell=True,
            stdout=open('/dev/null', 'w'),
            stderr=subprocess.STDOUT)
        logging.info('Command execution returned %d', returncode)

    def run(self):
        global event_queue
        global process_events

        self.job_count = 0

        time.sleep(random.randint(0, 10))

        while not event_queue:
            time.sleep(4)

        while process_events:
            try:
                self._process_event(event_queue.popleft())
            except Exception:
                time.sleep(5)

    def _process_event(self, event):
        global config
        if event:
            # This still needs some work
            # dependencies = self._check_for_dependencies(config, event)
            dependencies = []
            self.job_count += 1
            job_id = "%s:%d" % (self.name, self.job_count)
            try:
                # Launch test runs
                tests = []
                for test in config['tests']:
                    testexec = Executor(test, event, dependencies)
                    tests.append(testexec)
                    testexec.start()
                    logging.info('Started %s test execution...',
                                 test['test-name'])

                # Wait for all executions to finish
                for test in tests:
                    test.join()
                logging.info('All tests completed')

                # Report the results
                self._publish_results_to_gerrit(
                    config, tests, job_id)

                # Throttle things a little bit
                time.sleep(5)
            except Exception:
                logging.exception('Error in event processing!')

    def _check_for_dependencies(self, config, event):
        """Check if this patch has a cross-repo dependency."""
        result = []
        username = config['gerrit-id']
        key_file = CI_KEYFILE
        host = OS_REVIEW_HOST
        port = OS_REVIEW_HOST_PORT

        try:
            ssh = paramiko.SSHClient()
            ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            ssh.connect(host,
                        port,
                        username,
                        key_filename=key_file)
            stdin, stdout, stderr = ssh.exec_command(
                "gerrit query --format JSON %s" %
                event.get('change', {}).get('id'))
            while not stdout.channel.exit_status_ready():
                time.sleep(1)
            patch = json.loads(stdout.readlines())
            dependencies = DEP_REGEX.findall(patch['commitMessage'])
            for dep_id in dependencies:
                stdin, stdout, stderr = ssh.exec_command(
                    "gerrit query --format JSON %s" % dep_id)
                while not stdout.channel.exit_status_ready():
                    time.sleep(1)
                result.append(json.loads(stdout.readlines()))
            ssh.close()
        except Exception:
            logging.exception('Error looking for cross-repo dependency.')
        return result


class GerritEventStream(object):
    def __init__(self, *args, **kwargs):

        self.username = args[0]
        self.key_file = CI_KEYFILE
        self.host = OS_REVIEW_HOST
        self.port = OS_REVIEW_HOST_PORT
        logging.info('Connecting to gerrit stream with %s@%s:%s '
                     'using keyfile %s',
                     self.username,
                     self.host,
                     self.port,
                     self.key_file)

        self.ssh = paramiko.SSHClient()
        self.ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        retry = 0
        while True:
            retry += 1
            try:
                self.ssh.connect(self.host,
                                 self.port,
                                 self.username,
                                 key_filename=self.key_file)
                break
            except Exception as e:
                logging.critical('Failed to connect to gerrit stream: %s', e)
                if retry < 5:
                    time.sleep(5 * retry)
                else:
                    logging.info('Exiting CI processing, failed to connect.')
                    sys.exit(1)

        logging.info('Reading stream events...')
        self.stdin, self.stdout, self.stderr = self.ssh.exec_command(
            "gerrit stream-events")

    def __iter__(self):
        return self

    def next(self):
        return self.stdout.readline()


if __name__ == '__main__':
    global config
    json_data = open('ci.config')
    config = json.load(json_data)
    json_data.close()

    global event_queue
    global process_events
    event_queue = deque()
    process_events = True
    options = process_options()

    formatter = logging.Formatter(
        fmt='%(asctime)s %(levelname)-5s [%(threadName)-23s]: %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S')
    rotating_handler = logging.handlers.RotatingFileHandler(
        '~/ci.log', maxBytes=(1024 * 1024), backupCount=5)
    rotating_handler.setFormatter(formatter)
    root_logger = logging.getLogger()
    root_logger.addHandler(rotating_handler)
    root_logger.setLevel(logging.DEBUG)
    logging.getLogger('paramiko').setLevel(logging.WARNING)
    logging.getLogger('requests').setLevel(logging.WARNING)

    global worker_threads
    worker_threads = []
    atexit.register(shutdown_handler)

    for i in xrange(config['worker-threads']):
        jobThread = JobThread()
        jobThread.daemon = True
        jobThread.name = "Worker-%s" % i
        jobThread.start()
        worker_threads.append(jobThread)

    retries = 0
    while True:
        events = GerritEventStream(config['gerrit-id'])
        recheck_trigger = "run-%s" % config['ci_name']

        # See if we need to load any saved events
        if os.path.isfile(SAVED_QUEUE_NAME):
            tmp_queue = pickle.load(open(SAVED_QUEUE_NAME, "r"))
            for event in tmp_queue:
                event_queue.append(event)
            os.remove(SAVED_QUEUE_NAME)

        try:
            # event = json.loads(
            #     '{"patchSet": { "ref": '
            #     '"refs/changes/95/176095/1", "number": "1" '
            #     '}, "change": { "number": "176095" } }')
            # event_queue.append(event)
            for event in events:
                event = json.loads(event)
                valid_event = _filter_events(event, recheck_trigger)
                if valid_event:
                    retries = 0
                    try:
                        match = [src for src in event_queue if
                                 src['change']['id'] ==
                                 event['change']['id']]
                        if match:
                            event_queue.remove(match[0])
                            logging.debug('Removed event')
                    except Exception, ValueError:
                        pass
                    if not options.event_monitor_only:
                        event_queue.append(valid_event)
                    logging.info('%d events in queue.', len(event_queue))
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
