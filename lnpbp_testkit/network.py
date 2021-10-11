"""This is currently the most important module as it contains the `Network` class.

The network class is used to send payments.
"""

from __future__ import annotations
from bitcoin.rpc import Proxy as BitcoindProxy
from decimal import Decimal
from time import sleep
import threading
from xdg.BaseDirectory import load_first_config
from .lightning import LnNode, ParsedInvoice, Channel
from .lightning import P2PAddr as LnP2PAddr
from .lightning import InvoiceHandle as LnInvoiceHandle
from .lnd import LndRest
from . import lnd
from . import parsing
from pathlib import Path
import toml
import re

NODE_NAME_RE = re.compile("[a-z0-9_-]*")

try:
    from typing import Mapping  # type: ignore
except ImportError:  # pragma: nocover
    from typing_extensions import Mapping  # type: ignore

try:
    from typing import MutableMapping  # type: ignore
except ImportError:  # pragma: nocover
    from typing_extensions import MutableMapping  # type: ignore

try:
    from typing import Any  # type: ignore
except ImportError:  # pragma: nocover
    from typing_extensions import Any  # type: ignore

try:
    from typing import Optional  # type: ignore
except ImportError:  # pragma: nocover
    from typing_extensions import Optional  # type: ignore

try:
    from typing import Protocol  # type: ignore
except ImportError:  # pragma: nocover
    from typing_extensions import Protocol  # type: ignore

SECONDARY_NODE_ID = "1"
# Currently the max capacity for non-wumbo clients
MAX_NON_WUMBO_CAPACITY: int = 2 ** 24 - 1
CHANNEL_WARMUP_CAPACITY: int = MAX_NON_WUMBO_CAPACITY

class PaymentRequest(Protocol):
    def auto_pay(self, network: Network):
        """Automatically pays the request within given network"""

class ChainPayment:
    address: str
    # We have to use str because Python is too retarded to be capable of converting
    # Decimal to non-scientific notation >:(
    amount: str

    def __init__(self, address: str, amount: str):
        self.address = address
        self.amount = amount

    def auto_pay(self, network: Network):
        network._prepare_chain_coins(Decimal(self.amount))
        network._send_coins(self.address, self.amount)

class LightningPayment:
    invoice: str

    def __init__(self, invoice: str):
        self.invoice = invoice

    def auto_pay(self, network: Network):
        parsed_invoice = network._parse_invoice(self.invoice)
        if network._secondary_node is None:
            network._secondary_node = network._spawn_secondary_node(SECONDARY_NODE_ID)
        network._secondary_node.wait_init()
        network._prepare_channel(network._secondary_node, parsed_invoice.dest, (parsed_invoice.amount_msat + 999) // 1000)
        network._pay_ln_invoice(self.invoice)

class InvalidPaymentLink(Exception):
    def __init__(self, message: str):
        """This constructor is private!"""

        super().__init__(message)

def _parse_link(link: str) -> PaymentRequest:
        parts = link.split(':')
        if len(parts) == 1:
            if link.startswith("1") or link.startswith("3") or link.startswith("bc1"):
                raise InvalidPaymentLink("Address can't be parsed as a payment link because it's missing amount")
            if link.startswith("lnbcrt"):
                return LightningPayment(link)
            if link.startswith("ln"):
                raise InvalidPaymentLink("Attempt to pay an invoice from a different network, only regtest is allowed")

            raise InvalidPaymentLink("Unknown payment link format")

        schema = parts[0]

        if schema == "bitcoin":
            parts = link[len(schema) + 1:].split("?")

            if len(parts) < 2:
                raise InvalidPaymentLink("Unknown amount")

            if len(parts) > 2:
                raise InvalidPaymentLink("Invalid character '?' in the parameters")

            address = parts[0]
            params = parts[1]

            if params.startswith("amount="):
                pos = len("amount=")
            else:
                pos = params.find("&amount=")
                if pos < 0:
                    raise InvalidPaymentLink("Unknown amount")
                pos += len("&amount=")

            end = params.find('&', pos)
            if end < 0:
                end = len(params)

            amount = params[pos:end]

            return ChainPayment(address, amount)
        if schema == "lightning":
            return LightningPayment(link[len(schema) + 1:])

        raise InvalidPaymentLink("Unknown schema")

class Network:
    """Represents regtest network used for testing"""

    _main_bitcoind_url: str
    _bitcoind_public_port: int
    _zmq_tx_port: int
    _zmq_block_port: int
    _main_ln_node: LnNode
    _secondary_node: Optional[LnNode] = None
    _ln_nodes_by_name: MutableMapping[str, LnNode] = {}
    _ln_nodes_by_pubkey: MutableMapping[str, LnNode] = {}
    _auto_miner_running: bool = False
    _auto_miner_confirm_tx_block_count: int = 6
    _auto_miner_polling_interval: int = 3
    _auto_miner_thread: Optional[threading.Thread] = None

    def __init__(self, data: Mapping[str, Any]):
        """Configures the network

        `data` is intentionally a dictionary so that you can simply load it from a file.
        The public fields are:
        * `bitcoind_url` : "USERNAME:PASSWORD@HOST:PORT",
        * `bitcoind_public_port`: BITCOIN_RPC_PROXY_PORT, # optional, the port from bitcoind_url will be used if not present
        * `zmq_tx_port`: ZMQPUBRAWTX_PORT,
        * `zmq_block_port`: ZMQPUBRAWBLOCK_PORT,
	* `lnd_host`: "REST_HOST:REST_PORT",
	* `lnd_macaroon`: "HEX_ENCODED_MACAROON",
	* `lnd_tls_cert_file`: "PATH_TO_LND_TLS_CERTIFICATE",`
        """
        self._main_bitcoind_url = data["bitcoind_url"]
        self._zmq_tx_port = data["zmq_tx_port"]
        self._zmq_block_port = data["zmq_block_port"]
        self._main_ln_node = LndRest(data["lnd_host"], data["lnd_macaroon"], data["lnd_tls_cert_file"])
        if "bitcoind_public_port" in data:
            self._bitcoind_public_port = data["bitcoind_public_port"]
        else:
            self._bitcoind_public_port = parsing.port_from_uri(self._main_bitcoind_url)
        if "lnd_p2p_port" in data:
            self._main_ln_node._p2p_port = data["lnd_p2p_port"]
        if "secondary_lnd_host" in data:
            self._secondary_node = LndRest(data["secondary_lnd_host"], data["secondary_lnd_macaroon"], data["secondary_lnd_tls_cert_file"])

    def mine_blocks(self, count: int):
        """Generates `count` blocks

        This is usually not needed as transactions confirm automatically but it can be useful in some test scenarios.
        If you want to mine all transactions that appear in the mempool (not just those generated from this library)
        consider using `start_auto_mining()`.

        `count` must be at least `1`.
        """

        if count < 1:
            raise Exception("Attempt to mine %d blocks, must be at least 1" % count)
        self._prepare_wallet()
        # Something is wrong here, so let's retry
        try:
            address = BitcoindProxy(service_url = self._main_bitcoind_url)._call("getnewaddress")
        except:
            sleep(5)
            address = BitcoindProxy(service_url = self._main_bitcoind_url)._call("getnewaddress")
        BitcoindProxy(service_url = self._main_bitcoind_url)._call("generatetoaddress", count, address)

    def start_auto_mining(self, polling_interval_seconds: int = 3, confirm_tx_block_count: int = 6):
        """Mines blocks whenever any transaction appears in mempool

        This can be used when transactions are produced by software other than this testkit
        to ensure they are quickly confirmed. The miner runs in a different thread, so you can just call this function
        before your test triggers such transaction. Note that calling it after trigger is also OK because if there are
        already transactions in the mempool when you start auto miner those will be confirmed immediately.

        Since it is expected that this is used for tests only a simple polling implementation is used.
        You can adjust polling interval to your needs using `polling_interval_seconds` parameter (currently defaults to 3s).
        You can also pick a different number of blocks to be mined using `confirm_tx_block_count` (defaults to 6).

        Note that if your test synchronously knows about transaction being broadcast it may be better to just call `mine_blocks`
        after it was. However auto miner can still save you from writing `mine_blocks` multiple times and can be handy in manual
        tests too.
        """

        if polling_interval_seconds < 1:
            raise Exception("Attempt to set polling interval to %d, must be at least 1" % count)
        if self._auto_miner_running or self._auto_miner_thread is not None:
            raise Exception("Auto miner is already running")
        self._auto_miner_confirm_tx_block_count = confirm_tx_block_count
        self._auto_miner_polling_interval = confirm_tx_block_count
        self._auto_miner_running = True
        try:
            self._auto_miner_thread = threading.Thread(target = self._run_auto_miner)
        except Exception as e:
            self._auto_miner_running = False
            raise e

    def stop_auto_mining(self):
        """Stops automatic transaction mining started by `start_auto_mining`

        After call to this function returns no more transactions will be mined automatically (unless started again).
        This call synchronously waits for the mining thread to stop so you don't have to worry about races. However setting too
        long polling period will affect the waiting time too. It is thus recommended to either set waiting time to reasonably
        short or to perform tests *without* auto mining before tests *with* auto mining.
        """

        if self._auto_miner_thread is None:
            raise Exception("Auto miner is not running")
        self._auto_miner_running = False
        self._auto_miner_thread.join()
        self._auto_miner_thread = None

    def _run_auto_miner(self):
        while self._auto_miner_running:
            try:
                mempool_info = BitcoindProxy(service_url = self._main_bitcoind_url)._call("getmempoolinfo")
                if mempool_info["size"] > 0:
                    self.mine_blocks(self._auto_miner_confirm_tx_block_count)
            except Exception as e:
                print("Failed to auto-mine: {}".format(e))
            sleep(self._auto_miner_polling_interval)

    def _prepare_blocks(self):
        info = BitcoindProxy(service_url = self._main_bitcoind_url)._call("getblockchaininfo")
        block_count = info["blocks"]
        if block_count < 101:
            self.mine_blocks(101 - block_count)

    def warm_up(self, blocks: bool = True, secondary_ln_node: bool = True, channels: bool = False):
        """Initializes the network
        
        You can select specific things to initialize:

        * `blocks` - makes sure there's at least 101 blocks
        * `secondary_ln_node` - create a secondary LN node, the initialization of the node takes a while, so it's done asynchronously unless `channels` is `True`
        * `channels` - sets up some LN channels, currently only secondary -> main LN node, blocks if secondary LN node is not initialized
        """
        if blocks:
            self._prepare_blocks()

        if secondary_ln_node:
            if self._secondary_node is None:
                self._secondary_node = self._spawn_secondary_node(SECONDARY_NODE_ID)

            if channels:
                self._secondary_node.wait_init()
                self._open_channel(self._secondary_node, self._main_ln_node.get_p2p_address().pubkey, CHANNEL_WARMUP_CAPACITY)
                self._open_channel(self._main_ln_node, self._secondary_node.get_p2p_address().pubkey, CHANNEL_WARMUP_CAPACITY)
        else:
            if channels:
                raise Exception("Impossible to prepare channels without also preparing secondary LN node")


    def auto_pay(self, link: str):
        """Automatically pays BIP21 or BOLT11, with or without `lightning:` scheme.
        
        This methods generates coins if it's needed for paying and opens the channel if needed in case of LN payment.
        """
        _parse_link(link).auto_pay(self)

    def auto_pay_legacy(self, address: str, amount: str):
        """Sends specified amount to a chain addres.

        The amount must be decimal in "bitcoins" (not sats).
        
        This methods generates coins if it's needed for paying.
        """
        ChainPayment(address, amount).auto_pay(self)

    def create_ln_invoice(self, amount_msat: int, memo: str) -> LnInvoiceHandle:
        """Creates a Lightning invoice with given amount and memo"""

        if self._secondary_node is None:
            self._secondary_node = self._spawn_secondary_node(SECONDARY_NODE_ID)

        self._secondary_node.wait_init()
        self._prepare_channel(self._main_ln_node, self._secondary_node.get_p2p_address().pubkey, (amount_msat + 999) // 1000)
        return self._secondary_node.create_invoice(amount_msat, memo)

    def _prepare_wallet(self):
        wallets = BitcoindProxy(service_url = self._main_bitcoind_url)._call("listwallets")
        if len(wallets) == 0:
            BitcoindProxy(service_url = self._main_bitcoind_url)._call("createwallet", "test_wallet")

    def _prepare_chain_coins(self, amount: Decimal):
        self._prepare_wallet()
        # We assume pessimistic fee 100000 sats
        # I don't care to compute halvings etc, so just generate blocks in a loop
        while BitcoindProxy(service_url = self._main_bitcoind_url).getbalance() < amount * 100000000 + 100000:
            self.mine_blocks(101)

    def _send_coins(self, address: str, amount: str):
        BitcoindProxy(service_url = self._main_bitcoind_url)._call("sendtoaddress", address, amount)

        # Confirm the transaction
        self.mine_blocks(6)

    def _spawn_secondary_node(self, secondary_node_id: str) -> LnNode:
        lnd.create_testkit_node(secondary_node_id, self._bitcoind_public_port, self._zmq_tx_port, self._zmq_block_port)
        lnd.launch_testkit_node(secondary_node_id)
        return lnd.get_testkit_node(secondary_node_id)

    def _get_ln_p2p_address_by_id(self, node_id: str) -> LnP2PAddr:
        main_ln_node_addr = self._main_ln_node.get_p2p_address()
        if main_ln_node_addr.pubkey == node_id:
            return main_ln_node_addr

        if self._secondary_node is not None:
            secondary_ln_node_addr = self._secondary_node.get_p2p_address()
            if secondary_ln_node_addr.pubkey == node_id:
                return secondary_ln_node_addr

        return self._ln_nodes_by_pubkey[node_id].get_p2p_address()

    def _open_channel(self, source: LnNode, dest: str, amount_sat: int, push_sat: int = 0, private: bool = False) -> Channel:
        # Ensure there's enough coins
        address = source.get_chain_address()
        coins_to_send = amount_sat + 100000
        # We manually format because Python likes to screw it up
        self.auto_pay_legacy(address, "%d.%d" % (coins_to_send // 100000000, coins_to_send % 100000000))

        node_address = self._get_ln_p2p_address_by_id(dest)
        channel = source.open_channel(node_address, amount_sat, push_sat, private)

        # Confirm the channel
        self.mine_blocks(6)

        return channel

    def _prepare_channel(self, source: LnNode, dest: str, amount_sat: int):
        if source.get_spendable_sat(dest) < amount_sat:
            # Reserve some capacity for more payments
            channel_capacity = max(min(amount_sat * 2, MAX_NON_WUMBO_CAPACITY), 20000)

            self._open_channel(source, dest, channel_capacity)

            # wait for the channel to activate
            while source.get_spendable_sat(dest) < amount_sat:
                sleep(1)


    def _parse_invoice(self, invoice: str) -> ParsedInvoice:
        if self._secondary_node is None:
            self._secondary_node = self._spawn_secondary_node(SECONDARY_NODE_ID)
        self._secondary_node.wait_init()

        return self._main_ln_node.parse_invoice(invoice)

    def _pay_ln_invoice(self, invoice: str):
        if self._secondary_node is None:
            self._secondary_node = self._spawn_secondary_node(SECONDARY_NODE_ID)
        self._secondary_node.wait_init()

        self._secondary_node.pay_invoice(invoice)

    def load_scenario(self, path: Path):
        """
        Loads a sceario from toml file.

        This method allows declarative specification of test scenarios
        to be loaded from a Toml file. You can then more easily implement
        various test cases involving multiple nodes.

        Example toml input:

        ```toml
        # It may be annoying to type `type = "lnd"` (:D) but we are preparing for planned
        # feature of supporting other LN implementations or things like loop
        [nodes.alice]
        type = "lnd"

        [nodes.bob]
        type = "lnd"
        # Optional wallet balance
        # Warning: this is currently MINIMUM balance for nodes with channels
        # We need to implement PSBT opening to achieve exact balance - PRs welcome!
        wallet_balance_sats = 1000000

        # alice is initiator
        [channels.alice.bob]
        # The only required field
        capacity_sats = 1000000

        # Optional fields:
        push_sats = 100000
        # from alice to bob
        fwd_fee_proportional_millionths = 1000
        fwd_fee_base_msats = 100
        fwd_timelock_delta = 144

        # from bob to alice
        rev_fee_proportional_millionths = 1000
        rev_fee_base_msats = 100
        rev_timelock_delta = 144

        # Special name "$system" can be used to connect to the node integrated into the OS
        [channels.alice."$system"]
        capacity_sats = 10000000
        ```

        The function will first asynchronously spawn all nodes,
        then it waits for all of them to be initialized,
        then it funds the wallets / opens the channels (might be in a single or two transactions).

        Don't rely on order of node spawning/channel openning operations in your test!
        The function may reorder them arbitrarily.
        You're only guaranteed that the state after the function returns successfully will
        match the description in the file.

        If the secondary node was not spawned yet, the topmost node will become the secondary node.
        If it was, you can access it with name "1".
        """

        with open(path, "r") as scenario_file:
            scenario = toml.load(scenario_file)

        try:
            nodes = scenario["nodes"]
        except KeyError:
            nodes = []

        try:
            channels = scenario["channels"]
        except KeyError:
            channels = []

        for node_name in nodes:
            if not isinstance(node_name, str):
                raise Exception("Node name must be a string")

            if not NODE_NAME_RE.fullmatch(node_name):
                raise Exception("Invalid node name %s")

            if nodes[node_name]["type"] != "lnd":
                raise Exception("unsupported node type " + nodes[node_name]["type"])

            lnd.create_testkit_node(node_name, self._bitcoind_public_port, self._zmq_tx_port, self._zmq_block_port)
            lnd.launch_testkit_node(node_name)

        self._prepare_blocks()

        # Two loops so that we launch the nodes asynchronously
        for node_name in nodes:
            #  mypy can't see what we checked above
            if not isinstance(node_name, str):
                raise Exception("Node name must be a string")

            node = lnd.get_testkit_node(node_name)
            node.wait_init()
            pubkey = node.get_p2p_address().pubkey
            self._ln_nodes_by_pubkey[pubkey] = node
            self._ln_nodes_by_name[node_name] = node

        for initiator_name in channels:
            if initiator_name == "$system":
                initiator = self._main_ln_node
            else:
                initiator = self._ln_nodes_by_name[initiator_name]

            for receiver_name in channels[initiator_name]:
                if receiver_name == "$system":
                    receiver = self._main_ln_node
                else:
                    receiver = self._ln_nodes_by_name[receiver_name]

                try:
                    push_sats = channels[initiator_name][receiver_name]["push_sats"]
                    if not isinstance(push_sats, int):
                        raise Exception("push_sats for channel %s -> %s is not an int" % (initiator_name, receiver_name))
                except KeyError:
                    push_sats = 0

                try:
                    private = channels[initiator_name][receiver_name]["private"]
                    if not isinstance(private, int):
                        raise Exception("private for channel %s -> %s is not a bool" % (initiator_name, receiver_name))
                except KeyError:
                    private = False

                channel = self._open_channel(initiator, receiver.get_p2p_address().pubkey, channels[initiator_name][receiver_name]["capacity_sats"], push_sats, private)

                try:
                    fee_base_msat = channels[initiator_name][receiver_name]["fwd_fee_base_msat"]
                    if not isinstance(fee_base_msat, int):
                        raise Exception("fwd_fee_base_msat for channel %s -> %s is not an int" % (initiator_name, receiver_name))
                except KeyError:
                    fee_base_msat = 1000

                try:
                    fee_proportional_millionths = channels[initiator_name][receiver_name]["fwd_fee_proportional_millionths"]
                    if not isinstance(fee_proportional_millionths, int):
                        raise Exception("fwd_fee_proportional_millionths for channel %s -> %s is not an int" % (initiator_name, receiver_name))
                except KeyError:
                    fee_proportional_millionths = 100

                try:
                    time_lock_delta = channels[initiator_name][receiver_name]["fwd_time_lock_delta"]
                    if not isinstance(time_lock_delta, int):
                        raise Exception("fwd_time_lock_delta for channel %s -> %s is not an int" % (initiator_name, receiver_name))
                except KeyError:
                    time_lock_delta = 144

                initiator.update_channel_policy(channel, fee_base_msat, fee_proportional_millionths, time_lock_delta)

                try:
                    fee_base_msat = channels[initiator_name][receiver_name]["rev_fee_base_msat"]
                    if not isinstance(fee_base_msat, int):
                        raise Exception("rev_fee_base_msat for channel %s -> %s is not an int" % (initiator_name, receiver_name))
                except KeyError:
                    fee_base_msat = 1000

                try:
                    fee_proportional_millionths = channels[initiator_name][receiver_name]["rev_fee_proportional_millionths"]
                    if not isinstance(fee_proportional_millionths, int):
                        raise Exception("rev_fee_proportional_millionths for channel %s -> %s is not an int" % (initiator_name, receiver_name))
                except KeyError:
                    fee_proportional_millionths = 100

                try:
                    time_lock_delta = channels[initiator_name][receiver_name]["rev_time_lock_delta"]
                    if not isinstance(time_lock_delta, int):
                        raise Exception("rev_time_lock_delta for channel %s -> %s is not an int" % (initiator_name, receiver_name))
                except KeyError:
                    time_lock_delta = 144

                receiver.update_channel_policy(channel, fee_base_msat, fee_proportional_millionths, time_lock_delta)
