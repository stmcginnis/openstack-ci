# We'll move the actual work of deploying devstack and running tempest here
# Same idea, make it a class that can be over-ridden with your own version of
# tool chain.

from datetime import datetime
import fileinput
import logging
import os
import paramiko
from scp import SCPClient
import subprocess
import sys
from threading import Thread
import time

from instance import Instance


def wait_for_completion(session, err_session, return_output=False):
    """Wait for the executed command completes.

    Call after calling exec_command to wait for the
    command to complete and get the results.
    """
    session.setblocking(0)
    output = ''
    start = datetime.now().replace(microsecond=0)
    while True:
        if session.recv_ready():
            data = session.recv(4096)
            if return_output:
                output += data

        if err_session.recv_ready():
            data = err_session.recv(4096)
            if return_output:
                output += data

        if session.exit_status_ready():
            exit_code = session.recv_exit_status()
            end = datetime.now().replace(microsecond=0)
            return exit_code, output, (end-start)

class Executor(Thread):
    """CI test execution handler.

    Performs the actual setup and run of the CI tempest tests.
    """

    def __init__(self, conf, event):
        """Constructor.

        @param conf The configuration settings.
        @param event The gerrit event.
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
        logging.debug('Instantiated test execution %s' % self.name)

    def get_name(self):
        return self.name

    def get_commit_id(self):
        return self.commit_id

    def test_passed(self):
        return self.passed

    def _set_results(self, passing):
        status = 'FAILURE'
        if passing:
            status = 'SUCCESS'
        self.results = '* %s-dsvm-volume http://oslogs.compellent.com/%s/ : %s ' % (
                        self.conf['test-name'],
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
        try:
            self.run_test()
        except Exception as e:
            logging.critical('Test execution failed: %s', e)

    def run_test(self):
        """The test run."""
        name = self.name
        # Launch a test instance
        instance = Instance(name, self.conf)
        instance.wait_for_ready()
        ip = instance.get_instance_ip()

        logging.info('[%s] Starting CI test execution...', name)
        ssh_client = paramiko.SSHClient()
        ssh_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        connected = False
        for i in range(1, 10):
            try:
                ssh_client.connect(ip,
                                   username=self.conf.get('image-user') or 'ubuntu',
                                   key_filename='./jenkins_key')
                logging.debug('[%s] Connected to %s', name, ip)
                connected = True
                break
            except:
                logging.debug('[%s] Waiting to connect to instance %s...',
                              name, ip)
                time.sleep(10 * i) 
        if connected:
            scp = SCPClient(ssh_client.get_transport())
        else:
            logging.warning('[%s] Timed out waiting for SSH connection '
                            'to instance.', name)
            return

        # Upload necessary files to test host
        outfile = open('./scripts/local.conf', 'a')
        for line in fileinput.FileInput('./scripts/local.conf.template'):
            if 'CINDER_BRANCH' in line:
                line = 'CINDER_BRANCH=%s\n' % (self.patchset_ref)
            outfile.write(line)
        outfile.write('[%s]' % self.conf['backend-name'])
        for line in self.conf['config-opts']:
            outfile.write(line)
        outfile.close()

        scp.put('./scripts/local.conf', '~/devstack/local.conf')
        scp.put('./scripts/gather_logs.sh', '~/')
        os.remove('./scripts/local.conf')

        # Disable selinux enforcement to make sure no conflicts
        stdin, stdout, stderr = \
            ssh_client.exec_command('sudo setenforce 0')

        logging.debug('[%s] Stacking...', name)
        # Make sure no leftover config
        stdin, stdout, stderr = \
            ssh_client.exec_command('rm -f /etc/cinder/cinder.conf')

        stdin, stdout, stderr = \
            ssh_client.exec_command('cd ~/devstack && ./stack.sh > '
                                    '/tmp/stack.sh.log 2>&1')
        exit_code, output, timespan = wait_for_completion(stdout.channel,
                                                          stderr.channel)
        logging.debug('[%s] Stacking operation took %s', name, timespan)
        if exit_code != 0:
            logging.warning('[%s] Stacking returned %d, failing run.',
                            self.name,
                            exit_code)
            instance.delete_instance()
            return

        # Get the commit ID for later
        stdin, stdout, stderr = \
            ssh_client.exec_command("cd /opt/stack/cinder && "
                                    "git log --abbrev-commit --pretty=oneline "
                                    "-n1 | cut -f1 -d' '")
        exit_code, output, timespan = wait_for_completion(stdout.channel,
                                                          stderr.channel,
                                                          True)

        if exit_code != 0:
             logging.warning('[%s] Unable to extract commit id!', self.name)
        else:
             self.commit_id = output.rstrip('\r\n').strip()

        # Perform the actual tests
        logging.debug('[%s] Running tempest...', name)
        stdin, stdout, stderr = \
            ssh_client.exec_command('cd /opt/stack/tempest && '
                                    './tools/pretty_tox.sh api.volume '
                                    '> console.log.out '
                                    '2>&1')
        exit_code, output, timespan = wait_for_completion(stdout.channel,
                                                          stderr.channel)
        logging.info('[%s] Tempest test run took %s', name, timespan)
        if exit_code != 0:
            logging.warning('[%s] Tempest test run returned %d. Bummer.',
                            self.name, exit_code)
            self._set_results(False)
        else:
            self._set_results(True)
    
        logging.debug('[%s] Collecting logs...', self.name)
        stdin, stdout, stderr = \
            ssh_client.exec_command('bash ~/gather_logs.sh %s' %
                                    name)
        exit_code, output, timespan = wait_for_completion(stdout.channel,
                                                          stderr.channel)
        scp.get('~/%s.tar.gz' % name, './%s.tgz' % self.name)

        #subprocess.call(['scripts/checkin_logs.sh', self.name])
        instance.delete_instance()

