import argparse
import time
from itertools import count
from tkinter import *
from socket import ntohs, socket, PF_PACKET, SOCK_RAW

import protocols

indent = ' ' * 4  # Basic indentation level


class PacketSniffer:
    def __init__(self, interface: str):
        self.interface = interface
        self.data = None
        self.protocol_queue = ['Ethernet']
        self.__observers = list()

    def register(self, observer):
        self.__observers.append(observer)

    def __notify_all(self, *args):
        for observer in self.__observers:
            observer.update(*args)
        del self.protocol_queue[1:]

    def execute(self):
        with socket(PF_PACKET, SOCK_RAW, ntohs(0x0003)) as sock:
            if self.interface is not None:
                sock.bind((self.interface, 0))
            for self.packet_num in count(1):
                raw_packet = sock.recv(2048)
                start: int = 0
                for proto in self.protocol_queue:
                    try:
                        proto_class = getattr(protocols, proto)
                    except AttributeError:
                        print("Unknown Protocol")
                        continue
                    end: int = start + proto_class.header_len
                    protocol = proto_class(raw_packet[start:end])
                    setattr(self, proto.lower(), protocol)
                    if protocol.encapsulated_proto is None:
                        break
                    self.protocol_queue.append(protocol.encapsulated_proto)
                    start = end
                self.data = raw_packet[end:]
                self.__notify_all(self)


class PacketDisplay:
    def __init__(self, subject, *, displaydata: bool, scroll_text):

        subject.register(self)
        self.p = None
        self.display_data = displaydata
        self.scroll_text = scroll_text

    def update(self, packet):
        self.p = packet
        self._display_output_header()
        self._display_packet_info()
        self._display_packet_contents()
        time.sleep(0.3)

    def _display_output_header(self):
        local_time = time.strftime('%H:%M:%S', time.localtime())
        self.insert('\n============ Packet #{0} at {1}:'.format(self.p.packet_num, local_time))

    def _display_packet_info(self):
        try:
            for proto in self.p.protocol_queue:
                getattr(self, '_display_{}_data'.format(proto.lower()))()
        except AttributeError:
            print("Unknown Protocol")


    def _display_ethernet_data(self):
        self.insert('{0}[+] MAC {1:.>23} -> {2}'.format(indent, self.p.ethernet.source,
                                                        self.p.ethernet.dest))

    def _display_ipv4_data(self):
        self.insert('{0}[+] IPv4 {1:.>22} -> {2: <15} | '
                    'PROTO: {3} TTL: {4}'.format(indent, self.p.ipv4.source,
                                                 self.p.ipv4.dest,
                                                 self.p.ipv4.encapsulated_proto,
                                                 self.p.ipv4.ttl))

    def _display_tcp_data(self):
        self.insert('{0}[+] TCP {1:.>23} -> {2: <15} | '
                    'Flags: {3} > {4}'.format(indent, self.p.tcp.sport,
                                              self.p.tcp.dport,
                                              self.p.tcp.flag_hex,
                                              self.p.tcp.flag_txt))

    def _display_udp_data(self):
        self.insert('{0}[+] UDP {1:.>23} -> {2}'.format(indent, self.p.udp.sport,
                                                        self.p.udp.dport))

    def _display_packet_contents(self):
        if self.display_data is True:
            self.insert('{0}[+] DATA:'.format(indent))
            data = self.p.data.decode(errors='ignore'). \
                replace('\n', '\n{0}'.format(indent * 2))
            self.insert('{0}{1}'.format(indent, data))

    def insert(self, *text):
        print_text = ""
        for x in text:
            print_text += x
        self.scroll_text.insert(self.scroll_text.size()+1, print_text + "\n\r")


def assemble_sniffer(interface: str, displaydata: bool, scroll_text):
    packet_sniffer = PacketSniffer(interface)
    PacketDisplay(subject=packet_sniffer,
                  displaydata=displaydata, scroll_text=scroll_text)
    packet_sniffer.execute()


def start_sniffing(text_scroll):
    scroll_txt = text_scroll
    parser = argparse.ArgumentParser(description='A pure-Python network packet '
                                                 'sniffer.')
    parser.add_argument('-i', type=str, default=None, )
    parser.add_argument('-d', action='store_true')
    cli_args = parser.parse_args()
    assemble_sniffer(interface=cli_args.i,
                     displaydata=cli_args.d, scroll_text=text_scroll)
