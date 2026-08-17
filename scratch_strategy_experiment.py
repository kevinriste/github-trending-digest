"""THROWAWAY experiment: compare HN comment selection strategies for camps analysis.

Fetches real threads once, caches the full tree, then runs several selection
strategies over the SAME tree and reports coverage metrics. No model calls.
"""

import sys
import time
from collections import deque

import requests

BUDGET = 48000
MIN_TEXT = 40
MAX_DEPTH = 6
MAX_NODES = 400
FETCH_WORKERS = 24

from concurrent.futures import ThreadPoolExecutor

S = requests.Session()


def get(item_id):
    for _ in range(3):
        try:
            r = S.get(f"https://hacker-news.firebaseio.com/v0/item/{item_id}.json", timeout=20)
            r.raise_for_status()
            return r.json()
        except requests.RequestException:
            time.sleep(0.3)
    return None


def clean(t):
    if not t:
        return ""
    # cheap tag strip; good enough for length metrics
    import re
    from html import unescape
    return unescape(re.sub(r"<[^>]+>", " ", t)).strip()


def fetch_tree(story_id):
    """Return dict of full tree: {id: {by, text, depth, root_id, root_pos, kids, parent}}."""
    story = get(story_id)
    top = [int(k) for k in (story.get("kids") or [])]
    tree = {}
    # BFS fetch whole tree (bounded) with a thread pool per level
    frontier = [(k, 1, k, i + 1, story_id) for i, k in enumerate(top)]
    while frontier:
        with ThreadPoolExecutor(max_workers=FETCH_WORKERS) as ex:
            results = list(ex.map(lambda job: (job, get(job[0])), frontier))
        nxt = []
        for (cid, depth, root_id, root_pos, parent), payload in results:
            if not payload or payload.get("type") != "comment":
                continue
            if payload.get("dead") or payload.get("deleted"):
                continue
            kids = [int(k) for k in (payload.get("kids") or [])]
            tree[cid] = {
                "id": cid, "by": payload.get("by") or "unknown",
                "text": clean(payload.get("text") or ""),
                "depth": depth, "root_id": root_id, "root_pos": root_pos,
                "kids": kids, "parent": parent,
            }
            if depth < MAX_DEPTH:
                for k in kids:
                    nxt.append((k, depth + 1, root_id, root_pos, cid))
        frontier = nxt
    return story, top, tree


def subtree_size(tree, cid):
    n = 0
    stack = list(tree[cid]["kids"])
    while stack:
        k = stack.pop()
        if k in tree:
            n += 1
            stack.extend(tree[k]["kids"])
    return n


def line_cost(node):
    return len(node["text"]) + len(node["by"]) + 4 * node["depth"] + 3


def eligible(node):
    return len(node["text"]) >= MIN_TEXT


# ---------- strategies: each returns an ordered list of node ids (contiguity handled at render) ----------

def strat_dfs_budget(story, top, tree):
    """CURRENT: DFS per top branch in rank order, global char budget."""
    kept, chars = [], 0
    for root in top:
        stack = [root]
        # DFS preserving parent-first, left-to-right
        order = []
        while stack:
            cid = stack.pop()
            if cid not in tree:
                continue
            order.append(cid)
            for k in reversed(tree[cid]["kids"]):
                stack.append(k)
        for cid in order:
            node = tree[cid]
            if not eligible(node):
                continue
            c = line_cost(node)
            if chars + c > BUDGET and kept:
                return kept
            chars += c
            kept.append(cid)
            if len(kept) >= MAX_NODES:
                return kept
    return kept


def strat_bfs_budget(story, top, tree):
    """Level-order across the WHOLE thread: all depth-1, then depth-2, ... global budget.

    Guarantees ancestors precede descendants, so contiguity holds when regrouped.
    """
    kept, chars = [], 0
    q = deque(top)
    while q:
        cid = q.popleft()
        if cid in tree:
            for k in tree[cid]["kids"]:
                q.append(k)
        if cid not in tree:
            continue
        node = tree[cid]
        if not eligible(node):
            continue
        c = line_cost(node)
        if chars + c > BUDGET and kept:
            break
        chars += c
        kept.append(cid)
        if len(kept) >= MAX_NODES:
            break
    return kept


def strat_roundrobin_branch(story, top, tree):
    """Round-robin across top branches; within each, BFS. Ancestors kept for contiguity.

    Every top thread gets its root comment before any thread goes deep.
    """
    kept, chars = [], 0
    queues = [deque([root]) for root in top]
    while any(queues):
        progressed = False
        for q in queues:
            if not q:
                continue
            cid = q.popleft()
            progressed = True
            if cid in tree:
                for k in tree[cid]["kids"]:
                    q.append(k)
            if cid not in tree:
                continue
            node = tree[cid]
            if not eligible(node):
                continue
            c = line_cost(node)
            if chars + c > BUDGET and kept:
                return kept
            chars += c
            kept.append(cid)
            if len(kept) >= MAX_NODES:
                return kept
        if not progressed:
            break
    return kept


def strat_engagement_contiguous(story, top, tree):
    """Rank comments by engagement (subtree size + reply count + shallowness), keep top
    within budget, then ADD ancestors of every kept node (for contiguity/context)."""
    scored = []
    for cid, node in tree.items():
        if not eligible(node):
            continue
        sz = subtree_size(tree, cid)
        depth_bonus = {1: 1.5, 2: 1.0, 3: 0.6}.get(node["depth"], 0.3)
        score = sz * 1.0 + len(node["kids"]) * 1.5 + depth_bonus * 3 + min(len(node["text"]), 600) / 300
        scored.append((score, cid))
    scored.sort(reverse=True)

    chosen = set()
    chars = 0

    def ancestors(cid):
        out = []
        p = tree[cid]["parent"]
        while p in tree:
            out.append(p)
            p = tree[p]["parent"]
        return out

    for _, cid in scored:
        if cid in chosen:
            continue
        needed = [a for a in ancestors(cid) if a not in chosen] + [cid]
        add_cost = sum(line_cost(tree[c]) for c in needed)
        if chars + add_cost > BUDGET and chosen:
            continue
        chars += add_cost
        chosen.update(needed)
        if len(chosen) >= MAX_NODES:
            break
    return list(chosen)


STRATS = {
    "dfs_budget(current)": strat_dfs_budget,
    "bfs_budget": strat_bfs_budget,
    "roundrobin_branch": strat_roundrobin_branch,
    "engagement_contig": strat_engagement_contiguous,
}


def metrics(name, ids, top, tree):
    ids = set(ids)
    n = len(ids)
    chars = sum(line_cost(tree[c]) for c in ids)
    top_covered = len({tree[c]["root_pos"] for c in ids})
    depths = {}
    for c in ids:
        d = tree[c]["depth"]
        depths[d] = depths.get(d, 0) + 1
    # high-engagement capture: of the 15 comments with largest subtree, how many kept?
    eng = sorted(tree.keys(), key=lambda c: subtree_size(tree, c), reverse=True)[:15]
    eng_captured = sum(1 for c in eng if c in ids)
    dd = {k: depths.get(k, 0) for k in range(1, MAX_DEPTH + 1)}
    print(f"  {name:24s} nodes={n:3d} chars={chars:5d} top_threads={top_covered:2d}/{len(top):2d} "
          f"eng15={eng_captured:2d}/15 depths={dd}")


def main():
    story_ids = [int(x) for x in sys.argv[1:]] if len(sys.argv) > 1 else None
    if not story_ids:
        ids = requests.get("https://hacker-news.firebaseio.com/v0/topstories.json", timeout=20).json()
        story_ids = []
        for i in ids[:40]:
            it = get(i)
            if it and it.get("type") == "story" and (it.get("descendants") or 0) > 100:
                story_ids.append(i)
            if len(story_ids) >= 4:
                break
    for sid in story_ids:
        story, top, tree = fetch_tree(sid)
        print(f"\nSTORY {sid}: {story.get('title')!r}  descendants={story.get('descendants')} "
              f"top_threads={len(top)} tree_nodes={len(tree)}")
        for name, fn in STRATS.items():
            metrics(name, fn(story, top, tree), top, tree)


if __name__ == "__main__":
    main()
