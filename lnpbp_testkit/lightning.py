from __future__ import annotations
from datetime import datetime
from time import sleep

try:
    from typing import Protocol
except ImportError:  # pragma: nocover
    from typing_extensions import Protocol # type: ignore

class Channel:
    txid: str
    output_index: int

    def __init__(self, txid: str, output_index: int):
        self.txid = txid
        self.output_index = output_index

class P2PAddr:
    pubkey: str
    host: str
    port: int

    def __init__(self, pubkey: str, host: str, port: int):
        self.pubkey = pubkey
        self.host = host
        self.port = port

    @staticmethod
    def parse(addr: str) -> P2PAddr:
        pubkey, host_port = addr.split('@')
        host, port_str = host_port.split(':')

        return P2PAddr(pubkey, host, int(port_str))

    def serialize(self) -> str:
        return "%s@%s:%d" % (self.pubkey, self.host, self.port)

class ParsedInvoice:
    dest: str
    amount_msat: int
    expiry: datetime

class InvoiceHandle:
    """Handle with BOLT11 invoice that can be easily checked for payment"""
    _bolt11: str
    _node: LnNode

    def bolt11(self) -> str:
        return self._bolt11

    def uri(self) -> str:
        return "lightning:%s" % self._bolt11

    def is_paid(self) -> bool:
        return self._node.is_invoice_paid(self._bolt11)

    def wait_paid(self) -> bool:
        return self._node.wait_invoice_paid(self._bolt11)

class LnNode(Protocol):
    def get_p2p_address(self) -> P2PAddr:
        """Returns P2P address of the node"""

    def open_channel(self, peer: P2PAddr, capacity_sat: int, push_sat: int = 0, private: bool = False) -> Channel:
        """Opens a channel with peer `peer` with capacity `capacity_sat`"""

    def get_spendable_sat(self, dest: str) -> int:
        """Returns how many sats can be sent to given node"""

    def get_chain_balance(self) -> int:
        """Returns on-chain balance in sats"""

    def get_chain_address(self) -> str:
        """Returns on-chain deposit address"""

    def parse_invoice(self, invoice: str) -> ParsedInvoice:
        """Parse given invoice"""

    def pay_invoice(self, invoice: str):
        """Pay given invoice"""

    def create_invoice(self, amount_msat: int, memo: str) -> InvoiceHandle:
        """Creates an invoice with given amount and memo"""

    def is_invoice_paid(self, invoice: str) -> bool:
        """Checks if the invoice is paid"""

    def wait_invoice_paid(self, invoice: str) -> bool:
        """Checks if the invoice is paid

        The default implementation just calls is_invoice_paid() in a loop
        """

        parsed = self.parse_invoice(invoice)
        while datetime.now() < parsed.expiry and not self.is_invoice_paid(invoice):
            sleep(1)
        return self.is_invoice_paid(invoice)

    def wait_init(self):
        """Blocks until the node is fully functional"""

    def update_channel_policy(self, channel: Channel, base_fee_msat: int, fee_proportional_millionths: int, time_lock_delta: int):
        """Updates the policy of `channel`"""
