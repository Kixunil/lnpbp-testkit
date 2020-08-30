import requests
import os
import socket
import subprocess
import itertools
from time import sleep
from pathlib import Path
from xdg.BaseDirectory import load_first_config, save_config_path, save_data_path, load_data_paths
from hashlib import sha256
from .lightning import LnNode, ParsedInvoice
from .lightning import P2PAddr as LnP2PAddr
from . import parsing

try:
    from typing import Mapping  # type: ignore
except ImportError:  # pragma: nocover
    from typing_extensions import Mapping  # type: ignore

try:
    from typing import Any  # type: ignore
except ImportError:  # pragma: nocover
    from typing_extensions import Any  # type: ignore

try:
    from typing import Optional  # type: ignore
except ImportError:  # pragma: nocover
    from typing_extensions import Optional  # type: ignore

_CONFIG_TEMPLATE = """
datadir=%s
noseedbackup=1
alias=%s
bitcoin.defaultchanconfs=1
color=#%s
debuglevel=info
listen=0.0.0.0:%d
restlisten=0.0.0.0:%d
rpclisten=0.0.0.0:%d
bitcoin.active=1
bitcoin.regtest=1
bitcoin.node=bitcoind
bitcoind.rpchost=127.0.0.1:%d
bitcoind.rpcpass=public
bitcoind.rpcuser=public
bitcoind.zmqpubrawblock=tcp://127.0.0.1:%d
bitcoind.zmqpubrawtx=tcp://127.0.0.1:%d
tlscertpath=%s
tlskeypath=%s
logdir=%s
"""

class UnexpectedHttpStatusException(Exception):
    """Thrown when LND returns something else than 200"""

    status_code: int
    method: str
    error: Optional[str]

    def __init__(self, status_code: int, method, error: Optional[str]):
        self.status_code = status_code
        self.method = method
        self.error = error

        if self.error is None:
            super().__init__("Unexpected LND HTTP status: %d, method: %s" % (self.status_code, self.method))
        else:
            super().__init__("Unexpected LND HTTP status: %d, method: %s, error message: %s" % (self.status_code, self.method, self.error))

class LndRest(LnNode):
    _rest_address: str
    _session: requests.Session
    # Used as fallback
    _p2p_port: Optional[int] = None
    _initialized: bool = False

    def __init__(self, host_port: str, macaroon: str, tls_cert_path: Path):
        headers = { "Grpc-Metadata-macaroon": macaroon }

        self._rest_address = host_port
        self._session = requests.Session()
        self._session.headers.update(headers)
        self._session.verify = str(tls_cert_path)

    def _rpc_call(self, rpc_method: str, data: Optional[Mapping[str, Any]] = None, http_method: Optional[str] = None):
        if http_method is None:
            if data is None:
                http_method = "GET"
            else:
                http_method = "POST"

        url = "https://%s/v1/%s" % (self._rest_address, rpc_method)
        request = requests.Request(http_method, url, json=data)

        prepared = self._session.prepare_request(request)
        response = self._session.send(prepared)

        if response.status_code != 200:
            try:
                resp_json = response.json()
                error = resp_json["error"]
            except Exception:
                error = None

            raise UnexpectedHttpStatusException(response.status_code, rpc_method, error)

        return response.json()


    def get_p2p_address(self) -> LnP2PAddr:
        info = self._rpc_call("getinfo")

        try:
            return LnP2PAddr.parse(info["uris"][1])
        except IndexError:
            if self._p2p_port is None:
                raise Exception("Unknown P2P address")

            return LnP2PAddr(info["identity_pubkey"], "127.0.0.1", self._p2p_port)

    def open_channel(self, peer: LnP2PAddr, capacity_sat: int):
        peer_req = {
                "addr": {
                    "pubkey": peer.pubkey,
                    "host": "%s:%d" % (peer.host, peer.port),
                },
                "perm": False,
        }
        self._rpc_call("peers", peer_req)
        while True:
            try:
                info = self._rpc_call("getinfo")
                if info["synced_to_chain"] and info["synced_to_graph"]:
                    break
            except:
                pass

            sleep(1)
        channel_req = {
                "node_pubkey_string": peer.pubkey,
                "local_funding_amount": capacity_sat,
        }
        self._rpc_call("channels", channel_req)

    def get_spendable_sat(self, dest: str) -> int:
        response = self._rpc_call("channels")
        max_amount = 0
        for channel in response["channels"]:
            if channel["remote_pubkey"] == dest and channel["active"]:
                spendable = int(channel["local_balance"]) - int(channel["local_chan_reserve_sat"])
                if spendable > max_amount:
                    max_amount = spendable

        return max_amount

    def get_chain_balance(self) -> int:
        response = self._rpc_call("balance/blockchain")
        return int(response["confirmed_balance"])

    def get_chain_address(self) -> str:
        response = self._rpc_call("newaddress", { "type" : "p2wkh" }, http_method="GET")
        return response["address"]

    def parse_invoice(self, invoice: str) -> ParsedInvoice:
        response = self._rpc_call("payreq/%s" % invoice)
        parsed_invoice = ParsedInvoice()
        parsed_invoice.dest = response["destination"]
        parsed_invoice.amount_msat = int(response["num_msat"])
        return parsed_invoice

    def pay_invoice(self, invoice: str):
        resp = self._rpc_call("channels/transactions", { "payment_request" : invoice })
        if "payment_error" in resp and resp["payment_error"] is not None and len(str(resp["payment_error"])) > 0:
            raise Exception(resp["payment_error"])

    def wait_init(self):
        while not self._initialized:
            try:
                info = self._rpc_call("getinfo")
                if info["synced_to_chain"]:
                    self._initialized = True
                    return
            except:
                pass

            sleep(3)


def get_xdg_resource(instance_id: str) -> str:
    return "lnd-testkit-" + instance_id

def get_data_dir(instance_id: str) -> Path:
    return Path(next(load_data_paths(get_xdg_resource(instance_id))))

def generate_testkit_config(instance_id: str, p2p_port: int, grpc_port: int, rest_port: int, bitcoind_public_port: int, zmq_tx_port: int, zmq_block_port: int):
    uid = os.getuid()
    hostname = socket.gethostname()
    alias = "testkit-lnd-%s-%d-%s" % (hostname, uid, instance_id)
    color = sha256(alias.encode("utf-8")).hexdigest()[0:6]
    data_dir = Path(save_data_path(get_xdg_resource(instance_id)))
    config_file_path = Path(save_config_path(get_xdg_resource(instance_id))).joinpath("lnd.conf")
    tmp_config_path = config_file_path.with_suffix(".tmp")
    
    data_dir = get_data_dir(instance_id)
    log_dir = data_dir.joinpath("logs")
    tls_key_path = data_dir.joinpath("tls.key")
    tls_cert_path = get_testkit_default_tls_cert_path(instance_id)
    config = _CONFIG_TEMPLATE % (data_dir, alias, color, p2p_port, rest_port, grpc_port, bitcoind_public_port, zmq_block_port, zmq_tx_port, tls_cert_path, tls_key_path, log_dir)

    os.makedirs(config_file_path.parent, exist_ok=True)

    with open(tmp_config_path, "w") as tmp_config_file:
        tmp_config_file.write(config)
        tmp_config_file.flush()
        os.fdatasync(tmp_config_file.fileno())

    os.rename(tmp_config_path, config_file_path)

def create_testkit_node(node_id: str, bitcoind_public_port: int, zmq_tx_port: int, zmq_block_port: int):
    config_dir = load_first_config(get_xdg_resource(node_id))
    if config_dir is None or not Path(config_dir).joinpath("lnd.conf").exists():
        # TODO: dynamically allocate ports
        generate_testkit_config(node_id, 29735, 30009, 28080, bitcoind_public_port, zmq_tx_port, zmq_block_port)

def launch_testkit_node(instance_id: str):
    unit_name = "lnd-testkit-regtest@%s.service" % instance_id
    subprocess.run(["systemctl", "--user", "start", unit_name])

def get_testkit_default_tls_cert_path(instance_id: str) -> Path:
    return get_data_dir(instance_id).joinpath("tls.cert")

def get_testkit_default_admin_macaroon_path(instance_id: str) -> Path:
    return Path(next(load_data_paths(get_xdg_resource(instance_id)))).joinpath("chain/bitcoin/regtest/admin.macaroon")

def wait_for_file(path: Path):
    # TODO: optional inotify
    while not path.exists():
        sleep(1)

def get_testkit_node(instance_id: str) -> LnNode:
    config_file_path = Path(load_first_config(get_xdg_resource(instance_id))).joinpath("lnd.conf")
    config = parsing.parse_simple_config(config_file_path)
    host_port = "127.0.0.1:%d" % parsing.port_from_host_port(config["restlisten"])

    if "adminmacaroonpath" in config:
        admin_macaroon_path = Path(config["adminmacaroonpath"])
    else:
        admin_macaroon_path = get_testkit_default_admin_macaroon_path(instance_id)

    if "tlscertpath" in config:
        tls_cert_path = Path(config["tlscertpath"])
    else:
        tls_cert_path = get_testkit_default_tls_cert_path(instance_id)

    wait_for_file(admin_macaroon_path)
    wait_for_file(tls_cert_path)

    with open(admin_macaroon_path, "rb") as macaroon_file:
        macaroon = macaroon_file.read().hex()

    lnd = LndRest(host_port, macaroon, tls_cert_path)
    lnd._p2p_port = parsing.port_from_host_port(config["listen"])

    return lnd
