"""shardring.core 的验收测试。

断言虚拟节点布局、键的归属、环上二分与回绕、节点增删时的最小迁移，
覆盖正常查询、撞格去重、空环与非法输入。
"""

import unittest

from shardring import RingError, ShardRing

BITS = 6
VNODES = 4
LAYOUT = ("alpha", "beta", "gamma", "delta")
JOINER = "epsilon"
ITEMS = [("key-%02d" % index, "值-%02d" % index) for index in range(24)]


def spread_hash(text):
    """测试注入的确定性哈希：把标签摊成一个大整数。"""
    value = 0
    for char in text:
        value = (value * 37 + ord(char)) % 1000003
    value = (value * 2654435761) % (1 << 32)
    value ^= value >> 6
    return value


def colliding_hash(text):
    """测试注入的确定性哈希：两个标签固定在撞到同一格，其余照常摊开。"""
    fixed = {"alpha#1": 17, "beta#2": 17}
    if text in fixed:
        return fixed[text]
    return spread_hash(text)


def ring_of(*names, hash_fn=spread_hash):
    """按给定名字建一个环。"""
    ring = ShardRing(hash_fn, bits=BITS, vnodes=VNODES)
    for name in names:
        ring.add_node(name)
    return ring


def slot_of(key, hash_fn=spread_hash):
    """键在环上的落点。"""
    return hash_fn(key) % (1 << BITS)


def expected_claims(names, hash_fn=spread_hash):
    """按环的定义直接算出每个虚拟节点的落点与归属，不使用环自己的布局。"""
    claims = {}
    for name in sorted(names):
        for index in range(VNODES):
            position = hash_fn("%s#%d" % (name, index)) % (1 << BITS)
            if position not in claims:
                claims[position] = name
    return claims


def expected_owner(claims, position):
    """环上不小于 position 的最小格子的归属，逐格扫描。"""
    for slot in sorted(claims):
        if slot >= position:
            return claims[slot]
    return claims[min(claims)]


def key_on_slot(position):
    """找一把哈希正好落在第 position 格上的键。"""
    for index in range(20000):
        key = "slot-key-%d" % index
        if slot_of(key) == position:
            return key
    raise AssertionError("找不到落在第 %d 格上的键" % position)


class LayoutTests(unittest.TestCase):
    def test_virtual_nodes_are_laid_out_by_label(self):
        ring = ring_of(*LAYOUT)
        claims = expected_claims(LAYOUT)
        self.assertEqual(ring.nodes(), sorted(LAYOUT))
        self.assertEqual(ring.ring_positions(), sorted(claims))
        self.assertEqual(
            ring.ring_positions(),
            [1, 4, 20, 24, 26, 27, 32, 34, 37, 43, 44, 46, 48, 51, 53, 60],
        )
        self.assertEqual(ring.positions_of("alpha"), [4, 27, 43, 51])
        for name in LAYOUT:
            self.assertEqual(ring.vnodes_of(name), VNODES, name)
            self.assertEqual(
                ring.positions_of(name),
                sorted(position for position, owner in claims.items()
                       if owner == name),
                name,
            )


class DedupeTests(unittest.TestCase):
    def test_a_contested_slot_belongs_to_one_node_only(self):
        ring = ShardRing(colliding_hash, bits=BITS, vnodes=VNODES)
        ring.add_node("alpha")
        ring.add_node("beta")
        claims = expected_claims(("alpha", "beta"), colliding_hash)
        self.assertIn(17, claims)
        self.assertEqual(claims[17], "alpha")
        self.assertEqual(ring.locate_position(17), "alpha")
        self.assertEqual(ring.ring_positions(), sorted(claims))
        self.assertEqual(
            ring.positions_of("alpha"),
            sorted(position for position, owner in claims.items()
                   if owner == "alpha"),
        )
        self.assertEqual(
            ring.positions_of("beta"),
            sorted(position for position, owner in claims.items()
                   if owner == "beta"),
        )
        self.assertEqual(ring.vnodes_of("alpha"), VNODES)
        self.assertEqual(ring.vnodes_of("beta"), VNODES - 1)
        self.assertEqual(
            set(ring.positions_of("alpha")) & set(ring.positions_of("beta")),
            set(),
        )


class LocateTests(unittest.TestCase):
    def test_keys_go_to_the_owner_of_their_slot(self):
        ring = ring_of(*LAYOUT)
        claims = expected_claims(LAYOUT)
        exact = key_on_slot(min(claims))
        for key in [item[0] for item in ITEMS] + [exact]:
            position = slot_of(key)
            owner = expected_owner(claims, position)
            self.assertEqual(ring.hash_key(key), position, key)
            self.assertEqual(ring.locate(key), owner, key)
            self.assertEqual(ring.locate_position(position), owner, key)


class SlotSearchTests(unittest.TestCase):
    def test_a_key_landing_on_a_slot_belongs_to_its_owner(self):
        ring = ring_of(*LAYOUT)
        claims = expected_claims(LAYOUT)
        for position in sorted(claims):
            key = key_on_slot(position)
            self.assertEqual(ring.hash_key(key), position, key)
            self.assertEqual(ring.locate(key), claims[position], position)
            self.assertEqual(
                ring.locate_position(position), claims[position], position
            )
            self.assertEqual(
                ring.locate_position(position + 1),
                expected_owner(claims, position + 1),
                position,
            )


class WrapTests(unittest.TestCase):
    def test_the_ring_wraps_after_the_last_slot(self):
        ring = ring_of(*LAYOUT)
        claims = expected_claims(LAYOUT)
        first = min(claims)
        last = max(claims)
        self.assertEqual(ring.ring_positions()[0], first)
        self.assertEqual(ring.ring_positions()[-1], last)
        self.assertEqual(ring.successor_position(last), last)
        self.assertEqual(ring.locate_position(last), claims[last])
        self.assertEqual(ring.successor_position(last + 1), first)
        self.assertEqual(ring.locate_position(last + 1), claims[first])
        self.assertEqual(ring.successor_position(ring.size - 1), first)
        self.assertEqual(ring.locate_position(ring.size - 1), claims[first])
        self.assertEqual(ring.successor_position(first), first)
        self.assertEqual(ring.locate_position(first), claims[first])


class DataTests(unittest.TestCase):
    def test_values_sit_on_their_owner(self):
        ring = ring_of(*LAYOUT)
        claims = expected_claims(LAYOUT)
        items = list(ITEMS) + [(key_on_slot(min(claims)), "落在第一格上")]
        for key, value in items:
            ring.put(key, value)
        for key, value in items:
            owner = expected_owner(claims, slot_of(key))
            self.assertIn(key, ring.keys_of(owner), key)
        for key, value in items:
            holders = [name for name in ring.nodes() if key in ring.keys_of(name)]
            self.assertEqual(len(holders), 1, key)
        for key, value in items:
            self.assertEqual(ring.get(key), value, key)
        ring.put("key-00", "改过的值")
        self.assertEqual(ring.get("key-00"), "改过的值")
        self.assertEqual(
            len([name for name in ring.nodes() if "key-00" in ring.keys_of(name)]),
            1,
        )


class JoinTests(unittest.TestCase):
    def test_a_new_node_takes_only_its_own_keys(self):
        ring = ring_of(*LAYOUT)
        for key, value in ITEMS:
            ring.put(key, value)
        before = dict((key, ring.locate(key)) for key, _ in ITEMS)
        ring.add_node(JOINER)
        self.assertEqual(ring.nodes(), sorted(LAYOUT + (JOINER,)))
        after = dict((key, ring.locate(key)) for key, _ in ITEMS)
        moved = sorted(key for key, _ in ITEMS if before[key] != after[key])
        self.assertEqual(
            moved,
            sorted(key for key in after if after[key] == JOINER),
            "换主人的键应当正好是转给新节点的那些",
        )
        for key, value in ITEMS:
            self.assertIn(key, ring.keys_of(after[key]), key)
        self.assertEqual(
            ring.keys_of(JOINER),
            sorted(key for key in after if after[key] == JOINER),
        )
        for key, value in ITEMS:
            self.assertEqual(ring.get(key), value, key)


class LeaveTests(unittest.TestCase):
    def test_a_leaving_node_hands_over_only_its_own_keys(self):
        ring = ring_of(*LAYOUT)
        for key, value in ITEMS:
            ring.put(key, value)
        before = dict((key, ring.locate(key)) for key, _ in ITEMS)
        ring.remove_node("gamma")
        self.assertEqual(
            ring.nodes(), sorted(name for name in LAYOUT if name != "gamma")
        )
        after = dict((key, ring.locate(key)) for key, _ in ITEMS)
        moved = sorted(key for key, _ in ITEMS if before[key] != after[key])
        self.assertEqual(
            moved,
            sorted(key for key in before if before[key] == "gamma"),
            "只有退出节点的键会换主人",
        )
        for key, value in ITEMS:
            self.assertIn(key, ring.keys_of(after[key]), key)
        for key, value in ITEMS:
            self.assertEqual(ring.get(key), value, key)
        held = sorted(key for name in ring.nodes() for key in ring.keys_of(name))
        self.assertEqual(held, sorted(key for key, _ in ITEMS))


class EmptyRingTests(unittest.TestCase):
    def test_an_empty_ring_reports_an_error(self):
        ring = ShardRing(spread_hash, bits=BITS, vnodes=VNODES)
        self.assertEqual(ring.nodes(), [])
        self.assertEqual(ring.ring_positions(), [])
        with self.assertRaises(RingError):
            ring.successor_position(3)
        with self.assertRaises(RingError):
            ring.locate_position(3)
        with self.assertRaises(RingError):
            ring.locate("key")
        with self.assertRaises(RingError):
            ring.put("key", "值")
        ring.add_node("alpha")
        ring.remove_node("alpha")
        self.assertEqual(ring.nodes(), [])
        self.assertEqual(ring.ring_positions(), [])
        with self.assertRaises(RingError):
            ring.locate("key")


class InputTests(unittest.TestCase):
    def test_bad_input_is_rejected(self):
        self.assertRaises(TypeError, ShardRing, "不是函数")
        self.assertRaises(RingError, ShardRing, spread_hash, 0)
        self.assertRaises(RingError, ShardRing, spread_hash, True)
        self.assertRaises(RingError, ShardRing, spread_hash, BITS, 0)
        ring = ring_of("alpha")
        self.assertRaises(RingError, ring.add_node, "")
        self.assertRaises(TypeError, ring.add_node, 7)
        self.assertRaises(RingError, ring.add_node, "alpha")
        self.assertRaises(RingError, ring.remove_node, "beta")
        self.assertRaises(TypeError, ring.hash_key, 7)
        self.assertRaises(RingError, ring.hash_key, "")
        self.assertRaises(RingError, ring.locate_position, -1)
        self.assertRaises(RingError, ring.locate_position, 1 << BITS)
        self.assertRaises(TypeError, ring.locate_position, "3")
        self.assertRaises(RingError, ring.keys_of, "beta")
        self.assertRaises(RingError, ring.positions_of, "")
        self.assertRaises(TypeError, ring.positions_of, 3)


if __name__ == "__main__":
    unittest.main()
