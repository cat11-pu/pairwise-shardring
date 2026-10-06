# shardring

只依赖 Python 标准库的一致性哈希环内核：物理节点铺成环上的一组虚拟节点，键按自己的
哈希落点寻找归属，节点增删后按新的布局搬动键值数据。哈希函数由调用方注入
（给出 `hash_fn(text) -> int`），内核不做任何真实哈希、不读写文件、不联网、
不取真实时钟、不使用随机数，同一个输入永远得到同样的结果。

- `shardring/core.py` — 环内核：虚拟节点布局、键的归属、环上二分与增删迁移。
- `tests/test_core.py` — 验收用例。

## 运行测试

在项目根目录执行：

```
python3 -m unittest discover -s tests -v
```

Windows 上如果没有 `python3`，可用：

```
python -m unittest discover -s tests -v
```
