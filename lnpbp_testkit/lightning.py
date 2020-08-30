from __future__ import annotations

try:
    from typing import Protocol
except ImportError:  # pragma: nocover
    from typing_extensions import Protocol # type: ignore

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

class LnNode(Protocol):
    def get_p2p_address(self) -> P2PAddr:
        """Returns P2P address of the node"""

    def open_channel(self, peer: P2PAddr, capacity_sat: int):
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

    def wait_init(self):
        """Blocks until the node is fully functional"""
