# Copyright (c) 2016 OpenStack Foundation.
#
# All Rights Reserved.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

import mock
import six
import testtools

from neutron import context as nctx
from neutron import manager
from neutron.tests.unit.plugins.ml2 import test_plugin


class empty_wrapper(object):
    def __init__(self, type):
        pass

    def __call__(self, f):
        @six.wraps(f)
        def wrapped_f(*args, **kwargs):
            return f(*args, **kwargs)
        return wrapped_f


class DFL3RouterPluginBase(test_plugin.Ml2PluginV2TestCase):

    l3_plugin = ('dragonflow.neutron.services.l3_router_plugin.'
                 'DFL3RouterPlugin')

    def setUp(self):
        lock_db = mock.patch('dragonflow.db.neutron.lockedobjects_db').start()
        lock_db.wrap_db_lock = empty_wrapper
        nbapi_instance = mock.patch('dragonflow.db.api_nb.NbApi').start()
        nbapi_instance.get_instance.return_value = mock.MagicMock()
        super(DFL3RouterPluginBase, self).setUp()
        self.l3p = (manager.NeutronManager.
                    get_service_plugins()['L3_ROUTER_NAT'])
        self.nb_api = self.l3p.nb_api
        self.ctx = nctx.get_admin_context()


class TestDFL3RouterPlugin(DFL3RouterPluginBase):

    @mock.patch('neutron.db.l3_db.L3_NAT_db_mixin.create_floatingip')
    def test_create_floatingip_failed_in_neutron(self, func):
        func.side_effect = Exception("The exception")
        with testtools.ExpectedException(Exception):
            self.l3p.create_floatingip(self.ctx, mock.ANY)


class TestDFL3RouterPluginRevision(DFL3RouterPluginBase):

    def get_additional_service_plugins(self):
        p = super(TestDFL3RouterPluginRevision,
                  self).get_additional_service_plugins()
        p.update({'revision_plugin_name': 'revisions'})
        return p

    def _test_create_router_revision(self):
        r = {'router': {'name': 'router', 'tenant_id': 'tenant',
                        'admin_state_up': True}}
        router = self.l3p.create_router(self.ctx, r)
        self.assertGreater(router['revision_number'], 0)
        self.nb_api.create_lrouter.assert_called_once_with(
            router['id'], topic='tenant', name='router', distributed=False,
            version=router['revision_number'], ports=[])
        return router

    def test_create_update_router_revision(self):
        router = self._test_create_router_revision()
        old_version = router['revision_number']
        router['name'] = 'another_router'
        new_router = self.l3p.update_router(
            self.ctx, router['id'], {'router': router})
        self.assertGreater(new_router['revision_number'], old_version)

    def test_add_delete_router_interface_revision(self):
        router = self._test_create_router_revision()
        old_version = router['revision_number']
        with self.subnet() as s:
            data = {'subnet_id': s['subnet']['id']}
            router_port_info = self.l3p.add_router_interface(
                self.ctx, router['id'], data)
            router_with_int = self.l3p.get_router(self.ctx, router['id'])
            self.assertGreater(router_with_int['revision_number'],
                               old_version)
            self.nb_api.add_lrouter_port.assert_called_once_with(
                router_port_info['port_id'], router_port_info['id'],
                router_port_info['network_id'],
                router_port_info['tenant_id'],
                router_version=router_with_int['revision_number'],
                mac=mock.ANY, network=mock.ANY, tunnel_key=mock.ANY)

            router_port_info = self.l3p.remove_router_interface(
                 self.ctx, router['id'], data)
            router_without_int = self.l3p.get_router(self.ctx, router['id'])
            self.assertGreater(router_without_int['revision_number'],
                               router_with_int['revision_number'])
            self.nb_api.delete_lrouter_port.assert_called_once_with(
                 router_port_info['port_id'], router_port_info['id'],
                 router_port_info['tenant_id'],
                 router_version=router_without_int['revision_number'])

    def _test_create_floatingip_revision(self):
        kwargs = {'arg_list': ('router:external',),
                  'router:external': True}
        with self.network(**kwargs) as n:
            with self.subnet(network=n):
                floatingip = self.l3p.create_floatingip(
                    self.ctx,
                    {'floatingip': {'floating_network_id': n['network']['id'],
                                    'tenant_id': n['network']['tenant_id']}})
                self.assertGreater(floatingip['revision_number'], 0)
                self.nb_api.create_floatingip.assert_called_once_with(
                    id=floatingip['id'],
                    topic=floatingip['tenant_id'],
                    version=floatingip['revision_number'],
                    name=mock.ANY, floating_ip_address=mock.ANY,
                    floating_network_id=mock.ANY, router_id=mock.ANY,
                    port_id=mock.ANY, fixed_ip_address=mock.ANY,
                    status=mock.ANY, floating_port_id=mock.ANY,
                    floating_mac_address=mock.ANY,
                    external_gateway_ip=mock.ANY,
                    external_cidr=mock.ANY)
        return floatingip

    def test_create_update_floatingip_revision(self):
        floatingip = self._test_create_floatingip_revision()
        old_version = floatingip['revision_number']
        floatingip['tenant_id'] = 'another_tenant'
        new_fip = self.l3p.update_floatingip(
            self.ctx, floatingip['id'], {'floatingip': floatingip})
        self.assertGreater(new_fip['revision_number'], old_version)
        self.nb_api.update_floatingip.assert_called_once_with(
            id=floatingip['id'], topic=new_fip['tenant_id'],
            notify=True, name=mock.ANY, router_id=mock.ANY,
            port_id=mock.ANY, version=new_fip['revision_number'],
            fixed_ip_address=mock.ANY)

    def test_create_floatingip_with_normal_user(self):
        normal_context = nctx.Context(is_admin=False, overwrite=False)
        kwargs = {'arg_list': ('router:external',),
                  'router:external': True}
        with self.network(**kwargs) as n:
            with self.subnet(network=n):
                floatingip = self.l3p.create_floatingip(
                    normal_context,
                    {'floatingip': {'floating_network_id': n['network']['id'],
                                    'tenant_id': n['network']['tenant_id']}})
                self.assertTrue(floatingip)
