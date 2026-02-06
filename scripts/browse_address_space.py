"""Browse an OPC UA server address space using asyncua.

Usage:
  python browse_address_space.py --endpoint opc.tcp://127.0.0.1:4840
  python browse_address_space.py --endpoint opc.tcp://127.0.0.1:4840 --depth 4 --values
  python browse_address_space.py --endpoint opc.tcp://127.0.0.1:4840 --user myuser --password mypass

Generated with ChatGPT
"""

import argparse
import asyncio

from asyncua import Client, ua
from asyncua.common import Node


def indent(level: int) -> str:
    return "  " * level


async def node_class_name(node_class: ua.NodeClass) -> str:
    # ua.NodeClass is an enum; name is nicer when available
    try:
        return node_class.name
    except Exception:
        return safe_str(node_class)


async def try_read_datatype(node: Node) -> str:
    """Best-effort read of a Variable node's DataType attribute.

    Returns a short string if possible.
    """
    try:
        dt = await node.read_data_type()  # NodeId of the datatype
        return str(dt)
    except Exception:
        return "no-data-type"


async def browse_tree(
    node: Node,
    level: int,
    max_depth: int,
    visited: set,
    max_children: int,
):
    """Recursively browse children of `node` to `max_depth`.

    Uses a visited set to avoid infinite loops in case of cyclic references.
    """
    try:
        nodeid = node.nodeid.to_string()
    except Exception as e:
        print(e)
        nodeid = "<unknown nodeid>"

    # De-dupe by NodeId string form
    if nodeid in visited:
        print(f"{indent(level)}↩ {nodeid} (already visited)")
        return
    visited.add(nodeid)

    # Read metadata (best effort)
    try:
        display_name = (await node.read_display_name()).Text
    except Exception as e:
        print(e)
        display_name = "<no display name>"

    try:
        browse_name = await node.read_browse_name()
        browse_name_str = str(browse_name)
    except Exception:
        print(e)
        browse_name_str = "<no browse name>"

    try:
        nclass = await node.read_node_class()
        nclass_str = await node_class_name(nclass)
    except Exception:
        nclass_str = "<unknown class>"

    # Optional: datatype + value (only really for Variables)
    dtype_str = None
    value_str = None
    if nclass_str == "Variable":
        dtype_str = await try_read_datatype(node)
        if show_values:
            value_str = await try_read_value(node)

    # Print line
    parts = [
        f"{indent(level)}- {display_name}",
        f"[{nclass_str}]",
        f"NodeId={nodeid_key}",
    ]
    parts.append(f"BrowseName={browse_name_str}")
    if dtype_str:
        parts.append(f"DataType={dtype_str}")
    if value_str is not None:
        parts.append(f"Value={value_str}")
    print(" | ".join(parts))

    # Stop if depth reached
    if level >= max_depth:
        return

    # Browse children
    try:
        children = await node.get_children()
    except Exception as e:
        print(
            f"{indent(level + 1)}<cannot get children: {type(e).__name__}: {e}>",
        )
        return

    if max_children > 0 and len(children) > max_children:
        print(
            f"{indent(level + 1)}<showing first {max_children} of {len(children)} children>",
        )
        children = children[:max_children]

    for child in children:
        await browse_tree(
            child,
            level + 1,
            max_depth,
            show_values,
            visited,
            max_children,
        )


async def main():
    parser = argparse.ArgumentParser(
        description="Browse OPC UA address space using asyncua.",
    )
    parser.add_argument(
        "--endpoint",
        required=True,
        help="OPC UA endpoint URL, e.g. opc.tcp://127.0.0.1:4840",
    )
    parser.add_argument(
        "--depth",
        type=int,
        default=3,
        help="Max browse depth (default: 3)",
    )
    parser.add_argument(
        "--values",
        action="store_true",
        help="Read and print Variable values (best effort)",
    )
    parser.add_argument(
        "--max-children",
        type=int,
        default=200,
        help="Max children per node (0 = unlimited)",
    )
    parser.add_argument("--user", default=None, help="Username (optional)")
    parser.add_argument("--password", default=None, help="Password (optional)")
    args = parser.parse_args()

    client = Client(url=args.endpoint)

    # Optional username/password auth
    if args.user is not None:
        client.set_user(args.user)
        client.set_password(args.password or "")

    # Tip: If you need certificates/security policy, asyncua supports set_security_string(...)
    # Example:
    # client.set_security_string(
    #   "Basic256Sha256,SignAndEncrypt,cert.der,key.pem,server_cert.der"
    # )

    async with client:
        print(f"Connected to: {args.endpoint}\n")

        # Start browsing at Objects folder (most common entry point for application data)
        root = client.nodes.root
        objects = client.nodes.objects

        print("=== Root ===")
        await browse_tree(
            root,
            level=0,
            max_depth=1,
            show_values=args.values,
            visited=set(),
            max_children=args.max_children,
        )

        print("\n=== Objects ===")
        await browse_tree(
            objects,
            level=0,
            max_depth=args.depth,
            show_values=args.values,
            visited=set(),
            max_children=args.max_children,
        )


if __name__ == "__main__":
    asyncio.run(main())
