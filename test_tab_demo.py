price = input()
prices = list(map(int, price.split(",")))
max_mon = 0
buy = prices[0]
for p in prices[1:]:
    max_mon = max(max_mon, p - buy)
    buy = min(buy, p)

print(max_mon)
