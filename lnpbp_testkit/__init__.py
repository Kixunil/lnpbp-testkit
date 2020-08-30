"""LNP/BP Testkit is a framework for writing automated tests of applications built on top of LNP/BP.

See the README for more information.

The most important items in this framework are:

* The `Network` class if you're using the testkit outside Cryptoanarchy Debian Repository
* The `cadr` module (or more specifically, the `cadr.network()` function) if you're using Cryptoanarchy Debian Repository.
"""

from .network import Network
