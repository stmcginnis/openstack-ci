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

from datetime import datetime
import fileinput
import logging
import os
from threading import Thread
import time

import paramiko
from retrying import retry
from scp import SCPClient

from instance import Instance
from vmwinstance import VMWInstance

TIMEOUT_DURATION = (60 * 120)


def wait_for_completion(session, err_session, return_output=False):
    """Wait for the executed command completes.

    Call after calling exec_command to wait for the
    command to complete and get the results.
    """
    session.setblocking(0)
    output = ''
    start = datetime.now().replace(microsecond=0)
    timeout = time.time() + TIMEOUT_DURATION
    session_closed = False
    while True:
        if session.recv_ready():
            data = session.recv(4096)
            if not data:
                # Closed connection?
                logging.debug('Session closed!')
                session_closed = True
            if return_output:
                output += data

        if err_session.recv_ready():
            data = err_session.recv(4096)
            if not data:
                session_closed = True
            if return_output:
                output += data

        if (time.time() > timeout or session_closed or
                session.exit_status_ready()):
            exit_code = (
                session.recv_exit_status() if session.exit_status_ready()
                else -1)
            end = datetime.now().replace(microsecond=0)
            return exit_code, output, (end-start)
        time.sleep(0.05)


@retry(wait_exponential_multiplier=1000,
       wait_exponential_max=10000,
       stop_max_delay=30000)
def scp_put(scp, from_path, to_path):
    scp.put(from_path, to_path)


class Executor(Thread):
    """CI test execution handler.

    Performs the actual setup and run of the CI tempest tests.
    """

    def __init__(self, conf, event, dependencies):
        """Constructor.

        :param conf: The configuration settings.
        :param event: The gerrit event.
        :param dependencies: Any cross-repo dependencies.
        """
        Thread.__init__(self)
        self.patchset_ref = event['patchSet']['ref']
        self.name = '%s-%s-%s' % (
            conf['test-name'],
            event['change']['number'],
            event['patchSet']['number'])
        self.conf = conf
        self.results = None
        self.passed = False
        self.commit_id = None
        self.dependencies = dependencies
        self.concurrency = conf.get('test-concurrency', 1)
        logging.debug('Instantiated test execution %s', self.name)

    def get_name(self):
        return self.name

    def get_commit_id(self):
        return self.commit_id

    def get_change_id(self):
        return self.change_number

    def test_passed(self):
        return self.passed

    def _set_results(self, passing):
        status = 'FAILURE'
        if passing:
            status = 'SUCCESS'
        self.results = '* %s-dsvm-volume http://oslogs.' \
                       'compellent.com/%s/ : %s ' % \
                       (self.conf['test-name'],
                        self.name,
                        status)
        self.passed = passing

    def get_results(self):
        """Gets the test run results."""
        return self.results

    def run(self):
        """Thread run.

        Executes the tests.
        """
        name = self.name
        vmw_platform = self.conf.get('test-platform') == 'vmware'
        if vmw_platform:
            instance = VMWInstance(name, self.conf)
        else:
            instance = Instance(name, self.conf)
        instance.wait_for_ready()
        try:
            self.run_test(instance)
        except Exception:
            logging.exception('Test execution failed:')

        if instance:
            instance.delete_instance()

    def run_test(self, instance):
        """The test run."""
        name = self.name
        ip = instance.get_instance_ip()

        logging.info('Starting CI test execution...')
        ssh_client = paramiko.SSHClient()
        ssh_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        connected = False
        for i in range(1, 10):
            try:
                ssh_client.connect(
                    ip,
                    username=self.conf.get('image-user') or 'ubuntu',
                    key_filename='./jenkins_key')
                logging.debug('Connected to %s', ip)
                connected = True
                break
            except:
                logging.debug('Waiting to connect to instance %s...', ip)
                time.sleep(15 * i)
        if connected:
            scp = SCPClient(ssh_client.get_transport())
        else:
            logging.warning('Timed out waiting for SSH connection '
                            'to instance.')
            instance.delete_instance()
            return

        # Upload necessary files to test host
        ipoct = ip.split('.')
        outfile = open('./scripts/%slocal.conf' % name, 'a')
        input_filename = './scripts/local.conf.template'
        custom_file = './scripts/%s-%slocal.conf.template' % (
            self.conf['test-name'],
            self.change_number)
        if os.path.exists(custom_file):
            input_filename = custom_file

        for line in fileinput.FileInput(input_filename):
            if 'CINDER_BRANCH' in line:
                line = 'CINDER_BRANCH=%s\n' % (self.patchset_ref)
                # Handle any cross-repo dependencies here
                for dep in self.dependencies:
                    project = dep.get('change', {}).get('project', '')
                    cross_rep = ("%s_BRANCH=%s" % (
                                 project.replace('openstack/', '').upper(),
                                 project.get('patchSet', {}).get('ref')))
                    if cross_rep.startswith("_BRANCH") or cross_rep.endswith(
                            '='):
                        continue
                    line = "%s%s\n" % (line, cross_rep)
            if 'TEMPEST_STORAGE_PROTOCOL' in line:
                line = 'TEMPEST_STORAGE_PROTOCOL=%s\n' % (
                    self.conf.get('test-type', 'iSCSI'))
            if 'FIXED_RANGE' in line:
                line = 'FIXED_RANGE=192.168.%s.0/24\n' % ipoct[3]
            outfile.write(line)
        outfile.write('[%s]\n' % self.conf['backend-name'])
        for line in self.conf['config-opts']:
            outfile.write('%s\n' % line)
        outfile.close()

        scp_put(scp, './scripts/%slocal.conf' % name, '~/devstack/local.conf')
        scp_put(scp, './scripts/gather_logs.sh', '~/')
        scp_put(scp, './scripts/subunit2html.py', '/opt/stack/tempest/')
        os.remove('./scripts/%slocal.conf' % name)

        # Disable selinux enforcement to make sure no conflicts
        stdin, stdout, stderr = ssh_client.exec_command('sudo setenforce 0')

        logging.debug('Stacking...')
        # Make sure no leftover config
        stdin, stdout, stderr = ssh_client.exec_command(
            'rm -f /etc/cinder/cinder.conf')

        stdin, stdout, stderr = ssh_client.exec_command(
                'cd ~/devstack && ./stack.sh > '
                '/tmp/stack.sh.log 2>&1')
        exit_code, output, timespan = wait_for_completion(stdout.channel,
                                                          stderr.channel)
        logging.debug('Stacking operation took %s', timespan)
        if exit_code != 0:
            logging.warning('Stacking returned %d, failing run.',
                            exit_code)
            try:
                scp.get('/tmp/stack.sh.log', './%sstack.log' % name)
            except:
                pass
            return

        # Get the commit ID for later
        stdin, stdout, stderr = ssh_client.exec_command(
            "cd /opt/stack/cinder && git log --abbrev-commit --pretty=oneline "
            "-n1 | cut -f1 -d' '")
        exit_code, output, timespan = wait_for_completion(stdout.channel,
                                                          stderr.channel,
                                                          True)

        if exit_code != 0:
            logging.warning('Unable to extract commit id!')
        else:
            self.commit_id = output.rstrip('\r\n').strip()

        # Seeing some dnsmasq weirdness, block it
        stdin, stdout, stderr = ssh_client.exec_command(
            'sudo ebtables -I INPUT -i eth0 '
            '--protocol ipv4 --ip-proto udp '
            '--ip-dport 67:68 -j DROP')

        # Perform the actual tests
        logging.debug('Running tempest...')
        stdin, stdout, stderr = ssh_client.exec_command(
            "cd /opt/stack/tempest && "
            "tox -e all -- "
            "'^(?=.*volume)(?!.*test_encrypted_cinder_volumes)"
            "/*' --concurrency=%d > "
            "console.log.out 2>&1" % self.concurrency)
        exit_code, output, timespan = wait_for_completion(stdout.channel,
                                                          stderr.channel)
        logging.info('Tempest test run took %s', timespan)
        if exit_code != 0:
            logging.warning('Tempest test returned %d. Bummer.',
                            exit_code)
            self._set_results(False)
        else:
            self._set_results(True)

        # Generate the testr report
        stdin, stdout, stderr = ssh_client.exec_command(
            'cd /opt/stack/tempest && testr last --subunit > testrepository.'
            'subunit && python subunit2html.py testrepository.subunit')
        exit_code, output, timespan = wait_for_completion(stdout.channel,
                                                          stderr.channel)

        logging.debug('Collecting logs...')
        stdin, stdout, stderr = ssh_client.exec_command(
            'bash ~/gather_logs.sh %s' % name)
        exit_code, output, timespan = wait_for_completion(stdout.channel,
                                                          stderr.channel)
        scp.get('~/%s.tar.gz' % name, './%s.tgz' % self.name)
