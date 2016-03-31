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

import sys

sys.path.append("..")

import random
import string
import json
import pexpect
from os.path import abspath, dirname, realpath

from hackathon import Component, RequiredFeature, Context
from hackathon.hmongo.models import Experiment, VirtualEnvironment
from hackathon.constants import EStatus, VE_PROVIDER, VEStatus, VERemoteProvider
from hackathon_response import internal_server_error

__all__ = ["ExprStarter"]


class ExprStarter(Component):
    """Base for experiment starter"""

    template_library = RequiredFeature("template_library")

    def start_expr(self, context):
        """To start a new Experiment asynchronously

        :type context: Context
        :param context: the execution context.

        """
        expr = Experiment(status=EStatus.INIT,
                          template=context.template,
                          user=context.user,
                          virtual_environments=[],
                          hackathon=context.hackathon)
        expr.save()

        context.template_content = self.template_library.load_template(context.template)
        context.experiment = expr
        expr.status = EStatus.STARTING
        expr.save()

        return self._internal_start_expr(context)

    def stop_expr(self, context):
        """Stop experiment asynchronously

        :type context: Context
        :param context: the execution context.

        """
        return self._internal_stop_expr(context)

    def rollback(self, context):
        """cancel/rollback a expr which is in error state

        :type context: Context
        :param context: the execution context.

        """
        return self._internal_rollback(context)

    def _internal_start_expr(self, context):
        raise NotImplementedError()

    def _internal_stop_expr(self, context):
        raise NotImplementedError()

    def _internal_rollback(self, context):
        raise NotImplementedError()

    def _on_virtual_environment_failed(self, context):
        expr = Experiment.objects(id=context.experiment_id)
        self.rollback(Context(experiment=expr))

    def _on_virtual_environment_success(self, context):
        expr = Experiment.objects(id=context.experiment_id).no_dereference() \
            .only("status", "virtual_environments").first()
        if all(ve.status == VEStatus.RUNNING for ve in expr.virtual_environments):
            expr.status = VEStatus.RUNNING
            expr.save()
            self._on_expr_started(context)

        self._hooks_on_virtual_environment_success(context)

    def _on_virtual_environment_stopped(self, context):
        expr = Experiment.objects(id=context.experiment_id).no_dereference() \
            .only("status", "virtual_environments").first()
        ve = expr.virtual_environments.get(name=context.virtual_environment_name)
        ve.status = VEStatus.STOPPED

        if all(ve.status == VEStatus.STOPPED for ve in expr.virtual_environments):
            expr.status = VEStatus.STOPPED
            expr.save()

    def _on_virtual_environment_unexpected_error(self, context):
        self.log.warn("experiment unexpected error: " + context.experiment_id)
        expr = Experiment.objects(id=context.experiment_id).no_dereference() \
            .only("status", "virtual_environments").first()
        if "virtual_environment_name" in context:
            expr.virtual_environments.get(name=context.virtual_environment_name).status = VEStatus.UNEXPECTED_ERROR
        expr.save()

    def _hooks_on_virtual_environment_success(self, context):
        pass

    def _on_expr_started(self, context):
        # send notice
        pass


class DockerExprStarter(ExprStarter):
    def _internal_rollback(self, context):
        # currently rollback share the same process as stop
        self._internal_stop_expr(context)

    def _stop_virtual_environment(self, virtual_environment, experiment, context):
        pass

    def _internal_start_expr(self, context):
        try:
            virtual_environments_units = context.template_content.units
            map(lambda ve_unit: self.__start_virtual_environment(context, ve_unit), virtual_environments_units)
            return context
        except Exception as e:
            self.log.error(e)
            self.log.error("Failed starting containers")
            self.rollback(context)
            return internal_server_error('Failed in starting containers')

    def _internal_start_virtual_environment(self, context, docker_template_unit):
        raise NotImplementedError()

    def _get_docker_proxy(self):
        raise NotImplementedError()

    def _internal_stop_expr(self, context):
        expr = Experiment.objects(id=context.experiment_id).first()
        if not expr:
            return

        # delete containers and change expr status
        for ve in expr.virtual_environments:
            context = context.copy()  # create new context for every virtual_environment
            context.virtual_environment_name = ve.name
            self._stop_virtual_environment(ve, expr, context)

    def __start_virtual_environment(self, context, docker_template_unit):
        origin_name = docker_template_unit.get_name()
        prefix = str(context.experiment.id)[0:9]
        suffix = "".join(random.sample(string.ascii_letters + string.digits, 8))
        new_name = '%s-%s-%s' % (prefix, origin_name, suffix.lower())
        docker_template_unit.set_name(new_name)
        self.log.debug("starting to start container: %s" % new_name)

        # db document for VirtualEnvironment
        ve = VirtualEnvironment(provider=VE_PROVIDER.DOCKER,
                                name=new_name,
                                image=docker_template_unit.get_image_with_tag(),
                                status=VEStatus.INIT,
                                remote_provider=VERemoteProvider.Guacamole,
                                experiment=context.experiment)
        # create a new context for current ve only
        context = context.copy()
        context.experiment.virtual_environments.append(ve)
        context.experiment.save()

        # start container remotely , use hosted docker or alauda docker
        self._internal_start_virtual_environment(context, docker_template_unit)

    def _enable_guacd_file_transfer(self, context):
        """
        This function should be invoked after container is started in alauda_docker.py and hosted_docker.py
        :param ve: virtual environment
        """
        expr = Experiment.objects(id=context.experiment_id).no_dereference() \
            .only("virtual_environments").first()
        virtual_env = expr.virtual_environments.get(name=context.virtual_environment_name)
        remote = virtual_env.remote_para

        p = pexpect.spawn("scp -P %s %s %s@%s:/usr/local/sbin/guacctl" %
                          (remote["port"],
                           abspath("%s/../expr/guacctl" % dirname(realpath(__file__))),
                           remote["username"],
                           remote["hostname"]))
        i = p.expect([pexpect.TIMEOUT, 'yes/no', 'password: '])
        if i == 1:
            p.sendline("yes")
            i = p.expect([pexpect.TIMEOUT, 'password:'])

        if i != 0:
            p.sendline(remote["password"])
            p.expect(pexpect.EOF)
        p.close()