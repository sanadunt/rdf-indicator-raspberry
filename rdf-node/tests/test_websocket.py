import base64,hashlib,unittest
from unittest import mock
from rdf_node.mqtt import Reader
from rdf_node.mqtt_ws import WebSocketReader,client_handshake,encode_client_frame,validate_mqtt_host,validate_websocket_path

GUID='258EAFA5-E914-47DA-95CA-C5AB0DC85B11'

def server_frame(payload,opcode=2,fin=True,masked=False):
    first=(0x80 if fin else 0)|opcode
    length=len(payload);flag=0x80 if masked else 0
    if length<126:header=bytes([first,flag|length])
    elif length<65536:header=bytes([first,flag|126])+length.to_bytes(2,'big')
    else:header=bytes([first,flag|127])+length.to_bytes(8,'big')
    if not masked:return header+payload
    key=b'\x01\x02\x03\x04'
    return header+key+bytes(value^key[index&3] for index,value in enumerate(payload))

class MemorySocket:
    def __init__(self,response):self.response=response;self.sent=bytearray()
    def sendall(self,data):self.sent.extend(data)
    def recv(self,size):
        out=self.response[:size];self.response=self.response[size:];return out

def response_for(key,protocol='mqtt',accept=None,extra=b''):
    accept=accept or base64.b64encode(hashlib.sha1((key+GUID).encode()).digest()).decode()
    return (b'HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: keep-alive, Upgrade\r\n'
            +f'Sec-WebSocket-Accept: {accept}\r\nSec-WebSocket-Protocol: {protocol}\r\n\r\n'.encode()+extra)

class WebSocketTests(unittest.TestCase):
    def test_websocket_path_is_origin_form_without_injection(self):
        self.assertEqual(validate_websocket_path('/mqtt?node=uav-01'),'/mqtt?node=uav-01')
        for path in ('mqtt','//broker/mqtt','/mqtt#fragment','/mqtt\r\nHost:attacker','/mqtt%zz','/has space'):
            with self.subTest(path=path),self.assertRaises(ValueError):validate_websocket_path(path)

    def test_mqtt_host_rejects_http_header_injection(self):
        for host in ('broker.example\r\nHost:attacker','bad_name','broker.example/path'):
            with self.subTest(host=host),self.assertRaises(ValueError):validate_mqtt_host(host)
        sock=MemorySocket(b'')
        with self.assertRaises(ValueError):client_handshake(sock,'broker.example\r\nHost:attacker',443,'/mqtt')
        self.assertFalse(sock.sent)

    def test_client_frames_are_binary_and_masked(self):
        payload=b'\x10\x00mqtt'
        wire=encode_client_frame(payload)
        self.assertEqual(wire[0],0x82)
        self.assertTrue(wire[1]&0x80)
        key=wire[2:6]
        self.assertEqual(bytes(value^key[index&3] for index,value in enumerate(wire[6:])),payload)

    def test_reader_streams_binary_fragments_and_control_ping(self):
        payload=b'\x20\x03\x00\x00\x00\x82\x01\x00'
        wire=(server_frame(payload[:2],fin=False)+server_frame(b'ping',opcode=9)+
              server_frame(payload[2:5],opcode=0,fin=False)+server_frame(payload[5:],opcode=0))
        reader=WebSocketReader();events=[]
        for start in range(0,len(wire),3):events.extend(reader.feed(wire[start:start+3]))
        self.assertEqual(events,[('data',payload[:2]),('ping',b'ping'),('data',payload[2:5]),('data',payload[5:])])
        mqtt=Reader();packets=[]
        for kind,data in events:
            if kind=='data':packets.extend(mqtt.feed(data))
        self.assertEqual(packets,[(0x20,b'\x00\x00\x00'),(0x82,b'\x00')])

    def test_reader_rejects_masked_or_text_server_frames(self):
        for wire in (server_frame(b'bad',masked=True),server_frame(b'not mqtt',opcode=1)):
            with self.subTest(wire=wire),self.assertRaises(ValueError):WebSocketReader().feed(wire)

    def test_reader_rejects_noncanonical_lengths_and_oversize_frames(self):
        with self.assertRaises(ValueError):WebSocketReader().feed(b'\x82\x7e\x00\x7d')
        with self.assertRaises(ValueError):WebSocketReader().feed(b'\x82\x7f\x00\x00\x00\x00\x00\x01\x00\x01')

    def test_client_handshake_validates_accept_protocol_and_preserves_trailing_bytes(self):
        nonce=b'0123456789abcdef';key=base64.b64encode(nonce).decode()
        sock=MemorySocket(response_for(key,extra=b'\x89\x00'))
        with mock.patch('rdf_node.mqtt_ws.os.urandom',return_value=nonce):
            rest=client_handshake(sock,'broker.example',443,'/mqtt')
        self.assertEqual(rest,b'\x89\x00')
        self.assertIn(b'GET /mqtt HTTP/1.1\r\n',sock.sent)
        self.assertIn(b'Sec-WebSocket-Protocol: mqtt\r\n',sock.sent)
        self.assertIn(b'Host: broker.example\r\n',sock.sent)

    def test_client_handshake_rejects_unverified_upgrade(self):
        nonce=b'0123456789abcdef';key=base64.b64encode(nonce).decode()
        cases=(response_for(key,accept='wrong'),response_for(key,protocol='other'),
               response_for(key).replace(b'101 Switching Protocols',b'200 OK'))
        for response in cases:
            with self.subTest(response=response):
                with mock.patch('rdf_node.mqtt_ws.os.urandom',return_value=nonce):
                    with self.assertRaises(ValueError):client_handshake(MemorySocket(response),'broker.example',443,'/mqtt')

if __name__=='__main__':unittest.main()
