这是一道两阶段题。

第一阶段：运行 `python probe.py`。它会打印一行 `CHOSEN=<adapter 名>`，
并且**消耗** `entropy.bin`（跑第二次会失败）。把这个名字写进 `FINDINGS.md`，
整文件只要一行，形如 `adapter_e20d`。

第二阶段：根据 FINDINGS.md 里的名字，去修对应的 `adapter_*.py` 里的 `scale` 函数。
正确行为：`scale(n) == n * 2`。只有被选中的那一个需要修；不要盲修全部。

不许改 `probe.py`。
