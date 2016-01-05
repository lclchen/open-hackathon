# -*- coding: utf-8 -*-
"""
Copyright (c) Microsoft Open Technologies (Shanghai) Co. Ltd.  All rights reserved.

The MIT License (MIT)

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in
all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
THE SOFTWARE.
"""

__author__ = 'rapidhere'

from hackathon.azureformation.utility import (
    find_unassigned_endpoints,
)
from azure.servicemanagement import (
    WindowsConfigurationSet,
    LinuxConfigurationSet,
    OSVirtualHardDisk,
    ConfigurationSet,
    ConfigurationSetInputEndpoint,
)
from threading import (
    current_thread,
)
from hackathon.constants import VE_PROVIDER
from template_constants import AZURE_UNIT
from template_unit import TemplateUnit


class AzureTemplateUnit(TemplateUnit):
    # template name in virtual_environment

    # other constants
    BLOB_BASE = '%s-%s-%s-%s-%s-%s-%s-%s.vhd'
    MEDIA_BASE = 'https://%s.%s/%s/%s'

    def __init__(self, virtual_environment):
        super(AzureTemplateUnit, self).__init__(VE_PROVIDER.AZURE)
        self.virtual_environment = virtual_environment

    def get_name(self):
        return "TODO: name"

    def get_type(self):
        return "TODO: type"

    def get_description(self):
        return "TODO: description"

    def get_image_type(self):
        return self.virtual_environment[AZURE_UNIT.IMAGE][AZURE_UNIT.IMAGE_TYPE]

    def is_vm_image(self):
        return self.get_image_type() == AZURE_UNIT.VM

    def get_vm_image_name(self):
        """
        Return None if image type is not vm
        :return:
        """
        return self.virtual_environment[AZURE_UNIT.IMAGE][AZURE_UNIT.IMAGE_NAME] if self.is_vm_image() else None

    def get_image_name(self):
        return self.virtual_environment[AZURE_UNIT.IMAGE][AZURE_UNIT.IMAGE_NAME]

    def get_system_config(self):
        """
        Return None if image type is vm
        :return:
        """
        if self.is_vm_image():
            return None
        sc = self.virtual_environment[AZURE_UNIT.SYSTEM_CONFIG]
        # check whether virtual machine is Windows or Linux
        if sc[AZURE_UNIT.SYSTEM_CONFIG_OS_FAMILY] == AZURE_UNIT.WINDOWS:
            system_config = WindowsConfigurationSet(computer_name=sc[AZURE_UNIT.SYSTEM_CONFIG_HOST_NAME],
                                                    admin_password=sc[AZURE_UNIT.SYSTEM_CONFIG_USER_PASSWORD],
                                                    admin_username=sc[AZURE_UNIT.SYSTEM_CONFIG_USER_NAME])
            system_config.domain_join = None
            system_config.win_rm = None
        else:
            system_config = LinuxConfigurationSet(host_name=sc[AZURE_UNIT.SYSTEM_CONFIG_HOST_NAME],
                                                  user_name=sc[AZURE_UNIT.SYSTEM_CONFIG_USER_NAME],
                                                  user_password=sc[AZURE_UNIT.SYSTEM_CONFIG_USER_PASSWORD],
                                                  disable_ssh_password_authentication=False)
        return system_config

    def get_os_virtual_hard_disk(self):
        """
        Return None if image type is vm
        Media link should be unique
        :return:
        """
        if self.is_vm_image():
            return None
        i = self.virtual_environment[AZURE_UNIT.IMAGE]
        sa = self.virtual_environment[AZURE_UNIT.STORAGE_ACCOUNT]
        c = self.virtual_environment[AZURE_UNIT.CONTAINER]
        now = self.util.get_now()
        blob = self.BLOB_BASE % (i[AZURE_UNIT.IMAGE_NAME],
                                 str(now.year),
                                 str(now.month),
                                 str(now.day),
                                 str(now.hour),
                                 str(now.minute),
                                 str(now.second),
                                 str(current_thread().ident))
        media_link = self.MEDIA_BASE % (sa[AZURE_UNIT.STORAGE_ACCOUNT_SERVICE_NAME],
                                        sa[AZURE_UNIT.STORAGE_ACCOUNT_URL_BASE],
                                        c,
                                        blob)
        os_virtual_hard_disk = OSVirtualHardDisk(i[AZURE_UNIT.IMAGE_NAME], media_link)
        return os_virtual_hard_disk

    def get_network_config(self, service, update):
        """
        Return None if image type is vm and not update
        Public endpoint should be assigned in real time
        :param service:
        :return:
        """
        if self.is_vm_image() and not update:
            return None
        cs = self.virtual_environment[AZURE_UNIT.CLOUD_SERVICE]
        nc = self.virtual_environment[AZURE_UNIT.NETWORK_CONFIG]
        network_config = ConfigurationSet()
        network_config.configuration_set_type = nc[AZURE_UNIT.NETWORK_CONFIG_CONFIGURATION_SET_TYPE]
        input_endpoints = nc[AZURE_UNIT.NETWORK_CONFIG_INPUT_ENDPOINTS]
        # avoid duplicate endpoint under same cloud service
        # TODO: ?
        assigned_endpoints = service.get_assigned_endpoints(cs[AZURE_UNIT.CLOUD_SERVICE_SERVICE_NAME])
        endpoints = map(lambda i: i[AZURE_UNIT.NETWORK_CONFIG_INPUT_ENDPOINTS_LOCAL_PORT], input_endpoints)
        unassigned_endpoints = map(str, find_unassigned_endpoints(endpoints, assigned_endpoints))
        map(lambda (i, u): i.update({AZURE_UNIT.NETWORK_CONFIG_INPUT_ENDPOINTS_PORT: u}), zip(input_endpoints, unassigned_endpoints))
        for input_endpoint in input_endpoints:
            network_config.input_endpoints.input_endpoints.append(
                ConfigurationSetInputEndpoint(
                    input_endpoint[AZURE_UNIT.NETWORK_CONFIG_INPUT_ENDPOINTS_NAME],
                    input_endpoint[AZURE_UNIT.NETWORK_CONFIG_INPUT_ENDPOINTS_PROTOCOL],
                    input_endpoint[AZURE_UNIT.NETWORK_CONFIG_INPUT_ENDPOINTS_PORT],
                    input_endpoint[AZURE_UNIT.NETWORK_CONFIG_INPUT_ENDPOINTS_LOCAL_PORT]
                )
            )
        return network_config

    def get_storage_account_name(self):
        return self.virtual_environment[AZURE_UNIT.STORAGE_ACCOUNT][AZURE_UNIT.STORAGE_ACCOUNT_SERVICE_NAME]

    def get_storage_account_description(self):
        return self.virtual_environment[AZURE_UNIT.STORAGE_ACCOUNT][AZURE_UNIT.STORAGE_ACCOUNT_DESCRIPTION]

    def get_storage_account_label(self):
        return self.virtual_environment[AZURE_UNIT.STORAGE_ACCOUNT][AZURE_UNIT.STORAGE_ACCOUNT_LABEL]

    def get_storage_account_location(self):
        return self.virtual_environment[AZURE_UNIT.STORAGE_ACCOUNT][AZURE_UNIT.STORAGE_ACCOUNT_LOCATION]

    def get_cloud_service_name(self):
        return self.virtual_environment[AZURE_UNIT.CLOUD_SERVICE][AZURE_UNIT.CLOUD_SERVICE_SERVICE_NAME]

    def get_cloud_service_label(self):
        return self.virtual_environment[AZURE_UNIT.CLOUD_SERVICE][AZURE_UNIT.CLOUD_SERVICE_LABEL]

    def get_cloud_service_location(self):
        return self.virtual_environment[AZURE_UNIT.CLOUD_SERVICE][AZURE_UNIT.CLOUD_SERVICE_LOCATION]

    def get_deployment_slot(self):
        return self.virtual_environment[AZURE_UNIT.DEPLOYMENT][AZURE_UNIT.DEPLOYMENT_DEPLOYMENT_SLOT]

    def get_deployment_name(self):
        return self.virtual_environment[AZURE_UNIT.DEPLOYMENT][AZURE_UNIT.DEPLOYMENT_DEPLOYMENT_NAME]

    def get_virtual_machine_name(self):
        return self.virtual_environment[AZURE_UNIT.ROLE_NAME]

    def get_virtual_machine_label(self):
        return self.virtual_environment[AZURE_UNIT.LABEL]

    def get_virtual_machine_size(self):
        return self.virtual_environment[AZURE_UNIT.ROLE_SIZE]

    def get_remote_provider_name(self):
        return self.virtual_environment[AZURE_UNIT.REMOTE][AZURE_UNIT.REMTOE_PROVIDER]

    def get_remote_port_name(self):
        return self.virtual_environment[AZURE_UNIT.REMOTE][AZURE_UNIT.REMOTE_INPUT_ENDPOINT_NAME]

    def get_remote_paras(self, name, hostname, port):
        r = self.virtual_environment[AZURE_UNIT.REMOTE]
        sc = self.virtual_environment[AZURE_UNIT.SYSTEM_CONFIG]
        remote = {
            AZURE_UNIT.REMOTE_PARAMETER_NAME: name,
            AZURE_UNIT.REMOTE_PARAMETER_DISPLAY_NAME: r[AZURE_UNIT.REMOTE_INPUT_ENDPOINT_NAME],
            AZURE_UNIT.REMOTE_PARAMETER_HOST_NAME: hostname,
            AZURE_UNIT.REMOTE_PARAMETER_PROTOCOL: r[AZURE_UNIT.REMOTE_PROTOCOL],
            AZURE_UNIT.REMOTE_PARAMETER_PORT: port,
            AZURE_UNIT.REMOTE_PARAMETER_USER_NAME: sc[AZURE_UNIT.SYSTEM_CONFIG_USER_NAME],
            AZURE_UNIT.REMOTE_PARAMETER_PASSWORD: sc[AZURE_UNIT.SYSTEM_CONFIG_USER_PASSWORD],
            "enable-sftp": True
        }

        return remote
