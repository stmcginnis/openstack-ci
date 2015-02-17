#!/usr/bin/python

from collections import deque
import json
import logging
from optparse import OptionParser
import os
import paramiko
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


def _filter_events(event):

    if (event.get('type', 'nill') == 'comment-added' and
            'Verified+1' in event['comment'] and
            event['change']['project'] in PROJECT):
        if event['author']['username'] == 'jenkins':
            logging.info('Adding review id %s to job queue...',
                         event['change']['number'])
            return event
        else:
            logging.debug('Not doing anything with %s %s event.',
                          event['change']['project'],
                          event['change']['number'])
    else:
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
                time.sleep(60)
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
                    commands += 'scp -r %s 192.168.100.22:/var/http/oslogs/\n' % \
                        test.get_name()
                    commands += 'rm -fr %s*\n' % test.get_name()

                if commands == '':
                    continue
                commands += "ssh -p 29418 -i gerrit_key dell-storagecenter-ci@"
                commands += "review.openstack.org gerrit review -m '\"\n"

                if success:
                    commands += 'Build succeeded.\n%s' % status_lines
                else:
                    commands += 'Build failed.\n%s' % status_lines

                commands += "\"' %s\n" % commit_id
                logging.info('Commands:\n%s\n', commands)
                # Uncomment to enable automatic failure reporting
                # if not success:
                #     commands = commands.replace('ssh', '# ssh')

                # if success:
                returncode = subprocess.call(
                    commands,
                    shell=True,
                    stdout=open('/dev/null', 'w'),
                    stderr=subprocess.STDOUT)
                logging.info('Command execution returned %d', returncode)


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

        self.stdin, self.stdout, self.stderr =\
            self.ssh.exec_command("gerrit stream-events")

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

    while True:
        events = GerritEventStream(config['gerrit-id'])
        try:
            for event in events:
                event = json.loads(event)
                valid_event = _filter_events(event)
                if valid_event:
                    if not options.event_monitor_only:
                        logging.debug("Adding event to queue...")
                        event_queue.append(valid_event)
        except KeyboardInterrupt:
            print('Got keyboard interrupt.')
            sys.exit()
