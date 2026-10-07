"""shardring —— 一致性哈希环内核（纯标准库，行为完全确定）。

把物理节点铺成环上的一组虚拟节点，键按自己的哈希落点寻找归属；节点增删时
只有归属真的变了的键会换节点。哈希函数由调用方注入：调用方给出
``hash_fn(text) -> int``，内核自己不做任何哈希，也不读写文件、不联网、
不取真实时钟、不使用随机数，同一个输入永远得到同样的结果。

约定：

* 环空间是 0 .. size - 1，size = 1 << bits；
* 每个节点铺 vnodes 个虚拟节点，第 i 个虚拟节点的标签是 "<名字>#<i>"，
  它在环上的位置是 hash_fn(标签) % size；
* 两个虚拟节点可以落在同一格：这一格归名字排序靠前的节点，另一个虚拟节点被
  丢掉，所以一个节点实际占用的虚拟节点数可能少于 vnodes；
* 环上不小于给定位置的最小格子叫它的后继位置，环上没有更大的格子时绕回第一格。
"""

import bisect

DEFAULT_BITS = 8
DEFAULT_VNODES = 8


class RingError(ValueError):
    """环操作相关的错误。"""


class ShardRing:
    """一致性哈希环：节点铺成虚拟节点，键按环上位置寻找归属。"""

    def __init__(self, hash_fn, bits=DEFAULT_BITS, vnodes=DEFAULT_VNODES):
        if not callable(hash_fn):
            raise TypeError("哈希函数必须可调用")
        if isinstance(bits, bool) or not isinstance(bits, int) or bits < 1:
            raise RingError("环位数必须是正整数")
        if isinstance(vnodes, bool) or not isinstance(vnodes, int) or vnodes < 1:
            raise RingError("虚拟节点数必须是正整数")
        self.hash_fn = hash_fn
        self.bits = bits
        self.size = 1 << bits
        self.vnodes = vnodes
        self._nodes = set()
        self._data = {}
        self._owners = {}
        self._positions = []
        self._node_positions = {}

    # ------------------------------------------------------------ 参数校验
    def _require_free_name(self, name):
        """确认这个名字可以作为新节点加入。"""
        if not isinstance(name, str):
            raise TypeError("节点名必须是字符串")
        if not name:
            raise RingError("节点名不能是空字符串")
        if name in self._nodes:
            raise RingError("节点 %s 已经在环上" % name)
        return name

    def _require_node(self, name):
        """确认这个名字是环上的节点。"""
        if not isinstance(name, str):
            raise TypeError("节点名必须是字符串")
        if not name:
            raise RingError("节点名不能是空字符串")
        if name not in self._nodes:
            raise RingError("节点 %s 不在环上" % name)
        return name

    def _require_position(self, position):
        """确认这是环空间里的一个位置。"""
        if isinstance(position, bool) or not isinstance(position, int):
            raise TypeError("环上位置必须是整数")
        if position < 0 or position >= self.size:
            raise RingError("环上位置 %r 超出环空间" % (position,))
        return position

    # ------------------------------------------------------------ 环上布局
    def _hash_value(self, text):
        """取出哈希函数给出的整数结果。"""
        value = self.hash_fn(text)
        if isinstance(value, bool) or not isinstance(value, int):
            raise TypeError("哈希函数必须返回整数")
        return value

    def _position_for(self, name, index):
        """算出一个虚拟节点在环上的位置。"""
        label = "%s#%d" % (name, index)
        return self._hash_value(label) % self.size

    def _vnode_positions(self, name):
        """列出节点全部虚拟节点的落点。"""
        positions = []
        for index in range(self.vnodes):
            positions.append(self._position_for(name, index))
        return positions

    def _rebuild_layout(self):
        """按当前节点集合重铺环上的虚拟节点。"""
        owners = {}
        node_positions = dict((name, []) for name in self._nodes)
        for name in sorted(self._nodes):
            for position in self._vnode_positions(name):
                if position in owners:
                    continue
                owners[position] = name
                node_positions[name].append(position)
        self._owners = owners
        self._positions = sorted(owners)
        for positions in node_positions.values():
            positions.sort()
        self._node_positions = node_positions

    # ------------------------------------------------------------ 环上查询
    def nodes(self):
        """按名字升序返回环上所有节点。"""
        return sorted(self._nodes)

    def ring_positions(self):
        """按升序返回环上被占用的位置。"""
        return list(self._positions)

    def positions_of(self, name):
        """按升序返回节点占用的环上位置。"""
        return list(self._node_positions[self._require_node(name)])

    def vnodes_of(self, name):
        """返回节点实际占用的虚拟节点数。"""
        return len(self.positions_of(name))

    def hash_key(self, key):
        """把键映射到环上的一个位置。"""
        if not isinstance(key, str):
            raise TypeError("键必须是字符串")
        if not key:
            raise RingError("键不能是空字符串")
        return self._hash_value(key) % self.size

    def _slot_index(self, position):
        """在升序位置表上二分，找出第一个不小于 position 的下标。"""
        return bisect.bisect_left(self._positions, position)

    def successor_position(self, position):
        """返回环上不小于 position 的最小位置；没有更大的位置时绕回第一格。"""
        self._require_position(position)
        if not self._positions:
            raise RingError("环上还没有节点")
        index = self._slot_index(position)
        if index == len(self._positions):
            index = 0
        return self._positions[index]

    def locate_position(self, position):
        """返回接管环上该位置的节点。"""
        self._require_position(position)
        if not self._positions:
            raise RingError("环上还没有节点")
        return self._owners[self.successor_position(position)]

    def locate(self, key):
        """返回负责给定键的节点。"""
        return self.locate_position(self.hash_key(key))

    # ------------------------------------------------------------ 键值数据
    def put(self, key, value):
        """把键值交给归属节点保存，并返回归属节点。"""
        owner = self.locate(key)
        self._data.setdefault(owner, {})[key] = value
        return owner

    def get(self, key):
        """读取键的值；环上没有这个键时抛出 KeyError。"""
        owner = self.locate(key)
        bucket = self._data.get(owner, {})
        if key in bucket:
            return bucket[key]
        raise KeyError(key)

    def keys_of(self, name):
        """按升序列出节点手上的键。"""
        self._require_node(name)
        return sorted(self._data.get(name, {}))

    # ------------------------------------------------------------ 拓扑变化
    def _reassign(self):
        """按当前布局重新摆放键：只有归属变了的键会换节点。"""
        items = []
        for name in sorted(self._data):
            items.extend(self._data[name].items())
        self._data = dict((name, {}) for name in self._nodes)
        for key, value in items:
            owner = self.locate(key)
            self._data[owner][key] = value

    def add_node(self, name):
        """把节点加入环，并接管本应由它负责的键。"""
        self._require_free_name(name)
        self._nodes.add(name)
        self._data[name] = {}
        self._rebuild_layout()
        self._reassign()
        return name

    def remove_node(self, name):
        """把节点从环上摘掉，并处理它留下的键。"""
        self._require_node(name)
        self._nodes.discard(name)
        self._rebuild_layout()
        keys = self._data.pop(name, {})
        if not self._nodes:
            return name
        for key, value in keys.items():
            owner = self.locate(key)
            self._data[owner][key] = value
        return name
