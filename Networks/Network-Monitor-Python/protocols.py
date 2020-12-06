from ctypes import BigEndianStructure, create_string_buffer, c_ubyte, c_uint8, \
    c_uint16, c_uint32, sizeof
from socket import inet_ntop, AF_INET


class Protocol(BigEndianStructure):
    _pack_ = 1

    def __new__(cls, packet):
        return cls.from_buffer_copy(packet)

    def __init__(self, packet=None):
        super().__init__()
        self.encapsulated_proto = None

    def __str__(self):
        return create_string_buffer(sizeof(self))[:]

    @staticmethod
    def addr_array_to_hdwr(address: str) -> str:
        return ':'.join(format(octet, '02x') for octet in bytes(address))

    @staticmethod
    def hex_format(value: int, str_length: int) -> str:
        return format(value, '#0{}x'.format(str_length))


class Ethernet(Protocol):
    _fields_ = [
        ('dst', c_ubyte * 6),
        ('src', c_ubyte * 6),
        ('eth', c_uint16)
    ]
    header_len = 14
    ethertypes = {'0x0806': 'ARP', '0x0800': 'IPv4', '0x86dd': 'IPv6'}

    def __init__(self, packet: bytes):
        super().__init__(packet)
        self.dest = self.addr_array_to_hdwr(self.dst)
        self.source = self.addr_array_to_hdwr(self.src)
        self.ethertype = self.hex_format(self.eth, 6)
        self.encapsulated_proto = self.ethertypes.get(self.ethertype, None)


class IPv4(Protocol):
    _fields_ = [
        ("version", c_uint8, 4),
        ("ihl", c_uint8, 4),
        ("dscp", c_uint8, 6),
        ("ecp", c_uint8, 2),
        ("len", c_uint16),
        ("id", c_uint16),
        ("flags", c_uint16, 3),
        ("offset", c_uint16, 13),
        ("ttl", c_uint8),
        ("proto", c_uint8),
        ("chksum", c_uint16),
        ("src", c_ubyte * 4),
        ("dst", c_ubyte * 4)
    ]
    header_len = 20
    proto_numbers = {1: 'ICMP', 6: 'TCP', 17: 'UDP'}

    def __init__(self, packet: bytes):
        super().__init__(packet)
        self.source = inet_ntop(AF_INET, self.src)
        self.dest = inet_ntop(AF_INET, self.dst)
        self.encapsulated_proto = self.proto_numbers.get(self.proto, None)


class TCP(Protocol):
    _fields_ = [
        ("sport", c_uint16),
        ("dport", c_uint16),
        ("seq", c_uint32),
        ("ack", c_uint32),
        ("offset", c_uint16, 4),
        ("reserved", c_uint16, 3),
        ("flags", c_uint16, 9),
        ("window", c_uint16),
        ("chksum", c_uint16),
        ("urg", c_uint16),
    ]
    header_len = 32

    def __init__(self, packet: bytes):
        super().__init__(packet)
        self.flag_hex = self.hex_format(self.flags, 5)
        self.flag_txt = self.translate_flags()

    def translate_flags(self):
        f_names = 'NS', 'CWR', 'ECE', 'URG', 'ACK', 'PSH', 'RST', 'SYN', 'FIN'
        f_bits = format(self.flags, '09b')
        return ' '.join(flag_name for flag_name, flag_bit in
                        zip(f_names, f_bits) if flag_bit == '1')


class UDP(Protocol):
    _fields_ = [
        ("sport", c_uint16),
        ("dport", c_uint16),
        ("len", c_uint16),
        ("chksum", c_uint16)
    ]
    header_len = 8

    def __init__(self, packet: bytes):
        super().__init__(packet)
