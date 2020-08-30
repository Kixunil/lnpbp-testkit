"""Module responsible for integrating LNP/BP Testkit into Cryptoanarchy Debian Repository

See https://deb.ln-ask.me to learn more about the Cryptoanarchy Debian Repository.
"""

from pathlib import Path
import configparser
import subprocess
from subprocess import Popen
import itertools
import toml
from .network import Network
from . import parsing

try:
    from typing import Optional  # type: ignore
except ImportError:  # pragma: nocover
    from typing_extensions import Optional  # type: ignore

class Zmq:
    block_port: int
    tx_port: int

def get_zmq():
    config = parsing.parse_simple_config("/etc/bitcoin-regtest/conf.d/zmq.conf")

    zmq = Zmq()
    zmq.block_port = parsing.port_from_uri(config["zmqpubrawblock"])
    zmq.tx_port = parsing.port_from_uri(config["zmqpubrawtx"])

    return zmq

def get_bitcoin_rpc_proxy_port():
    with open("/etc/bitcoin-rpc-proxy-regtest/conf.d/interface.conf") as config_file:
        config = toml.load(config_file)

    return config["bind_port"]

BITCOIN_CONFIG_PATH: Path = Path("/etc/bitcoin-regtest/bitcoin.conf")
LND_CONFIG_PATH: Path = Path("/etc/lnd-system-regtest/lnd.conf")

_network: Optional[Network] = None

def network() -> Network:
    """Instantiates network by connecting to the system regtest network.
    
    You need the right permissions to use this method - either passwordless sudo or
    `sudo usermod -a -G bitcoin-regtest,lnd-system-regtest $USER`.
    """
    global _network
    if _network is not None:
        return _network

    try:
        with open(BITCOIN_CONFIG_PATH, "r") as bitcoind_config_file:
            bitcoind_config = configparser.ConfigParser()
            # Remove the first line (regtest=1)
            next(bitcoind_config_file)
            bitcoind_config.read_file(bitcoind_config_file)

    except PermissionError:
        with Popen(["sudo", "-n", "cat", BITCOIN_CONFIG_PATH], stdout=subprocess.PIPE) as process:
            bitcoind_config = configparser.ConfigParser()
            # Remove the first line (regtest=1)
            next(process.stdout)
            bitcoind_config.read_file((line.decode("utf-8") for line in process.stdout))

    try:
        with open(bitcoind_config["regtest"]["rpccookiefile"], "r") as bitcoin_cookie_file:
            bitcoin_cookie = bitcoin_cookie_file.read()

    except PermissionError:
        bitcoin_cookie = subprocess.run(["sudo", "-n", "cat", bitcoind_config["regtest"]["rpccookiefile"]], stdout=subprocess.PIPE).stdout.decode("utf-8")

    zmq = get_zmq()
    bitcoin_rpc_proxy_port = get_bitcoin_rpc_proxy_port()

    lnd_config = parsing.parse_simple_config(LND_CONFIG_PATH)

    lnd_admin_macaroon_path = lnd_config["adminmacaroonpath"]

    try:
        with open(lnd_admin_macaroon_path, "rb") as macaroon_file:
            lnd_admin_macaroon = macaroon_file.read().hex()

    except PermissionError:
        lnd_admin_macaroon = subprocess.run(["sudo", "-n", "xxd", "-p", "-c", "1000000", lnd_admin_macaroon_path], stdout=subprocess.PIPE).stdout.decode("utf-8").strip()

    network_config = {
        "bitcoind_url" : "http://%s@127.0.0.1:%d" % (bitcoin_cookie, int(bitcoind_config["regtest"]["rpcport"])),
        "bitcoind_public_port": bitcoin_rpc_proxy_port,
        "zmq_tx_port": zmq.tx_port,
        "zmq_block_port": zmq.block_port,
        "lnd_host": "127.0.0.1:%d" % parsing.port_from_host_port(lnd_config["restlisten"]),
        "lnd_macaroon": lnd_admin_macaroon,
        "lnd_tls_cert_file": lnd_config["tlscertpath"],
        "lnd_p2p_port": parsing.port_from_host_port(lnd_config["listen"])
    }

    _network = Network(network_config)
    return _network
