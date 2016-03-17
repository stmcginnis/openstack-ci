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

import time

from novaclient.v1_1 import client as novaclient


class InstanceBuildException(Exception):
    def __init__(self, message):
        Exception.__init__(self, message)


class Instance(object):
    """Class to handle VM instance management."""

    def __init__(self, name, test_config):
        self.os_user = test_config.get('os-user') or 'admin'
        self.os_pass = test_config.get('os-pass') or 'cinder'
        self.os_tenant_id = test_config.get('os-tenant') or 'admin'
        self.os_auth_url = 'http://%s:5000/v2.0' % test_config['test-host']
        self.default_flavor = test_config.get('os-flavor') or 4
        self.key_name = 'jenkins'
        self.image_id = test_config['image-id']
        self.nova_client = novaclient.Client(self.os_user,
                                             self.os_pass,
                                             self.os_tenant_id,
                                             self.os_auth_url)
        try:
            self.instance = self._boot_instance(name)
        except Exception as e:
            raise InstanceBuildException('Failed to create instance: %s' % e)

    def _boot_instance(self, name):
        return self.nova_client.servers.create(name,
                                               self.image_id,
                                               self.default_flavor,
                                               key_name=self.key_name)

    def get_instance_ip(self):
        ips = self.instance.networks['private']
        ip = ips[1] if len(ips) > 1 else ips[0]
        return ip

    def wait_for_ready(self, timeout=90):
        while timeout > 0:
            self.instance = self.nova_client.servers.get(self.instance.id)
            if self.instance.status == 'ACTIVE':
                return True
            time.sleep(1)
        raise InstanceBuildException('Instance failed to become ready after '
                                     '%s seconds' % timeout)

    def get_current_status(self):
        self.instance = self.nova_client.servers.get(self.instance.id)
        return self.instance.status

    def delete_instance(self):
        if self.instance:
            self.instance = self.nova_client.servers.delete(self.instance.id)
