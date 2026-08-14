这个仓库需要做四件事,做完之后所有测试都要通过:

1. `units.py` 里的 `to_celsius` 换算公式写反了,修正它
2. `units.py` 里补一个 `to_fahrenheit(c)`,做相反方向的换算
3. `formatting.py` 里的 `format_temp` 要把数值保留一位小数(例如 21.456 -> "21.5C")
4. `legacy.py` 里的 `old_convert` 是个老的近似实现,把它改成直接委托给 `units.to_celsius`

不许修改任何 test_ 开头的文件——只能修被测代码。
