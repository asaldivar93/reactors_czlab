#!/usr/bin/env python3
"""Browse only nodes whose BrowseName starts with 'R#' (and then show all their descendants).

Examples:
  python browse_r_hash.py --endpoint opc.tcp://127.0.0.1:4840
  python browse_r_hash.py --endpoint opc.tcp://127.0.0.1:4840 --depth 6
  python browse_r_hash.py --endpoint opc.tcp://127.0.0.1:4840 --values

"""

from __future__ import annotations

import argparse
import asyncio
import re

from asyncua import Client
from asyncua.common import Node

R_NUMERIC_BROWSENAME = re.compile(r"^R\d+$")


def indent(level: int) -> str:
    return "  " * level


async def node_line(node: Node) -> str:
    """Build one printable line with useful metadata."""
    try:
        nodeid = node.nodeid.to_string()
    except Exception as e:
        print(e)
        nodeid = "<unknown nodeid>"

    try:
        disp_name = (await node.read_display_name()).Text
    except Exception as e:
        print(e)
        disp_name = "<no display name>"

    try:
        browse_name = await node.read_browse_name()
        # bn is a QualifiedName with .Name and .NamespaceIndex
        bn_str = f"{browse_name.NamespaceIndex}:{browse_name.Name}"
    except Exception as e:
        print(e)
        bn_str = "<no browse name>"

    try:
        node_class = await node.read_node_class()
        nc_str = node_class.name
    except Exception as e:
        print(e)
        nc_str = "<unknown class>"

    try:
        data_type = await node.read_data_type()
        dt_str = str(data_type)
    except Exception as e:
        print(e)
        dt_str = "<unknown data type>"

    parts = [
        f"DisplayName:{disp_name}",
        f"NodeClasss:[{nc_str}]",
        f"BrowseName:{bn_str}",
        f"NodeId:{nodeid}",
        f"DataType:{dt_str}",
    ]

    return " | ".join(parts)


async def print_subtree(
    node: Node,
    level: int,
    max_depth: int,
    visited: set[str],
    max_children: int,
):
    """Print node and all descendants (no filtering inside subtree)."""
    # loop protection
    try:
        nodeid = node.nodeid.to_string()
    except Exception as e:
        print(e)
        nodeid = f"<unknown:{id(node)}>"

    if nodeid in visited:
        print(f"{indent(level)}↩ {nodeid} (already visited)")
        return
    visited.add(nodeid)

    print(f"{indent(level)}- {await node_line(node)}")

    if level >= max_depth:
        return

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
        await print_subtree(
            child,
            level + 1,
            max_depth,
            visited,
            max_children,
        )


async def browse_for_r_hash_roots(
    objects_node: Node,
    search_depth: int,
    max_children: int,
) -> list:
    """Find nodes under objects_node whose BrowseName starts with prefix.

    Returns a list of matching nodes.
    """
    matches = []
    visited = set()

    async def recurse(node: Node, level: int):
        # loop protection
        try:
            nodeid = node.nodeid.to_string()
        except Exception:
            nodeid = f"<unknown:{id(node)}>"

        if nodeid in visited:
            return
        visited.add(nodeid)

        # Check browse name
        try:
            bn = await node.read_browse_name()
            name = bn.Name  # just the name (no namespace index)
        except Exception:
            name = ""

        if R_NUMERIC_BROWSENAME.match(name):
            matches.append(node)
            # IMPORTANT: do NOT recurse further from here for searching,
            # because we only need roots that start with R#.
            # (We’ll print their full subtree later.)
            return

        if level >= search_depth:
            return

        try:
            children = await node.get_children()
        except Exception:
            return

        if max_children > 0 and len(children) > max_children:
            children = children[:max_children]

        for child in children:
            await recurse(child, level + 1)

    await recurse(objects_node, 0)
    return matches


async def main():
    parser = argparse.ArgumentParser(
        description="Browse only 'R#' roots and their descendants using asyncua.",
    )
    parser.add_argument(
        "--endpoint",
        required=True,
        help="OPC UA endpoint URL, e.g. opc.tcp://127.0.0.1:4840",
    )
    parser.add_argument(
        "--prefix",
        default="R#",
        help="BrowseName prefix to match (default: R#)",
    )
    parser.add_argument(
        "--search-depth",
        type=int,
        default=3,
        help="How deep to SEARCH for R# nodes (default: 3)",
    )
    parser.add_argument(
        "--depth",
        type=int,
        default=6,
        help="How deep to PRINT each matched subtree (default: 6)",
    )
    parser.add_argument(
        "--values",
        action="store_true",
        help="Read and print Variable values (best effort)",
    )
    parser.add_argument(
        "--max-children",
        type=int,
        default=500,
        help="Max children per node (0 = unlimited)",
    )
    parser.add_argument("--user", default=None, help="Username (optional)")
    parser.add_argument("--password", default=None, help="Password (optional)")
    args = parser.parse_args()

    client = Client(url=args.endpoint)
    if args.user is not None:
        client.set_user(args.user)
        client.set_password(args.password or "")

    async with client:
        objects = client.nodes.objects
        print(f"Connected to: {args.endpoint}")
        print(
            f"Searching under Objects for BrowseName starting with '{args.prefix}' (search depth={args.search_depth})...\n",
        )

        roots = await browse_for_r_hash_roots(
            objects_node=objects,
            search_depth=args.search_depth,
            max_children=args.max_children,
        )

        if not roots:
            print("No matching nodes found.")
            return

        print(f"Found {len(roots)} matching root(s):\n")

        for i, root in enumerate(roots, start=1):
            print(f"=== Match {i} ===")
            await print_subtree(
                root,
                level=0,
                max_depth=args.depth,
                visited=set(),
                max_children=args.max_children,
            )
            print()  # blank line between trees


if __name__ == "__main__":
    asyncio.run(main())
