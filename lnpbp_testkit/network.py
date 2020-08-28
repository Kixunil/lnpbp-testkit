from __future__ import annotations
from bitcoin.rpc import Proxy as BitcoindProxy
from decimal import Decimal
from time import sleep
from xdg.BaseDirectory import load_first_config
from .lightning import LnNode, ParsedInvoice
from .lightning import P2PAddr as LnP2PAddr
from .lnd import LndRest
from . import lnd

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

try:
    from typing import Protocol  # type: ignore
except ImportError:  # pragma: nocover
    from typing_extensions import Protocol  # type: ignore

SECONDARY_NODE_ID = "1"

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
        network._prepare_channel(parsed_invoice.dest, (parsed_invoice.amount_msat + 999) // 1000)
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
            if link.startswith("lnrt"):
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
                end = len(params) - pos

            amount = params[pos:end]

            return ChainPayment(address, amount)
        if schema == "lightning":
            return LightningPayment(link[len(schema) + 1:])

        raise InvalidPaymentLink("Unknown schema")

class Network:
    _main_bitcoind_url: str
    _bitcoind_public_port: int
    _zmq_tx_port: int
    _zmq_block_port: int
    _main_ln_node: LnNode
    _secondary_node: Optional[LnNode] = None

    def __init__(self, data: Mapping[str, Any]):
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


    def auto_pay(self, link: str):
        _parse_link(link).auto_pay(self)

    def auto_pay_legacy(self, address: str, amount: str):
        ChainPayment(address, amount).auto_pay(self)

    def _prepare_chain_coins(self, amount: Decimal):
        # We assume pessimistic fee 100000 sats
        # I don't care to compute halvings etc, so just generate blocks in a loop
        while BitcoindProxy(service_url = self._main_bitcoind_url).getbalance() < amount * 100000000 + 100000:
            address = BitcoindProxy(service_url = self._main_bitcoind_url)._call("getnewaddress")
            BitcoindProxy(service_url = self._main_bitcoind_url)._call("generatetoaddress", 101, address)

    def _send_coins(self, address: str, amount: str):
        BitcoindProxy(service_url = self._main_bitcoind_url)._call("sendtoaddress", address, amount)

        # Confirm the transaction
        address = BitcoindProxy(service_url = self._main_bitcoind_url)._call("getnewaddress")
        BitcoindProxy(service_url = self._main_bitcoind_url)._call("generatetoaddress", 6, address)

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

        raise Exception("Unknown node " + node_id)

    def _prepare_channel(self, dest: str, amount_sat: int):
        if self._secondary_node is None:
            self._secondary_node = self._spawn_secondary_node(SECONDARY_NODE_ID)

        if self._secondary_node.get_spendable_sat(dest) < amount_sat:
            # Reserve some capacity for more payments
            channel_capacity = amount_sat * 2
            # Ensure there's enough coins
            while self._secondary_node.get_chain_balance() < channel_capacity + 100000:
                address = self._secondary_node.get_chain_address()
                BitcoindProxy(service_url = self._main_bitcoind_url)._call("generatetoaddress", 101, address)

            node_address = self._get_ln_p2p_address_by_id(dest)
            self._secondary_node.open_channel(node_address, channel_capacity)

            # Confirm the channel
            # Something is wrong here, so let's retry
            try:
                address = BitcoindProxy(service_url = self._main_bitcoind_url)._call("getnewaddress")
            except:
                sleep(5)
                address = BitcoindProxy(service_url = self._main_bitcoind_url)._call("getnewaddress")
            BitcoindProxy(service_url = self._main_bitcoind_url)._call("generatetoaddress", 6, address)

            # wait for the channel to activate
            while self._secondary_node.get_spendable_sat(dest) < amount_sat:
                sleep(1)

    def _parse_invoice(self, invoice: str) -> ParsedInvoice:
        if self._secondary_node is None:
            self._secondary_node = self._spawn_secondary_node(SECONDARY_NODE_ID)

        return self._main_ln_node.parse_invoice(invoice)

    def _pay_ln_invoice(self, invoice: str):
        if self._secondary_node is None:
            self._secondary_node = self._spawn_secondary_node(SECONDARY_NODE_ID)

        self._secondary_node.pay_invoice(invoice)