"""
node_pool.py — request-level load balancing across one or more inference nodes.

The pipeline's vision passes (2, 2b, 3) send one LLM request per page / figure /
table.  NodePool spreads those individual requests across every configured node
so that even a single PDF fans its pages out over all available GPUs, instead of
pinning one whole PDF to one node.

Mechanism: each node contributes `concurrency` slots to a shared asyncio.Queue.
A request pulls any free slot (which identifies a node), runs on that node, then
returns the slot.  Because a busy node has no free slots left, requests naturally
flow to idle nodes — work-stealing with no scheduler.  Total simultaneous
requests across the whole pool = len(clients) * concurrency.
"""

import asyncio
from contextlib import asynccontextmanager


class NodePool:
    """Distributes chat-completion requests across one or more node clients.

    Pass a list of AsyncOpenAI-compatible clients (one per node) and the
    per-node concurrency limit.  All clients must live on the same event loop
    as the coroutines that use the pool.
    """

    def __init__(self, clients, concurrency, labels=None):
        if not clients:
            raise ValueError("NodePool requires at least one client")
        if labels is None:
            labels = [str(getattr(c, "base_url", f"node{i}"))
                      for i, c in enumerate(clients)]
        # Map each client object back to its label, and seed a per-node counter.
        self._label_of = {id(c): lbl for c, lbl in zip(clients, labels)}
        self._counts   = {lbl: 0 for lbl in labels}

        self._slots: asyncio.Queue = asyncio.Queue()
        # Each node contributes `concurrency` slots.  A slot *is* a client, so
        # taking a slot both picks a node and reserves capacity on it.
        for client in clients:
            for _ in range(max(1, concurrency)):
                self._slots.put_nowait(client)

    @asynccontextmanager
    async def slot(self):
        """Acquire a free node slot, yielding that node's client.

        Hold the slot for the whole request (image encoding + the call + any
        retries) so the node's concurrency limit is respected end-to-end and
        retries stay on the same node.
        """
        client = await self._slots.get()
        # Count the request against the node serving it.  Safe without a lock:
        # all consumers share one event loop and this increment never awaits.
        self._counts[self._label_of[id(client)]] += 1
        try:
            yield client
        finally:
            self._slots.put_nowait(client)

    def stats(self) -> dict:
        """Return a copy of the per-node request counts (label -> count)."""
        return dict(self._counts)

    async def create(self, **kwargs):
        """Convenience wrapper: run one chat.completions.create on a free node."""
        async with self.slot() as client:
            return await client.chat.completions.create(**kwargs)
