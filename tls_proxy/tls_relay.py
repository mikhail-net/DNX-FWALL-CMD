#!/usr/bin/env python3

import os, sys, time
import threading
import json
import traceback

path = os.environ['HOME_DIR']
sys.path.insert(0, path)

from socket import socket, AF_PACKET, SOCK_RAW, AF_INET, SOCK_STREAM
from dnx_configure.dnx_system_info import Interface
from dnx_configure.dnx_exceptions import *
from dnx_configure.dnx_packet_checks import Checksums
from tls_proxy.tls_proxy_sniffer import SSLHandlerThread, SSL, SSLType
from tls_proxy.tls_proxy_packets import PacketHeaders, PacketManipulation
from tls_proxy.tls_relay_connection_handler import ConnectionHandler as CH

class TLSRelay:
    def __init__(self, action):
        TLSRelay.action = action
        self.path = os.environ['HOME_DIR']
        
        with open(f'{self.path}/data/config.json', 'r') as settings:
            self.setting = json.load(settings)

        self.lan_int = self.setting['Settings']['Interface']['Inside']
        TLSRelay.wan_int = self.setting['Settings']['Interface']['Outside']
        self.dnsserver = self.setting['Settings']['DNSServers']

        Int = Interface()
        self.lan_ip = Int.IP(self.lan_int)
        self.wan_ip = Int.IP(self.wan_int)
        dfg = Int.DefaultGateway()
        dfg_mac = Int.IPtoMAC(dfg)
        self.wan_mac = Int.MAC(self.wan_int)
        self.lan_mac = Int.MAC(self.lan_int)
        wan_subnet = Int.WANSubnet(self.wan_int, dfg)
        self.wan_info = [dfg_mac, wan_subnet]

        TLSRelay.connections = {'Clients': {}}
        TLSRelay.active_connections = {'Clients': {}}
        TLSRelay.tcp_handshakes = {'Clients': {}}
        self.nat_ports = {}

        ## RAW Sockets which actually handle the traffic.
        TLSRelay.lan_sock = socket(AF_PACKET, SOCK_RAW)
        self.lan_sock.bind((self.lan_int, 3))
        self.wan_sock = socket(AF_PACKET, SOCK_RAW)
        self.wan_sock.bind((self.wan_int, 3))

        self.tls_ports = {443}
        TLSRelay.tcp_info = []
               
    def Start(self):
        self.Main()

    def Main(self):
        print(f'[+] Listening -> {self.lan_int}')
        while True:
            conn_handle = False
            relay = True
            data_from_host = self.lan_sock.recv(65565)
            try:
                host_packet_headers = PacketHeaders(data_from_host)
                host_packet_headers.Parse()
                print('recieved shit from shit')
                if (host_packet_headers.dport in self.tls_ports):
                    print(f'HTTPS CONNECTION FROM HOST: {host_packet_headers.sport}')
                    src_mac = host_packet_headers.smac
                    src_ip = host_packet_headers.src
                    src_port = host_packet_headers.sport
                    dst_ip = host_packet_headers.dst
                    dst_port = host_packet_headers.dport
    #                print(f'{self.active_connections} : {len(host_packet_headers.payload)} || DFH: {len(data_from_host)}')
                    if (len(host_packet_headers.payload) == 0):
                        tcp_relay = False
                        if (src_ip not in self.tcp_handshakes):
                            TLSRelay.sock, TLSRelay.connection = self.CreateConnection(src_mac, src_ip, src_port, dst_ip, dst_port)
                            tcp_relay = True
                        elif (src_ip in self.tcp_handshakes and src_port not in self.tcp_handshakes[src_ip]):
                            self.sock, self.connection = self.CreateConnection(src_mac, src_ip, src_port, dst_ip, dst_port)
                            tcp_relay = True
                        else:
                            self.connection = self.connections['Clients'][src_ip][src_port]
                            
                        if (tcp_relay):
                            print('SENDING TO TCP HANDLER')
                            ConnectionHandler = CH(TLSRelay)
                            TCPRelay = threading.Thread(target=ConnectionHandler.Start, args=(2, 'TCP'))
                            TCPRelay.daemon = True
                            TCPRelay.start()
                            
                        packet_from_host = PacketManipulation(host_packet_headers, self.wan_info, data_from_host, self.connection, from_server=False)
                        packet_from_host.Start()
                        self.wan_sock.send(packet_from_host.send_data)
                        relay = False

                    elif (src_ip not in self.active_connections):
                        nat_port = self.tcp_handshakes['Clients'][src_ip][src_port]
                        self.active_connections[src_ip] = {src_port: nat_port}
                        conn_handle = True
                    elif (src_ip in self.active_connections and src_port not in self.active_connections[src_ip]):
                        nat_port = self.tcp_handshakes['Clients'][src_ip][src_port]
                        self.active_connections[src_ip].update({src_port: nat_port})
                        conn_handle = True
                    else:
                        nat_port = self.active_connections[src_ip][src_port]
                        
                    if (relay):
                        connection = self.connections['Clients'][src_ip][src_port]
                        packet_from_host = PacketManipulation(host_packet_headers, self.wan_info, data_from_host, connection, from_server=False)
                        packet_from_host.Start()
                        self.wan_sock.send(packet_from_host.send_data)
                        
                    if (conn_handle):
                        SSL = SSLType(data_from_host)
                        _, TLSRelay.tcp_info = SSL.Parse()
                        print(f'Sending Connection to Thread: CLIENT {src_port} | NAT {nat_port}')
                        ConnectionHandler = CH(TLSRelay)
                        SSLRelay = threading.Thread(target=ConnectionHandler.Start, args=(120, 'SSL'))
                        SSLRelay.daemon = True
                        SSLRelay.start()

            except DNXError:
                pass
            except Exception as E:
                print(f'MAIN PARSE EXCEPTION: {E}')
#                traceback.print_exc()

    def CreateConnection(self, src_mac, src_ip, src_port, dst_ip, dst_port):
        tcp_handshakes = self.tcp_handshakes['Clients']
        connections = self.connections['Clients']
        sock, nat_port = self.CreateSocket()
        connection = {'Client': {'IP': src_ip, 'Port': src_port, 'MAC': src_mac},
                        'NAT': {'IP': self.wan_ip, 'Port': nat_port, 'MAC': self.wan_mac},
                        'LAN': {'IP': self.lan_ip, 'MAC': self.lan_mac},
                        'Server': {'IP': dst_ip, 'Port': dst_port},
                        'DFG': {'MAC': self.wan_info[0]},
                        'Socket': sock}
                        
        if (src_ip not in tcp_handshakes):
            tcp_handshakes[src_ip] = {src_port: nat_port}
            connections[src_ip] = {src_port: connection}
        elif (src_ip in tcp_handshakes and src_port not in tcp_handshakes[src_ip]):
            tcp_handshakes[src_ip].update({src_port: nat_port})
            connections[src_ip].update({src_port: connection})

        return sock, connection

    def CreateSocket(self):
        sock = socket(AF_INET, SOCK_STREAM)
        sock.bind((self.wan_ip, 0))
        sock.listen()
        nat_port = sock.getsockname()[1]
                        
        return sock, nat_port