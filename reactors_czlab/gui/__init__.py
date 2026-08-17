"""Web GUI for the bioreactor controller.

Runs on the PC and on the Raspberry Pi, connects to the OPC UA server as
a client, and exposes the read/write variables and methods the server
publishes to an operator.

Layered so that the parts worth testing can be tested without a browser:
``address``, ``format`` and ``controllers`` are pure, ``state`` owns the
one connection, ``components`` and ``pages`` are assembly.
"""
