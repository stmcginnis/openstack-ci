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

import time

from pyVim import connect
from pyVmomi import vim

import instance


class VMWInstance(instance.Instance):
    """Class to handle VM instance management."""

    def __init__(self, name, test_config):
        self.host = test_config.get('test-host')
        self.user = test_config.get('os-user') or 'root'
        self.password = test_config.get('os-pass') or 'Equallogic1'
        self.image_id = test_config['image-id']
        self.port = int(test_config.get('os-port') or '443')

        try:
            self.instance = self._boot_instance(name)
            # print('Instance set to %s' % self.instance)
        except Exception as e:
            raise instance.InstanceBuildException(
                'Failed to create instance: %s' % e)

    def _get_esx_connection(self):
        service_instance = connect.SmartConnect(host=self.host,
                                                user=self.user,
                                                pwd=self.password,
                                                port=self.port)
        return service_instance

    def _boot_instance(self, name):
        esx = self._get_esx_connection()
        content = esx.RetrieveContent()
        children = content.rootFolder.childEntity
        for child in children:
            if hasattr(child, 'vmFolder'):
                datacenter = child
            else:
                # Some other non-datacenter type
                continue
        vm = None
        for virtual_machine in datacenter.vmFolder.childEntity:
            if virtual_machine.summary.config.name == self.image_id:
                vm = virtual_machine
                break

        if vm is None:
            raise Exception('VM not found.')

        for i in range(1, 200):
            if (vm.runtime.powerState !=
                    vim.VirtualMachinePowerState.poweredOff):
                if i < 200:
                    # VM is in use, wait for it to become avilable
                    time.sleep(15)
                else:
                    raise Exception('VM has power state of %s' %
                                    vm.runtime.powerState)

        task = vm.PowerOn()
        while task.info.state not in [vim.TaskInfo.State.success,
                                      vim.TaskInfo.State.error]:
            time.sleep(1)

        if task.info.state == vim.TaskInfo.State.error:
            raise Exception('Failed to power on VM')

        while not vm.guest.ipAddress:
            time.sleep(10)
        self.ip = vm.guest.ipAddress
        connect.Disconnect(esx)
        return vm

    def get_instance_ip(self):
        return self.ip

    def wait_for_ready(self, timeout=90):
        # TODO(smcginnis): See if there is a good way to check this
        time.sleep(1)

    def delete_instance(self):
        # print('Shutting down')
        esx = self._get_esx_connection()
        content = esx.RetrieveContent()
        children = content.rootFolder.childEntity
        for child in children:
            if hasattr(child, 'vmFolder'):
                datacenter = child
            else:
                # Some other non-datacenter type
                continue
        vm = self.instance
        for virtual_machine in datacenter.vmFolder.childEntity:
            if virtual_machine.summary.config.name == self.image_id:
                vm = virtual_machine
                break

        if vm is None:
            raise Exception('VM not found.')

        if vm.runtime.powerState != vim.VirtualMachinePowerState.poweredOn:
            return

        task = vm.PowerOff()
        while task.info.state not in [vim.TaskInfo.State.success,
                                      vim.TaskInfo.State.error]:
            time.sleep(1)

        if task.info.state == vim.TaskInfo.State.error:
            raise Exception('Failed to power off VM')

        time.sleep(20)
        vm.RevertToCurrentSnapshot()
        connect.Disconnect(esx)