
# Copyright 2013-present Barefoot Networks, Inc.
# Copyright 2018-present Open Networking Foundation
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
import Queue
import argparse
import json
import logging
import os
import re
import struct
import subprocess
import sys
import threading
import datetime
from collections import OrderedDict
import time
from StringIO import StringIO
from collections import Counter
from functools import wraps, partial
from unittest import SkipTest

import google.protobuf.text_format
import grpc
from p4.tmp import p4config_pb2
from p4.v1 import p4runtime_pb2

from basic import P4RuntimeClient

from convert import encode

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("pi_client")

def readTableRules(sw, table_name = None):
    """
    Reads the table entries from all tables on the switch.
    :param p4info_helper: the P4Info helper
    :param sw: the switch connection
    """

    rule_counter = 0

    print '\n----- Reading tables rules -----'
    if table_name is not None:
        t_id = sw.get_table_id(table_name)
    else:
        t_id = None
    for response in sw.ReadTableEntries(table_id = t_id):
        for entity in response.entities:
            entry = entity.table_entry
            # TODO For extra credit, you can use the p4info_helper to translate
            #      the IDs the entry to names
            table_name = sw.p4info_helper.get_tables_name(entry.table_id)
            print '%s: ' % table_name,
            for m in entry.match:
                print sw.p4info_helper.get_match_field_name(table_name, m.field_id),
                print '%r' % (sw.p4info_helper.get_match_field_value(m),),
            action = entry.action.action
            action_name = sw.p4info_helper.get_actions_name(action.action_id)
            print '->', action_name,
            for p in action.params:
                print sw.p4info_helper.get_action_param_name(action_name, p.param_id),
                print '%r' % p.value,
            print
            rule_counter = rule_counter + 1
    print("======================================================")
    print("Total number of rules: %d" % rule_counter)
    print

def error(msg, *args, **kwargs):
    logger.error(msg, *args, **kwargs)


def warn(msg, *args, **kwargs):
    logger.warn(msg, *args, **kwargs)


def info(msg, *args, **kwargs):
    logger.info(msg, *args, **kwargs)

def choose_fwd_type(switch_instance):
    print("======================================")
    print("FWD_TYPE: ")
    print("(0) IPv4 LPM (1) Per-packet ECMP")
    print("(2) PPS(Shared counter), (3) PPS(Independent counter)")
    print("Enter 999 to read table entries")
    input_number = raw_input("Enter a number to choose FWD_TYPE: ")
    try:
        input_number = int(input_number)
        if (input_number == 0):
            print("Changing FWD_TYPE to IPv4 LPM")
            _act_param = {"fwd_type": 0, "priority": 0}
        elif (input_number == 1):
            print("Changing FWD_TYPE to Per-packet ECMP")
            _act_param = {"fwd_type": 1, "priority": 0}
        elif (input_number == 2):
            print("Changing FWD_TYPE to PPS(shared counter)")
            _act_param = {"fwd_type": 2, "priority": 0}
        elif (input_number == 3):
            print("Changing FWD_TYPE to PPS(Independent counter)")
            _act_param = {"fwd_type": 3, "priority": 0}
        elif (input_number == 999):
            readTableRules(switch_instance)
            return
        else:
            print("Please enter a correct number!")
            return

        table_name = "set_source_or_sink"
        print("Modify %s Flow Entries" % table_name)
        table_entry = switch_instance.p4info_helper.buildTableEntry(
            table_name="MyIngress.set_source_or_sink",
            match_fields={
                "standard_metadata.ingress_port": (1, 4)
            },
            action_name="MyIngress.set_source",
            action_params= _act_param,
            priority=1)
        switch_instance.WriteTableEntry(table_entry, modify_entry=True)
        print("%s rule installed" % table_name)
        
    except ValueError:
        print("Please enter a number!")
    


def main():
    parser = argparse.ArgumentParser(
        description="A simple P4Runtime Client")
    parser.add_argument('--device',
                        help='Target device',
                        type=str, action="store", required=True,
                        choices=['tofino', 'bmv2'])
    parser.add_argument('--p4info',
                        help='Location of p4info proto in text format',
                        type=str, action="store", required=True, 
                        default='/home/sdn/onos/pipelines/basic/src/main/resources/p4c-out/bmv2/basic.p4info')
    parser.add_argument('--config',
                        help='Location of Target Dependant Binary',
                        type=str, action="store",
                        default='/home/sdn/onos/pipelines/basic/src/main/resources/p4c-out/bmv2/basic.json')
    parser.add_argument('--ctx-json',
                        help='Location of Context.json',
                        type=str, action="store")
    parser.add_argument('--grpc-addr',
                        help='Address to use to connect to P4 Runtime server',
                        type=str, default='localhost:50051')
    parser.add_argument('--device-id',
                        help='Device id for device under test',
                        type=int, required=True, default=0)
    parser.add_argument('--skip-config',
                        help='Assume a device with pipeline already configured',
                        action="store_true", default=False)
    parser.add_argument('--skip-role-config',
                        help='Assume a device do not need role config',
                        action="store_true", default=False)
    parser.add_argument('--election-id',
                        help='ID for mastership election',
                        type=int, required=True, default=False)
    parser.add_argument('--role-id',
                        help='ID for distinguish different client',
                        type=int, required=False, default=0)
    args, unknown_args = parser.parse_known_args()

    # device = args.device

    if not os.path.exists(args.p4info):
        error("P4Info file {} not found".format(args.p4info))
        sys.exit(1)

    # grpc_port = args.grpc_addr.split(':')[1]

    try:
        print "Try to connect to P4Runtime Server"
        s1 = P4RuntimeClient(grpc_addr = args.grpc_addr, 
                             device_id = args.device_id, 
                             device = args.device,
                             election_id = args.election_id,
                             role_id = args.role_id,
                             config_path = args.config,
                             p4info_path = args.p4info,
                             ctx_json = args.ctx_json)
        s1.handshake()
        if not args.skip_config:
            s1.update_config()

        if not args.skip_role_config:
            # Role config must be set after fwd pipeline or table info not appear in server may cause server crash.
            roleconfig = s1.get_new_roleconfig()
            s1.add_roleconfig_entry(roleconfig, "ingress.table0_control.table0", 1)
            s1.add_roleconfig_entry(roleconfig, "ingress.table1_control.table1", 1)
            s1.handshake(roleconfig)


        # IPv4_lpm
        table_name = "ipv4_lpm"
        print("Insert %s Flow Entries" % table_name)
        table_entry = s1.p4info_helper.buildTableEntry(
            table_name="MyIngress.ipv4_lpm",
            match_fields={
                "hdr.ipv4.dstAddr": ("10.0.1.1", 32)
            },
            action_name="MyIngress.ipv4_forward",
            action_params={
                "dstAddr": "00:00:00:00:01:01",
                "port": 1
            })
        s1.WriteTableEntry(table_entry)
        print("%s rule installed" % table_name)


        print("Insert %s Flow Entries" % table_name)
        table_entry = s1.p4info_helper.buildTableEntry(
            table_name="MyIngress.ipv4_lpm",
            match_fields={
                "hdr.ipv4.dstAddr": ("10.0.1.2", 32)
            },
            action_name="MyIngress.ipv4_forward",
            action_params={
                "dstAddr": "00:00:00:00:01:02",
                "port": 2
            })
        s1.WriteTableEntry(table_entry)
        print("%s rule installed" % table_name)


        print("Insert %s Flow Entries" % table_name)
        table_entry = s1.p4info_helper.buildTableEntry(
            table_name="MyIngress.ipv4_lpm",
            match_fields={
                "hdr.ipv4.dstAddr": ("10.0.1.3", 32)
            },
            action_name="MyIngress.ipv4_forward",
            action_params={
                "dstAddr": "00:00:00:00:01:03",
                "port": 3
            })
        s1.WriteTableEntry(table_entry)
        print("%s rule installed" % table_name)


        print("Insert %s Flow Entries" % table_name)
        table_entry = s1.p4info_helper.buildTableEntry(
            table_name="MyIngress.ipv4_lpm",
            match_fields={
                "hdr.ipv4.dstAddr": ("10.0.1.4", 32)
            },
            action_name="MyIngress.ipv4_forward",
            action_params={
                "dstAddr": "00:00:00:00:01:01",
                "port": 4
            })
        s1.WriteTableEntry(table_entry)
        print("%s rule installed" % table_name)


        print("Insert %s Flow Entries" % table_name)
        table_entry = s1.p4info_helper.buildTableEntry(
            table_name="MyIngress.ipv4_lpm",
            match_fields={
                "hdr.ipv4.dstAddr": ("10.0.2.0", 24)
            },
            action_name="MyIngress.ipv4_forward",
            action_params={
                "dstAddr": "00:00:00:04:02:00",
                "port": 6
            })
        s1.WriteTableEntry(table_entry)
        print("%s rule installed" % table_name)


        print("Insert %s Flow Entries" % table_name)
        table_entry = s1.p4info_helper.buildTableEntry(
            table_name="MyIngress.ipv4_lpm",
            match_fields={
                "hdr.ipv4.dstAddr": ("10.0.7.0", 24)
            },
            action_name="MyIngress.ipv4_forward",
            action_params={
                "dstAddr": "00:00:00:03:07:00",
                "port": 5
            })
        s1.WriteTableEntry(table_entry)
        print("%s rule installed" % table_name)


        print("Insert %s Flow Entries" % table_name)
        table_entry = s1.p4info_helper.buildTableEntry(
            table_name="MyIngress.ipv4_lpm",
            match_fields={
                "hdr.ipv4.dstAddr": ("10.0.8.0", 24)
            },
            action_name="MyIngress.ipv4_forward",
            action_params={
                "dstAddr": "00:00:00:04:08:00",
                "port": 6
            })
        s1.WriteTableEntry(table_entry)
        print("%s rule installed" % table_name)


        # Set the sink direction
        table_name = "set_sink_direction"
        print("Insert %s Flow Entries" % table_name)
        table_entry = s1.p4info_helper.buildTableEntry(
            table_name="MyIngress.set_sink_direction",
            match_fields={
                "hdr.ipv4.dstAddr": ("10.0.1.0", 24)
            },
            action_name="MyIngress.set_direction",
            action_params={
                "direction": 1
            })
        s1.WriteTableEntry(table_entry)
        print("%s rule installed" % table_name)


        print("Insert %s Flow Entries" % table_name)
        table_entry = s1.p4info_helper.buildTableEntry(
            table_name="MyIngress.set_sink_direction",
            match_fields={
                "hdr.ipv4.dstAddr": ("10.0.2.0", 24)
            },
            action_name="MyIngress.set_direction",
            action_params={
                "direction": 2
            })
        s1.WriteTableEntry(table_entry)
        print("%s rule installed" % table_name)


        print("Insert %s Flow Entries" % table_name)
        table_entry = s1.p4info_helper.buildTableEntry(
            table_name="MyIngress.set_sink_direction",
            match_fields={
                "hdr.ipv4.dstAddr": ("10.0.7.0", 24)
            },
            action_name="MyIngress.set_direction",
            action_params={
                "direction": 3
            })
        s1.WriteTableEntry(table_entry)
        print("%s rule installed" % table_name)


        print("Insert %s Flow Entries" % table_name)
        table_entry = s1.p4info_helper.buildTableEntry(
            table_name="MyIngress.set_sink_direction",
            match_fields={
                "hdr.ipv4.dstAddr": ("10.0.8.0", 24)
            },
            action_name="MyIngress.set_direction",
            action_params={
                "direction": 4
            })
        s1.WriteTableEntry(table_entry)
        print("%s rule installed" % table_name)


        # Set ecmp count rule
        # Remember to add priority if you use "Range" match
        table_name = "forward_by_ecmp_count"
        print("Insert %s Flow Entries" % table_name)
        table_entry = s1.p4info_helper.buildTableEntry(
            table_name="MyIngress.forward_by_ecmp_count",
            match_fields={
                "hdr.myheader.direction": 2,
                "meta.ecmp_count": (0, 0)
            },
            action_name="MyIngress.send_to_port",
            action_params={
                "port": 5
            },
            priority=1)
        s1.WriteTableEntry(table_entry)
        print("%s rule installed" % table_name)


        print("Insert %s Flow Entries" % table_name)
        table_entry = s1.p4info_helper.buildTableEntry(
            table_name="MyIngress.forward_by_ecmp_count",
            match_fields={
                "hdr.myheader.direction": 2,
                "meta.ecmp_count": (1, 1)
            },
            action_name="MyIngress.send_to_port",
            action_params={
                "port": 6
            },
            priority=1)
        s1.WriteTableEntry(table_entry)
        print("%s rule installed" % table_name)


        print("Insert %s Flow Entries" % table_name)
        table_entry = s1.p4info_helper.buildTableEntry(
            table_name="MyIngress.forward_by_ecmp_count",
            match_fields={
                "hdr.myheader.direction": 3,
                "meta.ecmp_count": (0, 0)
            },
            action_name="MyIngress.send_to_port",
            action_params={
                "port": 5
            },
            priority=1)
        s1.WriteTableEntry(table_entry)
        print("%s rule installed" % table_name)


        print("Insert %s Flow Entries" % table_name)
        table_entry = s1.p4info_helper.buildTableEntry(
            table_name="MyIngress.forward_by_ecmp_count",
            match_fields={
                "hdr.myheader.direction": 3,
                "meta.ecmp_count": (1, 1)
            },
            action_name="MyIngress.send_to_port",
            action_params={
                "port": 6
            },
            priority=1)
        s1.WriteTableEntry(table_entry)
        print("%s rule installed" % table_name)


        print("Insert %s Flow Entries" % table_name)
        table_entry = s1.p4info_helper.buildTableEntry(
            table_name="MyIngress.forward_by_ecmp_count",
            match_fields={
                "hdr.myheader.direction": 4,
                "meta.ecmp_count": (0, 0)
            },
            action_name="MyIngress.send_to_port",
            action_params={
                "port": 5
            },
            priority=1)
        s1.WriteTableEntry(table_entry)
        print("%s rule installed" % table_name)


        print("Insert %s Flow Entries" % table_name)
        table_entry = s1.p4info_helper.buildTableEntry(
            table_name="MyIngress.forward_by_ecmp_count",
            match_fields={
                "hdr.myheader.direction": 4,
                "meta.ecmp_count": (1, 1)
            },
            action_name="MyIngress.send_to_port",
            action_params={
                "port": 6
            },
            priority=1)
        s1.WriteTableEntry(table_entry)
        print("%s rule installed" % table_name)


        # Forward by packet counter
        table_name = "forward_by_packet_counter"
        print("Insert %s Flow Entries" % table_name)
        table_entry = s1.p4info_helper.buildTableEntry(
            table_name="MyIngress.forward_by_packet_counter",
            match_fields={
                "hdr.myheader.direction": 2,
                "meta.packet_counter": (0, 0)
            },
            action_name="MyIngress.send_to_port",
            action_params={
                "port": 5
            },
            priority=1)
        s1.WriteTableEntry(table_entry)
        print("%s rule installed" % table_name)


        print("Insert %s Flow Entries" % table_name)
        table_entry = s1.p4info_helper.buildTableEntry(
            table_name="MyIngress.forward_by_packet_counter",
            match_fields={
                "hdr.myheader.direction": 2,
                "meta.packet_counter": (1, 1)
            },
            action_name="MyIngress.send_to_port",
            action_params={
                "port": 6
            },
            priority=1)
        s1.WriteTableEntry(table_entry)
        print("%s rule installed" % table_name)


        # Forward by packet counter
        table_name = "forward_by_packet_counter"
        print("Insert %s Flow Entries" % table_name)
        table_entry = s1.p4info_helper.buildTableEntry(
            table_name="MyIngress.forward_by_packet_counter",
            match_fields={
                "hdr.myheader.direction": 3,
                "meta.packet_counter": (0, 0)
            },
            action_name="MyIngress.send_to_port",
            action_params={
                "port": 5
            },
            priority=1)
        s1.WriteTableEntry(table_entry)
        print("%s rule installed" % table_name)


        print("Insert %s Flow Entries" % table_name)
        table_entry = s1.p4info_helper.buildTableEntry(
            table_name="MyIngress.forward_by_packet_counter",
            match_fields={
                "hdr.myheader.direction": 3,
                "meta.packet_counter": (1, 1)
            },
            action_name="MyIngress.send_to_port",
            action_params={
                "port": 6
            },
            priority=1)
        s1.WriteTableEntry(table_entry)
        print("%s rule installed" % table_name)


        print("Insert %s Flow Entries" % table_name)
        table_entry = s1.p4info_helper.buildTableEntry(
            table_name="MyIngress.forward_by_packet_counter",
            match_fields={
                "hdr.myheader.direction": 4,
                "meta.packet_counter": (0, 0)
            },
            action_name="MyIngress.send_to_port",
            action_params={
                "port": 5
            },
            priority=1)
        s1.WriteTableEntry(table_entry)
        print("%s rule installed" % table_name)


        print("Insert %s Flow Entries" % table_name)
        table_entry = s1.p4info_helper.buildTableEntry(
            table_name="MyIngress.forward_by_packet_counter",
            match_fields={
                "hdr.myheader.direction": 4,
                "meta.packet_counter": (1, 1)
            },
            action_name="MyIngress.send_to_port",
            action_params={
                "port": 6
            },
            priority=1)
        s1.WriteTableEntry(table_entry)
        print("%s rule installed" % table_name)


        # Set source or sink switch by port
        # FWD TYPE:
        #  0: IPv4_LPM, 1: ECMP, 2: PPS(shared counter),
        #  3: PPS (independent counter)
        table_name = "set_source_or_sink"
        print("Insert %s Flow Entries" % table_name)
        table_entry = s1.p4info_helper.buildTableEntry(
            table_name="MyIngress.set_source_or_sink",
            match_fields={
                "standard_metadata.ingress_port": (1, 4)
            },
            action_name="MyIngress.set_source",
            action_params={
                "fwd_type": 1,
                "priority": 0
            },
            priority=1)
        s1.WriteTableEntry(table_entry)
        print("%s rule installed" % table_name)


        print("Insert %s Flow Entries" % table_name)
        table_entry = s1.p4info_helper.buildTableEntry(
            table_name="MyIngress.set_source_or_sink",
            match_fields={
                "standard_metadata.ingress_port": (5, 6)
            },
            action_name="MyIngress.set_sink",
            action_params={
            },
            priority=1)
        s1.WriteTableEntry(table_entry)
        print("%s rule installed" % table_name)


        # Set maximum number of counter
        table_name = "set_counter_max"
        print("Insert %s Flow Entries" % table_name)
        table_entry = s1.p4info_helper.buildTableEntry(
            table_name="MyIngress.set_counter_max",
            match_fields={
                "hdr.myheader.direction": 1
            },
            action_name="MyIngress.set_counter_max_value",
            action_params={
                "max": 2
            })
        s1.WriteTableEntry(table_entry)
        print("%s rule installed" % table_name)


        print("Insert %s Flow Entries" % table_name)
        table_entry = s1.p4info_helper.buildTableEntry(
            table_name="MyIngress.set_counter_max",
            match_fields={
                "hdr.myheader.direction": 2
            },
            action_name="MyIngress.set_counter_max_value",
            action_params={
                "max": 2
            })
        s1.WriteTableEntry(table_entry)
        print("%s rule installed" % table_name)


        print("Insert %s Flow Entries" % table_name)
        table_entry = s1.p4info_helper.buildTableEntry(
            table_name="MyIngress.set_counter_max",
            match_fields={
                "hdr.myheader.direction": 3
            },
            action_name="MyIngress.set_counter_max_value",
            action_params={
                "max": 2
            })
        s1.WriteTableEntry(table_entry)
        print("%s rule installed" % table_name)


        print("Insert %s Flow Entries" % table_name)
        table_entry = s1.p4info_helper.buildTableEntry(
            table_name="MyIngress.set_counter_max",
            match_fields={
                "hdr.myheader.direction": 4
            },
            action_name="MyIngress.set_counter_max_value",
            action_params={
                "max": 2
            })
        s1.WriteTableEntry(table_entry)
        print("%s rule installed" % table_name)


        # Get an ECMP hash number
        table_name = "ecmp_select_next_port"
        print("Insert %s Flow Entries" % table_name)
        table_entry = s1.p4info_helper.buildTableEntry(
            table_name="MyIngress.ecmp_select_next_port",
            match_fields={
                "hdr.myheader.direction": 1
            },
            action_name="MyIngress.ecmp_set_next_port",
            action_params={
                "ecmp_base": 0,
                "ecmp_count": 2
            })
        s1.WriteTableEntry(table_entry)
        print("%s rule installed" % table_name)


        print("Insert %s Flow Entries" % table_name)
        table_entry = s1.p4info_helper.buildTableEntry(
            table_name="MyIngress.ecmp_select_next_port",
            match_fields={
                "hdr.myheader.direction": 2
            },
            action_name="MyIngress.ecmp_set_next_port",
            action_params={
                "ecmp_base": 0,
                "ecmp_count": 2
            })
        s1.WriteTableEntry(table_entry)
        print("%s rule installed" % table_name)


        print("Insert %s Flow Entries" % table_name)
        table_entry = s1.p4info_helper.buildTableEntry(
            table_name="MyIngress.ecmp_select_next_port",
            match_fields={
                "hdr.myheader.direction": 3
            },
            action_name="MyIngress.ecmp_set_next_port",
            action_params={
                "ecmp_base": 0,
                "ecmp_count": 2
            })
        s1.WriteTableEntry(table_entry)
        print("%s rule installed" % table_name)


        print("Insert %s Flow Entries" % table_name)
        table_entry = s1.p4info_helper.buildTableEntry(
            table_name="MyIngress.ecmp_select_next_port",
            match_fields={
                "hdr.myheader.direction": 4
            },
            action_name="MyIngress.ecmp_set_next_port",
            action_params={
                "ecmp_base": 0,
                "ecmp_count": 2
            })
        s1.WriteTableEntry(table_entry)
        print("%s rule installed" % table_name)


        readTableRules(s1)


        while 1:
            choose_fwd_type(s1)

            #s1.packetin_rdy.wait()
            #packetin = s1.get_packet_in()
            #if packetin:
                # Print Packet from CPU_PORT of Switch
                #print " ".join("{:02x}".format(ord(c)) for c in packetin.payload)

                # Print metadatas:
                #     1. packet_in switch port (9 bits)
                #     2. padding (7 bits)
                #for metadata_ in packetin.metadata:
                    #print " ".join("{:02x}".format(ord(c)) for c in metadata_.value)

    except Exception:
        raise
        s1.tearDown()

    except KeyboardInterrupt:
        s1.tearDown()


if __name__ == '__main__':
    main()
