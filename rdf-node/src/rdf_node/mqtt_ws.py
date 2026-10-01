"""Bounded RFC 6455 client transport for MQTT binary frames."""
import base64
import hashlib
import hmac
import os
import ipaddress
import re

GUID='258EAFA5-E914-47DA-95CA-C5AB0DC85B11'
MAX_HANDSHAKE=8192
MAX_FRAME=65536
MAX_MESSAGE=262144
MAX_READ=8192
_PATH=re.compile(r"/[A-Za-z0-9._~!$&'()*+,;=:@%/?-]*\Z")
_HOST_LABEL=re.compile(r'[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\Z')


def validate_websocket_path(path):
    if (not isinstance(path,str) or len(path)>256 or not path.isascii() or not path.startswith('/') or
        path.startswith('//') or not _PATH.fullmatch(path) or re.search(r'%(?![0-9A-Fa-f]{2})',path)):
        raise ValueError('INVALID_MQTT_WEBSOCKET_PATH')
    return path

def validate_mqtt_host(host):
    if not isinstance(host,str) or not host or len(host)>253 or not host.isascii() or host!=host.strip() or '%' in host:
        raise ValueError('INVALID_MQTT_HOST')
    try:ipaddress.ip_address(host)
    except ValueError:
        if not all(len(label)<=63 and _HOST_LABEL.fullmatch(label) for label in host.split('.')):
            raise ValueError('INVALID_MQTT_HOST')
    return host


def encode_client_frame(payload,opcode=2):
    if opcode not in (2,8,10):raise ValueError('INVALID_WEBSOCKET_OPCODE')
    payload=bytes(payload)
    length=len(payload)
    if length>MAX_FRAME or opcode in (8,10) and length>125:raise ValueError('WEBSOCKET_FRAME_LIMIT')
    mask=os.urandom(4)
    if len(mask)!=4:raise ValueError('WEBSOCKET_MASK_FAILED')
    flag=0x80
    if length<126:header=bytes([flag|opcode,flag|length])
    elif length<65536:header=bytes([flag|opcode,flag|126])+length.to_bytes(2,'big')
    else:header=bytes([flag|opcode,flag|127])+length.to_bytes(8,'big')
    return header+mask+bytes(value^mask[index&3] for index,value in enumerate(payload))


def _validate_close(payload):
    if len(payload)==1:raise ValueError('WEBSOCKET_BAD_CLOSE')
    if len(payload)<2:return
    code=int.from_bytes(payload[:2],'big')
    if code<1000 or code>=5000 or code in (1004,1005,1006,1015) or 1016<=code<3000:
        raise ValueError('WEBSOCKET_BAD_CLOSE')
    try:payload[2:].decode('utf-8')
    except UnicodeDecodeError:raise ValueError('WEBSOCKET_BAD_CLOSE')


class WebSocketReader:
    """Incrementally parse unmasked server frames and stream MQTT bytes."""
    def __init__(self):
        self.buffer=bytearray();self.fragmented=False;self.message_size=0;self.closed=False

    def feed(self,data):
        if self.closed and data:raise ValueError('WEBSOCKET_DATA_AFTER_CLOSE')
        self.buffer.extend(data)
        if len(self.buffer)>MAX_FRAME+14+MAX_READ:raise ValueError('WEBSOCKET_BUFFER_LIMIT')
        events=[]
        while len(self.buffer)>=2:
            first,second=self.buffer[0],self.buffer[1]
            fin=bool(first&0x80);opcode=first&0x0f
            if first&0x70:raise ValueError('WEBSOCKET_RESERVED_BITS')
            if second&0x80:raise ValueError('WEBSOCKET_SERVER_MASKED')
            marker=second&0x7f;pos=2
            if marker==126:
                if len(self.buffer)<4:break
                length=int.from_bytes(self.buffer[2:4],'big');pos=4
                if length<126:raise ValueError('WEBSOCKET_NONCANONICAL_LENGTH')
            elif marker==127:
                if len(self.buffer)<10:break
                if self.buffer[2]&0x80:raise ValueError('WEBSOCKET_LENGTH_HIGH_BIT')
                length=int.from_bytes(self.buffer[2:10],'big');pos=10
                if length<65536:raise ValueError('WEBSOCKET_NONCANONICAL_LENGTH')
            else:length=marker
            control=opcode>=8
            if control and (not fin or length>125):raise ValueError('WEBSOCKET_BAD_CONTROL_FRAME')
            if length>MAX_FRAME:raise ValueError('WEBSOCKET_FRAME_LIMIT')
            if len(self.buffer)<pos+length:break
            payload=bytes(self.buffer[pos:pos+length]);del self.buffer[:pos+length]
            if opcode==2:
                if self.fragmented:raise ValueError('WEBSOCKET_INTERLEAVED_DATA')
                self.message_size=length
                if self.message_size>MAX_MESSAGE:raise ValueError('WEBSOCKET_MESSAGE_LIMIT')
                if not fin:self.fragmented=True
                else:self.message_size=0
                if payload:events.append(('data',payload))
            elif opcode==0:
                if not self.fragmented:raise ValueError('WEBSOCKET_UNEXPECTED_CONTINUATION')
                self.message_size+=length
                if self.message_size>MAX_MESSAGE:raise ValueError('WEBSOCKET_MESSAGE_LIMIT')
                if fin:self.fragmented=False;self.message_size=0
                if payload:events.append(('data',payload))
            elif opcode==1:
                raise ValueError('WEBSOCKET_TEXT_FRAME_REJECTED')
            elif opcode==8:
                _validate_close(payload);self.closed=True;events.append(('close',payload))
                if self.buffer:raise ValueError('WEBSOCKET_DATA_AFTER_CLOSE')
                break
            elif opcode==9:
                events.append(('ping',payload))
            elif opcode==10:
                events.append(('pong',payload))
            else:
                raise ValueError('WEBSOCKET_OPCODE_REJECTED')
        return events


def client_handshake(sock,host,port,path):
    path=validate_websocket_path(path)
    if type(port) is not int or not 1<=port<=65535:raise ValueError('INVALID_MQTT_WEBSOCKET_ENDPOINT')
    host=validate_mqtt_host(host)
    nonce=os.urandom(16)
    if len(nonce)!=16:raise ValueError('WEBSOCKET_NONCE_FAILED')
    key=base64.b64encode(nonce).decode('ascii')
    authority=f'[{host}]' if ':' in host and not host.startswith('[') else host
    if port!=443:authority+=f':{port}'
    request=(f'GET {path} HTTP/1.1\r\nHost: {authority}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n'
             f'Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\nSec-WebSocket-Protocol: mqtt\r\n\r\n').encode('ascii')
    sock.sendall(request)
    response=bytearray()
    while True:
        end=response.find(b'\r\n\r\n')
        if end>=0:
            if end+4>MAX_HANDSHAKE:raise ValueError('WEBSOCKET_HANDSHAKE_TOO_LARGE')
            header=bytes(response[:end]);trailing=bytes(response[end+4:]);break
        if len(response)>MAX_HANDSHAKE:raise ValueError('WEBSOCKET_HANDSHAKE_TOO_LARGE')
        chunk=sock.recv(min(4096,MAX_HANDSHAKE+4-len(response)))
        if not chunk:raise ValueError('WEBSOCKET_HANDSHAKE_EOF')
        response.extend(chunk)
    try:lines=header.decode('ascii').split('\r\n')
    except UnicodeDecodeError:raise ValueError('WEBSOCKET_BAD_HANDSHAKE')
    status=lines[0].split(' ',2) if lines else []
    if len(status)<2 or status[0]!='HTTP/1.1' or status[1]!='101':
        raise ValueError('WEBSOCKET_STATUS_REJECTED')
    headers={}
    for line in lines[1:]:
        if not line or line[0] in ' \t' or ':' not in line:raise ValueError('WEBSOCKET_BAD_HANDSHAKE')
        name,value=line.split(':',1);name=name.strip().lower();value=value.strip()
        if not name or name in headers:raise ValueError('WEBSOCKET_BAD_HANDSHAKE')
        headers[name]=value
    if 'websocket' not in [token.strip().lower() for token in headers.get('upgrade','').split(',')]:
        raise ValueError('WEBSOCKET_UPGRADE_REQUIRED')
    if 'upgrade' not in [token.strip().lower() for token in headers.get('connection','').split(',')]:
        raise ValueError('WEBSOCKET_CONNECTION_REQUIRED')
    expected=base64.b64encode(hashlib.sha1((key+GUID).encode('ascii')).digest()).decode('ascii')
    if not hmac.compare_digest(headers.get('sec-websocket-accept',''),expected):
        raise ValueError('WEBSOCKET_ACCEPT_REJECTED')
    if headers.get('sec-websocket-protocol')!='mqtt':raise ValueError('WEBSOCKET_SUBPROTOCOL_REQUIRED')
    if 'sec-websocket-extensions' in headers:raise ValueError('WEBSOCKET_EXTENSION_UNSUPPORTED')
    return trailing
