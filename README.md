# LNP/BP Testkit

A framework for writing automated tests of applications built on top of LNP/BP

## About

This framework allows you to easily test flow in your application involving LNP/BP payments.
The usual flow is:

1. Create a payment request (BIP21 or BOLT11) in the application
2. Pay the request
3. Check if the application received the payment

This looks simple, but the step #2 is far from trivial.
It involves setting up regtest bitcoind, two LN nodes, opening a sufficiently big channel between the nodes, using the appropriate RPC API...
The aim of this project is to make step #2 two lines of Python:

```python
from lnpbp_testkit.cadr import network
network().auto_pay(request)
```

As you can see from `cadr`, this integrates with [Cryptoanarchy Debian Repository](https://github.com/Kixunil/cryptoanarchy-deb-repo-builder) which is recommended but not mandatory.
Read below for instructions on standalone use.

## Supported and wanted features

- [x] Pay any BIP21 request (with `bitcoin:` scheme)
- [x] Pay any BOLT11 invoice (with or without `lightning:` scheme)
- [x] Pay custom amount to chain address
- [ ] Pay using LNURL-pay
- [ ] Pay using LNURL-withdraw
- [ ] Pay using LN keysend
- [ ] Pay using PayJoin
- [ ] Withdraw using BIP21 request
- [ ] Withdraw using chain address
- [ ] Withdraw using BOLT11
- [ ] Withdraw using LNURL-withdraw
- [ ] Withdraw using LNURL-pay
- [ ] Withdraw using LN keysend
- [ ] Withdraw using PayJoin
- [ ] Support more complicated situations involving multiple LN nodes
- [ ] Support non-LND LN implementations

Feel free to submit PRs for wanted features!
(If you're confused about LNURL: withdraw using pay and pay using withdraw are useful if you're testing wallets, not services.)

## Usage 

### With Cryptoanarchy Debian Repository (recommended)

The Testkit is in the repository, just `apt install python3-lnpbp-testkit`.
**Important: do NOT install it outside of apt! Versions different than what's in the repository may not work.**
If you don't have passwordless `sudo` you must set the permissions using `sudo usermod -a -G bitcoin-regtest,lnd-system-regtest $USER`.
The Testkit assumes your application is integrated with the repository too,
specifically with `bitcoin-regtest`/`lnd-system-regtest`.

Once you have created and obtained the payment request (e.g. using Selenium) you can just call `lnpbp_testkit.cadr.network().auto_pay(request)` to pay it.

Yes, it's that simple. :)

### Without Cryptoanarchy Debian Repository

0. Make sure you have `systemd` installed and active (warning: this is not the default in most Docker images!)
1. Install `lnd` into your `$PATH`
2. Setup regtest `bitcoind`
3. Setup regtest `lnd` connected to `bitcoind`
4. Copy `lnd-testkit-regtest@.service` file from this repository into `/etc/systemd/user/`
5. Instantiate `Network` by supplying the required parameters:

```python
network_config = {
	"bitcoind_url" : "USERNAME:PASSWORD@HOST:PORT",
        "bitcoind_public_port": BITCOIN_RPC_PROXY_PORT, # optional, the port from bitcoind_url will be used if not present
        "zmq_tx_port": ZMQPUBRAWTX_PORT,
        "zmq_block_port": ZMQPUBRAWBLOCK_PORT,
	"lnd_host": "REST_HOST:REST_PORT",
	"lnd_macaroon": "HEX_ENCODED_MACAROON",
	"lnd_tls_cert_file": "PATH_TO_LND_TLS_CERTIFICATE",
}
network = lnpbp_testkit.Network(network_config)
```

5. You can now pay the request using `network.auto_pay()`

## FAQ

### How do I pay an arbitrary amount to a chain address?

Use `auto_pay_legacy(address, decimal_amount)` instead of `auto_pay`

### Do I need to generate coins before I can pay the BIP21 request?

No, it's automatic, ad-hoc.

### How do I setup the channels between the nodes?

Don't, it's automatic, ad-hoc based on the amount in the invoice.

### Do I need to create a Debian package for my application in order to test the integration with Cryptoanarchy Debian Repository?

No, just configure it with the appropriate settings and launch it before running the test.
That being said, it'd be nice if you avoid hard-coding the parameters of your app into the test - it will make writing tests for packaged application easier.
I plan to support this use case more explicitly in the future.
It will be useful in CI to save time by skipping build of the Debian package.

### Why does `auto_pay()` take so long to execute?

One of the key features of `auto_pay()` is that it generates coins/sets up channels on-demand.
It also spawns secondary LN node when needed.
This obviously takes some time and LND isn't particularly fast when it comes to initialization.
If you want to improve it, consider writing a PR against LND to speed it up, or complaining at their repository.
As another optimization, you can call the `warm_up()` method at the beginning of the test.
This will start LND asynchronously, so that you can perform other operations (such as setting up a web service using Selenium) in parallel to LND initializing.

### Does this project use semantic versioning?

Yes. 0.2.0 will mean breaking change.
