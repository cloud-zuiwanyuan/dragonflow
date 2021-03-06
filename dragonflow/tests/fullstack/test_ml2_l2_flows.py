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

import re

from oslo_config import cfg

from dragonflow.controller.common import constants as const
from dragonflow.tests.common import utils
from dragonflow.tests.fullstack import test_base
from dragonflow.tests.fullstack import test_objects as objects

ML2_CONF_INI = '/etc/neutron/plugins/ml2/ml2_conf.ini'
L2_ML2_APP_NAME = 'l2_ml2_app.L2App'


class TestL2FLows(test_base.DFTestBase):
    def _get_metadata_id(self, flows, ip, mac):
        for flow in flows:
            if flow['table'] == str(const.L3_PROACTIVE_LOOKUP_TABLE):
                if 'nw_dst=' + ip in flow['match'] and mac in flow['actions']:
                    m = re.search('metadata=0x([0-9a-f]+)', flow['match'])
                    if m:
                        return m.group(1)
        return None

    def _get_vm_port(self, ip, mac):
        ports = self.nb_api.get_all_logical_ports()
        for port in ports:
            if port.get_device_owner() == 'compute:None':
                if port.get_ip() == ip and port.get_mac() == mac:
                    return port
        return None

    def test_tunnel_network_flows(self):
        if self._check_l2_ml2_app_enable() is False:
            return

        network = self.store(objects.NetworkTestObj(self.neutron, self.nb_api))
        network_id = network.create(network={'name': 'private'})
        network_params = network.get_network()
        segmentation_id = network_params['network']['provider:segmentation_id']
        subnet = {'network_id': network_id,
            'cidr': '10.200.0.0/24',
            'gateway_ip': '10.200.0.1',
            'ip_version': 4,
            'name': 'private',
            'enable_dhcp': True}
        subnet = self.neutron.create_subnet({'subnet': subnet})
        self.assertIsNotNone(subnet)

        ovs = utils.OvsFlowsParser()
        vm = self.store(objects.VMTestObj(self, self.neutron))
        vm.create(network=network)
        ip = vm.get_first_ipv4()
        self.assertIsNotNone(ip)
        self.assertIsNotNone(vm.server.addresses['private'])
        mac = vm.server.addresses['private'][0]['OS-EXT-IPS-MAC:mac_addr']
        self.assertIsNotNone(mac)
        metadataid = utils.wait_until_is_and_return(
            lambda: self._get_metadata_id(ovs.dump(self.integration_bridge),
                                          ip, mac),
            exception=Exception('Metadata id was not found in OpenFlow rules')
        )
        port = utils.wait_until_is_and_return(
            lambda: self._get_vm_port(ip, mac),
            exception=Exception('No port assigned to VM')
        )
        tunnel_key = port.get_tunnel_key()
        tunnel_key_hex = hex(tunnel_key)
        r = self._check_tunnel_flows(ovs.dump(self.integration_bridge),
                                     metadataid,
                                     hex(segmentation_id),
                                     tunnel_key_hex,
                                     mac)
        self.assertIsNotNone(r)
        vm.close()
        network.close()

    def _check_tunnel_flows(self, flows, metadtata, segmentation_id,
                          port_key_hex, mac):
        l2_lookup_unicast_match = 'metadata=0x' + metadtata + \
                                 ',dl_dst=' + mac
        l2_lookup_unicast_action = 'goto_table:' + \
                                   str(const.EGRESS_TABLE)
        l2_lookup_multicast_match = 'metadata=0x' + metadtata + ',dl_dst=' + \
                                    '01:00:00:00:00:00/01:00:00:00:00:00'
        l2_lookup_multicast_action = 'set_field:' + port_key_hex + \
                                     '->reg7,resubmit(,' + \
                                     str(const.EGRESS_TABLE) + ')' + \
                                     ',set_field:0' + \
                                     '->reg7,resubmit(,' + \
                                     str(const.EGRESS_TABLE) + ')'

        ingress_match = 'tun_id=' + str(segmentation_id)
        ingress_action = 'set_field:0x' + metadtata + '->metadata,' + \
                         'goto_table:' + \
                         str(const.INGRESS_DESTINATION_PORT_LOOKUP_TABLE)

        l2_lookup_unicast_check = None
        l2_lookup_multicast_check = None
        ingress_check = None

        for flow in flows:
            if flow['table'] == str(const.L2_LOOKUP_TABLE):
                if (l2_lookup_multicast_match in flow['match']):
                    if l2_lookup_multicast_action in flow['actions']:
                        l2_lookup_multicast_check = True
                if (l2_lookup_unicast_match in flow['match']):
                    if l2_lookup_unicast_action in flow['actions']:
                        l2_lookup_unicast_check = True

            if flow['table'] == str(
                    const.INGRESS_CLASSIFICATION_DISPATCH_TABLE):
                if (ingress_match in flow['match']):
                    if ingress_action in flow['actions']:
                        ingress_check = True

        if l2_lookup_multicast_check is None or \
            l2_lookup_unicast_check is None or \
            ingress_check is None:
            return None

        return True

    def _check_l2_ml2_app_enable(self):
        apps_list = cfg.CONF.df.apps_list
        if L2_ML2_APP_NAME in apps_list:
            return True
        return False
