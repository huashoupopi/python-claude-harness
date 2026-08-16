"""入口:把执行转交给真正的主干 examples/22_trunk.py。

为什么要有这个转发,而不是直接在这儿写实现:
    ⑴ 主干文件名以数字开头,`import 22_trunk` 是语法错误 —— 只能按路径装载;
    ⑵ examples/ 下是按课程 01-22 编号的学习序列,22 是九件共存的最终形态,
       这个编号本身是叙事的一部分,不想为了「能 import」去改名。

⚠️ 历史遗留:本文件此前 import 的是 harness/ 那个包 —— 那是课程早期的半成品
   (686 行,只有 loop/tools/hooks/permissions 四件),而真正在跑、被 100 条测试和
   整套 bench 覆盖的是 examples/22_trunk.py(约 3900 行,27 个工具)。
   于是 clone 下来的人跑 `python main.py`,跑起来的是个残废版本。
   harness/ 暂时留着(pyproject 的打包配置指着它),清理记在 BACKLOG.md。
"""

import runpy
from pathlib import Path

TRUNK = Path(__file__).parent / "examples" / "22_trunk.py"

if __name__ == "__main__":
    # run_name="__main__" 让主干底部那句 `if __name__ == "__main__": main()` 生效;
    # sys.argv 原样透传,所以 `python main.py "修好失败的测试"` 和直接跑主干等价。
    runpy.run_path(str(TRUNK), run_name="__main__")
